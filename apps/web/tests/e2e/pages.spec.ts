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
    // Console "Failed to load resource" messages carry no URL, so track responses separately and
    // judge those by host — the sandbox blocks Google Fonts, which must not fail the page.
    const errors: string[] = [];
    const badResponses: string[] = [];
    const external = /fonts\.googleapis|fonts\.gstatic/;
    page.on("console", (m) => {
      if (m.type() !== "error") return;
      if (/Failed to load resource/i.test(m.text())) return;   // covered by badResponses
      errors.push(m.text());
    });
    page.on("pageerror", (e) => errors.push(String(e)));
    page.on("response", (r) => { if (r.status() >= 400 && !external.test(r.url())) badResponses.push(`${r.status()} ${r.url()}`); });

    await page.goto(path);
    await expect(page.locator("main").first()).toContainText(new RegExp(label, "i"), { timeout: 10_000 });
    // The shared ErrorBox renders this; seeing it means a query failed.
    await expect(page.getByText(/Something went wrong|Failed to fetch|Not signed in/i)).toHaveCount(0);
    expect(badResponses, "page made a failing request").toEqual([]);
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
