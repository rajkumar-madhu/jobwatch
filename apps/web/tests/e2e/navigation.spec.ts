import { expect, test } from "@playwright/test";

test.describe("navigation", () => {
  test("sidebar links reach their pages (desktop)", async ({ page, isMobile }) => {
    test.skip(isMobile, "sidebar is a drawer on mobile — covered separately");
    await page.goto("/");
    for (const [label, path] of [["Jobs", "/jobs"], ["Incidents", "/incidents"], ["Analytics", "/analytics"]] as const) {
      await page.getByRole("link", { name: label, exact: true }).first().click();
      await expect(page).toHaveURL(new RegExp(`${path}$`));
    }
  });

  test("mobile exposes a drawer, not a hidden sidebar", async ({ page, isMobile }) => {
    test.skip(!isMobile, "mobile only");
    await page.goto("/");
    const toggle = page.getByRole("button", { name: /menu|navigation/i }).first();
    await expect(toggle).toBeVisible();
    await toggle.click();
    await expect(page.getByRole("link", { name: "Jobs", exact: true }).first()).toBeVisible();
  });

  test("a job row opens its detail page", async ({ page }) => {
    await page.goto("/jobs");
    await page.getByRole("link", { name: /nightly-database-backup/i }).first().click();
    await expect(page.locator("body")).toContainText(/nightly-database-backup/i);
  });
});
