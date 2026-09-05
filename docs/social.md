# Social: the smallest thing that is still Strava

Builder is single-player today, on purpose. This is the design for the layer on top, written
before the code so the code has something to be wrong against. The constraint that matters:
**the feed must not be clunky.** A feed of raw sessions is a log. A feed of recap cards with a
sentence of analysis and, sometimes, a photo, is a story — that is the whole difference.

## Objects

| object | what it is | notes |
|---|---|---|
| **post** | a shared session | one per session; created by "Share", never automatically. Carries the recap card model, the analysis headline + summary (never the full analysis unless the author opts in), optional caption, photos, audio, visibility. |
| **photo** | an image on a post | up to 6; stored in object storage (S3-compatible, presigned PUT); the DB stores only the key and dimensions. Screenshots of the thing you built are the point. |
| **audio** | a voice note on a post | one, ≤ 90 s, same storage path. A person saying what they built beats a caption. Deferred behind photos. |
| **kudos** | one tap | unique per (user, post); count denormalised on the post |
| **comment** | text, ≤ 500 chars, flat (no threads) | threads are where feeds go to die |
| **follow** | directed, no approval for public profiles | private profiles require approval |
| **faction** | a club | name, slug, join code or open; members; a weekly board ranked by **attended hours**, never active hours (a bot farm would win) |
| **profile** | handle, display name, bio, public flag, archetype (rolling), totals | `users.handle` and `profile_public` already exist |

## Visibility

Three levels on a post: `private` (only you — the default for every session; sharing is the
act), `followers`, `public`. Faction posts inherit the poster's level; the faction board sees
only the aggregate hours you allow it to (`faction_share_hours` per membership). Repository
names appear on a post only when the repo is marked public — the existing visibility rule
carries through unchanged, and an excluded repo cannot be posted at all.

A shared session that is later un-shared becomes private again and disappears from every
feed and board immediately (`is_shared = false`), and its photos are deleted from storage
within the deletion sweep. The existing `sessions_public` RLS policy is what the public read
path already uses; posts reuse it through the session id.

## Feed

`GET /v1/feed?before=<cursor>` returns posts from people you follow and members of your
factions, newest first, keyset paginated, ≤ 30 per page. Each item is self-contained: the
card model, the strip (1024 bytes, base64), analysis headline/summary, photo thumbnails,
kudos count, comment count, and whether *you* gave kudos. One request renders the screen;
nothing fans out per row. A second tab, `GET /v1/feed/faction/<slug>`, is the same shape
scoped to a faction.

No algorithm. Reverse chronological, and the only ranking anywhere is the faction board,
which is a plain sum over a plain week.

## Factions

Create → you are the first admin. Join by code (`XXXX-XXXX`, same alphabet as device
pairing) or open. Board: attended hours this week, sessions this week, longest attended
session this week, current streak — per member, with the aggregates shown to everyone in the
faction and the per-session detail only for posts the member shared. Weekly reset at 04:00
Monday in the faction's chosen timezone (default: the creator's), using the same day-boundary
constant as everything else.

## Storage

Object storage is the one new dependency. Env: `OBJECT_STORE_ENDPOINT`, `OBJECT_STORE_BUCKET`,
`OBJECT_STORE_KEY`, `OBJECT_STORE_SECRET`, `OBJECT_STORE_PUBLIC_BASE`. When unset, photo
upload returns 503 with a clear message and the rest of social works; nothing else in the app
depends on it. Uploads are presigned PUTs from the phone; the server never proxies bytes.
Images are downscaled on the phone before upload (long edge ≤ 2048, JPEG q=0.85).

## Tables (migration 0007)

```
posts(id, session_id UNIQUE, user_id, caption, visibility, kudos_count, comment_count,
      created_at, updated_at)
post_media(id, post_id, kind photo|audio, object_key, width, height, duration_ms, position)
kudos(user_id, post_id, created_at, PK (user_id, post_id))
comments(id, post_id, user_id, body, created_at, deleted_at)
follows(follower_id, followee_id, state pending|accepted, created_at, PK (follower_id, followee_id))
factions(id, slug UNIQUE, name, join_code UNIQUE, open, tz, created_by, created_at)
faction_members(faction_id, user_id, role admin|member, share_hours bool, joined_at, PK)
```

Every table gets RLS. Read policies route through SECURITY DEFINER helpers
(`can_view_post(post_id)`, `is_faction_member(faction_id)`) — the lesson from
`sessions_public` applies: a policy that reads another RLS table sees it through the viewer's
own eyes and fails open. Write policies are owner-only. The boot guard's required set grows
by these tables.

## API

```
POST   /v1/posts                        {session_id, caption?, visibility}
PATCH  /v1/posts/{id}                   {caption?, visibility?}
DELETE /v1/posts/{id}
POST   /v1/posts/{id}/media:presign     {kind, content_type, bytes} -> {upload_url, object_key}
POST   /v1/posts/{id}/media             {object_key, width, height, duration_ms?}
POST   /v1/posts/{id}/kudos   DELETE /v1/posts/{id}/kudos
POST   /v1/posts/{id}/comments          {body}      DELETE /v1/comments/{id}
GET    /v1/feed?before=                 GET /v1/feed/faction/{slug}?before=
POST   /v1/follows/{handle}  DELETE /v1/follows/{handle}   POST /v1/follows/{handle}:accept
GET    /v1/users/{handle}               profile + public posts (keyset)
POST   /v1/factions  POST /v1/factions:join {code}  GET /v1/factions/{slug}/board?week=
PATCH  /v1/factions/{slug}/members/me   {share_hours}
```

## What is deliberately absent

Challenges, digests, reactions beyond kudos, reposts, DMs, an explore tab, and any ranking
that is not a sum. Each of those is a product, not a feature, and each makes the feed worse
before it makes it better. Co-op sessions (two people, one repo, overlapping time — the
`merge_group_id` seam and the GiST index already exist for it) come after factions, because
factions produce the social graph that makes co-op detection worth surfacing.
