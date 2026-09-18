"""CloudWatch custom metrics, via Embedded Metric Format.

EMF rather than `PutMetricData`: the metrics ride inside an ordinary log line,
which costs no API call on the hot path and adds no latency. CloudWatch parses
the `_aws` block out of the log and publishes the metrics itself.

The metric names are the ones the dashboard and the README quote, so they are
constants here and nowhere else. `CostPerAddress` is an estimate computed from
which model ran, because the actual invoice arrives days later and cannot be a
per-request number.
"""

from __future__ import annotations

import json
import time
from typing import Any

NAMESPACE = "PataSetu"

# Rough per-call cost in USD, for the CostPerAddress estimate. Illustrative
# rather than a price list: the point of the metric is the *shape* -- cache
# hits cost nothing, the cheap model costs little, escalation costs more -- and
# the dashboard reads that shape. Verify against current pricing on Day 0.
COST_USD = {
    "cache_hit": 0.0,
    "deterministic_only": 0.0,
    "cheap_model": 0.00025,
    "strong_model": 0.0045,
    "embedding": 0.00002,
    "opensearch_query": 0.00001,
}


def cost_estimate(
    *, cached: bool, model_used: str | None, escalated: bool, retrieval_ran: bool
) -> float:
    if cached:
        return COST_USD["cache_hit"]
    cost = 0.0
    if retrieval_ran:
        cost += COST_USD["embedding"] + COST_USD["opensearch_query"]
    if model_used:
        cost += COST_USD["strong_model"] if escalated else COST_USD["cheap_model"]
    return cost


def emf_record(
    metrics: dict[str, float],
    *,
    dimensions: dict[str, str] | None = None,
    properties: dict[str, Any] | None = None,
) -> str:
    """One EMF log line carrying every metric for a request."""
    dims = dimensions or {}
    return json.dumps(
        {
            "_aws": {
                "Timestamp": int(time.time() * 1000),
                "CloudWatchMetrics": [
                    {
                        "Namespace": NAMESPACE,
                        "Dimensions": [list(dims)] if dims else [[]],
                        "Metrics": [
                            {"Name": name, "Unit": _unit(name)} for name in metrics
                        ],
                    }
                ],
            },
            **dims,
            **metrics,
            **(properties or {}),
        }
    )


def _unit(name: str) -> str:
    if name.endswith("Ms"):
        return "Milliseconds"
    if name.startswith("Cost"):
        return "None"
    if name.endswith("Rate") or name.startswith("Is"):
        return "Count"
    return "None"


def request_metrics(
    resolution: dict[str, Any], *, elapsed_ms: float
) -> dict[str, float]:
    """The per-request metric set. Rates are emitted as 0/1 and averaged by CloudWatch."""
    status = resolution.get("status")
    geo = resolution.get("geo") or {}
    evidence = " ".join(resolution.get("evidence") or [])
    escalated = "escalated to" in evidence
    model_used = "structured by" in evidence
    retrieval_ran = "candidates retrieved" in evidence or "landmark '" in evidence
    cached = bool(resolution.get("cached"))

    return {
        "ResolutionRate": 1.0 if status == "RESOLVED" else 0.0,
        "ClarificationRate": 1.0 if status == "NEEDS_INFO" else 0.0,
        "AmbiguousRate": 1.0 if status == "AMBIGUOUS" else 0.0,
        "EscalationRate": 1.0 if escalated else 0.0,
        "CacheHitRate": 1.0 if cached else 0.0,
        "ModelCallRate": 1.0 if model_used else 0.0,
        "GeoFromGraphRate": 1.0 if geo.get("source") == "landmark_graph" else 0.0,
        "GeoFromCentroidRate": 1.0 if geo.get("source") == "pincode_centroid" else 0.0,
        "LatencyMs": round(elapsed_ms, 2),
        "CostPerAddress": cost_estimate(
            cached=cached,
            model_used="model" if model_used else None,
            escalated=escalated,
            retrieval_ran=retrieval_ran,
        ),
    }
