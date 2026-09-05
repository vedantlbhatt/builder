"""`/users/me`, `/factions/mine` and `is_you`, AS builder_app, through the real routes.

Same harness as test_social.py. Two of these guarantees can fail open without an error:
`/factions/mine` listing a faction the viewer never joined (the join under RLS returning
someone else's membership), and a handle change slipping past the 30-day rule because the
comparison read the wrong column. Both are checked past the route as well as through it.

Run with a local Postgres:
    BUILDER_TEST_DB=postgresql+psycopg://localhost/builder_test pytest tests/test_users.py
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from test_social import _handle_of, _person, _post, _session
from test_sync import (  # noqa: F401 - fixtures are picked up by name
    TEST_DB,
    _pair,
    app_engine,
    app_env,
    client,
    created_users,
    owner_engine,
)

pytestmark = pytest.mark.skipif(not TEST_DB, reason="set BUILDER_TEST_DB to run")

_SHARED_FIXTURES = (app_env, client, created_users)


def _me(client, headers) -> dict:
    r = client.get("/v1/users/me", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def _patch(client, headers, **body):
    return client.patch("/v1/users/me", json=body, headers=headers)


def _handle_row(uid: str):
    with owner_engine().connect() as c:
        return c.execute(
            text("SELECT handle, handle_changed_at FROM users WHERE id = :u"), {"u": uid}
        ).one()


# --------------------------------------------------------------------------- /users/me


def test_me_round_trip(client, created_users):
    uid, h = _pair(client, created_users)
    tag = uid[:6]

    me = _me(client, h)
    assert me["id"] == uid
    assert me["handle"] is None and me["display_name"] is None
    assert me["profile_public"] is False and me["factions"] == []
    assert datetime.fromisoformat(me["created_at"]).tzinfo is not None

    # Handle is normalised, display name trimmed; the response is the me-shape.
    r = _patch(client, h, handle=f"  Alice_{tag} ", display_name="  Alice  ")
    assert r.status_code == 200, r.text
    assert r.json()["handle"] == f"alice_{tag}" and r.json()["display_name"] == "Alice"
    assert r.json()["id"] == uid and r.json()["factions"] == []
    assert _me(client, h) == r.json()

    # Visibility flips independently; omitted fields are left alone.
    r = _patch(client, h, profile_public=True)
    assert r.status_code == 200 and r.json()["profile_public"] is True
    assert r.json()["handle"] == f"alice_{tag}" and r.json()["display_name"] == "Alice"

    # Null clears the display name, an omitted one does not, whitespace-only clears too.
    assert _patch(client, h, display_name=None).json()["display_name"] is None
    assert _patch(client, h, display_name="Al").json()["display_name"] == "Al"
    assert _patch(client, h, profile_public=False).json()["display_name"] == "Al"
    assert _patch(client, h, display_name="   ").json()["display_name"] is None
    assert _patch(client, h, display_name="x" * 41).status_code == 422
    assert _patch(client, h, display_name="x" * 40).status_code == 200

    assert _patch(client, h).status_code == 422, "an empty patch names nothing to change"
    assert client.get("/v1/users/me").status_code == 401

    # The profile route resolves the new handle, and "me" is routed here, not there.
    r = client.get(f"/v1/users/alice_{tag}", headers=h)
    assert r.status_code == 200 and r.json()["profile"]["is_you"] is True


def test_handle_rules(client, created_users):
    uid_a, h_a = _pair(client, created_users)
    uid_b, h_b = _pair(client, created_users)
    tag = uid_a[:6]

    for bad in ("ab", "al-ice", "a" * 25, "alice.b", "al ice", ""):
        r = _patch(client, h_a, handle=bad)
        assert r.status_code == 422, (bad, r.text)
        assert "3-24" in r.json()["detail"]
    for reserved in ("me", "ME", "admin", "Builder", "api", "feed", "settings", "pair", "u"):
        r = _patch(client, h_a, handle=reserved)
        # `u` also fails the length rule; everything else is caught by the reserved list.
        assert r.status_code == 422, (reserved, r.text)
    assert _handle_row(uid_a).handle is None, "nothing above may have been written"

    # First claim: allowed, and does NOT start the 30-day clock.
    assert _patch(client, h_a, handle=f"Alice_{tag}").status_code == 200
    row = _handle_row(uid_a)
    assert (row.handle, row.handle_changed_at) == (f"alice_{tag}", None)

    # Taken, in any case: the citext UNIQUE decides, and the answer is 409.
    r = _patch(client, h_b, handle=f"ALICE_{tag}")
    assert r.status_code == 409 and "taken" in r.json()["detail"], r.text
    assert _handle_row(uid_b).handle is None

    # Re-asserting your own handle in another case is not a change.
    r = _patch(client, h_a, handle=f"ALICE_{tag}")
    assert r.status_code == 200 and r.json()["handle"] == f"alice_{tag}"
    assert _handle_row(uid_a).handle_changed_at is None

    # First CHANGE: allowed, stamps handle_changed_at.
    r = _patch(client, h_a, handle=f"alicia_{tag}")
    assert r.status_code == 200 and r.json()["handle"] == f"alicia_{tag}", r.text
    changed = _handle_row(uid_a).handle_changed_at
    assert changed is not None and datetime.now(UTC) - changed < timedelta(minutes=1)
    # ...and the old handle is free again.
    assert _patch(client, h_b, handle=f"alice_{tag}").status_code == 200

    # Second change inside 30 days: 409 — the account's state refuses the transition — with
    # a detail that says when it opens. The stamp is untouched and the handle unchanged.
    r = _patch(client, h_a, handle=f"alison_{tag}", display_name="Alison")
    assert r.status_code == 409, r.text
    assert "30 days" in r.json()["detail"] and "next change allowed at" in r.json()["detail"]
    row = _handle_row(uid_a)
    assert (row.handle, row.handle_changed_at) == (f"alicia_{tag}", changed)
    assert _me(client, h_a)["display_name"] is None, "a refused patch writes nothing"
    # Other fields still change while the handle is locked.
    assert _patch(client, h_a, display_name="Alicia").json()["display_name"] == "Alicia"

    # 31 days later the change goes through, and the clock restarts.
    with owner_engine().begin() as c:
        c.execute(
            text("UPDATE users SET handle_changed_at = now() - interval '31 days' WHERE id = :u"),
            {"u": uid_a},
        )
    r = _patch(client, h_a, handle=f"alison_{tag}")
    assert r.status_code == 200 and r.json()["handle"] == f"alison_{tag}", r.text
    assert _handle_row(uid_a).handle_changed_at > changed


# ----------------------------------------------------------------------- /factions/mine


def test_factions_mine_lists_created_and_joined_and_excludes_others(client, created_users):
    uid_a, h_a = _person(client, created_users, "alice")
    uid_b, h_b = _person(client, created_users, "bob")
    uid_c, h_c = _person(client, created_users, "cara")
    tag = uid_a[:6]

    assert client.get("/v1/factions/mine", headers=h_a).json() == {"factions": []}

    owls = client.post(
        "/v1/factions", json={"name": "Night Owls", "slug": f"owls-{tag}"}, headers=h_a
    ).json()
    larks = client.post(
        "/v1/factions", json={"name": "Larks", "slug": f"larks-{tag}", "open": True}, headers=h_a
    ).json()
    assert (
        client.post("/v1/factions:join", json={"code": owls["join_code"]}, headers=h_b).status_code
        == 200
    )
    caras = client.post("/v1/factions", json={"name": "Cara's", "slug": f"cara-{tag}"}, headers=h_c)
    assert caras.status_code == 201

    def mine(headers):
        r = client.get("/v1/factions/mine", headers=headers)
        assert r.status_code == 200, r.text
        return r.json()["factions"]

    alice = mine(h_a)
    assert [(f["slug"], f["role"], f["member_count"], f["open"]) for f in alice] == [
        (owls["slug"], "admin", 2, False),
        (larks["slug"], "admin", 1, True),
    ]
    assert all(f["share_hours"] is True for f in alice)
    assert alice[0]["name"] == "Night Owls"
    assert datetime.fromisoformat(alice[0]["joined_at"]) <= datetime.fromisoformat(
        alice[1]["joined_at"]
    )
    assert "join_code" not in alice[0], "the invite code has its own route"

    bob = mine(h_b)
    assert [(f["slug"], f["role"], f["member_count"]) for f in bob] == [(owls["slug"], "member", 2)]
    # Cara: only her own — not the open faction she could see, nor the closed one.
    assert [(f["slug"], f["role"]) for f in mine(h_c)] == [(f"cara-{tag}", "admin")]

    # The same list rides along on /users/me, and follows membership changes.
    assert [f["slug"] for f in _me(client, h_b)["factions"]] == [owls["slug"]]
    r = client.patch(
        f"/v1/factions/{owls['slug']}/members/me", json={"share_hours": False}, headers=h_b
    )
    assert r.status_code == 200
    assert mine(h_b)[0]["share_hours"] is False
    assert (
        client.post("/v1/factions:join", json={"slug": larks["slug"]}, headers=h_c).status_code
        == 200
    )
    assert [f["slug"] for f in mine(h_c)] == [f"cara-{tag}", larks["slug"]]
    assert [f["member_count"] for f in mine(h_a)] == [2, 2]

    # Past the route: as builder_app with Cara as viewer, the join the route runs yields
    # no row for a membership that is not hers, so the list cannot fail open.
    with app_engine().connect() as c:
        c.execute(text("SELECT set_config('app.viewer_id', :v, false)"), {"v": uid_c})
        seen = c.execute(
            text(
                """
                SELECT f.slug FROM faction_members fm JOIN factions f ON f.id = fm.faction_id
                WHERE fm.user_id = :other
                """
            ),
            {"other": uid_a},
        ).all()
    assert [r.slug for r in seen] == [larks["slug"]], (
        "a shared faction's roster is visible to its members; a closed one's is not"
    )


# ------------------------------------------------------------------------------ is_you


def test_is_you_on_feed_items_and_comments(client, created_users):
    uid_a, h_a = _person(client, created_users, "alice")
    uid_b, h_b = _person(client, created_users, "bob")
    alice, bob = _handle_of(uid_a), _handle_of(uid_b)
    post = _post(client, h_a, _session(client, h_a, uid_a), "public")
    assert post["author"] == {"handle": alice, "display_name": None, "is_you": True}

    assert client.post(f"/v1/follows/{alice}", headers=h_b).json()["state"] == "accepted"
    mine = client.post(f"/v1/posts/{post['id']}/comments", json={"body": "hi"}, headers=h_b)
    assert mine.status_code == 201 and mine.json()["author"]["is_you"] is True
    theirs = client.post(f"/v1/posts/{post['id']}/comments", json={"body": "yo"}, headers=h_a)
    assert theirs.json()["author"]["is_you"] is True

    def authors(headers, path):
        r = client.get(path, headers=headers)
        assert r.status_code == 200, r.text
        body = r.json()
        items = body.get("items") or body.get("comments") or body.get("posts")
        return [(i["author"]["handle"], i["author"]["is_you"]) for i in items]

    assert authors(h_a, "/v1/feed") == [(alice, True)]
    assert authors(h_b, "/v1/feed") == [(alice, False)]
    assert authors(h_a, f"/v1/users/{alice}") == [(alice, True)]
    assert authors(h_b, f"/v1/users/{alice}") == [(alice, False)]
    assert client.get(f"/v1/posts/{post['id']}", headers=h_b).json()["author"]["is_you"] is False

    comments = f"/v1/posts/{post['id']}/comments"
    assert authors(h_a, comments) == [(bob, False), (alice, True)]
    assert authors(h_b, comments) == [(bob, True), (alice, False)]
