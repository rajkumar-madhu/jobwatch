/** Thin API client. Auth: API key (X-API-Key) from localStorage for now; Keycloak session cookie in Phase 5. */
export const API = process.env.NEXT_PUBLIC_API_URL!;

export function getKey(): string | null { return typeof window === "undefined" ? null : localStorage.getItem("cs_api_key"); }
export function setKey(k: string) { localStorage.setItem("cs_api_key", k); }

// R8: cookie-authenticated writes need the double-submit CSRF token from /auth/session.
// Kept in memory only — putting it in localStorage would hand it to any XSS, which is the
// attacker the token is meant to stop.
let csrf: string | null = null;
export function setCsrf(t: string | null) { csrf = t; }
export function getCsrf() { return csrf; }

async function refreshCsrf(): Promise<string | null> {
  try {
    const r = await fetch(`${API}/auth/session`, { credentials: "include" });
    if (!r.ok) return null;
    csrf = (await r.json()).csrf_token ?? null;
    return csrf;
  } catch { return null; }
}

const SAFE = ["GET", "HEAD", "OPTIONS"];

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const key = getKey();
  const method = (init.method ?? "GET").toUpperCase();
  const needsCsrf = !key && !SAFE.includes(method);
  if (needsCsrf && !csrf) await refreshCsrf();
  const send = (tok: string | null) => fetch(`${API}${path}`, { ...init, credentials: "include", headers: {
    "Content-Type": "application/json", ...(key ? { "X-API-Key": key } : {}), ...(tok ? { "X-CSRF-Token": tok } : {}), ...(init.headers || {}) } });
  let res = await send(needsCsrf ? csrf : null);
  if (res.status === 403 && needsCsrf) {   // token expired mid-session: refresh once and retry
    const fresh = await refreshCsrf();
    if (fresh) res = await send(fresh);
  }
  if (res.status === 401) throw new ApiError(401, "Not signed in. Sign in with your account or add an API key in Settings.");
  if (!res.ok) { let msg = res.statusText; try { msg = (await res.json()).detail ?? msg; } catch {} throw new ApiError(res.status, String(msg)); }
  return res.status === 204 ? (undefined as T) : res.json();
}
export class ApiError extends Error { constructor(public status: number, msg: string) { super(msg); } }

// JobStatus stays hand-written: it is a DB enum, not a response field, so the generated types
// widen it to `string`. Narrowing it here keeps exhaustive switches in the UI working.
export type JobStatus = "unknown" | "healthy" | "running" | "late" | "missed" | "failed" | "timeout" | "recovered" | "paused";

// ---------------------------------------------------------------------------
// R15 — types generated from the real API's OpenAPI spec (lib/api-types.ts).
//
// Regenerate after changing any response_model:
//   cd apps/api && python -c "import json;from cronsentinel.main import app;json.dump(app.openapi(),open('../web/openapi.json','w'),indent=2)"
//   cd apps/web && npx openapi-typescript openapi.json -o lib/api-types.ts
// CI does this and fails if the checked-in file is stale, so the types cannot drift from the API.
//
// Use the aliases below rather than hand-written interfaces: `api<Job>("/api/v1/jobs/…")` is then
// checked against what the backend actually declares, and a removed or renamed field becomes a
// compile error instead of `undefined` at runtime.
//
// NOTE: only response *shapes* are covered. Endpoints without a response_model fall back to
// `unknown` here — that is deliberate, so an unmodelled endpoint is visibly untyped.
// ---------------------------------------------------------------------------
import type { components } from "./api-types";

type S = components["schemas"];

export type Job = S["JobOut"];
export type JobPage = S["JobPage"];
export type MonitoringGaps = S["MonitoringGapsOut"];
export type Execution = S["ExecutionOut"];
export type Incident = S["IncidentOut"];
export type Channel = S["ChannelOut"];
export type AlertRule = S["AlertRuleOut"];
export type StatusPage = S["StatusPageOut"];
export type Workspace = S["WorkspaceOut"];
export type Overview = S["OverviewOut"];
export type DependencyGraph = S["DependencyGraph"];
export type Agent = S["AgentOut"];
export type Cluster = S["ClusterOut"];
export type Topology = S["TopologyOut"];
export type LogSearch = S["LogSearchOut"];
export type Series = S["SeriesOut"];
export type JobAnalytics = S["JobAnalyticsOut"];
export type SignalDestination = S["SignalDestinationOut"];
export type SignalDelivery = S["SignalDeliveryOut"];
export type SignalSchema = S["SignalSchemaOut"];
