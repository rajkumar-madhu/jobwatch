# UI review without a database
```bash
cd apps/api && pip install -e . && cd ../../tools/ui-review && uvicorn mock_api:app --port 8000   # demo data, no auth
cd apps/web && npm install && npm run dev                                                        # Settings → paste any API key
```
Screenshots in docs/screenshots/ were produced this way (Playwright, 1440×900 and 390×844).
