"""Cedar policy tests: quiet hours, opt-out, second message, overwrite (FR-21..24).

These run the real Cedar evaluator against the real policy files. If a policy
file is edited, these tests are what notices.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

from patasetu import authz, store
from patasetu.store import InMemoryStore

cedarpy = pytest.importorskip("cedarpy")


def contact(**overrides: Any) -> authz.Decision:
    base = {
        "order_id": "O1",
        "confidence": 0.55,
        "messages_sent_for_order": 0,
        "local_hour": 14,
        "channel": "sms",
        "opted_out": False,
        "pending": True,
    }
    base.update(overrides)
    return authz.authorize_contact(**base)


class TestPolicyFiles:
    def test_policies_load_and_validate_against_schema(self) -> None:
        result = cedarpy.validate_policies(authz.load_policies(), authz.load_schema())
        assert result.validation_passed, result

    def test_every_policy_has_an_id_annotation(self) -> None:
        text = authz.load_policies()
        assert text.count("@id(") == text.count("permit(") + text.count("forbid(")


class TestContactCustomer:
    def test_allowed_in_the_day_for_an_unresolved_order(self) -> None:
        d = contact()
        assert d.allowed
        assert d.matched_policies == ["contact-allowed-window"]

    @pytest.mark.parametrize("hour", [0, 5, 8, 21, 23])
    def test_denied_outside_09_to_20(self, hour: int) -> None:
        """FR-22: deny outside 09:00-20:00 local time. The demo is hour 23."""
        d = contact(local_hour=hour)
        assert not d.allowed
        assert d.matched_policies == []
        assert "outside contact hours" in d.reason

    @pytest.mark.parametrize("hour", [9, 12, 20])
    def test_boundaries_are_inclusive(self, hour: int) -> None:
        assert contact(local_hour=hour).allowed

    def test_denied_if_a_message_was_already_sent(self) -> None:
        """FR-22: one message per order."""
        d = contact(messages_sent_for_order=1)
        assert not d.allowed
        assert "already been sent" in d.reason

    def test_denied_for_opted_out_customer_even_in_the_day(self) -> None:
        """FR-22: opt-out beats everything. The forbid names itself."""
        d = contact(opted_out=True)
        assert not d.allowed
        assert d.matched_policies == ["contact-opted-out"]
        assert "opted out" in d.reason

    def test_denied_when_confidence_is_already_high(self) -> None:
        # No reason to bother the customer.
        d = contact(confidence=0.9)
        assert not d.allowed
        assert "above the clarification threshold" in d.reason

    def test_denied_on_an_unapproved_channel(self) -> None:
        d = contact(channel="whatsapp")
        assert not d.allowed
        assert "whatsapp" in d.reason

    def test_denied_when_order_is_not_pending(self) -> None:
        assert not contact(pending=False).allowed

    def test_human_operator_is_not_bound_by_quiet_hours(self) -> None:
        d = contact(principal="operator", local_hour=23)
        assert d.allowed
        assert d.matched_policies == ["contact-human-operator"]

    def test_but_even_an_operator_cannot_contact_an_opted_out_customer(self) -> None:
        assert not contact(principal="operator", opted_out=True).allowed

    def test_confidence_at_threshold_is_denied(self) -> None:
        # < 80, not <= 80.
        assert not contact(confidence=0.80).allowed
        assert contact(confidence=0.79).allowed

    def test_decision_is_serialisable(self) -> None:
        payload = json.loads(json.dumps(contact().as_dict()))
        assert payload["decision"] == "ALLOW"
        assert payload["context"]["confidence_pct"] == 55


class TestOverwriteStoredAddress:
    def test_allowed_only_with_near_certainty_from_the_graph(self) -> None:
        """FR-24: confidence > 0.95 AND geo_source == landmark_graph."""
        d = authz.authorize_overwrite(
            order_id="O1", confidence=0.97, geo_source="landmark_graph"
        )
        assert d.allowed and d.matched_policies == ["overwrite-needs-certainty"]

    def test_denied_at_exactly_95(self) -> None:
        assert not authz.authorize_overwrite(
            order_id="O1", confidence=0.95, geo_source="landmark_graph"
        ).allowed

    @pytest.mark.parametrize("source", ["geocoder", "pincode_centroid", ""])
    def test_denied_from_any_other_geo_source(self, source: str) -> None:
        d = authz.authorize_overwrite(order_id="O1", confidence=0.99, geo_source=source)
        assert not d.allowed
        assert "not the landmark graph" in d.reason


class TestFailClosed:
    def test_evaluator_failure_is_a_deny_with_the_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def boom(*a: Any, **k: Any) -> Any:
            raise RuntimeError("engine exploded")

        monkeypatch.setattr(cedarpy, "is_authorized", boom)
        d = contact()
        assert not d.allowed
        assert "failing closed" in d.reason
        assert d.errors

    def test_to_pct_clamps(self) -> None:
        assert (
            authz.to_pct(-1) == 0
            and authz.to_pct(2) == 100
            and authz.to_pct(0.555) == 56
        )


# --- the Lambda: denial lands in the review queue -----------------------------


def load_authorizer(kv: InMemoryStore) -> Any:
    path = Path(__file__).resolve().parents[1] / "functions" / "authorizer" / "app.py"
    spec = importlib.util.spec_from_file_location("authorizer_app", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    module.PROVIDERS.__dict__["store"] = kv
    return module


def post(app: Any, body: dict) -> tuple[int, dict]:
    r = app.handler(
        {
            "rawPath": "/v1/authorize",
            "requestContext": {"http": {"method": "POST"}},
            "body": json.dumps(body),
        }
    )
    return r["statusCode"], json.loads(r["body"])


class TestAuthorizerLambda:
    def test_deny_at_23_routes_to_the_queue_with_policy_and_reason(self) -> None:
        """The demo: clock at 23:00 -> DENY on screen -> case in the queue."""
        kv = InMemoryStore()
        app = load_authorizer(kv)
        code, body = post(
            app,
            {
                "order_id": "DEMO-23",
                "confidence": 0.55,
                "local_hour": 23,
                "summary": "behind shiv mandir",
            },
        )
        assert code == 200 and body["decision"] == "DENY"
        assert "outside contact hours" in body["reason"]

        queue = kv.query_status("NEEDS_INFO")
        assert [q["order_id"] for q in queue] == ["DEMO-23"]
        assert "Cedar DENY" in queue[0]["review_reason"]
        events = kv.query_prefix(store.order_pk("DEMO-23"), "EVENT#")
        assert (
            events[0]["event"] == "authz_decision" and events[0]["decision"] == "DENY"
        )

    def test_allow_is_recorded_but_not_queued(self) -> None:
        kv = InMemoryStore()
        app = load_authorizer(kv)
        _code, body = post(
            app, {"order_id": "OK-1", "confidence": 0.55, "local_hour": 14}
        )
        assert body["decision"] == "ALLOW"
        assert kv.query_status("NEEDS_INFO") == []
        assert (
            kv.query_prefix(store.order_pk("OK-1"), "EVENT#")[0]["decision"] == "ALLOW"
        )

    def test_second_message_is_counted_from_the_audit_trail(self) -> None:
        kv = InMemoryStore()
        kv.put(
            store.order_pk("O2"), "EVENT#2026-09-19T10:00:00", {"event": "message_sent"}
        )
        app = load_authorizer(kv)
        _, body = post(app, {"order_id": "O2", "confidence": 0.5, "local_hour": 14})
        assert body["decision"] == "DENY" and "already been sent" in body["reason"]

    def test_opted_out_names_the_forbid_policy(self) -> None:
        app = load_authorizer(InMemoryStore())
        _, body = post(
            app,
            {"order_id": "O3", "confidence": 0.5, "local_hour": 14, "opted_out": True},
        )
        assert body["matched_policies"] == ["contact-opted-out"]

    @pytest.mark.parametrize(
        "body",
        [
            {},
            {"order_id": "x", "confidence": "high"},
            {"order_id": "x", "local_hour": 25},
            {"order_id": "x", "action": "launchMissiles"},
        ],
    )
    def test_validation(self, body: dict) -> None:
        app = load_authorizer(InMemoryStore())
        code, _ = post(app, body)
        assert code == 400
