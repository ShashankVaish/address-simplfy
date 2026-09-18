/**
 * Contract-shaped fixtures, so the console runs before the backend is deployed.
 *
 * The shapes here are typed against the frozen contract: if the contract
 * changes, this file fails to compile, which is the point. The values are the
 * README's running example -- the Ramesh Nagar address -- so what a developer
 * sees in mock mode is what the demo video will show.
 */

import type { QueueItem, QueueResponse, Resolution } from "./types";

const wait = (ms: number) => new Promise((r) => setTimeout(r, ms));

export const MOCK_RESOLVED: Resolution = {
  status: "RESOLVED",
  confidence: 0.91,
  structured: {
    building: "14",
    street: null,
    sub_locality: null,
    locality: "Ramesh Nagar",
    city: "New Delhi",
    district: "West",
    state: "Delhi",
    pincode: "110015",
    landmarks: [
      { name: "Shiv Mandir", relation: "behind", matched_id: "LMK#DL#SHIV", match_score: 0.89, distance_m: 63 },
      { name: "Gupta General Store", relation: "near", matched_id: null, match_score: null, distance_m: null },
    ],
  },
  geo: { lat: 28.6524, lng: 77.1206, source: "landmark_graph", accuracy_m: 78 },
  digipin: "39JJTT4565",
  evidence: [
    "stripped 1 phone number(s) before any model or embedding call",
    "pincode 110015 found in gazetteer -> Ramesh Nagar, West, Delhi",
    "locality 'Ramesh Nagar' in text matches pincode 110015",
    "house number '14' matched deterministically",
    "landmark candidates: behind 'shiv mandir', near 'gupta general store'",
    "landmark 'Shiv Mandir' matched LMK#DL#SHIV at 63 m (fused 0.89 from bm25, geo, knn; seen 47x)",
    "geocoded from landmark graph: LMK#DL#SHIV ('Shiv Mandir', seen 47x), accuracy ~78 m",
    "DIGIPIN 39JJTT4565 computed locally from the coordinate",
    "confidence 0.91 from raw 0.84 (isotonic, 12 knots)",
    "RESOLVED: confidence 0.91 is at or above the 0.80 threshold",
  ],
  clarification: null,
  alternatives: [],
  correlation_id: "mock-0001",
  timings_ms: { s0_normalise: 1.1, s1_parse: 0.4, s2_retrieve: 44.8, s3_structure: 612.0, s4_geocode: 0.1, s5_digipin: 0.0, s6_confidence: 0.2, s7_decide: 0.1 },
  cached: false,
};

export const MOCK_NEEDS_INFO: Resolution = {
  ...MOCK_RESOLVED,
  status: "NEEDS_INFO",
  confidence: 0.55,
  structured: { ...MOCK_RESOLVED.structured, building: null },
  geo: { lat: 28.659194, lng: 77.135639, source: "pincode_centroid", accuracy_m: 3000 },
  digipin: "39JJTT4565",
  evidence: [
    "pincode 110015 found in gazetteer -> Ramesh Nagar, West, Delhi",
    "no landmark matched, so the graph could not place this",
    "fell back to the centroid of pincode 110015 (7 offices, accuracy ~3.0 km)",
    "this is a pincode centroid, NOT a doorstep -- it must not be presented as a delivery destination",
    "confidence 0.55 from raw 0.55 (uncalibrated (identity): raw score, not a fitted probability)",
    "NEEDS_INFO: confidence 0.55 is below the 0.80 threshold; asking exactly one question, about 'building', in hi-IN",
  ],
  clarification: {
    field: "building",
    question: "Ramesh Nagar mil gaya. Flat ya makaan number kya hai?",
    language: "hi-IN",
    expected_gain: 0.28,
  },
  correlation_id: "mock-0002",
};

/** Pick a fixture from the text so the mock feels responsive to input. */
export async function mockResolve(raw: string): Promise<Resolution> {
  // A believable delay, long enough to see the stage-by-stage reveal.
  await wait(650);
  const looksComplete = /\b(h\.?\s*no|house|flat|plot)\b/i.test(raw) && /\d{6}/.test(raw);
  const base = looksComplete ? MOCK_RESOLVED : MOCK_NEEDS_INFO;
  const devanagari = /[ऀ-ॿ]/.test(raw);
  return {
    ...base,
    correlation_id: `mock-${Date.now().toString(36)}`,
    clarification:
      base.clarification && !devanagari
        ? { ...base.clarification, language: "en-IN", question: "We found Ramesh Nagar. What is the flat or house number?" }
        : base.clarification,
  };
}

const QUEUE: QueueItem[] = [
  {
    order_id: "ORD-10021",
    status: "NEEDS_INFO",
    created_at: "2026-09-18T09:12:04Z",
    updated_at: "2026-09-18T09:12:04Z",
    confidence: 0.55,
    summary: "Ramesh Nagar, New Delhi, 110015",
    structured: MOCK_NEEDS_INFO.structured,
    clarification: MOCK_NEEDS_INFO.clarification,
    alternatives: [],
    digipin: "39JJTT4565",
    geo: MOCK_NEEDS_INFO.geo,
    evidence: MOCK_NEEDS_INFO.evidence,
    correlation_id: "mock-q1",
  },
  {
    order_id: "ORD-10022",
    status: "AMBIGUOUS",
    created_at: "2026-09-18T09:40:31Z",
    updated_at: "2026-09-18T09:40:31Z",
    confidence: 0.62,
    summary: "behind Shiv Mandir",
    structured: { ...MOCK_RESOLVED.structured, pincode: null, locality: null, city: null, district: null, state: null },
    clarification: null,
    alternatives: [{}, {}],
    digipin: null,
    geo: null,
    evidence: [
      "AMBIGUOUS: the text names 'Shiv Mandir' (affinity 0.95) and 'Shiv Mandir' (affinity 0.95) about equally well, but they are 1,150,000 m apart; both are returned rather than guessing",
    ],
    correlation_id: "mock-q2",
  },
];

export async function mockQueue(): Promise<QueueResponse> {
  await wait(200);
  const open = QUEUE.filter((q) => q.status === "NEEDS_INFO" || q.status === "AMBIGUOUS");
  return { items: open, count: open.length };
}

export async function mockFeedback(order_id: string, action: string): Promise<QueueItem> {
  await wait(150);
  const item = QUEUE.find((q) => q.order_id === order_id);
  if (!item) throw new Error(`unknown order ${order_id}`);
  item.status = action === "reject" ? "REJECTED" : "RESOLVED";
  item.updated_at = new Date().toISOString();
  item.reviewed_by = "you";
  return item;
}
