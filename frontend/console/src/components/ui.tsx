/**
 * Shared presentational pieces.
 *
 * Each one renders a fact from the contract and refuses to lie about it: a
 * missing field is a dash, a centroid is a shaded area rather than a pin, an
 * uncalibrated confidence says so, and a value the model inferred is marked
 * differently from one the customer actually typed.
 *
 * No hex here. Every colour is a token from index.css via Tailwind.
 */

import { useEffect, useRef, useState } from "react";
import maplibregl from "maplibre-gl";
import {
  FIELD_ORDER,
  confidenceBand,
  formatDigipin,
  isDoorstepAccurate,
  type Geo,
  type Landmark,
  type Status,
  type StructuredAddress,
} from "../api/types";

// --- icons (hand-drawn, 1.6px stroke, currentColor) ---------------------------

type IconName = "check" | "dash" | "pin" | "graph" | "question" | "sun" | "moon" | "system" | "refresh" | "arrow" | "spark" | "shield" | "clock" | "info" | "x";

const PATHS: Record<IconName, string> = {
  check: "M5 12l4 4 10-10",
  dash: "M5 12h14",
  pin: "M12 21s-6-5.5-6-11a6 6 0 0 1 12 0c0 5.5-6 11-6 11z M12 10m-2.2 0a2.2 2.2 0 1 0 4.4 0a2.2 2.2 0 1 0-4.4 0",
  graph: "M6 6m-2.5 0a2.5 2.5 0 1 0 5 0a2.5 2.5 0 1 0-5 0 M18 8m-2.5 0a2.5 2.5 0 1 0 5 0a2.5 2.5 0 1 0-5 0 M12 18m-2.5 0a2.5 2.5 0 1 0 5 0a2.5 2.5 0 1 0-5 0 M8.4 6.4l7.2 1.2M7 8.2l3.6 7.4M16.8 10.3l-3.6 5.4",
  question: "M9.5 9a2.5 2.5 0 1 1 3.5 2.3c-.7.4-1 1-1 1.7v.5 M12 17h.01 M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z",
  sun: "M12 12m-4 0a4 4 0 1 0 8 0a4 4 0 1 0-8 0 M12 2v2M12 20v2M2 12h2M20 12h2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4",
  moon: "M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z",
  system: "M3 5h18v11H3z M8 20h8 M12 16v4",
  refresh: "M20 12a8 8 0 1 1-2.3-5.7 M20 4v5h-5",
  arrow: "M5 12h14 M13 6l6 6-6 6",
  spark: "M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z",
  shield: "M12 3l8 4v5c0 5-3.5 8-8 9-4.5-1-8-4-8-9V7l8-4z",
  clock: "M12 12m-9 0a9 9 0 1 0 18 0a9 9 0 1 0-18 0 M12 7v5l3 2",
  info: "M12 12m-9 0a9 9 0 1 0 18 0a9 9 0 1 0-18 0 M12 11v5 M12 8h.01",
  x: "M6 6l12 12M18 6L6 18",
};

export function Icon({ name, className = "h-4 w-4" }: { name: IconName; className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={PATHS[name]} />
    </svg>
  );
}

// --- logo -------------------------------------------------------------------------

/** The PataSetu mark: a pin over a bridge between two skylines. PNG with a transparent ground. */
export function Logo({ className = "h-8" }: { className?: string }) {
  return <img src="/logo-256.png" alt="" className={`${className} w-auto select-none`} draggable={false} />;
}

// --- layout -----------------------------------------------------------------------

export function Panel({
  title,
  aside,
  children,
  className = "",
  padded = true,
}: {
  title?: string;
  aside?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
  padded?: boolean;
}) {
  return (
    <section className={`panel min-w-0 ${className}`}>
      {(title || aside) && (
        <header className="flex items-center justify-between gap-3 border-b border-hairline px-4 py-2.5">
          {title && <h2 className="panel-title">{title}</h2>}
          {aside && <div className="text-xs text-muted">{aside}</div>}
        </header>
      )}
      <div className={padded ? "p-4" : ""}>{children}</div>
    </section>
  );
}

/** An empty state that says what would appear here and how to make it. */
export function Empty({ icon = "info", title, hint }: { icon?: IconName; title: string; hint?: string }) {
  return (
    <div className="flex items-start gap-3 py-2 text-sm">
      <span className="mt-0.5 grid h-7 w-7 shrink-0 place-items-center rounded-md border border-hairline bg-raised text-muted">
        <Icon name={icon} />
      </span>
      <div>
        <div className="font-medium text-ink-2">{title}</div>
        {hint && <div className="mt-0.5 text-xs text-muted">{hint}</div>}
      </div>
    </div>
  );
}

export function ErrorNote({ children }: { children: React.ReactNode }) {
  return (
    <div role="alert" className="flex items-start gap-2 rounded-md border border-deny/40 bg-deny/5 px-3 py-2 text-sm text-deny">
      <Icon name="info" className="mt-0.5 h-4 w-4 shrink-0" />
      <span>{children}</span>
    </div>
  );
}

/** Skeleton bones in the shape of a label/value record. */
export function RecordSkeleton({ rows = 6 }: { rows?: number }) {
  return (
    <div className="space-y-2.5" aria-hidden="true">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="grid grid-cols-[88px_1fr] items-center gap-3">
          <div className="bone h-3 w-16" />
          <div className="bone h-3.5" style={{ width: `${40 + ((i * 23) % 45)}%` }} />
        </div>
      ))}
    </div>
  );
}

// --- status ----------------------------------------------------------------------

const STATUS: Record<Status, { cls: string; label: string; icon: IconName }> = {
  RESOLVED: { cls: "border-leaf/40 bg-leaf/10 text-leaf", label: "RESOLVED", icon: "check" },
  NEEDS_INFO: { cls: "border-saffron/40 bg-saffron/10 text-saffron", label: "NEEDS INFO", icon: "question" },
  AMBIGUOUS: { cls: "border-hairline-2 bg-raised text-ink-2", label: "AMBIGUOUS", icon: "info" },
};

export function StatusPill({ status }: { status: Status | string }) {
  const s = STATUS[status as Status];
  if (!s) return <span className="pill border-hairline-2 bg-raised text-ink-2">{status.replace("_", " ")}</span>;
  return (
    <span className={`pill ${s.cls}`}>
      <Icon name={s.icon} className="h-3 w-3" />
      {s.label}
    </span>
  );
}

// --- confidence ------------------------------------------------------------------

/** Counts a number toward its target; instant under reduced motion. */
function useCountUp(target: number, ms = 600): number {
  const [v, setV] = useState(target);
  const from = useRef(0);
  useEffect(() => {
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduce) {
      setV(target);
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

/**
 * The confidence meter. The 0.80 auto-resolve threshold is drawn on the track,
 * so a score reads as "above" or "below" the line before the number is read.
 */
export function ConfidenceMeter({ value, calibrated, threshold = 0.8 }: { value: number; calibrated: boolean; threshold?: number }) {
  const shown = useCountUp(value);
  const band = confidenceBand(value);
  const fill = band === "high" ? "bg-leaf" : band === "medium" ? "bg-saffron" : "bg-deny";
  const above = value >= threshold;
  return (
    <div>
      <div className="flex items-baseline justify-between">
        <span className="panel-title">Confidence</span>
        <span className="font-mono text-xl font-semibold tnum" aria-live="off">{shown.toFixed(2)}</span>
      </div>
      <div className="relative mt-2 h-2.5 w-full rounded-full bg-raised ring-1 ring-inset ring-hairline" role="meter" aria-valuemin={0} aria-valuemax={1} aria-valuenow={Number(value.toFixed(2))} aria-label="Calibrated confidence">
        <div className={`h-full rounded-full transition-[width] duration-slow ease-settle ${fill}`} style={{ width: `${Math.round(value * 100)}%` }} />
        <div className="absolute -top-1 w-0.5 bg-ink" style={{ left: `${threshold * 100}%`, height: "18px" }} aria-hidden="true" />
      </div>
      <div className="mt-1.5 flex items-center justify-between text-2xs text-muted">
        <span>{calibrated ? "calibrated probability" : "raw score — calibration not fitted"}</span>
        <span className="font-mono">
          {above ? "at or above" : "below"} {threshold.toFixed(2)} · {above ? "auto-resolves" : "asks one question"}
        </span>
      </div>
    </div>
  );
}

// --- structured record --------------------------------------------------------------

const LABELS: Record<keyof StructuredAddress, string> = {
  building: "Building",
  street: "Street",
  sub_locality: "Sub-locality",
  locality: "Locality",
  city: "City",
  district: "District",
  state: "State",
  pincode: "Pincode",
  landmarks: "Landmarks",
};

export type FieldSource = "stated" | "gazetteer" | "graph" | "model";

/**
 * Where each field's value came from, read off the evidence lines the
 * pipeline emits. "stated" was in the text and parsed deterministically;
 * "gazetteer" follows from the pincode; "graph" from a matched landmark;
 * anything else non-null was inferred by the model.
 */
export function fieldSources(structured: StructuredAddress, evidence: string[]): Partial<Record<keyof StructuredAddress, FieldSource>> {
  const out: Partial<Record<keyof StructuredAddress, FieldSource>> = {};
  const text = evidence.join("\n");
  const pincodeStated = /pincode \d{6} found in gazetteer/.test(text);
  const localityStated = /locality '.+' in text matches pincode/.test(text);
  const localityGraph = /anchor: locality '.+' matched LMK#/.test(text);
  const buildingStated = /house number '.+' matched deterministically/.test(text);
  const modelRan = /structured by /.test(text);
  for (const f of FIELD_ORDER) {
    const v = structured[f];
    if (v === null || v === undefined) continue;
    if (f === "pincode" && pincodeStated) out[f] = "stated";
    else if (f === "building" && buildingStated) out[f] = "stated";
    else if (f === "locality" && localityStated) out[f] = "stated";
    else if (f === "locality" && localityGraph) out[f] = "graph";
    else if ((f === "city" || f === "district" || f === "state") && pincodeStated) out[f] = "gazetteer";
    else out[f] = modelRan ? "model" : "stated";
  }
  return out;
}

const SOURCE_STYLE: Record<FieldSource, { cls: string; label: string; title: string }> = {
  stated: { cls: "text-leaf border-leaf/40", label: "stated", title: "Written in the address; parsed deterministically" },
  gazetteer: { cls: "text-ink-2 border-hairline-2", label: "pincode", title: "Follows from the pincode via the India Post directory" },
  graph: { cls: "text-leaf border-leaf/40", label: "graph", title: "From a landmark the graph already knows" },
  model: { cls: "text-saffron border-saffron/40", label: "inferred", title: "Filled in by the model; not stated in the text" },
};

export function StructuredRecord({
  structured,
  evidence = [],
  revealed,
}: {
  structured: StructuredAddress;
  evidence?: string[];
  /** Fields to show; undefined shows everything. Drives the stage reveal. */
  revealed?: Set<string>;
}) {
  const sources = fieldSources(structured, evidence);
  return (
    <dl className="divide-y divide-hairline">
      {FIELD_ORDER.map((field) => {
        const value = structured[field] as string | null;
        const show = !revealed || revealed.has(field);
        const src = value ? sources[field] : undefined;
        return (
          <div key={field} className={`reveal grid grid-cols-[92px_1fr_auto] items-center gap-3 py-1.5 ${show ? "" : "reveal-hidden"}`}>
            <dt className="text-xs text-muted">{LABELS[field]}</dt>
            <dd className={`truncate ${value ? "font-medium text-ink" : "text-muted"}`} title={value ?? "not in the input"}>
              {value ?? <span aria-label="not in the input">—</span>}
            </dd>
            <dd className="justify-self-end">
              {src && (
                <span className={`pill border ${SOURCE_STYLE[src].cls}`} title={SOURCE_STYLE[src].title}>
                  {SOURCE_STYLE[src].label}
                </span>
              )}
            </dd>
          </div>
        );
      })}
    </dl>
  );
}

// --- landmarks --------------------------------------------------------------------

const RELATION_WORD: Record<Landmark["relation"], string> = {
  near: "near",
  behind: "behind",
  opposite: "opposite",
  above: "above",
  beside: "beside",
  inside: "inside",
};

export function LandmarkList({ landmarks, revealed = true }: { landmarks: Landmark[]; revealed?: boolean }) {
  if (!landmarks.length) {
    return <Empty icon="graph" title="No landmarks in this address" hint="A locality and pincode alone can still resolve; a landmark is what places the doorstep." />;
  }
  return (
    <ul className={`reveal divide-y divide-hairline ${revealed ? "" : "reveal-hidden"}`}>
      {landmarks.map((lm, i) => (
        <li key={i} className="grid grid-cols-[76px_1fr_auto] items-center gap-3 py-2">
          <span className="eyebrow">{RELATION_WORD[lm.relation] ?? lm.relation}</span>
          <span className="truncate font-medium" title={lm.name}>
            {lm.name}
          </span>
          {lm.matched_id ? (
            <span className="pill border-leaf/40 bg-leaf/10 text-leaf" title={`${lm.matched_id}${lm.distance_m != null ? ` · ${Math.round(lm.distance_m)} m away` : ""}`}>
              <Icon name="check" className="h-3 w-3" />
              in graph
            </span>
          ) : (
            <span className="pill border-hairline-2 bg-raised text-muted" title="Not in the landmark graph yet. A confirmed delivery will add it.">
              new
            </span>
          )}
        </li>
      ))}
    </ul>
  );
}

// --- DIGIPIN ------------------------------------------------------------------------

export function DigipinBadge({ digipin }: { digipin: string | null }) {
  if (!digipin) {
    return <Empty icon="pin" title="No DIGIPIN" hint="A DIGIPIN needs a coordinate; this address did not get one." />;
  }
  return (
    <div className="flex flex-wrap items-end justify-between gap-2">
      <div>
        <div className="font-mono text-2xl font-semibold tracking-[0.08em] text-ink tnum">{formatDigipin(digipin)}</div>
        <div className="mt-0.5 text-2xs text-muted">
          DIGIPIN · India Post grid · ~4 m cell · stored as <span className="font-mono text-ink-2">{digipin}</span>
        </div>
      </div>
      <span className="eyebrow">computed locally · ₹0</span>
    </div>
  );
}

// --- evidence -----------------------------------------------------------------------

export function EvidenceList({ evidence, columns = false }: { evidence: string[]; columns?: boolean }) {
  if (!evidence.length) return <Empty title="No evidence recorded" />;
  return (
    <ol className={`space-y-1.5 text-xs leading-relaxed text-ink-2 ${columns ? "lg:columns-2 lg:gap-8 [&>li]:break-inside-avoid" : ""}`}>
      {evidence.map((line, i) => {
        const warn = /NOT a doorstep|CONFLICT|degraded|penalty|AMBIGUOUS|denied|uncalibrated/.test(line);
        const good = /matched|RESOLVED|found in gazetteer|landmark graph/.test(line) && !warn;
        return (
          <li key={i} className="grid grid-cols-[1.5rem_1fr] gap-2">
            <span className="select-none text-right font-mono text-muted tnum">{i + 1}</span>
            <span className={warn ? "text-warning" : good ? "text-ink" : ""}>{line}</span>
          </li>
        );
      })}
    </ol>
  );
}

// --- map ---------------------------------------------------------------------------

type MapTheme = "light" | "dark";

/**
 * One free basemap for both themes: OpenStreetMap raster. Dark mode does not
 * swap tiles (the free dark tilesets now need an API key); it dims and
 * desaturates the same tiles in the renderer, which keeps overlays untouched.
 */
function mapStyle(): maplibregl.StyleSpecification {
  return {
    version: 8,
    sources: {
      base: {
        type: "raster",
        tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
        tileSize: 256,
        attribution: "© OpenStreetMap contributors",
      },
    },
    layers: [{ id: "base", type: "raster", source: "base" }],
  };
}

function applyTheme(m: maplibregl.Map, theme: MapTheme): void {
  if (!m.getLayer("base")) return;
  const dark = theme === "dark";
  m.setPaintProperty("base", "raster-saturation", dark ? -0.85 : 0);
  m.setPaintProperty("base", "raster-brightness-max", dark ? 0.42 : 1);
  m.setPaintProperty("base", "raster-brightness-min", dark ? 0.03 : 0);
  m.setPaintProperty("base", "raster-contrast", dark ? 0.2 : 0);
}

function pinElement(): HTMLElement {
  const el = document.createElement("div");
  el.className = "text-leaf drop-shadow";
  el.style.width = "28px";
  el.style.height = "34px";
  el.innerHTML =
    '<svg viewBox="0 0 28 34" width="28" height="34" aria-hidden="true"><path d="M14 33s-11-9.5-11-19a11 11 0 0 1 22 0c0 9.5-11 19-11 19z" fill="currentColor"/><circle cx="14" cy="14" r="4.5" fill="rgb(var(--c-surface))"/></svg>';
  return el;
}

function circle(lng: number, lat: number, radiusM: number, steps = 64): GeoJSON.Feature<GeoJSON.Polygon> {
  const coords: [number, number][] = [];
  const dLat = radiusM / 111_320;
  const dLng = radiusM / (111_320 * Math.cos((lat * Math.PI) / 180));
  for (let i = 0; i <= steps; i++) {
    const t = (i / steps) * 2 * Math.PI;
    coords.push([lng + dLng * Math.cos(t), lat + dLat * Math.sin(t)]);
  }
  return { type: "Feature", properties: {}, geometry: { type: "Polygon", coordinates: [coords] } };
}

/**
 * A pin for a doorstep-accurate point; a shaded area for anything else. The
 * distinction is the whole point of `geo.source`: a pincode centroid drawn as
 * a pin is a lie a rider will believe.
 *
 * Map lives in a ref for the component's life; the style swaps on theme
 * change; the container is observed so a reflow never leaves a torn canvas.
 */
export function MapPanel({ geo, theme, dim = false }: { geo: Geo | null; theme: MapTheme; dim?: boolean }) {
  const container = useRef<HTMLDivElement>(null);
  const map = useRef<maplibregl.Map | null>(null);
  const marker = useRef<maplibregl.Marker | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    if (!container.current || map.current) return;
    const m = new maplibregl.Map({
      container: container.current,
      style: mapStyle(),
      center: [78.9, 22.5],
      zoom: 3.6,
      attributionControl: false,
    });
    m.addControl(new maplibregl.AttributionControl({ compact: true }), "bottom-right");
    m.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    m.once("load", () => {
      applyTheme(m, theme);
      setReady(true);
    });
    map.current = m;
    const ro = new ResizeObserver(() => m.resize());
    ro.observe(container.current);
    return () => {
      ro.disconnect();
      m.remove();
      map.current = null;
    };
    // The initial theme is read once; later changes go through setStyle below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Theme flip: re-tint the same tiles; overlays and camera stay put.
  useEffect(() => {
    const m = map.current;
    if (!m || !ready) return;
    applyTheme(m, theme);
  }, [theme, ready]);

  useEffect(() => {
    const m = map.current;
    if (!m || !ready) return;
    const layerId = "accuracy";
    const apply = () => {
      marker.current?.remove();
      marker.current = null;
      if (m.getLayer(layerId)) m.removeLayer(layerId);
      if (m.getLayer(layerId + "-line")) m.removeLayer(layerId + "-line");
      if (m.getSource(layerId)) m.removeSource(layerId);
      if (!geo) return;
      const doorstep = isDoorstepAccurate(geo);
      const radius = geo.accuracy_m ?? (doorstep ? 60 : 3000);
      const rgb = getComputedStyle(document.documentElement).getPropertyValue(doorstep ? "--c-leaf" : "--c-warning").trim().replace(/ /g, ",");
      m.addSource(layerId, { type: "geojson", data: circle(geo.lng, geo.lat, radius) });
      m.addLayer({ id: layerId, type: "fill", source: layerId, paint: { "fill-color": `rgb(${rgb})`, "fill-opacity": doorstep ? 0.12 : 0.18 } });
      m.addLayer({ id: layerId + "-line", type: "line", source: layerId, paint: { "line-color": `rgb(${rgb})`, "line-width": 1.5, "line-opacity": 0.7 } });
      if (doorstep) marker.current = new maplibregl.Marker({ element: pinElement(), anchor: "bottom" }).setLngLat([geo.lng, geo.lat]).addTo(m);
      const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      m.flyTo({ center: [geo.lng, geo.lat], zoom: doorstep ? 16 : 12.5, duration: reduce ? 0 : 900, essential: true });
    };
    if (m.isStyleLoaded()) apply();
    else m.once("styledata", apply);
  }, [geo, ready]);

  const doorstep = isDoorstepAccurate(geo);
  return (
    <div className={`relative h-72 w-full overflow-hidden rounded-md border border-hairline transition-opacity duration-slow ease-out ${dim ? "opacity-40" : "opacity-100"}`}>
      <div ref={container} className="h-full w-full" />
      {geo ? (
        <div className="absolute bottom-2 left-2 z-overlay flex items-center gap-2 rounded-md border border-hairline bg-surface/90 px-2 py-1 text-2xs backdrop-blur">
          <span className={`inline-block h-2 w-2 rounded-full ${doorstep ? "bg-leaf" : "bg-warning"}`} aria-hidden="true" />
          <span className="font-medium">{doorstep ? "Doorstep" : "Area only"}</span>
          <span className="text-muted">· {geo.source.replace("_", " ")} · ±{Math.round(geo.accuracy_m ?? 0)} m</span>
        </div>
      ) : (
        <div className="absolute bottom-2 left-2 z-overlay rounded-md border border-hairline bg-surface/90 px-2 py-1 text-2xs text-muted backdrop-blur">
          The pin drops here when an address resolves
        </div>
      )}
    </div>
  );
}

// --- misc -----------------------------------------------------------------------------

export function ageOf(iso: string): string {
  const ms = Date.now() - new Date(iso).getTime();
  const m = Math.max(0, Math.round(ms / 60_000));
  if (m < 1) return "just now";
  if (m < 60) return `${m} min`;
  const h = Math.round(m / 60);
  return h < 48 ? `${h} h` : `${Math.round(h / 24)} d`;
}
