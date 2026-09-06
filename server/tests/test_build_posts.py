"""Build posts: a post about a PROJECT, across as many sittings as the work took.

A session post is about one sitting, which is the wrong unit for a feed of people sharing
what they are building: a feature takes four sittings across a week and none of them on
its own is the thing anybody wants to read. These tests cover the unit change and, more
importantly, the two places the old shape was baked in as an INNER JOIN on `sessions`.
"""

import copy
import uuid

import pytest
from sqlalchemy import text
from test_social import _handle, _handle_of, _session
from test_sync import (  # noqa: F401 - fixtures are picked up by name
    TEST_DB,
    _pair,
    _payload,
    _upload,
    app_env,
    client,
    created_users,
    owner_engine,
    paired,
)

pytestmark = pytest.mark.skipif(not TEST_DB, reason="set BUILDER_TEST_DB to run")

_SHARED_FIXTURES = (app_env, client, created_users, paired)

SHIPPED = {
    "what": "Turns the logs your AI coding tools write into a feed of what you built",
    "why": "For builders who want their work shareable without adding any tracking",
    "stage": "working",
    "changes": [
        {"text": "Aider sessions show up alongside Claude Code", "evidence": "migration 0015"}
    ],
    "hard_part": "The digest step kept failing its own schema, four attempts before one landed.",
    "stack": ["expo", "fastapi"],
    "demo": "The feed with a build post in it",
    "next": None,
    "shipped_version": 1,
    "model": "sonnet",
    "generated_at": "2026-09-06T04:24:59Z",
    "dashes_rewritten": 0,
    "unsupported_claims_dropped": 0,
}


def shipped(**overrides) -> dict:
    d = copy.deepcopy(SHIPPED)
    d.update(overrides)
    return d


def _repo_of(session_id: str) -> str:
    with owner_engine().connect() as c:
        return str(
            c.execute(
                text("SELECT repo_id FROM sessions WHERE id = CAST(:s AS uuid)"), {"s": session_id}
            ).scalar()
        )


def _seed(client, headers, uid) -> tuple[str, str, str]:
    """(session id, repo id, repo hash) for a session in its own fresh repository."""
    rhash = uuid.uuid4().hex * 2
    sid = _session(client, headers, uid, repo_hash=rhash)
    return sid, _repo_of(sid), rhash


def _exclude(client, headers, rhash: str):
    return client.post(
        "/v1/repos/visibility", json={"repo_hash": rhash, "visibility": "excluded"}, headers=headers
    )


def _post(client, headers, rid, **kw):
    body = {"repo_id": rid, "shipped": shipped(), "visibility": "public"}
    body.update(kw)
    return client.post("/v1/posts/build", json=body, headers=headers)


def test_a_build_post_round_trips_through_the_feed(client, paired):
    uid, headers = paired
    _, rid, rhash = _seed(client, headers, uid)
    r = _post(client, headers, rid)
    assert r.status_code == 201, r.text
    item = r.json()
    assert item["shipped"]["what"].startswith("Turns the logs")
    assert item["session"] is None, "a build post is about a project, not a sitting"

    feed = client.get("/v1/feed", headers=headers).json()["items"]
    assert [i["id"] for i in feed] == [item["id"]]
    assert feed[0]["shipped"]["hard_part"].startswith("The digest step")


def test_a_build_post_is_visible_to_its_own_author(client, paired):
    """`can_view_post` INNER JOINed `sessions`, so a post with none matched nothing and
    was invisible to everyone including the person who wrote it. Fail-closed, and a bug."""
    uid, headers = paired
    _, rid, rhash = _seed(client, headers, uid)
    pid = _post(client, headers, rid).json()["id"]
    assert client.get(f"/v1/posts/{pid}", headers=headers).status_code == 200


def test_a_session_post_and_a_build_post_share_one_feed(client, paired):
    uid, headers = paired
    sid, rid, rhash = _seed(client, headers, uid)
    assert (
        client.post(
            "/v1/posts", json={"session_id": sid, "visibility": "public"}, headers=headers
        ).status_code
        == 201
    )
    assert _post(client, headers, rid).status_code == 201

    items = client.get("/v1/feed", headers=headers).json()["items"]
    assert len(items) == 2
    kinds = {("build" if i["shipped"] else "session") for i in items}
    assert kinds == {"build", "session"}
    session_post = next(i for i in items if i["shipped"] is None)
    assert session_post["session"] is not None, "the session post kept its session"


def test_several_build_posts_about_one_project_are_allowed(client, paired):
    """One post per SESSION is still the rule. A project is posted about repeatedly, which
    is the entire point of a feed about what somebody is building."""
    uid, headers = paired
    _, rid, rhash = _seed(client, headers, uid)
    assert _post(client, headers, rid).status_code == 201
    assert _post(client, headers, rid, caption="week two").status_code == 201
    assert len(client.get("/v1/feed", headers=headers).json()["items"]) == 2


def test_you_cannot_post_about_a_repository_you_have_never_worked_in(client, created_users, paired):
    """`repos` is SHARED: two people on one open source project resolve to one row. Without
    the ownership check anybody could post about any project that has ever been seen."""
    vuid, victim = paired
    _, rid, rhash = _seed(client, victim, vuid)

    _, attacker = _pair(client, created_users)
    r = _post(client, attacker, rid)
    assert r.status_code == 404, r.text


def test_an_excluded_repository_cannot_be_posted_about(client, paired):
    """Refused, and the 404 is the honest code: excluding a repository deletes its
    sessions, so by the time the exclusion check would run there is nothing of the
    caller's in that repository at all."""
    uid, headers = paired
    _, rid, rhash = _seed(client, headers, uid)
    assert _exclude(client, headers, rhash).status_code == 200
    assert _post(client, headers, rid).status_code == 404


def test_the_exclusion_check_does_not_depend_on_the_sweep_having_run(client, paired):
    """The row is written directly, leaving the sessions in place, which is the state a
    half-applied sweep or a future code path would leave behind. The route must still
    refuse, and say why."""
    uid, headers = paired
    _, rid, _rhash = _seed(client, headers, uid)
    with owner_engine().begin() as c:
        c.execute(
            text(
                "INSERT INTO repo_visibility (user_id, repo_id, visibility) "
                "VALUES (CAST(:u AS uuid), CAST(:r AS uuid), 'excluded') "
                "ON CONFLICT (user_id, repo_id) DO UPDATE SET visibility = 'excluded'"
            ),
            {"u": uid, "r": rid},
        )
    r = _post(client, headers, rid)
    assert r.status_code == 403
    assert "excluded" in r.json()["detail"]


def test_excluding_a_repository_afterwards_takes_its_build_posts_down(client, paired):
    """The 0004 failure in a new place: a rule that reads as though it enforces exclusion
    and enforces nothing. A build post reaches its repository only through `repo_id`."""
    uid, headers = paired
    _, rid, rhash = _seed(client, headers, uid)
    pid = _post(client, headers, rid).json()["id"]
    assert client.get(f"/v1/posts/{pid}", headers=headers).status_code == 200

    _exclude(client, headers, rhash)
    assert client.get(f"/v1/posts/{pid}", headers=headers).status_code == 404
    assert client.get("/v1/feed", headers=headers).json()["items"] == []
    # Deleted, not merely filtered. This route's promise is that an excluded repository
    # has NOTHING on the server, and a build post has no session to cascade from.
    with owner_engine().connect() as c:
        assert (
            c.execute(
                text("SELECT count(*) FROM posts WHERE id = CAST(:p AS uuid)"), {"p": pid}
            ).scalar()
            == 0
        )


def test_a_private_build_post_is_invisible_to_a_follower(client, created_users, paired):
    auid, author = paired
    _, rid, rhash = _seed(client, author, auid)
    _post(client, author, rid, visibility="private")

    _, other = _pair(client, created_users)
    assert client.get("/v1/feed", headers=other).json()["items"] == []


def test_a_followers_only_build_post_reaches_an_accepted_follower(client, created_users, paired):
    uid, author = paired
    _, rid, rhash = _seed(client, author, uid)
    pid = _post(client, author, rid, visibility="followers").json()["id"]

    _handle(uid, f"author-{uid[:6]}")
    fuid, follower = _pair(client, created_users)
    _handle(fuid, f"fan-{fuid[:6]}")
    assert (
        client.post(f"/v1/follows/{_handle_of(uid)}", headers=follower).status_code == 200
    )
    assert client.get(f"/v1/posts/{pid}", headers=follower).status_code == 200


def test_a_document_that_breaks_the_spec_is_refused(client, paired):
    uid, headers = paired
    _, rid, rhash = _seed(client, headers, uid)
    assert _post(client, headers, rid, shipped=shipped(stage="vibes")).status_code == 422
    assert _post(client, headers, rid, shipped=shipped(what="x" * 101)).status_code == 422
    assert _post(client, headers, rid, shipped=shipped(stack=["a"] * 7)).status_code == 422
    assert _post(client, headers, rid, shipped=shipped(repo_name="builder")).status_code == 422


def test_the_route_needs_a_device(client):
    r = client.post("/v1/posts/build", json={"repo_id": str(uuid.uuid4()), "shipped": shipped()})
    assert r.status_code == 401
