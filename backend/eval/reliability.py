"""Confidence calibration: fit, diagram, threshold.

A confidence number nobody has validated is decoration. This script makes it
mean something:

1.  Run the pipeline over the **dev** split and record, per address, the *raw*
    S6 score and whether the answer was actually correct.
2.  Fit **isotonic regression** (pool-adjacent-violators) from raw score to
    observed correctness. Isotonic is the right choice on a few hundred
    labelled examples: monotonic, non-parametric, no functional form to get
    wrong, and stable where a logistic fit would be shaky.
3.  Draw the **reliability diagram** -- predicted confidence on x, observed
    accuracy on y, the diagonal drawn in. If the 0.9 bucket is right 89% of the
    time, the number means something.
4.  Choose the **threshold** that meets the precision target ("at least 95% of
    auto-resolved addresses are correct") and report the clarification rate
    that follows. The threshold is a consequence of a stated safety target,
    not a round number chosen by taste.

Output: `data/calibration.json` (loaded by `Calibrator.load()` at runtime),
`docs/results/reliability.svg`, and a printed summary. No numpy, no
matplotlib: the Lambda layer stays small and the plot is plain SVG.

In-sample caveat, stated up front: fitting on dev and drawing the diagram on
dev is optimistic. A 2-fold cross-validated ECE is printed alongside so the
optimism is visible. The test split is touched exactly once, at the end, by
`run_ablation` -- never by this script.

    python -m eval.reliability --split dev --stack R2
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "layers/common/python"))

from eval.metrics import SCORED_FIELDS, normalise_value
from eval.run_ablation import build_providers
from patasetu import confidence as conf
from patasetu import gazetteer
from patasetu.config import load as load_config
from patasetu.pipeline import Stack, StageUnavailable, resolve

# Geocode error above which an otherwise-correct address counts as wrong. The
# fields can all match while the pin sits in the wrong neighbourhood; a rider
# would call that wrong, so the label does too.
GEO_CORRECT_M = 500.0


# --- correctness label ---------------------------------------------------------


def is_correct(prediction: dict[str, Any], row: dict[str, Any]) -> bool:
    truth = row["truth"]
    populated = [f for f in SCORED_FIELDS if normalise_value(truth.get(f)) is not None]
    structured = prediction.get("structured") or {}
    if any(
        normalise_value(structured.get(f)) != normalise_value(truth.get(f))
        for f in populated
    ):
        return False
    geo = prediction.get("geo")
    if geo and row.get("geo"):
        err = gazetteer.haversine_m(
            geo["lat"], geo["lng"], row["geo"]["lat"], row["geo"]["lng"]
        )
        if err > GEO_CORRECT_M:
            return False
    return True


# --- isotonic regression (PAV) ---------------------------------------------------


def pav(xs: list[float], ys: list[float]) -> list[tuple[float, float]]:
    """Pool-adjacent-violators. Returns (x, fitted_y) knots, non-decreasing in y.

    Points are sorted by x; each block starts as one point; adjacent blocks
    whose means violate monotonicity are merged until none do. The result is
    the least-squares monotone fit. Knots are emitted as the x-range of each
    block mapped to the block mean, which the runtime interpolates between.
    """
    if not xs:
        return []
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    blocks: list[list[float]] = []  # [x_min, x_max, sum_y, n]
    for i in order:
        blocks.append([xs[i], xs[i], ys[i], 1.0])
        while (
            len(blocks) >= 2
            and blocks[-2][2] / blocks[-2][3] > blocks[-1][2] / blocks[-1][3]
        ):
            b = blocks.pop()
            a = blocks[-1]
            a[1] = b[1]
            a[2] += b[2]
            a[3] += b[3]
    knots: list[tuple[float, float]] = []
    for x_min, x_max, s, n in blocks:
        mean = s / n
        knots.append((x_min, mean))
        if x_max != x_min:
            knots.append((x_max, mean))
    # Collapse runs of knots at the same fitted value to their end points: the
    # interpolation between them is flat anyway, and 150 knots for a 3-level
    # step function is noise in the JSON.
    compact: list[tuple[float, float]] = []
    for x, y in knots:
        if len(compact) >= 2 and compact[-1][1] == y and compact[-2][1] == y:
            compact[-1] = (x, y)
        else:
            compact.append((x, y))
    return compact


def apply_knots(knots: list[tuple[float, float]], x: float) -> float:
    return conf.Calibrator(knots)(x)


# --- diagram + summary statistics --------------------------------------------------


def bin_reliability(
    scores: list[float], correct: list[bool], n_bins: int = 10
) -> list[dict[str, float]]:
    bins: list[dict[str, float]] = []
    for b in range(n_bins):
        lo, hi = b / n_bins, (b + 1) / n_bins
        idx = [
            i
            for i, s in enumerate(scores)
            if (lo <= s < hi) or (b == n_bins - 1 and s == 1.0)
        ]
        if not idx:
            bins.append(
                {"lo": lo, "hi": hi, "n": 0, "confidence": 0.0, "accuracy": 0.0}
            )
            continue
        bins.append(
            {
                "lo": lo,
                "hi": hi,
                "n": len(idx),
                "confidence": statistics.fmean(scores[i] for i in idx),
                "accuracy": statistics.fmean(1.0 if correct[i] else 0.0 for i in idx),
            }
        )
    return bins


def expected_calibration_error(bins: list[dict[str, float]], total: int) -> float:
    """Weighted mean |confidence - accuracy| across bins. Lower is better."""
    if not total:
        return 0.0
    return sum(
        b["n"] / total * abs(b["confidence"] - b["accuracy"]) for b in bins if b["n"]
    )


def choose_threshold(
    scores: list[float],
    correct: list[bool],
    *,
    precision_target: float,
    min_support: int = 10,
) -> dict[str, Any]:
    """Smallest threshold at which precision among auto-resolved >= target.

    Scanned from high to low so the first passing value is the *lowest* safe
    threshold, which maximises the auto-resolution rate under the constraint.
    Requires a minimum number of addresses above the threshold: a precision of
    1.0 on three addresses is not evidence of anything.
    """
    candidates = sorted({round(s, 2) for s in scores}, reverse=True)
    best: dict[str, Any] | None = None
    for t in candidates:
        above = [c for s, c in zip(scores, correct, strict=True) if s >= t]
        if len(above) < min_support:
            continue
        precision = sum(above) / len(above)
        if precision >= precision_target:
            best = {
                "threshold": t,
                "precision": precision,
                "auto_resolution_rate": len(above) / len(scores),
                "support": len(above),
            }
        else:
            # Precision only degrades as the threshold falls further.
            break
    if best is None:
        return {
            "threshold": None,
            "precision": None,
            "auto_resolution_rate": 0.0,
            "support": 0,
            "note": (
                f"no threshold with >= {min_support} addresses reaches "
                f"{precision_target:.0%} precision on this split"
            ),
        }
    return best


def svg_reliability(
    bins: list[dict[str, float]], *, ece: float, title: str, out: Path
) -> None:
    """A dependency-free reliability diagram."""
    w, h, pad = 520, 520, 60
    plot = w - 2 * pad

    def sx(v: float) -> float:
        return pad + v * plot

    def sy(v: float) -> float:
        return h - pad - v * plot

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" font-family="Inter, system-ui, sans-serif" font-size="12">',
        f'<rect width="{w}" height="{h}" fill="#ffffff"/>',
        f'<text x="{w / 2}" y="28" text-anchor="middle" font-size="15" font-weight="600">{title}</text>',
        f'<text x="{w / 2}" y="46" text-anchor="middle" fill="#666">ECE (2-fold CV) = {ece:.3f} · bar height = observed accuracy · label = n · dot = mean confidence</text>',
        # axes
        f'<line x1="{sx(0)}" y1="{sy(0)}" x2="{sx(1)}" y2="{sy(0)}" stroke="#333"/>',
        f'<line x1="{sx(0)}" y1="{sy(0)}" x2="{sx(0)}" y2="{sy(1)}" stroke="#333"/>',
        # diagonal = perfect calibration
        f'<line x1="{sx(0)}" y1="{sy(0)}" x2="{sx(1)}" y2="{sy(1)}" stroke="#999" stroke-dasharray="6 4"/>',
    ]
    for t in (0.0, 0.2, 0.4, 0.6, 0.8, 1.0):
        parts.append(
            f'<text x="{sx(t)}" y="{sy(0) + 18}" text-anchor="middle" fill="#444">{t:.1f}</text>'
        )
        parts.append(
            f'<text x="{sx(0) - 8}" y="{sy(t) + 4}" text-anchor="end" fill="#444">{t:.1f}</text>'
        )
    parts.append(
        f'<text x="{w / 2}" y="{h - 14}" text-anchor="middle">predicted confidence</text>'
    )
    parts.append(
        f'<text x="18" y="{h / 2}" text-anchor="middle" transform="rotate(-90 18 {h / 2})">observed accuracy</text>'
    )
    n_bins = len(bins)
    bar_w = plot / n_bins
    for b in bins:
        if not b["n"]:
            continue
        x0 = sx(b["lo"]) + 2
        y_top = sy(b["accuracy"])
        gap = b["confidence"] - b["accuracy"]
        colour = (
            "#1f7a4d" if abs(gap) < 0.1 else "#e87d1e" if abs(gap) < 0.25 else "#c0392b"
        )
        parts.append(
            f'<rect x="{x0:.1f}" y="{y_top:.1f}" width="{bar_w - 4:.1f}" height="{sy(0) - y_top:.1f}" fill="{colour}" fill-opacity="0.75"/>'
        )
        parts.append(
            f'<text x="{x0 + (bar_w - 4) / 2:.1f}" y="{y_top - 4:.1f}" text-anchor="middle" fill="#333">{int(b["n"])}</text>'
        )
        # mean confidence marker for the bin
        parts.append(
            f'<circle cx="{sx(b["confidence"]):.1f}" cy="{sy(b["accuracy"]):.1f}" r="3" fill="#111"/>'
        )
    parts.append("</svg>")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(parts), encoding="utf-8")


# --- main ----------------------------------------------------------------------------


def collect(
    rows: list[dict[str, Any]], stack: Stack, data_dir: Path
) -> tuple[list[float], list[bool]]:
    cfg = load_config()
    providers = build_providers(stack, data_dir)
    identity = conf.Calibrator(None)  # raw scores, regardless of any fitted file
    raw: list[float] = []
    correct: list[bool] = []
    for row in rows:
        result = resolve(
            row["raw"], stack=stack, cfg=cfg, providers=providers, calibrator=identity
        )
        raw.append(result.confidence)
        correct.append(is_correct(result.model_dump(mode="json"), row))
    return raw, correct


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--split",
        choices=("dev",),
        default="dev",
        help="calibration is fitted on dev only",
    )
    ap.add_argument("--stack", default="R2", choices=[s.value for s in Stack])
    ap.add_argument("--data-dir", type=Path, default=Path("eval/data"))
    ap.add_argument("--out", type=Path, default=Path("data/calibration.json"))
    ap.add_argument("--svg", type=Path, default=Path("../docs/results/reliability.svg"))
    ap.add_argument("--precision-target", type=float, default=None)
    args = ap.parse_args()

    path = args.data_dir / f"gold_{args.split}.jsonl"
    if not path.exists():
        print(f"error: {path} not found", file=sys.stderr)
        return 1
    rows = [json.loads(line) for line in path.open(encoding="utf-8")]
    cfg = load_config()
    target = (
        args.precision_target
        if args.precision_target is not None
        else cfg.thresholds.precision_target
    )
    stack = Stack(args.stack)

    gazetteer.warm()
    try:
        raw, correct = collect(rows, stack, args.data_dir)
    except StageUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    n = len(raw)
    base_rate = sum(correct) / n
    print(f"stack {stack.value} · n={n} · overall correct {base_rate:.3f}")

    # --- fit --------------------------------------------------------------------
    knots = pav(raw, [1.0 if c else 0.0 for c in correct])
    calibrated = [apply_knots(knots, s) for s in raw]

    raw_bins = bin_reliability(raw, correct)
    cal_bins = bin_reliability(calibrated, correct)
    ece_raw = expected_calibration_error(raw_bins, n)
    ece_cal = expected_calibration_error(cal_bins, n)

    # --- honest check: 2-fold cross-validated ECE ----------------------------------
    half = n // 2
    folds = [
        (list(range(half)), list(range(half, n))),
        (list(range(half, n)), list(range(half))),
    ]
    cv_scores: list[float] = [0.0] * n
    for train, test in folds:
        k = pav([raw[i] for i in train], [1.0 if correct[i] else 0.0 for i in train])
        for i in test:
            cv_scores[i] = apply_knots(k, raw[i])
    ece_cv = expected_calibration_error(bin_reliability(cv_scores, correct), n)

    # --- threshold --------------------------------------------------------------------
    choice = choose_threshold(calibrated, correct, precision_target=target)

    print(
        f"ECE raw {ece_raw:.3f} -> calibrated (in-sample) {ece_cal:.3f} · 2-fold CV {ece_cv:.3f}"
    )
    print("reliability (calibrated, in-sample):")
    for b in cal_bins:
        if b["n"]:
            print(
                f"  [{b['lo']:.1f},{b['hi']:.1f})  n={int(b['n']):>3}  conf {b['confidence']:.2f}  acc {b['accuracy']:.2f}"
            )
    if choice["threshold"] is None:
        print(f"threshold: {choice['note']}")
    else:
        print(
            f"threshold for precision >= {target:.0%}: {choice['threshold']:.2f}  "
            f"(precision {choice['precision']:.3f}, auto-resolve {choice['auto_resolution_rate']:.1%}, "
            f"n above {choice['support']})"
        )

    # --- write --------------------------------------------------------------------------
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(
            {
                "knots": [[round(x, 4), round(y, 4)] for x, y in knots],
                "fitted_on": {
                    "split": args.split,
                    "stack": stack.value,
                    "n": n,
                    "provider": cfg.provider,
                },
                "ece": {
                    "raw": round(ece_raw, 4),
                    "calibrated_in_sample": round(ece_cal, 4),
                    "calibrated_2fold_cv": round(ece_cv, 4),
                },
                "threshold": choice,
                "precision_target": target,
                "bins": cal_bins,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    # The diagram carries the cross-validated ECE, not the in-sample one: an
    # isotonic fit is exactly calibrated on its own training data by
    # construction, so the in-sample figure is always 0.000 and says nothing.
    svg_reliability(
        cal_bins,
        ece=ece_cv,
        title=f"Reliability — stack {stack.value}, dev split (n={n}, {cfg.provider}, 2-fold CV)",
        out=args.svg,
    )
    print(f"wrote {args.out} and {args.svg}")
    if choice["threshold"] is not None:
        print(
            f"to apply: set CONF_RESOLVED_AT={choice['threshold']:.2f} (template parameter / env)"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
