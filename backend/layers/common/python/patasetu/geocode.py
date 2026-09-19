"""S4 -- geocoding, cheapest and most accurate source first.

    1. Landmark graph hit    free, instant, and the most accurate source we
                             have, because it was learned from real deliveries
    2. Amazon Location       a real geocoder, for when no landmark matched
    3. Pincode centroid      last resort, kilometres wide, and never presented
                             as a doorstep (NFR-15)

The ordering is not only about cost. A landmark-graph coordinate is a point
someone actually delivered to; a geocoder returns the centre of a street
segment; a pincode centroid is the middle of an area that can be 10 km across.
So the cheapest source is also the best one, which is a pleasant thing to be
able to say out loud.

Every result carries its `source` and an honest `accuracy_m`. A caller that
wants to know whether it may draw a pin asks `geo.is_doorstep_accurate`, and
S6 scores the source tier. Nothing downstream has to guess how good a
coordinate is.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from patasetu import gazetteer
from patasetu.models import Geo, GeoSource, Relation
from patasetu.providers import ProviderUnavailable
from patasetu.retrieve import Candidate

# Typical accuracy by source, in metres. These are the radii the UI shades and
# the numbers S6 turns into a confidence tier, so they are deliberate estimates
# rather than round guesses.
ACCURACY_LANDMARK_M = 60.0
ACCURACY_INSIDE_EXTRA_M = 120.0
ACCURACY_GEOCODER_M = 250.0
# A pincode centroid's accuracy depends on how large the pincode is. Estimated
# from the spread of its own post offices rather than assumed -- a dense urban
# pincode really is tighter than a rural one, and pretending otherwise either
# over-trusts the centroid or throws away a usable constraint.
ACCURACY_CENTROID_MIN_M = 1_200.0
ACCURACY_CENTROID_MAX_M = 8_000.0

# How far a relation word displaces the point. "Behind Shiv Mandir" is not at
# Shiv Mandir, and a rider sent to the temple door will stand at the wrong gate.
# These are small, honest offsets: enough to bias the point off the landmark
# itself, not enough to pretend we know which side of the building it is.
RELATION_OFFSET_M: dict[Relation, float] = {
    Relation.NEAR: 0.0,
    # INSIDE means the address *is* this place -- a locality or a complex. The
    # point is not displaced, but the place is an area, so the accuracy widens.
    Relation.INSIDE: 0.0,
    Relation.ABOVE: 0.0,
    Relation.BESIDE: 25.0,
    Relation.BEHIND: 40.0,
    Relation.OPPOSITE: 40.0,
}


@dataclass
class GeocodeResult:
    geo: Geo | None
    evidence: list[str] = field(default_factory=list)
    # Which sources were tried and failed, for the degraded-mode report.
    attempted: list[str] = field(default_factory=list)


def centroid_accuracy_m(pincode: str | None) -> float:
    """Estimate how wide a pincode centroid's uncertainty really is.

    Uses the office count as a proxy for area: a pincode with one delivery
    office is a small area, one with thirty covers a lot of ground. Crude, but
    it is derived from the data rather than invented, and it keeps the UI from
    drawing the same circle for a Mumbai block and a Rajasthan tehsil.
    """
    if pincode is None:
        return ACCURACY_CENTROID_MAX_M
    info = gazetteer.lookup(pincode)
    if info is None:
        return ACCURACY_CENTROID_MAX_M
    # 1 office -> ~1.2 km, 30+ offices -> ~8 km, log-scaled between.
    n = max(1, info.office_count)
    fraction = min(1.0, math.log(n) / math.log(30))
    return ACCURACY_CENTROID_MIN_M + fraction * (
        ACCURACY_CENTROID_MAX_M - ACCURACY_CENTROID_MIN_M
    )


def offset_point(
    lat: float, lng: float, metres: float, bearing_deg: float = 0.0
) -> tuple[float, float]:
    """Move a point `metres` along `bearing_deg`. Flat-earth, which is fine here.

    At these distances (tens of metres) the spherical correction is far below
    the accuracy we are claiming, so the simple form is honest and cheaper.
    """
    if metres == 0.0:
        return lat, lng
    d_lat = (metres * math.cos(math.radians(bearing_deg))) / 111_320.0
    cos_lat = math.cos(math.radians(lat)) or 1e-9
    d_lng = (metres * math.sin(math.radians(bearing_deg))) / (111_320.0 * cos_lat)
    return lat + d_lat, lng + d_lng


def from_landmark(
    candidate: Candidate, relation: Relation = Relation.NEAR
) -> GeocodeResult:
    """Geocode from a matched landmark: the best source we have.

    Accuracy widens for a landmark seen only once. A point corroborated by
    forty deliveries deserves a 60 m claim; a point seen once is a single
    observation that may itself have been a bad GPS reading, and saying "60 m"
    about it would be a lie that S6 then compounds.
    """
    record = candidate.record
    observations = max(1, record.observation_count)
    # Halve the confidence radius as observations accumulate, floor at the base.
    accuracy = ACCURACY_LANDMARK_M * (1.0 + 2.0 / math.sqrt(observations))

    offset = RELATION_OFFSET_M.get(relation, 0.0)
    lat, lng = offset_point(record.lat, record.lng, offset)
    if relation is Relation.INSIDE:
        # A locality or complex is an area: the coordinate is its reference
        # point, and the doorstep is somewhere within it.
        accuracy += ACCURACY_INSIDE_EXTRA_M
    if offset:
        # The offset direction is unknown, so it is added to the uncertainty
        # rather than presented as a known displacement.
        accuracy += offset

    geo = Geo(
        lat=round(lat, 6),
        lng=round(lng, 6),
        source=GeoSource.LANDMARK_GRAPH,
        accuracy_m=round(accuracy, 1),
    )
    evidence = [
        f"geocoded from landmark graph: {record.landmark_id} "
        f"('{record.canonical_name}', seen {observations}x), "
        f"accuracy ~{geo.accuracy_m:.0f} m"
    ]
    if offset:
        evidence.append(
            f"relation '{relation.value}' offsets the point by ~{offset:.0f} m; "
            f"direction is unknown so it widens the accuracy rather than moving it"
        )
    return GeocodeResult(geo=geo, evidence=evidence, attempted=["landmark_graph"])


def from_centroid(pincode: str | None) -> GeocodeResult:
    """Last resort. Marked, penalised, and never a doorstep."""
    if pincode is None:
        return GeocodeResult(
            geo=None,
            evidence=["no coordinate available: no validated pincode"],
            attempted=["pincode_centroid"],
        )
    info = gazetteer.lookup(pincode)
    if info is None or not info.has_centroid:
        return GeocodeResult(
            geo=None,
            evidence=[f"pincode {pincode} has no usable centroid in the gazetteer"],
            attempted=["pincode_centroid"],
        )

    accuracy = centroid_accuracy_m(pincode)
    geo = Geo(
        lat=info.lat,  # type: ignore[arg-type]
        lng=info.lng,  # type: ignore[arg-type]
        source=GeoSource.PINCODE_CENTROID,
        accuracy_m=round(accuracy, 1),
    )
    return GeocodeResult(
        geo=geo,
        evidence=[
            f"fell back to the centroid of pincode {pincode} "
            f"({info.office_count} offices, accuracy ~{accuracy / 1000:.1f} km)",
            "this is a pincode centroid, NOT a doorstep -- it must not be "
            "presented as a delivery destination",
        ],
        attempted=["pincode_centroid"],
    )


class LocationServiceGeocoder:
    """Amazon Location Service, for when no landmark matched."""

    def __init__(self, place_index: str, region: str) -> None:
        self.place_index = place_index
        self.region = region
        self._client: Any = None

    @property
    def client(self) -> Any:
        if self._client is None:
            try:
                import boto3
            except ImportError as exc:  # pragma: no cover - boto3 ships in Lambda
                raise ProviderUnavailable(
                    "boto3 is required for Location Service"
                ) from exc
            self._client = boto3.client("location", region_name=self.region)
        return self._client

    def geocode(
        self, text: str, *, near: tuple[float, float] | None = None
    ) -> tuple[float, float, float] | None:
        """Return (lat, lng, accuracy_m) for the best result, or None."""
        params: dict[str, Any] = {
            "IndexName": self.place_index,
            "Text": text,
            "MaxResults": 1,
            # Restricting to India is both a correctness and a cost measure: a
            # partial Indian address can otherwise match a street in another
            # country and return a confident coordinate on the wrong continent.
            "FilterCountries": ["IND"],
        }
        if near is not None:
            # Bias, not a filter: a nearby pincode centroid makes the right
            # street far more likely to win without excluding a correct result
            # just outside the box.
            params["BiasPosition"] = [near[1], near[0]]  # Location wants lon,lat

        try:
            response = self.client.search_place_index_for_text(**params)
        except Exception as exc:
            raise ProviderUnavailable(
                f"Location Service geocode failed: {exc}"
            ) from exc

        results = response.get("Results") or []
        if not results:
            return None
        place = results[0].get("Place") or {}
        point = place.get("Geometry", {}).get("Point")
        if not point or len(point) != 2:
            return None
        lon, lat = float(point[0]), float(point[1])
        return lat, lon, ACCURACY_GEOCODER_M


def geocode(
    *,
    candidates: list[Candidate],
    relations: list[Relation] | None = None,
    pincode: str | None,
    address_text: str = "",
    geocoder: object | None = None,
    use_landmark_graph: bool = True,
) -> GeocodeResult:
    """Run S4, trying each source in order until one answers.

    `use_landmark_graph` exists for the ablation: configuration B has no
    retrieval, so it must not be able to reach the graph even if candidates
    were somehow present.
    """
    attempted: list[str] = []
    evidence: list[str] = []

    # --- 1. landmark graph -------------------------------------------------
    if use_landmark_graph and candidates:
        relation = (relations or [Relation.NEAR])[0]
        result = from_landmark(candidates[0], relation)
        result.attempted = ["landmark_graph"]
        return result
    if use_landmark_graph:
        attempted.append("landmark_graph")
        evidence.append("no landmark matched, so the graph could not place this")

    # --- 2. street geocoder ------------------------------------------------
    if geocoder is not None and address_text.strip():
        attempted.append("geocoder")
        near: tuple[float, float] | None = None
        info = gazetteer.lookup(pincode) if pincode else None
        if info is not None and info.has_centroid:
            near = (info.lat, info.lng)  # type: ignore[arg-type]
        try:
            hit = geocoder.geocode(address_text, near=near)  # type: ignore[attr-defined]
        except ProviderUnavailable as exc:
            hit = None
            evidence.append(f"geocoder unavailable, falling back: {exc}")
        if hit is not None:
            lat, lng, accuracy = hit
            evidence.append(
                f"geocoded by Amazon Location Service, accuracy ~{accuracy:.0f} m"
            )
            return GeocodeResult(
                geo=Geo(
                    lat=round(lat, 6),
                    lng=round(lng, 6),
                    source=GeoSource.GEOCODER,
                    accuracy_m=accuracy,
                ),
                evidence=evidence,
                attempted=attempted,
            )
        evidence.append("street geocoder returned no result")

    # --- 3. pincode centroid -----------------------------------------------
    fallback = from_centroid(pincode)
    fallback.evidence = [*evidence, *fallback.evidence]
    fallback.attempted = [*attempted, *fallback.attempted]
    return fallback
