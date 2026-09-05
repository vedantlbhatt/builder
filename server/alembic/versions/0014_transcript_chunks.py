"""transcript_chunks — raw transcript bytes delivered by Claude Code hooks, kept only
until the session they belong to is final.

The hook channel (docs/hooks-capture.md) is the zero-install path: Claude Code itself
POSTs the transcript tail from a `Stop` / `SessionEnd` / `UserPromptSubmit` hook, and the
server runs the same sessionizer `python -m capture` runs on a machine. Chunks are keyed
by byte offset so a hook that fires ten times a session appends ten tails and a replay is
a no-op. Owner-only, like `devices` and `capture_keys`: the bytes are the conversation.

Revision ID: 0014_transcript_chunks
Revises: 0012_end_reasons_v3
"""

from alembic import op

revision = "0014_transcript_chunks"
down_revision = "0012_end_reasons_v3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE transcript_chunks (
          id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          user_id            uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          device_id          uuid NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
          native_session_id  text NOT NULL,
          project_dir        text NOT NULL,
          byte_offset        bigint NOT NULL,
          bytes              bytea NOT NULL,
          hook               text,
          received_at        timestamptz NOT NULL DEFAULT now(),
          UNIQUE (user_id, native_session_id, byte_offset)
        );
        CREATE INDEX transcript_chunks_session
          ON transcript_chunks (user_id, native_session_id, byte_offset);

        GRANT SELECT, INSERT, DELETE ON transcript_chunks TO builder_app, builder_worker;

        ALTER TABLE transcript_chunks ENABLE ROW LEVEL SECURITY;
        ALTER TABLE transcript_chunks FORCE ROW LEVEL SECURITY;
        CREATE POLICY transcript_chunks_owner ON transcript_chunks
          USING (user_id = NULLIF(current_setting('app.viewer_id', true), '')::uuid)
          WITH CHECK (user_id = NULLIF(current_setting('app.viewer_id', true), '')::uuid);
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS transcript_chunks;")
