/** Thin API client. Auth: API key (X-API-Key) from localStorage for now; Keycloak session cookie in Phase 5. */
export const API = process.env.NEXT_PUBLIC_API_URL!;

export function getKey(): string | null { return typeof window === "undefined" ? null : localStorage.getItem("cs_api_key"); }
export function setKey(k: string) { localStorage.setItem("cs_api_key", k); }

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const key = getKey();
  const res = await fetch(`${API}${path}`, { ...init, credentials: "include", headers: { "Content-Type": "application/json", ...(key ? { "X-API-Key": key } : {}), ...(init.headers || {}) } });
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
