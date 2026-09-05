"""Session boundaries (two clocks, an end reason, live snapshots) and the opt-in analysis.

Revision ID: 0006_boundaries_and_analysis

Contract v2, server side. Three things land here; the third is the one to read carefully.

1. The two clocks. `active_seconds` splits into `attended_seconds` (a human evidently
   present) and `autonomous_seconds` (the agent working after the human went quiet), plus
   `presence_count` and `end_reason`. Every existing row predates the split and was, by the
   v1 rule, wholly attended — so attended is backfilled from active and autonomous stays 0,
   which keeps `attended + autonomous == active` true for history without inventing a
   number. Records rank by ATTENDED time from now on (docs/session-boundaries.md): the
   longest session in the reference corpus, 5h40m active with zero typed prompts, would
   otherwise have been the headline personal record. `sessions_record_idx` follows.

2. `live` joins `sess_state`. Open and idle sessions are uploaded as snapshots and replaced
   in place when they finalize, so the phone can show a session while the Mac is still
   working. `ALTER TYPE ... ADD VALUE` is special: PG12+ lets it run inside a transaction
   but refuses to USE the new value until that transaction commits ("unsafe use of new
   value"), and the partial index right after it does use it. So the ADD VALUE goes out in
   its own autocommit window and the rest of the migration runs in a fresh transaction.
   Verified on PG16.

3. `session_analysis`: the ONE field that carries prose (spec/analysis.v1.json), stored
   one row per session. RLS ENABLE + FORCE, with the owner and public policies copied from
   `session_stats` — including the 0004 lesson: the public policy checks repo exclusion
   through the SECURITY DEFINER `session_repo_excluded`, never through a bare subquery on
   `repo_visibility`, because a policy predicate that reads another RLS-protected table
   sees it through the viewer's eyes and fails OPEN. `boot.assert_policies_present` lists
   this table, so a deployment where this migration never ran refuses to start.

The downgrade is real. Postgres cannot drop an enum value, so removing `live` means
swapping the type out from under the column — which in turn means dropping and recreating
`sessions_public` (Postgres refuses to retype a column a policy mentions) and mapping any
live rows to `open`, which is what 0001 called the same thing.
"""

from alembic import op

revision = "0006_boundaries_and_analysis"
down_revision = "0005_identities"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # See the docstring: committed on its own, or the index below fails with
    # "unsafe use of new value 'live'".
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE sess_state ADD VALUE IF NOT EXISTS 'live'")

    op.execute(
        """
        ALTER TABLE sessions
          ADD COLUMN attended_seconds   integer NOT NULL DEFAULT 0
                                        CHECK (attended_seconds >= 0),
          ADD COLUMN autonomous_seconds integer NOT NULL DEFAULT 0
                                        CHECK (autonomous_seconds >= 0),
          ADD COLUMN presence_count     integer NOT NULL DEFAULT 0
                                        CHECK (presence_count >= 0),
          -- text + CHECK rather than a second enum: adding a reason later must not need
          -- another autocommit dance, and the legal set is the contract's to define.
          ADD COLUMN end_reason         text NOT NULL DEFAULT 'idle_gap'
                                        CHECK (end_reason IN
                                          ('idle_gap','human_returned','day_boundary',
                                           'still_running'));

        -- Every row before this migration was uploaded under v1, which had no autonomous
        -- clock: the whole of active_seconds was claimed as a sitting. Carrying that claim
        -- into attended_seconds keeps attended + autonomous == active for history; leaving
        -- attended at 0 would erase every record ever set.
        UPDATE sessions SET attended_seconds = active_seconds;

        -- The phone's "what is running right now" query, and the profile's live list.
        CREATE INDEX sessions_live_idx ON sessions (user_id, updated_at DESC)
          WHERE state = 'live';

        -- Records rank by attended time. Same predicate as 0002, different sort column.
        DROP INDEX IF EXISTS sessions_record_idx;
        CREATE INDEX sessions_record_idx ON sessions (user_id, attended_seconds DESC)
          WHERE state = 'final' AND notable AND NOT unattended AND time_quality = 'ok';

        CREATE TABLE session_analysis (
          session_id       uuid PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
          -- == spec version. A retuned prompt is a recompute, not a migration.
          analysis_version integer NOT NULL,
          model            text,
          generated_at     timestamptz NOT NULL,
          -- Hash of the digest the model read; lets a client skip an unchanged analysis.
          digest_hash      sha256hex NOT NULL,
          -- The whole document, validated field by field against analysis_spec.py on the
          -- way in (extra='forbid' at every level, every string bounded). Stored as one
          -- jsonb rather than 40 columns because the phone renders it whole and the spec
          -- version, not the table, is what defines its shape.
          body             jsonb NOT NULL,
          created_at       timestamptz NOT NULL DEFAULT now(),
          updated_at       timestamptz NOT NULL DEFAULT now()
        );

        GRANT SELECT, INSERT, UPDATE, DELETE ON session_analysis TO builder_app, builder_worker;

        ALTER TABLE session_analysis ENABLE ROW LEVEL SECURITY;
        ALTER TABLE session_analysis FORCE  ROW LEVEL SECURITY;

        -- Mirrors stats_owner / stats_public from 0003 + 0004 exactly.
        CREATE POLICY analysis_owner ON session_analysis
          USING (EXISTS (SELECT 1 FROM sessions s WHERE s.id = session_id
                         AND s.user_id = NULLIF(current_setting('app.viewer_id', true), '')::uuid))
          WITH CHECK (EXISTS (
            SELECT 1 FROM sessions s WHERE s.id = session_id
              AND s.user_id = NULLIF(current_setting('app.viewer_id', true), '')::uuid));

        CREATE POLICY analysis_public ON session_analysis FOR SELECT USING (
          EXISTS (
            SELECT 1 FROM sessions s
            WHERE s.id = session_id AND s.is_shared
              AND NOT session_repo_excluded(s.user_id, s.repo_id)
          )
        );
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP POLICY IF EXISTS analysis_public ON session_analysis;
        DROP POLICY IF EXISTS analysis_owner  ON session_analysis;
        DROP TABLE IF EXISTS session_analysis;

        DROP INDEX IF EXISTS sessions_live_idx;
        DROP INDEX IF EXISTS sessions_record_idx;

        ALTER TABLE sessions
          DROP COLUMN IF EXISTS end_reason,
          DROP COLUMN IF EXISTS presence_count,
          DROP COLUMN IF EXISTS autonomous_seconds,
          DROP COLUMN IF EXISTS attended_seconds;

        -- A live snapshot is what 0001 called an open session. Mapping rather than
        -- deleting: the row is real work, it just loses the name for "not finished yet".
        UPDATE sessions SET state = 'open' WHERE state = 'live';

        -- Postgres cannot DROP VALUE from an enum, and refuses to retype a column that a
        -- policy mentions ("cannot alter type of a column used in a policy definition"),
        -- so: drop the policy, swap the type, put the policy back exactly as 0004 wrote it.
        DROP POLICY IF EXISTS sessions_public ON sessions;

        ALTER TYPE sess_state RENAME TO sess_state_v1;
        CREATE TYPE sess_state AS ENUM ('open','idle','finalizing','final');
        ALTER TABLE sessions ALTER COLUMN state DROP DEFAULT;
        ALTER TABLE sessions ALTER COLUMN state TYPE sess_state
          USING state::text::sess_state;
        ALTER TABLE sessions ALTER COLUMN state SET DEFAULT 'final';
        DROP TYPE sess_state_v1;

        CREATE POLICY sessions_public ON sessions FOR SELECT USING (
          is_shared
          AND superseded_by IS NULL
          AND state = 'final'
          AND NOT session_repo_excluded(user_id, repo_id)
        );

        CREATE INDEX sessions_record_idx ON sessions (user_id, active_seconds DESC)
          WHERE state = 'final' AND notable AND NOT unattended AND time_quality = 'ok';
        """
    )
