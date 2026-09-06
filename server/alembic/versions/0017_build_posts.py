"""posts: a build post, spanning sessions, about a project rather than a sitting.

Revision ID: 0017_build_posts

The feed is builders sharing what they are building. Every post so far has been ABOUT ONE
SESSION, which is the wrong unit for that: a feature takes four sittings across a week and
none of them on their own is the thing anybody wants to read about. `session_id` was
`UNIQUE NOT NULL`, so "what I built this week" was unrepresentable.

Three changes, and the third is the one that matters:

1. `session_id` becomes NULLABLE. A post about a session still cascades from it, exactly
   as before. A post with no session cannot: it is reached by `user_id`, which cascades
   from `users`, and by the repo sweep through `repo_id` below.

2. The UNIQUE constraint becomes a PARTIAL unique index. One post per session is still the
   rule where there IS a session; without the `WHERE` clause Postgres would treat every
   NULL as distinct anyway, but stating it partially says out loud that the rule applies
   to session posts and not to build posts, of which a person may have many.

3. `shipped` holds the document (spec/shipped.v1.json), validated field by field against
   shipped_spec.py on the way in. `repo_id` says which project it is about, so an excluded
   repository's sweep reaches build posts the same way it reaches session posts, and the
   feed can group a person's posts by what they are building.

WHY THE SWEEP MATTERS ENOUGH TO CARRY A COLUMN: `repo_visibility` lets somebody exclude a
repository after the fact, and the exclusion has to be able to find everything it should
take down. A build post naming a private project and reachable only through sessions that
were never uploaded would survive the sweep and stay public, which is the 0004 failure
in a new place: a rule that reads as though it enforces exclusion and enforces nothing.

`posts_owner` and `posts_visible` are written on `user_id` and `visibility`, neither of
which moved, so they cover the new rows unchanged. `can_view_post`, which `posts_visible`
calls, does NOT: it INNER JOINs `sessions` on `p.session_id`, so a post with no session
matches nothing and the function returns false. A build post would have been invisible to
everyone including its own author, with no error anywhere.

That is the good failure direction and still a bug, and it is the reason this migration
replaces the function rather than only adding columns. The join is now LEFT, and the
exclusion check takes its `(user_id, repo_id)` from the session for a session post and
from the post itself for a build post. It goes through the SAME `session_repo_excluded`
in both cases: the first draft of this migration added a `repo_excluded_for_owner` with
the arguments the other way round, which is one rule written twice and the exact thing
this codebase treats as a bug. It reads `repo_visibility` as SECURITY DEFINER with a
fixed search_path for the reason 0004 exists: a policy predicate that reads another
RLS-protected table sees it through the viewer's own eyes, so an outsider's subquery
returns zero rows, NOT EXISTS comes back true, and the exclusion fails OPEN.
"""

from alembic import op

VIEWER = "NULLIF(current_setting('app.viewer_id', true), '')::uuid"

revision = "0017_build_posts"
down_revision = "0016_builder_narrative"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        f"""
        ALTER TABLE posts ALTER COLUMN session_id DROP NOT NULL;

        -- The UNIQUE constraint carried the NOT NULL rule's assumption with it.
        ALTER TABLE posts DROP CONSTRAINT IF EXISTS posts_session_id_key;
        CREATE UNIQUE INDEX posts_one_per_session
          ON posts (session_id) WHERE session_id IS NOT NULL;

        ALTER TABLE posts
          -- Which project this is about. Null for a session post, which reaches its repo
          -- through the session; required for a build post, which has no other route.
          ADD COLUMN repo_id uuid REFERENCES repos(id) ON DELETE CASCADE,
          -- The whole document, spec/shipped.v1.json, validated by shipped_spec.py on the
          -- way in (extra='forbid', every string bounded). One jsonb rather than a column
          -- per field, for the reason 0006 gives: the phone renders it whole and the spec
          -- version, not the table, defines its shape.
          ADD COLUMN shipped jsonb,
          -- Exactly one kind of post. A row with neither is a post about nothing; a row
          -- with both is two posts wearing one id, and the feed would have to guess which
          -- half to render.
          ADD CONSTRAINT posts_are_one_thing CHECK (
            (session_id IS NOT NULL AND shipped IS NULL)
            OR (session_id IS NULL AND shipped IS NOT NULL AND repo_id IS NOT NULL)
          );

        CREATE INDEX posts_repo_created_idx ON posts (repo_id, created_at DESC)
          WHERE repo_id IS NOT NULL;

        -- See the docstring: the 0007 version INNER JOINs `sessions`, so every build post
        -- would be invisible to everyone, its author included.
        CREATE OR REPLACE FUNCTION can_view_post(p_post uuid)
        RETURNS boolean
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = public, pg_temp
        AS $$
          SELECT EXISTS (
            SELECT 1
            FROM posts p
            LEFT JOIN sessions s ON s.id = p.session_id
            WHERE p.id = p_post
              -- One rule, one function. A session post asks about the session's
              -- repository, a build post about its own.
              AND NOT session_repo_excluded(
                    COALESCE(s.user_id, p.user_id), COALESCE(s.repo_id, p.repo_id))
              AND (
                p.user_id = {VIEWER}
                OR p.visibility = 'public'
                OR (p.visibility = 'followers' AND EXISTS (
                      SELECT 1 FROM follows f
                      WHERE f.follower_id = {VIEWER}
                        AND f.followee_id = p.user_id
                        AND f.state = 'accepted'))
              )
          )
        $$;
        ALTER FUNCTION can_view_post(uuid) OWNER TO builder_worker;
        """
    )


def downgrade() -> None:
    op.execute(
        f"""
        DELETE FROM posts WHERE session_id IS NULL;
        -- can_view_post goes back to its 0007 body: an inner join on sessions, which is
        -- correct once every post has one again.
        CREATE OR REPLACE FUNCTION can_view_post(p_post uuid)
        RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = public, pg_temp AS $$
          SELECT EXISTS (
            SELECT 1 FROM posts p JOIN sessions s ON s.id = p.session_id
            WHERE p.id = p_post
              AND NOT session_repo_excluded(s.user_id, s.repo_id)
              AND (p.user_id = {VIEWER} OR p.visibility = 'public'
                   OR (p.visibility = 'followers' AND EXISTS (
                         SELECT 1 FROM follows f
                         WHERE f.follower_id = {VIEWER} AND f.followee_id = p.user_id
                           AND f.state = 'accepted')))
          )
        $$;
        ALTER FUNCTION can_view_post(uuid) OWNER TO builder_worker;
        DROP INDEX IF EXISTS posts_repo_created_idx;
        ALTER TABLE posts DROP CONSTRAINT IF EXISTS posts_are_one_thing;
        ALTER TABLE posts DROP COLUMN IF EXISTS shipped, DROP COLUMN IF EXISTS repo_id;
        DROP INDEX IF EXISTS posts_one_per_session;
        ALTER TABLE posts ADD CONSTRAINT posts_session_id_key UNIQUE (session_id);
        ALTER TABLE posts ALTER COLUMN session_id SET NOT NULL;
        """
    )
