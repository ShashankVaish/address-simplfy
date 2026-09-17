/**
 * The `/v1/resolve` wire contract. FROZEN.
 *
 * This file and `backend/layers/common/python/patasetu/models.py` describe the
 * same payload and must change together. Freezing it is the single decision
 * that lets four people work in parallel instead of blocking each other: the
 * console builds against `mock.ts` shaped by these types while the backend is
 * still being written.
 *
 * Changing the shape after Thursday blocks three people to unblock one. If a
 * field turns out to be missing, add an optional one; do not rename or retype
 * what is already here.
 */

/** The three outcomes of a resolution. There is no fourth. */
export type Status = "RESOLVED" | "NEEDS_INFO" | "AMBIGUOUS";

/**
 * Where a coordinate came from.
 *
 * The UI must branch on this. `pincode_centroid` carries kilometres of
 * uncertainty and may never be drawn as a doorstep pin -- see
 * `isDoorstepAccurate`.
 */
export type GeoSource = "landmark_graph" | "geocoder" | "pincode_centroid";

/**
 * How a landmark relates to the address.
 *
 * Carries real information: "behind Shiv Mandir" and "opposite Shiv Mandir" are
 * different doorsteps, so the relation is rendered, not dropped.
 */
export type Relation =
  | "near"
  | "behind"
  | "opposite"
  | "above"
  | "beside"
  | "inside";

export interface Landmark {
  name: string;
  relation: Relation;
  /** Landmark-graph id, or null when nothing in the graph fitted. */
  matched_id: string | null;
  /** Fused RRF score of the match. Null when unmatched. */
  match_score: number | null;
  /** Metres between the matched landmark and the resolved point. */
  distance_m: number | null;
}

/**
 * The parsed address.
 *
 * Every field is nullable, and `null` means "the input did not contain this".
 * It never means an empty string and never a guess. Render a dash, not a
 * placeholder value.
 */
export interface StructuredAddress {
  building: string | null;
  street: string | null;
  sub_locality: string | null;
  locality: string | null;
  city: string | null;
  district: string | null;
  state: string | null;
  /** Six digits, first digit 1-8. */
  pincode: string | null;
  landmarks: Landmark[];
}

export interface Geo {
  lat: number;
  lng: number;
  source: GeoSource;
  /** Radius within which the true point is expected to lie. */
  accuracy_m: number | null;
}

export interface Clarification {
  /** The single field this question targets. */
  field: string;
  question: string;
  /** "hi-IN" when the incoming address was Devanagari, else "en-IN". */
  language: string;
  /** Confidence we expect to reach if this is answered. */
  expected_gain: number | null;
}

/** A competing reading, surfaced rather than silently discarded. */
export interface Alternative {
  structured: StructuredAddress;
  geo: Geo | null;
  confidence: number;
  reason: string;
}

export interface Resolution {
  status: Status;
  /** Calibrated confidence in [0,1]. */
  confidence: number;
  structured: StructuredAddress;
  geo: Geo | null;
  /**
   * Continuous 10-character DIGIPIN, no separators -- the official encoder
   * rejects hyphens. Use `formatDigipin` for display only.
   */
  digipin: string | null;
  /**
   * Human-readable reasons for each major decision. Not debug output: this is
   * what an ops analyst reads to decide whether to trust the result, and what
   * the playground renders beside the fields.
   */
  evidence: string[];
  /** Present if and only if status is NEEDS_INFO. */
  clarification: Clarification | null;
  /** At least two entries if and only if status is AMBIGUOUS. */
  alternatives: Alternative[];
  correlation_id: string | null;
  /** Per-stage milliseconds, keyed "s0_normalise", "s1_parse", and so on. */
  timings_ms: Record<string, number>;
  /** True when the exact-hash cache answered without running the pipeline. */
  cached: boolean;
}

export interface ResolveRequest {
  raw: string;
  /** Optional GPS hint. Constrains retrieval only; never returned as the answer. */
  hint?: { lat: number; lng: number };
  order_id?: string;
}

export interface HealthResponse {
  status: "ok";
  provider: "aws" | "local";
  serving_stack: "A" | "B" | "C" | "D" | "E";
  calibration: string;
  gazetteer: Record<string, number>;
  init_ms: number;
}

/** The scalar fields, in the order they should be displayed. */
export const FIELD_ORDER: readonly (keyof StructuredAddress)[] = [
  "building",
  "street",
  "sub_locality",
  "locality",
  "city",
  "district",
  "state",
  "pincode",
] as const;

/**
 * False for a pincode centroid, whatever the confidence.
 *
 * A centroid drawn as a pin is a lie a rider will believe, so the map component
 * must use this to choose between a pin and a shaded circle of `accuracy_m`.
 */
export function isDoorstepAccurate(geo: Geo | null): boolean {
  return geo !== null && geo.source !== "pincode_centroid";
}

/**
 * Hyphenate a DIGIPIN for display: "XXXXXXXXXX" -> "XXX-XXX-XXXX".
 *
 * Display only. Never send this form back to the API or store it: the official
 * India Post decoder rejects any input containing hyphens.
 */
export function formatDigipin(digipin: string | null): string | null {
  if (!digipin) return null;
  const pin = digipin.replace(/[\s-]/g, "").toUpperCase();
  if (pin.length !== 10) return digipin;
  return `${pin.slice(0, 3)}-${pin.slice(3, 6)}-${pin.slice(6, 10)}`;
}

/** Colour band for the confidence bar. Thresholds match `config.Thresholds`. */
export function confidenceBand(confidence: number): "high" | "medium" | "low" {
  if (confidence >= 0.8) return "high";
  if (confidence >= 0.5) return "medium";
  return "low";
}
