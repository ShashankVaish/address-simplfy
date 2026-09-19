"""`POST /v1/resolve` and `GET /health` -- the hot path.

The handler is deliberately thin. All pipeline logic lives in
`patasetu.pipeline`, which is pure and cloud-free, so the same code path is
exercised by `scripts/resolve_one.py`, by the test suite and by the ablation
runner. What the handler adds is only what a Lambda must: request parsing,
error mapping, structured logging and the correlation id.

Two properties that matter operationally:

*   **It never returns 500 for a bad address.** A malformed request is a 400; a
    degraded dependency produces a low-confidence `NEEDS_INFO` with the
    degradation recorded in `evidence`, not an error. A judge pasting something
    strange into the playground must get an answer, not a stack trace.
*   **The gazetteer loads at import time**, so the ~270 ms table load lands in
    cold start rather than inside the first user's request -- and outside the
    per-stage timings, which it would otherwise make meaningless.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
import uuid
from typing import Any

# The Lambda layer mounts at /opt/python; local runs need the repo path.
sys.path.insert(0, "/opt/python")
if os.path.isdir(
    _local := os.path.join(os.path.dirname(__file__), "../../layers/common/python")
):
    sys.path.insert(0, os.path.abspath(_local))

from pydantic import ValidationError

from patasetu import gazetteer, metrics, store
from patasetu.cache import ResolutionCache
from patasetu.confidence import Calibrator
from patasetu.config import load as load_config
from patasetu.models import ResolveRequest
from patasetu.pipeline import Stack, StageUnavailable, resolve
from patasetu.providers import Providers

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

# --- cold-start initialisation ------------------------------------------------
# Done once per container, on purpose. Everything here is read-only.
_COLD_START = time.perf_counter()
CONFIG = load_config()
CALIBRATOR = Calibrator.load()
_WARMED = gazetteer.warm()
PROVIDERS = Providers(CONFIG)
# Level 1 and 2 of the cache. The store is DynamoDB in aws mode and a dict in
# local mode; the near-duplicate vector index lives in this container either
# way, because a kNN round trip to find a cache entry would cost more than the
# pipeline run it saves.
CACHE = ResolutionCache(
    PROVIDERS.store,
    ttl_days=CONFIG.cache_ttl_days,
    # Near-duplicate lookup needs an embedding of every incoming address. With
    # the local hashing embedder that is free; with Titan it is a paid Bedrock
    # call on every request, *before* we know whether the cache will hit -- so
    # in aws mode the probe is exact-hash only until the hit rate justifies it.
    embedder=PROVIDERS.embedder if CONFIG.is_local else None,
)
_INIT_MS = (time.perf_counter() - _COLD_START) * 1000.0

logger.info(
    json.dumps(
        {
            "event": "cold_start",
            "init_ms": round(_INIT_MS, 1),
            "provider": CONFIG.provider,
            "calibration": CALIBRATOR.describe(),
            **_WARMED,
        }
    )
)

# Which ablation stack the deployed API serves. Day 1 ships A; Day 2 moves this
# to E once S2 and S3 are wired.
SERVING_STACK = Stack(os.environ.get("SERVING_STACK", "A"))

_CORS = {
    "Content-Type": "application/json",
    # The playground is public and read-only so a judge can use it without a
    # login (FR-26, NFR-16).
    "Access-Control-Allow-Origin": os.environ.get("CORS_ORIGIN", "*"),
    "Access-Control-Allow-Headers": "content-type",
    "Access-Control-Allow-Methods": "POST,GET,OPTIONS",
}


def _respond(status: int, body: dict[str, Any]) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": _CORS,
        "body": json.dumps(body, ensure_ascii=False),
    }


def _log(correlation_id: str, **fields: Any) -> None:
    """Structured JSON log with a correlation id (NFR-20)."""
    logger.info(json.dumps({"correlation_id": correlation_id, **fields}))


def _emit_needs_info(order_id: str, correlation_id: str) -> None:
    """Best effort: a missing bus must never fail the customer's answer.

    The event carries ids only. The clarifier reads the stored resolution from
    the order's META row, so no address text crosses the bus.
    """
    if CONFIG.is_local:
        return
    try:
        import boto3

        boto3.client("events", region_name=CONFIG.region).put_events(
            Entries=[
                {
                    "Source": "patasetu.resolver",
                    "DetailType": "AddressNeedsInfo",
                    "EventBusName": CONFIG.event_bus,
                    "Detail": json.dumps(
                        {"order_id": order_id, "correlation_id": correlation_id}
                    ),
                }
            ]
        )
    except Exception:  # pragma: no cover - network
        logger.exception("AddressNeedsInfo event not emitted")


def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    correlation_id = (
        (event.get("headers") or {}).get("x-correlation-id")
        or getattr(context, "aws_request_id", None)
        or str(uuid.uuid4())
    )

    route = (
        event.get("rawPath")
        or event.get("path")
        or (event.get("requestContext", {}).get("http", {}) or {}).get("path")
        or "/"
    )
    method = (
        event.get("httpMethod")
        or (event.get("requestContext", {}).get("http", {}) or {}).get("method")
        or "POST"
    )

    if method == "OPTIONS":
        return _respond(204, {})

    if route.endswith("/health"):
        return _respond(
            200,
            {
                "status": "ok",
                "provider": CONFIG.provider,
                "serving_stack": SERVING_STACK.value,
                "calibration": CALIBRATOR.describe(),
                "gazetteer": _WARMED,
                "landmark_index": PROVIDERS.search.count()
                if SERVING_STACK.uses_retrieval and CONFIG.is_local
                else None,
                "cache": {
                    "lookups": CACHE.stats.lookups,
                    "hit_rate": round(CACHE.stats.hit_rate, 3),
                },
                "init_ms": round(_INIT_MS, 1),
            },
        )

    # --- parse the request ---------------------------------------------------
    try:
        raw_body = event.get("body") or "{}"
        if event.get("isBase64Encoded"):
            import base64

            raw_body = base64.b64decode(raw_body).decode("utf-8")
        payload = json.loads(raw_body)
    except (ValueError, TypeError) as exc:
        _log(correlation_id, event="bad_json", error=str(exc))
        return _respond(400, {"error": "body must be valid JSON", "detail": str(exc)})

    try:
        request = ResolveRequest.model_validate(payload)
    except ValidationError as exc:
        _log(correlation_id, event="bad_request", errors=exc.error_count())
        return _respond(
            400,
            {
                "error": "invalid request",
                "detail": json.loads(exc.json()),
            },
        )

    # --- run the pipeline ----------------------------------------------------
    started = time.perf_counter()
    try:
        result = resolve(
            request.raw,
            stack=SERVING_STACK,
            hint=request.hint,
            cfg=CONFIG,
            calibrator=CALIBRATOR,
            providers=PROVIDERS,
            cache=CACHE,
            correlation_id=correlation_id,
        )
    except StageUnavailable as exc:
        # A configuration error on our side, not the caller's. 503 rather than
        # 500 because it is a missing dependency, and the message names it.
        _log(correlation_id, event="stage_unavailable", error=str(exc))
        return _respond(503, {"error": "stage unavailable", "detail": str(exc)})
    except Exception as exc:
        # Deliberately broad. An unexpected failure on one address must not take
        # the endpoint down during a demo, and the correlation id is enough to
        # find it in the logs afterwards.
        logger.exception("unhandled error in resolver")
        _log(correlation_id, event="unhandled_error", error=repr(exc))
        return _respond(
            500,
            {
                "error": "internal error",
                "correlation_id": correlation_id,
            },
        )

    elapsed_ms = (time.perf_counter() - started) * 1000.0
    payload = result.model_dump(mode="json")

    # Audit trail and review-queue row (NFR-22, FR-27). The order id is the
    # caller's if given, else the correlation id, so every resolution is
    # queryable as a timeline and every NEEDS_INFO / AMBIGUOUS case reaches the
    # queue. Write failures are swallowed inside the store: a broken audit must
    # never fail the customer's answer.
    store.record_resolution(
        PROVIDERS.store,
        order_id=request.order_id or correlation_id,
        resolution=payload,
        correlation_id=correlation_id,
    )

    # A NEEDS_INFO case is handed to the clarifier agent through EventBridge,
    # so question generation and the Cedar contact check happen off the request
    # path (FR-20, FR-22). The customer's answer never waits on either.
    if result.status.value == "NEEDS_INFO":
        _emit_needs_info(request.order_id or correlation_id, correlation_id)

    # One EMF line per request carries every dashboard metric: no PutMetricData
    # call, no added latency. CloudWatch extracts and publishes them (NFR-10).
    logger.info(
        metrics.emf_record(
            metrics.request_metrics(payload, elapsed_ms=elapsed_ms),
            dimensions={"Stack": SERVING_STACK.value, "Provider": CONFIG.provider},
            properties={"correlation_id": correlation_id},
        )
    )

    _log(
        correlation_id,
        event="resolved",
        status=result.status.value,
        confidence=round(result.confidence, 3),
        geo_source=result.geo.source.value if result.geo else None,
        has_digipin=result.digipin is not None,
        n_landmarks=len(result.structured.landmarks),
        # Note what is absent: the raw address text is never logged. It routinely
        # contains a phone number the customer typed in, and CloudWatch is not a
        # place to put customer PII (NFR-25). The correlation id is enough to
        # follow one request across every stage.
        elapsed_ms=round(elapsed_ms, 1),
        stages={k: round(v, 2) for k, v in result.timings_ms.items()},
    )

    return _respond(200, payload)
