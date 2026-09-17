"""S7 -- decide: RESOLVED, NEEDS_INFO, or AMBIGUOUS.

Three outcomes and no fourth. The interesting one is AMBIGUOUS, because it is
the state most systems do not have: two readings that are both plausible and
far apart on the ground. A single-answer API has to pick one, and picking one
is how a parcel ends up 4 km away with a confident-looking confidence score.
Surfacing both to a human is slower and correct.

The order of the checks matters. Ambiguity is tested *before* the confidence
threshold, because two candidates 4 km apart with near-equal scores can average
out to a perfectly respectable confidence. A threshold alone would wave that
through.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from patasetu.config import Config
from patasetu.models import (
    Alternative,
    Clarification,
    Geo,
    Status,
    StructuredAddress,
)
from patasetu.retrieve import Candidate

# Priority order for the one question we are allowed to ask. Asking about the
# flat number while the locality is also unknown wastes it: the answer would
# not be enough to resolve the address either way.
QUESTION_PRIORITY: tuple[tuple[str, str, str], ...] = (
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
        "building",
        "Flat ya makaan number kya hai?",
        "What is the flat or house number?",
    ),
    (
        "landmark",
        "Koi nishani bataiye - school, bank, ya bada mandir?",
        "Any nearby landmark - a school, a bank, or a temple?",
    ),
)

# Asked when every field is present but the location is still unverified. A map
# tap, not typing.
GEO_CONFIRM_HI = (
    "Address mil gaya, lekin exact location confirm karni hai. "
    "Map par apna ghar tap kar dijiye."
)
GEO_CONFIRM_EN = (
    "We have your address but not the exact spot. "
    "Please tap your building on the map."
)


@dataclass
class Decision:
    status: Status
    clarification: Clarification | None = None
    alternatives: list[Alternative] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)


def find_ambiguity(
    candidates: list[Candidate], cfg: Config
) -> tuple[Candidate, Candidate] | None:
    """Two near-equal candidates that are far apart on the ground.

    Both conditions are required. Near-equal scores for two landmarks 20 m
    apart do not matter -- either choice puts the rider in the right place.
    Scores far apart do not matter either, however distant the candidates:
    the winner is a clear winner. It is only the conjunction that is dangerous.
    """
    if len(candidates) < 2:
        return None

    best = candidates[0]
    best_score = best.rrf_score or 0.0
    if best_score <= 0.0:
        return None

    for rival in candidates[1:]:
        relative_gap = (best_score - rival.rrf_score) / best_score
        if relative_gap > cfg.thresholds.ambiguous_score_delta:
            # Candidates are sorted by score, so once the gap is wide enough,
            # every later candidate is further behind still.
            return None
        separation = _haversine_m(
            best.record.lat, best.record.lng, rival.record.lat, rival.record.lng
        )
        if separation >= cfg.thresholds.ambiguous_spread_m:
            return best, rival
    return None


def _haversine_m(a_lat: float, a_lng: float, b_lat: float, b_lng: float) -> float:
    from patasetu.gazetteer import haversine_m

    return haversine_m(a_lat, a_lng, b_lat, b_lng)


def choose_question(
    structured: StructuredAddress, *, devanagari: bool
) -> Clarification:
    """Pick the single field whose absence costs the most, and ask about it.

    One question, never a form (FR-16). The language mirrors the script of the
    incoming address (FR-17), because a Hindi question to someone who wrote in
    Hindi gets answered and an English one often does not.
    """
    for name, hindi, english in QUESTION_PRIORITY:
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

    # Nothing is missing, yet confidence is below the threshold. The cause is
    # not an absent field: it is an unverified *location* -- landmarks that
    # matched nothing, or a coordinate that is only a pincode centroid. Asking
    # for the house number here, when we already have it, is how customers
    # learn to ignore us.
    return Clarification(
        field="geo_confirm",
        question=GEO_CONFIRM_HI if devanagari else GEO_CONFIRM_EN,
        language="hi-IN" if devanagari else "en-IN",
    )


def decide(
    *,
    confidence: float,
    structured: StructuredAddress,
    geo: Geo | None,
    candidates: list[Candidate],
    cfg: Config,
    devanagari: bool = False,
    model_alternatives: list[dict[str, object]] | None = None,
) -> Decision:
    """Run S7."""
    evidence: list[str] = []

    # --- 1. ambiguity, before any threshold --------------------------------
    pair = find_ambiguity(candidates, cfg)
    if pair is not None:
        best, rival = pair
        separation = _haversine_m(
            best.record.lat, best.record.lng, rival.record.lat, rival.record.lng
        )
        evidence.append(
            f"AMBIGUOUS: '{best.record.canonical_name}' and "
            f"'{rival.record.canonical_name}' score within "
            f"{cfg.thresholds.ambiguous_score_delta:.0%} of each other but are "
            f"{separation:.0f} m apart; both are returned rather than guessing"
        )
        alternatives = [
            Alternative(
                structured=structured.model_copy(deep=True),
                geo=Geo(
                    lat=candidate.record.lat,
                    lng=candidate.record.lng,
                    source=geo.source if geo else None,  # type: ignore[arg-type]
                    accuracy_m=geo.accuracy_m if geo else None,
                )
                if geo is not None
                else None,
                confidence=round(confidence, 4),
                reason=(
                    f"landmark {candidate.landmark_id} "
                    f"('{candidate.record.canonical_name}'), "
                    f"fused score {candidate.rrf_score:.4f}"
                ),
            )
            for candidate in (best, rival)
        ]
        return Decision(
            status=Status.AMBIGUOUS, alternatives=alternatives, evidence=evidence
        )

    # --- 2. the model itself reported two readings -------------------------
    if model_alternatives and len(model_alternatives) >= 1:
        evidence.append(
            f"AMBIGUOUS: the model returned {len(model_alternatives)} alternative "
            f"reading(s) rather than one; surfacing them instead of choosing"
        )
        alternatives = [
            Alternative(
                structured=structured.model_copy(
                    update={
                        k: v
                        for k, v in (alt.get("fields") or {}).items()  # type: ignore[union-attr]
                        if hasattr(structured, k)
                    }
                ),
                geo=geo,
                confidence=round(confidence, 4),
                reason=str(alt.get("reason") or "alternative reading")[:300],
            )
            for alt in model_alternatives
        ]
        # An AMBIGUOUS response must carry at least two options to be useful, so
        # the primary reading is included as the first alternative.
        alternatives.insert(
            0,
            Alternative(
                structured=structured,
                geo=geo,
                confidence=round(confidence, 4),
                reason="primary reading",
            ),
        )
        return Decision(
            status=Status.AMBIGUOUS, alternatives=alternatives, evidence=evidence
        )

    # --- 3. the threshold --------------------------------------------------
    if confidence >= cfg.thresholds.resolved_at:
        evidence.append(
            f"RESOLVED: confidence {confidence:.2f} is at or above the "
            f"{cfg.thresholds.resolved_at:.2f} threshold"
        )
        return Decision(status=Status.RESOLVED, evidence=evidence)

    clarification = choose_question(structured, devanagari=devanagari)
    evidence.append(
        f"NEEDS_INFO: confidence {confidence:.2f} is below the "
        f"{cfg.thresholds.resolved_at:.2f} threshold; asking exactly one "
        f"question, about '{clarification.field}', in {clarification.language}"
    )
    return Decision(
        status=Status.NEEDS_INFO, clarification=clarification, evidence=evidence
    )
