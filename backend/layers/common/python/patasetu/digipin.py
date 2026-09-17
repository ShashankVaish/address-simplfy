"""DIGIPIN encoder/decoder.

VENDORED, not hand-rolled. Ported line-for-line from the official India Post
implementation at https://github.com/INDIAPOST-gov/digipin (`src/digipin.js`,
Department of Posts / CEPT, revision dated 2026-05-04).

Do not "improve" the arithmetic below. Two properties of the official code look
like bugs and are not:

1.  The row index is reversed (`3 - floor(...)`) because grid row 0 is the
    *northernmost* band, while latitude increases northward.
2.  In the longitude bound update, `max_lon` is derived from the *already
    updated* `min_lon`, whereas both latitude bounds are derived from the *old*
    `min_lat`. Reordering those four statements silently shifts every code.

Format note (matters, and contradicts some secondary sources): the canonical
DIGIPIN is a continuous 10-character string with **no separators**. Earlier
revisions of the official encoder inserted hyphens after characters 3 and 6,
and the official decoder now rejects any input containing them. We therefore
store and transmit the continuous form and treat `XXX-XXX-XXXX` as a
presentation concern only -- see `format_digipin` / `parse_digipin`.
"""

from __future__ import annotations

import math
from typing import Final, NamedTuple

__all__ = [
    "DIGIPIN_ALPHABET",
    "DigipinError",
    "LatLng",
    "cell_bounds",
    "decode",
    "encode",
    "format_digipin",
    "is_valid",
    "parse_digipin",
]

# The 4x4 symbol grid. Row 0 is the northernmost band, column 0 the westernmost.
DIGIPIN_GRID: Final[tuple[tuple[str, ...], ...]] = (
    ("F", "C", "9", "8"),
    ("J", "3", "2", "7"),
    ("K", "4", "5", "6"),
    ("L", "M", "P", "T"),
)

# Bounding box of the national grid (EPSG:4326). Covers the landmass and the
# maritime EEZ, and is aligned to Survey of India toposheets.
MIN_LAT: Final[float] = 2.5
MAX_LAT: Final[float] = 38.5
MIN_LON: Final[float] = 63.5
MAX_LON: Final[float] = 99.5

LEVELS: Final[int] = 10

DIGIPIN_ALPHABET: Final[frozenset[str]] = frozenset("23456789CJKLMPFT")

# Reverse lookup, built once: symbol -> (row, col).
_SYMBOL_POS: Final[dict[str, tuple[int, int]]] = {
    sym: (r, c) for r, row in enumerate(DIGIPIN_GRID) for c, sym in enumerate(row)
}


class DigipinError(ValueError):
    """Raised for out-of-range coordinates or a malformed DIGIPIN."""


class LatLng(NamedTuple):
    lat: float
    lng: float


def encode(lat: float, lng: float) -> str:
    """Encode a coordinate into a continuous 10-character DIGIPIN.

    Pure function: no network, no state, microseconds. Safe to run inside a
    Lambda on the hot path and on a rider's phone with no connectivity.

    >>> encode(13.11179621, 80.20264269)
    '4T396F42L7'
    """
    if not (MIN_LAT <= lat <= MAX_LAT):
        raise DigipinError(f"latitude {lat} out of range [{MIN_LAT}, {MAX_LAT}]")
    if not (MIN_LON <= lng <= MAX_LON):
        raise DigipinError(f"longitude {lng} out of range [{MIN_LON}, {MAX_LON}]")

    min_lat, max_lat = MIN_LAT, MAX_LAT
    min_lon, max_lon = MIN_LON, MAX_LON
    out: list[str] = []

    for _ in range(LEVELS):
        lat_div = (max_lat - min_lat) / 4
        lon_div = (max_lon - min_lon) / 4

        # Reversed row: grid row 0 is the northernmost band.
        row = 3 - math.floor((lat - min_lat) / lat_div)
        col = math.floor((lng - min_lon) / lon_div)

        # Clamp guards the exact-boundary case (e.g. lat == max_lat), which
        # would otherwise index row -1 / col 4.
        row = max(0, min(row, 3))
        col = max(0, min(col, 3))

        out.append(DIGIPIN_GRID[row][col])

        # Latitude: both bounds derive from the OLD min_lat.
        max_lat = min_lat + lat_div * (4 - row)
        min_lat = min_lat + lat_div * (3 - row)
        # Longitude: max derives from the NEW min_lon. Order is load-bearing.
        min_lon = min_lon + lon_div * col
        max_lon = min_lon + lon_div

    return "".join(out)


def cell_bounds(digipin: str) -> tuple[float, float, float, float]:
    """Return the (min_lat, min_lng, max_lat, max_lng) box of a DIGIPIN cell."""
    pin = parse_digipin(digipin)

    min_lat, max_lat = MIN_LAT, MAX_LAT
    min_lon, max_lon = MIN_LON, MAX_LON

    for char in pin:
        row, col = _SYMBOL_POS[char]
        lat_div = (max_lat - min_lat) / 4
        lon_div = (max_lon - min_lon) / 4
        # Decode mirrors encode's reversed rows, measuring down from max_lat.
        min_lat, max_lat = (
            max_lat - lat_div * (row + 1),
            max_lat - lat_div * row,
        )
        min_lon, max_lon = (
            min_lon + lon_div * col,
            min_lon + lon_div * (col + 1),
        )

    return min_lat, min_lon, max_lat, max_lon


def decode(digipin: str) -> LatLng:
    """Decode a DIGIPIN to the centre of its cell.

    Accepts the canonical continuous form and, leniently, the hyphenated
    display form -- users paste what they were shown.

    Returns the *centre* of the ~3.8 m cell, which is within a few metres of
    whatever coordinate produced the code -- encoding is lossy by design.

    >>> decode("4T396F42L7")
    LatLng(lat=13.11178, lng=80.202635)
    """
    min_lat, min_lon, max_lat, max_lon = cell_bounds(digipin)
    return LatLng(
        lat=round((min_lat + max_lat) / 2, 6),
        lng=round((min_lon + max_lon) / 2, 6),
    )


def parse_digipin(digipin: str) -> str:
    """Normalise user input to the canonical continuous 10-character form.

    Tolerates lowercase, surrounding whitespace, and the hyphenated display
    form. Rejects anything else -- a DIGIPIN that is one character wrong points
    at a different doorstep, so silent coercion is not acceptable here.
    """
    if not isinstance(digipin, str):
        raise DigipinError("DIGIPIN must be a string")

    pin = digipin.strip().upper().replace("-", "").replace(" ", "")

    if len(pin) != LEVELS:
        raise DigipinError(
            f"DIGIPIN must be {LEVELS} characters, got {len(pin)}: {digipin!r}"
        )
    bad = sorted(set(pin) - DIGIPIN_ALPHABET)
    if bad:
        raise DigipinError(
            f"invalid DIGIPIN character(s) {bad} in {digipin!r}; "
            f"allowed: {''.join(sorted(DIGIPIN_ALPHABET))}"
        )
    return pin


def is_valid(digipin: str) -> bool:
    """True if `digipin` parses. Never raises."""
    try:
        parse_digipin(digipin)
    except DigipinError:
        return False
    return True


def format_digipin(digipin: str) -> str:
    """Hyphenate for display only: 'XXXXXXXXXX' -> 'XXX-XXX-XXXX'.

    Never store or transmit this form -- the official decoder rejects hyphens.
    """
    pin = parse_digipin(digipin)
    return f"{pin[0:3]}-{pin[3:6]}-{pin[6:10]}"
