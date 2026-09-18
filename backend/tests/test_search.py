"""In-memory search engine tests: BM25, kNN, the geo filter, and index upkeep."""

from __future__ import annotations

import math

import pytest

from patasetu.embeddings import HashingEmbedder
from patasetu.models import LandmarkRecord
from patasetu.search import (
    BM25_B,
    BM25_K1,
    SIGNAL_GEO,
    SIGNAL_LEXICAL,
    SIGNAL_VECTOR,
    InMemorySearchEngine,
    index_mapping,
    tokenise,
)

RAMESH = (28.6519, 77.1200)


class TestTokenise:
    def test_lowercases_and_splits_on_punctuation(self) -> None:
        assert tokenise("H.No 14, Shiv-Mandir") == ["h", "no", "14", "shiv", "mandir"]

    def test_keeps_devanagari(self) -> None:
        assert tokenise("शिव मंदिर near SBI") == ["शिव", "मंदिर", "near", "sbi"]

    def test_empty(self) -> None:
        assert tokenise("") == []
        assert tokenise("  ,,, ") == []


class TestIndexUpkeep:
    def test_count_and_get(self, engine: InMemorySearchEngine) -> None:
        assert engine.count() == 6
        assert engine.get("LMK#DL#SHIV").canonical_name == "Shiv Mandir"
        assert engine.get("nope") is None

    def test_upsert_replaces_rather_than_duplicates(
        self, engine: InMemorySearchEngine
    ) -> None:
        before = engine.count()
        engine.upsert(
            [
                LandmarkRecord(
                    landmark_id="LMK#DL#SHIV",
                    canonical_name="Shiv Mandir Renamed",
                    lat=28.65,
                    lng=77.12,
                )
            ]
        )
        assert engine.count() == before
        assert engine.get("LMK#DL#SHIV").canonical_name == "Shiv Mandir Renamed"

    def test_upsert_maintains_document_frequencies(
        self, engine: InMemorySearchEngine
    ) -> None:
        """Re-warming the graph must not let IDF drift.

        If the old document's tokens are not removed first, every upsert adds
        another count for "mandir" and the term's IDF sinks a little further.
        """
        df_before = engine._doc_freq["mandir"]
        for _ in range(5):
            engine.upsert([engine.get("LMK#DL#SHIV")])
        assert engine._doc_freq["mandir"] == df_before

    def test_total_length_tracks_upserts(self, engine: InMemorySearchEngine) -> None:
        total_before = engine._total_len
        engine.upsert([engine.get("LMK#DL#SHIV")])
        assert engine._total_len == total_before

    def test_rejects_wrong_embedding_dimension(
        self, engine: InMemorySearchEngine
    ) -> None:
        with pytest.raises(ValueError):
            engine.upsert(
                [
                    LandmarkRecord(
                        landmark_id="X",
                        canonical_name="X",
                        lat=28.0,
                        lng=77.0,
                        embedding=[0.1, 0.2],
                    )
                ]
            )

    def test_record_without_embedding_is_indexed_lexically(
        self, engine: InMemorySearchEngine
    ) -> None:
        engine.upsert(
            [
                LandmarkRecord(
                    landmark_id="NOVEC",
                    canonical_name="Vectorless Place",
                    lat=28.652,
                    lng=77.12,
                )
            ]
        )
        hits = engine.bm25("vectorless", ["NOVEC"])
        assert hits and hits[0][0] == "NOVEC"
        assert engine.knn([1.0] * engine.dim, ["NOVEC"]) == []


class TestBM25:
    def test_exact_name_ranks_first(self, engine: InMemorySearchEngine) -> None:
        ids = list(engine._records)
        ranked = engine.bm25("shiv mandir", ids)
        assert ranked[0][0] in {"LMK#DL#SHIV", "LMK#MH#SHIV"}

    def test_alias_is_searchable(self, engine: InMemorySearchEngine) -> None:
        ranked = engine.bm25("paani ki tanki", list(engine._records))
        assert ranked[0][0] == "LMK#DL#TANK"

    def test_devanagari_alias_is_searchable(self, engine: InMemorySearchEngine) -> None:
        ranked = engine.bm25("शिव मंदिर", list(engine._records))
        assert ranked[0][0] == "LMK#DL#SHIV"

    def test_no_match_returns_empty(self, engine: InMemorySearchEngine) -> None:
        assert engine.bm25("zzz qqq", list(engine._records)) == []
        assert engine.bm25("", list(engine._records)) == []
        assert engine.bm25("shiv", []) == []

    def test_rarer_term_scores_higher(self, engine: InMemorySearchEngine) -> None:
        # "mandir" appears in three documents, "gupta" in one.
        gupta = engine.bm25("gupta", list(engine._records))[0][1]
        mandir = max(s for _, s in engine.bm25("mandir", list(engine._records)))
        assert gupta > mandir

    def test_matches_standard_formula_on_a_tiny_index(self) -> None:
        """Pin the arithmetic against a hand computation."""
        e = InMemorySearchEngine(dim=4)
        e.upsert(
            [
                LandmarkRecord(
                    landmark_id="A", canonical_name="shiv mandir", lat=1, lng=1
                ),
                LandmarkRecord(
                    landmark_id="B", canonical_name="gupta store", lat=1, lng=1
                ),
                LandmarkRecord(
                    landmark_id="C", canonical_name="shiv temple gali", lat=1, lng=1
                ),
            ]
        )
        # query "shiv": df=2, N=3, tf=1 in A (len 2), avgdl = (2+2+3)/3 = 7/3
        idf = math.log(1 + (3 - 2 + 0.5) / (2 + 0.5))
        denom = 1 + BM25_K1 * (1 - BM25_B + BM25_B * 2 / (7 / 3))
        expected_a = idf * (1 * (BM25_K1 + 1)) / denom
        got = dict(e.bm25("shiv", ["A", "B", "C"]))
        assert math.isclose(got["A"], expected_a, rel_tol=1e-9)
        assert "B" not in got


class TestKNN:
    def test_returns_empty_without_a_query_vector(
        self, engine: InMemorySearchEngine
    ) -> None:
        assert engine.knn(None, list(engine._records)) == []
        assert engine.knn([0.0] * engine.dim, list(engine._records)) == []

    def test_nearest_by_cosine(
        self, engine: InMemorySearchEngine, embedder: HashingEmbedder
    ) -> None:
        ranked = engine.knn(
            embedder.embed("gupta general store"), list(engine._records)
        )
        assert ranked[0][0] == "LMK#DL#GUPTA"

    def test_misspelling_still_finds_it(
        self, engine: InMemorySearchEngine, embedder: HashingEmbedder
    ) -> None:
        # BM25 sees only the whole token "shiv"; "mandhir" contributes nothing.
        # The vector signal recovers the misspelled half.
        ranked = engine.knn(embedder.embed("shiv mandhir"), list(engine._records))
        assert ranked[0][0] in {"LMK#DL#SHIV", "LMK#MH#SHIV"}
        # And it is a strong match, not a coincidence of ranking.
        assert ranked[0][1] > 0.5

    def test_drops_non_positive_similarity(
        self, engine: InMemorySearchEngine, embedder: HashingEmbedder
    ) -> None:
        for _, score in engine.knn(embedder.embed("xyz"), list(engine._records)):
            assert score > 0.0


class TestGeo:
    def test_filter_keeps_only_landmarks_within_radius(
        self, engine: InMemorySearchEngine
    ) -> None:
        ids, distances = engine.geo_candidates(RAMESH, 1_000.0)
        assert set(ids) == {
            "LMK#DL#RAMESH",
            "LMK#DL#SHIV",
            "LMK#DL#GUPTA",
            "LMK#DL#TANK",
        }
        assert all(d <= 1_000.0 for d in distances.values())

    def test_filter_removes_the_mumbai_shiv_mandir(
        self, engine: InMemorySearchEngine
    ) -> None:
        """The '400 other Shiv Mandirs' problem, solved by the filter."""
        ids, _ = engine.geo_candidates(RAMESH, 3_000.0)
        assert "LMK#MH#SHIV" not in ids
        assert "LMK#DL#SHIV" in ids

    def test_no_centre_means_no_filter(self, engine: InMemorySearchEngine) -> None:
        ids, distances = engine.geo_candidates(None, 3_000.0)
        assert len(ids) == engine.count()
        assert distances == {}

    def test_distances_from(self, engine: InMemorySearchEngine) -> None:
        d = engine.distances_from(RAMESH, ["LMK#DL#RAMESH", "LMK#MH#SHIV", "nope"])
        assert d["LMK#DL#RAMESH"] < 5
        assert d["LMK#MH#SHIV"] > 1_000_000
        assert "nope" not in d


class TestHybridSearch:
    def test_returns_all_three_signals(
        self, engine: InMemorySearchEngine, embedder: HashingEmbedder
    ) -> None:
        out = engine.hybrid_search(
            query_text="shiv mandir",
            query_vector=embedder.embed("shiv mandir"),
            centre=RAMESH,
            radius_m=3_000.0,
            size=10,
        )
        assert set(out) == {SIGNAL_LEXICAL, SIGNAL_VECTOR, SIGNAL_GEO}
        assert out[SIGNAL_LEXICAL][0][0] == "LMK#DL#SHIV"
        assert out[SIGNAL_VECTOR][0][0] == "LMK#DL#SHIV"
        assert out[SIGNAL_GEO][0][0] == "LMK#DL#RAMESH"  # nearest to the centre

    def test_geo_filter_applies_to_every_signal(
        self, engine: InMemorySearchEngine, embedder: HashingEmbedder
    ) -> None:
        out = engine.hybrid_search(
            query_text="shiv mandir",
            query_vector=embedder.embed("shiv mandir"),
            centre=RAMESH,
            radius_m=3_000.0,
            size=10,
        )
        for signal in out.values():
            assert all(doc_id != "LMK#MH#SHIV" for doc_id, _ in signal)

    def test_geo_scores_decrease_with_distance(
        self, engine: InMemorySearchEngine
    ) -> None:
        out = engine.hybrid_search(
            query_text="", query_vector=None, centre=RAMESH, radius_m=3_000.0, size=10
        )
        scores = [s for _, s in out[SIGNAL_GEO]]
        assert scores == sorted(scores, reverse=True)
        assert all(0.0 <= s <= 1.0 for s in scores)

    def test_size_caps_each_signal(self, engine: InMemorySearchEngine) -> None:
        out = engine.hybrid_search(
            query_text="mandir",
            query_vector=None,
            centre=RAMESH,
            radius_m=50_000.0,
            size=1,
        )
        assert len(out[SIGNAL_LEXICAL]) == 1
        assert len(out[SIGNAL_GEO]) == 1

    def test_without_centre_geo_signal_is_empty(
        self, engine: InMemorySearchEngine
    ) -> None:
        out = engine.hybrid_search(
            query_text="mandir",
            query_vector=None,
            centre=None,
            radius_m=3_000.0,
            size=10,
        )
        assert out[SIGNAL_GEO] == []
        assert out[SIGNAL_LEXICAL]


class TestMapping:
    def test_dimension_is_wired_through(self) -> None:
        m = index_mapping(1024)
        assert m["mappings"]["properties"]["embedding"]["dimension"] == 1024
        assert m["mappings"]["properties"]["location"]["type"] == "geo_point"
        assert m["settings"]["index.knn"] is True
