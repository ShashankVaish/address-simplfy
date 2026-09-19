"""S1 -- deterministic parse. No model, no network, under a millisecond.

Extracts everything that regex and a gazetteer can settle, which on a clean
address is the whole job: those addresses leave the pipeline here having cost
nothing at all. On a messy one it still fixes the pincode, state, district,
centroid and unit numbers, so the model in S3 gets a much narrower question than
"parse this".

The phone-number handling is a hard requirement, not hygiene (NFR-25, FR-25).
Indian delivery addresses routinely carry a mobile number and "call before
coming". That number must be lifted out *here*, before anything reaches a model
or an embedding API. Stripping it downstream is too late: the text has already
been sent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Final

from patasetu import gazetteer
from patasetu.models import Relation
from patasetu.normalize import strip_chatter

# --------------------------------------------------------------------------
# Phone numbers -- removed first, before any other parsing
# --------------------------------------------------------------------------

# Indian mobile numbers are ten digits starting 6-9, optionally with a +91 or 0
# prefix and arbitrary spacing, dashes or dots. Landlines are an STD code plus
# 6-8 digits. Matched before pincode extraction so a phone's digits cannot be
# mistaken for a pincode.
_PHONE_RE: Final = re.compile(
    r"""
    (?<!\d)
    (?:
        (?:\+?91[\s.\-]?|0)?        # optional country or trunk prefix
        [6-9]\d{9}                  # ten-digit mobile
      | 0\d{2,4}[\s.\-]?\d{6,8}     # STD code + landline
    )
    (?!\d)
    """,
    re.VERBOSE,
)
# Digits split by separators: "98765 43210", "98765-43210".
_SPACED_PHONE_RE: Final = re.compile(
    r"(?<!\d)(?:\+?91[\s.\-]?)?[6-9]\d{4}[\s.\-]\d{5}(?!\d)"
)

# --------------------------------------------------------------------------
# Unit numbers
# --------------------------------------------------------------------------

# Order matters: the more specific label wins, so "flat" is tried before the
# bare-number fallback. `expand_abbreviations` in S0 has already rewritten
# "h.no" to "house number", so only the long forms need matching here.
_BUILDING_PATTERNS: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    (
        "flat",
        re.compile(
            r"\bflat\s*(?:number\s*)?[:\-]?\s*([0-9]{1,4}\s*[a-z]?(?:\s*[/-]\s*[0-9a-z]{1,4})?)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "house",
        re.compile(
            r"\bhouse\s*number\s*[:\-]?\s*([0-9]{1,5}\s*[a-z]?(?:\s*[/-]\s*[0-9a-z]{1,4})?)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "door",
        re.compile(r"\bdoor\s*number\s*[:\-]?\s*([0-9a-z/\-]{1,10})\b", re.IGNORECASE),
    ),
    (
        "plot",
        re.compile(r"\bplot\s*number\s*[:\-]?\s*([0-9a-z/\-]{1,10})\b", re.IGNORECASE),
    ),
    (
        "shop",
        re.compile(r"\bshop\s*[:\-]?\s*([0-9]{1,4}\s*[a-z]?)\b", re.IGNORECASE),
    ),
    (
        "room",
        re.compile(r"\broom\s*[:\-]?\s*([0-9]{1,4}\s*[a-z]?)\b", re.IGNORECASE),
    ),
    (
        "survey",
        re.compile(
            r"\bsurvey\s*number\s*[:\-]?\s*([0-9a-z/\-]{1,12})\b", re.IGNORECASE
        ),
    ),
)

_FLOOR_RE: Final = re.compile(
    r"\b(?:"
    r"([0-9]{1,2})(?:st|nd|rd|th)?\s*floor"
    r"|(ground|first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)\s*floor"
    r"|floor\s*[:\-]?\s*([0-9]{1,2})"
    r")\b",
    re.IGNORECASE,
)
_BLOCK_RE: Final = re.compile(
    r"\bblock\s*[:\-]?\s*([a-z0-9]{1,3})\b|\b([a-z])\s*block\b", re.IGNORECASE
)
_SECTOR_RE: Final = re.compile(
    r"\bsector\s*[:\-]?\s*([0-9]{1,3}\s*[a-z]?)\b", re.IGNORECASE
)
_PHASE_RE: Final = re.compile(r"\bphase\s*[:\-]?\s*([0-9ivx]{1,4})\b", re.IGNORECASE)
_TOWER_RE: Final = re.compile(r"\btower\s*[:\-]?\s*([a-z0-9]{1,3})\b", re.IGNORECASE)

# --------------------------------------------------------------------------
# Landmark relations
# --------------------------------------------------------------------------

# The relation word is the information. "Behind Shiv Mandir" and "opposite Shiv
# Mandir" are different doorsteps, and a landmark match that ignores the
# preposition collapses them into one.
_RELATION_WORDS: Final[dict[str, Relation]] = {
    "behind": Relation.BEHIND,
    "back of": Relation.BEHIND,
    "peeche": Relation.BEHIND,
    "pichhe": Relation.BEHIND,
    "opposite": Relation.OPPOSITE,
    "in front of": Relation.OPPOSITE,
    "saamne": Relation.OPPOSITE,
    "samne": Relation.OPPOSITE,
    "beside": Relation.BESIDE,
    "next to": Relation.BESIDE,
    "adjacent": Relation.BESIDE,
    "adjacent to": Relation.BESIDE,
    "paas": Relation.NEAR,
    "above": Relation.ABOVE,
    "over": Relation.ABOVE,
    "upar": Relation.ABOVE,
    "inside": Relation.INSIDE,
    "within": Relation.INSIDE,
    "andar": Relation.INSIDE,
    "near": Relation.NEAR,
    "nearby": Relation.NEAR,
    "close to": Relation.NEAR,
}

# Tokens that terminate a landmark phrase: another relation word, a field
# separator, or a locality suffix that belongs to the address rather than the
# landmark.
_LANDMARK_STOP: Final = re.compile(
    r"\b(?:"
    + "|".join(re.escape(w) for w in sorted(_RELATION_WORDS, key=len, reverse=True))
    + r"|post office|post ophis|post ofis|police station|district|tehsil|taluka|pincode|pin"
    + r"|house number|flat|block|sector|phase|floor|tower"
    + r")\b",
    re.IGNORECASE,
)

_RELATION_RE: Final = re.compile(
    r"(?<!\w)("
    + "|".join(re.escape(w) for w in sorted(_RELATION_WORDS, key=len, reverse=True))
    + r")(?!\w)\s+",
    re.IGNORECASE,
)

# Hindi and Hinglish postpositions: the relation word comes *after* the landmark.
# These are how landmark addresses are actually written and spoken across most of
# north India, transliterated by S0 into these forms.
_POSTPOSITIONS: Final[dict[str, Relation]] = {
    "ke pichhe": Relation.BEHIND,
    "ke peeche": Relation.BEHIND,
    "ke piche": Relation.BEHIND,
    "ke samne": Relation.OPPOSITE,
    "ke saamne": Relation.OPPOSITE,
    "ke sammne": Relation.OPPOSITE,
    "ke paas": Relation.NEAR,
    "ke pas": Relation.NEAR,
    "ke nazdeek": Relation.NEAR,
    "ke bagal": Relation.BESIDE,
    "ke bagal me": Relation.BESIDE,
    "ke upar": Relation.ABOVE,
    "ke andar": Relation.INSIDE,
    "ke bilkul paas": Relation.NEAR,
}

_POSTPOSITION_RE: Final = re.compile(
    r"(?<!\w)(?P<rel>"
    + "|".join(re.escape(w) for w in sorted(_POSTPOSITIONS, key=len, reverse=True))
    + r")(?!\w)",
    re.IGNORECASE,
)

# Words that are never a landmark on their own.
_WEAK_LANDMARKS: Final[frozenset[str]] = frozenset(
    {
        "here",
        "there",
        "it",
        "this",
        "that",
        "home",
        "house",
        "my house",
        "the",
        "a",
        "an",
        "and",
        "side",
        "area",
        "road",
        "street",
        "lane",
    }
)


@dataclass
class ParseResult:
    """What S1 could establish on its own.

    `confident_fields` is the set S3 is told not to revisit. Handing the model
    the deterministic answers *and* telling it which ones are settled is what
    keeps it from "correcting" a gazetteer-validated pincode into something that
    matches the rest of the text.
    """

    building: str | None = None
    street: str | None = None
    sub_locality: str | None = None
    locality: str | None = None
    city: str | None = None
    district: str | None = None
    state: str | None = None
    pincode: str | None = None
    floor: str | None = None
    block: str | None = None
    tower: str | None = None

    landmarks: list[tuple[str, Relation]] = field(default_factory=list)

    # Extracted and quarantined. Never returned to a client, never sent to a
    # model, never embedded.
    phones: list[str] = field(default_factory=list)
    # The address text with phones and chatter removed. This is what goes to
    # retrieval and to the model.
    clean_text: str = ""

    pincode_candidates: list[str] = field(default_factory=list)
    pincode_known: bool = False
    locality_agrees: bool | None = None
    matched_locality: str | None = None
    state_conflict: bool = False
    centroid: tuple[float, float] | None = None
    confident_fields: set[str] = field(default_factory=set)
    evidence: list[str] = field(default_factory=list)

    @property
    def is_complete_enough_to_skip_model(self) -> bool:
        """True when S3 can be skipped entirely, at zero model cost.

        The bar is deliberately high: a gazetteer-validated pincode whose
        locality is corroborated by the text, a unit number, and no landmark
        that needs resolving. Anything less and the model earns its ~600 ms.
        """
        return (
            self.pincode_known
            and self.locality_agrees is True
            and not self.state_conflict
            and self.building is not None
            and not self.landmarks
        )


def strip_phones(text: str) -> tuple[str, list[str]]:
    """Remove phone numbers, returning the cleaned text and what was removed.

    Runs before pincode extraction. A ten-digit mobile contains six-digit
    substrings, and the trailing five digits of "9876543210" would otherwise be
    a candidate pincode.
    """
    found: list[str] = []

    def take(m: re.Match[str]) -> str:
        found.append(m.group(0).strip())
        return " "

    cleaned = _SPACED_PHONE_RE.sub(take, text)
    cleaned = _PHONE_RE.sub(take, cleaned)
    return re.sub(r"\s+", " ", cleaned).strip(), found


def _first_group(m: re.Match[str] | None) -> str | None:
    if m is None:
        return None
    for g in m.groups():
        if g:
            return re.sub(r"\s+", "", g).upper()
    return None


def _trim_trailing_places(name: str, place_names: frozenset[str]) -> str:
    """Strip locality, city and state names off the end of a landmark phrase.

    A relation word tells us where the landmark *starts* but nothing about where
    it ends, so "opposite water tank ramesh nagar delhi 110015" initially
    captures the rest of the address. Since the landmark is followed by the
    locality and city -- which the gazetteer already knows for this pincode --
    those can be removed from the tail.

    Trimming stops at two words, so a landmark whose own name happens to be a
    known locality survives: "near loyola college" must stay "loyola college",
    even though Loyola College is itself the registered locality of 600034.
    """
    words = name.split()
    changed = True
    while changed and len(words) > 2:
        changed = False
        # Longest suffix first, so a two-word place beats its last word alone.
        for take in (3, 2, 1):
            if len(words) - take < 2:
                continue
            suffix = " ".join(words[-take:]).casefold()
            if suffix in place_names:
                words = words[:-take]
                changed = True
                break
    return " ".join(words)


# Tokens that are never part of a landmark's *name*: a pincode, a bare number,
# and the "post office" suffix (in either script's transliteration). Stripped
# from both ends of a captured phrase. A phrase reduced to nothing by this is
# not a landmark at all.
_POSTAL_JARGON = r"post\s+(?:office|ophis|ofis|aphis|afis)|post office|po|p\.o\.?"
_PHRASE_TRIM_RE = re.compile(
    rf"^(?:\s*(?:\d{{3,}}|{_POSTAL_JARGON}))+\s*"
    rf"|\s*(?:(?:\d{{3,}}|{_POSTAL_JARGON})\s*)+$",
    re.IGNORECASE,
)


def clean_landmark_phrase(phrase: str) -> str:
    """Strip numbers and postal jargon from the ends of a landmark phrase."""
    previous = None
    while previous != phrase:
        previous = phrase
        phrase = _PHRASE_TRIM_RE.sub("", phrase).strip(" ,.-")
    return phrase


def extract_landmarks(
    text: str, place_names: frozenset[str] = frozenset()
) -> list[tuple[str, Relation]]:
    """Pull (name, relation) pairs out of the address text.

    Handles both word orders, which is not optional for Hindi input:

        English  "behind Shiv Mandir"      relation *precedes* the landmark
        Hindi    "shiv mandir ke pichhe"   relation *follows* it

    Matching only the English order on "शिव मंदिर के पीछे, रमेश नगर" captures
    everything after "pichhe" and returns "ramesh nagar" as the landmark -- the
    locality, not the landmark, with the actual landmark discarded. Since
    resolving Hindi and Latin input to the same result is a requirement (FR-09),
    the postposition form is matched explicitly.

    The result is a *candidate* name for S2 to match against the graph, not a
    verified landmark, which is why an unmatched one is still returned with a
    null id rather than dropped.
    """
    out: list[tuple[str, Relation]] = []
    seen: set[str] = set()

    def add(name: str, relation: Relation) -> None:
        name = clean_landmark_phrase(name)
        name = _trim_trailing_places(name, place_names)
        if len(name) < 3 or name.casefold() in _WEAK_LANDMARKS:
            return
        if not re.search(r"[a-zऀ-ॿ]{3}", name, re.IGNORECASE):
            return
        key = name.casefold()
        if key in seen:
            return
        seen.add(key)
        out.append((name, relation))

    # Postpositions first, and the span they consume is removed before the
    # preposition pass, so "shiv mandir ke pichhe" is not also read as
    # "pichhe <rest of address>".
    consumed: list[tuple[int, int]] = []
    for m in _POSTPOSITION_RE.finditer(text):
        relation = _POSTPOSITIONS[m.group("rel").lower()]
        head = text[: m.start()]
        # Take the last few words before the postposition.
        words = head.split()
        # Cut at the previous relation word, stop token, or six-digit pincode:
        # a landmark name never contains a pincode, and without this cut the
        # phrase "752034 baulabanadh ke paas" captures the pincode.
        for i in range(len(words) - 1, -1, -1):
            pair = " ".join(words[i : i + 2])
            if (
                _LANDMARK_STOP.fullmatch(words[i])
                or _LANDMARK_STOP.fullmatch(pair)
                or words[i] in _RELATION_WORDS
                or re.fullmatch(r"\d{6}", words[i])
            ):
                # A two-word stop ("post office") removes both words.
                cut = i + 2 if _LANDMARK_STOP.fullmatch(pair) else i + 1
                words = words[cut:]
                break
        add(" ".join(words[-4:]), relation)
        consumed.append((m.start(), m.end()))

    masked = list(text)
    for start, end in consumed:
        for i in range(start, end):
            masked[i] = " "
    remaining = "".join(masked)

    for m in _RELATION_RE.finditer(remaining):
        relation = _RELATION_WORDS[m.group(1).lower()]
        tail = remaining[m.end() :]
        stop = _LANDMARK_STOP.search(tail)
        phrase = tail[: stop.start()] if stop else tail
        # A landmark name is rarely more than four words; beyond that we are
        # swallowing the rest of the address.
        add(" ".join(phrase.split()[:4]), relation)

    return out


def parse(text: str) -> ParseResult:
    """Run S1 over normalised text from S0."""
    result = ParseResult()

    # 1. PII first. Nothing else may see the phone number.
    without_phones, phones = strip_phones(text)
    result.phones = phones
    if phones:
        result.evidence.append(
            f"stripped {len(phones)} phone number(s) before any model or embedding call"
        )

    clean = strip_chatter(without_phones)
    result.clean_text = clean

    # 2. Pincode, and everything it implies.
    chosen, candidates = gazetteer.resolve_pincode(clean)
    result.pincode_candidates = candidates
    if chosen is not None:
        info = gazetteer.lookup(chosen)
        assert info is not None  # resolve_pincode only returns known pincodes
        result.pincode = chosen
        result.pincode_known = True
        result.district = info.district or None
        result.state = info.state or None
        result.confident_fields.update({"pincode", "district", "state"})
        if info.has_centroid:
            result.centroid = (info.lat, info.lng)  # type: ignore[arg-type]
        result.evidence.append(
            f"pincode {chosen} found in gazetteer -> "
            f"{info.locality}, {info.district}, {info.state}"
        )

        agrees, matched = gazetteer.locality_agrees(chosen, clean)
        result.locality_agrees = agrees
        result.matched_locality = matched
        if agrees:
            result.locality = matched.title() if matched else info.locality
            result.confident_fields.add("locality")
            result.evidence.append(
                f"locality '{result.locality}' in text matches pincode {chosen}"
            )
        elif agrees is False:
            result.evidence.append(
                f"no locality of pincode {chosen} appears in the text"
            )
    elif candidates:
        result.evidence.append(
            f"six-digit token(s) {candidates} present but unknown to the "
            f"gazetteer; not treated as a pincode"
        )

    # 3. State from the text, and whether it contradicts the pincode.
    stated_state = gazetteer.find_state(clean)
    if stated_state is not None:
        if result.pincode is not None and gazetteer.pincode_conflicts_with_state(
            result.pincode, stated_state
        ):
            result.state_conflict = True
            result.evidence.append(
                f"CONFLICT: text says {stated_state} but pincode "
                f"{result.pincode} is in {result.state}"
            )
            # Neither side is silently preferred (NFR-14). The pincode stays as
            # the parsed value because it is gazetteer-validated, but the
            # conflict is flagged and S6 penalises confidence for it.
            result.confident_fields.discard("state")
        elif result.state is None:
            result.state = stated_state
            result.confident_fields.add("state")

    # 4. City. The pincode's own district outranks a fuzzy text match: the
    # pincode is validated, whereas find_city is a similarity score that can be
    # confidently wrong. Only fall back to the text when there is no pincode.
    if result.district and result.state:
        result.city = gazetteer.city_for_district(result.district, result.state)
        if result.city:
            result.confident_fields.add("city")
    else:
        city = gazetteer.find_city(clean)
        if city is not None:
            result.city = city
            result.confident_fields.add("city")

    # 5. Unit numbers.
    for label, pattern in _BUILDING_PATTERNS:
        value = _first_group(pattern.search(clean))
        if value:
            result.building = value
            result.confident_fields.add("building")
            result.evidence.append(
                f"{label} number '{value}' matched deterministically"
            )
            break

    result.floor = _first_group(_FLOOR_RE.search(clean))
    result.block = _first_group(_BLOCK_RE.search(clean))
    result.tower = _first_group(_TOWER_RE.search(clean))

    sector = _first_group(_SECTOR_RE.search(clean))
    phase = _first_group(_PHASE_RE.search(clean))
    if sector:
        result.sub_locality = f"Sector {sector}"
        result.confident_fields.add("sub_locality")
    elif phase:
        result.sub_locality = f"Phase {phase}"
        result.confident_fields.add("sub_locality")

    # 6. Landmark candidates for S2. The gazetteer's names for this pincode,
    # plus the city and state, let the extractor tell where a landmark phrase
    # ends -- see `_trim_trailing_places`.
    place_names: set[str] = set()
    if result.pincode is not None:
        place_names |= set(gazetteer.localities_by_pincode().get(result.pincode, ()))
    for value in (result.city, result.district, result.state, result.locality):
        if value:
            place_names.add(value.casefold())
    place_names.add("delhi")

    result.landmarks = extract_landmarks(clean, frozenset(place_names))
    if result.landmarks:
        result.evidence.append(
            "landmark candidates: "
            + ", ".join(f"{rel.value} '{name}'" for name, rel in result.landmarks)
        )

    return result
