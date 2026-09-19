# Integration harness (R5)

Runs against a real Postgres **as the non-superuser app role**. As a superuser every RLS test
passes vacuously — `test_connected_as_non_superuser` guards against that.

```bash
# one-off local Postgres (any 16+); owner user creates the DB
createdb -O owner cronsentinel
cd apps/api
DATABASE_URL=postgresql+psycopg://owner:pw@localhost/cronsentinel JOBWATCH_APP_DB_PASSWORD=app alembic upgrade head
DATABASE_URL=postgresql+psycopg://jobwatch_app:app@localhost/cronsentinel pytest tests/integration -q
```

Or `docker compose -f infra/docker/docker-compose.yml up migrate` then point DATABASE_URL at the
compose Postgres with the `jobwatch_app` user.

No NATS / Redis / Celery needed: worker `tick()` functions are called directly and the Celery task
runs with `.apply()`; HTTP is patched.

| file | proves |
|---|---|
| test_migrations | head revision, every org_id table has FORCE RLS, app role is not superuser, suite itself runs as app |
| test_slot_lifecycle | generator backfill + horizon + idempotency; overdue → missed + FAILING + synthetic executions; misses survive reconciler downtime; execution binds to slot → OK; late start does not drift `next_expected_at`; paused → skipped/UNKNOWN |
| test_unknown_suppression | dead agent → slots missed but job UNKNOWN/agent_offline; rule engine drops UNKNOWN (no incident) |
| test_outbound | fanout honours event_type filters; delivery is HMAC-signed + ledgered; failure recorded; destinations RLS-isolated |
