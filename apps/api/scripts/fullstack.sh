#!/usr/bin/env bash
# R17 — run the whole JobWatch backend as local processes, mirroring infra/docker/docker-compose.yml
# service-for-service, for the full-chain test in tests/fullstack/.
#
# Why not compose: this is what CI/sandboxes without Docker can run, and it exercises the same
# entrypoints (uvicorn apps, `python -m cronsentinel.workers.*`, celery). Compose on a real host is
# still the deployment proof — see docs/DEVELOPMENT.md R17 for what this does and does not cover.
#
# Needs Postgres (migrated, jobwatch_app role), Redis and NATS JetStream already listening.
#   apps/api/scripts/fullstack.sh up | down | status
set -euo pipefail
RUN=${JOBWATCH_RUN_DIR:-/tmp/jobwatch-fullstack}
API_PORT=${API_PORT:-18000}
INGEST_PORT=${INGEST_PORT:-18010}
cd "$(dirname "$0")/.."

: "${DATABASE_URL:?set DATABASE_URL (jobwatch_app role, not a superuser — RLS must be on)}"
: "${SECRET_ENCRYPTION_KEY:?set SECRET_ENCRYPTION_KEY}"
export REDIS_URL=${REDIS_URL:-redis://localhost:6379/0} NATS_URL=${NATS_URL:-nats://localhost:4222}

# name|command — one per compose service (keycloak/web/scorer excluded: not on the alert path)
SERVICES=(
  "api|uvicorn cronsentinel.main:app --host 127.0.0.1 --port $API_PORT"
  "ingest|uvicorn cronsentinel.ingest_main:app --host 127.0.0.1 --port $INGEST_PORT"
  "exec-processor|python -m cronsentinel.workers.exec_processor"
  "schedule-generator|python -m cronsentinel.workers.schedule_generator"
  "reconciler|python -m cronsentinel.workers.reconciler"
  "rule-engine|python -m cronsentinel.workers.rule_engine"
  "outbound-exporter|python -m cronsentinel.workers.outbound_exporter"
  "notifier|celery -A cronsentinel.celery_app.celery worker -Q notify -l info --concurrency 2 --pool threads"
)

up() {
  mkdir -p "$RUN"
  for s in "${SERVICES[@]}"; do
    name=${s%%|*}; cmd=${s#*|}
    if [[ -f "$RUN/$name.pid" ]] && kill -0 "$(cat "$RUN/$name.pid")" 2>/dev/null; then echo "$name already running"; continue; fi
    setsid bash -c "exec $cmd" >"$RUN/$name.log" 2>&1 </dev/null &
    echo $! >"$RUN/$name.pid"
    echo "started $name (pid $!)"
  done
  for url in "http://127.0.0.1:$API_PORT/healthz" "http://127.0.0.1:$INGEST_PORT/healthz"; do
    for _ in $(seq 1 40); do curl -sf "$url" >/dev/null && break; sleep 0.5; done
    curl -sf "$url" >/dev/null || { echo "not healthy: $url — see $RUN/*.log"; exit 1; }
  done
  echo "up — logs in $RUN"
}

down() {
  for s in "${SERVICES[@]}"; do
    name=${s%%|*}; f="$RUN/$name.pid"
    [[ -f $f ]] && { kill "$(cat "$f")" 2>/dev/null || true; rm -f "$f"; echo "stopped $name"; }
  done
}

status() {
  for s in "${SERVICES[@]}"; do
    name=${s%%|*}; f="$RUN/$name.pid"
    if [[ -f $f ]] && kill -0 "$(cat "$f")" 2>/dev/null; then echo "UP   $name"; else echo "DOWN $name"; fi
  done
}

"${1:-status}"
