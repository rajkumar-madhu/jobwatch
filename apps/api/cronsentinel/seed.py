"""Demo data generator. `python -m cronsentinel.seed --org <org_id>` — idempotent-ish (skips existing job names).
Produces 7 days of executions across healthy/running/failed/late/missed/recovered states."""
import argparse, random, secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from .db import system_session
from .schedule import next_run, prev_run

SERVERS = ["prod-web-01", "prod-db-01", "backup-01"]
JOBS = [  # name, cron, expected_s, tags, profile
    ("nightly-database-backup", "0 2 * * *", 900, ["prod", "db", "backup"], "slowing"),
    ("billing-reconciliation", "0 */6 * * *", 300, ["prod", "billing"], "healthy"),
    ("market-data-import", "*/15 * * * *", 60, ["prod", "data"], "flaky"),
    ("customer-report-generator", "30 6 * * *", 600, ["prod", "reports"], "failing"),
    ("log-cleanup", "0 4 * * *", 120, ["prod", "maintenance"], "healthy"),
    ("mongodb-backup", "0 3 * * *", 700, ["prod", "db", "backup"], "missed"),
    ("redis-snapshot", "*/30 * * * *", 20, ["prod", "cache"], "running"),
]
STDERR = {"failing": "psycopg2.OperationalError: connection to server at \"prod-db-01\" failed: timeout expired\n(12 retries exhausted)\n",
          "flaky": "WARN upstream 502 from feed.example; retrying\nERROR max retries exceeded\n"}


def seed(org: str):
    now = datetime.now(timezone.utc)
    with system_session() as s:
        ws = s.execute(text("SELECT id FROM workspaces WHERE org_id=:o ORDER BY created_at LIMIT 1"), {"o": org}).scalar()
        if not ws:
            ws = s.execute(text("INSERT INTO workspaces (org_id, name) VALUES (:o, 'default') RETURNING id"), {"o": org}).scalar()
        srv_ids = {}
        for h in SERVERS:
            srv_ids[h] = s.execute(text("INSERT INTO servers (org_id, hostname, labels) VALUES (:o, :h, '{\"env\":\"production\"}') ON CONFLICT (org_id, hostname) DO UPDATE SET hostname=EXCLUDED.hostname RETURNING id"), {"o": org, "h": h}).scalar()
        for name, cron, exp, tags, profile in JOBS:
            if s.execute(text("SELECT 1 FROM jobs WHERE workspace_id=:w AND name=:n"), {"w": ws, "n": name}).first():
                print("skip", name); continue
            host = "prod-db-01" if "db" in tags else ("backup-01" if "backup" in tags else "prod-web-01")
            jid = s.execute(text(
                "INSERT INTO jobs (org_id, workspace_id, name, kind, source, heartbeat_token, schedule_expr, expected_runtime_s, grace_s, tags, server_id, command, run_as_user, status, next_expected_at) "
                "VALUES (:o, :w, :n, 'cron', 'agent', :t, :c, :e, 300, :tags, :srv, :cmd, 'root', 'healthy', :nx) RETURNING id"),
                {"o": org, "w": ws, "n": name, "t": secrets.token_urlsafe(18), "c": cron, "e": exp, "tags": tags, "srv": srv_ids[host],
                 "cmd": f"/opt/jobs/{name}.sh", "nx": next_run(cron)}).scalar()
            # walk back 7 days of scheduled slots
            t = prev_run(cron, "UTC", now)
            slots = []
            while t > now - timedelta(days=7):
                slots.append(t); t = prev_run(cron, "UTC", t)
            slots.reverse()
            last_status, k = "success", 0
            for i, st in enumerate(slots):
                k += 1
                base = exp * 1000
                status, dur, code, err = "success", int(random.gauss(base, base * 0.08)), 0, None
                if profile == "slowing": dur = int(base * (1 + 3.2 * i / len(slots)))
                if profile == "flaky" and random.random() < 0.15: status, code, err = "failed", 1, STDERR["flaky"]
                if profile == "failing" and i >= len(slots) - 3: status, code, err, dur = "failed", 2, STDERR["failing"], 12000
                if profile == "missed" and i == len(slots) - 1: status = "missed"
                if status == "missed":
                    s.execute(text("INSERT INTO executions (id, org_id, job_id, status, scheduled_ts, server_received_ts) VALUES (:id, :o, :j, 'missed', :ts, :ts)"),
                              {"id": f"missed-{jid}-{int(st.timestamp())}", "o": org, "j": jid, "ts": st})
                    last_status = "missed"; continue
                start = st + timedelta(seconds=random.randint(0, 3)); end = start + timedelta(milliseconds=dur)
                eid = f"{int(start.timestamp()*1000)}-{secrets.token_hex(6)}"
                s.execute(text("INSERT INTO executions (id, org_id, job_id, status, scheduled_ts, agent_ts_start, agent_ts_end, server_received_ts, skew_ms, duration_ms, exit_code, host, sequence_max) "
                               "VALUES (:id, :o, :j, :st, :sch, :a, :b, :b, :skew, :d, :c, :h, 1)"),
                          {"id": eid, "o": org, "j": jid, "st": status, "sch": st, "a": start, "b": end, "skew": random.randint(-40, 40), "d": dur, "c": code, "h": host})
                for seq, kind, ts_ in ((0, "start", start), (1, "success" if status == "success" else "fail", end)):
                    s.execute(text("INSERT INTO execution_events (org_id, execution_id, sequence, kind, agent_ts, server_ts, payload) VALUES (:o, :e, :q, :k, :t, :t, :p::jsonb)"),
                              {"o": org, "e": eid, "q": seq, "k": kind, "t": ts_, "p": '{"command":"/opt/jobs/%s.sh"}' % name if seq == 0 else "{}"})
                if err:
                    s.execute(text("INSERT INTO execution_logs (org_id, execution_id, stream, chunk_idx, content) VALUES (:o, :e, 'stderr', 0, :c)"), {"o": org, "e": eid, "c": err})
                s.execute(text("INSERT INTO execution_logs (org_id, execution_id, stream, chunk_idx, content) VALUES (:o, :e, 'stdout', 0, :c)"),
                          {"o": org, "e": eid, "c": f"[{start:%H:%M:%S}] starting {name}\n[{end:%H:%M:%S}] done, {random.randint(100,9000)} rows\n"})
                last_status = status
            final = {"failing": "failed", "missed": "missed", "running": "running", "flaky": "recovered" if last_status == "success" else "failed"}.get(profile, "healthy")
            if profile == "running":
                start = now - timedelta(seconds=25); eid = f"{int(start.timestamp()*1000)}-{secrets.token_hex(6)}"
                s.execute(text("INSERT INTO executions (id, org_id, job_id, status, scheduled_ts, agent_ts_start, server_received_ts, host) VALUES (:id, :o, :j, 'running', :t, :t, :t, :h)"), {"id": eid, "o": org, "j": jid, "t": start, "h": host})
                s.execute(text("INSERT INTO execution_events (org_id, execution_id, sequence, kind, agent_ts, server_ts) VALUES (:o, :e, 0, 'start', :t, :t)"), {"o": org, "e": eid, "t": start})
            s.execute(text("UPDATE jobs SET status=:st, last_status=:ls, last_run_at=:lr, reliability_score=:sc WHERE id=:id"),
                      {"st": final, "ls": last_status if final != "running" else None, "lr": slots[-1] if slots else None,
                       "sc": {"healthy": 98, "slowing": 91, "flaky": 74, "failing": 61, "missed": 83, "running": 97}[profile], "id": jid})
            # host metrics: disk I/O climb on backup host for the "slowing" story
            if profile == "slowing":
                for m in range(0, 7 * 24 * 2):
                    ts_ = now - timedelta(minutes=30 * m); frac = 1 - m / (7 * 24 * 2)
                    s.execute(text("INSERT INTO host_metrics (org_id, server_id, ts, cpu_pct, mem_pct, load1, disk_pct, inode_pct, io_wait_ms) VALUES (:o, :s, :t, :c, :mem, :l, :d, :i, :w) ON CONFLICT DO NOTHING"),
                              {"o": org, "s": srv_ids[host], "t": ts_, "c": random.uniform(10, 30), "mem": random.uniform(40, 55), "l": random.uniform(0.5, 2), "d": 62 + 25 * frac, "i": 30, "w": 12 + 176 * frac})
            print("seeded", name, final, len(slots), "runs")
        # one open incident for the failing job
        j = s.execute(text("SELECT id, name FROM jobs WHERE workspace_id=:w AND name='customer-report-generator'"), {"w": ws}).first()
        if j and not s.execute(text("SELECT 1 FROM incidents WHERE org_id=:o AND :j = ANY(affected_job_ids) AND status<>'resolved'"), {"o": org, "j": j.id}).first():
            iid = s.execute(text("INSERT INTO incidents (org_id, severity, title, correlation_key, affected_job_ids, started_at) VALUES (:o, 'high', :t, :ck, ARRAY[:j]::uuid[], :st) RETURNING id"),
                            {"o": org, "t": f"{j.name} is failed", "ck": f"job:{j.id}", "j": j.id, "st": now - timedelta(hours=20)}).scalar()
            s.execute(text("INSERT INTO incident_events (org_id, incident_id, ts, kind, payload) VALUES (:o, :i, :t, 'opened', '{\"prev\":\"healthy\",\"new\":\"failed\"}')"), {"o": org, "i": iid, "t": now - timedelta(hours=20)})
            print("seeded incident")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--org", required=True); a = ap.parse_args()
    seed(a.org)
