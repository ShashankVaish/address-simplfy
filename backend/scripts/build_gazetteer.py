"""Build the pincode gazetteer from the All-India Pincode Directory.

Source: the India Post pincode directory (data.gov.in), mirrored with
latitude/longitude at github.com/harshvardhaniimi/IndiaPIN. One row per post
office -- ~157,000 offices across ~19,300 pincodes.

Produces two artefacts under `backend/data/`:

  pincodes.csv    One row per pincode: the human locality name, district,
                  state, a centroid, and the office count. This is what S1
                  loads to validate a parsed pincode and to hand S2 a
                  geographic constraint.
  localities.csv  Every delivery-office name with its pincode, gzipped. This
                  is the reverse index behind the pincode-locality agreement
                  feature in S6: if the text says "BTM Layout" and the pincode
                  says Ramesh Nagar, that is a hard conflict, and we need the
                  full alias list to detect it rather than just the primary
                  name.

Two deliberate choices:

*   The centroid is a **median**, not a mean. Some pincodes carry a stray
    office geocoded to the wrong district; a mean would drag the centroid
    kilometres off and S2's 3 km geo filter would then exclude the correct
    landmarks. The median ignores it.
*   Coordinates outside the DIGIPIN national bounding box are dropped as
    corrupt rather than clamped. About 6% of source rows have missing or
    unusable coordinates; a pincode with no usable coordinate is still emitted
    (the locality name alone is useful to S1) but with an empty centroid, and
    callers must treat that as "no geographic constraint available".

Usage:
    python -m scripts.build_gazetteer --src data/pincode_raw.csv
"""

from __future__ import annotations

import argparse
import csv
import gzip
import re
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "layers/common/python"))

from patasetu.digipin import MAX_LAT, MAX_LON, MIN_LAT, MIN_LON

# Office-type tokens in the source data ("S.O", "B.O", "HO", "GPO"). These are
# postal jargon, never part of a locality name a customer would write. They
# appear in three shapes and all three have to be handled:
#
#   "Ramesh Nagar S.O"                 token at the end
#   "Subhash Nagar S.O (West Delhi)"   token before a parenthetical
#   "Aliganj SO South Delhi"           token followed by a district qualifier
#
# Anchoring the pattern at end-of-string catches only the first shape and leaves
# ~1.2% of names dirty. Those names are what the pincode-locality agreement
# feature in S6 matches address text against, and "Aliganj SO South Delhi" will
# never match the "Aliganj" a customer writes -- a silent miss, not an error. So
# cut the name at the first standalone office-type token and drop the remainder.
_OFFICE_TOKEN_RE = re.compile(r"\s*\b(?:G\.?P\.?O|[BSH]\.?O)\b\.?.*$", re.IGNORECASE)
# Parenthetical disambiguators, anywhere: "Chowk (Hathras)" -> "Chowk".
_PAREN_RE = re.compile(r"\s*\([^)]*\)")

# Head office beats sub office beats branch office as a source of the name a
# human would recognise for the pincode.
_TYPE_RANK = {"HO": 0, "GPO": 0, "SO": 1, "BO": 2}

# A valid civilian Indian pincode starts 1-8. The 9xxxxx range is reserved for
# the Army Postal Service, whose records carry contradictory geography (900056 is
# filed under Kushi Nagar, UP, with coordinates in Delhi) and which never appear
# in a courier address.
CIVILIAN_PINCODE_RE = re.compile(r"[1-8]\d{5}")

# Placeholder rows in the source directory. The official data contains one
# literal "TEST OFFICE" at pincode 999999, filed under the Tamilnadu circle with
# a Telangana district and Tamil Nadu coordinates. Importing it verbatim made
# 999999 a *valid* pincode that would validate any address containing it and
# assign a bogus centroid in Telangana -- found by a test that assumed 999999
# could not possibly resolve.
_PLACEHOLDER_NAMES = re.compile(
    r"^\s*(?:test\s*office|dummy|sample|n\.?a\.?|xxx+)\s*$", re.IGNORECASE
)


def is_usable_office(pincode: str, office_name: str) -> bool:
    """Reject placeholder and non-civilian directory rows."""
    if not CIVILIAN_PINCODE_RE.fullmatch(pincode):
        return False
    return not _PLACEHOLDER_NAMES.match(office_name or "")


def clean_office_name(raw: str) -> str:
    """Strip postal office-type jargon and disambiguators from an office name.

    Keeps the uncut name if cutting would empty it: a handful of offices are
    named literally "GPO", and reducing those to "" would discard the only
    locality label that pincode has.
    """
    name = " ".join(_PAREN_RE.sub(" ", raw.strip()).split())
    cut = _OFFICE_TOKEN_RE.sub("", name).strip(" ,-")
    return " ".join((cut or name).split()).strip(" ,-")


def titlecase_place(raw: str) -> str:
    """The source ships districts and states in SHOUTING CASE."""
    out = raw.strip().title()
    # Keep the handful of genuine acronyms upright.
    for acronym in ("Nct", "Ncr"):
        out = re.sub(rf"\b{acronym}\b", acronym.upper(), out)
    return out


def parse_coord(lat_s: str, lon_s: str) -> tuple[float, float] | None:
    """Return (lat, lng) if it is a usable Indian coordinate, else None."""
    try:
        lat, lng = float(lat_s), float(lon_s)
    except (TypeError, ValueError):
        return None
    # Reject nulls, zeros and anything outside the national grid. A coordinate
    # outside the DIGIPIN bounding box cannot be encoded anyway.
    if not (MIN_LAT <= lat <= MAX_LAT and MIN_LON <= lng <= MAX_LON):
        return None
    return lat, lng


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--src", type=Path, required=True, help="raw directory CSV")
    ap.add_argument("--out-dir", type=Path, default=Path("data"))
    args = ap.parse_args()

    if not args.src.exists():
        print(f"error: source not found: {args.src}", file=sys.stderr)
        return 1

    args.out_dir.mkdir(parents=True, exist_ok=True)

    # pincode -> aggregation state
    names: dict[str, list[tuple[int, str]]] = defaultdict(list)
    coords: dict[str, list[tuple[float, float]]] = defaultdict(list)
    place: dict[str, tuple[str, str]] = {}
    counts: dict[str, int] = defaultdict(int)
    localities: set[tuple[str, str]] = set()

    n_rows = n_bad_coord = n_rejected = 0

    with args.src.open(encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            pin = (row.get("Pincode") or "").strip()
            if not is_usable_office(pin, row.get("OfficeName") or ""):
                n_rejected += 1
                continue
            n_rows += 1
            counts[pin] += 1

            name = clean_office_name(row.get("OfficeName") or "")
            otype = (row.get("OfficeType") or "").strip().upper()
            if name:
                names[pin].append((_TYPE_RANK.get(otype, 3), name))
                localities.add((pin, name))

            if pin not in place:
                place[pin] = (
                    titlecase_place(row.get("District") or ""),
                    titlecase_place(row.get("StateName") or ""),
                )

            point = parse_coord(row.get("Latitude", ""), row.get("Longitude", ""))
            if point is None:
                n_bad_coord += 1
            else:
                coords[pin].append(point)

    pincodes_path = args.out_dir / "pincodes.csv"
    with pincodes_path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(
            ["pincode", "locality", "district", "state", "lat", "lng", "office_count"]
        )
        n_no_centroid = 0
        for pin in sorted(counts):
            # Best name: lowest office-type rank, then alphabetical for stability.
            ranked = sorted(names.get(pin) or [(3, "")])
            locality = ranked[0][1]

            pts = coords.get(pin) or []
            if pts:
                lat = f"{statistics.median(p[0] for p in pts):.6f}"
                lng = f"{statistics.median(p[1] for p in pts):.6f}"
            else:
                lat = lng = ""
                n_no_centroid += 1

            district, state = place.get(pin, ("", ""))
            w.writerow([pin, locality, district, state, lat, lng, counts[pin]])

    localities_path = args.out_dir / "localities.csv.gz"
    with gzip.open(localities_path, "wt", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["pincode", "locality"])
        for pin, name in sorted(localities):
            w.writerow([pin, name])

    print(f"read        {n_rows:>7,} post-office rows")
    print(f"pincodes    {len(counts):>7,} -> {pincodes_path}")
    print(f"  of which  {n_no_centroid:>7,} have no usable centroid")
    print(f"localities  {len(localities):>7,} -> {localities_path}")
    print(f"dropped     {n_bad_coord:>7,} unusable coordinates")
    print(f"rejected    {n_rejected:>7,} placeholder or non-civilian rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
