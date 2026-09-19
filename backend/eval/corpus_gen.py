"""Perturb the real seeds into the evaluation corpus.

The seeds are clean, real addresses with real coordinates. Real addresses are
not clean, so this module introduces the mess -- and, crucially, *knows what it
introduced*, which is what makes the corpus labelled rather than merely large.

Every perturbation records itself in the row's `perturbations` list, so the
ablation can be sliced by failure mode. "Field F1 0.91" is a number; "field F1
0.91 overall but 0.62 when the pincode is missing" tells you what to build next.

The perturbations, each drawn from how Indian addresses actually arrive:

  drop_pincode        the single most informative token, removed
  misspell            one edit to a landmark or locality name
  abbreviate          the reverse of S0's expansion table
  transliterate       Latin -> Devanagari
  reorder             fields shuffled out of postal order
  merge_lines         all structure collapsed to one run-on line
  inject_phone        a mobile number appended (never a real one -- see below)
  append_chatter      "call before coming", "ring the bell"
  add_unit            a synthetic flat/house number, recorded in the truth
  case_noise          ALL CAPS or lowercase throughout
  drop_city           city removed, leaving pincode to carry it
  extra_whitespace    ragged spacing and stray punctuation

Phone numbers are generated from documentation-safe ranges and are explicitly
not real subscriber numbers. They exist so the corpus can prove S1 strips them.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "layers/common/python"))

from patasetu.normalize import load_abbreviations

# --------------------------------------------------------------------------
# Latin -> Devanagari, for the transliteration perturbation
# --------------------------------------------------------------------------

# Only needed to *generate* Devanagari test input; the pipeline's own direction
# is Devanagari -> Latin (see normalize.py). Longest-key-first digraphs before
# single letters, so "sh" does not become "s" + "h".
# fmt: off
_LATIN_TO_DEVA: list[tuple[str, str]] = [
    ("chh", "छ"), ("sh", "श"), ("ch", "च"), ("th", "थ"), ("dh", "ध"),
    ("ph", "फ"), ("bh", "भ"), ("gh", "घ"), ("kh", "ख"), ("jh", "झ"),
    ("aa", "ा"), ("ee", "ी"), ("oo", "ू"), ("ai", "ै"), ("au", "ौ"),
    ("k", "क"), ("g", "ग"), ("j", "ज"), ("t", "त"), ("d", "द"),
    ("n", "न"), ("p", "प"), ("b", "ब"), ("m", "म"), ("y", "य"),
    ("r", "र"), ("l", "ल"), ("v", "व"), ("w", "व"), ("s", "स"),
    ("h", "ह"), ("f", "फ"), ("z", "ज़"), ("q", "क़"), ("x", "क्स"),
    ("c", "क"),
    ("a", ""), ("i", "ि"), ("u", "ु"), ("e", "े"), ("o", "ो"),
]
# fmt: on

# A handful of place words worth mapping directly: they are extremely common and
# a syllabic transliteration of them reads as nonsense to a Hindi speaker.
# fmt: off
_KNOWN_DEVA: dict[str, str] = {
    "nagar": "नगर", "delhi": "दिल्ली", "mumbai": "मुंबई", "colony": "कॉलोनी",
    "road": "रोड", "gali": "गली", "mandir": "मंदिर", "masjid": "मस्जिद",
    "market": "मार्केट", "bazaar": "बाज़ार", "post": "पोस्ट",
    "office": "ऑफिस", "sector": "सेक्टर", "block": "ब्लॉक",
    "house": "मकान", "number": "नंबर", "floor": "मंज़िल", "new": "नई",
    "village": "गाँव", "district": "ज़िला", "state": "राज्य",
    "kolkata": "कोलकाता", "chennai": "चेन्नई", "bengaluru": "बेंगलुरु",
    "hyderabad": "हैदराबाद", "pune": "पुणे", "jaipur": "जयपुर",
    "lucknow": "लखनऊ", "patna": "पटना", "bhopal": "भोपाल",
}
# fmt: on


# English prepositions become Hindi *post*positions: "near Nairi" is
# "नैरि के पास", with the relation after the landmark. Generating the English
# word order with a Hindi word ("के पास नैरि") produces text no Hindi speaker
# would write, and it taught the extractor to capture the wrong span.
_RELATION_TO_POSTPOSITION = (
    (re.compile(r"\bnear\s+([^,]+?)(?=,|$)", re.IGNORECASE), "के पास"),
    (re.compile(r"\bbehind\s+([^,]+?)(?=,|$)", re.IGNORECASE), "के पीछे"),
    (re.compile(r"\bopposite\s+([^,]+?)(?=,|$)", re.IGNORECASE), "के सामने"),
    (re.compile(r"\bbeside\s+([^,]+?)(?=,|$)", re.IGNORECASE), "के बगल"),
)


def hindi_word_order(text: str) -> str:
    """Move relation words after their landmark, as Hindi does."""
    for pattern, postposition in _RELATION_TO_POSTPOSITION:
        # Bind the postposition explicitly: a closure over the loop variable is
        # evaluated late, and would silently use the last one if this were ever
        # deferred.
        def rewrite(m: re.Match[str], post: str = postposition) -> str:
            return f"{m.group(1).strip()} {post}"

        text = pattern.sub(rewrite, text)
    return text


def to_devanagari(text: str) -> str:
    """Approximate Latin -> Devanagari, for generating test input only."""
    text = hindi_word_order(text)
    out: list[str] = []
    for word in text.split():
        core = word.strip(".,-")
        low = core.casefold()
        if low in _KNOWN_DEVA:
            out.append(_KNOWN_DEVA[low])
            continue
        if core.isdigit():
            out.append(core)
            continue
        buf = low
        piece: list[str] = []
        while buf:
            for latin, deva in _LATIN_TO_DEVA:
                if buf.startswith(latin):
                    piece.append(deva)
                    buf = buf[len(latin) :]
                    break
            else:
                piece.append(buf[0])
                buf = buf[1:]
        out.append("".join(piece))
    return " ".join(out)


# --------------------------------------------------------------------------
# Perturbations
# --------------------------------------------------------------------------


# Reverse of S0's expansion table: pick a short form for a long one. Built from
# abbreviations.json so the two can never drift apart.
def _contraction_table() -> dict[str, list[str]]:
    table: dict[str, list[str]] = {}
    for short, long in load_abbreviations().items():
        # Skip the identity entries ("gali" -> "gali") and city renames, which
        # are normalisations rather than abbreviations.
        if short == long:
            continue
        table.setdefault(long, []).append(short)
    return table


# fmt: off
_KEYBOARD_NEIGHBOURS: dict[str, str] = {
    "a": "sq", "b": "vn", "c": "xv", "d": "sf", "e": "wr", "f": "dg",
    "g": "fh", "h": "gj", "i": "uo", "j": "hk", "k": "jl", "l": "k",
    "m": "n", "n": "bm", "o": "ip", "p": "o", "q": "wa", "r": "et",
    "s": "ad", "t": "ry", "u": "yi", "v": "cb", "w": "qe", "x": "zc",
    "y": "tu", "z": "x",
}
# fmt: on


def misspell_word(word: str, rng: random.Random) -> str:
    """One realistic edit: a neighbouring key, a doubled letter, or a drop.

    Modelled on typing errors rather than random character noise, because the
    point is to test whether fuzzy matching and the vector index recover the
    kinds of mistakes people actually make.
    """
    if len(word) < 4:
        return word
    i = rng.randrange(1, len(word) - 1)
    kind = rng.choice(("neighbour", "double", "drop", "swap"))
    if kind == "neighbour" and word[i].lower() in _KEYBOARD_NEIGHBOURS:
        return (
            word[:i] + rng.choice(_KEYBOARD_NEIGHBOURS[word[i].lower()]) + word[i + 1 :]
        )
    if kind == "double":
        return word[:i] + word[i] + word[i:]
    if kind == "drop":
        return word[:i] + word[i + 1 :]
    return word[:i] + word[i + 1] + word[i] + word[i + 2 :]


def fake_phone(rng: random.Random) -> str:
    """A syntactically valid but non-allocated Indian mobile number.

    Deliberately not a real subscriber number: the corpus is committed to the
    repository, and a plausible real number in a public test fixture is a
    privacy problem waiting to happen. These use the 9999 9xxxxx shape, which
    matches the regex S1 must catch while not belonging to anyone.
    """
    style = rng.randrange(4)
    tail = f"{rng.randrange(0, 100000):05d}"
    number = f"99999{tail}"
    if style == 0:
        return number
    if style == 1:
        return f"+91 {number[:5]} {number[5:]}"
    if style == 2:
        return f"{number[:5]}-{number[5:]}"
    return f"0{number}"


_CHATTER = (
    "call before coming",
    "please call",
    "ring the bell",
    "call krke aana",
    "leave with the guard",
    "deliver after 6 pm",
)


def add_unit_number(rng: random.Random) -> tuple[str, str]:
    """A synthetic unit number, returned as (text_fragment, truth_value).

    Invented, and labelled as such. The seeds are real institutional addresses
    with no flat numbers, but `building` is the single most important field
    operationally, so the corpus has to exercise it. Returning the truth value
    alongside the text keeps the answer key honest.
    """
    style = rng.randrange(6)
    num = rng.randrange(1, 300)
    suffix = rng.choice(("", "", "", "A", "B", "C", "D"))
    value = f"{num}{suffix}"
    if style == 0:
        return f"H.No {value}", value
    if style == 1:
        return f"Flat {value}", value
    if style == 2:
        return f"h no {value}", value
    if style == 3:
        return f"{value},", value
    if style == 4:
        return f"Plot No {value}", value
    return f"House Number {value}", value


def _split_fields(raw: str) -> list[str]:
    return [p.strip() for p in raw.split(",") if p.strip()]


def perturb(
    seed: dict[str, Any], rng: random.Random, *, variant: int
) -> dict[str, Any]:
    """Produce one corpus row from one seed.

    Variant 0 is always the unperturbed seed, so every locality has at least one
    clean example and the ablation has a ceiling to compare against.
    """
    truth = json.loads(json.dumps(seed["truth"]))  # deep copy
    raw = seed["raw"]
    applied: list[str] = []

    if variant == 0:
        return {
            "address_id": f"{seed['seed_id']}#v0",
            "seed_id": seed["seed_id"],
            "raw": raw,
            "truth": truth,
            "geo": seed["geo"],
            "digipin": seed["digipin"],
            "perturbations": [],
            "phones_expected": [],
        }

    parts = _split_fields(raw)
    phones_expected: list[str] = []

    # --- add a unit number (before reordering, so it stays at the front) ---
    if rng.random() < 0.55:
        fragment, value = add_unit_number(rng)
        parts.insert(0, fragment.rstrip(","))
        truth["building"] = value
        applied.append("add_unit")

    # --- drop the pincode ---
    if rng.random() < 0.30:
        pin = truth.get("pincode")
        if pin:
            parts = [re.sub(rf"\b{pin}\b", "", p).strip(" -,") for p in parts]
            parts = [p for p in parts if p]
            # The truth keeps the pincode: the correct answer is still the
            # correct answer. This measures whether the system can recover a
            # field the input does not contain, which is the whole point of the
            # landmark graph.
            applied.append("drop_pincode")

    # --- drop the city ---
    if rng.random() < 0.20 and truth.get("city"):
        city = truth["city"]
        parts = [p for p in parts if p.casefold() != city.casefold()]
        applied.append("drop_city")

    # --- misspell a name ---
    if rng.random() < 0.35:
        idx = [i for i, p in enumerate(parts) if len(p.split()) <= 4 and len(p) > 5]
        if idx:
            i = rng.choice(idx)
            words = parts[i].split()
            j = rng.randrange(len(words))
            words[j] = misspell_word(words[j], rng)
            parts[i] = " ".join(words)
            applied.append("misspell")

    # --- abbreviate ---
    if rng.random() < 0.40:
        contractions = _contraction_table()
        text = ", ".join(parts)
        for long, shorts in contractions.items():
            if len(long) < 4:
                continue
            if (
                re.search(rf"(?<!\w){re.escape(long)}(?!\w)", text, re.IGNORECASE)
                and rng.random() < 0.6
            ):
                text = re.sub(
                    rf"(?<!\w){re.escape(long)}(?!\w)",
                    rng.choice(shorts),
                    text,
                    count=1,
                    flags=re.IGNORECASE,
                )
                if "abbreviate" not in applied:
                    applied.append("abbreviate")
        parts = _split_fields(text)

    # --- reorder ---
    if rng.random() < 0.25 and len(parts) > 2:
        head = parts[0]
        tail = parts[1:]
        rng.shuffle(tail)
        parts = [head, *tail]
        applied.append("reorder")

    # --- inject a phone number ---
    if rng.random() < 0.30:
        phone = fake_phone(rng)
        parts.append(phone)
        phones_expected.append(phone)
        applied.append("inject_phone")

    # --- append chatter ---
    if rng.random() < 0.25:
        parts.append(rng.choice(_CHATTER))
        applied.append("append_chatter")

    text = ", ".join(parts)

    # --- transliterate to Devanagari ---
    if rng.random() < 0.15:
        # The pincode and any injected phone stay in Latin digits, as they do in
        # real mixed-script addresses.
        text = to_devanagari(text)
        applied.append("transliterate")

    # --- merge lines: collapse all structure ---
    if rng.random() < 0.30:
        text = text.replace(",", " ")
        applied.append("merge_lines")

    # --- case noise ---
    if rng.random() < 0.20:
        text = text.upper() if rng.random() < 0.5 else text.lower()
        applied.append("case_noise")

    # --- ragged whitespace and stray punctuation ---
    if rng.random() < 0.25:
        text = re.sub(r"\s", lambda _: " " * rng.randint(1, 3), text)
        text = text.replace(",", rng.choice((",", " ,", ",,", " -")))
        applied.append("extra_whitespace")

    return {
        "address_id": f"{seed['seed_id']}#v{variant}",
        "seed_id": seed["seed_id"],
        "raw": text.strip(),
        "truth": truth,
        "geo": seed["geo"],
        "digipin": seed["digipin"],
        "perturbations": applied,
        "phones_expected": phones_expected,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--seeds", type=Path, default=Path("eval/data/seed_addresses.jsonl")
    )
    ap.add_argument("--out", type=Path, default=Path("eval/data/corpus.jsonl"))
    ap.add_argument("--n", type=int, default=2000, help="target corpus size")
    ap.add_argument("--seed", type=int, default=20260917)
    args = ap.parse_args()

    if not args.seeds.exists():
        print(f"error: seeds not found: {args.seeds}", file=sys.stderr)
        print("Run: python -m scripts.build_seeds", file=sys.stderr)
        return 1

    seeds = [json.loads(line) for line in args.seeds.open(encoding="utf-8")]
    if not seeds:
        print("error: no seeds", file=sys.stderr)
        return 1

    rng = random.Random(args.seed)
    per_seed = max(1, round(args.n / len(seeds)))

    rows: list[dict[str, Any]] = []
    for s in seeds:
        for v in range(per_seed):
            rows.append(perturb(s, rng, variant=v))

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    counts = Counter(p for row in rows for p in row["perturbations"])
    clean = sum(1 for r in rows if not r["perturbations"])

    print(f"wrote {len(rows):,} addresses from {len(seeds)} seeds -> {args.out}")
    print(f"  {clean:,} unperturbed ({clean / len(rows):.1%})")
    print("  perturbation frequencies:")
    for name, count in counts.most_common():
        print(f"    {name:<18} {count:>5}  {count / len(rows):>6.1%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
