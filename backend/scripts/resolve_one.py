"""Run the pipeline on a single address. No cloud, no deploy, no waiting.

This is the tightest feedback loop in the project and it is worth keeping fast:
every parser change is verified here before it reaches a Lambda.

    python -m scripts.resolve_one "h no 14 behind shiv mandir ramesh nagar delhi 110015"
    python -m scripts.resolve_one --json "..."      # machine-readable
    python -m scripts.resolve_one --stack A "..."   # pick an ablation stack
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "layers/common/python"))

from patasetu import gazetteer
from patasetu.digipin import format_digipin
from patasetu.pipeline import Stack, StageUnavailable, resolve


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("address", help="raw address text")
    ap.add_argument("--json", action="store_true", help="emit JSON only")
    ap.add_argument(
        "--stack",
        default="A",
        choices=[s.value for s in Stack],
        help="ablation configuration (default A: deterministic only)",
    )
    ap.add_argument("--lat", type=float, help="optional GPS hint latitude")
    ap.add_argument("--lng", type=float, help="optional GPS hint longitude")
    args = ap.parse_args()

    hint = (
        {"lat": args.lat, "lng": args.lng}
        if args.lat is not None and args.lng is not None
        else None
    )

    # Load the gazetteer before timing anything, so the reported stage latencies
    # reflect steady state rather than a one-off table load.
    gazetteer.warm()

    try:
        result = resolve(args.address, stack=Stack(args.stack), hint=hint)
    except StageUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result.model_dump(mode="json"), indent=2, ensure_ascii=False))
        return 0

    s = result.structured
    print()
    print(f"  input      {args.address}")
    print(f"  status     {result.status.value}")
    print(f"  confidence {result.confidence:.2f}")
    print()
    print("  structured")
    for name in (
        "building",
        "street",
        "sub_locality",
        "locality",
        "city",
        "district",
        "state",
        "pincode",
    ):
        value = getattr(s, name)
        shown = value if value is not None else "-"
        print(f"    {name:<13} {shown}")
    if s.landmarks:
        print("    landmarks")
        for lm in s.landmarks:
            matched = lm.matched_id or "unmatched"
            print(f"      {lm.relation.value:<9} {lm.name}  [{matched}]")
    print()
    if result.geo:
        print(
            f"  geo        {result.geo.lat:.5f}, {result.geo.lng:.5f}"
            f"  ({result.geo.source.value}, ~{result.geo.accuracy_m:.0f} m)"
        )
        print(f"  doorstep?  {'yes' if result.geo.is_doorstep_accurate else 'NO'}")
    else:
        print("  geo        -")
    if result.digipin:
        print(
            f"  digipin    {result.digipin}   (display: {format_digipin(result.digipin)})"
        )
    print()
    if result.clarification:
        c = result.clarification
        print(f"  question   [{c.language}] {c.question}")
        print(f"             (targets '{c.field}')")
        print()
    print("  evidence")
    for line in result.evidence:
        print(f"    - {line}")
    print()
    total = sum(result.timings_ms.values())
    stages = "  ".join(
        f"{k.split('_', 1)[0]}:{v:.1f}" for k, v in result.timings_ms.items()
    )
    print(f"  timings    {total:.1f} ms total   {stages}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
