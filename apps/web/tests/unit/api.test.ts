/**
 * R12 — lib/api.ts. The CSRF handling here was written in R8 and never executed: if it sends the
 * token on the wrong requests, every cookie write 403s; if it sends it on none, the SPA is broken.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

const CSRF = "csrf-token-abc";

function mockFetch(handler: (url: string, init: RequestInit) => Partial<Response> & { jsonBody?: unknown }) {
  return vi.fn(async (url: string, init: RequestInit = {}) => {
    const r = handler(String(url), init);
    return {
      ok: (r.status ?? 200) < 400, status: r.status ?? 200,
      json: async () => r.jsonBody ?? {}, text: async () => JSON.stringify(r.jsonBody ?? {}),
      ...r,
    } as Response;
  });
}

async function freshApi() {
  vi.resetModules();
  return await import("@/lib/api");
}

beforeEach(() => {
  localStorage.clear();
  vi.unstubAllGlobals();
});

describe("cookie auth", () => {
  it("fetches a CSRF token before the first write and sends it", async () => {
    const calls: { url: string; init: RequestInit }[] = [];
    vi.stubGlobal("fetch", mockFetch((url, init) => {
      calls.push({ url, init });
      if (url.endsWith("/auth/session")) return { jsonBody: { csrf_token: CSRF } };
      return { jsonBody: { ok: true } };
    }));
    const { api } = await freshApi();
    await api("/api/v1/jobs", { method: "POST", body: "{}" });
    expect(calls[0].url).toContain("/auth/session");
    expect((calls[1].init.headers as Record<string, string>)["X-CSRF-Token"]).toBe(CSRF);
  });

  it("does not send a CSRF token on reads", async () => {
    const seen: RequestInit[] = [];
    vi.stubGlobal("fetch", mockFetch((url, init) => {
      seen.push(init);
      return url.endsWith("/auth/session") ? { jsonBody: { csrf_token: CSRF } } : { jsonBody: [] };
    }));
    const { api } = await freshApi();
    await api("/api/v1/jobs");
    expect(seen).toHaveLength(1);
    expect((seen[0].headers as Record<string, string>)["X-CSRF-Token"]).toBeUndefined();
  });

  it("reuses the token instead of refetching on every write", async () => {
    let sessionCalls = 0;
    vi.stubGlobal("fetch", mockFetch((url) => {
      if (url.endsWith("/auth/session")) { sessionCalls++; return { jsonBody: { csrf_token: CSRF } }; }
      return { jsonBody: {} };
    }));
    const { api } = await freshApi();
    await api("/api/v1/jobs", { method: "POST", body: "{}" });
    await api("/api/v1/jobs", { method: "POST", body: "{}" });
    expect(sessionCalls).toBe(1);
  });

  it("refreshes once and retries when the token has expired", async () => {
    let tok = "stale";
    let writes = 0;
    vi.stubGlobal("fetch", mockFetch((url, init) => {
      if (url.endsWith("/auth/session")) { tok = CSRF; return { jsonBody: { csrf_token: CSRF } }; }
      writes++;
      const sent = (init.headers as Record<string, string>)["X-CSRF-Token"];
      return sent === CSRF ? { jsonBody: { ok: true } } : { status: 403, jsonBody: { detail: "bad csrf" } };
    }));
    const { api, setCsrf } = await freshApi();
    setCsrf("stale");
    await expect(api("/api/v1/jobs", { method: "POST", body: "{}" })).resolves.toBeTruthy();
    expect(writes).toBe(2);
    expect(tok).toBe(CSRF);
  });

  it("gives up after one refresh rather than looping", async () => {
    let writes = 0;
    vi.stubGlobal("fetch", mockFetch((url) => {
      if (url.endsWith("/auth/session")) return { jsonBody: { csrf_token: CSRF } };
      writes++;
      return { status: 403, jsonBody: { detail: "still bad" } };
    }));
    const { api } = await freshApi();
    await expect(api("/api/v1/jobs", { method: "POST", body: "{}" })).rejects.toBeTruthy();
    expect(writes).toBe(2);
  });

  it("sends credentials so the session cookie travels", async () => {
    const seen: RequestInit[] = [];
    vi.stubGlobal("fetch", mockFetch((url, init) => {
      seen.push(init);
      return url.endsWith("/auth/session") ? { jsonBody: { csrf_token: CSRF } } : { jsonBody: {} };
    }));
    const { api } = await freshApi();
    await api("/api/v1/jobs");
    expect(seen[0].credentials).toBe("include");
  });
});

describe("api key auth", () => {
  it("sends the key and skips CSRF entirely", async () => {
    const calls: { url: string; init: RequestInit }[] = [];
    vi.stubGlobal("fetch", mockFetch((url, init) => { calls.push({ url, init }); return { jsonBody: {} }; }));
    const { api, setKey } = await freshApi();
    setKey("cs_live_abc");
    await api("/api/v1/jobs", { method: "POST", body: "{}" });
    expect(calls).toHaveLength(1);              // no /auth/session round trip
    const h = calls[0].init.headers as Record<string, string>;
    expect(h["X-API-Key"]).toBe("cs_live_abc");
    expect(h["X-CSRF-Token"]).toBeUndefined();
  });
});

describe("errors", () => {
  it("turns 401 into a sign-in message rather than a raw body", async () => {
    vi.stubGlobal("fetch", mockFetch(() => ({ status: 401, jsonBody: {} })));
    const { api } = await freshApi();
    await expect(api("/api/v1/jobs")).rejects.toMatchObject({ status: 401 });
  });

  it("keeps the token out of localStorage so XSS cannot lift it", async () => {
    vi.stubGlobal("fetch", mockFetch((url) => (url.endsWith("/auth/session") ? { jsonBody: { csrf_token: CSRF } } : { jsonBody: {} })));
    const { api } = await freshApi();
    await api("/api/v1/jobs", { method: "POST", body: "{}" });
    expect(JSON.stringify(localStorage)).not.toContain(CSRF);
  });
});

// R32: mutate() builds the URL from the template + params and sends the body once, JSON-encoded.
describe("mutate()", () => {
  it("substitutes path params, encodes them, and appends query", async () => {
    localStorage.setItem("cs_api_key", "k");
    const calls: [string, RequestInit][] = [];
    vi.stubGlobal("fetch", vi.fn(async (u: string, i: RequestInit) => { calls.push([u, i]); return new Response("{}", { status: 200 }); }));
    const { mutate } = await import("@/lib/api");
    await mutate("/api/v1/alerts/rules/{rule_id}", "patch", { path: { rule_id: "a/b" }, query: { enabled: true } });
    await mutate("/api/v1/dependencies", "delete", { query: { job_id: "j", depends_on_job_id: "d" } });
    await mutate("/api/v1/jobs/{job_id}", "patch", { path: { job_id: "x" }, body: { paused: false } });
    expect(calls[0][0]).toMatch(/\/api\/v1\/alerts\/rules\/a%2Fb\?enabled=true$/);
    expect(calls[0][1].method).toBe("PATCH");
    expect(calls[0][1].body).toBeUndefined();
    expect(calls[1][0]).toMatch(/\/api\/v1\/dependencies\?job_id=j&depends_on_job_id=d$/);
    expect(calls[2][1].body).toBe(JSON.stringify({ paused: false }));
  });
});
