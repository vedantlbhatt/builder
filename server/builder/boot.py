import logging

from sqlalchemy import text

from .db import engine
from .settings import settings

log = logging.getLogger("builder.boot")


class UnsafeDatabaseRole(SystemExit):
    pass


def assert_rls_enforced() -> None:
    """Refuse to start if the API's connection can bypass row level security.

    This check exists because the failure it catches is invisible. Railway hands you a
    superuser DATABASE_URL; superusers bypass RLS unconditionally; every policy becomes a
    no-op; and an isolation test connecting as that same superuser passes. The guarantee
    fails OPEN, with a green test suite and no error anywhere.

    A crash on boot is the only failure mode loud enough to be safe.
    """
    with engine().connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT current_user AS who,
                       current_setting('is_superuser') AS su,
                       (SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user) AS brls
                """
            )
        ).one()

    if row.su == "on" or row.brls:
        raise UnsafeDatabaseRole(
            f"FATAL: the API is connected as '{row.who}', which bypasses row level "
            "security (superuser or BYPASSRLS). Every policy is a silent no-op and user "
            "isolation is not enforced. Set APP_DATABASE_URL to the builder_app role. "
            "Refusing to start."
        )

    log.info("RLS enforced: connected as %s (nosuperuser, nobypassrls)", row.who)


def assert_policies_present() -> None:
    """A role that cannot bypass RLS is not enough — the tables must actually have it on.

    A table with RLS disabled is readable by everyone regardless of role, so this catches
    a migration that was written but never run.
    """
    required = {
        "sessions",
        "session_strips",
        "session_stats",
        # 0006. The one table that stores prose; a deployment where that migration never
        # ran must not serve analyses with RLS off.
        "session_analysis",
        "repo_visibility",
        "devices",
        "push_tokens",
        "identities",
        # 0007. The social tables: a post's visibility IS its policy, so a deployment
        # where the migration never ran would serve every post to everyone.
        "posts",
        "post_media",
        "kudos",
        "comments",
        "follows",
        "factions",
        "faction_members",
        # 0008. Owner-only: a record that a session's completion was announced. Without
        # RLS one viewer could read when another's sessions finished.
        "session_notifications",
        # 0011. A key's hash is not a secret once it is a row another viewer can read and
        # offline-match; owner-only like devices.
        "capture_keys",
        # 0014. Raw transcript bytes from the hook channel — the conversation itself,
        # held until the session is final. Owner-only.
        "transcript_chunks",
        # 0016. Prose about the PERSON, not about a session. Owner-only with no public
        # policy at all, so a deployment where the migration never ran must not serve it.
        "builder_narrative",
    }
    with engine().connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT relname, relrowsecurity, relforcerowsecurity
                FROM pg_class WHERE relname = ANY(:names)
                """
            ),
            {"names": list(required)},
        ).all()

    found = {r.relname for r in rows}
    missing = required - found
    if missing:
        raise UnsafeDatabaseRole(
            f"FATAL: tables missing entirely: {sorted(missing)}. Run migrations."
        )

    unprotected = [r.relname for r in rows if not (r.relrowsecurity and r.relforcerowsecurity)]
    if unprotected:
        raise UnsafeDatabaseRole(
            f"FATAL: row level security is not enabled+forced on {sorted(unprotected)}. "
            "Refusing to start."
        )


def run_startup_checks() -> None:
    if settings().environment == "test":
        return
    assert_rls_enforced()
    assert_policies_present()
