"""S0 tests: folding, transliteration, abbreviations, cache keys."""

from __future__ import annotations

import pytest

from patasetu.normalize import (
    expand_abbreviations,
    fold,
    normalise,
    transliterate_devanagari,
)


class TestFold:
    def test_collapses_separators_and_case(self) -> None:
        assert fold("H.No 14,, Ramesh  Nagar | Delhi") == "h.no 14 ramesh nagar delhi"

    def test_nfkc_folds_fullwidth_digits(self) -> None:
        # Pasted from a spreadsheet, these are a pincode the regex would miss.
        assert "110015" in fold("１１００１５")

    def test_keeps_devanagari(self) -> None:
        assert "मंदिर" in fold("शिव मंदिर")


class TestTransliteration:
    @pytest.mark.parametrize(
        ("deva", "latin"),
        [
            ("मंदिर", "mandir"),  # anusvara must not eat the inherent vowel
            ("नगर", "nagar"),  # word-final schwa deleted
            ("रमेश", "ramesh"),
            ("दिल्ली", "dilli"),  # virama emits nothing
            ("मुंबई", "mumbai"),  # anusvara assimilates before a labial
            ("बेंगलुरु", "bengaluru"),
            ("गली", "gali"),
            ("चेन्नई", "chennai"),
            ("शिव मंदिर", "shiv mandir"),
        ],
    )
    def test_known_place_names(self, deva: str, latin: str) -> None:
        assert transliterate_devanagari(deva) == latin

    def test_devanagari_digits_become_latin(self) -> None:
        assert transliterate_devanagari("११००१५") == "110015"

    def test_virama_never_appears_in_output(self) -> None:
        # Regression: the virama fell through the emit loop and surfaced raw,
        # producing "dil्li".
        out = transliterate_devanagari("दिल्ली नई दिल्ली क्षेत्र")
        assert "्" not in out

    def test_latin_passes_through_untouched(self) -> None:
        assert transliterate_devanagari("ramesh nagar 110015") == "ramesh nagar 110015"


class TestAbbreviations:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("h.no 14", "house number 14"),
            ("opp sbi", "opposite sbi"),
            ("nr mandir", "near mandir"),
            ("sec 22", "sector 22"),
            ("blr", "bengaluru"),
            ("up", "uttar pradesh"),
        ],
    )
    def test_expands(self, raw: str, expected: str) -> None:
        assert expand_abbreviations(raw) == expected

    @pytest.mark.parametrize(
        "word",
        [
            "gupta",
            "behind",
            "store",
            "delhi",
            "sunrise",
            "upper",
            "nagar",
            "apartments",
        ],
    )
    def test_never_matches_inside_a_word(self, word: str) -> None:
        """Regression: alternation bound looser than concatenation.

        The pattern guarded only its first and last branches, so every branch in
        between matched mid-word: the "up" branch rewrote "gupta" to "guttar
        pradeshta", and the "beh" branch rewrote "behind" to "behindind". The fix
        was a non-capturing group around the alternation.
        """
        assert expand_abbreviations(word) == word

    def test_longest_key_wins(self) -> None:
        # "h no" must beat any shorter key that also matches.
        assert expand_abbreviations("h no 5") == "house number 5"


class TestNormalise:
    def test_cache_key_is_stable_across_formatting(self) -> None:
        a = normalise("H.No 14, Ramesh Nagar, Delhi 110015")
        b = normalise("h no 14   ramesh  nagar, delhi, 110015")
        assert a.cache_key == b.cache_key

    def test_cache_key_differs_on_content(self) -> None:
        a = normalise("h no 14 ramesh nagar delhi 110015")
        b = normalise("h no 41 ramesh nagar delhi 110015")
        assert a.cache_key != b.cache_key

    def test_retrieval_text_keeps_both_scripts(self) -> None:
        n = normalise("शिव मंदिर, रमेश नगर")
        assert "मंदिर" in n.retrieval_text  # original, for a Devanagari index
        assert "mandir" in n.retrieval_text  # transliteration, for a Latin one

    def test_detects_script_mix(self) -> None:
        assert normalise("शिव मंदिर near SBI").is_multi_script is True
        assert normalise("shiv mandir near SBI").is_multi_script is False

    def test_flags_unsupported_indic_script(self) -> None:
        # Tamil is structurally supported but unmeasured; it must say so rather
        # than be silently mangled.
        assert normalise("சென்னை 600034").has_unsupported_script is True

    def test_devanagari_input_yields_latin_working_text(self) -> None:
        n = normalise("शिव मंदिर के पीछे, रमेश नगर, दिल्ली 110015")
        assert "shiv mandir" in n.text
        assert "110015" in n.text
