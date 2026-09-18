"""The resolution pipeline: S0 through S7, with stages switchable.

One function, `resolve`, runs the pipeline at a chosen `Stack`. The stack is
what makes the ablation table a measurement rather than a comparison of
different programs -- configurations A to E are this same code path with
progressively more stages enabled.

    A  deterministic only          S0, S1, centroid geocode, S5-S7
    B  + a single LLM call         adds S3, no retrieval
    C  + BM25 landmark retrieval   adds S2, lexical only
    D  + vector and geo retrieval  full hybrid S2
    E  + a warmed landmark graph   D against a graph carrying observations

Two diagnostic stacks, R1 and R2, run retrieval *without* a model. They are not
part of the judged five-row table. They exist because they can be measured
before Bedrock access is granted, and because they isolate what retrieval
contributes to geocoding from what the model contributes to field extraction.
Reporting them separately is how the ablation stays honest about which numbers
came from where.
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
from patasetu import geocode as geocode_stage
from patasetu import retrieve as retrieve_stage
from patasetu.cache import ResolutionCache
from patasetu.config import Config
from patasetu.config import load as load_config
from patasetu.decide import decide as decide_stage
from patasetu.digipin import DigipinError
from patasetu.digipin import encode as digipin_encode
from patasetu.models import (
    Geo,
    Landmark,
    Relation,
    Resolution,
    StructuredAddress,
)
from patasetu.normalize import Normalised, normalise
from patasetu.parse import ParseResult, parse
from patasetu.providers import Providers, ProviderUnavailable
from patasetu.structure import structure as structure_stage


class Stack(StrEnum):
    """Ablation configurations. A-E match docs/results/ablation.md."""

    A_DETERMINISTIC = "A"
    B_LLM_ONLY = "B"
    C_BM25 = "C"
    D_HYBRID = "D"
    E_WARM_GRAPH = "E"
    # Diagnostics: retrieval with no model. Measurable without Bedrock.
    R1_BM25_NO_LLM = "R1"
    R2_HYBRID_NO_LLM = "R2"

    @property
    def uses_model(self) -> bool:
        return self in (
            Stack.B_LLM_ONLY,
            Stack.C_BM25,
            Stack.D_HYBRID,
            Stack.E_WARM_GRAPH,
        )

    @property
    def uses_retrieval(self) -> bool:
        return self in (
            Stack.C_BM25,
            Stack.D_HYBRID,
            Stack.E_WARM_GRAPH,
            Stack.R1_BM25_NO_LLM,
            Stack.R2_HYBRID_NO_LLM,
        )

    @property
    def uses_vector(self) -> bool:
        """BM25-only for C and R1; the full hybrid for D, E and R2."""
        return self in (
            Stack.D_HYBRID,
            Stack.E_WARM_GRAPH,
            Stack.R2_HYBRID_NO_LLM,
        )

    @property
    def uses_geo_signal(self) -> bool:
        return self.uses_vector

    @property
    def is_diagnostic(self) -> bool:
        return self in (Stack.R1_BM25_NO_LLM, Stack.R2_HYBRID_NO_LLM)


class StageUnavailable(RuntimeError):
    """A stage this configuration needs is not wired up or not reachable."""


@dataclass
class Stopwatch:
    """Per-stage timings, for the latency budget and the metrics screen."""

    timings: dict[str, float]

    def time(self, stage: str) -> Callable[[], None]:
        start = time.perf_counter()

        def stop() -> None:
            self.timings[stage] = (time.perf_counter() - start) * 1000.0

        return stop


_SCALAR_FIELDS = (
    "building",
    "street",
    "sub_locality",
    "locality",
    "city",
    "district",
    "state",
    "pincode",
)


def _structured_from_parse(p: ParseResult) -> StructuredAddress:
    """Project S1's output into the wire contract. Invents nothing (FR-02)."""
    building = p.building
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


def _build_features(
    *,
    parsed: ParseResult,
    geo: Geo | None,
    structured: StructuredAddress,
    retrieval: retrieve_stage.RetrievalResult | None,
    model_self_confidence: float | None,
    cfg: Config,
) -> conf.Features:
    """Assemble the seven S6 features from whatever the stages produced."""
    f = conf.Features()
    f.field_completeness = conf.field_completeness(structured.filled_fields())

    if parsed.locality_agrees is True:
        f.pincode_locality_agreement = 1.0
    elif parsed.locality_agrees is False:
        f.pincode_locality_agreement = 0.35
    else:
        f.pincode_locality_agreement = 0.5

    if geo is not None:
        f.geo_source_tier = conf.GEO_TIER[geo.source]

    if retrieval is not None and retrieval.candidates:
        best = retrieval.candidates[0]
        f.landmark_match = retrieval.top_score
        f.landmark_observations = conf.observation_weight(best.record.observation_count)
        scores = [c.rrf_score for c in retrieval.candidates]
        distances = [
            gazetteer.haversine_m(
                best.record.lat, best.record.lng, c.record.lat, c.record.lng
            )
            for c in retrieval.candidates
        ]
        f.candidate_separation = conf.candidate_separation(scores, distances, cfg)
    else:
        # No retrieval ran, or it found nothing. Left at zero rather than filled
        # with a neutral 0.5: treating a *missing* signal as an average one is
        # how a weaker configuration ends up looking better than it is.
        f.landmark_match = 0.0
        f.landmark_observations = 0.0
        f.candidate_separation = 1.0

    f.model_self_confidence = (
        0.5 if model_self_confidence is None else model_self_confidence
    )

    if parsed.state_conflict:
        f.penalties.append(("pincode_state_conflict", 0.45))
    if parsed.pincode is None and parsed.pincode_candidates:
        f.penalties.append(("unknown_pincode_token", 0.85))
    if not parsed.pincode_known:
        f.penalties.append(("no_validated_pincode", 0.75))
    if retrieval is not None and retrieval.degraded:
        # Running degraded is not free: if the vector signal was unavailable we
        # genuinely know less, and the confidence has to say so.
        f.penalties.append(("retrieval_degraded", 0.92))

    return f


def _apply_fields(
    structured: StructuredAddress,
    fields: dict[str, str | None],
    landmarks: list[dict[str, Any]],
) -> StructuredAddress:
    """Merge S3's (or the matcher's) output over S1's.

    `structure.normalise_model_output` has already refused to overwrite
    deterministic values, so anything arriving here for a settled field is the
    deterministic value itself.
    """
    updates: dict[str, Any] = {
        name: value
        for name, value in fields.items()
        if value is not None and name in _SCALAR_FIELDS
    }
    if landmarks:
        updates["landmarks"] = [
            Landmark(
                name=item["name"],
                relation=Relation(item.get("relation", "near")),
                matched_id=item.get("matched_id"),
                match_score=item.get("match_score"),
                distance_m=item.get("distance_m"),
            )
            for item in landmarks
        ]
    return structured.model_copy(update=updates)


def _peek_pincode(norm: Normalised) -> str | None:
    """Cheap pincode sniff for the cache's near-duplicate bucket.

    Deliberately not the full S1 parse: the cache probe has to be cheaper than
    the work it avoids, and the bucket key only needs to be consistent.
    """
    chosen, _ = gazetteer.resolve_pincode(norm.text)
    return chosen


def _matched_candidates(
    structured: StructuredAddress,
    retrieval: retrieve_stage.RetrievalResult | None,
) -> list[retrieve_stage.Candidate]:
    """The candidates actually claimed by a landmark in the final answer.

    S4 must geocode from a landmark the *answer* asserts, not merely one that
    retrieval surfaced. A candidate that ranked well but which neither the model
    nor the matcher attached to any phrase is not evidence about this address.
    Order follows the answer's landmark order, so the first landmark named is
    the one geocoded from.
    """
    if retrieval is None:
        return []
    by_id = {c.landmark_id: c for c in retrieval.candidates}
    out: list[retrieve_stage.Candidate] = []
    for lm in structured.landmarks:
        if lm.matched_id and lm.matched_id in by_id:
            out.append(by_id[lm.matched_id])
    return out


def resolve(
    raw: str,
    *,
    stack: Stack = Stack.A_DETERMINISTIC,
    hint: dict[str, float] | None = None,
    cfg: Config | None = None,
    calibrator: conf.Calibrator | None = None,
    providers: Providers | None = None,
    cache: ResolutionCache | None = None,
    correlation_id: str | None = None,
) -> Resolution:
    """Run the pipeline and return a `Resolution`.

    Side-effect free apart from the cache: no EventBridge, no logging handlers,
    no metric publication. The Lambda handler adds those. That separation is
    what lets the whole pipeline be exercised by a CLI, by the test suite and by
    the ablation runner with no cloud at all.
    """
    cfg = cfg or load_config()
    calibrator = calibrator or conf.Calibrator.load()
    correlation_id = correlation_id or str(uuid.uuid4())

    watch = Stopwatch(timings={})
    evidence: list[str] = []

    # --- S0 normalise ------------------------------------------------------
    stop = watch.time("s0_normalise")
    norm = normalise(raw)
    stop()
    if norm.has_unsupported_script:
        evidence.append(
            "input contains an Indic script other than Devanagari; "
            "transliteration is not applied and coverage is unmeasured"
        )

    # --- S0b cache probe ---------------------------------------------------
    if cache is not None:
        stop = watch.time("s0_cache")
        hit = cache.lookup(
            cache_key=norm.cache_key,
            text=norm.text,
            pincode=_peek_pincode(norm),
        )
        stop()
        if hit is not None:
            cached = Resolution.model_validate(hit.resolution)
            return cached.model_copy(
                update={
                    "cached": True,
                    "correlation_id": correlation_id,
                    "timings_ms": watch.timings,
                    "evidence": [*hit.evidence, *cached.evidence],
                }
            )

    # --- S1 deterministic parse -------------------------------------------
    stop = watch.time("s1_parse")
    parsed = parse(norm.text)
    stop()
    evidence.extend(parsed.evidence)

    structured = _structured_from_parse(parsed)

    centre = parsed.centroid
    # The geo radius depends on what the centre *is*. A pincode centroid is the
    # middle of an area that can be tens of kilometres across in rural India --
    # 29% of the gold set sits more than 3 km from its own pincode centroid,
    # and a fixed 3 km filter was excluding the correct landmark for all of
    # them. So the radius scales with the pincode's estimated size. A GPS hint
    # is a rider near the doorstep and keeps the tight default.
    radius_m = cfg.retrieval.geo_radius_m
    if centre is not None and parsed.pincode:
        radius_m = max(
            radius_m, 2.5 * geocode_stage.centroid_accuracy_m(parsed.pincode)
        )
    if hint is not None:
        # A GPS hint is a better retrieval centre than a pincode centroid: the
        # rider is standing near the doorstep, the centroid is the middle of an
        # area. It constrains retrieval only and is never the answer (FR-07).
        centre = (hint["lat"], hint["lng"])
        radius_m = cfg.retrieval.geo_radius_m
        evidence.append(
            "GPS hint used to constrain candidate retrieval; it is not used as "
            "the resolved coordinate"
        )

    providers = providers or Providers(cfg)

    # The locality is searched for too, as the *anchor*. "X Post Office, near
    # Y" is a doorstep AT X, NEAR Y -- and if X is in the graph its coordinate
    # is the answer, not Y's. Without this, every address was geocoded from
    # whatever it was "near", which is by definition somewhere else.
    anchor_phrases: list[tuple[str, Relation]] = (
        [(structured.locality, Relation.INSIDE)] if structured.locality else []
    )

    # --- S2 retrieval ------------------------------------------------------
    retrieval: retrieve_stage.RetrievalResult | None = None
    if stack.uses_retrieval:
        stop = watch.time("s2_retrieve")
        try:
            retrieval = retrieve_stage.retrieve(
                landmark_names=[
                    *(name for name, _ in parsed.landmarks),
                    *(name for name, _ in anchor_phrases),
                ],
                retrieval_text=norm.retrieval_text,
                centre=centre,
                cfg=cfg,
                providers=providers,
                use_vector=stack.uses_vector,
                use_geo=stack.uses_geo_signal,
                radius_m=radius_m,
            )
        except ProviderUnavailable as exc:
            stop()
            raise StageUnavailable(
                f"configuration {stack.value} needs S2 retrieval: {exc}"
            ) from exc
        stop()
        evidence.extend(retrieval.evidence)

    # --- S3 structuring ----------------------------------------------------
    model_result = None
    if stack.uses_model:
        stop = watch.time("s3_structure")
        try:
            cheap = providers.cheap_model
            strong = providers.strong_model
        except ProviderUnavailable as exc:
            stop()
            raise StageUnavailable(
                f"configuration {stack.value} needs S3 structuring: {exc}"
            ) from exc

        model_result = structure_stage(
            raw_text=parsed.clean_text,
            deterministic={name: getattr(structured, name) for name in _SCALAR_FIELDS},
            # The prompt budget: more than a handful of candidates measurably
            # hurts selection accuracy and wastes tokens. The full list stays
            # available to the matcher and to S4.
            candidates=(
                retrieval.candidates[: cfg.retrieval.candidates_to_model]
                if retrieval
                else []
            ),
            cfg=cfg,
            cheap_model=cheap,
            strong_model=strong,
            multi_script=norm.is_multi_script,
        )
        stop()
        evidence.extend(model_result.evidence)

        if model_result.unavailable:
            raise StageUnavailable(
                f"configuration {stack.value} needs S3 structuring: "
                f"{'; '.join(model_result.evidence)}"
            )
        if model_result.failed:
            # The model was reachable but returned garbage twice. Degrade rather
            # than 500: the deterministic result is still useful, and the
            # confidence penalty below reflects what was lost.
            evidence.append(
                "proceeding with the deterministic result only; confidence is "
                "penalised because S3 did not contribute"
            )
        else:
            structured = _apply_fields(
                structured, model_result.fields, model_result.landmarks
            )
            for conflict in model_result.conflicts:
                evidence.append(f"conflict: {conflict}")

    # Retrieval ran without a model (diagnostic stacks): attach matches from
    # the retrieval result directly, so landmark ids still reach the response.
    if retrieval is not None and not stack.uses_model and parsed.landmarks:
        matches = retrieve_stage.attach_matches(
            parsed.landmarks, retrieval, providers.embedder
        )
        structured = _apply_fields(structured, {}, matches)

    # The anchor match is internal: it decides where S4 geocodes from, but it
    # is not reported as a "landmark" -- the customer did not name it as one.
    anchor: list[retrieve_stage.Candidate] = []
    if retrieval is not None and anchor_phrases:
        anchor_matches = retrieve_stage.attach_matches(
            anchor_phrases, retrieval, providers.embedder
        )
        by_id = {c.landmark_id: c for c in retrieval.candidates}
        anchor = [
            by_id[m["matched_id"]] for m in anchor_matches if m["matched_id"] in by_id
        ]
        if anchor:
            evidence.append(
                f"anchor: locality '{structured.locality}' matched "
                f"{anchor[0].landmark_id} ('{anchor[0].record.canonical_name}'); "
                f"the doorstep is inside it, so it is geocoded from here"
            )

    # --- S4 geocode --------------------------------------------------------
    stop = watch.time("s4_geocode")
    matched = _matched_candidates(structured, retrieval)
    relations = [lm.relation for lm in structured.landmarks if lm.matched_id]
    if anchor:
        # The anchor outranks any "near X": it is where the address is.
        matched = [anchor[0], *[c for c in matched if c is not anchor[0]]]
        relations = [Relation.INSIDE, *relations]
    geo_result = geocode_stage.geocode(
        candidates=matched,
        relations=relations or None,
        pincode=parsed.pincode,
        address_text=parsed.clean_text,
        geocoder=providers.geocoder,
        use_landmark_graph=stack.uses_retrieval,
    )
    stop()
    geo = geo_result.geo
    evidence.extend(geo_result.evidence)

    # --- S5 DIGIPIN --------------------------------------------------------
    stop = watch.time("s5_digipin")
    digipin: str | None = None
    if geo is not None:
        try:
            digipin = digipin_encode(geo.lat, geo.lng)
            evidence.append(f"DIGIPIN {digipin} computed locally from the coordinate")
        except DigipinError as exc:
            evidence.append(f"DIGIPIN not computed: {exc}")
    stop()

    # --- S6 confidence -----------------------------------------------------
    stop = watch.time("s6_confidence")
    features = _build_features(
        parsed=parsed,
        geo=geo,
        structured=structured,
        retrieval=retrieval,
        model_self_confidence=(
            model_result.self_confidence
            if model_result is not None and not model_result.failed
            else None
        ),
        cfg=cfg,
    )
    if model_result is not None and model_result.failed:
        # A model outage caps confidence and forces a question, rather than
        # returning a confident deterministic-only answer as if S3 had run.
        features.penalties.append(("s3_unavailable", 0.6))

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
    decision = decide_stage(
        confidence=calibrated,
        structured=structured,
        geo=geo,
        candidates=retrieval.candidates if retrieval else [],
        cfg=cfg,
        devanagari="devanagari" in norm.scripts,
        model_alternatives=(
            model_result.alternatives if model_result is not None else None
        ),
    )
    stop()
    evidence.extend(decision.evidence)

    resolution = Resolution(
        status=decision.status,
        confidence=calibrated,
        structured=structured,
        geo=geo,
        digipin=digipin,
        evidence=evidence,
        clarification=decision.clarification,
        alternatives=decision.alternatives,
        correlation_id=correlation_id,
        timings_ms=watch.timings,
        cached=False,
    )

    # --- cache write -------------------------------------------------------
    if cache is not None:
        payload = resolution.model_dump(mode="json")
        if cache.should_cache(payload):
            cache.put(
                cache_key=norm.cache_key,
                text=norm.text,
                pincode=parsed.pincode,
                resolution=payload,
            )

    return resolution


def resolve_to_dict(raw: str, **kwargs: Any) -> dict[str, Any]:
    """`resolve`, serialised. What the Lambda returns and the evaluator scores."""
    return resolve(raw, **kwargs).model_dump(mode="json")
