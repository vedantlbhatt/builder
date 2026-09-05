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


def set_viewer(db: Session, user_id: str) -> None:
    """Raise the viewer from "nobody" to a resolved user, mid-transaction.

    Only the sign-in paths call this. They open the transaction before anyone is known —
    a pairing code, a refresh token or an identity token is all they have — and the tables
    they must then write (`devices`, `push_tokens`, `identities`) are RLS-protected with an
    owner policy. With the viewer still '', `INSERT INTO devices` fails WITH CHECK and a
    JOIN through `devices` matches nothing, which is how `/v1/auth/refresh` came to 401
    every valid token. `set_config(..., true)` is transaction-local, so overriding it here
    is safe; the direction only ever goes from unset to a user the caller has just proven.
    """
    db.execute(text("SELECT set_config('app.viewer_id', :vid, true)"), {"vid": user_id})
