"""Walk the gold set and verify each label by hand.

The answer keys are *derived* from the India Post directory record each address
was generated from, not typed in by a person. That is verifiable rather than
guessed -- the key comes from the same public record as the address text -- but
it is not the same thing as a hand-labelled set, and it inherits any error in
the source directory.

This tool is the human pass. It shows one address at a time with its derived
truth and records whether a person confirmed, rejected or corrected it. Four
people labelling 75 rows each goes quickly, and it does something the numbers
cannot: it calibrates everyone's idea of what "correct" means, before the
arguments start on Saturday.

`label_status` starts as "derived" and becomes "verified", "wrong" or
"corrected". The counts are reported alongside the metrics so the provenance of
every published number travels with it.

Keys:  y confirm   n mark wrong   e edit a field   s skip   q save and quit

    python -m eval.label_tool --split dev
    python -m eval.label_tool --split dev --start 75 --limit 75
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

FIELDS: tuple[str, ...] = (
    "building",
    "street",
    "sub_locality",
    "locality",
    "city",
    "district",
    "state",
    "pincode",
)


def show(row: dict[str, Any], position: int, total: int) -> None:
    print()
    print("=" * 72)
    print(
        f"[{position}/{total}]  {row['address_id']}   status={row.get('label_status')}"
    )
    print(f"  raw          {row['raw']}")
    if row.get("perturbations"):
        print(f"  perturbed by {', '.join(row['perturbations'])}")
    print("  derived truth")
    for name in FIELDS:
        value = row["truth"].get(name)
        print(f"    {name:<13} {value if value is not None else '-'}")

    landmarks = row["truth"].get("landmarks") or []
    if landmarks:
        rendered = ", ".join(
            f"{lm.get('relation', 'near')} {lm.get('name')}" for lm in landmarks
        )
        print(f"    landmarks     {rendered}")

    geo = row.get("geo") or {}
    print(f"    geo           {geo.get('lat')}, {geo.get('lng')}")
    print(f"    digipin       {row.get('digipin')}")


def save(rows: list[dict[str, Any]], path: Path) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    counts = Counter(r.get("label_status", "derived") for r in rows)
    print(f"\nsaved {path}")
    for status, count in sorted(counts.items()):
        print(f"  {status:<10} {count}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--split", choices=("dev", "test"), default="dev")
    ap.add_argument("--data-dir", type=Path, default=Path("eval/data"))
    ap.add_argument("--start", type=int, default=0, help="resume at this index")
    ap.add_argument("--limit", type=int, default=None, help="label at most N rows")
    args = ap.parse_args()

    path = args.data_dir / f"gold_{args.split}.jsonl"
    if not path.exists():
        print(f"error: {path} not found. Run scripts.make_gold first.", file=sys.stderr)
        return 1

    rows = [json.loads(line) for line in path.open(encoding="utf-8")]
    end = len(rows) if args.limit is None else min(len(rows), args.start + args.limit)

    if args.split == "test":
        print("Note: you are labelling the TEST split. Labelling is fine;")
        print("evaluating against it more than once is not (FR-40).")

    for i in range(args.start, end):
        row = rows[i]
        show(row, i + 1, len(rows))
        try:
            choice = (
                input("\n  [y]es / [n]o / [e]dit / [s]kip / [q]uit > ").strip().lower()
            )
        except (EOFError, KeyboardInterrupt):
            save(rows, path)
            return 0

        if choice == "q":
            save(rows, path)
            return 0
        if choice == "s":
            continue
        if choice == "y":
            row["label_status"] = "verified"
        elif choice == "n":
            row["label_status"] = "wrong"
            row["label_note"] = input("  what is wrong with it? > ").strip()
        elif choice == "e":
            field = input(f"  which field {FIELDS} > ").strip()
            if field not in FIELDS:
                print("  unknown field, skipping")
                continue
            value = input(f"  new value for {field} (blank means null) > ").strip()
            row["truth"][field] = value or None
            row["label_status"] = "corrected"
        else:
            print("  unrecognised key, skipping")

    save(rows, path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
