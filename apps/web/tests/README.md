# Web tests (R12)

Two layers, both runnable without a database.

## Unit — `npm test` (vitest, jsdom)
`tests/unit/format.test.ts` — duration/timestamp formatting and the status-label map (a status with
no label renders as a raw slug in the UI).
`tests/unit/api.test.ts` — `lib/api.ts`, mostly the CSRF flow added in R8: token fetched before the
first cookie write, reused after that, not sent on reads or with an API key, refreshed exactly once
on a 403, and never written to localStorage.

## End-to-end — `npm run test:e2e` (Playwright, desktop + Pixel 7)
Needs the mock API and a built app:

```bash
cd tools/ui-review && uvicorn mock_api:app --port 8000 &
cd apps/web && npm run build && npm start &
PLAYWRIGHT_BROWSERS_PATH=... npx playwright test
```

- `pages.spec.ts` — all 14 app pages plus the landing and public status pages render with no
  ErrorBox, no page error, and no failing request. Google Fonts is excluded by host: it is blocked
  in sandboxes, and the point of the fallback stack is that the page still renders without it.
- `navigation.spec.ts` — sidebar links on desktop, the hamburger drawer on mobile, job row → detail.
- `states.spec.ts` — the paths the mock never produces: API 500 shows an error, an empty list shows
  an empty state, 401 says sign in, a slow response shows a skeleton.

Route-level interception (`page.route`) is used for the failure states, so no server-side fixture
juggling is needed.
