import { defineConfig, devices } from "@playwright/test";

/** R12 — e2e against the mock API in tools/ui-review (no DB, deterministic data).
 *  Start both first, or let webServer do it:
 *    uvicorn mock_api:app --port 8000   (from tools/ui-review)
 *    npm run build && npm start         (from apps/web) */
export default defineConfig({
  testDir: "tests/e2e",
  timeout: 30_000,
  expect: { timeout: 8_000 },
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: { baseURL: process.env.WEB_URL ?? "http://localhost:3000", trace: "retain-on-failure" },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } } },
    { name: "mobile", use: { ...devices["Pixel 7"] } },
  ],
});
