"""Core schema: users, devices, repos, sessions, stats, strips.

Revision ID: 0001_core
"""

from alembic import op

revision = "0001_core"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE EXTENSION IF NOT EXISTS pgcrypto;
        CREATE EXTENSION IF NOT EXISTS citext;
        CREATE EXTENSION IF NOT EXISTS btree_gist;

        -- text with a CHECK, never char(64): char() blank-pads, so a 64-hex value
        -- round-trips fine but anything shorter comes back with trailing spaces and
        -- silently stops matching.
        CREATE DOMAIN sha256hex AS text CHECK (VALUE ~ '^[0-9a-f]{64}$');

        -- 'cursor_ide', NOT 'cursor'. The client emits cursor_ide; a mismatched label is
        -- a hard 22P02 that aborts the entire batch insert and takes the Claude Code rows
        -- down with it.
        CREATE TYPE harness AS ENUM ('claude_code','cursor_ide','cursor_agent','codex');
        CREATE TYPE repo_vis AS ENUM ('public','anonymous','excluded');
        CREATE TYPE sess_state AS ENUM ('open','idle','finalizing','final');

        CREATE TABLE users (
          id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          -- Team-stable ONLY if the App IDs are grouped under the primary in the Sign in
          -- with Apple pane. Ungrouped, Apple scopes `sub` per App ID, the same human
          -- gets two accounts with split history, and this UNIQUE makes the second one
          -- fail in a way that looks like a bug.
          apple_sub     text UNIQUE NOT NULL,
          handle        citext UNIQUE,
          display_name  text,
          email_relay   text,
          tz            text NOT NULL DEFAULT 'UTC',
          profile_public boolean NOT NULL DEFAULT false,
          created_at    timestamptz NOT NULL DEFAULT now(),
          deleted_at    timestamptz
        );

        CREATE TABLE devices (
          id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          user_id        uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          label          text NOT NULL,
          platform       text NOT NULL,
          agent_version  text NOT NULL,
          machine_id     sha256hex NOT NULL,
          clock_offset_ms integer,
          paired_at      timestamptz NOT NULL DEFAULT now(),
          last_seen_at   timestamptz,
          revoked_at     timestamptz,
          UNIQUE (user_id, machine_id)
        );

        -- Refresh tokens are stored as sha256 only, and rotate. `prev_id` forms a chain so
        -- reuse of a spent token is detectable — reuse means the token leaked, and the
        -- correct response is to revoke the whole chain rather than to issue another.
        CREATE TABLE device_tokens (
          id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          device_id    uuid NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
          refresh_hash sha256hex UNIQUE NOT NULL,
          prev_id      uuid REFERENCES device_tokens(id) ON DELETE SET NULL,
          issued_at    timestamptz NOT NULL DEFAULT now(),
          expires_at   timestamptz NOT NULL,
          used_at      timestamptz,
          revoked_at   timestamptz
        );

        -- Pairing codes for the RFC 8628 device grant. The agent is open source, so any
        -- embedded client secret would be public by construction; the device flow is
        -- designed for exactly that (and is what `gh auth login` uses).
        CREATE TABLE device_grants (
          device_code   sha256hex PRIMARY KEY,
          user_code     text UNIQUE NOT NULL,
          machine_id    sha256hex NOT NULL,
          label         text NOT NULL,
          platform      text NOT NULL,
          agent_version text NOT NULL,
          user_id       uuid REFERENCES users(id) ON DELETE CASCADE,
          approved_at   timestamptz,
          expires_at    timestamptz NOT NULL,
          created_at    timestamptz NOT NULL DEFAULT now()
        );

        CREATE TABLE repos (
          id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          repo_hash     sha256hex UNIQUE NOT NULL,
          pepper_version smallint NOT NULL,
          repo_id_basis text NOT NULL CHECK (repo_id_basis IN ('origin','root_commit')),
          public_name   text,
          first_seen_at timestamptz NOT NULL DEFAULT now()
        );

        -- Visibility is a property of (user, repo) and lives SERVER-side. A per-session
        -- copy would let the same repository be public on one session and anonymous on
        -- another, which is not a state a user can reason about or revoke.
        CREATE TABLE repo_visibility (
          user_id    uuid REFERENCES users(id) ON DELETE CASCADE,
          repo_id    uuid REFERENCES repos(id) ON DELETE CASCADE,
          visibility repo_vis NOT NULL DEFAULT 'anonymous',
          updated_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (user_id, repo_id)
        );

        CREATE TABLE push_tokens (
          id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          token       text NOT NULL,
          -- A sandbox token and a production token are indistinguishable by inspection,
          -- and sending to the wrong host returns BadDeviceToken. Recording which
          -- environment issued it is what stops push from breaking during TestFlight.
          environment text NOT NULL CHECK (environment IN ('sandbox','production')),
          created_at  timestamptz NOT NULL DEFAULT now(),
          last_used_at timestamptz,
          UNIQUE (user_id, token)
        );
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE IF EXISTS push_tokens, repo_visibility, repos, device_grants,
                             device_tokens, devices, users CASCADE;
        DROP TYPE IF EXISTS sess_state, repo_vis, harness;
        DROP DOMAIN IF EXISTS sha256hex;
        """
    )
