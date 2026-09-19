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

export type JobStatus = "unknown" | "healthy" | "running" | "late" | "missed" | "failed" | "timeout" | "recovered" | "paused";
export interface Job { id: string; workspace_id: string; name: string; description: string | null; kind: string; schedule_expr: string | null; schedule_human: string | null;
  tz: string; expected_runtime_s: number | null; grace_s: number; tags: string[]; status: JobStatus; paused: boolean; last_run_at: string | null;
  last_status: string | null; next_expected_at: string | null; reliability_score: number | null; heartbeat_token: string; }
export interface Execution { id: string; job_id: string; status: string; scheduled_ts: string; agent_ts_start: string | null; agent_ts_end: string | null;
  duration_ms: number | null; exit_code: number | null; host: string | null; skew_ms: number; }
export interface Overview { total_jobs: number; by_status: Record<string, number>; executions_today: number; success_rate_today: number | null;
  top_slowest_7d: { name: string; p95_ms: number }[]; top_failing_7d: { name: string; failures: number }[]; }
export interface Incident { id: string; severity: string; status: string; title: string; started_at: string; acknowledged_at: string | null; resolved_at: string | null;
  affected_job_ids: string[]; job_names: string[] | null; root_cause: string | null; resolution: string | null; }
