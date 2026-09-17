# WeCrew JobWatch

Multi-tenant SaaS for monitoring scheduled jobs — Linux cron, systemd timers, Kubernetes CronJobs, containers, CI pipelines, backups, scripts — with schedule-aware failure detection, alerting, incident correlation and an AI operations copilot.

| Doc | What |
|---|---|
| [docs/CRONSENTINEL_PLATFORM.md](docs/CRONSENTINEL_PLATFORM.md) | Product + platform design, decision log D1–D16 |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Services, data flow, request paths |
| [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) | docker-compose (dev) and Helm (prod) |
| [docs/SECURITY.md](docs/SECURITY.md) | Tenant isolation, RBAC, secrets, agent posture |
| [docs/AGENT.md](docs/AGENT.md) | Linux + Kubernetes agents |
| [docs/API.md](docs/API.md) | REST surface, auth, conventions |
| [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) | Per-phase build notes, smoke tests, known stubs |

## Quick start (dev)
```bash
cp .env.example .env
docker compose -f infra/docker/docker-compose.yml up -d --build          # add --profile ai for Ollama
docker compose -f infra/docker/docker-compose.yml exec api alembic upgrade head
docker compose -f infra/docker/docker-compose.yml exec api python -m cronsentinel.cli bootstrap --org acme --email you@example.com
docker compose -f infra/docker/docker-compose.yml exec api python -m cronsentinel.seed --org <org_id>
open http://localhost:3000/welcome      # dashboard at /, paste API key in Settings or sign in via Keycloak
```

## Repository
```
apps/api            FastAPI + SQLAlchemy + Alembic + Celery · workers: exec_processor, reconciler, rule_engine, scorer
apps/web            Next.js 15 dashboard, landing, onboarding, status pages
agents/linux-agent  Go, stdlib only · agents/k8s-agent  Go, client-go
infra/docker · infra/helm/{cronsentinel,cronsentinel-agent} · infra/keycloak-realm.json
docs/ · .github/workflows/ci.yml
```
Status: functional end-to-end skeleton of every spec section; see "Known stubs" in DEVELOPMENT.md before treating any area as production-complete.
