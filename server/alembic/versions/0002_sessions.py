"""Sessions, their stats, and their strips.

Revision ID: 0002_sessions
"""

from alembic import op

revision = "0002_sessions"
down_revision = "0001_core"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE sessions (
          id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          user_id             uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          device_id           uuid NOT NULL REFERENCES devices(id) ON DELETE CASCADE,

          -- Same name as on the client and as the Idempotency-Key. One identifier,
          -- everywhere: three names for the same value is how a sync bug becomes
          -- undebuggable.
          client_session_id   sha256hex NOT NULL,
          content_hash        sha256hex NOT NULL,
          public_slug         text UNIQUE,

          -- Set when a session absorbs another after a boundary moves. The loser's public
          -- URL 301s here rather than 404ing, because it may already have been shared.
          superseded_by       uuid REFERENCES sessions(id),

          sessionizer_version integer NOT NULL,
          active_calc_version integer NOT NULL,
          harness             harness NOT NULL,
          repo_id             uuid REFERENCES repos(id) ON DELETE SET NULL,
          merge_group_id      uuid,

          started_at          timestamptz NOT NULL,
          ended_at            timestamptz NOT NULL,
          active_seconds      integer NOT NULL CHECK (active_seconds >= 0),
          idle_seconds        integer NOT NULL DEFAULT 0,
          tz_offset_minutes   smallint NOT NULL,

          -- ONE name for the local date. An earlier draft had records.py querying
          -- `local_date` while the DDL created `day`; the query simply returned nothing.
          local_date          date NOT NULL,
          local_hour          smallint NOT NULL CHECK (local_hour BETWEEN 0 AND 23),
          local_dow           smallint NOT NULL CHECK (local_dow BETWEEN 0 AND 6),

          state               sess_state NOT NULL DEFAULT 'final',
          visible             boolean NOT NULL DEFAULT true,
          notable             boolean NOT NULL DEFAULT false,
          unattended          boolean NOT NULL DEFAULT false,
          time_quality        text NOT NULL DEFAULT 'ok'
                              CHECK (time_quality IN ('ok','mtime_corrected')),
          timeline_fidelity   text NOT NULL,

          -- Public repos only. Enforced by a validator on the way in, because the client
          -- omits the key entirely for anonymous repos and a NULL here must mean "not
          -- sent" rather than "sent as empty".
          title               text,
          title_source        text,

          is_shared           boolean NOT NULL DEFAULT false,
          shared_at           timestamptz,
          card_png_url        text,

          agent_observed_at   timestamptz NOT NULL,
          created_at          timestamptz NOT NULL DEFAULT now(),
          updated_at          timestamptz NOT NULL DEFAULT now(),

          CONSTRAINT sessions_span_ck   CHECK (ended_at >= started_at),
          -- Active time can never exceed elapsed. The client extends `ended_at` by the
          -- trailing gap credit precisely so this holds; if it ever fails, the client's
          -- active-time arithmetic has regressed.
          CONSTRAINT sessions_active_ck
            CHECK (active_seconds <= EXTRACT(epoch FROM (ended_at - started_at)) + 1),
          CONSTRAINT sessions_shared_ck CHECK (NOT is_shared OR shared_at IS NOT NULL),
          CONSTRAINT sessions_client_uq UNIQUE (user_id, client_session_id)
        );

        CREATE INDEX sessions_user_started_idx ON sessions (user_id, started_at DESC);
        CREATE INDEX sessions_user_date_idx    ON sessions (user_id, local_date);
        CREATE INDEX sessions_record_idx       ON sessions (user_id, active_seconds DESC)
          WHERE state = 'final' AND notable AND NOT unattended AND time_quality = 'ok';

        -- The co-op index. Built now because adding a GiST index to a large table later
        -- is a migration nobody wants to run; queried only when clubs exist.
        CREATE INDEX sessions_repo_span_idx ON sessions
          USING gist (repo_id, tstzrange(started_at, ended_at, '[]'));

        CREATE TABLE session_strips (
          session_id   uuid PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
          spec_version smallint NOT NULL DEFAULT 1,
          t0_ms        bigint NOT NULL,
          t1_ms        bigint NOT NULL,
          -- Exactly 1024 bytes. Fixed size means the payload is ~1.4 KB whatever the
          -- session length, and it is text-free by construction: no path, prompt or
          -- filename can hide in a 2-bit-per-column array.
          cols         bytea NOT NULL CHECK (octet_length(cols) = 1024),
          marks        jsonb NOT NULL DEFAULT '[]'
        );

        CREATE TABLE session_stats (
          session_id       uuid PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,

          tokens_reported  boolean NOT NULL,
          tok_in           bigint,
          tok_out          bigint,
          tok_cache_read   bigint,
          -- 5m and 1h cache writes stay SEPARATE: they price differently, and a single
          -- summed column would silently pick one definition for everyone downstream.
          tok_cache_w5m    bigint,
          tok_cache_w1h    bigint,
          abandoned_branch_tokens bigint NOT NULL DEFAULT 0,

          token_dedupe     text NOT NULL CHECK (token_dedupe IN ('message_id','request_id','none')),
          -- 'flat' is deliberately NOT a legal value. A second legal scope would let the
          -- ~3x subagent overcount into the database wearing a legitimate label, which is
          -- worse than rejecting it.
          token_scope      text NOT NULL CHECK (token_scope IN ('parent_aggregated','mixed')),
          token_coverage   text NOT NULL
                           CHECK (token_coverage IN ('complete','partial','structurally_absent')),

          models           jsonb NOT NULL DEFAULT '[]',
          model_state      text NOT NULL CHECK (model_state IN ('known','partial','unknown')),
          tool_calls       jsonb NOT NULL DEFAULT '{}',

          human_prompt_count integer NOT NULL DEFAULT 0,
          -- Claude Code can tell a typed prompt from a system injection; Cursor cannot.
          -- Carrying the basis is what stops the two being summed as if they meant the
          -- same thing.
          prompt_count_basis text NOT NULL
                             CHECK (prompt_count_basis IN ('typed_promptsource','user_bubble')),

          files_touched    integer NOT NULL DEFAULT 0,
          files_created    integer NOT NULL DEFAULT 0,
          lines_added_agent integer NOT NULL DEFAULT 0,
          lines_removed_agent integer NOT NULL DEFAULT 0,
          commit_count     integer NOT NULL DEFAULT 0,
          commit_insertions integer NOT NULL DEFAULT 0,
          commit_deletions integer NOT NULL DEFAULT 0,
          human_edit_events integer NOT NULL DEFAULT 0,

          agent_line_bucket text NOT NULL CHECK (agent_line_bucket IN
            ('almost_all_agent','nine_in_ten','three_in_four','about_half',
             'mostly_you','unknown')),
          attrib_confidence text NOT NULL
            CHECK (attrib_confidence IN ('high','medium','low','none')),

          -- Tokens must be absent rather than zero when unreported. Cursor writes
          -- {0,0} locally and the client turns that into NULL; a stored 0 would read as
          -- "this session used no tokens", which is a different and false claim.
          CONSTRAINT stats_tokens_ck CHECK (tokens_reported OR tok_in IS NULL),
          -- The 1.878x overcount arriving unlabelled must be impossible to store.
          CONSTRAINT stats_dedupe_ck CHECK (NOT tokens_reported OR token_dedupe <> 'none')
        );

        CREATE TABLE deletion_requests (
          id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          user_id      uuid NOT NULL,
          requested_at timestamptz NOT NULL DEFAULT now(),
          completed_at timestamptz,
          row_counts   jsonb,
          -- The only artifact that survives a deletion, and it carries no PII: proof the
          -- request was honoured without keeping anything about who made it.
          receipt_hmac sha256hex
        );
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE IF EXISTS deletion_requests, session_stats, session_strips, sessions CASCADE;
        """
    )
