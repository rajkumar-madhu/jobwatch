/** Base URL for heartbeat and ping calls.
 * Local compose serves ingest on port 8010. A hosted install uses the same
 * origin as the API; the ingress sends /ping and /heartbeat to ingest. */
export function ingestOrigin(apiBase = process.env.NEXT_PUBLIC_API_URL ?? ""): string {
  const raw = apiBase.trim();
  if (!raw) return "";
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    return raw.replace(/\/$/, "");
  }
  const local = url.hostname === "localhost" || url.hostname === "127.0.0.1" || url.hostname === "::1";
  if (local && (url.port === "" || url.port === "8000")) url.port = "8010";
  return url.origin;
}

/** Join an API path onto the public API base without a double slash. */
export function apiUrl(path: string, apiBase = process.env.NEXT_PUBLIC_API_URL ?? ""): string {
  const root = apiBase.trim().replace(/\/$/, "");
  const suffix = path.startsWith("/") ? path : `/${path}`;
  return `${root}${suffix}`;
}
