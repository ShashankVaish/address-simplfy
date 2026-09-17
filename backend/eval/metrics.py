"""Evaluation metrics.

Seven numbers, each chosen because it can move independently of the others. A
single headline score would hide the trade-off that matters most here: a system
can raise exact-match accuracy simply by asking the customer more questions, and
every question is friction for a real person. So auto-resolution rate and
clarification rate are reported *alongside* accuracy, never folded into it.

Definitions are fixed here, once, because a metric that is computed slightly
differently between Thursday and Sunday produces an improvement that is not
real.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

_EARTH_R_M = 6_371_000.0

# The scalar fields scored by field-level F1. `landmarks` is excluded because it
# is a list and is scored separately.
SCORED_FIELDS: tuple[str, ...] = (
    "building",
    "street",
    "sub_locality",
    "locality",
    "city",
    "district",
    "state",
    "pincode",
)

# Fields weighted above their share in the average, because getting them wrong
# actually fails a delivery. State and district are nearly free to infer from
# the pincode, so being right about them is not evidence of much.
FIELD_WEIGHTS: dict[str, float] = {
    "building": 3.0,
    "street": 1.5,
    "sub_locality": 1.5,
    "locality": 2.0,
    "city": 1.0,
    "district": 0.5,
    "state": 0.5,
    "pincode": 2.0,
}


def haversine_m(a_lat: float, a_lng: float, b_lat: float, b_lng: float) -> float:
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp = p2 - p1
    dl = math.radians(b_lng - a_lng)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * _EARTH_R_M * math.asin(math.sqrt(h))


def normalise_value(value: Any) -> str | None:
    """Canonical form for comparing two field values.

    Case, surrounding punctuation and internal spacing are not errors worth
    penalising -- "H.No. 14", "h no 14" and "14" describe the same doorstep, and
    a metric that marks two of them wrong measures formatting, not resolution.
    Content differences are still caught: "14" and "41" remain distinct.
    """
    if value is None:
        return None
    text = str(value).strip().casefold()
    if not text or text in {"null", "none", "n/a", "na", "-"}:
        return None
    # Drop punctuation and collapse whitespace.
    text = "".join(ch if ch.isalnum() or ch.isspace() else " " for ch in text)
    return " ".join(text.split()) or None


@dataclass
class FieldScore:
    """Per-field precision, recall and F1.

    A prediction of `None` where truth is `None` is *correct*, but it is not a
    true positive -- counting it as one would let a system that predicts nothing
    at all score perfectly on sparse fields. It is tracked separately as
    `true_negatives`.
    """

    field: str
    true_positives: int = 0
    false_positives: int = 0
    false_negatives: int = 0
    true_negatives: int = 0
    # Predicted a value, truth had one, but they disagree. Counted as both a
    # false positive and a false negative, and surfaced separately because a
    # wrong value is operationally worse than a missing one.
    wrong_values: int = 0

    @property
    def precision(self) -> float:
        denom = self.true_positives + self.false_positives
        return self.true_positives / denom if denom else 1.0

    @property
    def recall(self) -> float:
        denom = self.true_positives + self.false_negatives
        return self.true_positives / denom if denom else 1.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0

    @property
    def support(self) -> int:
        return self.true_positives + self.false_negatives


def score_fields(
    predictions: Sequence[dict[str, Any]],
    truths: Sequence[dict[str, Any]],
    fields: Iterable[str] = SCORED_FIELDS,
) -> dict[str, FieldScore]:
    """Per-field confusion counts over a whole split."""
    if len(predictions) != len(truths):
        raise ValueError(
            f"prediction/truth length mismatch: {len(predictions)} vs {len(truths)}"
        )

    scores = {f: FieldScore(field=f) for f in fields}
    for pred, truth in zip(predictions, truths, strict=True):
        for f, score in scores.items():
            p = normalise_value(pred.get(f))
            t = normalise_value(truth.get(f))
            if p is None and t is None:
                score.true_negatives += 1
            elif p is not None and t is None:
                score.false_positives += 1  # invented a value (FR-02 violation)
            elif p is None and t is not None:
                score.false_negatives += 1
            elif p == t:
                score.true_positives += 1
            else:
                score.wrong_values += 1
                score.false_positives += 1
                score.false_negatives += 1
    return scores


def measured_fields(scores: dict[str, FieldScore]) -> dict[str, FieldScore]:
    """Only the fields the answer key actually exercises.

    A field with zero support carries no information but still lands in the
    average: with no truth values anywhere, recall is vacuously 1.0, so the
    field scores 1.000 if the system also predicts nothing and 0.000 if it
    predicts anything. Averaging those in moves the headline F1 by several
    points for reasons that have nothing to do with resolution quality. They are
    reported separately instead, as unmeasured.
    """
    return {f: s for f, s in scores.items() if s.support > 0}


def unmeasured_fields(scores: dict[str, FieldScore]) -> list[str]:
    return [f for f, s in scores.items() if s.support == 0]


def macro_f1(scores: dict[str, FieldScore]) -> float:
    """Unweighted mean F1 across the fields with non-zero support."""
    measured = measured_fields(scores)
    if not measured:
        return 0.0
    return statistics.fmean(s.f1 for s in measured.values())


def weighted_f1(scores: dict[str, FieldScore]) -> float:
    """Mean F1 weighted by operational importance (see FIELD_WEIGHTS)."""
    measured = measured_fields(scores)
    total = sum(FIELD_WEIGHTS.get(f, 1.0) for f in measured)
    if not total:
        return 0.0
    return sum(s.f1 * FIELD_WEIGHTS.get(f, 1.0) for f, s in measured.items()) / total


def exact_match_rate(
    predictions: Sequence[dict[str, Any]],
    truths: Sequence[dict[str, Any]],
    fields: Iterable[str] = SCORED_FIELDS,
) -> float:
    """Fraction of addresses where *every* scored field is right.

    Strict and unforgiving by design. One wrong field fails the address, which
    is the right bar: a rider does not get 87% of the way to a doorstep.
    """
    fields = tuple(fields)
    if not predictions:
        return 0.0
    # Ignore fields the answer key never populates; otherwise a correct
    # extraction the key simply does not record would fail every address.
    populated = {
        f for f in fields if any(normalise_value(t.get(f)) is not None for t in truths)
    }
    fields = tuple(f for f in fields if f in populated) or fields
    hits = sum(
        all(normalise_value(p.get(f)) == normalise_value(t.get(f)) for f in fields)
        for p, t in zip(predictions, truths, strict=True)
    )
    return hits / len(predictions)


def geocode_errors_m(
    predictions: Sequence[dict[str, Any]],
    truths: Sequence[dict[str, Any]],
) -> list[float]:
    """Haversine error per address that produced a coordinate.

    Addresses with no predicted coordinate are omitted rather than scored as
    infinite: they are already counted against the system by the
    auto-resolution rate, and mixing the two would make the geocode figure
    depend on how often the system declines to answer.
    """
    out: list[float] = []
    for pred, truth in zip(predictions, truths, strict=True):
        pgeo, tgeo = pred.get("geo"), truth.get("geo")
        if not pgeo or not tgeo:
            continue
        if pgeo.get("lat") is None or tgeo.get("lat") is None:
            continue
        out.append(haversine_m(pgeo["lat"], pgeo["lng"], tgeo["lat"], tgeo["lng"]))
    return out


@dataclass
class RunMetrics:
    """The full metric set for one configuration on one split."""

    n: int
    field_scores: dict[str, FieldScore]
    macro_f1: float
    weighted_f1: float
    exact_match: float
    geo_median_m: float | None
    geo_p90_m: float | None
    geo_coverage: float
    auto_resolution_rate: float
    clarification_rate: float
    ambiguous_rate: float
    precision_at_threshold: float
    threshold: float
    latency_p50_ms: float | None = None
    latency_p95_ms: float | None = None
    notes: list[str] = field(default_factory=list)

    def as_row(self) -> dict[str, Any]:
        """One row of the ablation table."""
        return {
            "n": self.n,
            "field_f1": round(self.macro_f1, 4),
            "weighted_f1": round(self.weighted_f1, 4),
            "exact_match": round(self.exact_match, 4),
            "geo_median_m": round(self.geo_median_m, 1)
            if self.geo_median_m is not None
            else None,
            "geo_p90_m": round(self.geo_p90_m, 1)
            if self.geo_p90_m is not None
            else None,
            "geo_coverage": round(self.geo_coverage, 4),
            "auto_resolution_rate": round(self.auto_resolution_rate, 4),
            "clarification_rate": round(self.clarification_rate, 4),
            "precision_at_threshold": round(self.precision_at_threshold, 4),
        }


def percentile(values: Sequence[float], q: float) -> float:
    """Nearest-rank percentile. No numpy dependency in the Lambda layer."""
    if not values:
        raise ValueError("percentile of empty sequence")
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, math.ceil(q * len(ordered)) - 1))
    return ordered[idx]


def evaluate(
    predictions: Sequence[dict[str, Any]],
    truths: Sequence[dict[str, Any]],
    *,
    threshold: float = 0.80,
) -> RunMetrics:
    """Compute every metric for one run.

    `predictions` are `Resolution`-shaped dicts; `truths` carry `structured`-like
    fields plus a `geo`.
    """
    n = len(predictions)
    if n == 0:
        raise ValueError("no predictions to evaluate")

    pred_structured = [p.get("structured") or {} for p in predictions]
    scores = score_fields(pred_structured, truths)

    errors = geocode_errors_m(predictions, truths)

    statuses = [p.get("status") for p in predictions]
    auto = sum(1 for s in statuses if s == "RESOLVED") / n
    clarify = sum(1 for s in statuses if s == "NEEDS_INFO") / n
    ambiguous = sum(1 for s in statuses if s == "AMBIGUOUS") / n

    # Precision at threshold: of the addresses we auto-resolved above the
    # threshold, how many were completely correct. This is the safety metric --
    # the one that decides whether the threshold is allowed to move.
    above = [
        (p, t)
        for p, t in zip(predictions, truths, strict=True)
        if (p.get("confidence") or 0.0) >= threshold
    ]
    if above:
        correct = sum(
            all(
                normalise_value((p.get("structured") or {}).get(f))
                == normalise_value(t.get(f))
                for f in SCORED_FIELDS
            )
            for p, t in above
        )
        precision_at = correct / len(above)
    else:
        # No address cleared the threshold. Reporting 1.0 here would be a lie by
        # vacuous truth, so it is 0.0 and the note says why.
        precision_at = 0.0

    latencies = [
        sum((p.get("timings_ms") or {}).values())
        for p in predictions
        if p.get("timings_ms")
    ]

    notes: list[str] = []
    if not above:
        notes.append(
            f"no address reached the {threshold:.2f} threshold; "
            f"precision_at_threshold reported as 0.0 rather than vacuously 1.0"
        )
    unmeasured = unmeasured_fields(scores)
    if unmeasured:
        notes.append(
            "fields with no truth values anywhere in this split, excluded from "
            f"F1 and exact match as unmeasured: {', '.join(unmeasured)}"
        )
    if len(errors) < n:
        notes.append(
            f"{n - len(errors)} of {n} addresses produced no coordinate and are "
            f"excluded from geocode error (see geo_coverage)"
        )

    return RunMetrics(
        n=n,
        field_scores=scores,
        macro_f1=macro_f1(scores),
        weighted_f1=weighted_f1(scores),
        exact_match=exact_match_rate(pred_structured, truths),
        geo_median_m=statistics.median(errors) if errors else None,
        geo_p90_m=percentile(errors, 0.90) if errors else None,
        geo_coverage=len(errors) / n,
        auto_resolution_rate=auto,
        clarification_rate=clarify,
        ambiguous_rate=ambiguous,
        precision_at_threshold=precision_at,
        threshold=threshold,
        latency_p50_ms=statistics.median(latencies) if latencies else None,
        latency_p95_ms=percentile(latencies, 0.95) if latencies else None,
        notes=notes,
    )


def format_report(m: RunMetrics, *, title: str = "run") -> str:
    """Human-readable summary for the terminal and for docs/results."""
    lines = [
        f"{title}  (n={m.n})",
        "",
        f"  field F1 (macro)        {m.macro_f1:.3f}",
        f"  field F1 (weighted)     {m.weighted_f1:.3f}",
        f"  full-address exact      {m.exact_match:.3f}",
    ]
    if m.geo_median_m is not None:
        lines += [
            f"  geocode error median    {m.geo_median_m:,.0f} m",
            f"  geocode error p90       {m.geo_p90_m:,.0f} m",
            f"  geocode coverage        {m.geo_coverage:.3f}",
        ]
    else:
        lines.append("  geocode error           n/a (no coordinates produced)")
    lines += [
        f"  auto-resolution rate    {m.auto_resolution_rate:.3f}",
        f"  clarification rate      {m.clarification_rate:.3f}",
        f"  ambiguous rate          {m.ambiguous_rate:.3f}",
        f"  precision @ {m.threshold:.2f}        {m.precision_at_threshold:.3f}",
    ]
    if m.latency_p50_ms is not None:
        lines += [
            f"  latency p50             {m.latency_p50_ms:,.1f} ms",
            f"  latency p95             {m.latency_p95_ms:,.1f} ms",
        ]
    lines += ["", "  per-field:"]
    for name, s in m.field_scores.items():
        if s.support == 0:
            lines.append(f"    {name:<14} unmeasured (no truth values in this split)")
            continue
        lines.append(
            f"    {name:<14} F1 {s.f1:.3f}  P {s.precision:.3f}  R {s.recall:.3f}"
            f"  support {s.support:<4} wrong {s.wrong_values}"
        )
    if m.notes:
        lines += ["", "  notes:"]
        lines += [f"    - {note}" for note in m.notes]
    return "\n".join(lines)
