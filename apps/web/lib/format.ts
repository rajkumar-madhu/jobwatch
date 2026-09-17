import { formatDistanceToNowStrict, format } from "date-fns";
export const ago = (iso: string | null) => (iso ? formatDistanceToNowStrict(new Date(iso), { addSuffix: true }) : "—");
export const ts = (iso: string | null) => (iso ? format(new Date(iso), "MMM d HH:mm:ss") : "—");
export const dur = (ms: number | null) => { if (ms == null) return "—"; if (ms < 1000) return `${ms} ms`; const s = Math.round(ms / 1000); if (s < 90) return `${s}s`; const m = Math.floor(s / 60); return m < 90 ? `${m}m ${s % 60}s` : `${Math.floor(m / 60)}h ${m % 60}m`; };
export const statusLabel: Record<string, string> = { healthy: "Healthy", running: "Running", late: "Late", missed: "Missed", failed: "Failed", timeout: "Timed out", recovered: "Recovered", paused: "Paused", unknown: "No runs yet", success: "Success", scheduled: "Scheduled" };
