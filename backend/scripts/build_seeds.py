"""Build the seed address set from real, publicly-listed Indian addresses.

The seeds are the root of everything measurable in this project: `eval/corpus_gen.py`
perturbs them into the ~2,000-address corpus, and the hand-labelled gold set is
drawn from that. So they have to be *real* address text attached to *real*
coordinates -- a synthetic seed with an invented coordinate would make the
median-geocode-error metric meaningless.

Source: the All-India Pincode Directory (India Post, via data.gov.in). Every
record is a publicly listed government post office with a real name, real
locality, real district, real state, real pincode, and a real latitude and
longitude. Three properties make it the right source here:

*   **It is real, not generated.** Real Indian locality names, real spelling
    conventions, real pincode-locality pairings.
*   **It carries ground truth.** Each office has a published coordinate, so the
    gold set gets a true point to measure geocode error against. Addresses
    scraped off the open web would give us text but no trustworthy coordinate.
*   **It contains no personal data.** These are institutional addresses on the
    public record. Requirement NFR-19 and the privacy limitation in the README
    are satisfied by construction rather than by a scrubbing pass we have to
    trust.

The trade-off, stated here so it reaches `docs/learnings.md` and the README
limitations section rather than being quietly forgotten: post office addresses
are *better formed* than the messy landmark-based addresses PataSetu exists to
handle. The seeds are therefore clean canonical addresses by design, and all of
the mess -- dropped pincodes, misspelled landmarks, transliteration, merged
lines, appended chatter -- is introduced by `corpus_gen.py`, which knows what it
perturbed and can label it. Real-world *messiness* is modelled; real-world
*address content* is genuine.

Landmarks are real too: for a given pincode, the other named delivery offices in
that same pincode are real named places with real coordinates, so the landmark
graph can be warmed from the directory instead of from invented points.

Usage:
    python -m scripts.build_seeds --n 120
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, NamedTuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "layers/common/python"))

from patasetu import gazetteer
from patasetu.digipin import encode as digipin_encode

# Districts in the directory are administrative, not what anyone writes on a
# parcel. "Bengaluru Urban" is a district; "Bengaluru" is the city. Delhi's
# directory districts are bare compass words ("West", "South East") which are
# useless as a city name on their own.

# The nine Delhi directory districts. Everything inside them is written as
# "New Delhi" or "Delhi" by actual humans.
_DELHI_DISTRICTS = frozenset(
    {
        "Central Delhi",
        "East Delhi",
        "New Delhi",
        "North Delhi",
        "North East Delhi",
        "North West Delhi",
        "Shahdara",
        "South Delhi",
        "South East Delhi",
        "South West Delhi",
        "West Delhi",
        "Central",
        "East",
        "New Delhi Gpo",
        "North",
        "North East",
        "North West",
        "South",
        "South East",
        "South West",
        "West",
    }
)

# Cities we want represented, so the corpus is not 90% one metro. The eight
# largest metros carry the landmark-addressing problem most visibly; the tier-2
# and smaller entries keep the gazetteer honest about the rest of the country.
_TARGET_CITIES: tuple[str, ...] = (
    "New Delhi",
    "Mumbai",
    "Bengaluru",
    "Hyderabad",
    "Chennai",
    "Kolkata",
    "Pune",
    "Ahmedabad",
    "Jaipur",
    "Lucknow",
    "Kochi",
    "Guwahati",
    "Patna",
    "Bhopal",
    "Indore",
    "Nagpur",
    "Coimbatore",
    "Varanasi",
    "Chandigarh",
    "Noida",
    "Gurugram",
    "Visakhapatnam",
    "Bhubaneswar",
    "Thiruvananthapuram",
)

_EARTH_R_M = 6_371_000.0

# Minimum decimal places required in a source coordinate for it to serve as
# ground truth. 97.9% of directory records carry 6 decimal places (~0.1 m), but
# a few hundred are rounded to 2 or 3 (~1.1 km / ~110 m). Those would bake an
# error larger than our entire 150 m geocode-error target into the gold set's
# own answer key, so they are excluded from seeds. The gazetteer centroid still
# uses them: it is a median over many offices, where a few coarse values do no
# harm.
_MIN_COORD_DECIMALS = 4


def coord_decimals(raw: str) -> int:
    """Number of decimal places written in a coordinate string."""
    raw = (raw or "").strip()
    return len(raw.partition(".")[2]) if "." in raw else 0


def is_placeholder_coord(lat: float, lng: float) -> bool:
    """True if the coordinate looks manually rounded rather than surveyed.

    Counting *written* decimals is not enough: the directory stores some
    placeholder values at full width, so "25.250000" passes a format check while
    carrying ~1.1 km of error. A genuine surveyed coordinate landing exactly on
    a 0.01-degree multiple in *both* axes is a roughly 1-in-10,000 coincidence,
    so we treat it as entered by hand.
    """
    return round(lat, 2) == lat and round(lng, 2) == lng


def state_bounds(
    offices: list[Office],
) -> dict[str, tuple[float, float, float, float]]:
    """Robust lat/lng box per state, from the 1st-99th percentile of its offices.

    Needed because the directory contains genuine georeferencing errors: pincode
    683545 is in Kerala but its office is plotted at 15.59N 76.19E, which is in
    Karnataka, about 700 km away. Such a record would enter the gold set as an
    answer key pointing at the wrong state, and every model would be scored
    wrong for getting it right.

    Percentiles rather than min/max, so the outliers we are hunting cannot
    themselves widen the box that is supposed to exclude them.
    """
    by_state: dict[str, list[Office]] = defaultdict(list)
    for o in offices:
        by_state[o.state].append(o)

    bounds: dict[str, tuple[float, float, float, float]] = {}
    for state, group in by_state.items():
        if len(group) < 20:
            continue  # too few points for a percentile to mean anything
        lats = sorted(o.lat for o in group)
        lngs = sorted(o.lng for o in group)

        def pct(values: list[float], q: float) -> float:
            return values[min(len(values) - 1, int(q * (len(values) - 1)))]

        # Pad by half a degree so legitimate border offices are not rejected.
        bounds[state] = (
            pct(lats, 0.01) - 0.5,
            pct(lngs, 0.01) - 0.5,
            pct(lats, 0.99) + 0.5,
            pct(lngs, 0.99) + 0.5,
        )
    return bounds


def in_state_bounds(
    office: Office, bounds: dict[str, tuple[float, float, float, float]]
) -> bool:
    box = bounds.get(office.state)
    if box is None:
        return True  # no basis to reject
    min_lat, min_lng, max_lat, max_lng = box
    return min_lat <= office.lat <= max_lat and min_lng <= office.lng <= max_lng


def drop_district_outliers(offices: list[Office]) -> tuple[list[Office], int]:
    """Remove offices implausibly far from the centre of their own district.

    The state-level box is too coarse to catch intra-state errors: an office
    labelled Indore district but plotted at 81.07E is 540 km east of Indore and
    still comfortably inside Madhya Pradesh.

    The threshold is scaled to each district's own spread rather than fixed,
    because Indian districts differ in size by two orders of magnitude -- a 60 km
    cutoff that suits Indore would reject most of Kachchh or Leh. We compare
    against the median distance from the district's median point, which is
    resistant to the very outliers being removed.
    """
    by_district: dict[tuple[str, str], list[Office]] = defaultdict(list)
    for o in offices:
        by_district[(o.state, o.district)].append(o)

    keep: list[Office] = []
    dropped = 0
    for group in by_district.values():
        if len(group) < 10:
            keep.extend(group)  # too few points to judge
            continue
        import statistics

        c_lat = statistics.median(o.lat for o in group)
        c_lng = statistics.median(o.lng for o in group)
        dists = [haversine_m(c_lat, c_lng, o.lat, o.lng) for o in group]
        typical = statistics.median(dists)
        # Floor at 25 km so a very compact district does not reject its own
        # legitimate edges; multiplier at 5x so a sprawling one is left alone.
        limit = max(25_000.0, 5.0 * typical)
        for o, d in zip(group, dists, strict=True):
            if d <= limit:
                keep.append(o)
            else:
                dropped += 1
    return keep, dropped


def normalise_name(name: str) -> str:
    """Fix the directory's inconsistent casing.

    About 1,700 office names are stored in full capitals ("CHINCHBUNDER"), which
    no customer writes and which would teach the retrieval layer that shouting
    is a feature of real address text.
    """
    if name.isupper() or name.islower():
        return " ".join(w.capitalize() if w.isalpha() else w for w in name.split())
    return name


class Office(NamedTuple):
    name: str
    pincode: str
    district: str
    state: str
    city: str
    lat: float
    lng: float
    office_type: str


def haversine_m(a_lat: float, a_lng: float, b_lat: float, b_lng: float) -> float:
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp = p2 - p1
    dl = math.radians(b_lng - a_lng)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * _EARTH_R_M * math.asin(math.sqrt(h))


def city_for(district: str, state: str) -> str:
    """Defers to the gazetteer, so seeds and the live pipeline agree."""
    if state == "Delhi" or district in _DELHI_DISTRICTS:
        return "New Delhi"
    return gazetteer.city_for_district(district, state) or district


def load_offices(raw_csv: Path) -> list[Office]:
    """Load delivery offices that have a usable name and coordinate."""
    from build_gazetteer import (
        clean_office_name,
        is_usable_office,
        parse_coord,
        titlecase_place,
    )

    out: list[Office] = []
    with raw_csv.open(encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            pin = (row.get("Pincode") or "").strip()
            if not is_usable_office(pin, row.get("OfficeName") or ""):
                continue
            # Non-delivery offices are administrative and often share a
            # coordinate with their parent, which would create phantom
            # duplicate landmarks in the graph.
            if (row.get("Delivery") or "").strip().lower() != "delivery":
                continue
            lat_s, lng_s = row.get("Latitude", ""), row.get("Longitude", "")
            point = parse_coord(lat_s, lng_s)
            if point is None:
                continue
            # Reject coordinates too coarse to serve as an answer key.
            if min(coord_decimals(lat_s), coord_decimals(lng_s)) < _MIN_COORD_DECIMALS:
                continue
            if is_placeholder_coord(*point):
                continue
            name = normalise_name(clean_office_name(row.get("OfficeName") or ""))
            # Names that are pure jargon or a bare number make no sense as an
            # address line.
            if len(name) < 3 or name.upper() in {"GPO", "HO", "SO", "BO"}:
                continue
            if not re.search(r"[A-Za-z]{3}", name):
                continue
            district = titlecase_place(row.get("District") or "")
            state = titlecase_place(row.get("StateName") or "")
            out.append(
                Office(
                    name=name,
                    pincode=pin,
                    district=district,
                    state=state,
                    city=city_for(district, state),
                    lat=point[0],
                    lng=point[1],
                    office_type=(row.get("OfficeType") or "").strip().upper(),
                )
            )
    return out


# Grid cell size for the spatial index, in degrees. 0.05 deg is ~5.5 km of
# latitude, so a 3x3 block of cells always contains the full 3 km search radius.
_GRID_DEG = 0.05


def build_spatial_index(offices: list[Office]) -> dict[tuple[int, int], list[Office]]:
    """Bucket offices into a coarse lat/lng grid for neighbourhood lookup.

    A linear scan of 136,000 offices per seed would be 16 million distance
    computations. Bucketing makes each lookup touch a few dozen candidates.
    """
    index: dict[tuple[int, int], list[Office]] = defaultdict(list)
    for o in offices:
        index[(int(o.lat / _GRID_DEG), int(o.lng / _GRID_DEG))].append(o)
    return index


def nearby_landmarks(
    office: Office,
    index: dict[tuple[int, int], list[Office]],
    *,
    max_m: float = 3_000.0,
    limit: int = 2,
) -> list[dict[str, Any]]:
    """Real named places within `max_m` of `office`, from anywhere in the index.

    Searches geographically rather than by pincode. Restricting candidates to
    the same pincode left three quarters of seeds with no landmark at all --
    many pincodes have exactly one delivery office -- yet a place 400 m away
    across a pincode boundary is still the landmark a customer would name.
    Pincode boundaries are a postal artefact; people navigate by what they can
    see.

    Coordinates are genuine directory values, which is what lets the landmark
    graph be warmed with real geometry rather than invented points.
    """
    cy, cx = int(office.lat / _GRID_DEG), int(office.lng / _GRID_DEG)
    candidates: list[tuple[float, Office]] = []
    # Names that merely repeat the city, district or state are useless as
    # landmarks -- "near Delhi" inside a Delhi address tells a rider nothing,
    # and it would train the graph on a non-landmark.
    seen_names: set[str] = {
        office.name.casefold(),
        office.city.casefold(),
        office.district.casefold(),
        office.state.casefold(),
    }

    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            for other in index.get((cy + dy, cx + dx), ()):
                key = other.name.casefold()
                if key in seen_names:
                    continue
                d = haversine_m(office.lat, office.lng, other.lat, other.lng)
                # Below 25 m the two records effectively share a coordinate, so
                # the second carries no independent geographic information.
                if 25.0 < d <= max_m:
                    seen_names.add(key)
                    candidates.append((d, other))

    candidates.sort(key=lambda t: t[0])
    return [
        {
            "name": other.name,
            "relation": "near",
            "true_lat": round(other.lat, 6),
            "true_lng": round(other.lng, 6),
            "distance_m": round(d, 1),
        }
        for d, other in candidates[:limit]
    ]


_SUB_LOCALITY_RE = re.compile(
    r"\b(sector|phase)\s*[:\-]?\s*([0-9]{1,3}\s*[a-z]?|[ivx]{1,4})\b", re.IGNORECASE
)


def sub_locality_of(name: str) -> str | None:
    """Extract a sector or phase designation from a real place name."""
    m = _SUB_LOCALITY_RE.search(name)
    if m is None:
        return None
    return f"{m.group(1).capitalize()} {m.group(2).upper().replace(' ', '')}"


def compose_raw(office: Office, landmarks: list[dict[str, Any]]) -> tuple[str, str]:
    """Render the canonical address text, and say which shape was used.

    Two shapes, because the pipeline has to handle both: a plain postal address
    and a landmark-anchored one. Mess is added later, by corpus_gen.
    """
    # The locality name appears once, as the head of the address. Repeating it
    # in the tail -- "Kalkaji Post Office, ..., Kalkaji, New Delhi" -- is not how
    # anyone writes an address, and it would give the locality field two chances
    # to be matched, quietly flattering every locality score in the ablation.
    tail: list[str] = []
    if office.city and office.city.casefold() != office.name.casefold():
        tail.append(office.city)
    if office.state and office.state.casefold() != (office.city or "").casefold():
        tail.append(office.state)

    head = f"{office.name} Post Office"
    if landmarks:
        shape = "landmark"
        segments = [head, f"near {landmarks[0]['name']}", *tail]
    else:
        shape = "postal"
        segments = [head, *tail]

    raw = ", ".join(dict.fromkeys(segments)) + f" - {office.pincode}"
    return raw, shape


def build(
    offices: list[Office], n: int, seed: int
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    rng = random.Random(seed)

    # Drop offices whose coordinate contradicts their own stated state.
    bounds = state_bounds(offices)
    kept = [o for o in offices if in_state_bounds(o, bounds)]
    n_misplaced = len(offices) - len(kept)
    if n_misplaced:
        print(f"dropped {n_misplaced:,} offices geocoded outside their own state")

    offices, n_district = drop_district_outliers(kept)
    if n_district:
        print(f"dropped {n_district:,} offices far from their own district centre")

    index = build_spatial_index(offices)

    by_city: dict[str, list[Office]] = defaultdict(list)
    for o in offices:
        by_city[o.city].append(o)

    # Round-robin across the target cities so no single metro dominates, then
    # top up from the rest of the country.
    picked: list[Office] = []
    seen: set[tuple[str, str]] = set()
    pools = {c: by_city.get(c, [])[:] for c in _TARGET_CITIES}
    for pool in pools.values():
        rng.shuffle(pool)

    cursor = dict.fromkeys(_TARGET_CITIES, 0)
    while len(picked) < n:
        progressed = False
        for city in _TARGET_CITIES:
            if len(picked) >= n:
                break
            pool, i = pools[city], cursor[city]
            while i < len(pool):
                cand = pool[i]
                i += 1
                key = (cand.pincode, cand.name)
                if key in seen:
                    continue
                # One seed per pincode keeps localities from being over-sampled,
                # which would flatter the learning curve.
                if any(p.pincode == cand.pincode for p in picked):
                    continue
                seen.add(key)
                picked.append(cand)
                progressed = True
                break
            cursor[city] = i
        if not progressed:
            break

    if len(picked) < n:
        # Top up from anywhere, still one seed per pincode.
        rest = [o for o in offices if all(p.pincode != o.pincode for p in picked)]
        rng.shuffle(rest)
        picked.extend(rest[: n - len(picked)])

    seeds: list[dict[str, Any]] = []
    for i, office in enumerate(sorted(picked, key=lambda o: o.pincode), start=1):
        landmarks = nearby_landmarks(office, index)
        raw, shape = compose_raw(office, landmarks)
        lat_r, lng_r = round(office.lat, 6), round(office.lng, 6)
        seeds.append(
            {
                "seed_id": f"SEED#{i:04d}",
                "raw": raw,
                "shape": shape,
                "truth": {
                    "building": None,
                    "street": None,
                    # Real office names sometimes carry a sector or phase
                    # ("Rohini Sector 15"). Leaving it out of the key made a
                    # correct extraction score as an invented value.
                    "sub_locality": sub_locality_of(office.name),
                    "locality": office.name,
                    "city": office.city or None,
                    "district": office.district or None,
                    "state": office.state or None,
                    "pincode": office.pincode,
                    "landmarks": [
                        {"name": lm["name"], "relation": lm["relation"]}
                        for lm in landmarks
                    ],
                },
                # Round the coordinate FIRST, then encode from the rounded
                # value. A level-10 DIGIPIN cell is ~3.8 m across, so a point
                # sitting on a cell boundary encodes differently before and
                # after rounding to six decimals -- the stored code would then
                # not match the stored coordinate, and anything that recomputes
                # it (the gold set, the rider app computing DIGIPIN offline from
                # geo) would disagree with us by one cell.
                "geo": {"lat": lat_r, "lng": lng_r},
                "digipin": digipin_encode(lat_r, lng_r),
                "landmark_truth": landmarks,
                "provenance": {
                    "source": "india_post_all_india_pincode_directory",
                    "office_type": office.office_type,
                    "contains_personal_data": False,
                },
            }
        )

    stats = defaultdict(int)
    for s in seeds:
        stats[s["truth"]["city"] or "?"] += 1
    return seeds, dict(stats)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--src", type=Path, default=Path("data/pincode_raw.csv"))
    ap.add_argument("--out", type=Path, default=Path("eval/data/seed_addresses.jsonl"))
    ap.add_argument("--n", type=int, default=120)
    ap.add_argument("--seed", type=int, default=20260917)
    args = ap.parse_args()

    if not args.src.exists():
        print(f"error: source not found: {args.src}", file=sys.stderr)
        print("Download the All-India Pincode Directory first.", file=sys.stderr)
        return 1

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    offices = load_offices(args.src)
    print(f"loaded {len(offices):,} usable delivery offices")

    seeds, stats = build(offices, args.n, args.seed)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8", newline="\n") as fh:
        for s in seeds:
            fh.write(json.dumps(s, ensure_ascii=False) + "\n")

    n_lm = sum(1 for s in seeds if s["truth"]["landmarks"])
    print(f"wrote  {len(seeds)} seeds -> {args.out}")
    print(f"       {n_lm} carry at least one real nearby landmark")
    print(
        f"       {len({s['truth']['state'] for s in seeds})} states, "
        f"{len(stats)} cities"
    )
    for city, count in sorted(stats.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"         {count:>3}  {city}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
