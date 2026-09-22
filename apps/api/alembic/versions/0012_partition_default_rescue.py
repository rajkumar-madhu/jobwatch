"""R23 — ensure_month_partition must not fail forever when the default partition holds rows.

Found in R23: once any row lands in <parent>_default for a month that has no partition yet,
`CREATE TABLE … PARTITION OF … FOR VALUES FROM … TO …` fails with CheckViolation ("updated
partition constraint for default partition would be violated"). Every later attempt fails the
same way. Two ordinary ways to get there: the scorer (which creates next month's partition) is
down across a month boundary, or a single agent with a skewed clock reports a run dated months
ahead. Before R23 partition creation also ran inside the scorer's one transaction, so the failure
stopped scoring, usage metering and retention too, permanently.

New behaviour: if the default partition has rows for the month, create the table standalone,
move those rows into it, then ATTACH it. Otherwise create it as a partition directly, as before.
The move locks the default partition for its duration; it only happens in the recovery case.

Also adds drop_partition_if_empty(): the app and system roles are not table owners, so they cannot
DROP a partition. Found by the R23 tests — without it the scorer's partition-drop phase would fail
every hour. The function is SECURITY DEFINER and deliberately narrow: only month partitions
(<parent>_YYYYMM) of the three partitioned tables, only when empty, never the current or previous
month.
"""
from alembic import op

revision = "0012"; down_revision = "0011"; branch_labels = None; depends_on = None

FN = r"""
CREATE OR REPLACE FUNCTION ensure_month_partition(parent TEXT, month DATE) RETURNS void AS $$
DECLARE
  part TEXT := parent || '_' || to_char(month, 'YYYYMM');
  dflt TEXT := parent || '_default';
  start_d DATE := date_trunc('month', month)::date;
  end_d DATE := (date_trunc('month', month) + interval '1 month')::date;
  keycol TEXT;
  stranded BOOLEAN := false;
BEGIN
  IF EXISTS (SELECT 1 FROM pg_class WHERE relname = part) THEN
    RETURN;
  END IF;
  SELECT a.attname INTO keycol
    FROM pg_partitioned_table pt JOIN pg_attribute a ON a.attrelid = pt.partrelid AND a.attnum = pt.partattrs[0]
   WHERE pt.partrelid = parent::regclass;
  IF EXISTS (SELECT 1 FROM pg_class WHERE relname = dflt) THEN
    EXECUTE format('SELECT EXISTS (SELECT 1 FROM %I WHERE %I >= %L AND %I < %L)', dflt, keycol, start_d, keycol, end_d) INTO stranded;
  END IF;
  IF stranded THEN
    -- Rescue path: rows for this month are parked in the default partition.
    EXECUTE format('CREATE TABLE %I (LIKE %I INCLUDING DEFAULTS INCLUDING CONSTRAINTS)', part, parent);
    EXECUTE format('WITH moved AS (DELETE FROM %I WHERE %I >= %L AND %I < %L RETURNING *) INSERT INTO %I SELECT * FROM moved',
                   dflt, keycol, start_d, keycol, end_d, part);
    EXECUTE format('ALTER TABLE %I ATTACH PARTITION %I FOR VALUES FROM (%L) TO (%L)', parent, part, start_d, end_d);
    RAISE NOTICE 'ensure_month_partition: moved stranded rows from % into %', dflt, part;
  ELSE
    EXECUTE format('CREATE TABLE %I PARTITION OF %I FOR VALUES FROM (%L) TO (%L)', part, parent, start_d, end_d);
  END IF;
  EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON %I TO jobwatch_app', part);
  EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON %I TO jobwatch_system', part);
END $$ LANGUAGE plpgsql SECURITY DEFINER;
"""

OLD = r"""
CREATE OR REPLACE FUNCTION ensure_month_partition(parent TEXT, month DATE) RETURNS void AS $$
DECLARE
  part TEXT := parent || '_' || to_char(month, 'YYYYMM');
  start_d DATE := date_trunc('month', month)::date;
  end_d DATE := (date_trunc('month', month) + interval '1 month')::date;
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_class WHERE relname = part) THEN
    EXECUTE format('CREATE TABLE %I PARTITION OF %I FOR VALUES FROM (%L) TO (%L)', part, parent, start_d, end_d);
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON %I TO jobwatch_app', part);
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON %I TO jobwatch_system', part);
  END IF;
END $$ LANGUAGE plpgsql SECURITY DEFINER;
"""


DROP_FN = r"""
CREATE OR REPLACE FUNCTION drop_partition_if_empty(part TEXT) RETURNS boolean AS $$
DECLARE
  parent TEXT;
  is_empty BOOLEAN;
BEGIN
  SELECT p.relname INTO parent FROM pg_inherits i
    JOIN pg_class c ON c.oid = i.inhrelid JOIN pg_class p ON p.oid = i.inhparent
   WHERE c.relname = part;
  IF parent IS NULL OR parent NOT IN ('executions', 'expected_runs', 'host_metrics')
     OR part !~ ('^' || parent || '_[0-9]{6}$')
     OR right(part, 6) >= to_char(now() - interval '62 days', 'YYYYMM') THEN
    RETURN false;   -- not a month partition we manage, or too recent
  END IF;
  EXECUTE format('SELECT NOT EXISTS (SELECT 1 FROM %I)', part) INTO is_empty;
  IF NOT is_empty THEN
    RETURN false;
  END IF;
  EXECUTE format('DROP TABLE %I', part);
  RETURN true;
END $$ LANGUAGE plpgsql SECURITY DEFINER;
"""


def upgrade():
    op.execute(DROP_FN)
    op.execute("REVOKE ALL ON FUNCTION drop_partition_if_empty(text) FROM PUBLIC; "
               "GRANT EXECUTE ON FUNCTION drop_partition_if_empty(text) TO jobwatch_system;")
    op.execute(FN)
    op.execute("REVOKE ALL ON FUNCTION ensure_month_partition(text, date) FROM PUBLIC; "
               "GRANT EXECUTE ON FUNCTION ensure_month_partition(text, date) TO jobwatch_app, jobwatch_system;")


def downgrade():
    op.execute("DROP FUNCTION IF EXISTS drop_partition_if_empty(text)")
    op.execute(OLD)
