import { expect, test } from "@playwright/test";

/** Error and empty states — the paths the R9 screenshot pass never reached because the mock
 *  always returns data. A dashboard that shows a blank panel when the API is down is worse than
 *  one that says so. */
test("a failing API shows an error, not an empty page", async ({ page }) => {
  await page.route("**/api/v1/**", (r) => r.fulfill({ status: 500, body: '{"detail":"boom"}' }));
  await page.goto("/jobs");
  await expect(page.locator("body")).toContainText(/error|went wrong|failed/i, { timeout: 10_000 });
});

test("an empty job list shows an empty state, not a bare table", async ({ page }) => {
  await page.route("**/api/v1/jobs*", (r) => r.fulfill({ status: 200, contentType: "application/json", body: '{"items":[],"next_cursor":null}' }));
  await page.goto("/jobs");
  await expect(page.locator("body")).toContainText(/no jobs|nothing|get started|add a job/i, { timeout: 10_000 });
});

test("401 tells the user to sign in", async ({ page }) => {
  await page.route("**/api/v1/**", (r) => r.fulfill({ status: 401, body: "{}" }));
  await page.goto("/jobs");
  await expect(page.locator("body")).toContainText(/sign in|signed in/i, { timeout: 10_000 });
});

test("a slow API shows a loading state rather than a blank screen", async ({ page }) => {
  await page.route("**/api/v1/jobs*", async (r) => {
    await new Promise((res) => setTimeout(res, 1500));
    await r.fulfill({ status: 200, contentType: "application/json", body: '{"items":[],"next_cursor":null}' });
  });
  await page.goto("/jobs");
  await expect(page.locator(".skeleton, [aria-busy='true']").first()).toBeVisible({ timeout: 5_000 });
});

// R25: the mock reports a monitoring gap two days old, so the overview must own up to it. Slots
// inside a gap are "unobserved", not missed — the banner is how the customer learns that.
test("overview shows a monitoring gap the platform recorded", async ({ page }) => {
  await page.goto("/");
  const banner = page.getByTestId("monitoring-gap");
  await expect(banner).toContainText("Monitoring gap on our side");
  await expect(banner).toContainText("schedule-generator");
  await expect(banner).toContainText("14 scheduled runs could not be observed and were not alerted on");
});

test("overview stays quiet when there are no monitoring gaps", async ({ page }) => {
  await page.route("**/api/v1/platform/monitoring-gaps", (r) => r.fulfill({ json: { gaps: [], unobserved_slots_30d: 0 } }));
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
  await expect(page.getByTestId("monitoring-gap")).toHaveCount(0);
});
