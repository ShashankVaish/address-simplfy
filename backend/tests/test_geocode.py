"""S4 tests: source ordering, honest accuracy, the never-a-doorstep rule."""

from __future__ import annotations

from patasetu.geocode import (
    ACCURACY_CENTROID_MAX_M,
    ACCURACY_CENTROID_MIN_M,
    ACCURACY_LANDMARK_M,
    centroid_accuracy_m,
    from_centroid,
    from_landmark,
    geocode,
    offset_point,
)
from patasetu.models import GeoSource, Relation
from patasetu.providers import ProviderUnavailable
from patasetu.retrieve import Candidate
from patasetu.search import InMemorySearchEngine


def cand(engine: InMemorySearchEngine, lid: str) -> Candidate:
    return Candidate(
        landmark_id=lid,
        record=engine.get(lid),
        rrf_score=0.02,
        signals={"bm25": 1},
        distance_m=50.0,
    )


class TestCentroid:
    def test_known_pincode_gives_a_marked_centroid(self) -> None:
        r = from_centroid("110015")
        assert r.geo is not None
        assert r.geo.source is GeoSource.PINCODE_CENTROID
        assert r.geo.is_doorstep_accurate is False
        assert any("NOT a doorstep" in e for e in r.evidence)

    def test_unknown_pincode_gives_nothing(self) -> None:
        assert from_centroid("999999").geo is None
        assert from_centroid(None).geo is None

    def test_accuracy_scales_with_pincode_size(self) -> None:
        small = centroid_accuracy_m("400050")  # 1 office
        large = centroid_accuracy_m("411001")  # 8 offices
        assert ACCURACY_CENTROID_MIN_M <= small < large <= ACCURACY_CENTROID_MAX_M

    def test_unknown_pincode_gets_the_widest_accuracy(self) -> None:
        assert centroid_accuracy_m(None) == ACCURACY_CENTROID_MAX_M
        assert centroid_accuracy_m("999999") == ACCURACY_CENTROID_MAX_M


class TestLandmark:
    def test_uses_the_landmark_coordinate(self, engine: InMemorySearchEngine) -> None:
        r = from_landmark(cand(engine, "LMK#DL#SHIV"))
        assert r.geo.source is GeoSource.LANDMARK_GRAPH
        assert r.geo.is_doorstep_accurate is True
        assert (r.geo.lat, r.geo.lng) == (28.6524, 77.1206)

    def test_accuracy_tightens_with_observations(
        self, engine: InMemorySearchEngine
    ) -> None:
        seen_47 = from_landmark(cand(engine, "LMK#DL#SHIV")).geo.accuracy_m
        seen_3 = from_landmark(cand(engine, "LMK#DL#GUPTA")).geo.accuracy_m
        assert seen_47 < seen_3
        assert seen_47 >= ACCURACY_LANDMARK_M

    def test_relation_widens_accuracy_rather_than_guessing_direction(
        self, engine: InMemorySearchEngine
    ) -> None:
        near = from_landmark(cand(engine, "LMK#DL#SHIV"), Relation.NEAR).geo
        behind = from_landmark(cand(engine, "LMK#DL#SHIV"), Relation.BEHIND).geo
        assert behind.accuracy_m > near.accuracy_m

    def test_inside_widens_accuracy_for_an_area(
        self, engine: InMemorySearchEngine
    ) -> None:
        near = from_landmark(cand(engine, "LMK#DL#RAMESH"), Relation.NEAR).geo
        inside = from_landmark(cand(engine, "LMK#DL#RAMESH"), Relation.INSIDE).geo
        assert inside.accuracy_m > near.accuracy_m
        assert (inside.lat, inside.lng) == (near.lat, near.lng)  # not displaced


class TestOffset:
    def test_zero_offset_is_identity(self) -> None:
        assert offset_point(28.65, 77.12, 0.0) == (28.65, 77.12)

    def test_forty_metres_north(self) -> None:
        lat, lng = offset_point(28.65, 77.12, 40.0, 0.0)
        assert abs((lat - 28.65) * 111_320 - 40.0) < 0.01
        assert lng == 77.12


class FakeGeocoder:
    def __init__(self, result=None, raise_=False):
        self.result = result
        self.raise_ = raise_
        self.calls: list[tuple[str, tuple | None]] = []

    def geocode(self, text, *, near=None):
        self.calls.append((text, near))
        if self.raise_:
            raise ProviderUnavailable("down")
        return self.result


class TestCascade:
    def test_landmark_graph_first(self, engine: InMemorySearchEngine) -> None:
        r = geocode(
            candidates=[cand(engine, "LMK#DL#SHIV")],
            pincode="110015",
            address_text="x",
            geocoder=FakeGeocoder((1.0, 1.0, 1.0)),
        )
        assert r.geo.source is GeoSource.LANDMARK_GRAPH
        assert r.attempted == ["landmark_graph"]

    def test_geocoder_when_no_landmark(self, engine: InMemorySearchEngine) -> None:
        g = FakeGeocoder((28.6, 77.1, 250.0))
        r = geocode(
            candidates=[], pincode="110015", address_text="ramesh nagar", geocoder=g
        )
        assert r.geo.source is GeoSource.GEOCODER
        assert g.calls[0][1] is not None  # biased near the pincode centroid

    def test_centroid_last(self) -> None:
        r = geocode(
            candidates=[],
            pincode="110015",
            address_text="x",
            geocoder=FakeGeocoder(None),
        )
        assert r.geo.source is GeoSource.PINCODE_CENTROID
        assert r.attempted == ["landmark_graph", "geocoder", "pincode_centroid"]

    def test_geocoder_outage_falls_through(self) -> None:
        r = geocode(
            candidates=[],
            pincode="110015",
            address_text="x",
            geocoder=FakeGeocoder(raise_=True),
        )
        assert r.geo.source is GeoSource.PINCODE_CENTROID
        assert any("unavailable" in e for e in r.evidence)

    def test_no_geocoder_in_local_mode(self) -> None:
        r = geocode(candidates=[], pincode="110015", address_text="x", geocoder=None)
        assert r.geo.source is GeoSource.PINCODE_CENTROID
        assert "geocoder" not in r.attempted

    def test_graph_disabled_for_configuration_b(
        self, engine: InMemorySearchEngine
    ) -> None:
        r = geocode(
            candidates=[cand(engine, "LMK#DL#SHIV")],
            pincode="110015",
            address_text="x",
            geocoder=None,
            use_landmark_graph=False,
        )
        assert r.geo.source is GeoSource.PINCODE_CENTROID

    def test_nothing_at_all(self) -> None:
        r = geocode(candidates=[], pincode=None, address_text="", geocoder=None)
        assert r.geo is None
