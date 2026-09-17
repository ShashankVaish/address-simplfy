"""DIGIPIN encoder tests -- the Day 1 gate.

The encoder is the centrepiece of the demo and it fails *silently*: a wrong
alphabet or an off-by-one row produces a well-formed code pointing at the wrong
doorstep, and nothing anywhere raises. So these tests assert against fixed
reference vectors, not against our own output.

Reference vector source: the official India Post implementation
(github.com/INDIAPOST-gov/digipin, src/digipin.js) documents
getDigiPin(13.11179621, 80.20264269) -> "4T396F42L7". Everything else here is a
structural or round-trip property that any correct implementation must satisfy.
"""

from __future__ import annotations

import math

import pytest

from patasetu.digipin import (
    DIGIPIN_ALPHABET,
    MAX_LAT,
    MAX_LON,
    MIN_LAT,
    MIN_LON,
    DigipinError,
    cell_bounds,
    decode,
    encode,
    format_digipin,
    is_valid,
    parse_digipin,
)

# The official reference vector. If this line fails, stop and fix the encoder
# before writing another line of pipeline code.
OFFICIAL_VECTOR = (13.11179621, 80.20264269, "4T396F42L7")

# Coordinates spanning the grid, used for property tests. Not ground truth for
# any particular code -- only for invariants.
SPREAD = [
    (28.6224, 77.2085),  # New Delhi
    (19.0760, 72.8777),  # Mumbai
    (12.9716, 77.5946),  # Bengaluru
    (22.5726, 88.3639),  # Kolkata
    (13.0827, 80.2707),  # Chennai
    (26.9124, 75.7873),  # Jaipur
    (34.0837, 74.7973),  # Srinagar, near the north edge
    (8.0883, 77.5385),  # Kanyakumari, near the south edge
    (27.0844, 93.6053),  # Arunachal Pradesh, near the east edge
    (23.0225, 72.5714),  # Ahmedabad
]


def test_official_reference_vector() -> None:
    lat, lng, expected = OFFICIAL_VECTOR
    assert encode(lat, lng) == expected


def test_code_is_ten_characters_with_no_separators() -> None:
    # The canonical form carries no hyphens; the official decoder rejects them.
    for lat, lng in SPREAD:
        code = encode(lat, lng)
        assert len(code) == 10, code
        assert "-" not in code
        assert set(code) <= DIGIPIN_ALPHABET, code


def test_alphabet_is_exactly_the_sixteen_official_symbols() -> None:
    assert frozenset("23456789CJKLMPFT") == DIGIPIN_ALPHABET
    # The letters absent from the alphabet are the ones humans misread.
    assert not (DIGIPIN_ALPHABET & set("AIOQSUVWXYZ01"))


@pytest.mark.parametrize(("lat", "lng"), SPREAD)
def test_round_trip_lands_inside_the_same_cell(lat: float, lng: float) -> None:
    code = encode(lat, lng)
    back = decode(code)
    # Decoding yields the cell centre, so re-encoding it must give the same code.
    assert encode(back.lat, back.lng) == code


@pytest.mark.parametrize(("lat", "lng"), SPREAD)
def test_round_trip_error_is_under_five_metres(lat: float, lng: float) -> None:
    back = decode(encode(lat, lng))
    dlat = (back.lat - lat) * 111_320
    dlng = (back.lng - lng) * 111_320 * math.cos(math.radians(lat))
    assert math.hypot(dlat, dlng) < 5.0


@pytest.mark.parametrize(("lat", "lng"), SPREAD)
def test_cell_is_roughly_four_metres_square(lat: float, lng: float) -> None:
    min_lat, min_lng, max_lat, max_lng = cell_bounds(encode(lat, lng))
    height_m = (max_lat - min_lat) * 111_320
    width_m = (max_lng - min_lng) * 111_320 * math.cos(math.radians(lat))
    assert 3.0 < height_m < 4.5, height_m
    assert 2.5 < width_m < 4.5, width_m


@pytest.mark.parametrize(("lat", "lng"), SPREAD)
def test_coordinate_lies_within_its_own_cell(lat: float, lng: float) -> None:
    min_lat, min_lng, max_lat, max_lng = cell_bounds(encode(lat, lng))
    assert min_lat <= lat <= max_lat
    assert min_lng <= lng <= max_lng


def test_nearby_points_share_a_prefix_and_distant_ones_do_not() -> None:
    # Hierarchical grid: a shared prefix must imply proximity. This is what makes
    # the DIGIPIN cell usable as a dedup and grouping key.
    a = encode(28.6224, 77.2085)
    b = encode(28.6225, 77.2086)  # ~14 m away
    far = encode(19.0760, 72.8777)  # Mumbai
    assert a[:6] == b[:6]
    assert a[:2] != far[:2]


def test_encoder_is_deterministic() -> None:
    assert encode(*SPREAD[0]) == encode(*SPREAD[0])


def test_corners_of_the_grid_encode_without_error() -> None:
    # The clamp exists for exactly these four points.
    for lat in (MIN_LAT, MAX_LAT):
        for lng in (MIN_LON, MAX_LON):
            assert len(encode(lat, lng)) == 10


@pytest.mark.parametrize(
    ("lat", "lng"),
    [
        (0.0, 77.0),  # south of the grid
        (45.0, 77.0),  # north of the grid
        (28.6, 50.0),  # west of the grid
        (28.6, 120.0),  # east of the grid
        (-28.6, -77.0),  # sign error, the classic bug
    ],
)
def test_out_of_range_coordinates_raise(lat: float, lng: float) -> None:
    with pytest.raises(DigipinError):
        encode(lat, lng)


def test_display_formatting_round_trips() -> None:
    code = encode(*SPREAD[0])
    shown = format_digipin(code)
    assert shown == f"{code[:3]}-{code[3:6]}-{code[6:]}"
    assert parse_digipin(shown) == code


def test_parser_accepts_lowercase_hyphens_and_whitespace() -> None:
    assert parse_digipin("  4t3-96f-42l7  ") == "4T396F42L7"
    assert parse_digipin("4T396F42L7") == "4T396F42L7"


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "4T396F42L",  # nine characters
        "4T396F42L77",  # eleven
        "4T396F42LA",  # 'A' is not in the alphabet
        "4T396F42L0",  # '0' is excluded to avoid confusion with 'O'
        "4T396F42LI",  # 'I' likewise
        "4T396F42L!",
    ],
)
def test_malformed_digipins_are_rejected(bad: str) -> None:
    assert is_valid(bad) is False
    with pytest.raises(DigipinError):
        parse_digipin(bad)


def test_non_string_input_is_rejected() -> None:
    for bad in (None, 42, 4.2, ["4T396F42L7"]):
        with pytest.raises(DigipinError):
            parse_digipin(bad)  # type: ignore[arg-type]


def test_distinct_cells_get_distinct_codes() -> None:
    # 10 levels of 4x4 = 16^10 cells; collisions across our spread would mean
    # the bound updates are collapsing.
    codes = {encode(lat, lng) for lat, lng in SPREAD}
    assert len(codes) == len(SPREAD)
