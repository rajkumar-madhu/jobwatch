"""R35 — plan_limits.features only lists what the platform can deliver.

0001 seeded business with pagerduty, opsgenie, sms, sso and analytics. None of those exist: the
channel router (_KINDS) refuses the first three, the notifier (_SENDERS) cannot send them, SSO is
a Phase 6 TODO and "analytics" is not a distinct feature. The in-app billing page renders this
array verbatim, so a paying Business org was being shown five things it could not use. Business
keeps what really distinguishes it — 5,000 jobs and 365-day retention — and the same feature set
as Team. Enterprise stays {all}; the router still gates each kind on what it can actually deliver.
"""
from alembic import op

revision = "0015"; down_revision = "0014"; branch_labels = None; depends_on = None

HONEST = "{email,slack,webhook,teams,discord,telegram,ai,kubernetes}"
AS_SEEDED = "{email,slack,webhook,teams,discord,telegram,ai,kubernetes,pagerduty,opsgenie,sms,sso,analytics}"


def upgrade():
    op.execute(f"UPDATE plan_limits SET features = '{HONEST}' WHERE plan = 'business'")


def downgrade():
    op.execute(f"UPDATE plan_limits SET features = '{AS_SEEDED}' WHERE plan = 'business'")
