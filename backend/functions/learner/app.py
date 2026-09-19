"""EventBridge -> learner. Graph writes never sit in the request path.

Consumes `DeliveryConfirmed` events (from the rider app or the console's
approve action) and applies them to the landmark graph through
`patasetu.learn`. Also accepts `CaseReviewed` for the audit trail.

The event carries the order id; the landmarks the resolution used come from
the order's stored META row, so the rider app only needs to send *where the
delivery happened*, not what the system had inferred.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import UTC, datetime
from typing import Any

sys.path.insert(0, "/opt/python")
if os.path.isdir(
    _local := os.path.join(os.path.dirname(__file__), "../../layers/common/python")
):
    sys.path.insert(0, os.path.abspath(_local))

from patasetu import learn, store
from patasetu.config import load as load_config
from patasetu.providers import Providers, ProviderUnavailable

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

CONFIG = load_config()
PROVIDERS = Providers(CONFIG)


def _detail(event: dict[str, Any]) -> dict[str, Any]:
    detail = event.get("detail") or {}
    if isinstance(detail, str):
        try:
            detail = json.loads(detail)
        except ValueError:
            detail = {}
    return detail


def build_learning_event(detail: dict[str, Any]) -> learn.LearningEvent | None:
    """Turn an EventBridge detail into a LearningEvent, or None if it cannot."""
    order_id = str(detail.get("order_id") or "").strip()
    if not order_id:
        return None

    meta = PROVIDERS.store.get(store.order_pk(order_id), "META") or {}
    structured = store.from_dynamo(meta.get("structured") or {})
    landmark_ids = [
        lm["matched_id"]
        for lm in structured.get("landmarks") or []
        if isinstance(lm, dict) and lm.get("matched_id")
    ]
    spellings = {
        lm["matched_id"]: lm.get("name", "")
        for lm in structured.get("landmarks") or []
        if isinstance(lm, dict) and lm.get("matched_id")
    }

    # Where the delivery actually happened: the rider's fix if given, else the
    # resolved coordinate (an operator approving in the console has no GPS).
    point = detail.get("location") or store.from_dynamo(meta.get("geo") or {})
    if not point or point.get("lat") is None:
        return None

    return learn.LearningEvent(
        order_id=order_id,
        landmark_ids=landmark_ids,
        confirmed_lat=float(point["lat"]),
        confirmed_lng=float(point["lng"]),
        spellings=spellings,
        accuracy_m=float(detail["accuracy_m"])
        if detail.get("accuracy_m") is not None
        else None,
    )


def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    detail_type = event.get("detail-type") or event.get("detailType") or ""
    detail = _detail(event)
    order_id = str(detail.get("order_id") or "")
    now = datetime.now(UTC).isoformat()

    if detail_type != "DeliveryConfirmed":
        PROVIDERS.store.put(
            store.order_pk(order_id or "unknown"),
            f"EVENT#{now}",
            {"event": "learner_ignored", "detail_type": detail_type},
        )
        return {"ok": True, "ignored": detail_type}

    learning_event = build_learning_event(detail)
    if learning_event is None:
        logger.warning(
            json.dumps(
                {"event": "learner_skip", "reason": "no order/point", "detail": detail}
            )
        )
        return {"ok": False, "reason": "no order id or coordinate"}

    try:
        updates = learn.learn(PROVIDERS.search, learning_event)
    except ProviderUnavailable as exc:
        logger.error(json.dumps({"event": "learner_unavailable", "error": str(exc)}))
        return {"ok": False, "reason": str(exc)}

    PROVIDERS.store.put(
        store.order_pk(order_id),
        f"EVENT#{now}",
        {
            "event": "delivery_confirmed",
            "updates": [
                {
                    "landmark_id": u.landmark_id,
                    "observations": [u.before_observations, u.after_observations],
                    "moved_m": round(u.moved_m, 1),
                    "alias_added": u.alias_added,
                    "rejected": u.rejected_reason,
                }
                for u in updates
            ],
        },
    )
    applied = sum(1 for u in updates if u.applied)
    logger.info(
        json.dumps(
            {
                "event": "learned",
                "order_id": order_id,
                "applied": applied,
                "total": len(updates),
            }
        )
    )
    return {"ok": True, "applied": applied, "updates": len(updates)}
