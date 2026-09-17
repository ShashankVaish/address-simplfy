"""S0 -- normalisation, transliteration and the cache key.

Zero cost, sub-millisecond, and it decides how well every later stage performs:
S2's lexical matching, the embedding text, and the cache hit rate are all
functions of what happens here.

Three jobs:

1.  **Fold** the text to a canonical form -- Unicode NFKC, consistent
    punctuation, collapsed whitespace, lowercase.
2.  **Transliterate** Devanagari to Latin while *keeping the original*. Both
    forms go into retrieval, because a landmark stored as "shiv mandir" must be
    found by an address written "शिव मंदिर" and vice versa (FR-09).
3.  **Expand** abbreviations, so "h.no 14 opp shiv mandir" and "house number 14
    opposite shiv mandir" produce the same cache key and the same query.

The transliteration is a rule table rather than a library. It is imperfect --
it targets the subset of Devanagari that appears in address text, not general
Hindi -- but it adds no dependency, runs in microseconds, and works in a Lambda
and on a rider's phone offline. For this pipeline, being fast and present beats
being linguistically complete.
"""

from __future__ import annotations

import functools
import hashlib
import json
import os
import re
import unicodedata
from dataclasses import dataclass
from typing import Final

from patasetu.config import DATA_DIR

# --------------------------------------------------------------------------
# Script detection
# --------------------------------------------------------------------------

_DEVANAGARI = re.compile(r"[ऀ-ॿ]")
_LATIN = re.compile(r"[A-Za-z]")
# Other Indic blocks we can detect but do not transliterate. Flagged so the
# response can say so honestly instead of silently mangling the text: the README
# claims measured support for Latin and Devanagari only.
_OTHER_INDIC = re.compile(
    r"[ঀ-৿"  # Bengali
    r"਀-੿"  # Gurmukhi
    r"઀-૿"  # Gujarati
    r"଀-୿"  # Odia
    r"஀-௿"  # Tamil
    r"ఀ-౿"  # Telugu
    r"ಀ-೿"  # Kannada
    r"ഀ-ൿ]"  # Malayalam
)

# --------------------------------------------------------------------------
# Devanagari -> Latin
# --------------------------------------------------------------------------

# Two-character sequences and conjuncts first; the table is applied
# longest-key-first so "क्ष" is not decomposed into "क" + "ष".
# fmt: off
_DEVA_CONSONANTS: Final[dict[str, str]] = {
    "क्ष": "ksh", "ज्ञ": "gya", "श्र": "shr", "त्र": "tr",
    "क": "k", "ख": "kh", "ग": "g", "घ": "gh", "ङ": "n",
    "च": "ch", "छ": "chh", "ज": "j", "झ": "jh", "ञ": "n",
    "ट": "t", "ठ": "th", "ड": "d", "ढ": "dh", "ण": "n",
    "त": "t", "थ": "th", "द": "d", "ध": "dh", "न": "n",
    "प": "p", "फ": "ph", "ब": "b", "भ": "bh", "म": "m",
    "य": "y", "र": "r", "ल": "l", "व": "v",
    "श": "sh", "ष": "sh", "स": "s", "ह": "h",
    "ळ": "l",
    # Nukta forms, common in place names.
    "क़": "q", "ख़": "kh", "ग़": "g", "ज़": "z", "ड़": "r",
    "ढ़": "rh", "फ़": "f", "य़": "y",
}
# fmt: on

# Single-character vowel values, not strict ITRANS. Indian place names are
# romanised short in practice -- "Delhi", not "Delhee"; "Mumbai", not
# "Mumbaee" -- and the transliteration exists to match an index built from those
# romanised spellings.
# fmt: off
_DEVA_VOWELS: Final[dict[str, str]] = {
    "अ": "a", "आ": "a", "इ": "i", "ई": "i", "उ": "u", "ऊ": "u",
    "ऋ": "ri", "ए": "e", "ऐ": "ai", "ओ": "o", "औ": "au",
    "ऑ": "o", "ॲ": "a",
}
# fmt: on

# Dependent vowel signs (matras). A matra replaces the inherent vowel.
# fmt: off
_DEVA_MATRAS: Final[dict[str, str]] = {
    "ा": "a", "ि": "i", "ी": "i", "ु": "u", "ू": "u",
    "ृ": "ri", "े": "e", "ै": "ai", "ो": "o", "ौ": "au",
    "ॉ": "o", "ॅ": "a",
}
# fmt: on

# Nasal and other marks. These attach *after* a syllable and, crucially, do NOT
# suppress its inherent vowel.
_DEVA_NASALS: Final[dict[str, str]] = {
    "ं": "n",  # anusvara
    "ँ": "n",  # candrabindu
    "ः": "h",  # visarga
}

# The virama is the only mark that cancels the inherent vowel.
_VIRAMA: Final[str] = "्"

_DEVA_SILENT: Final[dict[str, str]] = {
    _VIRAMA: "",
    "ऽ": "",  # avagraha
    "़": "",  # bare nukta
}

_DEVA_MARKS: Final[dict[str, str]] = {**_DEVA_NASALS, **_DEVA_SILENT}

# Anusvara assimilates to the following consonant's place of articulation. Before
# a labial it is "m", which is why "मुंबई" is Mumbai and not Munbai.
_LABIALS: Final[frozenset[str]] = frozenset("पफबभम")

# fmt: off
_DEVA_DIGITS: Final[dict[str, str]] = {
    "०": "0", "१": "1", "२": "2", "३": "3", "४": "4",
    "५": "5", "६": "6", "७": "7", "८": "8", "९": "9",
}
# fmt: on

# Longest-first so multi-codepoint conjuncts win.
_DEVA_KEYS: Final[tuple[str, ...]] = tuple(
    sorted(
        (
            *_DEVA_CONSONANTS,
            *_DEVA_VOWELS,
            *_DEVA_MATRAS,
            *_DEVA_MARKS,
            *_DEVA_DIGITS,
        ),
        key=len,
        reverse=True,
    )
)

_DEVA_ALL: Final[dict[str, str]] = {
    **_DEVA_CONSONANTS,
    **_DEVA_VOWELS,
    **_DEVA_MATRAS,
    **_DEVA_MARKS,
    **_DEVA_DIGITS,
}


def has_devanagari(text: str) -> bool:
    return bool(_DEVANAGARI.search(text))


def has_latin(text: str) -> bool:
    return bool(_LATIN.search(text))


def detect_scripts(text: str) -> set[str]:
    """Which scripts appear. Used to route multi-script input to the strong model."""
    found: set[str] = set()
    if has_latin(text):
        found.add("latin")
    if has_devanagari(text):
        found.add("devanagari")
    if _OTHER_INDIC.search(text):
        found.add("other_indic")
    return found


def transliterate_devanagari(text: str) -> str:
    """Devanagari to Latin, approximately, for retrieval purposes.

    The inherent vowel is what makes this non-trivial. A Devanagari consonant
    carries an implicit "a", so a naive character map turns "मंदिर" into "mdir".
    Three rules govern it, and getting any of them wrong produces tokens that
    match nothing in the index:

    1.  A **matra** replaces the inherent vowel. A **virama** cancels it.
    2.  An **anusvara does not** cancel it -- "मं" is "man", not "mn". Treating
        the nasal marks as suppressors is what produced "mndira" for "मंदिर".
    3.  **Word-final schwa is deleted**, as in spoken Hindi: "नगर" is "nagar",
        not "nagara", and "रमेश" is "ramesh", not "ramesha". Without this, every
        transliterated token acquires a trailing vowel and fails to match the
        romanised spelling actually stored in the landmark index.

    Not a general Hindi romaniser -- "आगरा" comes out "agara" rather than "Agra",
    because predicting medial schwa deletion needs a lexicon. It targets the
    subset of Devanagari that appears in address text, and it is only ever a
    retrieval aid: the original is always kept alongside.
    """
    # Pass 1: segment into units, longest key first, so conjuncts stay whole.
    units: list[tuple[str, str]] = []  # (kind, key)
    i, n = 0, len(text)
    while i < n:
        for key in _DEVA_KEYS:
            if text.startswith(key, i):
                if key in _DEVA_CONSONANTS:
                    kind = "consonant"
                elif key in _DEVA_MATRAS:
                    kind = "matra"
                elif key in _DEVA_NASALS:
                    kind = "nasal"
                elif key == _VIRAMA:
                    kind = "virama"
                elif key in _DEVA_SILENT:
                    kind = "silent"
                elif key in _DEVA_VOWELS:
                    kind = "vowel"
                else:
                    kind = "digit"
                units.append((kind, key))
                i += len(key)
                break
        else:
            units.append(("other", text[i]))
            i += 1

    # Pass 2: emit, deciding the inherent vowel from what follows.
    out: list[str] = []
    for idx, (kind, key) in enumerate(units):
        if kind == "consonant":
            out.append(_DEVA_CONSONANTS[key])

            nxt_kind, nxt_key = (
                units[idx + 1] if idx + 1 < len(units) else ("boundary", "")
            )
            if nxt_kind in {"matra", "virama"}:
                continue  # vowel supplied or cancelled

            # Word-final schwa deletion. "Final" means the syllable is followed
            # by nothing, or by a space or non-Devanagari character.
            at_word_end = nxt_kind in {"boundary", "other"}
            if at_word_end and not (nxt_kind == "other" and nxt_key.strip()):
                continue

            out.append("a")

        elif kind == "matra":
            out.append(_DEVA_MATRAS[key])
        elif kind == "nasal":
            # Assimilate anusvara to a following labial.
            nxt = units[idx + 1] if idx + 1 < len(units) else ("boundary", "")
            if key in {"ं", "ँ"} and nxt[0] == "consonant" and nxt[1] in _LABIALS:
                out.append("m")
            else:
                out.append(_DEVA_NASALS[key])
        elif kind == "vowel":
            out.append(_DEVA_VOWELS[key])
        elif kind == "digit":
            out.append(_DEVA_DIGITS[key])
        elif kind in {"silent", "virama"}:
            # The virama has already done its work above, by suppressing the
            # preceding consonant's inherent vowel. It emits nothing itself --
            # omitting this branch let the raw mark fall through to the
            # pass-through case and surface in output as "dil्li".
            pass
        else:
            out.append(key)

    return "".join(out)


# --------------------------------------------------------------------------
# Abbreviations
# --------------------------------------------------------------------------


@functools.lru_cache(maxsize=1)
def load_abbreviations() -> dict[str, str]:
    """Load the expansion table once per container."""
    path = os.path.join(DATA_DIR, "abbreviations.json")
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh)
    return {k: v for k, v in raw.items() if not k.startswith("_")}


@functools.lru_cache(maxsize=1)
def _abbreviation_pattern() -> re.Pattern[str]:
    """One alternation over all keys, longest first.

    Longest-first is not cosmetic: with "h" and "h no" both in the table, a
    shortest-first alternation would rewrite "h no 14" to "house no 14" and then
    never match "h no". Sorting by length makes the longer key win.

    A single compiled alternation also means one pass over the string instead of
    158, which is the difference between ~30 us and ~2 ms on the hot path.
    """
    keys = sorted(load_abbreviations(), key=len, reverse=True)
    alternation = "|".join(re.escape(k) for k in keys)
    # Custom boundaries because keys contain '.' and '/', which \b handles
    # inconsistently: require a non-word character (or string edge) either side.
    #
    # The non-capturing group is essential. Alternation binds looser than
    # concatenation, so "(?<!\w)a|bb|cc(?!\w)" applies the guards only to the
    # first and last branches and lets every branch in between match
    # mid-word -- which silently rewrote "gupta" to "guttar pradeshta" (the "up"
    # branch) and "behind" to "behindind" (the "beh" branch).
    return re.compile(rf"(?<!\w)(?:{alternation})(?!\w)", re.IGNORECASE)


def expand_abbreviations(text: str) -> str:
    """Rewrite known abbreviations to their canonical long form."""
    table = load_abbreviations()
    pattern = _abbreviation_pattern()

    def sub(m: re.Match[str]) -> str:
        return table.get(m.group(0).lower(), m.group(0))

    return pattern.sub(sub, text)


# --------------------------------------------------------------------------
# Folding
# --------------------------------------------------------------------------

# Separators that mean "and then" in an address: commas, dashes, pipes,
# newlines, and the various Unicode dashes couriers paste in from spreadsheets.
_SEPARATORS = re.compile(r"[,;|/\\\n\r\t]+|[‐-―−]+|--+")
# Everything that is not a letter, digit, space or a character we keep.
_NOISE = re.compile(r"[^\w\sऀ-ॿ#&.'-]+")
_MULTISPACE = re.compile(r"\s+")
# A run of dots inside a word ("h.no") is meaningful; a trailing one is not.
_TRAILING_PUNCT = re.compile(r"(?<=\w)[.']+(?=\s|$)")


def fold(text: str) -> str:
    """Canonical case, punctuation and spacing.

    NFKC rather than NFC: it also folds the full-width and compatibility forms
    that arrive from copy-paste, so a full-width "110015" becomes ASCII
    "110015" instead of a pincode the regex will never see.
    """
    text = unicodedata.normalize("NFKC", text)
    text = _SEPARATORS.sub(" ", text)
    text = _NOISE.sub(" ", text)
    text = _TRAILING_PUNCT.sub("", text)
    text = _MULTISPACE.sub(" ", text)
    return text.strip().lower()


@dataclass(frozen=True)
class Normalised:
    """The output of S0, carrying both script forms.

    `text` is what S1's regexes and the cache key run on. `retrieval_text` is
    what goes to BM25 and to the embedding model -- it contains the original and
    the transliterated form together, so a query in either script matches an
    index built from the other.
    """

    original: str
    text: str
    retrieval_text: str
    scripts: frozenset[str]
    cache_key: str

    @property
    def is_multi_script(self) -> bool:
        """Mixed scripts in one address: an escalation trigger for S3."""
        return len({s for s in self.scripts if s != "other_indic"}) > 1

    @property
    def has_unsupported_script(self) -> bool:
        """True for scripts we detect but do not transliterate."""
        return "other_indic" in self.scripts


def normalise(raw: str) -> Normalised:
    """Run S0.

    The cache key is the sha256 of the *fully normalised* text, which is the
    point: "H.No 14, Ramesh Nagar" and "h no 14 ramesh nagar" hash to the same
    value and the second one costs a single DynamoDB read instead of a model
    call. Near-duplicate addresses are the norm in any real city corpus, so this
    is the largest cost lever in the system.
    """
    folded = fold(raw)

    scripts = detect_scripts(folded)
    if "devanagari" in scripts:
        latin = fold(transliterate_devanagari(folded))
        # Keep both: the Devanagari for an index built from Devanagari aliases,
        # the transliteration for one built from Latin.
        retrieval_source = f"{folded} {latin}"
        # Regexes for pincodes and house numbers work on the Latin form.
        text = expand_abbreviations(latin)
    else:
        retrieval_source = folded
        text = expand_abbreviations(folded)

    text = _MULTISPACE.sub(" ", text).strip()
    retrieval_text = _MULTISPACE.sub(
        " ", expand_abbreviations(retrieval_source)
    ).strip()

    return Normalised(
        original=raw,
        text=text,
        retrieval_text=retrieval_text,
        scripts=frozenset(scripts),
        cache_key=cache_key(text),
    )


def cache_key(normalised_text: str) -> str:
    """sha256 of normalised text. Stable across processes and deploys."""
    return hashlib.sha256(normalised_text.encode("utf-8")).hexdigest()
