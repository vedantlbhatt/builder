"""capture_keys: a non-rotating credential for headless uploaders.

Revision ID: 0011_capture_keys

The device flow issues ROTATING refresh tokens, and a spent token presented again revokes
the whole device (0001, `redeem_refresh_token`). That is the right rule for a Mac and the
wrong one for a fleet: `capture/` copied into several cloud containers works exactly once,
because the second container presents the token the first already rotated, and the user
is back to pairing every container (docs/cloud-capture.md). A capture key is the credential
that rule cannot break — it does not rotate, so any number of containers may hold the same
one — and in exchange it can do exactly one thing: upload sessions.

Shape:

* `device_id` is a `devices` row created WITH the key (`register_device`, platform
  `capture`), so every sync path, `sessions.device_id`, the owner policies and
  `device_owner` keep working unchanged. Revoking the key revokes the device row too.
* `key_hash` is sha256 of the plaintext; the plaintext is returned once by the route that
  minted it and never stored. `key_prefix` is the first eight characters (`bck_` plus
  four) — enough to tell keys apart on the phone, far too little to guess the rest.
* `revoked_at` rather than DELETE, so a revoked key is a 401 that stays a 401 and the
  list can still say when it was last used.

RLS ENABLE + FORCE with the owner policy from `devices`; owner only, no public policy.
`builder_app`'s UPDATE is column-scoped like 0007's: `last_used_at` (touched by the auth
path) and `revoked_at` (the revoke route); `name` and `key_hash` are fixed at insert.

`capture_key_lookup(hash)` is the pre-viewer lookup, in the exact shape of 0005's
`device_owner`: the auth path holds a key and nothing else, so it cannot set a viewer
before it knows the owner, and a SELECT on `capture_keys` from a viewer-less transaction
would match nothing under the owner policy — every valid key a 401, with no error anywhere
(the 0004 lesson). SECURITY DEFINER, owned by `builder_worker` (BYPASSRLS, so FORCE ROW
LEVEL SECURITY cannot blind it whoever ran the migration), pinned search_path, EXECUTE to
`builder_app` only. Once the owner is known the request sets the viewer and every other
read goes through the normal policy.
"""

from alembic import op

revision = "0011_capture_keys"
down_revision = "0010_harness_values"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE capture_keys (
          id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          user_id      uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          device_id    uuid NOT NULL UNIQUE REFERENCES devices(id) ON DELETE CASCADE,
          name         text NOT NULL CHECK (length(name) BETWEEN 1 AND 64),
          key_hash     sha256hex NOT NULL UNIQUE,
          key_prefix   text NOT NULL CHECK (key_prefix ~ '^bck_[A-Za-z0-9_-]{4}$'),
          created_at   timestamptz NOT NULL DEFAULT now(),
          last_used_at timestamptz,
          revoked_at   timestamptz
        );
        CREATE INDEX capture_keys_user_idx ON capture_keys (user_id);

        GRANT SELECT, INSERT, UPDATE, DELETE ON capture_keys TO builder_app, builder_worker;
        REVOKE UPDATE ON capture_keys FROM builder_app;
        GRANT UPDATE (last_used_at, revoked_at) ON capture_keys TO builder_app;

        ALTER TABLE capture_keys ENABLE ROW LEVEL SECURITY;
        ALTER TABLE capture_keys FORCE  ROW LEVEL SECURITY;

        CREATE POLICY capture_keys_owner ON capture_keys
          USING (user_id = NULLIF(current_setting('app.viewer_id', true), '')::uuid)
          WITH CHECK (user_id = NULLIF(current_setting('app.viewer_id', true), '')::uuid);

        -- Pre-viewer lookup; see the module docstring. Returns the columns the auth path
        -- needs to decide (revoked?) and to rate-limit its own write (last_used_at), and
        -- nothing it does not.
        CREATE OR REPLACE FUNCTION capture_key_lookup(p_hash text)
        RETURNS TABLE (id uuid, user_id uuid, device_id uuid,
                       last_used_at timestamptz, revoked_at timestamptz)
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = public, pg_temp
        AS $$
          SELECT id, user_id, device_id, last_used_at, revoked_at
          FROM capture_keys WHERE key_hash = p_hash
        $$;

        ALTER FUNCTION capture_key_lookup(text) OWNER TO builder_worker;
        REVOKE ALL ON FUNCTION capture_key_lookup(text) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION capture_key_lookup(text) TO builder_app;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP FUNCTION IF EXISTS capture_key_lookup(text);
        DROP POLICY IF EXISTS capture_keys_owner ON capture_keys;
        DROP TABLE IF EXISTS capture_keys;
        """
    )
