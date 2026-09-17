"""The resolution pipeline: S0 through S7, with stages switchable.

One function, `resolve`, runs the pipeline at a chosen `Stack`. The stack is
what makes the ablation table possible -- configurations A to E are the same
code path with progressively more stages enabled, so the reported gap between
them is a real measurement of what each stage contributes rather than a
comparison of different programs.

    A  deterministic only          S0, S1, centroid geocode, S5-S7
    B  + a single LLM call         adds S3 with no retrieval
    C  + BM25 landmark retrieval   adds lexical S2
    D  + vector and geo retrieval  full hybrid S2
    E  + a warmed landmark graph   D against a graph with observations

Day 1 implements A end to end. B to E raise `StageUnavailable` until their
providers are wired on Day 2, which keeps the runner honest: a configuration
that cannot run must not silently report the numbers of a weaker one.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from patasetu import confidence as conf
from patasetu import gazetteer
from patasetu.config import Config
from patasetu.config import load as load_config
from patasetu.digipin import DigipinError
from patasetu.digipin import encode as digipin_encode
from patasetu.models import (
    Clarification,
    Geo,
    GeoSource,
    Landmark,
    Resolution,
    Status,
    StructuredAddress,
)
from patasetu.normalize import normalise
from patasetu.parse import ParseResult, parse


class Stack(StrEnum):
    """Ablation configurations. The letters match docs/results/ablation.md."""

    A_DETERMINISTIC = "A"
    B_LLM_ONLY = "B"
    C_BM25 = "C"
    D_HYBRID = "D"
    E_WARM_GRAPH = "E"

    @property
    def uses_model(self) -> bool:
        return self is not Stack.A_DETERMINISTIC

    @property
    def uses_retrieval(self) -> bool:
        return self in (Stack.C_BM25, Stack.D_HYBRID, Stack.E_WARM_GRAPH)


class StageUnavailable(RuntimeError):
    """A stage this configuration needs is not wired up yet."""


@dataclass
class Stopwatch:
    """Per-stage timings, for the latency budget and the metrics screen."""

    timings: dict[str, float]

    def time(self, stage: str) -> Callable[[], None]:
        start = time.perf_counter()

        def stop() -> None:
            self.timings[stage] = (time.perf_counter() - start) * 1000.0

        return stop


# Which single missing field would most increase confidence, in priority order.
# Asking about the building number when the locality is also unknown wastes the
# one question we are allowed.
_QUESTION_PRIORITY: tuple[tuple[str, str, str], ...] = (
    (
        "building",
        "Flat ya makaan number kya hai?",
        "What is the flat or house number?",
    ),
    (
        "locality",
        "Aapka ilaaka ya colony ka naam kya hai?",
        "Which locality or colony is this?",
    ),
    (
        "pincode",
        "Pincode kya hai?",
        "What is the pincode?",
    ),
    (
        "landmark",
        "Koi nishani bataiye - school, bank, ya bada mandir?",
        "Any nearby landmark - a school, bank, or temple?",
    ),
)


def _structured_from_parse(p: ParseResult) -> StructuredAddress:
    """Project S1's output into the wire contract.

    Absent fields stay `None`. Nothing here invents a value (FR-02).
    """
    building = p.building
    # Floor, block and tower qualify the building rather than standing alone.
    extras = [
        x
        for x in (
            p.block and f"Block {p.block}",
            p.tower and f"Tower {p.tower}",
            p.floor and f"Floor {p.floor}",
        )
        if x
    ]
    if building and extras:
        building = f"{building}, " + ", ".join(extras)
    elif not building and extras:
        building = ", ".join(extras)

    return StructuredAddress(
        building=building,
        street=p.street,
        sub_locality=p.sub_locality,
        locality=p.locality,
        city=p.city,
        district=p.district,
        state=p.state,
        pincode=p.pincode,
        landmarks=[
            Landmark(name=name.title(), relation=relation)
            for name, relation in p.landmarks
        ],
    )


def _geocode_deterministic(p: ParseResult) -> Geo | None:
    """Configuration A's only geocoder: the pincode centroid.

    Marked `pincode_centroid` and given an honest accuracy radius, so nothing
    downstream can present it as a doorstep (NFR-15). The radius is derived
    from the pincode's own office spread where we have it, rather than a
    flat guess -- a dense urban pincode is genuinely tighter than a rural one.
    """
    if p.centroid is None:
        return None
    lat, lng = p.centroid
    info = gazetteer.lookup(p.pincode) if p.pincode else None
    # More offices in a pincode means a larger area, roughly.
    accuracy = 1_500.0 if (info and info.office_count <= 4) else 3_000.0
    return Geo(
        lat=lat,
        lng=lng,
        source=GeoSource.PINCODE_CENTROID,
        accuracy_m=accuracy,
    )


def _build_features(
    p: ParseResult, geo: Geo | None, structured: StructuredAddress
) -> conf.Features:
    f = conf.Features()
    f.field_completeness = conf.field_completeness(structured.filled_fields())

    if p.locality_agrees is True:
        f.pincode_locality_agreement = 1.0
    elif p.locality_agrees is False:
        f.pincode_locality_agreement = 0.35
    else:
        f.pincode_locality_agreement = 0.5

    if geo is not None:
        f.geo_source_tier = conf._GEO_TIER[geo.source]

    # No retrieval in configuration A, so there is no landmark match to score
    # and no candidate set to separate. Left at zero rather than filled with a
    # neutral value: pretending a missing signal is an average one is how a
    # deterministic-only baseline ends up looking better than it is.
    f.landmark_match = 0.0
    f.landmark_observations = 0.0
    f.candidate_separation = 1.0
    f.model_self_confidence = 0.5

    if p.state_conflict:
        # A pincode that contradicts the stated state is unresolvable from the
        # text alone. Multiplicative so no amount of completeness outvotes it.
        f.penalties.append(("pincode_state_conflict", 0.45))
    if p.pincode is None and p.pincode_candidates:
        f.penalties.append(("unknown_pincode_token", 0.85))
    if not p.pincode_known:
        f.penalties.append(("no_validated_pincode", 0.75))

    return f


def _choose_question(
    structured: StructuredAddress, p: ParseResult, devanagari: bool
) -> Clarification:
    """Pick the one field whose absence costs the most, and ask about it.

    One question, not a form (FR-16). Language mirrors the incoming script
    (FR-17).
    """
    for name, hindi, english in _QUESTION_PRIORITY:
        missing = (
            not structured.landmarks
            if name == "landmark"
            else getattr(structured, name, None) is None
        )
        if missing:
            return Clarification(
                field=name,
                question=hindi if devanagari else english,
                language="hi-IN" if devanagari else "en-IN",
            )

    # Nothing is missing, yet confidence is still below the threshold. The cause
    # is not an absent field -- it is an unverified location: landmarks that
    # matched nothing in the graph, or a coordinate that is only a pincode
    # centroid. Asking for the house number here would be absurd, since we
    # already have it, and it is exactly the kind of question that teaches
    # customers to ignore us. So we ask for a location confirmation instead,
    # which for this case is a map pin and a single tap rather than typing.
    return Clarification(
        field="geo_confirm",
        question=(
            "Address mil gaya, lekin exact location confirm karni hai. "
            "Map par apna ghar tap kar dijiye."
            if devanagari
            else "We have your address but not the exact spot. "
            "Please tap your building on the map."
        ),
        language="hi-IN" if devanagari else "en-IN",
    )


def resolve(
    raw: str,
    *,
    stack: Stack = Stack.A_DETERMINISTIC,
    hint: dict[str, float] | None = None,
    cfg: Config | None = None,
    calibrator: conf.Calibrator | None = None,
    correlation_id: str | None = None,
) -> Resolution:
    """Run the pipeline and return a `Resolution`.

    Pure and side-effect free: no DynamoDB, no EventBridge, no logging
    handlers. The Lambda handler wraps it with those. That separation is why
    the entire pipeline can be exercised by `scripts/resolve_one.py` and by the
    test suite with no cloud at all.
    """
    cfg = cfg or load_config()
    calibrator = calibrator or conf.Calibrator.load()
    correlation_id = correlation_id or str(uuid.uuid4())

    watch = Stopwatch(timings={})
    evidence: list[str] = []

    # --- S0 normalise -----------------------------------------------------
    stop = watch.time("s0_normalise")
    norm = normalise(raw)
    stop()
    if norm.has_unsupported_script:
        evidence.append(
            "input contains an Indic script other than Devanagari; "
            "transliteration is not applied and coverage is unmeasured"
        )

    # --- S1 deterministic parse -------------------------------------------
    stop = watch.time("s1_parse")
    parsed = parse(norm.text)
    stop()
    evidence.extend(parsed.evidence)

    structured = _structured_from_parse(parsed)

    # --- S2/S3 retrieval and structuring ----------------------------------
    if stack.uses_retrieval:
        raise StageUnavailable(
            f"configuration {stack.value} needs S2 retrieval (OpenSearch), "
            f"which is wired on Day 2"
        )
    if stack.uses_model:
        raise StageUnavailable(
            f"configuration {stack.value} needs S3 structuring (Bedrock), "
            f"which is wired on Day 2"
        )

    # --- S4 geocode --------------------------------------------------------
    stop = watch.time("s4_geocode")
    geo = _geocode_deterministic(parsed)
    stop()
    if geo is not None:
        evidence.append(
            f"geocoded from {geo.source.value} at "
            f"({geo.lat:.5f}, {geo.lng:.5f}), accuracy ~{geo.accuracy_m:.0f} m"
        )
        if geo.source is GeoSource.PINCODE_CENTROID:
            evidence.append(
                "this is a pincode centroid, NOT a doorstep -- do not route a "
                "rider to it as a final destination"
            )
    else:
        evidence.append("no coordinate could be derived: no validated pincode")

    # --- S5 DIGIPIN --------------------------------------------------------
    stop = watch.time("s5_digipin")
    digipin: str | None = None
    if geo is not None:
        try:
            digipin = digipin_encode(geo.lat, geo.lng)
            evidence.append(f"DIGIPIN {digipin} computed locally from the coordinate")
        except DigipinError as exc:
            # A coordinate outside the national grid means an upstream error, not
            # a DIGIPIN problem. Recorded rather than raised: the rest of the
            # resolution is still useful.
            evidence.append(f"DIGIPIN not computed: {exc}")
    stop()

    # --- S6 confidence -----------------------------------------------------
    stop = watch.time("s6_confidence")
    features = _build_features(parsed, geo, structured)
    raw_score = conf.score(features)
    calibrated = calibrator(raw_score)
    stop()
    evidence.append(
        f"confidence {calibrated:.2f} from raw {raw_score:.2f} "
        f"({calibrator.describe()})"
    )
    for reason, factor in features.penalties:
        evidence.append(f"penalty applied: {reason} (x{factor})")

    # --- S7 decide ---------------------------------------------------------
    stop = watch.time("s7_decide")
    devanagari = "devanagari" in norm.scripts
    clarification: Clarification | None = None

    if calibrated >= cfg.thresholds.resolved_at:
        status = Status.RESOLVED
    else:
        status = Status.NEEDS_INFO
        clarification = _choose_question(structured, parsed, devanagari)
        evidence.append(
            f"below threshold {cfg.thresholds.resolved_at:.2f}: asking one "
            f"question about '{clarification.field}'"
        )
    stop()

    return Resolution(
        status=status,
        confidence=calibrated,
        structured=structured,
        geo=geo,
        digipin=digipin,
        evidence=evidence,
        clarification=clarification,
        alternatives=[],
        correlation_id=correlation_id,
        timings_ms=watch.timings,
        cached=False,
    )


def resolve_to_dict(raw: str, **kwargs: Any) -> dict[str, Any]:
    """`resolve`, serialised. What the Lambda returns and the evaluator scores."""
    return resolve(raw, **kwargs).model_dump(mode="json")
