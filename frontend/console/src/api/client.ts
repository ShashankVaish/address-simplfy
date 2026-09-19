/**
 * Typed fetch wrapper. Mock when `VITE_API_URL` is unset, live otherwise.
 *
 * Every function returns the contract type and nothing else, so a component
 * cannot tell whether it is talking to the mock or to `ap-south-1` -- which is
 * exactly what lets the console be built before the backend exists.
 */

import { mockAuthorize, mockFeedback, mockHealth, mockQueue, mockResolve } from "./mock";
import type { AuthorizeRequest, AuthzDecision, FeedbackRequest, HealthResponse, QueueItem, QueueResponse, ResolveRequest, Resolution } from "./types";

const BASE = (import.meta.env.VITE_API_URL as string | undefined)?.replace(/\/+$/, "") ?? "";

export const isMock = BASE === "";

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
    public readonly detail?: unknown,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { "content-type": "application/json", ...(init?.headers ?? {}) },
  });
  const text = await response.text();
  let body: unknown = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = text;
  }
  if (!response.ok) {
    const message =
      typeof body === "object" && body !== null && "error" in body
        ? String((body as { error: unknown }).error)
        : `HTTP ${response.status}`;
    throw new ApiError(response.status, message, body);
  }
  return body as T;
}

export function resolve(req: ResolveRequest): Promise<Resolution> {
  if (isMock) return mockResolve(req.raw);
  return request<Resolution>("/v1/resolve", { method: "POST", body: JSON.stringify(req) });
}

export function queue(limit = 50): Promise<QueueResponse> {
  if (isMock) return mockQueue();
  return request<QueueResponse>(`/v1/queue?limit=${limit}`);
}

export async function feedback(req: FeedbackRequest): Promise<QueueItem> {
  if (isMock) return mockFeedback(req.order_id, req.action);
  const body = await request<{ order: QueueItem }>("/v1/feedback", { method: "POST", body: JSON.stringify(req) });
  return body.order;
}

export function authorize(req: AuthorizeRequest): Promise<AuthzDecision> {
  if (isMock) return mockAuthorize(req);
  return request<AuthzDecision>("/v1/authorize", { method: "POST", body: JSON.stringify(req) });
}

export function health(): Promise<HealthResponse> {
  if (isMock) return mockHealth();
  return request<HealthResponse>("/health");
}
