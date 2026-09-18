/**
 * Re-export of the frozen contract, plus the queue shapes.
 *
 * Components import from here, never from `@shared` directly, so the one place
 * the wire format is named in this app is this file.
 */
export type {
  Alternative,
  Clarification,
  Geo,
  GeoSource,
  HealthResponse,
  Landmark,
  Relation,
  ResolveRequest,
  Resolution,
  Status,
  StructuredAddress,
} from "@shared/contract";
export { FIELD_ORDER, confidenceBand, formatDigipin, isDoorstepAccurate } from "@shared/contract";

import type { Clarification, Geo, StructuredAddress } from "@shared/contract";

/** One review-queue case, as `GET /v1/queue` returns it. */
export interface QueueItem {
  order_id: string;
  status: "NEEDS_INFO" | "AMBIGUOUS" | "RESOLVED" | "REJECTED";
  created_at: string;
  updated_at: string;
  confidence: number | null;
  summary: string;
  structured: StructuredAddress;
  clarification: Clarification | null;
  alternatives: unknown[];
  digipin: string | null;
  geo: Geo | null;
  evidence: string[];
  correlation_id: string;
  reviewed_by?: string;
}

export interface QueueResponse {
  items: QueueItem[];
  count: number;
}

export interface FeedbackRequest {
  order_id: string;
  action: "approve" | "edit" | "reject";
  actor?: string;
  edits?: Partial<Record<keyof StructuredAddress, string | null>>;
}
