from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from .settings import settings

_engine = None
_SessionLocal = None


def engine():
    global _engine, _SessionLocal
    if _engine is None:
        _engine = create_engine(
            settings().app_database_url,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=10,
            future=True,
        )
        _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    return _engine


@contextmanager
def db_session(viewer_id: str | None = None) -> Iterator[Session]:
    """A transaction scoped to one viewer.

    `SET LOCAL app.viewer_id` is issued as the FIRST statement in the transaction, and
    `SET LOCAL` is scoped to it — so a pooled connection handed to the next request
    cannot carry the previous viewer's identity. Setting it any later would leave a
    window in which a query runs with whatever the last request left behind, which is
    the exact failure row level security exists to prevent.

    Passing `None` sets the empty string rather than skipping the statement: the policies
    compare against `NULLIF(current_setting(...), '')::uuid`, so an unset viewer matches
    nothing instead of erroring, and public reads still work through their own policy.
    """
    engine()
    session = _SessionLocal()
    try:
        session.execute(
            text("SELECT set_config('app.viewer_id', :vid, true)"),
            {"vid": viewer_id or ""},
        )
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
