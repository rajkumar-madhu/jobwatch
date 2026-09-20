"""Bootstrap helpers. `python -m cronsentinel.cli bootstrap --org acme --email you@x`"""
import argparse

from sqlalchemy import text

from .auth import generate_api_key, hash_secret
from .db import system_session


def bootstrap(org: str, email: str):
    with system_session() as s:
        o = s.execute(text("INSERT INTO organizations (name, slug) VALUES (:n, :s) RETURNING id"), {"n": org, "s": org.lower()}).first()
        u = s.execute(text("INSERT INTO users (email, name) VALUES (:e, :e) ON CONFLICT (email) DO UPDATE SET email=EXCLUDED.email RETURNING id"), {"e": email}).first()
        s.execute(text("INSERT INTO memberships (user_id, org_id, role) VALUES (:u, :o, 'owner')"), {"u": u.id, "o": o.id})
        s.execute(text("INSERT INTO subscriptions (org_id, plan) VALUES (:o, 'free')"), {"o": o.id})
        ws = s.execute(text("INSERT INTO workspaces (org_id, name) VALUES (:o, 'default') RETURNING id"), {"o": o.id}).first()
        s.execute(text("INSERT INTO environments (org_id, workspace_id, name) VALUES (:o, :w, 'production')"), {"o": o.id, "w": ws.id})
        prefix, raw = generate_api_key()
        s.execute(text("INSERT INTO api_keys (org_id, name, prefix, key_hash, role) VALUES (:o, 'bootstrap', :p, :h, 'admin')"),
                  {"o": o.id, "p": prefix, "h": hash_secret(raw)})
    print(f"org_id={o.id}\nworkspace_id={ws.id}\napi_key={raw}")

def purge_org(org_id: str, yes: bool = False):
    """Tenant offboarding. Removes the partitioned/unreferenced rows that no FK reaches, then the
    organization itself (whose cascades handle everything else). Irreversible."""
    from sqlalchemy import text

    from .db import system_session
    with system_session() as s:
        row = s.execute(text("SELECT name, slug FROM organizations WHERE id = CAST(:o AS uuid)"), {"o": org_id}).first()
        if not row:
            print(f"no such organization: {org_id}"); return 1
        counts = {t: s.execute(text(f"SELECT count(*) FROM {t} WHERE org_id = CAST(:o AS uuid)"), {"o": org_id}).scalar()
                  for t in ("jobs", "executions", "expected_runs", "incidents", "audit_logs")}
        print(f"organization: {row.name} ({row.slug})")
        for k, v in counts.items():
            print(f"  {k}: {v}")
    if not yes:
        print("\nrefusing to delete without --yes")
        return 1
    with system_session() as s:
        s.execute(text("SELECT purge_org(CAST(:o AS uuid))"), {"o": org_id})
        s.execute(text("DELETE FROM organizations WHERE id = CAST(:o AS uuid)"), {"o": org_id})
    print("purged")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("bootstrap"); b.add_argument("--org", required=True); b.add_argument("--email", required=True)
    p_ = sub.add_parser("purge-org", help="delete an organization and all of its data"); p_.add_argument("--org-id", required=True); p_.add_argument("--yes", action="store_true")
    a = ap.parse_args()
    if a.cmd == "bootstrap":
        bootstrap(a.org, a.email)
    elif a.cmd == "purge-org":
        raise SystemExit(purge_org(a.org_id, a.yes))
