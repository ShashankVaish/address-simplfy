"""The learning curve: resolution quality versus addresses seen per locality.

The claim to demonstrate: *the system gets better at your neighbourhood the
more it sees of it.* This script measures that rather than asserting it.

Procedure, which mirrors what happens in production:

1.  Start with an **empty** landmark graph.
2.  Feed the corpus **locality by locality**, address by address, in a fixed
    shuffled order.
3.  For each address, resolve it against the graph *as it stands*, and record
    whether the geocode came from the graph and how far it was from the truth.
4.  Then simulate the delivery being confirmed: the address's real landmarks
    (with their real coordinates) are written into the graph, and any landmark
    already present has its observation count incremented -- exactly what the
    learner Lambda does on a `DeliveryConfirmed` event.
5.  Plot the running graph-hit rate and median geocode error against the number
    of addresses seen in that locality.

The x-axis is *per locality*, not global, because that is the operational
question: how many deliveries into a new neighbourhood before the system
stops needing the customer? Localities are pooled by their address count so
the curve is an average over many neighbourhoods, not one lucky one.

What this does and does not show, honestly: it shows that knowledge learned
from one address transfers to differently-worded addresses for the same
places -- the mechanism. It does not show real delivery confirmations, which
do not exist yet; the "confirmation" here uses the corpus's own ground truth.

    python -m eval.learning_curve
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "layers/common/python"))

from scripts.warm_landmarks import build_records

from patasetu import confidence as conf
from patasetu import gazetteer
from patasetu.config import load as load_config
from patasetu.models import GeoSource, LandmarkRecord
from patasetu.pipeline import Stack, resolve
from patasetu.providers import Providers

# Bucket boundaries for "addresses seen so far in this locality". Log-ish
# spacing, because the interesting change happens in the first handful.
BUCKETS: tuple[tuple[int, int], ...] = (
    (0, 0),
    (1, 1),
    (2, 3),
    (4, 7),
    (8, 15),
    (16, 999),
)


def bucket_label(n: int) -> str:
    for lo, hi in BUCKETS:
        if lo <= n <= hi:
            return f"{lo}" if lo == hi else f"{lo}-{hi}"
    return f"{BUCKETS[-1][0]}+"


def confirm_delivery(engine: Any, seed_landmarks: list[LandmarkRecord]) -> None:
    """What the learner does when a rider taps Delivered.

    New landmarks are inserted; known ones have their observation count
    bumped and confidence nudged. Coordinates are not moved here because the
    corpus's truth *is* the coordinate; in production the learner applies an
    EMA nudge toward the confirmed point (see functions/learner).
    """
    for record in seed_landmarks:
        existing = engine.get(record.landmark_id)
        if existing is None:
            engine.upsert([record])
        else:
            existing.observation_count += 1
            existing.confidence = min(0.95, existing.confidence + 0.05)
            engine.upsert([existing])


def run(
    rows: list[dict[str, Any]], seeds: list[dict[str, Any]], *, stack: Stack, seed: int
) -> dict[str, Any]:
    cfg = load_config()
    providers = Providers(cfg)
    engine = providers.search  # empty in-memory index
    identity = conf.Calibrator(None)

    # The landmarks each seed would contribute on confirmation, with real
    # coordinates and embeddings, built once.
    all_records = build_records(seeds, None, with_observations=False)
    from scripts.warm_landmarks import embed_records, landmark_id

    embed_records(all_records, providers)
    by_id = {r.landmark_id: r for r in all_records}
    seed_by_id = {s["seed_id"]: s for s in seeds}

    def landmarks_for(seed_id: str) -> list[LandmarkRecord]:
        s = seed_by_id[seed_id]
        t = s["truth"]
        ids = [
            landmark_id(
                t["locality"], s["geo"]["lat"], s["geo"]["lng"], t["state"] or ""
            )
        ]
        ids += [
            landmark_id(lm["name"], lm["true_lat"], lm["true_lng"], t["state"] or "")
            for lm in s.get("landmark_truth", [])
        ]
        return [by_id[i].model_copy(deep=True) for i in ids if i in by_id]

    rng = random.Random(seed)
    order = rows[:]
    rng.shuffle(order)

    seen_per_locality: dict[str, int] = defaultdict(int)
    samples: dict[str, list[tuple[bool, float | None]]] = defaultdict(list)

    for row in order:
        locality = row["truth"]["locality"] or row["seed_id"]
        n_seen = seen_per_locality[locality]

        result = resolve(
            row["raw"], stack=stack, cfg=cfg, providers=providers, calibrator=identity
        )
        from_graph = (
            result.geo is not None and result.geo.source is GeoSource.LANDMARK_GRAPH
        )
        error = (
            gazetteer.haversine_m(
                result.geo.lat, result.geo.lng, row["geo"]["lat"], row["geo"]["lng"]
            )
            if result.geo
            else None
        )
        samples[bucket_label(n_seen)].append((from_graph, error))

        confirm_delivery(engine, landmarks_for(row["seed_id"]))
        seen_per_locality[locality] += 1

    points = []
    for lo, hi in BUCKETS:
        label = f"{lo}" if lo == hi else f"{lo}-{hi}"
        bucket = samples.get(label, [])
        if not bucket:
            continue
        errors = [e for _, e in bucket if e is not None]
        points.append(
            {
                "seen": label,
                "n": len(bucket),
                "graph_hit_rate": sum(1 for g, _ in bucket if g) / len(bucket),
                "geo_median_m": statistics.median(errors) if errors else None,
                "geo_coverage": len(errors) / len(bucket),
            }
        )
    return {
        "stack": stack.value,
        "n_addresses": len(rows),
        "n_localities": len(seen_per_locality),
        "points": points,
    }


def svg_curve(points: list[dict[str, Any]], *, title: str, out: Path) -> None:
    w, h, pad_l, pad_r, pad_t, pad_b = 640, 400, 64, 64, 56, 56
    plot_w, plot_h = w - pad_l - pad_r, h - pad_t - pad_b
    n = len(points)
    xs = [pad_l + (i + 0.5) * plot_w / max(1, n) for i in range(n)]

    def sy_rate(v: float) -> float:
        return pad_t + plot_h - v * plot_h

    errs = [p["geo_median_m"] for p in points if p["geo_median_m"] is not None]
    err_max = max(errs) if errs else 1.0

    def sy_err(v: float) -> float:
        return pad_t + plot_h - (v / err_max) * plot_h

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" font-family="Inter, system-ui, sans-serif" font-size="12">',
        f'<rect width="{w}" height="{h}" fill="#fff"/>',
        f'<text x="{w / 2}" y="26" text-anchor="middle" font-size="15" font-weight="600">{title}</text>',
        f'<text x="{w / 2}" y="44" text-anchor="middle" fill="#666">green: share geocoded from the landmark graph (left axis) · orange: median geocode error, m (right axis)</text>',
        f'<line x1="{pad_l}" y1="{pad_t + plot_h}" x2="{pad_l + plot_w}" y2="{pad_t + plot_h}" stroke="#333"/>',
        f'<line x1="{pad_l}" y1="{pad_t}" x2="{pad_l}" y2="{pad_t + plot_h}" stroke="#1f7a4d"/>',
        f'<line x1="{pad_l + plot_w}" y1="{pad_t}" x2="{pad_l + plot_w}" y2="{pad_t + plot_h}" stroke="#e87d1e"/>',
    ]
    for t in (0.0, 0.25, 0.5, 0.75, 1.0):
        parts.append(
            f'<text x="{pad_l - 8}" y="{sy_rate(t) + 4}" text-anchor="end" fill="#1f7a4d">{t:.0%}</text>'
        )
        parts.append(
            f'<text x="{pad_l + plot_w + 8}" y="{sy_rate(t) + 4}" text-anchor="start" fill="#e87d1e">{err_max * t:,.0f}</text>'
        )
    for x, p in zip(xs, points, strict=True):
        parts.append(
            f'<text x="{x}" y="{pad_t + plot_h + 18}" text-anchor="middle">{p["seen"]}</text>'
        )
        parts.append(
            f'<text x="{x}" y="{pad_t + plot_h + 34}" text-anchor="middle" fill="#888">n={p["n"]}</text>'
        )
    parts.append(
        f'<text x="{pad_l + plot_w / 2}" y="{h - 8}" text-anchor="middle">addresses already seen in this locality</text>'
    )

    def polyline(values: list[float | None], sy, colour: str) -> None:
        pts = [(x, sy(v)) for x, v in zip(xs, values, strict=True) if v is not None]
        if len(pts) > 1:
            parts.append(
                f'<polyline fill="none" stroke="{colour}" stroke-width="2.5" points="{" ".join(f"{x:.1f},{y:.1f}" for x, y in pts)}"/>'
            )
        for x, y in pts:
            parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" fill="{colour}"/>')

    polyline([p["graph_hit_rate"] for p in points], sy_rate, "#1f7a4d")
    polyline([p["geo_median_m"] for p in points], sy_err, "#e87d1e")
    parts.append("</svg>")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(parts), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--corpus", type=Path, default=Path("eval/data/corpus.jsonl"))
    ap.add_argument(
        "--seeds", type=Path, default=Path("eval/data/seed_addresses.jsonl")
    )
    ap.add_argument(
        "--stack",
        default="R2",
        choices=["R1", "R2"],
        help="retrieval stacks only; no model needed",
    )
    ap.add_argument(
        "--limit", type=int, default=600, help="addresses to feed (runtime)"
    )
    ap.add_argument("--seed", type=int, default=20260919)
    ap.add_argument("--out", type=Path, default=Path("eval/data/learning_curve.json"))
    ap.add_argument(
        "--svg", type=Path, default=Path("../docs/results/learning_curve.svg")
    )
    args = ap.parse_args()

    for p in (args.corpus, args.seeds):
        if not p.exists():
            print(f"error: {p} not found", file=sys.stderr)
            return 1
    rows = [json.loads(line) for line in args.corpus.open(encoding="utf-8")][
        : args.limit
    ]
    seeds = [json.loads(line) for line in args.seeds.open(encoding="utf-8")]

    gazetteer.warm()
    result = run(rows, seeds, stack=Stack(args.stack), seed=args.seed)

    print(
        f"stack {result['stack']} · {result['n_addresses']} addresses over {result['n_localities']} localities"
    )
    print(f"  {'seen':<7} {'n':>5}  {'graph hit':>9}  {'geo median':>10}")
    for p in result["points"]:
        med = f"{p['geo_median_m']:,.0f} m" if p["geo_median_m"] is not None else "--"
        print(f"  {p['seen']:<7} {p['n']:>5}  {p['graph_hit_rate']:>9.1%}  {med:>10}")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    svg_curve(
        result["points"],
        title=f"Learning curve — stack {result['stack']}, {result['n_addresses']} addresses, {result['n_localities']} localities",
        out=args.svg,
    )
    print(f"wrote {args.out} and {args.svg}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
