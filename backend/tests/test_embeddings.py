"""Embedder tests: the vector signal must be deterministic, normalised and useful."""

from __future__ import annotations

import math

import pytest

from patasetu.embeddings import (
    HashingEmbedder,
    cosine,
    is_zero,
    l2_normalise,
    landmark_embedding_text,
)


class TestHashingEmbedder:
    def test_output_has_requested_dimension(self) -> None:
        for dim in (16, 64, 1024):
            assert len(HashingEmbedder(dim=dim).embed("shiv mandir")) == dim

    def test_rejects_non_positive_dimension(self) -> None:
        with pytest.raises(ValueError):
            HashingEmbedder(dim=0)

    def test_is_unit_length(self, embedder: HashingEmbedder) -> None:
        v = embedder.embed("Sunrise Apartments")
        assert math.isclose(math.sqrt(sum(x * x for x in v)), 1.0, abs_tol=1e-9)

    def test_is_deterministic_across_instances(self) -> None:
        # blake2b, not hash(): PYTHONHASHSEED must not change the vectors, or the
        # index written by one process will never match queries from another.
        a = HashingEmbedder(dim=64).embed("Ramesh Nagar")
        b = HashingEmbedder(dim=64).embed("Ramesh Nagar")
        assert a == b

    def test_empty_text_is_a_zero_vector(self, embedder: HashingEmbedder) -> None:
        assert is_zero(embedder.embed(""))
        assert is_zero(embedder.embed("   ,,, "))

    def test_case_and_punctuation_are_ignored(self, embedder: HashingEmbedder) -> None:
        assert embedder.embed("Shiv Mandir") == embedder.embed("shiv, mandir!")

    @pytest.mark.parametrize(
        ("a", "b", "at_least"),
        [
            ("Sunrise Apartments", "Sunrise Apts", 0.5),
            ("Shiv Mandir", "Shiv Mandhir", 0.6),
            ("Loyola College", "Loyola Collage", 0.6),
            ("Ramesh Nagar", "Ramesh Nagar", 1.0),
        ],
    )
    def test_similar_names_are_close(
        self, embedder: HashingEmbedder, a: str, b: str, at_least: float
    ) -> None:
        assert cosine(embedder.embed(a), embedder.embed(b)) >= at_least - 1e-9

    @pytest.mark.parametrize(
        ("a", "b", "at_most"),
        [
            ("Shiv Mandir", "Gupta General Store", 0.15),
            ("Ramesh Nagar", "Rajouri Garden", 0.35),
        ],
    )
    def test_unrelated_names_are_far(
        self, embedder: HashingEmbedder, a: str, b: str, at_most: float
    ) -> None:
        assert cosine(embedder.embed(a), embedder.embed(b)) <= at_most

    def test_word_boundary_padding_distinguishes_prefixes(
        self, embedder: HashingEmbedder
    ) -> None:
        # Without boundary markers "nagar" and "agar" share every trigram.
        assert cosine(embedder.embed("nagar"), embedder.embed("agar")) < 0.95

    def test_batch_matches_single(self, embedder: HashingEmbedder) -> None:
        texts = ["Shiv Mandir", "Gupta Store", ""]
        assert embedder.embed_batch(texts) == [embedder.embed(t) for t in texts]


class TestVectorHelpers:
    def test_cosine_of_identical_is_one(self) -> None:
        assert math.isclose(cosine([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]), 1.0)

    def test_cosine_of_orthogonal_is_zero(self) -> None:
        assert cosine([1.0, 0.0], [0.0, 1.0]) == 0.0

    def test_cosine_handles_unnormalised_input(self) -> None:
        assert math.isclose(cosine([2.0, 0.0], [5.0, 0.0]), 1.0)

    def test_cosine_of_mismatched_lengths_is_zero(self) -> None:
        assert cosine([1.0, 2.0], [1.0]) == 0.0

    def test_cosine_of_zero_vector_is_zero(self) -> None:
        assert cosine([0.0, 0.0], [1.0, 1.0]) == 0.0

    def test_l2_normalise_leaves_zero_vector_alone(self) -> None:
        assert l2_normalise([0.0, 0.0]) == [0.0, 0.0]

    def test_landmark_text_dedupes_aliases(self) -> None:
        text = landmark_embedding_text(
            "Shiv Mandir", ["shiv mandir", "Shiv Temple", "SHIV MANDIR"]
        )
        assert text == "Shiv Mandir Shiv Temple"
