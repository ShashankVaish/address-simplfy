"""`GET /v1/queue`, `POST /v1/feedback`, `GET /v1/orders/{id}` -- the review queue.

The queue is one query on GSI-1 (`status`, `created_at`), oldest first. There
is no scan anywhere in this function and there must never be one: the table
holds every resolution ever made, and a scan over it is a bill.

Feedback closes the loop. An operator approving or editing a case marks it
RESOLVED and appends to the order's timeline; in aws mode it also emits an
event so the learner can nudge the landmark graph.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from typing import Any

sys.path.insert(0, "/opt/python")
if os.path.isdir(
    _local := os.path.join(os.path.dirname(__file__), "../../layers/common/python")
):
    sys.path.insert(0, os.path.abspath(_local))

from patasetu import store
from patasetu.config import load as load_config
from patasetu.providers import Providers

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

CONFIG = load_config()
PROVIDERS = Providers(CONFIG)

QUEUE_STATUSES = ("NEEDS_INFO", "AMBIGUOUS")
FEEDBACK_ACTIONS = ("approve", "edit", "reject")

_CORS = {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": os.environ.get("CORS_ORIGIN", "*"),
    "Access-Control-Allow-Headers": "content-type,authorization",
    "Access-Control-Allow-Methods": "POST,GET,OPTIONS",
}


def _respond(status: int, body: Any) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": _CORS,
        "body": json.dumps(body, ensure_ascii=False),
    }


def _clean(item: dict[str, Any]) -> dict[str, Any]:
    return store.from_dynamo(
        {k: v for k, v in item.items() if k not in ("PK", "SK", "ttl")}
    )


def _emit_feedback_event(order_id: str, action: str) -> None:
    """Tell the learner. Best-effort: a missing bus must not fail the review."""
    if CONFIG.is_local:
        return
    try:
        import boto3

        boto3.client("events", region_name=CONFIG.region).put_events(
            Entries=[
                {
                    "Source": "patasetu.console",
                    "DetailType": (
                        "DeliveryConfirmed" if action == "approve" else "CaseReviewed"
                    ),
                    "EventBusName": CONFIG.event_bus,
                    "Detail": json.dumps({"order_id": order_id, "action": action}),
                }
            ]
        )
    except Exception:  # pragma: no cover - network
        logger.exception("feedback event not emitted")


def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    http = event.get("requestContext", {}).get("http", {}) or {}
    method = event.get("httpMethod") or http.get("method") or "GET"
    route = event.get("rawPath") or event.get("path") or http.get("path") or "/"
    params = event.get("queryStringParameters") or {}
    path_params = event.get("pathParameters") or {}

    if method == "OPTIONS":
        return _respond(204, {})

    # --- GET /v1/queue --------------------------------------------------------
    if method == "GET" and route.endswith("/queue"):
        try:
            limit = max(1, min(200, int(params.get("limit", 50))))
        except ValueError:
            return _respond(400, {"error": "limit must be an integer"})
        wanted = params.get("status")
        statuses = (wanted,) if wanted in QUEUE_STATUSES else QUEUE_STATUSES

        rows: list[dict[str, Any]] = []
        for status in statuses:
            rows.extend(
                _clean(r) for r in PROVIDERS.store.query_status(status, limit=limit)
            )
        rows.sort(key=lambda r: r.get("created_at", ""))
        return _respond(200, {"items": rows[:limit], "count": len(rows[:limit])})

    # --- GET /v1/orders/{order_id} -------------------------------------------
    if method == "GET" and "/orders/" in route:
        order_id = path_params.get("order_id") or route.rsplit("/", 1)[-1]
        meta = PROVIDERS.store.get(store.order_pk(order_id), "META")
        if meta is None:
            return _respond(404, {"error": "unknown order", "order_id": order_id})
        events = PROVIDERS.store.query_prefix(
            store.order_pk(order_id), "EVENT#", limit=100
        )
        return _respond(
            200, {"order": _clean(meta), "timeline": [_clean(e) for e in events]}
        )

    # --- POST /v1/feedback -----------------------------------------------------
    if method == "POST" and route.endswith("/feedback"):
        try:
            body = json.loads(event.get("body") or "{}")
        except ValueError as exc:
            return _respond(
                400, {"error": "body must be valid JSON", "detail": str(exc)}
            )

        order_id = str(body.get("order_id") or "").strip()
        action = str(body.get("action") or "").strip().lower()
        actor = str(body.get("actor") or "operator")[:80]
        edits = body.get("edits")
        if not order_id or action not in FEEDBACK_ACTIONS:
            return _respond(
                400,
                {"error": f"order_id and an action in {FEEDBACK_ACTIONS} are required"},
            )
        if edits is not None and not isinstance(edits, dict):
            return _respond(400, {"error": "edits must be an object of field -> value"})

        meta = store.record_feedback(
            PROVIDERS.store, order_id=order_id, action=action, actor=actor, edits=edits
        )
        if meta is None:
            return _respond(404, {"error": "unknown order", "order_id": order_id})
        _emit_feedback_event(order_id, action)
        return _respond(200, {"order": _clean(meta)})

    return _respond(404, {"error": "no such route", "route": route})
