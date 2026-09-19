"""Create the `landmarks` index in OpenSearch Serverless, and optionally load it.

The mapping comes from `patasetu.search.index_mapping`, the same function the
engine uses, so the script and the code cannot disagree about the embedding
dimension -- the one mismatch that breaks kNN silently rather than loudly.

In local mode there is nothing to create: the in-process engine needs no
mapping, and this script says so and exits 0, so a CI job can call it
unconditionally.

Usage:
    python -m scripts.create_index                       # create only
    python -m scripts.create_index --load                # create + bulk-load
    python -m scripts.create_index --load --warm         # load the warmed graph
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "layers/common/python"))

from patasetu.config import load as load_config
from patasetu.providers import Providers, ProviderUnavailable
from scripts.warm_landmarks import load_landmarks


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--load", action="store_true", help="bulk-load landmarks.jsonl")
    ap.add_argument(
        "--warm", action="store_true", help="load landmarks_warm.jsonl instead"
    )
    ap.add_argument("--data-dir", type=Path, default=Path("eval/data"))
    ap.add_argument("--batch", type=int, default=100)
    args = ap.parse_args()

    cfg = load_config()
    if cfg.is_local:
        print("PROVIDER=local: the in-process engine needs no index. Nothing to do.")
        return 0

    providers = Providers(cfg)
    try:
        engine = providers.search
        engine.ensure_index()
    except ProviderUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"index '{cfg.landmark_index}' ready at {cfg.opensearch_endpoint}")

    if not args.load:
        return 0

    path = args.data_dir / ("landmarks_warm.jsonl" if args.warm else "landmarks.jsonl")
    if not path.exists():
        print(
            f"error: {path} not found; run scripts.warm_landmarks first",
            file=sys.stderr,
        )
        return 1

    records = load_landmarks(path)
    # Batched, and progress is printed, because a bulk load of thousands of
    # 1024-float vectors is the one slow step in the whole setup.
    total = 0
    for start in range(0, len(records), args.batch):
        chunk = records[start : start + args.batch]
        total += engine.upsert(chunk)
        print(f"  upserted {total:,}/{len(records):,}")
    print(f"done: {engine.count():,} landmarks in the index")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
