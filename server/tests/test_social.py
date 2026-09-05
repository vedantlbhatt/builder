"""The social layer end to end, AS builder_app, through the real routes.

Every guarantee here is a visibility guarantee, and every one of them can fail open
without an error: a followers-only post served before the follow is accepted, a private
post in a stranger's profile, an excluded repository's post still on the feed, a
kudos-giver rewriting the post they clapped for. So the harness is the one from
test_sync.py — the API engine pointed at `builder_app`, devices minted through pairing —
and where the guarantee is about isolation the test also reaches past the routes with a
bare connection and the other user's viewer, so a 404 cannot be the route's doing alone.

Run with a local Postgres:
    BUILDER_TEST_DB=postgresql+psycopg://localhost/builder_test pytest tests/test_social.py
"""

import base64
import re
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError
from test_contract import SAMPLE_ANALYSIS
from test_sync import (  # noqa: F401 - fixtures are picked up by name
    TEST_DB,
    _live,
    _owner_rows,
    _pair,
    _payload,
    _upload,
    app_engine,
    app_env,
    client,
    created_users,
    owner_engine,
)

pytestmark = pytest.mark.skipif(not TEST_DB, reason="set BUILDER_TEST_DB to run")

# pytest finds the imported fixtures through this module's namespace. Referencing them
# once here is what tells the linter the test parameters below do not shadow unused names.
_SHARED_FIXTURES = (app_env, client, created_users)

CODE_RE = re.compile(r"^[BCDFGHJKMNPQRSTVWXYZ23456789]{4}-[BCDFGHJKMNPQRSTVWXYZ23456789]{4}$")

# A Wednesday morning, Pacific: 16:00Z is 09:00 local, so local_date is the 12th and the
# board week is the ISO week containing it. Computed, not typed, so it cannot be off by one.
WEDNESDAY = datetime(2026, 8, 12, 16, 0, tzinfo=UTC)
_Y, _W, _ = date(2026, 8, 12).isocalendar()
WEEK = f"{_Y}-W{_W:02d}"


# ------------------------------------------------------------------------- helpers


def _handle(uid: str, handle: str, public: bool = True) -> None:
    with owner_engine().begin() as c:
        c.execute(
            text("UPDATE users SET handle = :h, profile_public = :p WHERE id = :u"),
            {"h": handle, "p": public, "u": uid},
        )


def _person(client, created_users, handle: str, public: bool = True) -> tuple[str, dict]:
    uid, headers = _pair(client, created_users)
    _handle(uid, f"{handle}-{uid[:6]}", public)
    return uid, headers


def _handle_of(uid: str) -> str:
    with owner_engine().connect() as c:
        return c.execute(text("SELECT handle FROM users WHERE id = :u"), {"u": uid}).scalar()


def _session(client, headers, uid: str, **overrides) -> str:
    """Upload one final session and return its server id."""
    p = _payload(**overrides)
    out = _upload(client, headers, p)
    assert out["accepted"] == 1, out
    return str(
        next(r.id for r in _owner_rows(uid) if r.client_session_id == p["client_session_id"])
    )


def _hours(client, headers, uid: str, hours: float, started: datetime = WEDNESDAY) -> str:
    secs = int(hours * 3600)
    return _session(
        client,
        headers,
        uid,
        started_at=started,
        ended_at=started + timedelta(seconds=secs),
        active_seconds=secs,
        attended_seconds=secs,
        autonomous_seconds=0,
        tz_offset_minutes=-420,
    )


def _post(client, headers, session_id: str, visibility: str = "public", **extra) -> dict:
    r = client.post(
        "/v1/posts",
        json={"session_id": session_id, "visibility": visibility, **extra},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    return r.json()


def _shared(session_id: str) -> tuple[bool, bool]:
    with owner_engine().connect() as c:
        row = c.execute(
            text("SELECT is_shared, shared_at FROM sessions WHERE id = :s"), {"s": session_id}
        ).one()
    return row.is_shared, row.shared_at is not None


def _feed_ids(client, headers, path: str = "/v1/feed") -> list[str]:
    r = client.get(path, headers=headers)
    assert r.status_code == 200, r.text
    return [i["id"] for i in r.json()["items"]]


def _profile_post_ids(client, headers, handle: str) -> list[str]:
    r = client.get(f"/v1/users/{handle}", headers=headers)
    assert r.status_code == 200, r.text
    return [i["id"] for i in r.json()["posts"]]


# ---------------------------------------------------------------------------- posts


def test_share_own_final_session_then_unshare(client, created_users):
    uid, h = _person(client, created_users, "alice")
    sid = _session(client, h, uid, analysis=SAMPLE_ANALYSIS)

    item = _post(client, h, sid, "public", caption="shipped the sync endpoint")
    assert item["author"]["handle"] == _handle_of(uid)
    assert item["caption"] == "shipped the sync endpoint"
    assert item["session"]["id"] == sid
    assert item["session"]["attended_seconds"] == 3600
    assert item["session"]["is_shared"] is True
    assert len(base64.b64decode(item["strip"]["cols"])) == 1024
    assert item["strip"]["marks"] == [] and item["strip"]["t0_ms"] < item["strip"]["t1_ms"]
    # Headline and summary only: the rest of the document is about the person.
    assert item["analysis"] == {
        "headline": SAMPLE_ANALYSIS["headline"],
        "summary": SAMPLE_ANALYSIS["summary"],
    }
    assert item["photos"] == [] and item["audio"] is None
    assert (item["kudos_count"], item["comment_count"], item["you_kudosed"]) == (0, 0, False)
    assert _shared(sid) == (True, True)

    # One post per session.
    r = client.post("/v1/posts", json={"session_id": sid, "visibility": "public"}, headers=h)
    assert r.status_code == 409

    # Opting the full analysis in changes the item, for everyone who can see it.
    r = client.patch(f"/v1/posts/{item['id']}", json={"share_analysis": True}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["analysis"] == SAMPLE_ANALYSIS
    assert client.get(f"/v1/posts/{item['id']}", headers=h).json()["share_analysis"] is True

    assert _feed_ids(client, h) == [item["id"]]

    r = client.delete(f"/v1/posts/{item['id']}", headers=h)
    assert r.status_code == 204
    assert _shared(sid) == (False, False), "un-sharing must take the session private again"
    assert _feed_ids(client, h) == []
    assert client.get(f"/v1/posts/{item['id']}", headers=h).status_code == 404
    # And the session is still there, unshared.
    assert client.get(f"/v1/sessions/{sid}", headers=h).json()["is_shared"] is False


def _detail_post_id(client, headers, session_id: str):
    r = client.get(f"/v1/sessions/{session_id}", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "post_id" in body, "null, never absent"
    return body["post_id"]


def test_session_detail_carries_only_the_viewers_own_post_id(client, created_users):
    """`post_id` on a session is the VIEWER's post, not the session's.

    The negative half has to reach the join. A stranger cannot read an unshared session
    at all, so asserting null on one would pass with the join missing entirely; the
    session here is shared through a followers-only post, which every viewer can read via
    `sessions_public` while only an accepted follower can read the post. The victim's post
    is proven to exist first, as the victim; then the stranger is upgraded to a follower
    who demonstrably CAN read the post, and still gets null — which is what separates
    "the predicate filtered it" from "RLS happened to hide it".
    """
    uid_a, h_a = _person(client, created_users, "alice", public=False)
    uid_b, h_b = _person(client, created_users, "bob")
    alice, bob = _handle_of(uid_a), _handle_of(uid_b)
    sid = _session(client, h_a, uid_a)

    # Before any post: the key is present and null on the detail and on the list.
    assert _detail_post_id(client, h_a, sid) is None
    listed = client.get("/v1/sessions", headers=h_a).json()["sessions"]
    assert [(x["id"], x["post_id"]) for x in listed] == [(sid, None)]

    post = _post(client, h_a, sid, "followers")

    # The post exists, as the victim sees it: through the route and in the table.
    assert client.get(f"/v1/posts/{post['id']}", headers=h_a).status_code == 200
    with owner_engine().connect() as c:
        row = c.execute(
            text("SELECT user_id, visibility FROM posts WHERE id = CAST(:p AS uuid)"),
            {"p": post["id"]},
        ).one()
    assert (str(row.user_id), row.visibility) == (uid_a, "followers")
    assert _shared(sid) == (True, True)

    # Owner: the id, on the detail and on the list.
    assert _detail_post_id(client, h_a, sid) == post["id"]
    listed = client.get("/v1/sessions", headers=h_a).json()["sessions"]
    assert [(x["id"], x["post_id"]) for x in listed] == [(sid, post["id"])]

    # Stranger: the session is readable (200, so the join ran), the post is not, null.
    assert client.get(f"/v1/posts/{post['id']}", headers=h_b).status_code == 404
    assert _detail_post_id(client, h_b, sid) is None

    # Accepted follower: the post IS readable to Bob now, and the session still says null,
    # because the field means "your post", not "a post you may see".
    assert client.post(f"/v1/follows/{alice}", headers=h_b).json()["state"] == "pending"
    assert client.post(f"/v1/follows/{bob}:accept", headers=h_a).json()["state"] == "accepted"
    assert client.get(f"/v1/posts/{post['id']}", headers=h_b).status_code == 200
    assert _detail_post_id(client, h_b, sid) is None

    # Same for a public post: anyone can read it, nobody but the author gets it here.
    r = client.patch(f"/v1/posts/{post['id']}", json={"visibility": "public"}, headers=h_a)
    assert r.status_code == 200, r.text
    assert client.get(f"/v1/posts/{post['id']}", headers=h_b).status_code == 200
    assert _detail_post_id(client, h_b, sid) is None
    assert _detail_post_id(client, h_a, sid) == post["id"]

    # A private post: the session drops out of `sessions_public`, so the stranger gets
    # 404 rather than a null, and the owner keeps the id through the owner policies.
    r = client.patch(f"/v1/posts/{post['id']}", json={"visibility": "private"}, headers=h_a)
    assert r.status_code == 200, r.text
    assert _shared(sid) == (False, False)
    assert client.get(f"/v1/sessions/{sid}", headers=h_b).status_code == 404
    assert _detail_post_id(client, h_a, sid) == post["id"]

    # Un-sharing takes it back to null, not to a dangling id.
    assert client.delete(f"/v1/posts/{post['id']}", headers=h_a).status_code == 204
    assert _detail_post_id(client, h_a, sid) is None


def test_a_live_session_cannot_be_shared(client, created_users):
    uid, h = _person(client, created_users, "alice")
    live = _live(datetime(2026, 8, 15, 16, 0, tzinfo=UTC), 20)
    _upload(client, h, live)
    sid = str(_owner_rows(uid)[0].id)
    r = client.post("/v1/posts", json={"session_id": sid, "visibility": "public"}, headers=h)
    assert r.status_code == 409, r.text
    assert _shared(sid) == (False, False)


def test_someone_elses_session_is_404_when_hidden_and_403_when_visible(client, created_users):
    uid_a, h_a = _person(client, created_users, "alice")
    _, h_b = _person(client, created_users, "bob")
    sid = _session(client, h_a, uid_a)

    # Unshared: under RLS Bob cannot see it at all, so it does not exist for him.
    r = client.post("/v1/posts", json={"session_id": sid, "visibility": "public"}, headers=h_b)
    assert r.status_code == 404
    # Shared: Bob can read it through sessions_public, and still cannot post it.
    _post(client, h_a, sid, "public")
    r = client.post("/v1/posts", json={"session_id": sid, "visibility": "public"}, headers=h_b)
    assert r.status_code == 403
    assert client.post("/v1/posts", json={"session_id": "nope"}, headers=h_b).status_code == 422
    assert (
        client.post(
            "/v1/posts", json={"session_id": sid, "visibility": "loud"}, headers=h_a
        ).status_code
        == 422
    )


def test_visibility_private_public_and_pagination(client, created_users):
    uid_a, h_a = _person(client, created_users, "alice")
    uid_b, h_b = _person(client, created_users, "bob")
    alice = _handle_of(uid_a)

    public = _post(client, h_a, _session(client, h_a, uid_a), "public")
    private = _post(client, h_a, _session(client, h_a, uid_a), "private")
    second_public = _post(client, h_a, _session(client, h_a, uid_a), "public")

    # A private post does not raise is_shared: sessions_public would make the session,
    # strip and analysis readable to anyone holding the uuid, and private means only you.
    assert _shared(private["session"]["id"]) == (False, False)
    assert _shared(public["session"]["id"]) == (True, True)

    # Stranger: public ones only, newest first. Owner: everything.
    assert _profile_post_ids(client, h_b, alice) == [second_public["id"], public["id"]]
    assert client.get(f"/v1/posts/{private['id']}", headers=h_b).status_code == 404
    assert client.get(f"/v1/posts/{public['id']}", headers=h_b).status_code == 200
    assert _profile_post_ids(client, h_a, alice) == [
        second_public["id"],
        private["id"],
        public["id"],
    ]
    assert _feed_ids(client, h_a) == [second_public["id"], private["id"], public["id"]]
    # Bob follows nobody: his feed is empty even though Alice's public posts exist.
    assert _feed_ids(client, h_b) == []

    # Keyset paging over the owner's view.
    first = client.get(f"/v1/users/{alice}?limit=2", headers=h_a).json()
    assert [p["id"] for p in first["posts"]] == [second_public["id"], private["id"]]
    assert first["next_before"] and first["next_before_id"] == private["id"]
    cursor = {"limit": 2, "before": first["next_before"], "before_id": first["next_before_id"]}
    rest = client.get(f"/v1/users/{alice}", params=cursor, headers=h_a).json()
    assert [p["id"] for p in rest["posts"]] == [public["id"]]
    assert rest["next_before"] is None
    # The cursor pasted in raw, "+00:00" arriving as " 00:00", reads the same.
    raw = client.get(
        f"/v1/users/{alice}?limit=2&before={first['next_before']}"
        f"&before_id={first['next_before_id']}",
        headers=h_a,
    )
    assert raw.status_code == 200, raw.text
    assert [p["id"] for p in raw.json()["posts"]] == [public["id"]]
    assert client.get("/v1/feed?before=yesterday", headers=h_a).status_code == 422
    assert client.get("/v1/feed?limit=31", headers=h_a).status_code == 422

    # Reaching past the route: the private post really is invisible to Bob's viewer.
    with app_engine().connect() as c:
        c.execute(text("SELECT set_config('app.viewer_id', :v, false)"), {"v": uid_b})
        seen = {
            str(r.id)
            for r in c.execute(text("SELECT id FROM posts WHERE user_id = :a"), {"a": uid_a}).all()
        }
    assert seen == {public["id"], second_public["id"]}


# -------------------------------------------------------------------------- follows


def test_followers_post_shows_only_after_the_follow_is_accepted(client, created_users):
    uid_a, h_a = _person(client, created_users, "alice", public=False)
    uid_b, h_b = _person(client, created_users, "bob")
    uid_c, h_c = _person(client, created_users, "cara", public=True)
    alice, bob, cara = _handle_of(uid_a), _handle_of(uid_b), _handle_of(uid_c)

    post = _post(client, h_a, _session(client, h_a, uid_a), "followers")

    # Private profile: the follow is a request.
    r = client.post(f"/v1/follows/{alice}", headers=h_b)
    assert r.status_code == 200 and r.json()["state"] == "pending", r.text
    assert _feed_ids(client, h_b) == []
    assert _profile_post_ids(client, h_b, alice) == []
    assert client.get(f"/v1/posts/{post['id']}", headers=h_b).status_code == 404
    profile = client.get(f"/v1/users/{alice}", headers=h_b).json()["profile"]
    assert profile["follow_state"] == "pending" and profile["profile_public"] is False

    # Accepting is the followee's act, and only a pending request can be accepted.
    assert client.post(f"/v1/follows/{bob}:accept", headers=h_c).status_code == 404
    r = client.post(f"/v1/follows/{bob}:accept", headers=h_a)
    assert r.status_code == 200 and r.json()["state"] == "accepted", r.text
    assert _feed_ids(client, h_b) == [post["id"]]
    assert _profile_post_ids(client, h_b, alice) == [post["id"]]
    item = client.get(f"/v1/posts/{post['id']}", headers=h_b).json()
    assert item["visibility"] == "followers" and item["you_kudosed"] is False

    # Cara never followed: nothing, even though she is public herself.
    assert _feed_ids(client, h_c) == []
    assert client.get(f"/v1/posts/{post['id']}", headers=h_c).status_code == 404

    # Public profile: immediate.
    r = client.post(f"/v1/follows/{cara}", headers=h_b)
    assert r.json()["state"] == "accepted"
    assert client.post(f"/v1/follows/{bob}", headers=h_b).status_code == 422
    assert client.post("/v1/follows/nobody-here", headers=h_b).status_code == 404

    # Unfollowing takes the post away again, immediately.
    assert client.delete(f"/v1/follows/{alice}", headers=h_b).status_code == 204
    assert _feed_ids(client, h_b) == []

    # The policy, not just the route: Bob cannot insert an accepted follow of a private
    # profile straight into the table.
    with app_engine().connect() as c:
        c.execute(text("SELECT set_config('app.viewer_id', :v, false)"), {"v": uid_b})
        with pytest.raises(ProgrammingError):
            c.execute(
                text(
                    "INSERT INTO follows (follower_id, followee_id, state) "
                    "VALUES (:b, :a, 'accepted')"
                ),
                {"b": uid_b, "a": uid_a},
            )


# ------------------------------------------------------------------ kudos, comments


def test_kudos_toggle_updates_counts_and_you_kudosed(client, created_users):
    uid_a, h_a = _person(client, created_users, "alice")
    uid_b, h_b = _person(client, created_users, "bob")
    post = _post(client, h_b, _session(client, h_b, uid_b), "public")
    hidden = _post(client, h_b, _session(client, h_b, uid_b), "private")

    r = client.post(f"/v1/posts/{post['id']}/kudos", headers=h_a)
    assert r.status_code == 200 and r.json() == {"kudos_count": 1, "you_kudosed": True}
    # Idempotent: a second tap is not a second kudos.
    assert client.post(f"/v1/posts/{post['id']}/kudos", headers=h_a).json()["kudos_count"] == 1

    mine = client.get(f"/v1/posts/{post['id']}", headers=h_a).json()
    theirs = client.get(f"/v1/posts/{post['id']}", headers=h_b).json()
    assert (mine["kudos_count"], mine["you_kudosed"]) == (1, True)
    assert (theirs["kudos_count"], theirs["you_kudosed"]) == (1, False)

    r = client.delete(f"/v1/posts/{post['id']}/kudos", headers=h_a)
    assert r.json() == {"kudos_count": 0, "you_kudosed": False}
    assert client.delete(f"/v1/posts/{post['id']}/kudos", headers=h_a).json()["kudos_count"] == 0

    # A post you cannot see, you cannot clap for — through the route or past it.
    assert client.post(f"/v1/posts/{hidden['id']}/kudos", headers=h_a).status_code == 404
    with app_engine().connect() as c:
        c.execute(text("SELECT set_config('app.viewer_id', :v, false)"), {"v": uid_a})
        with pytest.raises(ProgrammingError):
            c.execute(
                text("INSERT INTO kudos (user_id, post_id) VALUES (:u, :p)"),
                {"u": uid_a, "p": hidden["id"]},
            )
    with owner_engine().connect() as c:
        assert (
            c.execute(
                text("SELECT kudos_count FROM posts WHERE id = :p"), {"p": hidden["id"]}
            ).scalar()
            == 0
        )


def test_comment_then_soft_delete(client, created_users):
    uid_a, h_a = _person(client, created_users, "alice")
    uid_b, h_b = _person(client, created_users, "bob")
    post = _post(client, h_b, _session(client, h_b, uid_b), "public")

    r = client.post(
        f"/v1/posts/{post['id']}/comments", json={"body": "  nice strip  "}, headers=h_a
    )
    assert r.status_code == 201, r.text
    comment = r.json()
    assert comment["body"] == "nice strip" and comment["comment_count"] == 1
    assert comment["author"]["handle"] == _handle_of(uid_a)
    assert client.get(f"/v1/posts/{post['id']}", headers=h_b).json()["comment_count"] == 1
    listed = client.get(f"/v1/posts/{post['id']}/comments", headers=h_b).json()["comments"]
    assert [c["id"] for c in listed] == [comment["id"]]

    too_long = {"body": "x" * 501}
    assert (
        client.post(f"/v1/posts/{post['id']}/comments", json=too_long, headers=h_a).status_code
        == 422
    )
    assert (
        client.post(f"/v1/posts/{post['id']}/comments", json={"body": ""}, headers=h_a).status_code
        == 422
    )

    # Only the author deletes, and deleting is soft: the row stays, the body stops.
    assert client.delete(f"/v1/comments/{comment['id']}", headers=h_b).status_code == 404
    assert client.delete(f"/v1/comments/{comment['id']}", headers=h_a).status_code == 204
    assert client.delete(f"/v1/comments/{comment['id']}", headers=h_a).status_code == 404
    assert client.get(f"/v1/posts/{post['id']}/comments", headers=h_b).json()["comments"] == []
    assert client.get(f"/v1/posts/{post['id']}", headers=h_b).json()["comment_count"] == 0
    with owner_engine().connect() as c:
        row = c.execute(
            text("SELECT body, deleted_at FROM comments WHERE id = :c"), {"c": comment["id"]}
        ).one()
    assert row.deleted_at is not None and row.body == "nice strip"


# -------------------------------------------------------------------------- factions


def test_faction_create_join_by_code_and_board_by_attended_hours(client, created_users):
    uid_a, h_a = _person(client, created_users, "alice")
    uid_b, h_b = _person(client, created_users, "bob")
    uid_c, h_c = _person(client, created_users, "cara")
    alice, bob = _handle_of(uid_a), _handle_of(uid_b)
    slug = f"night-owls-{uid_a[:6]}"

    r = client.post(
        "/v1/factions",
        json={"name": "Night Owls", "slug": slug, "tz": "America/Los_Angeles"},
        headers=h_a,
    )
    assert r.status_code == 201, r.text
    faction = r.json()
    assert faction["slug"] == slug and faction["role"] == "admin"
    assert CODE_RE.match(faction["join_code"]), faction["join_code"]
    assert (
        client.post("/v1/factions", json={"name": "x", "slug": slug}, headers=h_b).status_code
        == 409
    )
    bad_tz = {"name": "y", "tz": "Mars/Olympus"}
    assert client.post("/v1/factions", json=bad_tz, headers=h_b).status_code == 422

    # Join by code, typed sloppily. A wrong code finds nothing; a closed faction is not
    # even visible to a non-member.
    sloppy = faction["join_code"].lower().replace("-", " ")
    r = client.post("/v1/factions:join", json={"code": sloppy}, headers=h_b)
    assert r.status_code == 200 and r.json()["role"] == "member", r.text
    assert "join_code" not in r.json(), "members do not get the invite code back"
    assert (
        client.post("/v1/factions:join", json={"code": "BBBB-2222"}, headers=h_c).status_code == 404
    )
    assert client.post("/v1/factions:join", json={"slug": slug}, headers=h_c).status_code == 404
    assert client.get(f"/v1/factions/{slug}/board", headers=h_c).status_code == 404
    assert client.get(f"/v1/feed/faction/{slug}", headers=h_c).status_code == 404

    # Hours this week. Bob attended more, across two sessions; Alice one longer-idle one.
    _hours(client, h_a, uid_a, 2)
    _hours(client, h_b, uid_b, 3)
    _hours(client, h_b, uid_b, 1, started=WEDNESDAY + timedelta(days=1))
    # Cara-like noise that must not count: Alice's autonomous-heavy session adds active
    # hours but only 10 attended minutes, and a session from another week is out of range.
    _session(
        client,
        h_a,
        uid_a,
        started_at=WEDNESDAY + timedelta(days=2),
        ended_at=WEDNESDAY + timedelta(days=2, hours=4),
        active_seconds=14400,
        attended_seconds=600,
        autonomous_seconds=13800,
        tz_offset_minutes=-420,
    )
    _hours(client, h_a, uid_a, 9, started=WEDNESDAY + timedelta(days=14))

    r = client.get(f"/v1/factions/{slug}/board?week={WEEK}", headers=h_a)
    assert r.status_code == 200, r.text
    board = r.json()
    assert board["week"] == WEEK and board["faction"]["join_code"] == faction["join_code"]
    rows = [
        (m["handle"], m["attended_seconds"], m["sessions"], m["longest_attended_seconds"])
        for m in board["members"]
    ]
    assert rows == [(bob, 14400, 2, 10800), (alice, 7800, 2, 7200)]
    assert board["members"][1]["you"] is True and board["members"][0]["role"] == "member"

    # Members see the board too, without the code.
    as_bob = client.get(f"/v1/factions/{slug}/board?week={WEEK}", headers=h_b).json()
    assert "join_code" not in as_bob["faction"] and as_bob["faction"]["role"] == "member"

    # Opting out zeroes the row without removing it, and the ranking follows.
    r = client.patch(f"/v1/factions/{slug}/members/me", json={"share_hours": False}, headers=h_b)
    assert r.status_code == 200 and r.json()["share_hours"] is False
    board = client.get(f"/v1/factions/{slug}/board?week={WEEK}", headers=h_a).json()
    rows = [
        (m["handle"], m["attended_seconds"], m["sessions"], m["share_hours"])
        for m in board["members"]
    ]
    assert rows == [(alice, 7800, 2, True), (bob, 0, 0, False)]

    # Default week resolves in the faction's zone and answers; bad weeks are 422.
    r = client.get(f"/v1/factions/{slug}/board", headers=h_a)
    assert r.status_code == 200 and re.match(r"^\d{4}-W\d{2}$", r.json()["week"])
    assert client.get(f"/v1/factions/{slug}/board?week=2026-W60", headers=h_a).status_code == 422
    assert client.get(f"/v1/factions/{slug}/board?week=week33", headers=h_a).status_code == 422

    # The faction feed: members' posts, each still under its own visibility.
    post = _post(client, h_b, _session(client, h_b, uid_b), "public")
    hidden = _post(client, h_b, _session(client, h_b, uid_b), "followers")
    assert _feed_ids(client, h_a, f"/v1/feed/faction/{slug}") == [post["id"]]
    assert hidden["id"] not in _feed_ids(client, h_a)
    # ...and faction-mates' public posts reach the main feed without a follow.
    assert _feed_ids(client, h_a) == [post["id"]]

    # An open faction can be joined by slug; a non-member can see it but not its board.
    open_slug = f"open-{uid_a[:6]}"
    r = client.post(
        "/v1/factions", json={"name": "Open Door", "slug": open_slug, "open": True}, headers=h_a
    )
    assert r.status_code == 201 and r.json()["tz"] == "UTC"
    assert client.get(f"/v1/factions/{open_slug}/board", headers=h_c).status_code == 403
    r = client.post("/v1/factions:join", json={"slug": open_slug}, headers=h_c)
    assert r.status_code == 200 and r.json()["role"] == "member"
    assert client.get(f"/v1/factions/{open_slug}/board", headers=h_c).status_code == 200

    # Past the route: a member cannot promote themselves, and the other faction's roster
    # is invisible to a non-member.
    with app_engine().connect() as c:
        c.execute(text("SELECT set_config('app.viewer_id', :v, false)"), {"v": uid_c})
        assert (
            c.execute(
                text(
                    "SELECT count(*) FROM faction_members fm "
                    "JOIN factions f ON f.id = fm.faction_id WHERE f.slug = :s"
                ),
                {"s": slug},
            ).scalar()
            == 0
        )
        with pytest.raises(ProgrammingError):
            c.execute(
                text("UPDATE faction_members SET role = 'admin' WHERE user_id = :u"), {"u": uid_c}
            )


# ------------------------------------------------------------------- exclusion, RLS


def test_excluding_the_repo_hides_a_public_post(client, created_users):
    uid_a, h_a = _person(client, created_users, "alice")
    uid_b, h_b = _person(client, created_users, "bob")
    bob = _handle_of(uid_b)
    sid = _session(client, h_b, uid_b)
    post = _post(client, h_b, sid, "public")
    assert client.get(f"/v1/posts/{post['id']}", headers=h_a).status_code == 200

    # Same path as test_rls: the exclusion row is written directly, so the session is
    # NOT deleted and the visibility check has to do the work on its own.
    with owner_engine().begin() as c:
        repo = c.execute(text("SELECT repo_id FROM sessions WHERE id = :s"), {"s": sid}).scalar()
        assert repo is not None
        c.execute(
            text(
                """
                INSERT INTO repo_visibility (user_id, repo_id, visibility)
                VALUES (:u, :r, 'excluded')
                ON CONFLICT (user_id, repo_id) DO UPDATE SET visibility = 'excluded'
                """
            ),
            {"u": uid_b, "r": repo},
        )
    try:
        assert client.get(f"/v1/posts/{post['id']}", headers=h_a).status_code == 404
        assert _profile_post_ids(client, h_a, bob) == []
        with app_engine().connect() as c:
            c.execute(text("SELECT set_config('app.viewer_id', :v, false)"), {"v": uid_a})
            assert c.execute(text("SELECT can_view_post(:p)"), {"p": post["id"]}).scalar() is False
        # The owner can still take it down, and cannot post another from that repository.
        other = _session(client, h_b, uid_b)
        r = client.post(
            "/v1/posts", json={"session_id": other, "visibility": "public"}, headers=h_b
        )
        assert r.status_code == 403
        assert client.delete(f"/v1/posts/{post['id']}", headers=h_b).status_code == 204
    finally:
        with owner_engine().begin() as c:
            c.execute(text("DELETE FROM repo_visibility WHERE user_id = :u"), {"u": uid_b})


def test_another_user_cannot_update_or_delete_a_post_directly(client, created_users):
    """As builder_app with the other viewer set — the exact position the API is in when
    it runs a request for that user. The post is PUBLIC, so the SELECT proves the viewer
    can reach the row and the zero-row UPDATE is the write policy's doing, not a 404."""
    uid_a, _ = _person(client, created_users, "alice")
    uid_b, h_b = _person(client, created_users, "bob")
    post = _post(client, h_b, _session(client, h_b, uid_b), "public", caption="mine")

    with app_engine().connect() as c:
        c.execute(text("SELECT set_config('app.viewer_id', :v, false)"), {"v": uid_a})
        assert (
            c.execute(text("SELECT count(*) FROM posts WHERE id = :p"), {"p": post["id"]}).scalar()
            == 1
        )
        assert (
            c.execute(
                text("UPDATE posts SET caption = 'hijacked' WHERE id = :p"), {"p": post["id"]}
            ).rowcount
            == 0
        )
        assert c.execute(text("DELETE FROM posts WHERE id = :p"), {"p": post["id"]}).rowcount == 0
        c.rollback()

    # Nor can the OWNER forge the counts: builder_app's UPDATE grant is per column.
    with app_engine().connect() as c:
        c.execute(text("SELECT set_config('app.viewer_id', :v, false)"), {"v": uid_b})
        with pytest.raises(ProgrammingError):
            c.execute(text("UPDATE posts SET kudos_count = 99 WHERE id = :p"), {"p": post["id"]})

    with owner_engine().connect() as c:
        row = c.execute(
            text("SELECT caption, kudos_count FROM posts WHERE id = :p"), {"p": post["id"]}
        ).one()
    assert (row.caption, row.kudos_count) == ("mine", 0)


# ----------------------------------------------------------------------------- media


def test_media_presign_is_503_until_configured_then_signs_and_attaches(
    client, created_users, monkeypatch
):
    from builder.settings import settings

    uid_a, h_a = _person(client, created_users, "alice")
    _, h_b = _person(client, created_users, "bob")
    post = _post(client, h_a, _session(client, h_a, uid_a), "public")
    presign = {"kind": "photo", "content_type": "image/jpeg", "bytes": 500_000}

    r = client.post(f"/v1/posts/{post['id']}/media:presign", json=presign, headers=h_a)
    assert r.status_code == 503 and "OBJECT_STORE_ENDPOINT" in r.json()["detail"]

    monkeypatch.setenv("OBJECT_STORE_ENDPOINT", "https://acct.r2.cloudflarestorage.com")
    monkeypatch.setenv("OBJECT_STORE_BUCKET", "builder-media")
    monkeypatch.setenv("OBJECT_STORE_KEY", "AKIAIOSFODNN7EXAMPLE")
    monkeypatch.setenv("OBJECT_STORE_SECRET", "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY")
    settings.cache_clear()

    r = client.post(f"/v1/posts/{post['id']}/media:presign", json=presign, headers=h_a)
    assert r.status_code == 200, r.text
    signed = r.json()
    assert signed["object_key"].startswith(f"posts/{post['id']}/") and signed[
        "object_key"
    ].endswith(".jpg")
    assert signed["upload_url"].startswith(
        f"https://acct.r2.cloudflarestorage.com/builder-media/{signed['object_key']}?"
    )
    assert "X-Amz-Signature=" in signed["upload_url"]
    assert signed["headers"] == {"Content-Type": "image/jpeg"} and signed["method"] == "PUT"

    # Not your post, not your presign; and the request is checked before storage is.
    assert (
        client.post(f"/v1/posts/{post['id']}/media:presign", json=presign, headers=h_b).status_code
        == 403
    )
    bad = {**presign, "content_type": "text/html"}
    assert (
        client.post(f"/v1/posts/{post['id']}/media:presign", json=bad, headers=h_a).status_code
        == 422
    )
    huge = {**presign, "bytes": 13 * 1024 * 1024}
    assert (
        client.post(f"/v1/posts/{post['id']}/media:presign", json=huge, headers=h_a).status_code
        == 413
    )

    # Attach: six photos fit, the seventh does not; a key from elsewhere is refused.
    def attach(body, headers=h_a):
        return client.post(f"/v1/posts/{post['id']}/media", json=body, headers=headers)

    keys = [f"posts/{post['id']}/{i:02d}.jpg" for i in range(7)]
    for key in keys[:6]:
        r = attach({"object_key": key, "width": 2048, "height": 1536})
        assert r.status_code == 201, r.text
    assert attach({"object_key": keys[6], "width": 10, "height": 10}).status_code == 409
    assert attach({"object_key": keys[0], "width": 10, "height": 10}).status_code == 409
    foreign = {"object_key": "posts/other/x.jpg", "width": 10, "height": 10}
    assert attach(foreign).status_code == 422
    assert attach({"object_key": f"posts/{post['id']}/nodims.jpg"}).status_code == 422
    assert attach({"object_key": keys[6], "width": 1, "height": 1}, headers=h_b).status_code == 403

    # One voice note, at most 90 s.
    long_note = {"object_key": f"posts/{post['id']}/a.m4a", "kind": "audio", "duration_ms": 90_001}
    assert attach(long_note).status_code == 422
    note = {**long_note, "duration_ms": 60_000}
    assert attach(note).status_code == 201
    second = {**note, "object_key": f"posts/{post['id']}/b.m4a"}
    assert attach(second).status_code == 409
    presign_audio = {"kind": "audio", "content_type": "audio/mp4", "bytes": 1_000_000}
    assert (
        client.post(
            f"/v1/posts/{post['id']}/media:presign", json=presign_audio, headers=h_a
        ).status_code
        == 409
    )

    # The item carries them, with no URL until a public base exists, then with one.
    item = client.get(f"/v1/posts/{post['id']}", headers=h_b).json()
    assert [p["object_key"] for p in item["photos"]] == keys[:6]
    assert item["photos"][0]["width"] == 2048 and item["photos"][0]["url"] is None
    assert item["audio"]["duration_ms"] == 60_000
    monkeypatch.setenv("OBJECT_STORE_PUBLIC_BASE", "https://media.example")
    settings.cache_clear()
    item = client.get(f"/v1/posts/{post['id']}", headers=h_b).json()
    assert item["photos"][0]["url"] == f"https://media.example/{keys[0]}"

    # Media dies with the post.
    assert client.delete(f"/v1/posts/{post['id']}", headers=h_a).status_code == 204
    with owner_engine().connect() as c:
        left = c.execute(
            text("SELECT count(*) FROM post_media WHERE post_id = :p"), {"p": post["id"]}
        ).scalar()
    assert left == 0
