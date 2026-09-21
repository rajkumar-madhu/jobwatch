from contextlib import contextmanager
from uuid import UUID

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from .config import settings

engine = create_engine(settings.database_url, pool_pre_ping=True, pool_size=10, max_overflow=20)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


@contextmanager
def tenant_session(org_id: UUID):
    """Session scoped to one tenant; RLS enforces org_id on every statement."""
    s: Session = SessionLocal()
    try:
        # SET cannot take bind parameters (psycopg raises "syntax error at or near $1"); set_config can.
        # is_local=true scopes it to the transaction, same as SET LOCAL.
        s.execute(text("SELECT set_config('app.org_id', :org, true)"), {"org": str(org_id)})
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


@contextmanager
def system_session():
    """Cross-tenant session for workers. Use sparingly; every query must still filter org_id explicitly."""
    s: Session = SessionLocal()
    try:
        # R21: a role switch, not a GUC. The old `app.bypass_rls` GUC was checked inside every tenant
        # policy as `... OR app_bypass()`, which stopped Postgres using org_id indexes for *tenant*
        # queries. jobwatch_system has its own policy; see migration 0011.
        s.execute(text("SET LOCAL ROLE jobwatch_system"))
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()
