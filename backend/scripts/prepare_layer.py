"""Copy the data files into the layer before `sam build`.

The gazetteer (`pincodes.csv`, `localities.csv.gz`) and the abbreviation table
live in `backend/data/`, which is the source of truth for scripts and tests. A
Lambda layer only ships what is under `layers/common/`, so this copies them to
`layers/common/python/patasetu/data/`, where `config.DATA_DIR` finds them at
`/opt/python/patasetu/data` with no environment variable.

Idempotent and cheap (~2 MB). The destination is gitignored. Run it in the
deploy step, before `sam build`:

    python -m scripts.prepare_layer && sam build && sam deploy
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "data"
DST = ROOT / "layers" / "common" / "python" / "patasetu" / "data"
FILES = ("pincodes.csv", "localities.csv.gz", "abbreviations.json")
# Shipped when it exists; the runtime falls back to an identity calibrator.
OPTIONAL = ("calibration.json",)


def main() -> int:
    missing = [f for f in FILES if not (SRC / f).exists()]
    if missing:
        print(f"error: missing in {SRC}: {missing}", file=sys.stderr)
        print(
            "Run: python -m scripts.build_gazetteer --src data/pincode_raw.csv",
            file=sys.stderr,
        )
        return 1
    DST.mkdir(parents=True, exist_ok=True)
    for name in FILES + OPTIONAL:
        if name in OPTIONAL and not (SRC / name).exists():
            print(f"  {name:<20} (absent; runtime stays uncalibrated)")
            continue
        shutil.copy2(SRC / name, DST / name)
        print(f"  {name:<20} -> {DST.relative_to(ROOT)}")
    print("layer data ready")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
