"""S6 -- confidence scoring.

A confidence number nobody has validated is decoration. This module produces a
*raw* score from interpretable features; Day 3 fits isotonic regression from
that raw score onto observed correctness on the dev split, and the calibrated
value is what ships. Until the calibrator exists, `calibrate` is the identity
and says so, because a raw score presented as a probability is exactly the
dishonesty the reliability diagram is meant to expose.

The model's own self-reported confidence is a *feature*, never the answer. LLMs
are poorly calibrated and confidently wrong in precisely the cases that matter
here.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import asdict, dataclass, field
from typing import Any

from patasetu.config import DATA_DIR, Config
from patasetu.models import GeoSource

# Weights over the features below. Hand-set for Day 1 and Day 2; the isotonic
# fit on Day 3 corrects the *mapping to probability*, not these weights, so they
# only need to be sensibly ordered rather than optimal.
WEIGHTS: dict[str, float] = {
    "field_completeness": 0.22,
    "pincode_locality_agreement": 0.20,
    "landmark_match": 0.16,
    "landmark_observations": 0.08,
    "geo_source_tier": 0.20,
    "candidate_separation": 0.08,
    "model_self_confidence": 0.06,
}

# Per-field contribution to completeness. A flat number matters far more than a
# state, which the pincode gives us for free.
_FIELD_VALUE: dict[str, float] = {
    "building": 0.28,
    "locality": 0.20,
    "pincode": 0.18,
    "sub_locality": 0.12,
    "street": 0.08,
    "city": 0.08,
    "district": 0.03,
    "state": 0.03,
}

GEO_TIER: dict[GeoSource, float] = {
    GeoSource.LANDMARK_GRAPH: 1.0,
    GeoSource.GEOCODER: 0.7,
    GeoSource.PINCODE_CENTROID: 0.3,
}


@dataclass
class Features:
    """The seven features, each in [0,1] and each independently inspectable.

    Keeping them as a named record rather than a bare vector is what lets the
    playground show *why* a confidence is what it is, and what makes a
    regression debuggable on Sunday morning.
    """

    field_completeness: float = 0.0
    # 1.0 agrees, 0.0 contradicts, 0.5 no evidence either way.
    pincode_locality_agreement: float = 0.5
    landmark_match: float = 0.0
    landmark_observations: float = 0.0
    geo_source_tier: float = 0.0
    candidate_separation: float = 1.0
    model_self_confidence: float = 0.5

    # Penalties applied after the weighted blend. These are not features: a
    # hard contradiction should not be something a high completeness score can
    # outvote (NFR-14).
    penalties: list[tuple[str, float]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def field_completeness(filled: set[str]) -> float:
    """Weighted share of the fields that matter, present."""
    total = sum(_FIELD_VALUE.values())
    got = sum(_FIELD_VALUE.get(f, 0.0) for f in filled)
    return min(1.0, got / total)


def observation_weight(count: int) -> float:
    """Log-scaled trust in a landmark's coordinate.

    Seen once is barely evidence; seen 47 times is a surveyed point. Log rather
    than linear because the difference between 1 and 5 observations is far more
    informative than between 45 and 50. Saturates near 50.
    """
    if count <= 0:
        return 0.0
    return min(1.0, math.log1p(count) / math.log1p(50))


def candidate_separation(
    scores: list[float], distances_m: list[float], cfg: Config
) -> float:
    """1.0 when the top candidate is a clear winner, 0.0 when it is a coin flip.

    Two candidates with near-equal scores that are kilometres apart is the
    situation where a confident answer is most dangerous, and it is what S7
    turns into AMBIGUOUS rather than a guess.
    """
    if len(scores) < 2:
        return 1.0
    ordered = sorted(scores, reverse=True)
    gap = ordered[0] - ordered[1]
    if gap >= cfg.thresholds.ambiguous_score_delta:
        return 1.0
    spread = max(distances_m) if distances_m else 0.0
    if spread <= cfg.thresholds.ambiguous_spread_m:
        # Near-equal scores but the candidates agree on location, so it does not
        # matter which one wins.
        return 0.8
    return max(0.0, gap / cfg.thresholds.ambiguous_score_delta)


def score(features: Features) -> float:
    """Blend features into a raw score in [0,1], then apply penalties."""
    raw = 0.0
    for name, weight in WEIGHTS.items():
        raw += weight * max(0.0, min(1.0, getattr(features, name)))

    for _reason, factor in features.penalties:
        raw *= factor

    return max(0.0, min(1.0, raw))


class Calibrator:
    """Maps a raw score to a calibrated probability.

    Isotonic regression, stored as a step function of (raw, calibrated) knots
    fitted on the dev split by `eval/reliability.py`. Monotonic and
    non-parametric, which is the right choice on a few hundred labelled
    examples where a logistic fit would be shakier.
    """

    def __init__(self, knots: list[tuple[float, float]] | None = None) -> None:
        self.knots = sorted(knots) if knots else None

    @property
    def is_fitted(self) -> bool:
        return bool(self.knots)

    @property
    def fingerprint(self) -> str:
        """Short stable id of the fitted map; "identity" when unfitted."""
        if not self.knots:
            return "identity"
        import hashlib
        import json

        digest = hashlib.sha256(json.dumps(self.knots).encode()).hexdigest()
        return digest[:12]

    def __call__(self, raw: float) -> float:
        if not self.knots:
            # Identity until the fit exists. Reported honestly rather than
            # dressed up: see `describe`.
            return raw
        # Piecewise-linear interpolation between knots.
        if raw <= self.knots[0][0]:
            return self.knots[0][1]
        if raw >= self.knots[-1][0]:
            return self.knots[-1][1]
        for (x0, y0), (x1, y1) in zip(self.knots, self.knots[1:], strict=False):
            if x0 <= raw <= x1:
                if x1 == x0:
                    return y1
                t = (raw - x0) / (x1 - x0)
                return y0 + t * (y1 - y0)
        return raw

    def describe(self) -> str:
        if not self.knots:
            return "uncalibrated (identity): raw score, not a fitted probability"
        return f"isotonic, {len(self.knots)} knots"

    @classmethod
    def load(cls, path: str | None = None) -> Calibrator:
        """Load the fitted calibrator, or an identity one if it is absent."""
        path = path or os.path.join(DATA_DIR, "calibration.json")
        if not os.path.exists(path):
            return cls(None)
        with open(path, encoding="utf-8") as fh:
            payload = json.load(fh)
        return cls([(float(x), float(y)) for x, y in payload["knots"]])
