"""Social: posts, media, kudos, comments, follows, factions.

Revision ID: 0007_social

docs/social.md, server side. Seven tables, every one RLS ENABLE + FORCE, and four
SECURITY DEFINER helpers. The helpers are the part to read carefully.

Every read policy that has to look at ANOTHER table goes through a SECURITY DEFINER
function — the 0004 lesson. `can_view_post` has to read `posts`, `follows`, `sessions` and
`repo_visibility` to decide whether a viewer may see a post; three of those are
RLS-protected, and a bare subquery in a policy would evaluate them through the viewer's own
eyes: an outsider's `follows` lookup returns nothing, an outsider's exclusion check returns
nothing, and `NOT EXISTS` comes back true. It would read as though it enforced the rule and
fail OPEN. `is_faction_member`, `join_faction` and `faction_board` exist for the same
reason: a person joining by code cannot see the faction yet, and a member reading the board
cannot see the other members' sessions at all.

Like 0005's helpers they are OWNED BY `builder_worker`. FORCE ROW LEVEL SECURITY applies to
the table owner too, so a SECURITY DEFINER function owned by a non-superuser owner would
itself see zero rows and every post would be invisible to everyone but its author. Note
the failure direction there is CLOSED, which is the better one — but `join_faction` would
then insert nothing and report success. `builder_worker` is BYPASSRLS by definition.

The denormalised `kudos_count` / `comment_count` are maintained by triggers, not by the
routes. The route giving kudos runs as the kudos-giver, who has no UPDATE right on someone
else's post row; a trigger function owned by `builder_worker` does. `builder_app` is also
stripped of table-level UPDATE on the tables where a column must never be set from a
request (a post's counts, a follow's follower, a member's role) and granted it back per
column, so the invariant holds against the API's own role rather than against its habits.
"""

from alembic import op

revision = "0007_social"
down_revision = "0006_boundaries_and_analysis"
branch_labels = None
depends_on = None

# The same expression every policy since 0003 uses. An unset viewer yields NULL, which
# matches no row, rather than erroring or acting as a wildcard.
VIEWER = "NULLIF(current_setting('app.viewer_id', true), '')::uuid"

# Pairing-code alphabet from auth.py: no vowels, no 0/O/1/I/L. A faction code is read
# aloud across a table, which is the same channel as a code read off a laptop screen.
CODE_RE = "^[BCDFGHJKMNPQRSTVWXYZ23456789]{4}-[BCDFGHJKMNPQRSTVWXYZ23456789]{4}$"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE posts (
          id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          -- One post per session, and the post dies with the session: un-sharing, an
          -- excluded repository's sweep and account deletion all reach it through here.
          session_id     uuid UNIQUE NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
          user_id        uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          caption        text CHECK (char_length(caption) <= 1000),
          visibility     text NOT NULL DEFAULT 'private'
                         CHECK (visibility IN ('private','followers','public')),
          -- The analysis headline and summary travel with every visible post. The rest
          -- of the document (dimensions, prompting, growth edge) describes the person,
          -- not the build, and leaves the machine only when they say so.
          share_analysis boolean NOT NULL DEFAULT false,
          kudos_count    integer NOT NULL DEFAULT 0 CHECK (kudos_count >= 0),
          comment_count  integer NOT NULL DEFAULT 0 CHECK (comment_count >= 0),
          created_at     timestamptz NOT NULL DEFAULT now(),
          updated_at     timestamptz NOT NULL DEFAULT now()
        );
        -- The feed is keyset-paginated on (created_at, id); the id breaks ties so two
        -- posts committed in the same microsecond cannot be skipped or repeated.
        CREATE INDEX posts_created_idx      ON posts (created_at DESC, id DESC);
        CREATE INDEX posts_user_created_idx ON posts (user_id, created_at DESC);

        CREATE TABLE post_media (
          id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          post_id     uuid NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
          kind        text NOT NULL CHECK (kind IN ('photo','audio')),
          -- The object store holds the bytes; this row holds only where and how big.
          object_key  text UNIQUE NOT NULL,
          width       integer CHECK (width > 0),
          height      integer CHECK (height > 0),
          duration_ms integer CHECK (duration_ms > 0),
          position    smallint NOT NULL DEFAULT 0,
          created_at  timestamptz NOT NULL DEFAULT now(),
          -- A photo has dimensions and no duration; a voice note is the reverse, and is
          -- at most 90 s. The route checks this too; the CHECK is what makes it true.
          CONSTRAINT post_media_shape_ck CHECK (
            (kind = 'photo' AND width IS NOT NULL AND height IS NOT NULL
               AND duration_ms IS NULL)
            OR (kind = 'audio' AND duration_ms IS NOT NULL AND duration_ms <= 90000)
          )
        );
        CREATE INDEX post_media_post_idx ON post_media (post_id, position);

        CREATE TABLE kudos (
          user_id    uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          post_id    uuid NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
          created_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (user_id, post_id)
        );
        CREATE INDEX kudos_post_idx ON kudos (post_id);

        CREATE TABLE comments (
          id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          post_id    uuid NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
          user_id    uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          body       text NOT NULL CHECK (char_length(body) BETWEEN 1 AND 500),
          created_at timestamptz NOT NULL DEFAULT now(),
          -- Soft delete: the row stays so the thread's shape survives, the body does not
          -- leave the server again.
          deleted_at timestamptz
        );
        CREATE INDEX comments_post_idx ON comments (post_id, created_at);

        CREATE TABLE follows (
          follower_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          followee_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          state       text NOT NULL DEFAULT 'pending'
                      CHECK (state IN ('pending','accepted')),
          created_at  timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (follower_id, followee_id),
          CONSTRAINT follows_not_self_ck CHECK (follower_id <> followee_id)
        );
        -- "Who follows me, and is it accepted" — the question `can_view_post` asks.
        CREATE INDEX follows_followee_idx ON follows (followee_id, state);

        CREATE TABLE factions (
          id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          slug       text UNIQUE NOT NULL CHECK (slug ~ '^[a-z0-9][a-z0-9-]{{1,39}}$'),
          name       text NOT NULL CHECK (char_length(name) BETWEEN 1 AND 60),
          join_code  text UNIQUE NOT NULL CHECK (join_code ~ '{CODE_RE}'),
          open       boolean NOT NULL DEFAULT false,
          -- The board resets at 04:00 Monday in THIS zone (docs/social.md). IANA name,
          -- validated by the route; a bad zone here would make "this week" undefined.
          tz         text NOT NULL DEFAULT 'UTC',
          created_by uuid REFERENCES users(id) ON DELETE SET NULL,
          created_at timestamptz NOT NULL DEFAULT now()
        );

        CREATE TABLE faction_members (
          faction_id  uuid NOT NULL REFERENCES factions(id) ON DELETE CASCADE,
          user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          role        text NOT NULL DEFAULT 'member' CHECK (role IN ('admin','member')),
          -- The board sees only the hours a member allows it to. Off means the member
          -- is listed with zeros, not hidden: a club knows who is in it.
          share_hours boolean NOT NULL DEFAULT true,
          joined_at   timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (faction_id, user_id)
        );
        CREATE INDEX faction_members_user_idx ON faction_members (user_id);

        GRANT SELECT, INSERT, UPDATE, DELETE
          ON posts, post_media, kudos, comments, follows, factions, faction_members
          TO builder_app, builder_worker;

        -- Column-level UPDATE for the request role. The counts are trigger-owned; a
        -- follow's parties and a member's role are fixed at insert. `builder_worker`
        -- keeps the table-level grant from above.
        REVOKE UPDATE ON posts, comments, follows, faction_members FROM builder_app;
        GRANT UPDATE (caption, visibility, share_analysis, updated_at) ON posts TO builder_app;
        GRANT UPDATE (deleted_at)  ON comments        TO builder_app;
        GRANT UPDATE (state)       ON follows         TO builder_app;
        GRANT UPDATE (share_hours) ON faction_members TO builder_app;

        -- ------------------------------------------------------------ helpers

        -- Who may see a post. Owner; or public; or followers-only and the viewer's follow
        -- is accepted — and never once the session's repository is excluded, which is
        -- the same check `sessions_public` makes, through the same function.
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
            JOIN sessions s ON s.id = p.session_id
            WHERE p.id = p_post
              AND NOT session_repo_excluded(s.user_id, s.repo_id)
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

        CREATE OR REPLACE FUNCTION is_faction_member(p_faction uuid)
        RETURNS boolean
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = public, pg_temp
        AS $$
          SELECT EXISTS (
            SELECT 1 FROM faction_members
            WHERE faction_id = p_faction AND user_id = {VIEWER}
          )
        $$;

        -- Joining. The joiner cannot see the faction row yet (not a member, and it may not
        -- be open), so the lookup AND the insert happen here, as the definer: the
        -- invariant "nobody is a member without the code or an open door" is enforced by
        -- the database, not by the route that happened to call it. Returns the faction id,
        -- or NULL when nothing matched. Code takes precedence over slug when both are sent.
        CREATE OR REPLACE FUNCTION join_faction(p_code text, p_slug text)
        RETURNS uuid
        LANGUAGE plpgsql
        VOLATILE
        SECURITY DEFINER
        SET search_path = public, pg_temp
        AS $$
        DECLARE
          v_viewer  uuid := {VIEWER};
          v_faction uuid;
        BEGIN
          IF v_viewer IS NULL THEN
            RETURN NULL;
          END IF;
          IF p_code IS NOT NULL THEN
            SELECT id INTO v_faction FROM factions WHERE join_code = p_code;
          ELSIF p_slug IS NOT NULL THEN
            SELECT id INTO v_faction FROM factions WHERE slug = p_slug AND open;
          END IF;
          IF v_faction IS NULL THEN
            RETURN NULL;
          END IF;
          INSERT INTO faction_members (faction_id, user_id, role)
          VALUES (v_faction, v_viewer, 'member')
          ON CONFLICT (faction_id, user_id) DO NOTHING;
          RETURN v_faction;
        END
        $$;

        -- The weekly board. ATTENDED seconds, never active: ranked by active, a bot farm
        -- wins (the longest session in the reference corpus had zero typed prompts).
        -- Members who turned `share_hours` off are listed with zeros. Returns nothing at
        -- all unless the viewer is a member, so a leaked faction id buys no aggregate.
        CREATE OR REPLACE FUNCTION faction_board(p_faction uuid, p_week_start date)
        RETURNS TABLE (
          member_id uuid, member_role text, member_share_hours boolean,
          attended_seconds bigint, session_count bigint, longest_attended_seconds bigint
        )
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = public, pg_temp
        AS $$
          SELECT fm.user_id, fm.role, fm.share_hours,
                 CASE WHEN fm.share_hours THEN COALESCE(SUM(s.attended_seconds), 0) ELSE 0 END,
                 CASE WHEN fm.share_hours THEN COUNT(s.id) ELSE 0 END,
                 CASE WHEN fm.share_hours THEN COALESCE(MAX(s.attended_seconds), 0) ELSE 0 END
          FROM faction_members fm
          LEFT JOIN sessions s
            ON s.user_id = fm.user_id
           AND s.state = 'final' AND s.visible
           -- local_date already carries the 04:00 day boundary (sync.py), so a week is
           -- seven of them starting on the Monday the caller resolved in the faction tz.
           AND s.local_date >= p_week_start
           AND s.local_date <  p_week_start + 7
          WHERE fm.faction_id = p_faction
            AND EXISTS (
              SELECT 1 FROM faction_members me
              WHERE me.faction_id = p_faction AND me.user_id = {VIEWER})
          GROUP BY fm.user_id, fm.role, fm.share_hours, fm.joined_at
          ORDER BY 4 DESC, fm.joined_at, fm.user_id
        $$;

        -- Count maintenance. AFTER triggers, owned by the bypass role, so the giver of
        -- kudos need not (and does not) hold UPDATE on the post.
        CREATE OR REPLACE FUNCTION kudos_count_sync()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = public, pg_temp
        AS $$
        BEGIN
          IF TG_OP = 'INSERT' THEN
            UPDATE posts SET kudos_count = kudos_count + 1 WHERE id = NEW.post_id;
            RETURN NEW;
          END IF;
          UPDATE posts SET kudos_count = GREATEST(kudos_count - 1, 0) WHERE id = OLD.post_id;
          RETURN OLD;
        END
        $$;

        -- A comment counts while it is not soft-deleted. Undeleting is not a route, but
        -- the trigger handles both directions so the count can never drift from the rows.
        CREATE OR REPLACE FUNCTION comment_count_sync()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = public, pg_temp
        AS $$
        DECLARE
          delta integer := 0;
        BEGIN
          IF TG_OP = 'INSERT' THEN
            delta := CASE WHEN NEW.deleted_at IS NULL THEN 1 ELSE 0 END;
          ELSIF TG_OP = 'DELETE' THEN
            delta := CASE WHEN OLD.deleted_at IS NULL THEN -1 ELSE 0 END;
          ELSE
            delta := (CASE WHEN NEW.deleted_at IS NULL THEN 1 ELSE 0 END)
                   - (CASE WHEN OLD.deleted_at IS NULL THEN 1 ELSE 0 END);
          END IF;
          IF delta <> 0 THEN
            UPDATE posts SET comment_count = GREATEST(comment_count + delta, 0)
            WHERE id = COALESCE(NEW.post_id, OLD.post_id);
          END IF;
          RETURN COALESCE(NEW, OLD);
        END
        $$;

        CREATE TRIGGER kudos_count_trg
          AFTER INSERT OR DELETE ON kudos
          FOR EACH ROW EXECUTE FUNCTION kudos_count_sync();
        CREATE TRIGGER comment_count_trg
          AFTER INSERT OR UPDATE OF deleted_at OR DELETE ON comments
          FOR EACH ROW EXECUTE FUNCTION comment_count_sync();

        ALTER FUNCTION can_view_post(uuid)         OWNER TO builder_worker;
        ALTER FUNCTION is_faction_member(uuid)     OWNER TO builder_worker;
        ALTER FUNCTION join_faction(text, text)    OWNER TO builder_worker;
        ALTER FUNCTION faction_board(uuid, date)   OWNER TO builder_worker;
        ALTER FUNCTION kudos_count_sync()          OWNER TO builder_worker;
        ALTER FUNCTION comment_count_sync()        OWNER TO builder_worker;

        REVOKE ALL ON FUNCTION can_view_post(uuid)       FROM PUBLIC;
        REVOKE ALL ON FUNCTION is_faction_member(uuid)   FROM PUBLIC;
        REVOKE ALL ON FUNCTION join_faction(text, text)  FROM PUBLIC;
        REVOKE ALL ON FUNCTION faction_board(uuid, date) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION can_view_post(uuid)       TO builder_app, builder_worker;
        GRANT EXECUTE ON FUNCTION is_faction_member(uuid)   TO builder_app, builder_worker;
        GRANT EXECUTE ON FUNCTION join_faction(text, text)  TO builder_app, builder_worker;
        GRANT EXECUTE ON FUNCTION faction_board(uuid, date) TO builder_app, builder_worker;

        -- ------------------------------------------------------------ policies

        ALTER TABLE posts           ENABLE ROW LEVEL SECURITY;
        ALTER TABLE posts           FORCE  ROW LEVEL SECURITY;
        ALTER TABLE post_media      ENABLE ROW LEVEL SECURITY;
        ALTER TABLE post_media      FORCE  ROW LEVEL SECURITY;
        ALTER TABLE kudos           ENABLE ROW LEVEL SECURITY;
        ALTER TABLE kudos           FORCE  ROW LEVEL SECURITY;
        ALTER TABLE comments        ENABLE ROW LEVEL SECURITY;
        ALTER TABLE comments        FORCE  ROW LEVEL SECURITY;
        ALTER TABLE follows         ENABLE ROW LEVEL SECURITY;
        ALTER TABLE follows         FORCE  ROW LEVEL SECURITY;
        ALTER TABLE factions        ENABLE ROW LEVEL SECURITY;
        ALTER TABLE factions        FORCE  ROW LEVEL SECURITY;
        ALTER TABLE faction_members ENABLE ROW LEVEL SECURITY;
        ALTER TABLE faction_members FORCE  ROW LEVEL SECURITY;

        -- The owner policy is separate from `can_view_post` on purpose: an author must
        -- still be able to delete a post whose repository was excluded after the fact,
        -- even though nobody (author included) may READ it through the visibility path.
        CREATE POLICY posts_owner ON posts
          USING (user_id = {VIEWER})
          WITH CHECK (user_id = {VIEWER});
        CREATE POLICY posts_visible ON posts FOR SELECT
          USING (can_view_post(id));

        -- Reads `posts` under RLS, which is honest here: the predicate only needs the
        -- rows the viewer owns, and the owner policy shows exactly those.
        CREATE POLICY media_owner ON post_media
          USING (EXISTS (SELECT 1 FROM posts p WHERE p.id = post_id AND p.user_id = {VIEWER}))
          WITH CHECK (EXISTS (SELECT 1 FROM posts p WHERE p.id = post_id AND p.user_id = {VIEWER}));
        CREATE POLICY media_visible ON post_media FOR SELECT
          USING (can_view_post(post_id));

        -- Kudos and comments: insert only on a post you can see, and as yourself. Split
        -- per command rather than FOR ALL, because permissive policies OR together — a
        -- FOR ALL owner policy would let an INSERT through without the visibility check.
        CREATE POLICY kudos_select ON kudos FOR SELECT
          USING (user_id = {VIEWER} OR can_view_post(post_id));
        CREATE POLICY kudos_insert ON kudos FOR INSERT
          WITH CHECK (user_id = {VIEWER} AND can_view_post(post_id));
        CREATE POLICY kudos_delete ON kudos FOR DELETE
          USING (user_id = {VIEWER});

        CREATE POLICY comments_select ON comments FOR SELECT
          USING (user_id = {VIEWER} OR can_view_post(post_id));
        CREATE POLICY comments_insert ON comments FOR INSERT
          WITH CHECK (user_id = {VIEWER} AND can_view_post(post_id));
        CREATE POLICY comments_update ON comments FOR UPDATE
          USING (user_id = {VIEWER})
          WITH CHECK (user_id = {VIEWER});
        CREATE POLICY comments_delete ON comments FOR DELETE
          USING (user_id = {VIEWER});

        -- A follow is visible to both parties. Only the follower can create one, and an
        -- ACCEPTED one only towards a public profile — otherwise a follower could skip
        -- approval by inserting the accepted state directly. `users` has no RLS, so the
        -- subquery on it sees the truth.
        CREATE POLICY follows_party ON follows FOR SELECT
          USING (follower_id = {VIEWER} OR followee_id = {VIEWER});
        CREATE POLICY follows_request ON follows FOR INSERT
          WITH CHECK (
            follower_id = {VIEWER}
            AND (state = 'pending'
                 OR EXISTS (SELECT 1 FROM users u WHERE u.id = followee_id AND u.profile_public))
          );
        CREATE POLICY follows_accept ON follows FOR UPDATE
          USING (followee_id = {VIEWER})
          WITH CHECK (followee_id = {VIEWER});
        CREATE POLICY follows_end ON follows FOR DELETE
          USING (follower_id = {VIEWER} OR followee_id = {VIEWER});

        CREATE POLICY factions_visible ON factions FOR SELECT
          USING (open OR created_by = {VIEWER} OR is_faction_member(id));
        CREATE POLICY factions_create ON factions FOR INSERT
          WITH CHECK (created_by = {VIEWER});
        CREATE POLICY factions_admin ON factions FOR UPDATE
          USING (created_by = {VIEWER})
          WITH CHECK (created_by = {VIEWER});
        CREATE POLICY factions_delete ON factions FOR DELETE
          USING (created_by = {VIEWER});

        -- Members see the roster. The only direct INSERT is the creator seating
        -- themselves as admin; everyone else arrives through `join_faction`.
        CREATE POLICY members_visible ON faction_members FOR SELECT
          USING (is_faction_member(faction_id));
        CREATE POLICY members_found ON faction_members FOR INSERT
          WITH CHECK (
            user_id = {VIEWER} AND role = 'admin'
            AND EXISTS (SELECT 1 FROM factions f
                        WHERE f.id = faction_id AND f.created_by = {VIEWER})
          );
        CREATE POLICY members_self ON faction_members FOR UPDATE
          USING (user_id = {VIEWER})
          WITH CHECK (user_id = {VIEWER});
        CREATE POLICY members_leave ON faction_members FOR DELETE
          USING (user_id = {VIEWER});
        """
    )


def downgrade() -> None:
    op.execute(
        """
        -- A session shared through a post was shared BY the post. With the social layer
        -- gone it must not stay world-readable through `sessions_public`.
        UPDATE sessions SET is_shared = false, shared_at = NULL
        WHERE id IN (SELECT session_id FROM posts);

        DROP TRIGGER IF EXISTS comment_count_trg ON comments;
        DROP TRIGGER IF EXISTS kudos_count_trg ON kudos;

        -- Policies go with their tables.
        DROP TABLE IF EXISTS faction_members, factions, follows, comments, kudos,
                             post_media, posts CASCADE;

        DROP FUNCTION IF EXISTS comment_count_sync();
        DROP FUNCTION IF EXISTS kudos_count_sync();
        DROP FUNCTION IF EXISTS faction_board(uuid, date);
        DROP FUNCTION IF EXISTS join_faction(text, text);
        DROP FUNCTION IF EXISTS is_faction_member(uuid);
        DROP FUNCTION IF EXISTS can_view_post(uuid);
        """
    )
