"""Shared types: the `/v1/resolve` contract, in Python.

This module and `frontend/shared/contract.ts` describe the same wire format and
must be changed together. The response shape is frozen -- three people build
against it in parallel, so changing it to unblock one of them blocks the other
two.

Two invariants are enforced here rather than left to convention, because both
are load-bearing safety properties:

*   A field the input did not contain is `None`. Never an empty string, never a
    plausible guess (FR-02). A confidently wrong flat number fails silently at
    the doorstep, which is worse than an admitted gap.
*   A geocode always states its `source`, and a `pincode_centroid` can never be
    presented as a doorstep (FR-03, NFR-15).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Confidence = Annotated[float, Field(ge=0.0, le=1.0)]


class Status(StrEnum):
    """The three outcomes of a resolution. There is no fourth."""

    RESOLVED = "RESOLVED"
    NEEDS_INFO = "NEEDS_INFO"
    AMBIGUOUS = "AMBIGUOUS"


class GeoSource(StrEnum):
    """Where a coordinate came from, cheapest and most accurate first.

    The ordering matters: it feeds the geo-source tier feature in S6, and
    `PINCODE_CENTROID` must be visibly distinguishable downstream so the UI can
    refuse to draw it as a doorstep pin.
    """

    LANDMARK_GRAPH = "landmark_graph"
    GEOCODER = "geocoder"
    PINCODE_CENTROID = "pincode_centroid"


class Relation(StrEnum):
    """How a landmark relates to the address.

    Indian addresses are directions given to a person, so the relation carries
    real information: "behind Shiv Mandir" and "opposite Shiv Mandir" are
    different doorsteps that a plain landmark match would collapse together.
    """

    NEAR = "near"
    BEHIND = "behind"
    OPPOSITE = "opposite"
    ABOVE = "above"
    BESIDE = "beside"
    INSIDE = "inside"


class StrictModel(BaseModel):
    """Base: reject unknown fields.

    A typo in a field name should fail loudly at the boundary rather than be
    silently dropped and then show up as a missing value in the evaluation.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Landmark(StrictModel):
    """A landmark mentioned in an address, and what we matched it to."""

    name: str
    relation: Relation = Relation.NEAR
    # The landmark graph id, or None when nothing in the graph fitted. Keeping
    # the raw name with a null id is required (FR-02): an unmatched landmark is
    # still evidence, and it is what seeds the graph next time.
    matched_id: str | None = None
    # Fused RRF score of the match, absent when there was no match.
    match_score: Confidence | None = None
    # Metres between the matched landmark and the resolved point.
    distance_m: float | None = Field(default=None, ge=0.0)

    @model_validator(mode="after")
    def _score_requires_match(self) -> Landmark:
        if self.matched_id is None and self.match_score is not None:
            raise ValueError("match_score set without matched_id")
        return self


class StructuredAddress(StrictModel):
    """The parsed address. Every field is optional and defaults to None.

    FR-01 fixes this field list; FR-02 fixes the meaning of None.
    """

    building: str | None = None
    street: str | None = None
    sub_locality: str | None = None
    locality: str | None = None
    city: str | None = None
    district: str | None = None
    state: str | None = None
    pincode: str | None = None
    landmarks: list[Landmark] = Field(default_factory=list)

    @field_validator(
        "building",
        "street",
        "sub_locality",
        "locality",
        "city",
        "district",
        "state",
        "pincode",
        mode="before",
    )
    @classmethod
    def _blank_is_none(cls, v: Any) -> Any:
        """Collapse "", "  ", "null" and "N/A" to None.

        Models asked for JSON will return an empty string or the literal string
        "null" for a missing field however firmly the prompt says otherwise.
        Normalising here means the rest of the pipeline, and the F1 metric, only
        ever see one representation of absence.
        """
        if v is None:
            return None
        if isinstance(v, str):
            s = v.strip()
            if s == "" or s.lower() in {"null", "none", "n/a", "na", "-", "unknown"}:
                return None
            return s
        return v

    @field_validator("pincode")
    @classmethod
    def _pincode_shape(cls, v: str | None) -> str | None:
        """A pincode is six digits and never starts with zero."""
        if v is None:
            return None
        if not (len(v) == 6 and v.isdigit() and v[0] != "0"):
            raise ValueError(f"invalid Indian pincode: {v!r}")
        return v

    def filled_fields(self) -> set[str]:
        """Names of the scalar fields that carry a value."""
        return {
            name
            for name in (
                "building",
                "street",
                "sub_locality",
                "locality",
                "city",
                "district",
                "state",
                "pincode",
            )
            if getattr(self, name) is not None
        }


class Geo(StrictModel):
    """A resolved coordinate, always with its provenance."""

    lat: float = Field(ge=-90.0, le=90.0)
    lng: float = Field(ge=-180.0, le=180.0)
    source: GeoSource
    # Radius within which the true point is expected to lie. A pincode centroid
    # carries kilometres of uncertainty and must say so rather than present as a
    # doorstep (NFR-15).
    accuracy_m: float | None = Field(default=None, ge=0.0)

    @property
    def is_doorstep_accurate(self) -> bool:
        """False for a pincode centroid, whatever its confidence.

        The UI uses this to choose between a pin and a shaded area. A centroid
        drawn as a pin is a lie a rider will believe.
        """
        return self.source is not GeoSource.PINCODE_CENTROID


class Clarification(StrictModel):
    """Exactly one question, in the script of the incoming address."""

    # The single field whose absence costs the most confidence.
    field: str
    question: str
    # BCP-47-ish tag: "hi-IN" for Devanagari in, "en-IN" otherwise (FR-17).
    language: str = "en-IN"
    # Confidence we expect to reach if this is answered. This is what makes the
    # choice of question a calculation rather than a guess.
    expected_gain: Confidence | None = None


class Alternative(StrictModel):
    """A competing reading, surfaced instead of being silently discarded."""

    structured: StructuredAddress
    geo: Geo | None = None
    confidence: Confidence
    reason: str


class Resolution(StrictModel):
    """The `/v1/resolve` response. Frozen shape."""

    status: Status
    confidence: Confidence
    structured: StructuredAddress
    geo: Geo | None = None
    # Continuous 10-character DIGIPIN, or None when we have no coordinate.
    digipin: str | None = None
    # Human-readable reasons for each major decision (FR-06). Not debug output:
    # this is what an ops analyst reads to decide whether to trust the result,
    # and what the playground renders.
    evidence: list[str] = Field(default_factory=list)
    clarification: Clarification | None = None
    alternatives: list[Alternative] = Field(default_factory=list)
    # Echoed for tracing across stages; also the review-queue lookup key.
    correlation_id: str | None = None
    # Per-stage milliseconds, for the latency budget and the metrics screen.
    timings_ms: dict[str, float] = Field(default_factory=dict)
    # True when the exact-hash cache answered without running the pipeline.
    cached: bool = False

    @model_validator(mode="after")
    def _status_matches_payload(self) -> Resolution:
        """Keep status honest about the rest of the payload.

        These three rules are the difference between a status field that means
        something and one that is decoration.
        """
        if self.status is Status.NEEDS_INFO and self.clarification is None:
            raise ValueError("NEEDS_INFO requires a clarification")
        if self.status is Status.AMBIGUOUS and len(self.alternatives) < 2:
            raise ValueError("AMBIGUOUS requires at least two alternatives")
        if self.digipin is not None and self.geo is None:
            raise ValueError("digipin without geo: a DIGIPIN is derived from a point")
        return self


class ResolveRequest(StrictModel):
    """`POST /v1/resolve` input."""

    raw: str = Field(min_length=1, max_length=2_000)
    # Optional GPS hint, used only to constrain retrieval (FR-07). It is never
    # returned as the answer -- a rider standing at the gate is not the doorstep.
    hint: dict[str, float] | None = None
    order_id: str | None = None

    @field_validator("hint")
    @classmethod
    def _hint_shape(cls, v: dict[str, float] | None) -> dict[str, float] | None:
        if v is None:
            return None
        if set(v) != {"lat", "lng"}:
            raise ValueError("hint must be exactly {'lat': float, 'lng': float}")
        if not (-90.0 <= v["lat"] <= 90.0 and -180.0 <= v["lng"] <= 180.0):
            raise ValueError("hint coordinates out of range")
        return v


class LandmarkRecord(StrictModel):
    """A landmark as stored in the OpenSearch `landmarks` index."""

    landmark_id: str
    canonical_name: str
    aliases: list[str] = Field(default_factory=list)
    type: str | None = None
    lat: float
    lng: float
    digipin_cell: str | None = None
    pincode: str | None = None
    # How many confirmed deliveries have corroborated this landmark. Seen 47
    # times is trustworthy; seen once is not, and S6 log-scales it accordingly.
    observation_count: int = Field(default=1, ge=0)
    confidence: Confidence = 0.5
    access_notes: list[dict[str, Any]] = Field(default_factory=list)
    last_seen: str | None = None
