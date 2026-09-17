"""Draw the gold set from the corpus and split it into dev and test.

300 addresses, stratified so that neither split is accidentally easier than the
other: sampling is balanced across city and across *perturbation count*, because
a split that happened to collect the clean rows would show an improvement that
does not exist.

On labelling, stated plainly because it affects how every number should be read:
each row's truth is **derived** from the India Post directory record the address
was generated from, not typed in by a human. That makes it verifiable rather
than guessed -- the answer key comes from the same public record as the address
text -- but it is not the same thing as a hand-labelled set, and it inherits any
error in the source directory. `label_tool.py` exists for the team to walk the
300 rows and flip `label_status` from "derived" to "verified" or "corrected";
the count of each is reported alongside the metrics so the provenance travels
with the result.

Usage:
    python -m scripts.make_gold --n 300
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any


def stratified_split(
    rows: list[dict[str, Any]], n: int, seed: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rng = random.Random(seed)

    # Stratify on (city, how messy). Both matter: city controls locality
    # difficulty, perturbation count controls input difficulty.
    def bucket(row: dict[str, Any]) -> tuple[str, str]:
        city = row["truth"].get("city") or "?"
        k = len(row["perturbations"])
        band = "clean" if k == 0 else "light" if k <= 2 else "heavy"
        return city, band

    buckets: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        buckets[bucket(row)].append(row)
    for group in buckets.values():
        rng.shuffle(group)

    # Proportional allocation, NOT round-robin. Giving each bucket an equal turn
    # equalises the difficulty bands, which pulled the clean rows up from 7.6%
    # of the corpus to 37.5% of the gold set -- every metric measured against it
    # would then have been optimistic, and the bias would have been invisible in
    # the numbers themselves. Allocating by bucket share keeps the gold set
    # distributed like the corpus it is drawn from.
    keys = sorted(buckets)
    total = len(rows)
    quota = {k: max(1, round(n * len(buckets[k]) / total)) for k in keys}

    # At most three rows per seed address, so the gold set is not 20 variants of
    # one doorstep. With 120 seeds this also sets the ceiling at 360 rows.
    max_per_seed = 3
    per_seed: dict[str, int] = defaultdict(int)
    chosen: list[dict[str, Any]] = []

    for key in keys:
        taken = 0
        for cand in buckets[key]:
            if taken >= quota[key] or len(chosen) >= n:
                break
            if per_seed[cand["seed_id"]] >= max_per_seed:
                continue
            chosen.append(cand)
            per_seed[cand["seed_id"]] += 1
            taken += 1

    # Rounding leaves us slightly under or over; top up from the remainder in
    # corpus order, still honouring the per-seed cap.
    if len(chosen) < n:
        picked = {id(c) for c in chosen}
        pool = [r for r in rows if id(r) not in picked]
        rng.shuffle(pool)
        for cand in pool:
            if len(chosen) >= n:
                break
            if per_seed[cand["seed_id"]] >= max_per_seed:
                continue
            chosen.append(cand)
            per_seed[cand["seed_id"]] += 1
    del chosen[n:]

    # Deal alternately into dev and test, after shuffling, so the two splits are
    # drawn from the same distribution.
    rng.shuffle(chosen)
    dev = chosen[0::2]
    test = chosen[1::2]
    return dev, test


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--corpus", type=Path, default=Path("eval/data/corpus.jsonl"))
    ap.add_argument("--out-dir", type=Path, default=Path("eval/data"))
    ap.add_argument("--n", type=int, default=300)
    ap.add_argument("--seed", type=int, default=20260917)
    args = ap.parse_args()

    if not args.corpus.exists():
        print(f"error: corpus not found: {args.corpus}", file=sys.stderr)
        return 1

    rows = [json.loads(line) for line in args.corpus.open(encoding="utf-8")]
    dev, test = stratified_split(rows, args.n, args.seed)

    for name, split in (("gold_dev", dev), ("gold_test", test)):
        path = args.out_dir / f"{name}.jsonl"
        with path.open("w", encoding="utf-8", newline="\n") as fh:
            for row in split:
                row = dict(row)
                row["label_status"] = "derived"
                row["label_source"] = "india_post_all_india_pincode_directory"
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        cities = len({r["truth"].get("city") for r in split})
        heavy = sum(1 for r in split if len(r["perturbations"]) >= 3)
        clean = sum(1 for r in split if not r["perturbations"])
        print(
            f"{name:<10} {len(split):>4} rows  {cities:>3} cities  "
            f"{clean:>3} clean  {heavy:>3} heavily perturbed  -> {path}"
        )

    print()
    print("Labels are DERIVED from the source directory, not hand-typed.")
    print("Run `python -m eval.label_tool --split dev` to verify them by hand.")
    print("The test split must be evaluated exactly once, at the end (FR-40).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
