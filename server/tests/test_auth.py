"""Sign-in, pairing and refresh — exercised AS builder_app, through the real code paths.

Every table these flows touch after the first step (`devices`, `identities`,
`push_tokens`) is RLS-protected with an owner policy, and every flow begins before the
owner is known. Run as the database owner, who bypasses RLS, all of this passes whether or
not the viewer is ever set. Run as `builder_app`, a viewer-less `INSERT INTO devices` is a
WITH CHECK violation and a viewer-less refresh is a 401 for every valid token. So the app
engine here is pointed at `builder_app`, exactly as `boot.py` demands in production, and
the routes are driven through FastAPI's test client rather than re-implemented.

Run with a local Postgres:
    BUILDER_TEST_DB=postgresql+psycopg://localhost/builder_test pytest tests/test_auth.py
"""

import os
import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, rsa
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

TEST_DB = os.environ.get("BUILDER_TEST_DB")
pytestmark = pytest.mark.skipif(not TEST_DB, reason="set BUILDER_TEST_DB to run")

GOOGLE_AUDIENCES = [
    "ios-client.apps.googleusercontent.com",
    "android-client.apps.googleusercontent.com",
]
MACHINE_A = "a" * 64
MACHINE_B = "b" * 64


def app_url() -> str:
    """The builder_app connection string, derived structurally from BUILDER_TEST_DB.

    Same helper as test_rls.py, for the same reason: CI's URL carries credentials, and a
    string replacement on it yields a username of `builder_app@postgres`.
    """
    u = make_url(TEST_DB)
    return str(
        u.set(
            username="builder_app",
            password=os.environ.get("BUILDER_TEST_APP_PASSWORD", u.password),
        ).render_as_string(hide_password=False)
    )


def owner_engine():
    return create_engine(TEST_DB, future=True)


def _ed25519_pem() -> str:
    key = ed25519.Ed25519PrivateKey.generate()
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


# ------------------------------------------------------------------------- fixtures


@pytest.fixture
def app_env(monkeypatch):
    """Point the API's engine at builder_app and give it a signing key, per test.

    `settings()` is an lru_cache and `db.engine()` is a module global, so both are reset
    on the way in and the way out — test_boot.py does the same, and leaving either one
    populated bleeds this test's configuration into the next file's.
    """
    import builder.auth as auth
    import builder.db as db_module
    from builder.settings import settings

    monkeypatch.setenv("APP_DATABASE_URL", app_url())
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("JWT_PRIVATE_KEY", _ed25519_pem())
    monkeypatch.setenv("GOOGLE_CLIENT_IDS", ", ".join(GOOGLE_AUDIENCES))
    # Non-empty so send_session_finished gets past its "APNs not configured" early return.
    monkeypatch.setenv("APNS_PRIVATE_KEY", "not-a-real-key")
    settings.cache_clear()
    db_module._engine = None
    auth._jwks_cache.clear()
    yield
    settings.cache_clear()
    db_module._engine = None
    auth._jwks_cache.clear()


@pytest.fixture
def client(app_env):
    from fastapi.testclient import TestClient

    from builder.main import app

    return TestClient(app)


@pytest.fixture
def created_users():
    """Ids to delete as the owner on the way out; cascades take devices and identities."""
    ids: list[str] = []
    yield ids
    if ids:
        with owner_engine().begin() as c:
            c.execute(text("DELETE FROM users WHERE id = ANY(CAST(:ids AS uuid[]))"), {"ids": ids})


@pytest.fixture(scope="module")
def google_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def google_jwks(google_key, monkeypatch):
    """Serve the test RSA key as Google's JWKS, in place of the network fetch."""
    import builder.auth as auth

    jwk = jwt.algorithms.RSAAlgorithm.to_jwk(google_key.public_key(), as_dict=True)
    jwk.update({"kid": "test-kid", "alg": "RS256", "use": "sig"})
    fetches = {"n": 0}

    def fake_fetch(url: str) -> dict:
        fetches["n"] += 1
        return {"keys": [jwk]}

    monkeypatch.setattr(auth, "_fetch_jwks_uncached", fake_fetch)
    auth._jwks_cache.clear()
    return fetches


def google_token(
    key, sub: str, *, aud=None, iss="https://accounts.google.com", exp_in=600, **extra
):
    now = datetime.now(UTC)
    claims = {
        "iss": iss,
        "sub": sub,
        "aud": aud or GOOGLE_AUDIENCES[0],
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=exp_in)).timestamp()),
        **extra,
    }
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": "test-kid"})


# ------------------------------------------------------------------ identity resolve


def test_identity_resolves_once_and_reuses(app_env, created_users):
    from builder.auth import resolve_or_create_user
    from builder.db import db_session

    sub = f"g-{uuid.uuid4()}"
    with db_session() as db:
        first = resolve_or_create_user(db, "google", sub, "a@example.com", True)
    created_users.append(first)
    with db_session() as db:
        second = resolve_or_create_user(db, "google", sub, None, None)

    assert first == second
    with owner_engine().connect() as c:
        n_users = c.execute(text("SELECT count(*) FROM users WHERE id = :u"), {"u": first}).scalar()
        ident = c.execute(
            text("SELECT user_id, email, email_verified FROM identities WHERE subject = :s"),
            {"s": sub},
        ).one()
        email = c.execute(text("SELECT email FROM users WHERE id = :u"), {"u": first}).scalar()
    assert n_users == 1
    assert str(ident.user_id) == first
    assert ident.email == "a@example.com" and ident.email_verified is True
    assert email == "a@example.com"


def test_apple_wrapper_backfills_legacy_column(app_env, created_users):
    from builder.auth import upsert_user_from_apple
    from builder.db import db_session

    sub = f"apple-{uuid.uuid4()}"
    with db_session() as db:
        uid = upsert_user_from_apple(db, sub, None)
    created_users.append(uid)
    with owner_engine().connect() as c:
        row = c.execute(text("SELECT apple_sub FROM users WHERE id = :u"), {"u": uid}).one()
    assert row.apple_sub == sub


def test_linking_attaches_identity_to_the_authenticated_user(app_env, created_users):
    from builder.auth import resolve_or_create_user
    from builder.db import db_session

    apple_sub, google_sub = f"apple-{uuid.uuid4()}", f"g-{uuid.uuid4()}"
    with db_session() as db:
        uid = resolve_or_create_user(db, "apple", apple_sub, None, None)
    created_users.append(uid)

    with db_session() as db:
        linked = resolve_or_create_user(
            db, "google", google_sub, "x@example.com", True, link_to=uid
        )
    assert linked == uid

    # Signing in with the Google identity alone now lands on the same user.
    with db_session() as db:
        again = resolve_or_create_user(db, "google", google_sub, None, None)
    assert again == uid

    with owner_engine().connect() as c:
        providers = (
            c.execute(
                text("SELECT provider FROM identities WHERE user_id = :u ORDER BY provider"),
                {"u": uid},
            )
            .scalars()
            .all()
        )
    assert providers == ["apple", "google"]


def test_linking_someone_elses_identity_is_409(app_env, created_users):
    from builder.auth import resolve_or_create_user
    from builder.db import db_session

    sub = f"g-{uuid.uuid4()}"
    with db_session() as db:
        owner = resolve_or_create_user(db, "google", sub, None, None)
    created_users.append(owner)
    with db_session() as db:
        other = resolve_or_create_user(db, "apple", f"apple-{uuid.uuid4()}", None, None)
    created_users.append(other)

    with pytest.raises(HTTPException) as exc, db_session() as db:
        resolve_or_create_user(db, "google", sub, None, None, link_to=other)
    assert exc.value.status_code == 409

    with owner_engine().connect() as c:
        still = c.execute(
            text("SELECT user_id FROM identities WHERE subject = :s"), {"s": sub}
        ).scalar()
    assert str(still) == owner, "a rejected link must not move the identity"


# ------------------------------------------------------------- google verification


def test_google_token_happy_path(app_env, google_jwks, google_key):
    from builder.auth import verify_google_id_token

    tok = google_token(google_key, "sub-1", email="p@example.com", email_verified=True)
    ident = verify_google_id_token(tok, GOOGLE_AUDIENCES)
    assert (ident.provider, ident.subject) == ("google", "sub-1")
    assert ident.email == "p@example.com" and ident.email_verified is True

    # Either documented issuer string, and any configured audience, is acceptable.
    tok = google_token(google_key, "sub-2", iss="accounts.google.com", aud=GOOGLE_AUDIENCES[1])
    assert verify_google_id_token(tok, GOOGLE_AUDIENCES).subject == "sub-2"


def test_google_jwks_is_cached(app_env, google_jwks, google_key):
    from builder.auth import verify_google_id_token

    for i in range(3):
        verify_google_id_token(google_token(google_key, f"s{i}"), GOOGLE_AUDIENCES)
    assert google_jwks["n"] == 1, "three verifications must cost one JWKS fetch"


def test_google_unknown_kid_refetches_once_then_401(app_env, google_jwks, google_key):
    from builder.auth import verify_google_id_token

    tok = jwt.encode(
        {"sub": "x", "aud": GOOGLE_AUDIENCES[0]}, google_key, "RS256", headers={"kid": "other"}
    )
    with pytest.raises(HTTPException) as exc:
        verify_google_id_token(tok, GOOGLE_AUDIENCES)
    assert exc.value.status_code == 401
    assert google_jwks["n"] == 2, "a kid miss is a rotation until proven otherwise: refetch once"


@pytest.mark.parametrize(
    "kwargs, reason",
    [
        ({"aud": "someone-elses-client"}, "audience"),
        ({"iss": "https://accounts.google.evil"}, "issuer"),
        ({"exp_in": -60}, "expired"),
    ],
)
def test_google_token_rejections(app_env, google_jwks, google_key, kwargs, reason):
    from builder.auth import verify_google_id_token

    with pytest.raises(HTTPException) as exc:
        verify_google_id_token(google_token(google_key, "sub", **kwargs), GOOGLE_AUDIENCES)
    assert exc.value.status_code == 401
    assert reason in exc.value.detail.lower()


def test_google_token_signed_by_another_key_is_401(app_env, google_jwks):
    from builder.auth import verify_google_id_token

    impostor = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(HTTPException) as exc:
        verify_google_id_token(google_token(impostor, "sub"), GOOGLE_AUDIENCES)
    assert exc.value.status_code == 401


def test_google_unconfigured_is_503_not_open(app_env, google_jwks, google_key):
    """An empty audience list must refuse, not accept everything. PyJWT skips the audience
    check entirely when `audience` is falsy, which would make a missing env var a bypass."""
    from builder.auth import verify_google_id_token

    with pytest.raises(HTTPException) as exc:
        verify_google_id_token(google_token(google_key, "sub"), [])
    assert exc.value.status_code == 503


# -------------------------------------------------------------------------- routes


def test_google_sign_in_route_creates_then_reuses_then_links(
    client, google_jwks, google_key, created_users
):
    sub = f"g-{uuid.uuid4()}"
    body = {
        "id_token": google_token(google_key, sub),
        "machine_id": MACHINE_A,
        "platform": "android",
    }

    r = client.post("/v1/auth/google", json=body)
    assert r.status_code == 200, r.text
    first = r.json()
    created_users.append(first["user_id"])
    assert first["linked"] is False

    r = client.post("/v1/auth/google", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["user_id"] == first["user_id"]

    # Authenticated as that user, a NEW Google identity links rather than creating.
    second_sub = f"g-{uuid.uuid4()}"
    r = client.post(
        "/v1/auth/google",
        json={"id_token": google_token(google_key, second_sub), "machine_id": MACHINE_B},
        headers={"authorization": f"Bearer {first['access_token']}"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["user_id"] == first["user_id"] and r.json()["linked"] is True

    # A third person cannot claim either identity by linking it to themselves.
    r = client.post(
        "/v1/auth/google",
        json={"id_token": google_token(google_key, f"g-{uuid.uuid4()}"), "machine_id": MACHINE_A},
    )
    created_users.append(r.json()["user_id"])
    r = client.post(
        "/v1/auth/google",
        json={"id_token": google_token(google_key, sub), "machine_id": MACHINE_A},
        headers={"authorization": f"Bearer {r.json()['access_token']}"},
    )
    assert r.status_code == 409

    # A stale bearer is a 401, not an anonymous sign-in that quietly forks the account.
    r = client.post("/v1/auth/google", json=body, headers={"authorization": "Bearer nope"})
    assert r.status_code == 401

    with owner_engine().connect() as c:
        devices = (
            c.execute(
                text("SELECT platform FROM devices WHERE user_id = :u ORDER BY platform"),
                {"u": first["user_id"]},
            )
            .scalars()
            .all()
        )
    assert devices == ["android", "ios"]


def _approved_grant(client, user_id: str) -> str:
    """Start a pairing as the agent would, then approve it as the owner would via the phone."""
    r = client.post(
        "/v1/auth/device/start",
        json={"machine_id": MACHINE_A, "label": "test-mac", "agent_version": "0.1"},
    )
    assert r.status_code == 200, r.text
    started = r.json()
    with owner_engine().begin() as c:
        c.execute(
            text(
                "UPDATE device_grants SET user_id = :u, approved_at = now() WHERE user_code = :uc"
            ),
            {"u": user_id, "uc": started["user_code"]},
        )
    return started["device_code"]


@pytest.fixture
def pairing_user(created_users):
    uid = str(uuid.uuid4())
    with owner_engine().begin() as c:
        c.execute(text("INSERT INTO users (id) VALUES (:i)"), {"i": uid})
    created_users.append(uid)
    return uid


def test_device_poll_inserts_the_device_as_builder_app(client, pairing_user):
    """The bug this file exists for: the route reads the grant with no viewer and then
    INSERTs into RLS-protected `devices`. Before the fix that was a 42501 → 500."""
    code = _approved_grant(client, pairing_user)

    r = client.post("/v1/auth/device/poll", json={"device_code": code})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "ok"

    with owner_engine().connect() as c:
        dev = c.execute(
            text("SELECT id, revoked_at FROM devices WHERE user_id = :u AND machine_id = :m"),
            {"u": pairing_user, "m": MACHINE_A},
        ).one()
        grants = c.execute(
            text("SELECT count(*) FROM device_grants WHERE machine_id = :m"), {"m": MACHINE_A}
        ).scalar()
    assert dev.revoked_at is None
    assert grants == 0, "the grant is single-use and must be gone after redemption"

    # Pending grants are a 200 with a status, not an error.
    r2 = client.post(
        "/v1/auth/device/start",
        json={"machine_id": MACHINE_B, "label": "m", "agent_version": "0.1"},
    )
    r = client.post("/v1/auth/device/poll", json={"device_code": r2.json()["device_code"]})
    assert r.json() == {"status": "authorization_pending"}
    with owner_engine().begin() as c:
        c.execute(text("DELETE FROM device_grants WHERE machine_id = :m"), {"m": MACHINE_B})


def test_refresh_rotates_and_reuse_actually_revokes(client, pairing_user):
    """Rotation, then reuse of the spent token. The 401 was always returned; the point of
    this test is that the revocation now SURVIVES it — it used to be rolled back with the
    transaction that raised."""
    code = _approved_grant(client, pairing_user)
    pair0 = client.post("/v1/auth/device/poll", json={"device_code": code}).json()

    r = client.post("/v1/auth/refresh", json={"refresh_token": pair0["refresh_token"]})
    assert r.status_code == 200, r.text
    pair1 = r.json()
    assert pair1["refresh_token"] != pair0["refresh_token"]

    # The new access token is genuinely usable.
    r = client.post(
        "/v1/auth/device/approve",
        json={"user_code": "XXXX-XXXX"},
        headers={"authorization": f"Bearer {pair1['access_token']}"},
    )
    assert r.status_code == 404, "authenticated, but no such pairing code"

    # Replay the spent token.
    r = client.post("/v1/auth/refresh", json={"refresh_token": pair0["refresh_token"]})
    assert r.status_code == 401
    assert "reuse" in r.json()["detail"]

    with owner_engine().connect() as c:
        rows = c.execute(
            text(
                """
                SELECT t.revoked_at FROM device_tokens t
                JOIN devices d ON d.id = t.device_id
                WHERE d.user_id = :u
                """
            ),
            {"u": pairing_user},
        ).all()
    assert len(rows) == 2
    assert all(r.revoked_at is not None for r in rows), "the whole chain must be revoked"

    # And the not-yet-used successor is dead too.
    r = client.post("/v1/auth/refresh", json={"refresh_token": pair1["refresh_token"]})
    assert r.status_code == 401

    r = client.post("/v1/auth/refresh", json={"refresh_token": "never-issued"})
    assert r.status_code == 401


def test_revoked_device_is_401_within_the_token_ttl(client, pairing_user):
    code = _approved_grant(client, pairing_user)
    pair = client.post("/v1/auth/device/poll", json={"device_code": code}).json()
    headers = {"authorization": f"Bearer {pair['access_token']}"}

    r = client.post("/v1/auth/device/approve", json={"user_code": "XXXX-XXXX"}, headers=headers)
    assert r.status_code == 404

    with owner_engine().begin() as c:
        c.execute(
            text("UPDATE devices SET revoked_at = now() WHERE user_id = :u"), {"u": pairing_user}
        )

    r = client.post("/v1/auth/device/approve", json={"user_code": "XXXX-XXXX"}, headers=headers)
    assert r.status_code == 401
    r = client.post("/v1/auth/refresh", json={"refresh_token": pair["refresh_token"]})
    assert r.status_code == 401, "a revoked device cannot refresh its way back in"


def test_session_finished_push_sees_the_users_tokens(client, pairing_user, monkeypatch):
    """`send_session_finished` runs outside a request and used to open a viewer-less
    session: zero rows, zero pushes, zero errors."""
    import builder.routes.push as push

    code = _approved_grant(client, pairing_user)
    pair = client.post("/v1/auth/device/poll", json={"device_code": code}).json()
    r = client.post(
        "/v1/push/register",
        json={"token": "abc123", "environment": "sandbox"},
        headers={"authorization": f"Bearer {pair['access_token']}"},
    )
    assert r.status_code == 200, r.text

    posted: list[str] = []

    class _Resp:
        status_code = 200

    class _Client:
        def __init__(self, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, **kw):
            posted.append(url)
            return _Resp()

    monkeypatch.setattr(push, "_apns_jwt", lambda: "apns-jwt")
    monkeypatch.setattr(push.httpx, "Client", _Client)

    sent = push.send_session_finished(pairing_user, "Session done", "1h 12m", str(uuid.uuid4()))
    assert sent == 1
    assert posted and posted[0].endswith("/3/device/abc123")
    assert "sandbox" in posted[0]
