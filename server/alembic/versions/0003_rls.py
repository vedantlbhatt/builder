"""Row level security, and the roles that make it real.

Revision ID: 0003_rls

READ THIS BEFORE CHANGING ANYTHING HERE.

Railway's DATABASE_URL is a SUPERUSER role, and **superusers bypass row level security
unconditionally**. `FORCE ROW LEVEL SECURITY` does not help: it closes the table-OWNER
loophole, not the superuser one.

So without the roles below, every policy in this file is a silent no-op — and, worse,
an isolation test connecting as that same superuser passes. The guarantee FAILS OPEN
with a green suite. `boot.py` therefore refuses to start the API unless its connection
is genuinely NOBYPASSRLS.
"""

from alembic import op

revision = "0003_rls"
down_revision = "0002_sessions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'builder_app') THEN
            CREATE ROLE builder_app NOLOGIN NOSUPERUSER NOBYPASSRLS;
          END IF;
          -- Background jobs legitimately need to see across users (co-op detection later,
          -- deletion sweeps now). Separate role, separate connection string, never the
          -- one the API request path uses.
          IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'builder_worker') THEN
            CREATE ROLE builder_worker NOLOGIN NOSUPERUSER BYPASSRLS;
          END IF;
        END $$;

        GRANT USAGE ON SCHEMA public TO builder_app, builder_worker;
        GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public
          TO builder_app, builder_worker;
        ALTER DEFAULT PRIVILEGES IN SCHEMA public
          GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO builder_app, builder_worker;

        ALTER TABLE sessions       ENABLE ROW LEVEL SECURITY;
        ALTER TABLE sessions       FORCE  ROW LEVEL SECURITY;
        ALTER TABLE session_strips ENABLE ROW LEVEL SECURITY;
        ALTER TABLE session_strips FORCE  ROW LEVEL SECURITY;
        ALTER TABLE session_stats  ENABLE ROW LEVEL SECURITY;
        ALTER TABLE session_stats  FORCE  ROW LEVEL SECURITY;
        ALTER TABLE repo_visibility ENABLE ROW LEVEL SECURITY;
        ALTER TABLE repo_visibility FORCE  ROW LEVEL SECURITY;
        ALTER TABLE devices        ENABLE ROW LEVEL SECURITY;
        ALTER TABLE devices        FORCE  ROW LEVEL SECURITY;
        ALTER TABLE push_tokens    ENABLE ROW LEVEL SECURITY;
        ALTER TABLE push_tokens    FORCE  ROW LEVEL SECURITY;

        -- `app.viewer_id` is set with SET LOCAL as the FIRST statement of every request's
        -- transaction, so it cannot leak across pooled connections.
        CREATE POLICY sessions_owner ON sessions
          USING (user_id = NULLIF(current_setting('app.viewer_id', true), '')::uuid)
          WITH CHECK (user_id = NULLIF(current_setting('app.viewer_id', true), '')::uuid);

        -- Anything explicitly shared is world-readable, unless its repository has since
        -- been marked excluded — revoking visibility has to reach already-shared rows.
        CREATE POLICY sessions_public ON sessions FOR SELECT USING (
          is_shared AND superseded_by IS NULL AND state = 'final'
          AND NOT EXISTS (
            SELECT 1 FROM repo_visibility rv
            WHERE rv.user_id = sessions.user_id
              AND rv.repo_id = sessions.repo_id
              AND rv.visibility = 'excluded')
        );

        CREATE POLICY strips_owner ON session_strips
          USING (EXISTS (SELECT 1 FROM sessions s WHERE s.id = session_id
                         AND s.user_id = NULLIF(current_setting('app.viewer_id', true), '')::uuid))
          WITH CHECK (EXISTS (SELECT 1 FROM sessions s WHERE s.id = session_id
                              AND s.user_id = NULLIF(current_setting('app.viewer_id', true), '')::uuid));

        CREATE POLICY strips_public ON session_strips FOR SELECT USING (
          EXISTS (SELECT 1 FROM sessions s WHERE s.id = session_id AND s.is_shared)
        );

        CREATE POLICY stats_owner ON session_stats
          USING (EXISTS (SELECT 1 FROM sessions s WHERE s.id = session_id
                         AND s.user_id = NULLIF(current_setting('app.viewer_id', true), '')::uuid))
          WITH CHECK (EXISTS (SELECT 1 FROM sessions s WHERE s.id = session_id
                              AND s.user_id = NULLIF(current_setting('app.viewer_id', true), '')::uuid));

        CREATE POLICY stats_public ON session_stats FOR SELECT USING (
          EXISTS (SELECT 1 FROM sessions s WHERE s.id = session_id AND s.is_shared)
        );

        CREATE POLICY repo_vis_owner ON repo_visibility
          USING (user_id = NULLIF(current_setting('app.viewer_id', true), '')::uuid)
          WITH CHECK (user_id = NULLIF(current_setting('app.viewer_id', true), '')::uuid);

        CREATE POLICY devices_owner ON devices
          USING (user_id = NULLIF(current_setting('app.viewer_id', true), '')::uuid)
          WITH CHECK (user_id = NULLIF(current_setting('app.viewer_id', true), '')::uuid);

        CREATE POLICY push_owner ON push_tokens
          USING (user_id = NULLIF(current_setting('app.viewer_id', true), '')::uuid)
          WITH CHECK (user_id = NULLIF(current_setting('app.viewer_id', true), '')::uuid);
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP POLICY IF EXISTS push_owner ON push_tokens;
        DROP POLICY IF EXISTS devices_owner ON devices;
        DROP POLICY IF EXISTS repo_vis_owner ON repo_visibility;
        DROP POLICY IF EXISTS stats_public ON session_stats;
        DROP POLICY IF EXISTS stats_owner ON session_stats;
        DROP POLICY IF EXISTS strips_public ON session_strips;
        DROP POLICY IF EXISTS strips_owner ON session_strips;
        DROP POLICY IF EXISTS sessions_public ON sessions;
        DROP POLICY IF EXISTS sessions_owner ON sessions;
        """
    )
