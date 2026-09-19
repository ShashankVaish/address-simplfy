"""`POST /v1/authorize` -- the Cedar decision endpoint.

The clarification workflow calls this before sending anything to a customer.
The endpoint is small on purpose: build the request, ask Cedar, and if the
answer is DENY, write the case to the review queue with the matched policy id
and the reason attached. A denial never disappears (FR-23).

Twenty seconds of demo most teams cannot show: set `local_hour` to 23, submit
a low-confidence address, and watch this return DENY with
`contact-allowed-window` unmatched and the case land in the queue.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import UTC, datetime, timedelta
from typing import Any

sys.path.insert(0, "/opt/python")
if os.path.isdir(
    _local := os.path.join(os.path.dirname(__file__), "../../layers/common/python")
):
    sys.path.insert(0, os.path.abspath(_local))

from patasetu import auth, authz, store
from patasetu.config import load as load_config
from patasetu.providers import Providers

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

CONFIG = load_config()
PROVIDERS = Providers(CONFIG)

# India Standard Time. The customer's local hour is what the quiet-hours rule
# is about; this system serves India, so IST is the clock, and the request may
# override it (which is also how the demo sets the clock to 23:00).
IST = timedelta(hours=5, minutes=30)

_CORS = {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": os.environ.get("CORS_ORIGIN", "*"),
    "Access-Control-Allow-Headers": "content-type,authorization",
    "Access-Control-Allow-Methods": "POST,OPTIONS",
}


def _respond(status: int, body: Any) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": _CORS,
        "body": json.dumps(body, ensure_ascii=False),
    }


def _messages_sent(order_id: str) -> int:
    """How many outreach messages this order has already had, from the audit trail."""
    events = PROVIDERS.store.query_prefix(store.order_pk(order_id), "EVENT#", limit=100)
    return sum(1 for e in events if e.get("event") == "message_sent")


def _record(order_id: str, decision: authz.Decision, *, note: str | None) -> None:
    """Append the decision to the order's timeline; on DENY, flag for review."""
    now = datetime.now(UTC).isoformat()
    PROVIDERS.store.put(
        store.order_pk(order_id),
        f"EVENT#{now}",
        {
            "event": "authz_decision",
            "action": decision.action,
            "decision": decision.decision,
            "matched_policies": decision.matched_policies,
            "reason": decision.reason,
            "context": decision.context,
        },
    )
    if decision.allowed:
        return

    # The case goes to a human, with the policy id and reason attached. If the
    # order is not yet in the table (the demo may authorise before resolving),
    # create the row so it still appears in the queue.
    meta = PROVIDERS.store.get(store.order_pk(order_id), "META") or {
        "order_id": order_id,
        "created_at": now,
        "summary": note or "",
        "structured": {},
        "evidence": [],
    }
    meta = {k: v for k, v in meta.items() if k not in ("PK", "SK")}
    meta.update(
        {
            "status": "NEEDS_INFO",
            "updated_at": now,
            "review_reason": (
                f"Cedar DENY on {decision.action}: {decision.reason}"
                + (
                    f" [{', '.join(decision.matched_policies)}]"
                    if decision.matched_policies
                    else ""
                )
            ),
            # Asking twice must not say it twice: keep one line per reason.
            "evidence": [
                *[
                    e
                    for e in (meta.get("evidence") or [])
                    if e != f"outreach denied by policy: {decision.reason}"
                ],
                f"outreach denied by policy: {decision.reason}",
            ],
        }
    )
    PROVIDERS.store.put(store.order_pk(order_id), "META", meta)


def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    http = event.get("requestContext", {}).get("http", {}) or {}
    method = event.get("httpMethod") or http.get("method") or "POST"
    if method == "OPTIONS":
        return _respond(204, {})

    # Operator routes are protected when the stack says so (NFR-16). The
    # playground never is; this handler serves only operator routes.
    try:
        auth.require_operator(event)
    except auth.Unauthorized as exc:
        return _respond(401, {"error": "unauthorized", "detail": str(exc)})

    try:
        body = json.loads(event.get("body") or "{}")
    except ValueError as exc:
        return _respond(400, {"error": "body must be valid JSON", "detail": str(exc)})

    order_id = str(body.get("order_id") or "").strip()
    action = str(body.get("action") or "contactCustomer")
    if not order_id:
        return _respond(400, {"error": "order_id is required"})

    try:
        confidence = float(body.get("confidence", 0.0))
    except (TypeError, ValueError):
        return _respond(400, {"error": "confidence must be a number"})

    if action == "contactCustomer":
        # The local hour: explicit in the request (the demo), else now in IST.
        if body.get("local_hour") is not None:
            try:
                local_hour = int(body["local_hour"])
            except (TypeError, ValueError):
                return _respond(400, {"error": "local_hour must be an integer 0-23"})
            if not 0 <= local_hour <= 23:
                return _respond(400, {"error": "local_hour must be an integer 0-23"})
        else:
            local_hour = (datetime.now(UTC) + IST).hour

        decision = authz.authorize_contact(
            order_id=order_id,
            confidence=confidence,
            messages_sent_for_order=int(
                body.get("messages_sent_for_order", _messages_sent(order_id))
            ),
            local_hour=local_hour,
            channel=str(body.get("channel", "sms")),
            opted_out=bool(body.get("opted_out", False)),
            pending=bool(body.get("pending", True)),
            principal=str(body.get("principal", "clarifier")),
        )
    elif action == "overwriteStoredAddress":
        decision = authz.authorize_overwrite(
            order_id=order_id,
            confidence=confidence,
            geo_source=str(body.get("geo_source", "")),
        )
    else:
        return _respond(400, {"error": f"unknown action {action!r}"})

    _record(order_id, decision, note=body.get("summary"))
    logger.info(
        json.dumps({"event": "authz", "order_id": order_id, **decision.as_dict()})
    )
    return _respond(200, decision.as_dict())
