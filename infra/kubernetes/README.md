# Production Kubernetes deployment
Use the platform Helm chart in `infra/helm/cronsentinel` (values below). Raw manifests are generated with `helm template`.

Topology: Ingress/WAF → `web` (Next.js) + `api` (FastAPI, HPA on CPU) + `ingest` (FastAPI, separate HPA on RPS) → NATS JetStream (3 replicas) →
workers (`exec-processor`, `reconciler`, `rule-engine`, `notifier`, `scorer`) → PostgreSQL 16 (primary+replica, pgBouncer) · Redis · MinIO/S3 · Keycloak · Ollama/vLLM (Copilot).

Minimum external dependencies to bring yourself: PostgreSQL, Redis, object storage, an SMTP relay. Everything else is in the chart.
