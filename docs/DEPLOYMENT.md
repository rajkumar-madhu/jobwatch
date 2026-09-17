# Deployment

## Development
`infra/docker/docker-compose.yml` brings up everything (postgres, redis, nats, keycloak with realm import, api, ingest, web, all workers). Profile `ai` adds Ollama; pull a model with `docker compose exec ollama ollama pull qwen2.5:14b`.

## Production (Kubernetes)
```bash
helm upgrade --install cronsentinel infra/helm/cronsentinel -n cronsentinel --create-namespace -f my-values.yaml
```
- Bring your own PostgreSQL (with a read replica for analytics), Redis, S3/MinIO, SMTP, and Keycloak (or run the realm on a Keycloak you already operate — see `infra/keycloak-realm.json`).
- The chart runs api/ingest/web/workers, a 3-node NATS JetStream StatefulSet, a pre-upgrade Alembic migration Job, Ingress (app + ingest hosts) and an HPA on ingest.
- Put a CDN/WAF in front of the Ingress; rate limits exist in-app too.
- Copilot: point `COPILOT_BASE_URL` at vLLM/Ollama inside the cluster. Nothing leaves your network unless you choose an external provider.

## Environment
See `.env.example`. Required in prod: `DATABASE_URL`, `REDIS_URL`, `NATS_URL`, `SECRET_ENCRYPTION_KEY` (32+ random bytes), `KEYCLOAK_*`, `API_PUBLIC_URL`, `WEB_PUBLIC_URL`, `COOKIE_SECURE=true`.

## Operations
- Metrics: `/metrics` on api (HTTP latency/count). Worker lag gauges: TODO.
- Health: `/healthz`, `/readyz` (DB + NATS).
- Retention: scorer drops executions beyond plan retention hourly (row deletes; switch to partition drops for large tenants — TODO).
- Backups: PostgreSQL + MinIO buckets. NATS stream is a work queue; loss = un-acked events replayed by agents (they buffer on disk).
