"""session_notifications: the memory that a session's completion was already announced.

Revision ID: 0008_session_notifications

One row per session, written in the same transaction as the upload that finalized it and
BEFORE the push goes out. `kind` is what was decided, not only what was sent:
`suppressed_stale` records a session that finished too long ago to be news (the
"backfill must be silent" rule — anything older than twice the session threshold is
recorded as notified without being delivered), so a later re-upload of the same session
cannot promote it to a banner either.

The primary key is the whole idempotency story. A push is sent at most once per session
because a second INSERT for the same `session_id` conflicts, and the route only sends what
it managed to record. The cheaper mistake is a missed banner, never a doubled one.

RLS ENABLE + FORCE with the owner policy copied from `session_analysis`: the row belongs
to whoever owns the session, and there is no public policy because a notification is
nobody else's business. `boot.assert_policies_present` lists this table.
"""

from alembic import op

revision = "0008_session_notifications"
down_revision = "0007_social"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE session_notifications (
          session_id uuid PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
          -- What was decided when the session finalized. The two sent kinds match the two
          -- notification titles in docs/session-boundaries.md; the third is the silent
          -- backfill record.
          kind       text NOT NULL
                     CHECK (kind IN ('session_finished','agent_run_finished','suppressed_stale')),
          sent_at    timestamptz NOT NULL DEFAULT now()
        );

        GRANT SELECT, INSERT, UPDATE, DELETE ON session_notifications
          TO builder_app, builder_worker;

        ALTER TABLE session_notifications ENABLE ROW LEVEL SECURITY;
        ALTER TABLE session_notifications FORCE  ROW LEVEL SECURITY;

        -- Mirrors analysis_owner from 0006. Owner only: no public policy.
        CREATE POLICY notifications_owner ON session_notifications
          USING (EXISTS (SELECT 1 FROM sessions s WHERE s.id = session_id
                         AND s.user_id = NULLIF(current_setting('app.viewer_id', true), '')::uuid))
          WITH CHECK (EXISTS (
            SELECT 1 FROM sessions s WHERE s.id = session_id
              AND s.user_id = NULLIF(current_setting('app.viewer_id', true), '')::uuid));
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP POLICY IF EXISTS notifications_owner ON session_notifications;
        DROP TABLE IF EXISTS session_notifications;
        """
    )
