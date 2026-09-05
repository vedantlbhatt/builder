"""Capture keys, AS builder_app, through the real routes.

The guarantee under test is a scope rule: a `bck_` key uploads sessions under its owner's
account and does nothing else. Every negative test here has a positive control in the same
test — the same key succeeding on `/v1/sync/known`, or a device token succeeding on the
route the key was refused from — because a 401 on its own could be a typo in the path or
a route that is not mounted (the "negative test that cannot reach the code" lesson).

Same harness as test_sync.py: the API engine is `builder_app`, so RLS is real, and the
phone that manages keys is a device minted through the pairing flow.

Run with a local Postgres:
    BUILDER_TEST_DB=postgresql+psycopg://localhost/builder_test pytest tests/test_capture_keys.py
"""

import uuid

import pytest
from sqlalchemy import text
from test_sync import (  # noqa: F401 - fixtures are picked up by name
    TEST_DB,
    _owner_rows,
    _pair,
    _payload,
    _upload,
    app_engine,
    app_env,
    client,
    created_users,
    owner_engine,
    paired,
)

pytestmark = pytest.mark.skipif(not TEST_DB, reason="set BUILDER_TEST_DB to run")

_SHARED_FIXTURES = (app_env, client, created_users, paired)


def _mint(client, headers, name="claude.ai/code") -> dict:
    r = client.post("/v1/capture-keys", json={"name": name}, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


def _key_headers(key: str) -> dict:
    return {"authorization": f"Bearer {key}"}


def _key_row(key_id: str):
    with owner_engine().connect() as c:
        return c.execute(
            text(
                """
                SELECT k.user_id, k.device_id, k.name, k.key_hash, k.key_prefix,
                       k.last_used_at, k.revoked_at,
                       d.platform, d.label, d.revoked_at AS device_revoked_at
                FROM capture_keys k JOIN devices d ON d.id = k.device_id
                WHERE k.id = :i
                """
            ),
            {"i": key_id},
        ).one()


# ------------------------------------------------------------------------- lifecycle


def test_create_list_revoke_round_trip(client, paired):
    """The plaintext appears in the POST response and nowhere else; the list carries the
    prefix and last_used_at; revoke removes it from the list and revokes its device."""
    import hashlib

    uid, headers = paired
    made = _mint(client, headers, name="  claude.ai/code  ")

    assert made["key"].startswith("bck_") and len(made["key"]) == 4 + 43
    assert made["key_prefix"] == made["key"][:8]
    assert made["name"] == "claude.ai/code", "names are stripped, like handles"

    row = _key_row(made["id"])
    assert str(row.user_id) == uid
    assert row.key_hash == hashlib.sha256(made["key"].encode()).hexdigest()
    assert made["key"] not in (row.key_hash, row.key_prefix, row.name)
    assert row.platform == "capture" and row.label == "claude.ai/code"
    assert row.last_used_at is None and row.revoked_at is None

    listed = client.get("/v1/capture-keys", headers=headers).json()["keys"]
    assert [k["id"] for k in listed] == [made["id"]]
    assert set(listed[0]) == {"id", "name", "key_prefix", "created_at", "last_used_at"}
    assert listed[0]["key_prefix"] == made["key_prefix"]
    assert listed[0]["last_used_at"] is None

    r = client.delete(f"/v1/capture-keys/{made['id']}", headers=headers)
    assert r.status_code == 204
    assert client.get("/v1/capture-keys", headers=headers).json()["keys"] == []
    row = _key_row(made["id"])
    assert row.revoked_at is not None
    assert row.device_revoked_at is not None, "the key's device row must be revoked with it"

    # Revoking again is a no-op on the timestamp, not an error; a made-up id is a 404.
    first = row.revoked_at
    assert client.delete(f"/v1/capture-keys/{made['id']}", headers=headers).status_code == 204
    assert _key_row(made["id"]).revoked_at == first
    assert client.delete(f"/v1/capture-keys/{uuid.uuid4()}", headers=headers).status_code == 404
    assert client.delete("/v1/capture-keys/not-a-uuid", headers=headers).status_code == 404


def test_name_is_required_and_bounded(client, paired):
    _, headers = paired
    assert client.post("/v1/capture-keys", json={"name": ""}, headers=headers).status_code == 422
    assert client.post("/v1/capture-keys", json={"name": "   "}, headers=headers).status_code == 422
    assert (
        client.post("/v1/capture-keys", json={"name": "x" * 65}, headers=headers).status_code == 422
    )
    assert client.post("/v1/capture-keys", json={}, headers=headers).status_code == 422


def test_at_most_ten_live_keys(client, paired):
    _, headers = paired
    ids = [_mint(client, headers, name=f"k{i}")["id"] for i in range(10)]
    r = client.post("/v1/capture-keys", json={"name": "eleventh"}, headers=headers)
    assert r.status_code == 409, r.text
    assert "10" in r.json()["detail"]

    # Revoked keys do not count against the cap.
    assert client.delete(f"/v1/capture-keys/{ids[0]}", headers=headers).status_code == 204
    assert (
        client.post("/v1/capture-keys", json={"name": "eleventh"}, headers=headers).status_code
        == 201
    )


# ---------------------------------------------------------------------------- uploads


def test_key_uploads_a_batch_under_its_owner(client, paired):
    uid, headers = paired
    made = _mint(client, headers)
    kh = _key_headers(made["key"])

    assert client.get("/v1/sync/known", headers=kh).json() == {"known": {}}

    p = _payload()
    out = _upload(client, kh, p)
    assert out["accepted"] == 1 and out["rejected"] == []

    rows = _owner_rows(uid)
    assert [r.client_session_id for r in rows] == [p["client_session_id"]]
    with owner_engine().connect() as c:
        device_id = c.execute(
            text("SELECT device_id FROM sessions WHERE id = :s"), {"s": rows[0].id}
        ).scalar()
        last_seen = c.execute(
            text("SELECT last_seen_at FROM devices WHERE id = :d"), {"d": device_id}
        ).scalar()
    assert str(device_id) == str(_key_row(made["id"]).device_id)
    assert last_seen is not None, "the sync route stamps the key's device like any other"

    # The phone sees it through the ordinary session routes.
    listed = client.get("/v1/sessions?notable_only=false", headers=headers).json()["sessions"]
    assert [s["client_session_id"] for s in listed] == [p["client_session_id"]]
    assert client.get("/v1/sync/known", headers=kh).json()["known"] == {
        p["client_session_id"]: p["content_hash"]
    }
    # The list reports the use.
    listed_keys = client.get("/v1/capture-keys", headers=headers).json()["keys"]
    assert listed_keys[0]["last_used_at"] is not None


def test_last_used_is_touched_at_most_once_a_minute(client, paired):
    uid, headers = paired
    made = _mint(client, headers)
    kh = _key_headers(made["key"])

    assert client.get("/v1/sync/known", headers=kh).status_code == 200
    first = _key_row(made["id"]).last_used_at
    assert first is not None
    assert client.get("/v1/sync/known", headers=kh).status_code == 200
    assert _key_row(made["id"]).last_used_at == first, "inside the window: no write"

    with owner_engine().begin() as c:
        c.execute(
            text(
                "UPDATE capture_keys SET last_used_at = now() - interval '2 minutes' WHERE id = :i"
            ),
            {"i": made["id"]},
        )
    stale = _key_row(made["id"]).last_used_at
    assert client.get("/v1/sync/known", headers=kh).status_code == 200
    assert _key_row(made["id"]).last_used_at > stale, "outside the window: touched"


def test_revoked_and_unknown_keys_are_the_same_401(client, paired):
    uid, headers = paired
    made = _mint(client, headers)
    kh = _key_headers(made["key"])
    assert client.get("/v1/sync/known", headers=kh).status_code == 200

    assert client.delete(f"/v1/capture-keys/{made['id']}", headers=headers).status_code == 204

    revoked = client.get("/v1/sync/known", headers=kh)
    assert revoked.status_code == 401
    batch = client.post("/v1/sync/sessions:batch", json={"sessions": [_payload()]}, headers=kh)
    assert batch.status_code == 401
    assert _owner_rows(uid) == [], "a revoked key must not upload"

    unknown = client.get("/v1/sync/known", headers=_key_headers("bck_" + "A" * 43))
    assert unknown.status_code == 401
    assert unknown.json()["detail"] == revoked.json()["detail"], (
        "revoked and unknown must be indistinguishable to whoever holds the string"
    )
    # A device revoked underneath a live key kills the key too.
    other = _mint(client, headers)
    with owner_engine().begin() as c:
        c.execute(
            text("UPDATE devices SET revoked_at = now() WHERE id = :d"),
            {"d": str(_key_row(other["id"]).device_id)},
        )
    assert client.get("/v1/sync/known", headers=_key_headers(other["key"])).status_code == 401


# ------------------------------------------------------------------------------ scope


@pytest.mark.parametrize(
    "method, path, body",
    [
        ("GET", "/v1/users/me", None),
        ("PATCH", "/v1/users/me", {"display_name": "leaked"}),
        ("GET", "/v1/sessions", None),
        ("GET", "/v1/sessions/live", None),
        ("GET", "/v1/profile", None),
        ("GET", "/v1/feed", None),
        ("POST", "/v1/posts", {"session_id": str(uuid.uuid4()), "visibility": "public"}),
        ("GET", "/v1/capture-keys", None),
        ("POST", "/v1/capture-keys", {"name": "successor"}),
        ("POST", "/v1/auth/device/approve", {"user_code": "XXXX-XXXX"}),
        ("POST", "/v1/push/register", {"token": "abc", "environment": "sandbox"}),
        ("POST", "/v1/account/delete", None),
    ],
)
def test_a_key_is_refused_everywhere_but_sync(client, paired, method, path, body):
    """The negative test with its controls: the key WORKS on /v1/sync/known in the same
    test (so it is a live key), and a device token gets past authentication on the same
    route (so the route exists and the 401 is the dependency's, not a 404 in disguise)."""
    uid, headers = paired
    made = _mint(client, headers)
    kh = _key_headers(made["key"])
    assert client.get("/v1/sync/known", headers=kh).status_code == 200

    refused = client.request(method, path, json=body, headers=kh)
    assert refused.status_code == 401, f"{method} {path}: {refused.text}"
    assert "capture key" in refused.json()["detail"]

    # Nothing changed hands: no key was minted, the account still exists.
    assert len(client.get("/v1/capture-keys", headers=headers).json()["keys"]) == 1
    with owner_engine().connect() as c:
        assert c.execute(text("SELECT count(*) FROM users WHERE id = :u"), {"u": uid}).scalar() == 1

    # The control runs last: for /v1/account/delete it deletes the account, as it should.
    control = client.request(method, path, json=body, headers=headers)
    assert control.status_code != 401, f"{method} {path} is not reachable with a device token"
    # Two routes answer 404 from INSIDE the handler — a post for a session that does not
    # exist, an approval for a code nobody started — which is past authentication. Any
    # other 404 is a route that is not mounted, and the refusal above proved nothing.
    past_auth_404 = {"/v1/posts", "/v1/auth/device/approve"}
    assert control.status_code != 404 or path in past_auth_404, (
        f"{method} {path} does not exist; the refusal above proved nothing"
    )


def test_a_key_cannot_read_back_what_it_uploaded(client, paired):
    """The one route a leaked key can reach only takes; the batch response reports counts,
    never rows, and `/known` returns hashes the holder already has."""
    _, headers = paired
    made = _mint(client, headers)
    kh = _key_headers(made["key"])
    p = _payload()
    assert _upload(client, kh, p)["accepted"] == 1
    known = client.get("/v1/sync/known", headers=kh).json()["known"]
    assert known == {p["client_session_id"]: p["content_hash"]}
    assert client.get("/v1/sessions", headers=kh).status_code == 401


# -------------------------------------------------------------------------------- RLS


def test_another_user_cannot_list_or_revoke_my_keys(client, created_users):
    uid_a, headers_a = _pair(client, created_users)
    uid_b, headers_b = _pair(client, created_users)
    made = _mint(client, headers_a)

    assert client.get("/v1/capture-keys", headers=headers_b).json()["keys"] == []
    assert client.delete(f"/v1/capture-keys/{made['id']}", headers=headers_b).status_code == 404
    assert _key_row(made["id"]).revoked_at is None
    assert client.get("/v1/sync/known", headers=_key_headers(made["key"])).status_code == 200

    # Past the route: as builder_app with B's viewer, the row is not there to be found.
    with app_engine().connect() as c:
        c.execute(text("SELECT set_config('app.viewer_id', :v, false)"), {"v": uid_b})
        as_b = c.execute(text("SELECT count(*) FROM capture_keys")).scalar()
        stolen = c.execute(
            text("UPDATE capture_keys SET revoked_at = now() WHERE id = :i RETURNING id"),
            {"i": made["id"]},
        ).first()
        c.execute(text("SELECT set_config('app.viewer_id', :v, false)"), {"v": uid_a})
        as_a = c.execute(
            text("SELECT count(*) FROM capture_keys WHERE id = :i"), {"i": made["id"]}
        ).scalar()
        c.execute(text("SELECT set_config('app.viewer_id', '', false)"))
        as_nobody = c.execute(text("SELECT count(*) FROM capture_keys")).scalar()
        c.rollback()
    assert as_a == 1, "the owner must see their own key, or the table is over-locked"
    assert as_b == 0 and stolen is None
    assert as_nobody == 0

    # The SECURITY DEFINER helper is the sanctioned exception, and only for the hash.
    with app_engine().connect() as c:
        c.execute(text("SELECT set_config('app.viewer_id', '', false)"))
        found = c.execute(
            text("SELECT user_id FROM capture_key_lookup(:h)"),
            {"h": _key_row(made["id"]).key_hash},
        ).scalar()
        miss = c.execute(
            text("SELECT user_id FROM capture_key_lookup(:h)"), {"h": "0" * 64}
        ).first()
    assert str(found) == uid_a and miss is None


def test_builder_app_cannot_rewrite_a_keys_hash_or_owner(client, paired):
    """Column-scoped UPDATE: the request role may touch last_used_at and revoked_at only.
    A bug that let it re-key or re-own a row would be an escalation with no error."""
    from sqlalchemy.exc import ProgrammingError

    uid, headers = paired
    made = _mint(client, headers)
    with app_engine().connect() as c:
        c.execute(text("SELECT set_config('app.viewer_id', :v, false)"), {"v": uid})
        for column, value in (("key_hash", "1" * 64), ("name", "renamed"), ("user_id", uid)):
            with pytest.raises(ProgrammingError) as exc:
                c.execute(
                    text(f"UPDATE capture_keys SET {column} = :v WHERE id = :i"),
                    {"v": value, "i": made["id"]},
                )
            assert "permission denied" in str(exc.value)
            c.rollback()
            c.execute(text("SELECT set_config('app.viewer_id', :v, false)"), {"v": uid})
        touched = c.execute(
            text("UPDATE capture_keys SET last_used_at = now() WHERE id = :i RETURNING id"),
            {"i": made["id"]},
        ).first()
        c.rollback()
    assert touched is not None
