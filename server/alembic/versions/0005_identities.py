"""Identities: one user, many sign-in providers. Plus the bootstrap lookups RLS needs.

Revision ID: 0005_identities

Two things happen here, and the second is the one to read carefully.

1. `users.apple_sub` stops being THE identity. A user is now a row in `users`; how they
   prove they are that user is a row in `identities` keyed by (provider, subject). Apple
   subs are backfilled so nothing existing changes hands. `apple_sub` stays, nullable and
   still UNIQUE, because it is cheaper to keep a legacy column honest than to explain a
   dropped one to every query that still mentions it.

   `users.email` is deliberately NOT unique. Apple hands out per-app relay addresses, a
   Google account and an Apple account can report the same address for two different
   people's devices, and an address can be verified by one provider and unverified by
   another. An email is a contact detail, not an identifier; nothing here merges on it.

2. `devices`, `push_tokens` and now `identities` are RLS-protected with an owner policy —
   and every sign-in path has to read or write them BEFORE it knows who the viewer is.
   Under the `sessions_public` lesson from 0004 (a policy that reads an RLS-protected table
   sees it through the viewer's eyes), a viewer-less `INSERT INTO devices` fails WITH
   CHECK, a viewer-less JOIN through `devices` returns nothing, and `/v1/auth/refresh`
   answers 401 to every valid token. Not a crash you notice in a test that runs as the
   owner, because the owner bypasses RLS.

   The two SECURITY DEFINER functions below are the only sanctioned way to cross that gap:
   `identity_user(provider, subject)` turns a verified token into a user id, and
   `device_owner(device_id)` turns a refresh token's device into one. Once the id is known
   the request sets `app.viewer_id` and every other table is read under the normal policy.

   They are OWNED BY `builder_worker`, not by whichever role ran the migration. FORCE ROW
   LEVEL SECURITY applies to the table owner too, so a SECURITY DEFINER function owned by a
   non-superuser owner would itself see zero rows and every sign-in would quietly mint a
   fresh account. `builder_worker` is BYPASSRLS by definition, so the lookup works no
   matter who ran `alembic upgrade`.
"""

from alembic import op

revision = "0005_identities"
down_revision = "0004_exclusion_fix"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE identities (
          user_id        uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          provider       text NOT NULL CHECK (provider IN ('apple','google')),
          subject        text NOT NULL,
          email          text,
          email_verified boolean,
          created_at     timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (provider, subject)
        );
        CREATE INDEX identities_user_id_idx ON identities (user_id);

        -- Every existing account signed in with Apple; carry the sub across verbatim so
        -- the next sign-in resolves to the same row it always did. The two prefixes are
        -- this migration's OWN downgrade sentinels (see below): a Google-only user who
        -- went through downgrade must come back as a Google identity, not a fake Apple one.
        INSERT INTO identities (user_id, provider, subject, email, email_verified, created_at)
        SELECT id, 'apple', apple_sub, email_relay, NULL, created_at
        FROM users
        WHERE apple_sub IS NOT NULL
          AND apple_sub NOT LIKE 'google:%'
          AND apple_sub NOT LIKE 'orphan:%';

        INSERT INTO identities (user_id, provider, subject, created_at)
        SELECT id, 'google', substr(apple_sub, length('google:') + 1), created_at
        FROM users
        WHERE apple_sub LIKE 'google:%';

        ALTER TABLE users ALTER COLUMN apple_sub DROP NOT NULL;
        ALTER TABLE users ADD COLUMN email citext;

        UPDATE users SET apple_sub = NULL
        WHERE apple_sub LIKE 'google:%' OR apple_sub LIKE 'orphan:%';

        GRANT SELECT, INSERT, UPDATE, DELETE ON identities TO builder_app, builder_worker;

        ALTER TABLE identities ENABLE ROW LEVEL SECURITY;
        ALTER TABLE identities FORCE  ROW LEVEL SECURITY;
        CREATE POLICY identities_owner ON identities
          USING (user_id = NULLIF(current_setting('app.viewer_id', true), '')::uuid)
          WITH CHECK (user_id = NULLIF(current_setting('app.viewer_id', true), '')::uuid);

        -- Pre-viewer lookups. See the module docstring for why these exist and why they
        -- are owned by builder_worker. STABLE, SECURITY DEFINER, pinned search_path.
        CREATE OR REPLACE FUNCTION identity_user(p_provider text, p_subject text)
        RETURNS uuid
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = public, pg_temp
        AS $$
          SELECT user_id FROM identities
          WHERE provider = p_provider AND subject = p_subject
        $$;

        CREATE OR REPLACE FUNCTION device_owner(p_device_id uuid)
        RETURNS uuid
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = public, pg_temp
        AS $$
          SELECT user_id FROM devices WHERE id = p_device_id
        $$;

        ALTER FUNCTION identity_user(text, text) OWNER TO builder_worker;
        ALTER FUNCTION device_owner(uuid)        OWNER TO builder_worker;

        REVOKE ALL ON FUNCTION identity_user(text, text) FROM PUBLIC;
        REVOKE ALL ON FUNCTION device_owner(uuid)        FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION identity_user(text, text) TO builder_app;
        GRANT EXECUTE ON FUNCTION device_owner(uuid)        TO builder_app;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP FUNCTION IF EXISTS device_owner(uuid);
        DROP FUNCTION IF EXISTS identity_user(text, text);

        -- Put every Apple sub back where 0001 expects it. A user who only ever signed in
        -- with Google has no Apple sub to restore; rather than delete their account to
        -- satisfy NOT NULL, they get a tagged sentinel that can never collide with a real
        -- sub (Apple subs are dotted numerics, never prefixed) and never verify as one.
        UPDATE users u
        SET apple_sub = i.subject
        FROM identities i
        WHERE i.user_id = u.id AND i.provider = 'apple' AND u.apple_sub IS NULL;

        UPDATE users u
        SET apple_sub = 'google:' || i.subject
        FROM identities i
        WHERE i.user_id = u.id AND i.provider = 'google' AND u.apple_sub IS NULL;

        UPDATE users SET apple_sub = 'orphan:' || id::text WHERE apple_sub IS NULL;

        ALTER TABLE users ALTER COLUMN apple_sub SET NOT NULL;
        ALTER TABLE users DROP COLUMN IF EXISTS email;

        DROP POLICY IF EXISTS identities_owner ON identities;
        DROP TABLE IF EXISTS identities;
        """
    )
