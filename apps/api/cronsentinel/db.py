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
        s.execute(text("SET LOCAL app.org_id = :org"), {"org": str(org_id)})
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
        s.execute(text("SET LOCAL app.bypass_rls = 'on'"))
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()
