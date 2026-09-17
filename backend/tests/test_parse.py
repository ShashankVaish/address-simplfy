"""S1 tests: phone stripping, pincode validation, units, landmark relations."""

from __future__ import annotations

import pytest

from patasetu.models import Relation
from patasetu.normalize import normalise
from patasetu.parse import ParseResult, extract_landmarks, parse, strip_phones


def run(raw: str) -> ParseResult:
    """Normalise then parse, the way the pipeline does."""
    return parse(normalise(raw).text)


class TestPhoneStripping:
    """NFR-25: a phone number must never reach a model or the embedding API."""

    @pytest.mark.parametrize(
        "phone",
        [
            "9876543210",
            "+91 98765 43210",
            "+919876543210",
            "09876543210",
            "98765-43210",
            "91 98765 43210",
        ],
    )
    def test_removes_every_common_format(self, phone: str) -> None:
        cleaned, found = strip_phones(f"h no 14 ramesh nagar delhi 110015 {phone}")
        assert found, f"failed to detect {phone}"
        # Only the house number and the pincode may remain.
        remaining = cleaned.replace("110015", "").replace("14", "")
        assert not any(ch.isdigit() for ch in remaining)

    def test_pincode_survives_phone_removal(self) -> None:
        p = run("h no 14 ramesh nagar delhi 110015 call me on 9876543210")
        assert p.pincode == "110015"
        assert p.phones

    def test_phone_digits_are_never_read_as_a_pincode(self) -> None:
        # A ten-digit mobile contains six-digit substrings: 9876543210 holds
        # "876543", "765432" and so on. Phones are stripped first for this
        # reason.
        p = run("ramesh nagar delhi 9876543210")
        assert p.pincode is None

    def test_clean_text_excludes_the_number(self) -> None:
        p = run("h no 14 ramesh nagar delhi 110015 9876543210")
        assert "9876543210" not in p.clean_text


class TestPincode:
    def test_validated_against_the_gazetteer(self) -> None:
        p = run("ramesh nagar delhi 110015")
        assert p.pincode == "110015"
        assert p.pincode_known is True
        assert p.state == "Delhi"
        assert p.centroid is not None

    def test_unknown_six_digit_token_is_not_accepted(self) -> None:
        # 999999 is not an allocated pincode. Accepting it would propagate a
        # wrong centroid, state and geo constraint through every later stage.
        p = run("some place 999999")
        assert p.pincode is None
        assert "999999" in p.pincode_candidates

    def test_locality_agreement_detected(self) -> None:
        assert run("ramesh nagar delhi 110015").locality_agrees is True

    def test_locality_disagreement_detected(self) -> None:
        assert run("btm layout 110015").locality_agrees is False

    def test_state_conflict_is_flagged_not_resolved(self) -> None:
        """NFR-14: a conflict is penalised, never silently decided."""
        p = run("bengaluru karnataka 110015")
        assert p.state_conflict is True
        assert any("CONFLICT" in e for e in p.evidence)

    def test_no_conflict_when_state_agrees(self) -> None:
        assert run("ramesh nagar delhi 110015").state_conflict is False


class TestUnitNumbers:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("h no 14 ramesh nagar delhi 110015", "14"),
            ("flat 4b sunrise apartments noida 201301", "4B"),
            ("plot no 42 chennai 600034", "42"),
            ("house number 115b patna 803201", "115B"),
        ],
    )
    def test_extracts_building(self, raw: str, expected: str) -> None:
        assert run(raw).building == expected

    def test_extracts_floor_tower_and_sector(self) -> None:
        p = run("flat 4b 2nd floor tower c sector 22 noida 201301")
        assert p.floor == "2"
        assert p.tower == "C"
        assert p.sub_locality == "Sector 22"

    def test_absent_fields_stay_none(self) -> None:
        """FR-02: never invent a value."""
        p = run("ramesh nagar delhi 110015")
        assert p.building is None
        assert p.floor is None
        assert p.tower is None


class TestLandmarks:
    def test_english_preposition_order(self) -> None:
        assert extract_landmarks("behind shiv mandir") == [
            ("shiv mandir", Relation.BEHIND)
        ]

    def test_hindi_postposition_order(self) -> None:
        """Hindi puts the relation after the landmark. Both orders must work.

        Matching only the English order on "shiv mandir ke pichhe" captured
        everything *after* the postposition, returning the locality as the
        landmark and discarding the actual landmark (FR-09).
        """
        assert extract_landmarks("shiv mandir ke pichhe") == [
            ("shiv mandir", Relation.BEHIND)
        ]

    def test_relation_is_preserved_not_flattened(self) -> None:
        # "behind X" and "opposite X" are different doorsteps.
        assert extract_landmarks("opposite shiv mandir")[0][1] is Relation.OPPOSITE
        assert extract_landmarks("near shiv mandir")[0][1] is Relation.NEAR

    def test_multiple_landmarks(self) -> None:
        p = run(
            "h no 14 behind shiv mandir near gupta general store "
            "opp water tank ramesh nagar delhi 110015"
        )
        names = [n for n, _ in p.landmarks]
        assert "shiv mandir" in names
        assert "gupta general store" in names

    def test_phrase_does_not_swallow_the_locality(self) -> None:
        """Regression: captured "water tank ramesh nagar delhi" as one name."""
        p = run("opp water tank ramesh nagar delhi 110015")
        assert ("water tank", Relation.OPPOSITE) in p.landmarks

    def test_hindi_and_latin_input_agree(self) -> None:
        a = run("शिव मंदिर के पीछे, रमेश नगर, दिल्ली 110015")
        b = run("behind shiv mandir, ramesh nagar, delhi 110015")
        assert [n for n, _ in a.landmarks] == [n for n, _ in b.landmarks]
        assert a.pincode == b.pincode == "110015"

    def test_weak_words_are_not_landmarks(self) -> None:
        assert extract_landmarks("near here") == []


class TestCity:
    def test_never_fuzzy_matches_a_locality_suffix(self) -> None:
        """Regression: "nagar" scored 0.909 against the district "Nagaur".

        High enough to pass any sane cutoff, which assigned Delhi addresses to
        Rajasthan.
        """
        assert run("h no 14 ramesh nagar delhi 110015").city == "New Delhi"

    def test_delhi_compass_district_is_not_used_as_a_city(self) -> None:
        p = run("ramesh nagar delhi 110015")
        assert p.district == "West"
        assert p.city == "New Delhi"

    @pytest.mark.parametrize(
        ("raw", "city"),
        [
            ("btm layout bengaluru 560029", "Bengaluru"),
            ("jubilee hills hyderabad 500033", "Hyderabad"),
            ("flat 4b sector 22 noida 201301", "Noida"),
        ],
    )
    def test_writes_the_city_a_human_would(self, raw: str, city: str) -> None:
        assert run(raw).city == city


class TestChatter:
    def test_delivery_instructions_are_removed(self) -> None:
        p = run("h no 14 ramesh nagar delhi 110015 call before coming")
        assert "call before coming" not in p.clean_text
