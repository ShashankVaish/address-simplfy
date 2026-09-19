"""EventBridge -> clarifier. Writes the question for a NEEDS_INFO case.

Consumes `AddressNeedsInfo` events (or a direct invocation with the same
shape), asks Cedar whether outreach is allowed *right now*, and only then
generates the question with the Strands agent. A DENY never produces a
message; it produces a review-queue entry with the policy id, via the same
`authz` path the authorizer endpoint uses.

The message itself is not sent from here (SNS delivery is on the kill list);
the question is stored on the order so the console shows it and an operator
or the customer can answer it.
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
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agent import ClarifierInput, generate

from patasetu import authz, store
from patasetu.config import load as load_config
from patasetu.providers import Providers

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

CONFIG = load_config()
PROVIDERS = Providers(CONFIG)
IST = timedelta(hours=5, minutes=30)


def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    detail = event.get("detail") or event
    if isinstance(detail, str):
        detail = json.loads(detail)
    order_id = str(detail.get("order_id") or "").strip()
    if not order_id:
        return {"ok": False, "reason": "order_id required"}

    meta = PROVIDERS.store.get(store.order_pk(order_id), "META")
    if meta is None:
        return {"ok": False, "reason": "unknown order"}
    meta = store.from_dynamo(meta)
    clarification = meta.get("clarification") or {}
    if not clarification:
        return {"ok": False, "reason": "order has no clarification to ask"}

    # Policy first. Nothing is generated for a case we may not contact.
    local_hour = int(detail.get("local_hour", (datetime.now(UTC) + IST).hour))
    decision = authz.authorize_contact(
        order_id=order_id,
        confidence=float(meta.get("confidence") or 0.0),
        messages_sent_for_order=sum(
            1
            for e in PROVIDERS.store.query_prefix(
                store.order_pk(order_id), "EVENT#", limit=100
            )
            if e.get("event") == "message_sent"
        ),
        local_hour=local_hour,
        opted_out=bool(detail.get("opted_out", False)),
    )
    now = datetime.now(UTC).isoformat()
    if not decision.allowed:
        meta.update(
            {"review_reason": f"Cedar DENY: {decision.reason}", "updated_at": now}
        )
        PROVIDERS.store.put(store.order_pk(order_id), "META", meta)
        PROVIDERS.store.put(
            store.order_pk(order_id),
            f"EVENT#{now}",
            {
                "event": "authz_decision",
                "decision": "DENY",
                "reason": decision.reason,
                "matched_policies": decision.matched_policies,
            },
        )
        return {"ok": True, "sent": False, "decision": decision.as_dict()}

    structured = meta.get("structured") or {}
    question, source = generate(
        ClarifierInput(
            field=str(clarification.get("field", "building")),
            known={
                k: str(v) for k, v in structured.items() if isinstance(v, str) and v
            },
            script="devanagari"
            if clarification.get("language") == "hi-IN"
            else "latin",
            fallback_question=str(clarification.get("question", "")),
            fallback_language=str(clarification.get("language", "en-IN")),
        )
    )

    meta["clarification"] = {
        **clarification,
        "question": question.question,
        "language": question.language,
        "source": source,
    }
    meta["updated_at"] = now
    PROVIDERS.store.put(store.order_pk(order_id), "META", meta)
    PROVIDERS.store.put(
        store.order_pk(order_id),
        f"EVENT#{now}",
        {
            "event": "question_generated",
            "source": source,
            "language": question.language,
        },
    )
    logger.info(
        json.dumps({"event": "clarified", "order_id": order_id, "source": source})
    )
    return {
        "ok": True,
        "sent": False,
        "question": question.model_dump(),
        "source": source,
    }
