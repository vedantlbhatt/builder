"""The boot guard, which is the only thing standing between a misconfiguration and a
silent loss of user isolation.

Railway hands you a superuser DATABASE_URL. Superusers bypass row level security
unconditionally, so pointing the API at it turns every policy into a no-op — and an
isolation test connecting as that same superuser still passes. There is no error, no log
line, and no symptom until someone reads another person's sessions.

Crashing on boot is the only response loud enough to be safe, so these tests assert that
it really does crash.
"""

import os

import pytest
from sqlalchemy import text

TEST_DB = os.environ.get("BUILDER_TEST_DB")
pytestmark = pytest.mark.skipif(not TEST_DB, reason="set BUILDER_TEST_DB to run")


def test_refuses_to_start_as_a_superuser(monkeypatch):
    import builder.boot as boot
    import builder.db as db_module
    from builder.settings import Settings, settings

    settings.cache_clear()
    monkeypatch.setenv("APP_DATABASE_URL", TEST_DB)  # the owner: a superuser locally
    monkeypatch.setenv("ENVIRONMENT", "production")
    db_module._engine = None

    with pytest.raises(SystemExit) as exc:
        boot.assert_rls_enforced()

    message = str(exc.value)
    assert "bypasses row level security" in message
    assert "APP_DATABASE_URL" in message
    settings.cache_clear()
    db_module._engine = None
    _ = Settings


def test_accepts_the_unprivileged_role(monkeypatch):
    import builder.boot as boot
    import builder.db as db_module
    from builder.settings import settings

    app_url = TEST_DB.replace("://", "://builder_app@", 1)
    settings.cache_clear()
    monkeypatch.setenv("APP_DATABASE_URL", app_url)
    monkeypatch.setenv("ENVIRONMENT", "production")
    db_module._engine = None

    boot.assert_rls_enforced()
    boot.assert_policies_present()

    settings.cache_clear()
    db_module._engine = None


def test_detects_rls_switched_off(monkeypatch):
    """A correct role is not enough — the tables must actually have RLS on.

    This catches a migration that was written but never run, which otherwise presents as
    a perfectly healthy service serving everyone's data to everyone.
    """
    import builder.boot as boot
    import builder.db as db_module
    from builder.settings import settings
    from sqlalchemy import create_engine

    owner = create_engine(TEST_DB, future=True)
    with owner.begin() as c:
        c.execute(text("ALTER TABLE sessions DISABLE ROW LEVEL SECURITY"))

    try:
        settings.cache_clear()
        monkeypatch.setenv("APP_DATABASE_URL", TEST_DB.replace("://", "://builder_app@", 1))
        monkeypatch.setenv("ENVIRONMENT", "production")
        db_module._engine = None

        with pytest.raises(SystemExit) as exc:
            boot.assert_policies_present()
        assert "row level security is not enabled" in str(exc.value)
    finally:
        with owner.begin() as c:
            c.execute(text("ALTER TABLE sessions ENABLE ROW LEVEL SECURITY"))
            c.execute(text("ALTER TABLE sessions FORCE ROW LEVEL SECURITY"))
        settings.cache_clear()
        db_module._engine = None
