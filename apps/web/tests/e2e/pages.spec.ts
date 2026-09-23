import { expect, test } from "@playwright/test";

/**
 * Every app page renders against the mock API without an error state or a console error.
 * This is the frontend equivalent of the backend's R9 route smoke suite: breadth, so a page
 * cannot ship broken, with the detail assertions in the focused specs below.
 */
const PAGES = [
  ["/", "Overview"], ["/jobs", "Jobs"], ["/failures", "Failures"], ["/incidents", "Incidents"],
  ["/logs", "Logs"], ["/kubernetes", "Kubernetes"], ["/topology", "Topology"], ["/analytics", "Analytics"],
  ["/copilot", "Copilot"], ["/alerting", "Alerting"], ["/integrations", "Integrations"],
  ["/agents", "agents"], ["/billing", "Billing"], ["/settings", "Settings"],
] as const;

for (const [path, label] of PAGES) {
  test(`${path} renders`, async ({ page }) => {
    // Console "Failed to load resource" messages carry no URL, so track responses separately.
    const errors: string[] = [];
    const badResponses: string[] = [];
    // R16: the app must make no request outside its own origin and the API. Self-hosted and
    // air-gapped deploys depend on this; R12 had to allow-list Google Fonts, which is now bundled.
    const thirdParty: string[] = [];
    const allowedHosts = new Set([new URL(page.url() === "about:blank" ? "http://localhost:3000" : page.url()).host, "localhost:3000", "localhost:8000", "127.0.0.1:8000"]);
    page.on("request", (r) => {
      const u = new URL(r.url());
      if (u.protocol.startsWith("http") && !allowedHosts.has(u.host)) thirdParty.push(r.url());
    });
    page.on("console", (m) => {
      if (m.type() !== "error") return;
      if (/Failed to load resource/i.test(m.text())) return;   // covered by badResponses
      errors.push(m.text());
    });
    page.on("pageerror", (e) => errors.push(String(e)));
    page.on("response", (r) => { if (r.status() >= 400) badResponses.push(`${r.status()} ${r.url()}`); });

    // Count API calls per path: a render loop that refetches continuously is invisible to every
    // other assertion here. R16 found /logs firing ~113 requests/s per tab (a Date.now() in the
    // query key) — the page looked fine.
    const perPath = new Map<string, number>();
    page.on("request", (r) => {
      const u = new URL(r.url());
      if (u.pathname.startsWith("/api/")) perPath.set(u.pathname, (perPath.get(u.pathname) ?? 0) + 1);
    });

    // Wait for the initial queries to settle rather than for "load": a query that fails after first
    // paint must still fail the test (R16: /logs had 422'd against the mock since R12, hidden
    // because the assertions ran first). Bounded, because the 15s background poll means some pages
    // are never fully idle; the first settle is what matters.
    await page.goto(path, { waitUntil: "domcontentloaded" });
    await page.waitForLoadState("networkidle", { timeout: 8_000 }).catch(() => {});
    await page.waitForTimeout(2_000); // a render loop shows up as a count, not as a timeout
    await expect(page.locator("main").first()).toContainText(new RegExp(label, "i"), { timeout: 10_000 });
    // The shared ErrorBox renders this; seeing it means a query failed.
    await expect(page.getByText(/Something went wrong|Failed to fetch|Not signed in/i)).toHaveCount(0);
    expect(badResponses, "page made a failing request").toEqual([]);
    expect(thirdParty, "page contacted a third-party host").toEqual([]);
    // Up to 3: initial fetch, a React strict-mode/remount refetch, and a retry. A loop is hundreds.
    const hot = [...perPath].filter(([, n]) => n > 3).map(([p, n]) => `${p} x${n}`);
    expect(hot, "page is refetching in a loop").toEqual([]);
    expect(errors.filter((e) => !/favicon|DevTools|Download the React/i.test(e))).toEqual([]);
  });
}

test("public status page renders without auth", async ({ page }) => {
  await page.goto("/status/acme-platform");
  await expect(page.locator("body")).not.toContainText(/Not signed in/i);
});

test("landing page renders", async ({ page }) => {
  await page.goto("/welcome");
  await expect(page.locator("body")).toContainText(/JobWatch/i);
});

// R33: /product — the feature tour. Same rules as every app page: no third-party host, no console
// error, every section reachable from the sticky anchor bar, every "Learn more" points into the app.
test("product page renders with no third-party requests and working anchors", async ({ page }) => {
  const thirdParty: string[] = [];
  page.on("request", (r) => { const h = new URL(r.url()).host; if (!/^localhost:(3000|8000)$/.test(h)) thirdParty.push(r.url()); });
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/product");
  await expect(page.getByRole("heading", { level: 1 })).toContainText(/every scheduled job/i);
  const nav = page.getByRole("navigation", { name: "Product sections" });
  const links = nav.getByRole("link");
  expect(await links.count()).toBe(11);
  for (const id of ["agents", "schedule", "alerting", "incidents", "logs", "analytics", "signals", "status", "copilot", "api", "security"]) {
    await expect(page.locator(`section#${id} h2`)).toBeVisible();
  }
  await nav.getByRole("link", { name: "Security" }).click();
  await expect(page).toHaveURL(/#security$/);
  await expect(page.locator("section#security")).toBeInViewport();
  // every section's call-to-action goes somewhere in the app, not to a dead anchor
  const ctas = await page.locator("section a[href^='/']").evaluateAll((as) => as.map((a) => (a as HTMLAnchorElement).getAttribute("href")));
  expect(ctas.length).toBeGreaterThanOrEqual(11);
  expect(thirdParty).toEqual([]);
  expect(errors).toEqual([]);
});
