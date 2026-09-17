"""Integrity tests for the seeds, corpus and gold set.

Everything the project reports rests on these files, and they can rot silently:
a perturbation that corrupts its own answer key, a coordinate outside India, a
gold split that drifts away from the corpus distribution. None of that raises an
exception anywhere -- it just quietly changes the numbers. So it is asserted.

These tests skip rather than fail when the data files are absent, so a fresh
clone can run the unit tests before downloading the 23 MB directory.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pytest

from patasetu import gazetteer
from patasetu.digipin import MAX_LAT, MAX_LON, MIN_LAT, MIN_LON, encode, is_valid

DATA = Path(__file__).resolve().parents[1] / "eval" / "data"


def _load(name: str) -> list[dict[str, Any]]:
    path = DATA / name
    if not path.exists():
        pytest.skip(
            f"{path} not built yet; run scripts.build_seeds and eval.corpus_gen"
        )
    return [json.loads(line) for line in path.open(encoding="utf-8")]


@pytest.fixture(scope="module")
def seeds() -> list[dict[str, Any]]:
    return _load("seed_addresses.jsonl")


@pytest.fixture(scope="module")
def corpus() -> list[dict[str, Any]]:
    return _load("corpus.jsonl")


@pytest.fixture(scope="module")
def gold_dev() -> list[dict[str, Any]]:
    return _load("gold_dev.jsonl")


@pytest.fixture(scope="module")
def gold_test() -> list[dict[str, Any]]:
    return _load("gold_test.jsonl")


class TestSeeds:
    def test_there_are_at_least_a_hundred(self, seeds: list[dict[str, Any]]) -> None:
        """FR-35: the corpus is seeded from at least 100 real addresses."""
        assert len(seeds) >= 100

    def test_every_seed_has_a_real_validated_pincode(
        self, seeds: list[dict[str, Any]]
    ) -> None:
        for s in seeds:
            pin = s["truth"]["pincode"]
            assert gazetteer.lookup(pin) is not None, f"{s['seed_id']} pincode {pin}"

    def test_coordinates_are_inside_india(self, seeds: list[dict[str, Any]]) -> None:
        for s in seeds:
            lat, lng = s["geo"]["lat"], s["geo"]["lng"]
            assert MIN_LAT <= lat <= MAX_LAT, s["seed_id"]
            assert MIN_LON <= lng <= MAX_LON, s["seed_id"]

    def test_no_placeholder_coordinates(self, seeds: list[dict[str, Any]]) -> None:
        """A coordinate rounded to 0.01 degrees carries ~1.1 km of error.

        The directory stores some of these at full width ("25.250000"), so they
        pass a decimal-count check while being useless as an answer key.
        """
        for s in seeds:
            lat, lng = s["geo"]["lat"], s["geo"]["lng"]
            assert not (round(lat, 2) == lat and round(lng, 2) == lng), s["seed_id"]

    def test_coordinate_agrees_with_its_own_pincode(
        self, seeds: list[dict[str, Any]]
    ) -> None:
        """Catches the source's georeferencing errors.

        Pincode 683545 is in Kerala but its directory office is plotted in
        Karnataka, 700 km away. A seed like that would enter the gold set as an
        answer key pointing at the wrong state.
        """
        for s in seeds:
            info = gazetteer.lookup(s["truth"]["pincode"])
            assert info is not None
            if not info.has_centroid:
                continue
            km = (
                gazetteer.haversine_m(
                    s["geo"]["lat"], s["geo"]["lng"], info.lat, info.lng
                )
                / 1000.0
            )
            assert km < 120, f"{s['seed_id']} is {km:.0f} km from its pincode centroid"

    def test_digipin_matches_the_coordinate(self, seeds: list[dict[str, Any]]) -> None:
        for s in seeds:
            assert is_valid(s["digipin"]), s["seed_id"]
            assert s["digipin"] == encode(s["geo"]["lat"], s["geo"]["lng"])

    def test_no_phone_numbers_in_seed_text(self, seeds: list[dict[str, Any]]) -> None:
        """NFR-19: seeds are institutional addresses and carry no personal data."""
        import re

        for s in seeds:
            assert not re.search(r"(?<!\d)[6-9]\d{9}(?!\d)", s["raw"]), s["seed_id"]

    def test_spread_across_many_states(self, seeds: list[dict[str, Any]]) -> None:
        # A corpus that is 90% one metro would make the learning curve look
        # better than it is.
        assert len({s["truth"]["state"] for s in seeds}) >= 10
        assert len({s["truth"]["city"] for s in seeds}) >= 15

    def test_most_seeds_carry_a_real_landmark(
        self, seeds: list[dict[str, Any]]
    ) -> None:
        # Landmark-based addressing is the problem being solved, so the corpus
        # has to exercise it.
        with_landmarks = sum(1 for s in seeds if s["truth"]["landmarks"])
        assert with_landmarks >= len(seeds) * 0.5

    def test_landmark_distances_are_plausible(
        self, seeds: list[dict[str, Any]]
    ) -> None:
        for s in seeds:
            for lm in s.get("landmark_truth", []):
                # Below 25 m two records share a coordinate; above 3 km it is not
                # a landmark anyone would navigate by.
                assert 25.0 < lm["distance_m"] <= 3_000.0, s["seed_id"]


class TestCorpus:
    def test_is_large_enough(self, corpus: list[dict[str, Any]]) -> None:
        """FR-35: at least 2,000 generated addresses."""
        assert len(corpus) >= 2_000

    def test_every_row_traces_back_to_a_seed(
        self, corpus: list[dict[str, Any]], seeds: list[dict[str, Any]]
    ) -> None:
        seed_ids = {s["seed_id"] for s in seeds}
        for row in corpus:
            assert row["seed_id"] in seed_ids

    def test_perturbation_never_corrupts_the_answer_key(
        self, corpus: list[dict[str, Any]], seeds: list[dict[str, Any]]
    ) -> None:
        """The whole point of a generated corpus.

        Misspelling a locality in the *input* must leave the truth intact --
        otherwise the system is rewarded for reproducing the typo, and the
        metric measures nothing.
        """
        by_seed = {s["seed_id"]: s for s in seeds}
        for row in corpus:
            seed = by_seed[row["seed_id"]]
            for field in ("locality", "city", "district", "state", "pincode"):
                assert row["truth"][field] == seed["truth"][field], (
                    f"{row['address_id']}: perturbation changed truth.{field}"
                )
            assert row["geo"] == seed["geo"]
            assert row["digipin"] == seed["digipin"]

    def test_added_unit_numbers_are_recorded_in_the_truth(
        self, corpus: list[dict[str, Any]]
    ) -> None:
        # A synthetic unit number is legitimate only if the answer key knows
        # about it.
        for row in corpus:
            if "add_unit" in row["perturbations"]:
                assert row["truth"]["building"] is not None, row["address_id"]

    def test_dropped_pincode_leaves_the_truth_intact(
        self, corpus: list[dict[str, Any]]
    ) -> None:
        # Removing the pincode from the input does not change the correct
        # answer; recovering it is exactly what the landmark graph is for.
        for row in corpus:
            if "drop_pincode" in row["perturbations"]:
                assert row["truth"]["pincode"] is not None
                assert row["truth"]["pincode"] not in row["raw"]

    def test_injected_phones_are_not_real_subscriber_numbers(
        self, corpus: list[dict[str, Any]]
    ) -> None:
        """The corpus is committed to a public repo.

        Generated phone numbers use the 99999xxxxx shape: valid enough for S1's
        regex to catch, and not allocated to anyone.
        """
        for row in corpus:
            for phone in row.get("phones_expected", []):
                digits = "".join(ch for ch in phone if ch.isdigit())
                # Strip a trunk "0" or a "91" country code before checking the
                # subscriber number itself.
                if digits.startswith("0"):
                    digits = digits.lstrip("0")
                if len(digits) > 10 and digits.startswith("91"):
                    digits = digits[2:]
                assert digits[:5] == "99999", f"{row['address_id']}: {phone}"

    def test_some_rows_are_left_clean(self, corpus: list[dict[str, Any]]) -> None:
        # A ceiling to compare against, and every seed gets one clean variant.
        assert any(not row["perturbations"] for row in corpus)

    def test_raw_text_is_never_empty(self, corpus: list[dict[str, Any]]) -> None:
        for row in corpus:
            assert row["raw"].strip(), row["address_id"]


class TestGoldSplits:
    def test_sizes(
        self, gold_dev: list[dict[str, Any]], gold_test: list[dict[str, Any]]
    ) -> None:
        """FR-36: at least 300 gold addresses, split evenly."""
        assert len(gold_dev) + len(gold_test) >= 300
        assert abs(len(gold_dev) - len(gold_test)) <= 1

    def test_dev_and_test_do_not_overlap(
        self, gold_dev: list[dict[str, Any]], gold_test: list[dict[str, Any]]
    ) -> None:
        dev_ids = {r["address_id"] for r in gold_dev}
        test_ids = {r["address_id"] for r in gold_test}
        assert not (dev_ids & test_ids)

    def test_splits_match_the_corpus_difficulty_distribution(
        self,
        corpus: list[dict[str, Any]],
        gold_dev: list[dict[str, Any]],
        gold_test: list[dict[str, Any]],
    ) -> None:
        """Regression: round-robin stratification made the gold set easier.

        Equalising the difficulty buckets pulled clean rows from 7.6% of the
        corpus up to 37.5% of the gold set, which would have made every reported
        metric optimistic -- and invisibly so, since nothing in the numbers
        themselves would show it.
        """

        def heavy_share(rows: list[dict[str, Any]]) -> float:
            return sum(1 for r in rows if len(r["perturbations"]) >= 3) / len(rows)

        reference = heavy_share(corpus)
        for name, split in (("dev", gold_dev), ("test", gold_test)):
            assert math.isclose(heavy_share(split), reference, abs_tol=0.12), (
                f"{name} split difficulty has drifted from the corpus"
            )

    def test_label_provenance_is_recorded(self, gold_dev: list[dict[str, Any]]) -> None:
        # Every published number has to be traceable to how its key was made.
        for row in gold_dev:
            assert row["label_status"] in {"derived", "verified", "wrong", "corrected"}
            assert row["label_source"]

    def test_no_seed_dominates_a_split(self, gold_dev: list[dict[str, Any]]) -> None:
        from collections import Counter

        counts = Counter(r["seed_id"] for r in gold_dev)
        assert max(counts.values()) <= 3
