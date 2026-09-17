"""The pincode and place gazetteer that S1 parses against.

Built by `scripts/build_gazetteer.py` from the All-India Pincode Directory:
19,300 pincodes with a locality name, district, state and median centroid, plus
a reverse index of all 156,000 delivery-office names.

Why this module is load-bearing rather than a lookup convenience: a valid
pincode is the single most informative token in an Indian address. It pins
locality, district and state, and it hands S2 a centroid to constrain retrieval
to a 3 km circle instead of the whole country. It is also the only cheap way to
*detect a contradiction* -- when the text says one locality and the pincode says
somewhere 40 km away, one of them is wrong, and S6 must be told rather than
allowed to average them.

Loaded once per Lambda container and cached. The pincode table is ~1.2 MB and
the locality index ~0.9 MB gzipped, both small enough to ship in the layer.
"""

from __future__ import annotations

import csv
import functools
import gzip
import math
import os
import re
from collections import defaultdict
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Final

from patasetu.config import DATA_DIR

_EARTH_R_M: Final = 6_371_000.0

PINCODE_RE: Final = re.compile(r"\b([1-9]\d{5})\b")


@dataclass(frozen=True, slots=True)
class PincodeInfo:
    pincode: str
    locality: str
    district: str
    state: str
    lat: float | None
    lng: float | None
    office_count: int

    @property
    def has_centroid(self) -> bool:
        return self.lat is not None and self.lng is not None


def haversine_m(a_lat: float, a_lng: float, b_lat: float, b_lng: float) -> float:
    """Great-circle distance in metres."""
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp = p2 - p1
    dl = math.radians(b_lng - a_lng)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * _EARTH_R_M * math.asin(math.sqrt(h))


# The 28 states and 8 union territories, with the spellings and abbreviations
# that actually turn up in address text. Renamed states keep their old names as
# aliases, because customers and courier databases both lag official renames by
# years.
_STATE_ALIASES: Final[dict[str, str]] = {
    "andhra pradesh": "Andhra Pradesh",
    "arunachal pradesh": "Arunachal Pradesh",
    "assam": "Assam",
    "bihar": "Bihar",
    "chhattisgarh": "Chhattisgarh",
    "chattisgarh": "Chhattisgarh",
    "goa": "Goa",
    "gujarat": "Gujarat",
    "haryana": "Haryana",
    "himachal pradesh": "Himachal Pradesh",
    "jharkhand": "Jharkhand",
    "karnataka": "Karnataka",
    "kerala": "Kerala",
    "madhya pradesh": "Madhya Pradesh",
    "maharashtra": "Maharashtra",
    "manipur": "Manipur",
    "meghalaya": "Meghalaya",
    "mizoram": "Mizoram",
    "nagaland": "Nagaland",
    "odisha": "Odisha",
    "orissa": "Odisha",
    "punjab": "Punjab",
    "rajasthan": "Rajasthan",
    "sikkim": "Sikkim",
    "tamil nadu": "Tamil Nadu",
    "tamilnadu": "Tamil Nadu",
    "telangana": "Telangana",
    "tripura": "Tripura",
    "uttar pradesh": "Uttar Pradesh",
    "uttarakhand": "Uttarakhand",
    "uttaranchal": "Uttarakhand",
    "west bengal": "West Bengal",
    "andaman and nicobar islands": "Andaman & Nicobar Islands",
    "chandigarh": "Chandigarh",
    "dadra and nagar haveli": "Dadra & Nagar Haveli and Daman & Diu",
    "daman and diu": "Dadra & Nagar Haveli and Daman & Diu",
    "delhi": "Delhi",
    "new delhi": "Delhi",
    "nct of delhi": "Delhi",
    "jammu and kashmir": "Jammu & Kashmir",
    "jammu kashmir": "Jammu & Kashmir",
    "ladakh": "Ladakh",
    "lakshadweep": "Lakshadweep",
    "puducherry": "Puducherry",
    "pondicherry": "Puducherry",
}


@functools.lru_cache(maxsize=1)
def pincodes() -> dict[str, PincodeInfo]:
    """pincode -> PincodeInfo, loaded once."""
    path = os.path.join(DATA_DIR, "pincodes.csv")
    out: dict[str, PincodeInfo] = {}
    with open(path, encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            lat = float(row["lat"]) if row["lat"] else None
            lng = float(row["lng"]) if row["lng"] else None
            out[row["pincode"]] = PincodeInfo(
                pincode=row["pincode"],
                locality=row["locality"],
                district=row["district"],
                state=row["state"],
                lat=lat,
                lng=lng,
                office_count=int(row["office_count"] or 0),
            )
    return out


@functools.lru_cache(maxsize=1)
def localities_by_pincode() -> dict[str, frozenset[str]]:
    """pincode -> every delivery-office name in it, case-folded.

    This is the alias set that makes pincode-locality agreement work. The
    primary name in `pincodes.csv` is one office out of up to several dozen, so
    matching only against it would call a correct address a conflict: pincode
    110027's primary name is "Janta Market", but a customer writing "Rajouri
    Garden" is equally right, and that name is in here.
    """
    path = os.path.join(DATA_DIR, "localities.csv.gz")
    acc: dict[str, set[str]] = defaultdict(set)
    with gzip.open(path, "rt", encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            acc[row["pincode"]].add(row["locality"].casefold())
    return {k: frozenset(v) for k, v in acc.items()}


@functools.lru_cache(maxsize=1)
def _known_cities() -> dict[str, str]:
    """Case-folded district name -> canonical district name.

    Districts double as the city for every metro in the directory, which is what
    an address line actually names.
    """
    return {info.district.casefold(): info.district for info in pincodes().values()}


def lookup(pincode: str) -> PincodeInfo | None:
    return pincodes().get(pincode)


def find_pincodes(text: str) -> list[str]:
    """Every six-digit token that could be a pincode, in order of appearance.

    Returns all of them rather than the first: addresses pasted from a form
    sometimes carry a phone extension or an old pincode alongside the real one,
    and S1 decides between them by which ones the gazetteer actually knows.
    """
    return PINCODE_RE.findall(text)


def resolve_pincode(text: str) -> tuple[str | None, list[str]]:
    """Pick the pincode from free text, preferring one the gazetteer knows.

    Returns (chosen, all_candidates). An unknown six-digit number is not
    silently accepted as a pincode -- being wrong here propagates a wrong
    centroid, a wrong state and a wrong geo constraint through every later stage.
    """
    candidates = find_pincodes(text)
    known = [p for p in candidates if p in pincodes()]
    if known:
        # Last known occurrence: Indian addresses put the pincode at the end,
        # and a leading six-digit run is more often a house or phone fragment.
        return known[-1], candidates
    return None, candidates


def find_state(text: str) -> str | None:
    """Longest-match a state or union territory name in the text."""
    folded = f" {text.casefold()} "
    best: str | None = None
    best_len = 0
    for alias, canonical in _STATE_ALIASES.items():
        if f" {alias} " in folded and len(alias) > best_len:
            best, best_len = canonical, len(alias)
    return best


# Structural words that appear in a large share of Indian locality names. They
# must never be fuzzy-matched against a district name: "nagar" scores 0.909
# against the district "Nagaur", which is high enough to pass any sane cutoff and
# would assign a Delhi address to Rajasthan. Every one of these is a suffix, not
# a place.
_NOT_A_CITY: Final[frozenset[str]] = frozenset(
    {
        "nagar",
        "nagara",
        "colony",
        "vihar",
        "puram",
        "garden",
        "gardens",
        "market",
        "bazaar",
        "extension",
        "enclave",
        "layout",
        "sector",
        "phase",
        "block",
        "road",
        "street",
        "lane",
        "cross",
        "main",
        "marg",
        "chowk",
        "pura",
        "pur",
        "ganj",
        "bagh",
        "kunj",
        "dham",
        "palya",
        "halli",
        "wadi",
        "wada",
        "pada",
        "peth",
        "para",
        "gali",
        "society",
        "apartments",
        "building",
        "tower",
        "house",
        "number",
        "floor",
        "post",
        "office",
        "police",
        "station",
        "village",
        "district",
        "north",
        "south",
        "east",
        "west",
        "central",
        "new",
        "old",
        "upper",
        "lower",
        "mandir",
        "masjid",
        "church",
        "temple",
        "school",
        "college",
        "hospital",
        "railway",
        "junction",
        "industrial",
        "estate",
        "area",
    }
)

# Directory districts that are useless as a city name on their own. Delhi's
# districts are bare compass words, so "West" would be returned as the city for
# a Ramesh Nagar address.
_DELHI_COMPASS_DISTRICTS: Final[frozenset[str]] = frozenset(
    {
        "central",
        "east",
        "new delhi",
        "north",
        "north east",
        "north west",
        "south",
        "south east",
        "south west",
        "west",
        "shahdara",
    }
)


# Administrative district -> the city a human actually writes. "Bengaluru Urban"
# is a district; nobody puts it on a parcel.
_CITY_BY_DISTRICT: Final[dict[str, str]] = {
    "Bengaluru Urban": "Bengaluru",
    "Bengaluru Rural": "Bengaluru",
    "Mumbai": "Mumbai",
    "Mumbai Suburban": "Mumbai",
    "Thane": "Thane",
    "Hyderabad": "Hyderabad",
    "Medchal Malkajgiri": "Hyderabad",
    "Rangareddy": "Hyderabad",
    "Chennai": "Chennai",
    "Kolkata": "Kolkata",
    "Pune": "Pune",
    "Ahmedabad": "Ahmedabad",
    "Ahmadabad": "Ahmedabad",
    "Jaipur": "Jaipur",
    "Lucknow": "Lucknow",
    "Ernakulam": "Kochi",
    "Patna": "Patna",
    "Kamrup": "Guwahati",
    "Kamrup Metropolitan": "Guwahati",
    "Bhopal": "Bhopal",
    "Indore": "Indore",
    "Nagpur": "Nagpur",
    "Surat": "Surat",
    "Coimbatore": "Coimbatore",
    "Chandigarh": "Chandigarh",
    "Gurgaon": "Gurugram",
    "Gautam Buddha Nagar": "Noida",
    "Ghaziabad": "Ghaziabad",
    "Visakhapatnam": "Visakhapatnam",
    "Vishakhapatnam": "Visakhapatnam",
    "Bhubaneswar": "Bhubaneswar",
    "Khordha": "Bhubaneswar",
    "Varanasi": "Varanasi",
    "Amritsar": "Amritsar",
    "Ludhiana": "Ludhiana",
    "Dehradun": "Dehradun",
    "Ranchi": "Ranchi",
    "Raipur": "Raipur",
    "Mysuru": "Mysuru",
    "Madurai": "Madurai",
    "Nashik": "Nashik",
    "Vadodara": "Vadodara",
    "Rajkot": "Rajkot",
    "Kanpur Nagar": "Kanpur",
    "Agra": "Agra",
    "Jodhpur": "Jodhpur",
    "Thiruvananthapuram": "Thiruvananthapuram",
    "Kozhikode": "Kozhikode",
    "Jalandhar": "Jalandhar",
    "Srinagar": "Srinagar",
    "Jammu": "Jammu",
    "Shimla": "Shimla",
    "Imphal West": "Imphal",
    "Shillong": "Shillong",
    "East Khasi Hills": "Shillong",
    "Aizawl": "Aizawl",
    "Gangtok": "Gangtok",
    "East Sikkim": "Gangtok",
    "Panaji": "Panaji",
    "North Goa": "Panaji",
    "Puducherry": "Puducherry",
}


def city_for_district(district: str, state: str) -> str | None:
    """The city a human would write for a given directory district."""
    if not district:
        return None
    if state.casefold() == "delhi" or district.casefold() in _DELHI_COMPASS_DISTRICTS:
        return "New Delhi"
    return _CITY_BY_DISTRICT.get(district, district)


def find_city(text: str, *, cutoff: float = 0.90) -> str | None:
    """Find a district/city name, tolerating a misspelling.

    Exact match first. Fuzzy matching is a fallback only, and it is deliberately
    hard to trigger: tokens shorter than five characters and any token in
    `_NOT_A_CITY` are excluded, and the cutoff is high. An over-eager city match
    is worse than none at all, because S1 marks the field confident and S3 is
    then told not to revisit it.
    """
    folded = text.casefold()
    cities = _known_cities()

    # Exact, longest first, so "north goa" beats "goa".
    for name in sorted(cities, key=len, reverse=True):
        if len(name) < 4 or name in _DELHI_COMPASS_DISTRICTS:
            continue
        if re.search(rf"(?<!\w){re.escape(name)}(?!\w)", folded):
            return cities[name]

    tokens = [t for t in re.findall(r"[a-z]{5,}", folded) if t not in _NOT_A_CITY]
    for token in reversed(tokens):  # city sits late in an Indian address
        match, score = _closest(token, cities.keys())
        if match is not None and score >= cutoff and match not in _NOT_A_CITY:
            return cities[match]
    return None


def _closest(token: str, candidates: object) -> tuple[str | None, float]:
    """Best ratio match for `token`. Plain stdlib, no fuzzy-match dependency."""
    best: str | None = None
    best_score = 0.0
    for cand in candidates:  # type: ignore[union-attr]
        # Cheap length gate before the O(n*m) comparison.
        if abs(len(cand) - len(token)) > 2:
            continue
        score = SequenceMatcher(None, token, cand).ratio()
        if score > best_score:
            best, best_score = cand, score
    return best, best_score


def locality_agrees(pincode: str, text: str) -> tuple[bool | None, str | None]:
    """Does any locality name for `pincode` appear in `text`?

    Returns (agrees, matched_name):

      (True, name)   a locality of this pincode is named in the text
      (False, None)  the pincode is known, has names, and none of them appear
      (None, None)   no basis to judge

    False is *not* proof of a conflict -- plenty of correct addresses name only
    a building and a landmark. It is a signal S6 weighs, which is why the third
    state exists instead of defaulting to False.
    """
    names = localities_by_pincode().get(pincode)
    if not names:
        return None, None

    folded = text.casefold()
    hit: str | None = None
    for name in sorted(names, key=len, reverse=True):
        if len(name) < 4:
            continue
        if re.search(rf"(?<!\w){re.escape(name)}(?!\w)", folded):
            hit = name
            break
    if hit is not None:
        return True, hit
    return False, None


def pincode_conflicts_with_state(pincode: str, state: str | None) -> bool:
    """True when a stated state contradicts the pincode's state.

    A hard conflict: the pincode's first digit alone fixes the postal region, so
    "Bengaluru, Karnataka, 110015" cannot be reconciled. S6 penalises this
    rather than picking a side (NFR-14).
    """
    if state is None:
        return False
    info = lookup(pincode)
    if info is None or not info.state:
        return False
    return info.state.casefold() != state.casefold()


def warm() -> dict[str, int]:
    """Force the lazy tables to load, and report what was loaded.

    Both tables are read on first use, which put a ~270 ms one-off cost inside
    whichever request happened to arrive first -- and, worse, inside the S1
    timing, making the stage look 300x slower than it is. Lambda handlers and
    long-running scripts call this during initialisation so the cost lands in
    cold start where it belongs and the per-stage numbers mean something.
    """
    return {
        "pincodes": len(pincodes()),
        "locality_index": len(localities_by_pincode()),
        "districts": len(_known_cities()),
    }
