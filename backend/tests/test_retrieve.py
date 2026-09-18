"""S2 tests: RRF arithmetic, query building, phrase-to-landmark matching."""

from __future__ import annotations

import math

import pytest

from patasetu.config import Config
from patasetu.embeddings import HashingEmbedder
from patasetu.models import Relation
from patasetu.providers import Providers
from patasetu.retrieve import (
    MIN_MATCH_AFFINITY,
    Candidate,
    RetrievalResult,
    attach_matches,
    build_query_text,
    name_affinity,
    normalise_rrf,
    reciprocal_rank_fusion,
    retrieve,
)
from patasetu.search import (
    SIGNAL_GEO,
    SIGNAL_LEXICAL,
    SIGNAL_VECTOR,
    InMemorySearchEngine,
)

RAMESH = (28.6519, 77.1200)


class TestRRF:
    def test_formula_with_one_based_ranks(self) -> None:
        fused = reciprocal_rank_fusion({"a": [("X", 9.0)]}, k=60)
        assert math.isclose(fused[0][1], 1 / 61)

    def test_agreement_across_signals_wins(self) -> None:
        # B is second in bm25 but present in all three signals; A is first in
        # one signal only. Fusion must prefer B.
        fused = reciprocal_rank_fusion(
            {
                "bm25": [("A", 9.0), ("B", 3.0)],
                "knn": [("B", 0.9)],
                "geo": [("B", 1.0)],
            },
            k=60,
        )
        assert fused[0][0] == "B"

    def test_returns_signal_ranks(self) -> None:
        fused = reciprocal_rank_fusion(
            {"bm25": [("A", 1)], "geo": [("Z", 1), ("A", 0.5)]}, k=60
        )
        ranks = {doc: r for doc, _, r in fused}
        assert ranks["A"] == {"bm25": 1, "geo": 2}

    def test_duplicate_within_a_signal_counts_once(self) -> None:
        fused = reciprocal_rank_fusion({"bm25": [("A", 1), ("A", 1), ("B", 1)]}, k=60)
        assert math.isclose({d: s for d, s, _ in fused}["A"], 1 / 61)
        assert math.isclose({d: s for d, s, _ in fused}["B"], 1 / 62)

    def test_weights_scale_a_signal(self) -> None:
        fused = reciprocal_rank_fusion({"geo": [("A", 1)]}, k=60, weights={"geo": 0.5})
        assert math.isclose(fused[0][1], 0.5 / 61)

    def test_rejects_non_positive_k(self) -> None:
        with pytest.raises(ValueError):
            reciprocal_rank_fusion({"a": [("X", 1)]}, k=0)

    def test_deterministic_order_on_ties(self) -> None:
        fused = reciprocal_rank_fusion({"a": [("B", 1)], "b": [("A", 1)]}, k=60)
        assert [d for d, _, _ in fused] == ["A", "B"]

    def test_empty(self) -> None:
        assert reciprocal_rank_fusion({}, k=60) == []
        assert reciprocal_rank_fusion({"a": []}, k=60) == []


class TestNormaliseRRF:
    def test_all_first_is_one(self) -> None:
        assert math.isclose(normalise_rrf(3 / 61, n_signals=3, k=60), 1.0)

    def test_scales_with_signals_available(self) -> None:
        # Same absolute score means more when fewer signals were available.
        assert normalise_rrf(1 / 61, 1, 60) > normalise_rrf(1 / 61, 3, 60)

    def test_zero_signals(self) -> None:
        assert normalise_rrf(0.5, 0, 60) == 0.0

    def test_clamped(self) -> None:
        assert normalise_rrf(99.0, 1, 60) == 1.0


class TestQueryText:
    def test_prefers_landmark_phrases(self) -> None:
        assert (
            build_query_text(["shiv mandir", "gupta store"], "whole address")
            == "shiv mandir gupta store"
        )

    def test_falls_back_to_full_text(self) -> None:
        assert build_query_text([], "h no 14 ramesh nagar") == "h no 14 ramesh nagar"
        assert build_query_text(["", "  "], "fallback") == "fallback"


class TestRetrieve:
    def test_finds_the_named_landmark(self, cfg: Config, providers: Providers) -> None:
        r = retrieve(
            landmark_names=["shiv mandir"],
            retrieval_text="",
            centre=RAMESH,
            cfg=cfg,
            providers=providers,
        )
        assert not r.is_empty
        assert r.candidates[0].landmark_id == "LMK#DL#SHIV"
        assert set(r.signals_used) == {SIGNAL_LEXICAL, SIGNAL_VECTOR, SIGNAL_GEO}
        assert 0.0 < r.top_score <= 1.0

    def test_geo_filter_excludes_the_far_namesake(
        self, cfg: Config, providers: Providers
    ) -> None:
        r = retrieve(
            landmark_names=["shiv mandir"],
            retrieval_text="",
            centre=RAMESH,
            cfg=cfg,
            providers=providers,
        )
        assert all(c.landmark_id != "LMK#MH#SHIV" for c in r.candidates)

    def test_without_centre_both_namesakes_are_candidates(
        self, cfg: Config, providers: Providers
    ) -> None:
        r = retrieve(
            landmark_names=["shiv mandir"],
            retrieval_text="",
            centre=None,
            cfg=cfg,
            providers=providers,
        )
        ids = {c.landmark_id for c in r.candidates}
        assert {"LMK#DL#SHIV", "LMK#MH#SHIV"} <= ids
        assert any("geo signal unavailable" in d for d in r.degraded)

    def test_bm25_only_mode_uses_one_signal(
        self, cfg: Config, providers: Providers
    ) -> None:
        r = retrieve(
            landmark_names=["shiv mandir"],
            retrieval_text="",
            centre=RAMESH,
            cfg=cfg,
            providers=providers,
            use_vector=False,
            use_geo=False,
        )
        assert r.signals_used == [SIGNAL_LEXICAL]
        assert r.candidates[0].landmark_id in {"LMK#DL#SHIV", "LMK#MH#SHIV"}

    def test_distances_are_attached(self, cfg: Config, providers: Providers) -> None:
        r = retrieve(
            landmark_names=["gupta store"],
            retrieval_text="",
            centre=RAMESH,
            cfg=cfg,
            providers=providers,
        )
        assert r.candidates[0].distance_m is not None
        assert r.candidates[0].distance_m < 200

    def test_nothing_to_search(self, cfg: Config, providers: Providers) -> None:
        r = retrieve(
            landmark_names=[],
            retrieval_text="   ",
            centre=RAMESH,
            cfg=cfg,
            providers=providers,
        )
        assert r.is_empty
        assert any("nothing to search" in e for e in r.evidence)

    def test_unknown_name_yields_no_candidates_or_only_weak_ones(
        self, cfg: Config, providers: Providers
    ) -> None:
        r = retrieve(
            landmark_names=["zzz qqq"],
            retrieval_text="",
            centre=RAMESH,
            cfg=cfg,
            providers=providers,
        )
        # geo alone can still return nearby places -- but nothing may claim a name match
        assert all(SIGNAL_LEXICAL not in c.signals for c in r.candidates)

    def test_radius_override_is_honoured(
        self, cfg: Config, providers: Providers
    ) -> None:
        near = retrieve(
            landmark_names=["hanuman mandir"],
            retrieval_text="",
            centre=RAMESH,
            cfg=cfg,
            providers=providers,
            radius_m=500.0,
        )
        far = retrieve(
            landmark_names=["hanuman mandir"],
            retrieval_text="",
            centre=RAMESH,
            cfg=cfg,
            providers=providers,
            radius_m=5_000.0,
        )
        assert all(c.landmark_id != "LMK#DL#HANUMAN" for c in near.candidates)
        assert any(c.landmark_id == "LMK#DL#HANUMAN" for c in far.candidates)

    def test_whole_fused_list_is_materialised(
        self, cfg: Config, providers: Providers
    ) -> None:
        """Regression: the prompt cap must not truncate the candidate pool.

        Applied inside retrieval, it let the proximity signal fill every slot
        and the correctly named landmark fell out before matching.
        """
        # With no centre every landmark is in range; the geo signal is absent,
        # so this is every landmark any *name* signal touched. All six share a
        # trigram or a token with "mandir" or each other -- the fixture is
        # built that way -- and every one must be materialised, not the top 8.
        r = retrieve(
            landmark_names=["mandir shiv gupta water hanuman ramesh"],
            retrieval_text="",
            centre=None,
            cfg=cfg,
            providers=providers,
        )
        assert len(r.candidates) == providers.search.count()


class TestAffinityAndMatching:
    def _candidates(
        self, engine: InMemorySearchEngine, ids: list[str]
    ) -> RetrievalResult:
        cands = [
            Candidate(
                landmark_id=i,
                record=engine.get(i),
                rrf_score=1 / (61 + n),
                signals={"bm25": n + 1},
            )
            for n, i in enumerate(ids)
        ]
        return RetrievalResult(candidates=cands, top_score=1.0)

    def test_affinity_is_high_for_the_right_name(
        self, engine: InMemorySearchEngine, embedder: HashingEmbedder
    ) -> None:
        assert name_affinity("shiv mandir", engine.get("LMK#DL#SHIV"), embedder) >= 0.9
        assert (
            name_affinity("gupta store", engine.get("LMK#DL#GUPTA"), embedder) >= 0.5
        )  # alias

    def test_affinity_is_low_for_the_wrong_name(
        self, engine: InMemorySearchEngine, embedder: HashingEmbedder
    ) -> None:
        assert (
            name_affinity("shiv mandir", engine.get("LMK#DL#GUPTA"), embedder)
            < MIN_MATCH_AFFINITY
        )

    def test_affinity_of_empty_phrase_is_zero(
        self, engine: InMemorySearchEngine, embedder: HashingEmbedder
    ) -> None:
        assert name_affinity("", engine.get("LMK#DL#SHIV"), embedder) == 0.0

    def test_each_phrase_gets_its_own_landmark(
        self, engine: InMemorySearchEngine, embedder: HashingEmbedder
    ) -> None:
        """Regression: assigning in retrieval order gave every phrase candidate[0]."""
        result = self._candidates(
            engine, ["LMK#DL#GUPTA", "LMK#DL#SHIV", "LMK#DL#TANK"]
        )
        out = attach_matches(
            [
                ("shiv mandir", Relation.BEHIND),
                ("gupta general store", Relation.NEAR),
                ("water tank", Relation.OPPOSITE),
            ],
            result,
            embedder,
        )
        assert [m["matched_id"] for m in out] == [
            "LMK#DL#SHIV",
            "LMK#DL#GUPTA",
            "LMK#DL#TANK",
        ]
        assert [m["relation"] for m in out] == [
            Relation.BEHIND,
            Relation.NEAR,
            Relation.OPPOSITE,
        ]

    def test_one_to_one(
        self, engine: InMemorySearchEngine, embedder: HashingEmbedder
    ) -> None:
        result = self._candidates(engine, ["LMK#DL#SHIV"])
        out = attach_matches(
            [("shiv mandir", Relation.BEHIND), ("shiv temple", Relation.NEAR)],
            result,
            embedder,
        )
        matched = [m["matched_id"] for m in out if m["matched_id"]]
        assert matched == ["LMK#DL#SHIV"]  # claimed once, not twice

    def test_unmatched_phrase_keeps_raw_name_and_null_id(
        self, engine: InMemorySearchEngine, embedder: HashingEmbedder
    ) -> None:
        """FR-02 / FR-12: never invent a match."""
        result = self._candidates(engine, ["LMK#DL#SHIV"])
        out = attach_matches([("sharma sweets", Relation.NEAR)], result, embedder)
        assert out == [
            {
                "name": "Sharma Sweets",
                "relation": Relation.NEAR,
                "matched_id": None,
                "match_score": None,
                "distance_m": None,
            }
        ]

    def test_matched_phrase_takes_canonical_name(
        self, engine: InMemorySearchEngine, embedder: HashingEmbedder
    ) -> None:
        result = self._candidates(engine, ["LMK#DL#GUPTA"])
        out = attach_matches([("gupta kirana", Relation.NEAR)], result, embedder)
        assert out[0]["name"] == "Gupta General Store"
        assert 0.0 < out[0]["match_score"] <= 1.0

    def test_records_affinity_on_candidates(
        self, engine: InMemorySearchEngine, embedder: HashingEmbedder
    ) -> None:
        result = self._candidates(engine, ["LMK#DL#SHIV", "LMK#DL#GUPTA"])
        attach_matches([("shiv mandir", Relation.NEAR)], result, embedder)
        assert result.candidates[0].affinity >= 0.9
        assert result.candidates[1].affinity < MIN_MATCH_AFFINITY

    def test_empty_inputs(
        self, engine: InMemorySearchEngine, embedder: HashingEmbedder
    ) -> None:
        assert attach_matches([], RetrievalResult(candidates=[]), embedder) == []
        out = attach_matches(
            [("x", Relation.NEAR)], RetrievalResult(candidates=[]), embedder
        )
        assert out[0]["matched_id"] is None
