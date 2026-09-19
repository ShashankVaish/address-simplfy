/**
 * Instruments: the pieces that make the pipeline visible.
 *
 * A gauge for confidence with the threshold on the arc, a DIGIPIN plate that
 * shows the code as the ten grid cells it is, a rail that lights the eight
 * stages with their real milliseconds, a 24-hour dial with the permitted
 * window drawn as an arc, and a stat tile. All inline SVG, all tokens.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { confidenceBand, formatDigipin } from "../api/types";

// --- confidence gauge -----------------------------------------------------------

function useCountUp(target: number, ms = 700): number {
  const [v, setV] = useState(0);
  const from = useRef(0);
  useEffect(() => {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      setV(target);
      from.current = target;
      return;
    }
    const start = performance.now();
    const a = from.current;
    let raf = 0;
    const tick = (t: number) => {
      const p = Math.min(1, (t - start) / ms);
      const e = 1 - Math.pow(1 - p, 3);
      setV(a + (target - a) * e);
      if (p < 1) raf = requestAnimationFrame(tick);
      else from.current = target;
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [target, ms]);
  return v;
}

/** A point on a circle, angle in degrees with 0 at the right, clockwise. */
function polar(cx: number, cy: number, r: number, deg: number): [number, number] {
  const a = (deg * Math.PI) / 180;
  return [cx + r * Math.cos(a), cy + r * Math.sin(a)];
}

function arcPath(cx: number, cy: number, r: number, fromDeg: number, toDeg: number): string {
  const [x1, y1] = polar(cx, cy, r, fromDeg);
  const [x2, y2] = polar(cx, cy, r, toDeg);
  const large = toDeg - fromDeg > 180 ? 1 : 0;
  return `M ${x1.toFixed(2)} ${y1.toFixed(2)} A ${r} ${r} 0 ${large} 1 ${x2.toFixed(2)} ${y2.toFixed(2)}`;
}

/**
 * Semicircular gauge, 180° sweep from the left. The 0.80 threshold is a tick
 * on the arc so the needle reads as "past the line" before the number does.
 */
export function ConfidenceGauge({ value, calibrated, threshold = 0.8 }: { value: number; calibrated: boolean; threshold?: number }) {
  const shown = useCountUp(value);
  const band = confidenceBand(value);
  const stroke = band === "high" ? "stroke-leaf" : band === "medium" ? "stroke-saffron" : "stroke-deny";
  const text = band === "high" ? "text-leaf" : band === "medium" ? "text-saffron" : "text-deny";
  const cx = 100, cy = 96, r = 78;
  const start = 180, sweep = 180;
  const valueDeg = start + sweep * Math.max(0, Math.min(1, shown));
  const tickDeg = start + sweep * threshold;
  const [tx1, ty1] = polar(cx, cy, r - 12, tickDeg);
  const [tx2, ty2] = polar(cx, cy, r + 12, tickDeg);
  const above = value >= threshold;
  return (
    <div className="flex flex-col items-center">
      <svg viewBox="0 0 200 112" className="w-full max-w-[280px]" role="meter" aria-valuemin={0} aria-valuemax={1} aria-valuenow={Number(value.toFixed(2))} aria-label={`Confidence ${value.toFixed(2)}, ${above ? "at or above" : "below"} the ${threshold} threshold`}>
        <path d={arcPath(cx, cy, r, start, start + sweep)} className="stroke-hairline" strokeWidth="12" fill="none" strokeLinecap="round" />
        {shown > 0.005 && <path d={arcPath(cx, cy, r, start, valueDeg)} className={stroke} strokeWidth="12" fill="none" strokeLinecap="round" />}
        <line x1={tx1} y1={ty1} x2={tx2} y2={ty2} className="stroke-ink" strokeWidth="2.5" strokeLinecap="round" />
        <text x={polar(cx, cy, r + 22, tickDeg)[0]} y={polar(cx, cy, r + 22, tickDeg)[1] + 4} textAnchor="middle" className="fill-ink font-mono" fontSize="9" fontWeight="600">
          {threshold.toFixed(2)}
        </text>
        <text x="22" y="108" textAnchor="middle" className="fill-muted font-mono" fontSize="9">0</text>
        <text x="178" y="108" textAnchor="middle" className="fill-muted font-mono" fontSize="9">1</text>
        <text x={cx} y={cy - 6} textAnchor="middle" className={`font-mono ${text}`} fill="currentColor" fontSize="34" fontWeight="600" style={{ fontVariantNumeric: "tabular-nums" }}>
          {shown.toFixed(2)}
        </text>
        <text x={cx} y={cy + 10} textAnchor="middle" className="fill-muted font-mono" fontSize="8" letterSpacing="1">
          {calibrated ? "CALIBRATED" : "RAW SCORE"}
        </text>
      </svg>
      <div className="-mt-1 font-mono text-2xs text-muted">
        {above ? "at or above" : "below"} {threshold.toFixed(2)} · {above ? "auto-resolves, nobody is called" : "asks exactly one question"}
      </div>
    </div>
  );
}

// --- DIGIPIN plate ---------------------------------------------------------------------

/** The ten characters as ten cells, grouped 3-3-4 the way the display form is. */
export function DigipinPlate({ digipin, size = "md" }: { digipin: string; size?: "md" | "lg" }) {
  const chars = digipin.replace(/[\s-]/g, "").toUpperCase().split("");
  const groups = [chars.slice(0, 3), chars.slice(3, 6), chars.slice(6, 10)];
  const cell = size === "lg" ? "h-11 w-9 text-xl" : "h-9 w-7 text-base";
  return (
    <div className="inline-flex flex-col gap-1.5">
      <div className="inline-flex items-center gap-2 rounded-md border border-hairline-2 bg-sunken p-2">
        {groups.map((g, gi) => (
          <div key={gi} className="flex gap-1">
            {g.map((c, i) => (
              <span key={i} className={`grid ${cell} place-items-center rounded-sm border border-code-dim/40 bg-code-dim/10 font-mono font-semibold text-code-ink`}>
                {c}
              </span>
            ))}
          </div>
        ))}
      </div>
      <div className="font-mono text-2xs text-muted">DIGIPIN · {formatDigipin(digipin)} · ~4 m cell · computed locally · ₹0</div>
    </div>
  );
}

// --- pipeline rail ------------------------------------------------------------------------

export const STAGES: { id: string; key: string; name: string }[] = [
  { id: "S0", key: "s0_normalise", name: "Normalise" },
  { id: "S1", key: "s1_parse", name: "Parse" },
  { id: "S2", key: "s2_retrieve", name: "Retrieve" },
  { id: "S3", key: "s3_structure", name: "Structure" },
  { id: "S4", key: "s4_geocode", name: "Geocode" },
  { id: "S5", key: "s5_digipin", name: "DIGIPIN" },
  { id: "S6", key: "s6_confidence", name: "Score" },
  { id: "S7", key: "s7_decide", name: "Decide" },
];

/**
 * Eight stages in a row. While the request is in flight the light sweeps;
 * once the answer lands each stage shows its real milliseconds, revealed in
 * the same order the page reveals the results.
 */
export function PipelineRail({ busy, timings, litThrough, cached }: { busy: boolean; timings: Record<string, number> | null; litThrough: number; cached: boolean }) {
  const [sweep, setSweep] = useState(0);
  useEffect(() => {
    if (!busy) return;
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduce) return;
    const id = window.setInterval(() => setSweep((s) => (s + 1) % STAGES.length), 160);
    return () => window.clearInterval(id);
  }, [busy]);
  return (
    <ol className="grid grid-cols-4 gap-1.5 sm:grid-cols-8" aria-label="Pipeline stages">
      {STAGES.map((s, i) => {
        const ms = timings ? timings[s.key] : undefined;
        const state = busy ? (i === sweep ? "rail-active" : "rail-pending") : timings ? (i <= litThrough ? "rail-done" : "rail-pending") : "rail-pending";
        return (
          <li key={s.id} className={`rounded-md border px-2 py-1.5 transition-colors duration-fast ${state}`}>
            <div className="flex items-baseline justify-between">
              <span className="font-mono text-2xs font-semibold">{s.id}</span>
              <span className="font-mono text-2xs tnum">{busy ? "…" : ms !== undefined && i <= litThrough ? (cached && i > 0 ? "cache" : `${ms < 1 ? "<1" : Math.round(ms)} ms`) : ""}</span>
            </div>
            <div className="truncate text-2xs">{s.name}</div>
          </li>
        );
      })}
    </ol>
  );
}

// --- stat tile -------------------------------------------------------------------------------

export function Stat({ label, value, tone = "ink", hint }: { label: string; value: string | number; tone?: "ink" | "leaf" | "saffron" | "deny" | "muted"; hint?: string }) {
  const t = { ink: "text-ink", leaf: "text-leaf", saffron: "text-saffron", deny: "text-deny", muted: "text-muted" }[tone];
  return (
    <div className="px-4 py-3">
      <div className={`font-mono text-xl font-semibold tnum ${t}`}>{value}</div>
      <div className="text-2xs uppercase tracking-[0.08em] text-muted">{label}</div>
      {hint && <div className="mt-0.5 text-2xs text-muted">{hint}</div>}
    </div>
  );
}

// --- 24-hour dial ---------------------------------------------------------------------------

/**
 * A clock face for local hour. The permitted window is an arc on the dial,
 * the hand is the customer's hour. Drag or use the keyboard; it is a slider.
 */
export function ClockDial({ hour, window: win, onChange }: { hour: number; window: [number, number]; onChange: (h: number) => void }) {
  const ref = useRef<SVGSVGElement>(null);
  const cx = 110, cy = 110, r = 84;
  const deg = (h: number) => -90 + (h / 24) * 360; // midnight at the top
  const inside = hour >= win[0] && hour <= win[1];
  const [hx, hy] = polar(cx, cy, r - 14, deg(hour));

  const fromPointer = useCallback(
    (e: PointerEvent | React.PointerEvent) => {
      const el = ref.current;
      if (!el) return;
      const rect = el.getBoundingClientRect();
      const x = ((e.clientX - rect.left) / rect.width) * 220 - cx;
      const y = ((e.clientY - rect.top) / rect.height) * 220 - cy;
      let a = (Math.atan2(y, x) * 180) / Math.PI + 90;
      if (a < 0) a += 360;
      onChange(Math.round((a / 360) * 24) % 24);
    },
    [onChange],
  );

  const onPointerDown = (e: React.PointerEvent) => {
    (e.target as Element).setPointerCapture?.(e.pointerId);
    fromPointer(e);
    const move = (ev: PointerEvent) => fromPointer(ev);
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };

  const onKey = (e: React.KeyboardEvent) => {
    const step = (d: number) => onChange((hour + d + 24) % 24);
    if (e.key === "ArrowRight" || e.key === "ArrowUp") step(1);
    else if (e.key === "ArrowLeft" || e.key === "ArrowDown") step(-1);
    else if (e.key === "Home") onChange(0);
    else if (e.key === "End") onChange(23);
    else if (e.key === "PageUp") step(3);
    else if (e.key === "PageDown") step(-3);
    else return;
    e.preventDefault();
  };

  return (
    <svg
      ref={ref}
      viewBox="0 0 220 220"
      className="mx-auto w-full max-w-[240px] cursor-pointer select-none touch-none"
      role="slider"
      tabIndex={0}
      aria-label="Customer's local hour"
      aria-valuemin={0}
      aria-valuemax={23}
      aria-valuenow={hour}
      aria-valuetext={`${hour < 10 ? "0" : ""}${hour}:00, ${inside ? "inside" : "outside"} the 09:00 to 20:00 window`}
      onPointerDown={onPointerDown}
      onKeyDown={onKey}
    >
      <circle cx={cx} cy={cy} r={r} className="fill-raised stroke-hairline" strokeWidth="1" />
      {/* permitted window */}
      <path d={arcPath(cx, cy, r, deg(win[0]), deg(win[1] + 1))} className="stroke-leaf/35" strokeWidth="14" fill="none" />
      {/* hour ticks */}
      {Array.from({ length: 24 }).map((_, h) => {
        const [x1, y1] = polar(cx, cy, r - 2, deg(h));
        const [x2, y2] = polar(cx, cy, r - (h % 6 === 0 ? 10 : 5), deg(h));
        return <line key={h} x1={x1} y1={y1} x2={x2} y2={y2} className={h >= win[0] && h <= win[1] ? "stroke-leaf" : "stroke-hairline-2"} strokeWidth={h % 6 === 0 ? 2 : 1} />;
      })}
      {[0, 6, 12, 18].map((h) => {
        const [x, y] = polar(cx, cy, r + 12, deg(h));
        return (
          <text key={h} x={x} y={y + 3} textAnchor="middle" className={`font-mono ${h >= win[0] && h <= win[1] ? "fill-leaf" : "fill-muted"}`} fontSize="9">
            {h < 10 ? `0${h}` : h}
          </text>
        );
      })}
      {/* hand */}
      <line x1={cx} y1={cy} x2={hx} y2={hy} className={inside ? "stroke-leaf" : "stroke-deny"} strokeWidth="3" strokeLinecap="round" />
      <circle cx={hx} cy={hy} r="7" className={`fill-surface ${inside ? "stroke-leaf" : "stroke-deny"}`} strokeWidth="3" />
      <circle cx={cx} cy={cy} r="3" className="fill-ink" />
      {/* readout */}
      <text x={cx} y={cy + 34} textAnchor="middle" className={`font-mono ${inside ? "fill-leaf" : "fill-deny"}`} fontSize="20" fontWeight="600" style={{ fontVariantNumeric: "tabular-nums" }}>
        {hour < 10 ? `0${hour}` : hour}:00
      </text>
      <text x={cx} y={cy + 48} textAnchor="middle" className="fill-muted font-mono" fontSize="8" letterSpacing="1">
        {inside ? "INSIDE 09–20" : "OUTSIDE 09–20"}
      </text>
    </svg>
  );
}
