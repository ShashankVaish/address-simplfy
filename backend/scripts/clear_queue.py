"""Empty the review queue before a demo: reject every open case.

Every NEEDS_INFO / AMBIGUOUS order is marked REJECTED with actor "cleanup",
which appends to each order's timeline like any other operator action -- the
audit trail is kept, the queue is cleared. Nothing is deleted.

    PROVIDER=aws AWS_REGION=ap-south-1 TABLE_NAME=patasetu-dev python -m scripts.clear_queue
    python -m scripts.clear_queue --keep DEMO-23          # leave the demo row alone
    python -m scripts.clear_queue --dry-run
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "layers/common/python"))

from patasetu import store
from patasetu.config import load as load_config
from patasetu.providers import Providers

OPEN_STATUSES = ("NEEDS_INFO", "AMBIGUOUS")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--keep", nargs="*", default=[], help="order ids to leave open")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    kv = Providers(load_config()).store
    keep = set(args.keep)
    total = 0
    for status in OPEN_STATUSES:
        # Loop: a page is at most `limit` rows and rejecting removes them
        # from the index, so keep querying until the page is empty.
        while True:
            page = [
                r
                for r in kv.query_status(status, limit=100)
                if r["order_id"] not in keep
            ]
            if not page:
                break
            for row in page:
                print(
                    f"  {status:<10} {row['order_id']}  {row.get('summary', '')[:60]}"
                )
                if not args.dry_run:
                    store.record_feedback(
                        kv,
                        order_id=row["order_id"],
                        action="reject",
                        actor="cleanup",
                        edits=None,
                    )
                total += 1
            if args.dry_run:
                break
    print(f"{'would reject' if args.dry_run else 'rejected'} {total} open case(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
