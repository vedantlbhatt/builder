"""Row level security, tested against a real Postgres and as a real unprivileged role.

THIS TEST IS ONLY MEANINGFUL IF IT CONNECTS AS builder_app.

Superusers bypass RLS unconditionally, so an isolation test run as the owner passes
whether or not a single policy exists. That is the failure mode this file exists to
prevent, and it is why the first test asserts its own connection is not privileged
before asserting anything else.

Run with a local Postgres:
    createdb builder_test
    BUILDER_TEST_DB=postgresql+psycopg://localhost/builder_test pytest tests/test_rls.py
"""

import os
import uuid

import pytest
from sqlalchemy import create_engine, text

TEST_DB = os.environ.get("BUILDER_TEST_DB")
pytestmark = pytest.mark.skipif(not TEST_DB, reason="set BUILDER_TEST_DB to run")


def owner_engine():
    return create_engine(TEST_DB, future=True)


def app_engine():
    """Connect as builder_app — the role the API actually uses."""
    url = TEST_DB.replace("://", "://builder_app@", 1) if "@" not in TEST_DB else TEST_DB
    return create_engine(url, future=True)


@pytest.fixture(scope="module")
def two_users():
    """Two users, each with a session, created as the owner."""
    eng = owner_engine()
    a, b = uuid.uuid4(), uuid.uuid4()
    with eng.begin() as c:
        for uid, sub in [(a, f"apple-{a}"), (b, f"apple-{b}")]:
            c.execute(
                text("INSERT INTO users (id, apple_sub) VALUES (:i, :s)"), {"i": uid, "s": sub}
            )
            dev = c.execute(
                text(
                    """
                    INSERT INTO devices (user_id, label, platform, agent_version, machine_id)
                    VALUES (:u, 'test', 'macos', '0.1', :m) RETURNING id
                    """
                ),
                {"u": uid, "m": f"{uid.hex}{uid.hex}"[:64]},
            ).scalar()
            c.execute(
                text(
                    """
                    INSERT INTO sessions (
                      user_id, device_id, client_session_id, content_hash,
                      sessionizer_version, active_calc_version, harness,
                      started_at, ended_at, active_seconds, tz_offset_minutes,
                      local_date, local_hour, local_dow, timeline_fidelity, agent_observed_at
                    ) VALUES (
                      :u, :d, :c, :h, 1, 1, 'claude_code',
                      now() - interval '2 hours', now(), 3600, 0,
                      CURRENT_DATE, 12, 3, 'full', now()
                    )
                    """
                ),
                {"u": uid, "d": dev, "c": f"{uid.hex}{uid.hex}"[:64], "h": "0" * 64},
            )
    yield a, b
    with eng.begin() as c:
        c.execute(text("DELETE FROM users WHERE id IN (:a, :b)"), {"a": a, "b": b})


def test_the_test_role_cannot_bypass_rls():
    """Assert the harness itself is unprivileged BEFORE trusting any isolation result.

    Without this, every other test in the file can pass against a database with no
    policies at all, and the suite becomes a green light for a broken guarantee.
    """
    with app_engine().connect() as c:
        row = c.execute(
            text(
                """
                SELECT current_user AS who,
                       current_setting('is_superuser') AS su,
                       (SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user) AS brls
                """
            )
        ).one()
    assert row.su == "off", f"connected as {row.who}, which is a superuser"
    assert row.brls is False, f"{row.who} has BYPASSRLS; isolation cannot be tested"


def test_viewer_sees_only_their_own_sessions(two_users):
    a, b = two_users
    with app_engine().connect() as c:
        c.execute(text("SELECT set_config('app.viewer_id', :v, false)"), {"v": str(a)})
        rows = c.execute(text("SELECT user_id FROM sessions")).all()
    assert rows, "viewer A should see their own session"
    assert all(r.user_id == a for r in rows)
    assert not any(r.user_id == b for r in rows)


def test_unset_viewer_sees_nothing_private(two_users):
    """An unset viewer must match nothing rather than everything.

    The policies compare against NULLIF(current_setting(...), '')::uuid, so a missing
    setting yields NULL and matches no row — instead of erroring, or worse, being treated
    as a wildcard.
    """
    with app_engine().connect() as c:
        c.execute(text("SELECT set_config('app.viewer_id', '', false)"))
        rows = c.execute(text("SELECT id FROM sessions WHERE NOT is_shared")).all()
    assert rows == []


def test_shared_sessions_are_publicly_readable(two_users):
    a, _ = two_users
    owner = owner_engine()
    with owner.begin() as c:
        c.execute(
            text(
                "UPDATE sessions SET is_shared = true, shared_at = now() WHERE user_id = :u"
            ),
            {"u": a},
        )
    try:
        with app_engine().connect() as c:
            c.execute(text("SELECT set_config('app.viewer_id', '', false)"))
            rows = c.execute(text("SELECT id FROM sessions")).all()
        assert len(rows) == 1
    finally:
        with owner.begin() as c:
            c.execute(text("UPDATE sessions SET is_shared = false WHERE user_id = :u"), {"u": a})


def test_excluding_a_repo_revokes_an_already_shared_session(two_users):
    """Revocation must reach rows that are already public.

    Marking a repository excluded is not a preference about future uploads — it has to
    take back what is already out there, or the control is decorative.
    """
    a, _ = two_users
    owner = owner_engine()
    with owner.begin() as c:
        repo = c.execute(
            text(
                """
                INSERT INTO repos (repo_hash, pepper_version, repo_id_basis)
                VALUES (:h, 1, 'origin')
                ON CONFLICT (repo_hash) DO UPDATE SET pepper_version = 1
                RETURNING id
                """
            ),
            {"h": "e" * 64},
        ).scalar()
        c.execute(
            text(
                "UPDATE sessions SET is_shared = true, shared_at = now(), repo_id = :r "
                "WHERE user_id = :u"
            ),
            {"u": a, "r": repo},
        )
        c.execute(
            text(
                """
                INSERT INTO repo_visibility (user_id, repo_id, visibility)
                VALUES (:u, :r, 'excluded')
                ON CONFLICT (user_id, repo_id) DO UPDATE SET visibility = 'excluded'
                """
            ),
            {"u": a, "r": repo},
        )
    try:
        with app_engine().connect() as c:
            c.execute(text("SELECT set_config('app.viewer_id', '', false)"))
            rows = c.execute(text("SELECT id FROM sessions")).all()
        assert rows == [], "an excluded repo's sessions must stop being publicly visible"
    finally:
        with owner.begin() as c:
            c.execute(text("DELETE FROM repo_visibility WHERE user_id = :u"), {"u": a})
            c.execute(text("UPDATE sessions SET is_shared = false WHERE user_id = :u"), {"u": a})


def test_a_viewer_cannot_write_rows_for_someone_else(two_users):
    """WITH CHECK, not just USING. Read isolation without write isolation lets one user
    insert rows attributed to another, which is worse than being able to read them."""
    a, b = two_users
    from sqlalchemy.exc import ProgrammingError

    # Resolve B's device as the OWNER first. An earlier version of this test did the
    # lookup inside the restricted connection, where `devices` is itself RLS-protected —
    # so the SELECT matched nothing, the INSERT wrote zero rows, and the test passed for
    # entirely the wrong reason. A negative test that cannot reach the code it is trying
    # to violate is worse than no test.
    with owner_engine().connect() as c:
        device_b = c.execute(
            text("SELECT id FROM devices WHERE user_id = :b LIMIT 1"), {"b": b}
        ).scalar()

    with app_engine().connect() as c:
        c.execute(text("SELECT set_config('app.viewer_id', :v, false)"), {"v": str(a)})
        with pytest.raises(ProgrammingError):
            c.execute(
                text(
                    """
                    INSERT INTO sessions (
                      user_id, device_id, client_session_id, content_hash,
                      sessionizer_version, active_calc_version, harness,
                      started_at, ended_at, active_seconds, tz_offset_minutes,
                      local_date, local_hour, local_dow, timeline_fidelity, agent_observed_at
                    ) VALUES (
                      :b, :d, :c, :h, 1, 1, 'claude_code',
                      now(), now(), 1, 0, CURRENT_DATE, 1, 1, 'full', now()
                    )
                    """
                ),
                {"b": b, "d": device_b, "c": "f" * 64, "h": "0" * 64},
            )
