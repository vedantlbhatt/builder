"""Fix: an excluded repository's shared sessions stayed publicly visible.

Revision ID: 0004_exclusion_fix

FOUND BY THE RLS TEST, and it is the subtle kind.

The `sessions_public` policy checked exclusion with:

    NOT EXISTS (SELECT 1 FROM repo_visibility rv WHERE ... AND rv.visibility = 'excluded')

`repo_visibility` also has row level security, with only an owner policy. So when an
anonymous viewer evaluates that subquery, it runs under *their* restricted visibility and
returns zero rows — which makes `NOT EXISTS` true, and the session stays public.

The policy read as though it enforced exclusion. It enforced nothing, and it failed OPEN:
a user who marked a repository excluded would still have its shared sessions served to
anyone. Nothing about the SQL looks wrong, and no error is ever raised.

The general lesson, worth remembering for every future policy: **a policy predicate that
reads another RLS-protected table sees that table through the viewer's own eyes.** Any
such check must go through a SECURITY DEFINER function, which runs as its owner and can
see the rows the check actually depends on.
"""

from alembic import op

revision = "0004_exclusion_fix"
down_revision = "0003_rls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        -- Runs as the function owner, so it can see repo_visibility regardless of who is
        -- asking. STABLE and with an empty search_path: a SECURITY DEFINER function with
        -- a mutable search_path is a privilege-escalation vector.
        CREATE OR REPLACE FUNCTION session_repo_excluded(p_user_id uuid, p_repo_id uuid)
        RETURNS boolean
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = public, pg_temp
        AS $$
          SELECT EXISTS (
            SELECT 1 FROM repo_visibility rv
            WHERE rv.user_id = p_user_id
              AND rv.repo_id = p_repo_id
              AND rv.visibility = 'excluded'
          );
        $$;

        REVOKE ALL ON FUNCTION session_repo_excluded(uuid, uuid) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION session_repo_excluded(uuid, uuid)
          TO builder_app, builder_worker;

        DROP POLICY IF EXISTS sessions_public ON sessions;
        CREATE POLICY sessions_public ON sessions FOR SELECT USING (
          is_shared
          AND superseded_by IS NULL
          AND state = 'final'
          AND NOT session_repo_excluded(user_id, repo_id)
        );

        -- The strip and stats policies inherit the fix by joining through sessions, but
        -- they were reading `sessions` under RLS too — which is correct here, since the
        -- sessions policy is now right and a hidden session hides its children.
        DROP POLICY IF EXISTS strips_public ON session_strips;
        CREATE POLICY strips_public ON session_strips FOR SELECT USING (
          EXISTS (
            SELECT 1 FROM sessions s
            WHERE s.id = session_id AND s.is_shared
              AND NOT session_repo_excluded(s.user_id, s.repo_id)
          )
        );

        DROP POLICY IF EXISTS stats_public ON session_stats;
        CREATE POLICY stats_public ON session_stats FOR SELECT USING (
          EXISTS (
            SELECT 1 FROM sessions s
            WHERE s.id = session_id AND s.is_shared
              AND NOT session_repo_excluded(s.user_id, s.repo_id)
          )
        );
        """
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS session_repo_excluded(uuid, uuid) CASCADE;")
