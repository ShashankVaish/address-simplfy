"""Audit trail and review-queue API tests."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

from patasetu import store
from patasetu.store import InMemoryStore

RESOLVED = {
    "status": "RESOLVED",
    "confidence": 0.9,
    "structured": {
        "building": "14",
        "locality": "Ramesh Nagar",
        "city": "New Delhi",
        "pincode": "110015",
    },
    "evidence": ["x"],
}
NEEDS_INFO = {
    "status": "NEEDS_INFO",
    "confidence": 0.5,
    "structured": {"locality": "Ramesh Nagar", "pincode": "110015"},
    "clarification": {
        "field": "building",
        "question": "Flat?",
        "language": "en-IN",
        "expected_gain": None,
    },
    "evidence": [],
}


def load_queue_app(kv: InMemoryStore) -> Any:
    path = Path(__file__).resolve().parents[1] / "functions" / "queue_api" / "app.py"
    spec = importlib.util.spec_from_file_location("queue_app", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    module.PROVIDERS.__dict__["store"] = kv
    return module


def event(path: str, method: str = "GET", body: dict | None = None, **pp: str) -> dict:
    return {
        "rawPath": path,
        "requestContext": {"http": {"path": path, "method": method}},
        "body": json.dumps(body) if body else None,
        "pathParameters": pp,
        "queryStringParameters": {},
    }


class TestAuditTrail:
    def test_records_meta_and_event(self) -> None:
        kv = InMemoryStore()
        store.record_resolution(
            kv, order_id="O1", resolution=NEEDS_INFO, correlation_id="c1"
        )
        meta = kv.get(store.order_pk("O1"), "META")
        assert meta["status"] == "NEEDS_INFO"
        assert meta["summary"] == "Ramesh Nagar, 110015"
        events = kv.query_prefix(store.order_pk("O1"), "EVENT#")
        assert len(events) == 1 and events[0]["event"] == "resolved"

    def test_re_resolution_keeps_original_arrival_time(self) -> None:
        kv = InMemoryStore()
        store.record_resolution(
            kv, order_id="O1", resolution=NEEDS_INFO, correlation_id="c1"
        )
        first = kv.get(store.order_pk("O1"), "META")["created_at"]
        store.record_resolution(
            kv, order_id="O1", resolution=RESOLVED, correlation_id="c2"
        )
        meta = kv.get(store.order_pk("O1"), "META")
        assert meta["created_at"] == first
        assert meta["status"] == "RESOLVED"
        assert len(kv.query_prefix(store.order_pk("O1"), "EVENT#")) == 2

    def test_event_rows_are_not_in_the_status_index(self) -> None:
        """Sparse GSI: EVENT rows carry no created_at and must never appear as cases."""
        kv = InMemoryStore()
        store.record_resolution(
            kv, order_id="O1", resolution=NEEDS_INFO, correlation_id="c1"
        )
        rows = kv.query_status("NEEDS_INFO")
        assert len(rows) == 1 and rows[0]["order_id"] == "O1"

    def test_feedback_edit_updates_fields_and_status(self) -> None:
        kv = InMemoryStore()
        store.record_resolution(
            kv, order_id="O1", resolution=NEEDS_INFO, correlation_id="c1"
        )
        meta = store.record_feedback(
            kv, order_id="O1", action="edit", actor="ops", edits={"building": "14"}
        )
        assert meta["status"] == "RESOLVED"
        assert meta["structured"]["building"] == "14"
        assert meta["reviewed_by"] == "ops"
        assert kv.query_status("NEEDS_INFO") == []

    def test_feedback_on_unknown_order(self) -> None:
        assert (
            store.record_feedback(
                InMemoryStore(),
                order_id="nope",
                action="approve",
                actor="a",
                edits=None,
            )
            is None
        )


class TestQueueApi:
    @pytest.fixture
    def kv(self) -> InMemoryStore:
        kv = InMemoryStore()
        store.record_resolution(
            kv, order_id="B", resolution=NEEDS_INFO, correlation_id="c"
        )
        store.record_resolution(
            kv, order_id="A", resolution=NEEDS_INFO, correlation_id="c"
        )
        store.record_resolution(
            kv, order_id="R", resolution=RESOLVED, correlation_id="c"
        )
        # Make A older than B so ordering is testable.
        kv._items[(store.order_pk("A"), "META")]["created_at"] = "2000-01-01T00:00:00"
        return kv

    def test_queue_lists_open_cases_oldest_first(self, kv: InMemoryStore) -> None:
        app = load_queue_app(kv)
        body = json.loads(app.handler(event("/v1/queue"))["body"])
        assert [r["order_id"] for r in body["items"]] == ["A", "B"]
        assert body["count"] == 2

    def test_queue_never_leaks_internal_keys(self, kv: InMemoryStore) -> None:
        app = load_queue_app(kv)
        body = json.loads(app.handler(event("/v1/queue"))["body"])
        assert all("PK" not in r and "SK" not in r for r in body["items"])

    def test_order_timeline(self, kv: InMemoryStore) -> None:
        app = load_queue_app(kv)
        body = json.loads(app.handler(event("/v1/orders/A", order_id="A"))["body"])
        assert body["order"]["order_id"] == "A"
        assert [e["event"] for e in body["timeline"]] == ["resolved"]

    def test_feedback_closes_a_case(self, kv: InMemoryStore) -> None:
        app = load_queue_app(kv)
        r = app.handler(
            event(
                "/v1/feedback",
                "POST",
                {"order_id": "A", "action": "approve", "actor": "ops"},
            )
        )
        assert r["statusCode"] == 200
        assert json.loads(r["body"])["order"]["status"] == "RESOLVED"
        remaining = json.loads(app.handler(event("/v1/queue"))["body"])["items"]
        assert [r["order_id"] for r in remaining] == ["B"]

    @pytest.mark.parametrize(
        ("body", "code"),
        [
            ({"order_id": "A", "action": "yolo"}, 400),
            ({"action": "approve"}, 400),
            ({"order_id": "A", "action": "edit", "edits": "not-a-dict"}, 400),
            ({"order_id": "nope", "action": "approve"}, 404),
        ],
    )
    def test_feedback_validation(
        self, kv: InMemoryStore, body: dict, code: int
    ) -> None:
        app = load_queue_app(kv)
        assert app.handler(event("/v1/feedback", "POST", body))["statusCode"] == code

    def test_unknown_route(self, kv: InMemoryStore) -> None:
        app = load_queue_app(kv)
        assert app.handler(event("/v1/nothing"))["statusCode"] == 404
        assert app.handler(event("/v1/queue", "OPTIONS"))["statusCode"] == 204
