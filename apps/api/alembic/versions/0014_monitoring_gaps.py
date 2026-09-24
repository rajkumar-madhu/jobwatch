"""R25 — monitoring gaps: an outage of ours must never page a customer.

Before: after a schedule-generator outage every affected job got up to BACKFILL_LIMIT (2 days) of
past slots at once; the reconciler saw their deadlines were in the past and marked them all
missed — one alert per slot per job, i.e. an alert storm for a fault on our side. Two distinct
mechanisms produced it:

1. While no slot existed, the agent's execution reports were recorded *unattached* (attach_slot
   only binds to open slots). The backfilled slot then looked empty even though the run happened.
   Fix (reconciler.retro_match): before any missed judgement, bind unattached terminal executions
   to still-open slots by the same 90 s / deadline window the live path uses.

2. Whatever is still unmatched inside a period where the platform was not watching cannot be
   called missed: nobody was there to see it. Those slots become `unobserved` — a new, terminal,
   non-alerting slot state. Never a synthetic execution: it is not an observation.

Gap detection: each worker upserts platform_heartbeats.last_seen every pass. On its first pass
after a break longer than its gap threshold it writes one monitoring_gaps row for [last_seen, now).
Both the generator and the reconciler do this, and the reconciler treats a *currently* silent
generator as an open gap too, so it does not matter which process comes back first.

No org_id on either table: platform-level facts, readable by any authenticated user (the API
exposes them so a customer can see why a slot is unobserved).

Note: ALTER TYPE ... ADD VALUE cannot be used in the same transaction it is added in (PostgreSQL),
which is why nothing here references 'unobserved' after adding it.
"""
from alembic import op

revision = "0014"; down_revision = "0013"; branch_labels = None; depends_on = None


def upgrade():
    op.execute("ALTER TYPE expected_run_state ADD VALUE IF NOT EXISTS 'unobserved'")
    op.execute("""
    CREATE TABLE platform_heartbeats (
        service text PRIMARY KEY,
        last_seen timestamptz NOT NULL DEFAULT now());
    CREATE TABLE monitoring_gaps (
        id bigserial PRIMARY KEY,
        service text NOT NULL,
        started_at timestamptz NOT NULL,
        ended_at timestamptz NOT NULL,
        slots_unobserved integer NOT NULL DEFAULT 0,
        recorded_at timestamptz NOT NULL DEFAULT now(),
        CHECK (ended_at > started_at));
    CREATE INDEX monitoring_gaps_window_idx ON monitoring_gaps (started_at, ended_at);
    GRANT SELECT ON platform_heartbeats, monitoring_gaps TO jobwatch_app, jobwatch_system;
    GRANT INSERT, UPDATE, DELETE ON platform_heartbeats, monitoring_gaps TO jobwatch_system;
    GRANT USAGE, SELECT ON SEQUENCE monitoring_gaps_id_seq TO jobwatch_system;
    """)


def downgrade():
    # Enum values cannot be removed; 'unobserved' stays in the type. Rows using it must be
    # re-labelled before downgrading further, or they block nothing but are unreadable by old code.
    op.execute("UPDATE expected_runs SET state='skipped' WHERE state='unobserved'")
    op.execute("DROP TABLE IF EXISTS monitoring_gaps; DROP TABLE IF EXISTS platform_heartbeats;")
