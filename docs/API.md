# API

OpenAPI: `GET /docs` on the api service. Base `/api/v1`. Cursor pagination on list endpoints (`limit`, `cursor`/`next_cursor`). Rate-limit headers `X-RateLimit-Remaining`, `Retry-After` on 429.

| Area | Endpoints |
|---|---|
| Auth | `GET /auth/login`, `GET /auth/callback`, `POST /auth/logout`, `GET /auth/session`, `POST /auth/orgs`, `POST /auth/switch/{org}` |
| Org | `GET /me`, `GET/POST /workspaces`, `GET/POST/DELETE /api-keys`, `GET /audit-logs` |
| Jobs | `GET/POST /jobs`, `GET/PATCH/DELETE /jobs/{id}`, `GET /jobs/{id}/executions`, `GET /jobs/{id}/next-runs`, `GET /jobs/{id}/impact`, `POST /jobs/schedule/preview` |
| Executions | `GET /executions/{id}`, `GET /executions/{id}/compare/{other}` |
| Heartbeat (ingest) | `GET /ping/{token}`, `GET|POST /heartbeat/{token}/{start|success|fail}`, `POST /api/v1/heartbeat/{token}` |
| Agents | `POST /agents/bootstrap-token`, `GET /agents`, `POST /agents/{id}/rotate|revoke`; ingest: `POST /agent/v1/enroll|discovery|events|metrics|heartbeat`, `POST /agent/v1/k8s/cronjobs|events` |
| Kubernetes | `GET /clusters`, `GET /clusters/{id}/cronjobs?namespace=` |
| Incidents | `GET /incidents`, `GET /incidents/{id}`, `POST /incidents/{id}/ack|resolve|notes` |
| Alerting | `GET/POST/DELETE /alerts/channels`, `POST /alerts/channels/{id}/test`, `GET/POST/PATCH/DELETE /alerts/rules`, `GET/POST/DELETE /alerts/maintenance`, `GET /alerts/ledger` |
| Dependencies / topology | `GET/POST/DELETE /dependencies`, `GET /topology` |
| Logs | `GET /logs/search` |
| Analytics | `GET /analytics/overview|series|jobs|mttr|report` |
| Copilot | `POST /copilot/ask`, `GET /copilot/history|suggestions` |
| Status pages | `GET/POST/DELETE /status-pages`; public `GET /public/status/{slug}` |
| Billing | `GET /billing`, `POST /billing/checkout?plan=`, `POST /billing/portal`, `POST /billing/webhook` |

Heartbeat JSON body: `{"status":"start|success|fail","execution_id"?,"sequence"?,"duration_ms"?,"exit_code"?,"agent_ts"?,"host"?,"stdout_tail"?,"stderr_tail"?,"meta"?}`.
