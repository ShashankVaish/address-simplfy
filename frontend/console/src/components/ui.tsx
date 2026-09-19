/**
 * Small presentational pieces shared by the pages.
 *
 * Each one renders a fact from the contract and refuses to lie about it: a
 * missing field is a dash, a centroid is a shaded circle rather than a pin,
 * and an uncalibrated confidence says so in its tooltip.
 */

import { useEffect, useRef } from "react";
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

// --- status ------------------------------------------------------------------

const STATUS_STYLE: Record<Status, string> = {
  RESOLVED: "bg-leaf/10 text-leaf ring-leaf/30",
  NEEDS_INFO: "bg-saffron/10 text-saffron ring-saffron/30",
  AMBIGUOUS: "bg-red-50 text-red-700 ring-red-200",
};

export function StatusPill({ status }: { status: Status | string }) {
  const style = STATUS_STYLE[status as Status] ?? "bg-gray-100 text-gray-700 ring-gray-200";
  return (
    <span className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-semibold ring-1 ${style}`}>
      {status.replace("_", " ")}
    </span>
  );
}

// --- confidence ---------------------------------------------------------------

export function ConfidenceBar({ value, calibrated }: { value: number; calibrated: boolean }) {
  const band = confidenceBand(value);
  const colour = band === "high" ? "bg-leaf" : band === "medium" ? "bg-saffron" : "bg-red-500";
  return (
    <div title={calibrated ? "Calibrated probability" : "Uncalibrated raw score, not a fitted probability"}>
      <div className="flex items-baseline justify-between">
        <span className="text-xs uppercase tracking-wide text-gray-500">Confidence</span>
        <span className="font-mono text-lg font-semibold">{value.toFixed(2)}</span>
      </div>
      <div className="mt-1 h-2 w-full overflow-hidden rounded-full bg-gray-200">
        <div className={`h-full rounded-full transition-all duration-700 ${colour}`} style={{ width: `${Math.round(value * 100)}%` }} />
      </div>
      {!calibrated && <p className="mt-1 text-[11px] text-gray-500">raw score — calibration not yet fitted</p>}
    </div>
  );
}

// --- structured fields ----------------------------------------------------------

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

export function StructuredPanel({
  structured,
  revealed,
}: {
  structured: StructuredAddress;
  /** Fields to show; undefined shows everything. Drives the stage reveal. */
  revealed?: Set<string>;
}) {
  return (
    <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-sm">
      {FIELD_ORDER.map((field) => {
        const value = structured[field] as string | null;
        const show = !revealed || revealed.has(field);
        return (
          <div key={field} className={`contents transition-opacity ${show ? "opacity-100" : "opacity-0"}`}>
            <dt className="text-gray-500">{LABELS[field]}</dt>
            <dd className={value ? "font-medium" : "text-gray-400"}>{value ?? "—"}</dd>
          </div>
        );
      })}
    </dl>
  );
}

export function LandmarkList({ landmarks, revealed }: { landmarks: Landmark[]; revealed?: boolean }) {
  if (!landmarks.length) return <p className="text-sm text-gray-400">No landmarks mentioned.</p>;
  return (
    <ul className={`space-y-1.5 text-sm transition-opacity ${revealed === false ? "opacity-0" : "opacity-100"}`}>
      {landmarks.map((lm, i) => (
        <li key={i} className="flex items-center gap-2">
          <span className="w-16 shrink-0 text-xs uppercase tracking-wide text-gray-500">{lm.relation}</span>
          <span className="font-medium">{lm.name}</span>
          {lm.matched_id ? (
            <span className="rounded bg-leaf/10 px-1.5 py-0.5 font-mono text-[11px] text-leaf" title={`${lm.matched_id}${lm.distance_m != null ? ` · ${Math.round(lm.distance_m)} m` : ""}`}>
              matched
            </span>
          ) : (
            <span className="rounded bg-gray-100 px-1.5 py-0.5 text-[11px] text-gray-500" title="Not in the landmark graph yet — this address will seed it">
              unmatched
            </span>
          )}
        </li>
      ))}
    </ul>
  );
}

// --- DIGIPIN -------------------------------------------------------------------

export function DigipinBadge({ digipin }: { digipin: string | null }) {
  if (!digipin) return <span className="text-gray-400">—</span>;
  return (
    <div>
      <div className="font-mono text-2xl font-semibold tracking-wider">{formatDigipin(digipin)}</div>
      <div className="text-[11px] text-gray-500">
        DIGIPIN · stored as <span className="font-mono">{digipin}</span> · ~4 m cell
      </div>
    </div>
  );
}

// --- evidence -------------------------------------------------------------------

export function EvidenceList({ evidence }: { evidence: string[] }) {
  return (
    <ol className="space-y-1 text-xs leading-relaxed text-gray-700">
      {evidence.map((line, i) => {
        const warn = /NOT a doorstep|CONFLICT|degraded|penalty|AMBIGUOUS/.test(line);
        return (
          <li key={i} className={`flex gap-2 ${warn ? "text-saffron" : ""}`}>
            <span className="select-none text-gray-400">{i + 1}.</span>
            <span>{line}</span>
          </li>
        );
      })}
    </ol>
  );
}

// --- map -------------------------------------------------------------------------

/**
 * A pin for a doorstep-accurate point; a shaded circle for anything else.
 *
 * The distinction is the whole point of `geo.source`. A pincode centroid drawn
 * as a pin is a lie a rider will believe.
 */
export function MapPin({ geo }: { geo: Geo | null }) {
  const container = useRef<HTMLDivElement>(null);
  const map = useRef<maplibregl.Map | null>(null);
  const marker = useRef<maplibregl.Marker | null>(null);

  useEffect(() => {
    if (!container.current || map.current) return;
    map.current = new maplibregl.Map({
      container: container.current,
      // Public OSM raster tiles for local development. Amazon Location tiles
      // replace this once the stack is deployed (the style URL is the only change).
      style: {
        version: 8,
        sources: { osm: { type: "raster", tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"], tileSize: 256, attribution: "© OpenStreetMap" } },
        layers: [{ id: "osm", type: "raster", source: "osm" }],
      },
      center: [78.9, 22.5],
      zoom: 3.6,
      attributionControl: false,
    });
    map.current.addControl(new maplibregl.AttributionControl({ compact: true }));
    return () => {
      map.current?.remove();
      map.current = null;
    };
  }, []);

  useEffect(() => {
    const m = map.current;
    if (!m) return;
    marker.current?.remove();
    marker.current = null;
    const layerId = "accuracy";
    const apply = () => {
      if (m.getLayer(layerId)) m.removeLayer(layerId);
      if (m.getSource(layerId)) m.removeSource(layerId);
      if (!geo) return;
      const doorstep = isDoorstepAccurate(geo);
      if (doorstep) {
        marker.current = new maplibregl.Marker({ color: "#1f7a4d" }).setLngLat([geo.lng, geo.lat]).addTo(m);
      }
      const radius = geo.accuracy_m ?? (doorstep ? 60 : 3000);
      m.addSource(layerId, { type: "geojson", data: circle(geo.lng, geo.lat, radius) });
      m.addLayer({
        id: layerId,
        type: "fill",
        source: layerId,
        paint: { "fill-color": doorstep ? "#1f7a4d" : "#e87d1e", "fill-opacity": doorstep ? 0.12 : 0.22 },
      });
      m.flyTo({ center: [geo.lng, geo.lat], zoom: doorstep ? 16 : 12.5, duration: 900 });
    };
    if (m.isStyleLoaded()) apply();
    else m.once("load", apply);
  }, [geo]);

  return (
    <div className="relative h-72 w-full overflow-hidden rounded-lg ring-1 ring-gray-200">
      <div ref={container} className="h-full w-full" />
      {geo && (
        <div className="absolute bottom-2 left-2 rounded bg-white/90 px-2 py-1 text-[11px] shadow">
          {isDoorstepAccurate(geo) ? "Doorstep" : "Area only"} · {geo.source.replace("_", " ")} · ±{Math.round(geo.accuracy_m ?? 0)} m
        </div>
      )}
    </div>
  );
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

// --- layout ----------------------------------------------------------------------

export function Card({ title, children, className = "" }: { title?: string; children: React.ReactNode; className?: string }) {
  return (
    <section className={`rounded-xl bg-white p-4 shadow-sm ring-1 ring-gray-200 ${className}`}>
      {title && <h2 className="mb-3 text-xs font-semibold uppercase tracking-wide text-gray-500">{title}</h2>}
      {children}
    </section>
  );
}
