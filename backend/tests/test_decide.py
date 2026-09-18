"""S7 tests: the three outcomes, and the rules for reaching each."""

from __future__ import annotations

from patasetu.config import Config
from patasetu.decide import (
    AMBIGUOUS_AFFINITY_DELTA,
    choose_question,
    decide,
    find_ambiguity,
)
from patasetu.models import Geo, GeoSource, Landmark, Status, StructuredAddress
from patasetu.retrieve import Candidate
from patasetu.search import InMemorySearchEngine


def cand(
    engine: InMemorySearchEngine, lid: str, *, affinity: float, signals=None
) -> Candidate:
    return Candidate(
        landmark_id=lid,
        record=engine.get(lid),
        rrf_score=1 / 61,
        signals=signals or {"bm25": 1},
        affinity=affinity,
    )


FULL = StructuredAddress(
    building="14",
    locality="Ramesh Nagar",
    city="New Delhi",
    district="West",
    state="Delhi",
    pincode="110015",
    landmarks=[Landmark(name="Shiv Mandir", matched_id="LMK#DL#SHIV")],
)
GEO = Geo(lat=28.65, lng=77.12, source=GeoSource.LANDMARK_GRAPH, accuracy_m=60.0)


class TestAmbiguity:
    def test_two_equally_named_far_apart_landmarks(
        self, cfg: Config, engine: InMemorySearchEngine
    ) -> None:
        # Delhi Shiv Mandir vs Mumbai Shiv Mandir: same name, 1,150 km apart.
        pair = find_ambiguity(
            [
                cand(engine, "LMK#DL#SHIV", affinity=0.95),
                cand(engine, "LMK#MH#SHIV", affinity=0.95),
            ],
            cfg,
        )
        assert pair is not None

    def test_near_each_other_is_not_ambiguous(
        self, cfg: Config, engine: InMemorySearchEngine
    ) -> None:
        # Shiv Mandir and Gupta store are 100 m apart: either is fine to route to.
        pair = find_ambiguity(
            [
                cand(engine, "LMK#DL#SHIV", affinity=0.9),
                cand(engine, "LMK#DL#GUPTA", affinity=0.9),
            ],
            cfg,
        )
        assert pair is None

    def test_clear_winner_is_not_ambiguous(
        self, cfg: Config, engine: InMemorySearchEngine
    ) -> None:
        pair = find_ambiguity(
            [
                cand(engine, "LMK#DL#SHIV", affinity=0.95),
                cand(
                    engine,
                    "LMK#MH#SHIV",
                    affinity=0.95 - AMBIGUOUS_AFFINITY_DELTA - 0.05,
                ),
            ],
            cfg,
        )
        assert pair is None

    def test_proximity_only_candidates_are_never_rivals(
        self, cfg: Config, engine: InMemorySearchEngine
    ) -> None:
        """Regression: two nearest places, named by nobody, came back AMBIGUOUS."""
        pair = find_ambiguity(
            [
                cand(engine, "LMK#DL#SHIV", affinity=0.0, signals={"geo": 1}),
                cand(engine, "LMK#DL#HANUMAN", affinity=0.0, signals={"geo": 2}),
            ],
            cfg,
        )
        assert pair is None

    def test_rrf_adjacency_alone_is_not_ambiguity(
        self, cfg: Config, engine: InMemorySearchEngine
    ) -> None:
        """Regression: ranks 1 and 2 score 1/61 vs 1/62 whatever the names are."""
        a = cand(engine, "LMK#DL#SHIV", affinity=0.95)
        b = cand(
            engine, "LMK#DL#HANUMAN", affinity=0.2
        )  # far, but the text does not name it
        b.rrf_score = 1 / 62
        assert find_ambiguity([a, b], cfg) is None

    def test_fewer_than_two_candidates(
        self, cfg: Config, engine: InMemorySearchEngine
    ) -> None:
        assert find_ambiguity([], cfg) is None
        assert find_ambiguity([cand(engine, "LMK#DL#SHIV", affinity=1.0)], cfg) is None


class TestQuestion:
    def test_asks_about_the_most_valuable_missing_field_first(self) -> None:
        q = choose_question(StructuredAddress(building="14"), devanagari=False)
        assert q.field == "locality"

    def test_pincode_before_building(self) -> None:
        q = choose_question(
            StructuredAddress(locality="Ramesh Nagar"), devanagari=False
        )
        assert q.field == "pincode"

    def test_building_when_place_is_known(self) -> None:
        q = choose_question(
            StructuredAddress(locality="Ramesh Nagar", pincode="110015"),
            devanagari=False,
        )
        assert q.field == "building"

    def test_landmark_when_fields_complete_but_no_landmark(self) -> None:
        q = choose_question(
            StructuredAddress(building="14", locality="Ramesh Nagar", pincode="110015"),
            devanagari=False,
        )
        assert q.field == "landmark"

    def test_geo_confirm_when_nothing_is_missing(self) -> None:
        """Regression: asked for the house number when we already had it."""
        q = choose_question(FULL, devanagari=False)
        assert q.field == "geo_confirm"
        assert "map" in q.question.lower()

    def test_language_mirrors_script(self) -> None:
        assert choose_question(FULL, devanagari=True).language == "hi-IN"
        assert choose_question(FULL, devanagari=False).language == "en-IN"
        assert "Map par" in choose_question(FULL, devanagari=True).question


class TestDecide:
    def test_resolved_above_threshold(self, cfg: Config) -> None:
        d = decide(confidence=0.9, structured=FULL, geo=GEO, candidates=[], cfg=cfg)
        assert d.status is Status.RESOLVED
        assert d.clarification is None

    def test_needs_info_below_threshold(self, cfg: Config) -> None:
        d = decide(
            confidence=0.5,
            structured=StructuredAddress(pincode="110015"),
            geo=GEO,
            candidates=[],
            cfg=cfg,
        )
        assert d.status is Status.NEEDS_INFO
        assert d.clarification is not None
        assert d.clarification.field == "locality"

    def test_ambiguity_beats_the_threshold(
        self, cfg: Config, engine: InMemorySearchEngine
    ) -> None:
        """Two rivals average to a fine confidence; the threshold must not win."""
        d = decide(
            confidence=0.95,
            structured=FULL,
            geo=GEO,
            candidates=[
                cand(engine, "LMK#DL#SHIV", affinity=0.95),
                cand(engine, "LMK#MH#SHIV", affinity=0.95),
            ],
            cfg=cfg,
        )
        assert d.status is Status.AMBIGUOUS
        assert len(d.alternatives) == 2
        assert {a.geo.lat for a in d.alternatives} == {28.6524, 19.0760}

    def test_model_alternatives_surface_as_ambiguous(self, cfg: Config) -> None:
        d = decide(
            confidence=0.9,
            structured=FULL,
            geo=GEO,
            candidates=[],
            cfg=cfg,
            model_alternatives=[
                {
                    "reason": "could be sector 22 or 23",
                    "fields": {"sub_locality": "Sector 23"},
                }
            ],
        )
        assert d.status is Status.AMBIGUOUS
        assert len(d.alternatives) == 2  # primary + the alternative
        assert d.alternatives[0].reason == "primary reading"
        assert d.alternatives[1].structured.sub_locality == "Sector 23"

    def test_exactly_at_threshold_resolves(self, cfg: Config) -> None:
        d = decide(
            confidence=cfg.thresholds.resolved_at,
            structured=FULL,
            geo=GEO,
            candidates=[],
            cfg=cfg,
        )
        assert d.status is Status.RESOLVED
