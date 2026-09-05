import hashlib
import secrets
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
from fastapi import Depends, HTTPException, Request
from sqlalchemy import text

from .db import db_session, set_viewer
from .settings import settings

# Human-typeable pairing codes. No vowels, no 0/O/1/I/L — the code is read off a laptop
# screen and typed into a phone, and every ambiguous glyph is a support request.
_CODE_ALPHABET = "BCDFGHJKMNPQRSTVWXYZ23456789"

APPLE_JWKS_URL = "https://appleid.apple.com/auth/keys"
GOOGLE_JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"


@dataclass
class CurrentDevice:
    user_id: uuid.UUID
    device_id: uuid.UUID


@dataclass
class ProviderIdentity:
    """What a verified identity token proves: who, according to whom, and how to reach them.

    `subject` is the only identifier. `email` is a contact detail that Apple relays per
    app and Google can report unverified; nothing downstream is allowed to match on it.
    """

    provider: str
    subject: str
    email: str | None = None
    email_verified: bool | None = None


def sha256(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def new_user_code(length: int = 8) -> str:
    raw = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(length))
    return f"{raw[:4]}-{raw[4:]}"


# --------------------------------------------------------------------------- tokens


def issue_access_token(user_id: str, device_id: str) -> str:
    """Short-lived Ed25519 JWT.

    Fifteen minutes, and two signing keys in play at once. With a single key, rotating it
    401s every token issued before the swap — including ones mid-flight — so the rotation
    itself becomes an outage. The verifier accepts both; the signer uses the current one.
    """
    now = datetime.now(UTC)
    payload = {
        "sub": user_id,
        "did": device_id,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=settings().access_token_ttl_seconds)).timestamp()),
    }
    return jwt.encode(payload, settings().jwt_private_key, algorithm="EdDSA")


def verify_access_token(token: str) -> dict:
    keys = [settings().jwt_private_key, settings().jwt_private_key_next]
    last_error: Exception | None = None
    for key in keys:
        if not key:
            continue
        try:
            return jwt.decode(token, key, algorithms=["EdDSA"])
        except Exception as e:  # noqa: BLE001 - try the next key
            last_error = e
    raise HTTPException(401, f"invalid token: {last_error}")


def issue_refresh_token(db, device_id: str, prev_id: str | None = None) -> str:
    """Opaque, 256-bit, stored only as a hash, and rotated on every use."""
    raw = secrets.token_urlsafe(32)
    db.execute(
        text(
            """
            INSERT INTO device_tokens (device_id, refresh_hash, prev_id, expires_at)
            VALUES (:d, :h, :p, now() + interval '90 days')
            """
        ),
        {"d": device_id, "h": sha256(raw), "p": prev_id},
    )
    return raw


def redeem_refresh_token(db, raw: str) -> tuple[str, str, str]:
    """Exchange a refresh token for a new pair, detecting reuse.

    A spent token being presented again means it leaked. The safe response is to revoke
    the entire chain rather than issue another one: the attacker and the user both have
    a token, and there is no way to tell which is which.

    Two things about the shape of this function are load-bearing:

    The caller opens the transaction with NO viewer — a refresh token is all it has. So
    the lookup reads `device_tokens` alone (no RLS) and resolves the owner through the
    SECURITY DEFINER `device_owner`, then sets the viewer and only then touches `devices`.
    The earlier version JOINed `devices` from the viewer-less transaction, matched zero
    rows under the owner policy, and answered "unknown refresh token" to every valid one.

    The revocation is COMMITTED before the 401 is raised. `db_session` rolls back on any
    exception, so an UPDATE followed by `raise HTTPException` inside the same transaction
    was undone on the way out: reuse was detected, reported, and never actually revoked.
    """
    row = db.execute(
        text(
            """
            SELECT id, device_id, used_at, revoked_at, expires_at
            FROM device_tokens WHERE refresh_hash = :h
            """
        ),
        {"h": sha256(raw)},
    ).first()

    if row is None:
        raise HTTPException(401, "unknown refresh token")

    if row.used_at is not None or row.revoked_at is not None:
        db.execute(
            text("UPDATE device_tokens SET revoked_at = now() WHERE device_id = :d"),
            {"d": str(row.device_id)},
        )
        db.commit()
        raise HTTPException(401, "refresh token reuse detected; all tokens for this device revoked")

    if row.expires_at < datetime.now(UTC):
        raise HTTPException(401, "refresh token expired")

    user_id = db.execute(text("SELECT device_owner(:d)"), {"d": str(row.device_id)}).scalar()
    if user_id is None:
        raise HTTPException(401, "unknown refresh token")
    set_viewer(db, str(user_id))

    revoked = db.execute(
        text("SELECT revoked_at FROM devices WHERE id = :d"), {"d": str(row.device_id)}
    ).first()
    if revoked is None or revoked.revoked_at is not None:
        raise HTTPException(401, "device revoked")

    db.execute(text("UPDATE device_tokens SET used_at = now() WHERE id = :i"), {"i": str(row.id)})
    new_refresh = issue_refresh_token(db, str(row.device_id), prev_id=str(row.id))
    access = issue_access_token(str(user_id), str(row.device_id))
    return access, new_refresh, str(user_id)


# --------------------------------------------------------------------------- devices


def register_device(
    db, user_id: str, machine_id: str, label: str, platform: str, agent_version: str
) -> str:
    """Create or refresh the device row for (user, machine), un-revoking it if needed.

    Every sign-in path lands here — pairing, Apple, Google — so there is exactly one place
    that writes `devices`, and the viewer must already be set to `user_id` when it runs.
    `devices` is RLS-protected with an owner policy: with the viewer unset this INSERT is
    a WITH CHECK violation, which surfaced as a bare 500 from `/v1/auth/device/poll`.
    """
    set_viewer(db, user_id)
    row = db.execute(
        text(
            """
            INSERT INTO devices (user_id, label, platform, agent_version, machine_id)
            VALUES (:u, :label, :platform, :ver, :mid)
            ON CONFLICT (user_id, machine_id) DO UPDATE
              SET agent_version = EXCLUDED.agent_version,
                  label = EXCLUDED.label,
                  revoked_at = NULL
            RETURNING id
            """
        ),
        {
            "u": user_id,
            "label": label,
            "platform": platform,
            "ver": agent_version,
            "mid": machine_id,
        },
    ).one()
    return str(row.id)


# --------------------------------------------------------------------------- deps


def current_device(request: Request) -> CurrentDevice:
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        raise HTTPException(401, "missing bearer token")
    return _device_from_bearer(header[7:])


def optional_current_device(request: Request) -> CurrentDevice | None:
    """The sign-in routes' half-open door: no header means "create", a header means "link".

    A header that is present but INVALID is a 401, not a silent fallthrough to "create".
    The client only sends one when it believes it is signed in; treating a stale token as
    anonymity would split that person's history into a fresh account with no error, which
    is the one outcome a linking policy exists to prevent.
    """
    header = request.headers.get("authorization", "")
    if not header:
        return None
    if not header.lower().startswith("bearer "):
        raise HTTPException(401, "malformed authorization header")
    return _device_from_bearer(header[7:])


def _device_from_bearer(token: str) -> CurrentDevice:
    """Verify the signature, then verify the device still exists and is not revoked.

    The second half costs one indexed SELECT per authenticated request. Without it a
    revoked device keeps working for up to `access_token_ttl_seconds` after revocation —
    and "revoke" in the settings screen would be a fifteen-minute suggestion. Tokens are
    short-lived precisely so this check can stay a query rather than a claim.
    """
    claims = verify_access_token(token)
    try:
        device = CurrentDevice(user_id=uuid.UUID(claims["sub"]), device_id=uuid.UUID(claims["did"]))
    except (KeyError, ValueError) as e:
        raise HTTPException(401, "malformed token claims") from e

    with db_session(viewer_id=str(device.user_id)) as db:
        row = db.execute(
            text("SELECT revoked_at FROM devices WHERE id = :d"), {"d": str(device.device_id)}
        ).first()
    # No row covers both "deleted" and "belongs to someone else": under the owner policy
    # a device the viewer does not own is indistinguishable from one that does not exist.
    if row is None or row.revoked_at is not None:
        raise HTTPException(401, "device revoked")
    return device


def current_user_id(device: CurrentDevice = Depends(current_device)) -> str:
    return str(device.user_id)


# --------------------------------------------------------------------- identity tokens

# JWKS by URL, with the time it stops being trusted. Apple and Google both publish keys
# that change rarely and rate-limit fetches that do not; a fetch per sign-in is a fetch
# per sign-in, and a slow JWKS endpoint becomes a slow (or failed) login for every user.
_JWKS_TTL_SECONDS = 10 * 60
_jwks_cache: dict[str, tuple[dict, float]] = {}


def _fetch_jwks_uncached(url: str) -> dict:
    import httpx

    return httpx.get(url, timeout=10).json()


def _jwks(url: str, *, force: bool = False) -> dict:
    cached = _jwks_cache.get(url)
    if cached and not force and cached[1] > time.monotonic():
        return cached[0]
    jwks = _fetch_jwks_uncached(url)
    _jwks_cache[url] = (jwks, time.monotonic() + _JWKS_TTL_SECONDS)
    return jwks


def _signing_key(jwks_url: str, kid: str):
    """Find the key for `kid`, refetching once on a miss.

    A miss is what a key rotation looks like from inside the cache window: the provider
    began signing with a key we have not seen. One forced refetch turns that into a
    verified login instead of ten minutes of 401s; a second miss is a genuinely unknown key.
    """
    for force in (False, True):
        keys = _jwks(jwks_url, force=force).get("keys", [])
        key_data = next((k for k in keys if k.get("kid") == kid), None)
        if key_data is not None:
            return jwt.algorithms.RSAAlgorithm.from_jwk(key_data)
    return None


def _verify_rs256_identity_token(
    token: str, *, jwks_url: str, audiences: list[str], issuers: list[str], provider: str
) -> dict:
    if not audiences:
        raise HTTPException(503, f"{provider} sign-in is not configured on this server")
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as e:
        raise HTTPException(401, f"malformed {provider} token") from e

    key = _signing_key(jwks_url, header.get("kid", ""))
    if key is None:
        raise HTTPException(401, f"unknown {provider} signing key")

    try:
        claims = jwt.decode(token, key, algorithms=["RS256"], audience=audiences)
    except jwt.PyJWTError as e:
        # PyJWT's message names the failing check (expired, audience, signature). That is
        # useful to whoever is wiring up a client and harmless to an attacker.
        raise HTTPException(401, f"invalid {provider} token: {e}") from e

    # Checked by hand rather than via `issuer=`: Google is documented to use either of two
    # issuer strings, and PyJWT's `issuer` parameter wants exactly one.
    if claims.get("iss") not in issuers:
        raise HTTPException(401, f"invalid {provider} token: wrong issuer")
    if not claims.get("sub"):
        raise HTTPException(401, f"invalid {provider} token: no subject")
    return claims


def _email_verified(value) -> bool | None:
    # Apple sends `email_verified` as the STRING "true"/"false"; Google sends a boolean.
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    return str(value).lower() == "true"


def verify_apple_identity(identity_token: str, expected_audiences: list[str]) -> ProviderIdentity:
    """Validate a Sign in with Apple identity token.

    The `sub` is stable across the iOS app, the Mac app and the web only if their App IDs
    are GROUPED under the primary in the Sign in with Apple pane. Ungrouped, Apple scopes
    it per App ID: the same human signs in on two surfaces and gets two accounts with
    split history, and the `(provider, subject)` primary key turns the second one into a
    second user rather than an error, which is worse because nobody notices.
    """
    claims = _verify_rs256_identity_token(
        identity_token,
        jwks_url=APPLE_JWKS_URL,
        audiences=expected_audiences,
        issuers=["https://appleid.apple.com"],
        provider="apple",
    )
    return ProviderIdentity(
        provider="apple",
        subject=claims["sub"],
        email=claims.get("email"),
        email_verified=_email_verified(claims.get("email_verified")),
    )


def verify_apple_identity_token(identity_token: str, expected_audiences: list[str]) -> str:
    """Kept for callers that only ever wanted the `sub`."""
    return verify_apple_identity(identity_token, expected_audiences).subject


def verify_google_id_token(id_token: str, expected_audiences: list[str]) -> ProviderIdentity:
    """Validate a Google Sign-In ID token.

    The audience is the OAuth client id that requested the token, and iOS, Android and web
    each have their own, so `expected_audiences` is the whole list from GOOGLE_CLIENT_IDS.
    Google's `sub` is stable across all of them for one Google account.
    """
    claims = _verify_rs256_identity_token(
        id_token,
        jwks_url=GOOGLE_JWKS_URL,
        audiences=expected_audiences,
        issuers=["accounts.google.com", "https://accounts.google.com"],
        provider="google",
    )
    return ProviderIdentity(
        provider="google",
        subject=claims["sub"],
        email=claims.get("email"),
        email_verified=_email_verified(claims.get("email_verified")),
    )


# ------------------------------------------------------------------------------ users


def resolve_or_create_user(
    db,
    provider: str,
    subject: str,
    email: str | None,
    email_verified: bool | None,
    link_to: str | None = None,
) -> str:
    """Turn a verified (provider, subject) into a user id, and leave the viewer set to it.

    ACCOUNT LINKING POLICY. A sign-in with an identity nobody has seen before creates a NEW
    user — unless the caller is already authenticated, in which case the identity is LINKED
    to the user they already are. An identity that is already someone else's is a 409 when
    linking and a plain sign-in as that someone else otherwise. Email is never consulted:
    Apple relays are per-app, Google addresses can be unverified, and two providers
    reporting the same string is not evidence of the same person. Merging on it would
    hand one user another user's history on the strength of a contact detail.

    Runs in a transaction that starts viewer-less, so the lookup goes through the SECURITY
    DEFINER `identity_user` rather than reading `identities` under its owner policy (which
    would see nothing and mint a new user on every sign-in). Once the id is known the
    viewer is set here, so the caller can go straight on to `register_device`.

    The insert is `ON CONFLICT DO NOTHING`: two first sign-ins racing on the same subject
    both miss the lookup, and the loser must adopt the winner's user rather than 500 and
    leave an orphan `users` row behind.
    """
    owner = db.execute(text("SELECT identity_user(:p, :s)"), {"p": provider, "s": subject}).scalar()

    if owner is not None:
        if link_to is not None and str(owner) != link_to:
            raise HTTPException(409, "this identity is already linked to a different account")
        user_id = str(owner)
        set_viewer(db, user_id)
        db.execute(
            text(
                """
                UPDATE identities
                   SET email = COALESCE(:email, email),
                       email_verified = COALESCE(:verified, email_verified)
                 WHERE provider = :p AND subject = :s
                """
            ),
            {"email": email, "verified": email_verified, "p": provider, "s": subject},
        )
        db.execute(
            text("UPDATE users SET email = COALESCE(email, :email) WHERE id = :u"),
            {"email": email, "u": user_id},
        )
        return user_id

    created: str | None = None
    if link_to is not None:
        user_id = link_to
        if provider == "apple":
            db.execute(
                text("UPDATE users SET apple_sub = COALESCE(apple_sub, :s) WHERE id = :u"),
                {"s": subject, "u": user_id},
            )
    else:
        created = str(
            db.execute(
                text(
                    """
                    INSERT INTO users (apple_sub, email_relay, email)
                    VALUES (:apple_sub, :relay, :email)
                    RETURNING id
                    """
                ),
                {
                    "apple_sub": subject if provider == "apple" else None,
                    "relay": email if provider == "apple" else None,
                    "email": email,
                },
            ).scalar()
        )
        user_id = created

    set_viewer(db, user_id)
    inserted = db.execute(
        text(
            """
            INSERT INTO identities (user_id, provider, subject, email, email_verified)
            VALUES (:u, :p, :s, :email, :verified)
            ON CONFLICT (provider, subject) DO NOTHING
            RETURNING user_id
            """
        ),
        {"u": user_id, "p": provider, "s": subject, "email": email, "verified": email_verified},
    ).scalar()
    if inserted is not None:
        return user_id

    # Lost the race. Discard the user we minted (users has no RLS) and adopt the winner's.
    if created is not None:
        db.execute(text("DELETE FROM users WHERE id = :u"), {"u": created})
    owner = db.execute(text("SELECT identity_user(:p, :s)"), {"p": provider, "s": subject}).scalar()
    if owner is None:
        raise HTTPException(500, "identity vanished mid-sign-in")
    if link_to is not None and str(owner) != link_to:
        raise HTTPException(409, "this identity is already linked to a different account")
    set_viewer(db, str(owner))
    return str(owner)


def upsert_user_from_apple(db, apple_sub: str, email_relay: str | None) -> str:
    """Thin wrapper over `resolve_or_create_user` for callers that predate providers."""
    return resolve_or_create_user(db, "apple", apple_sub, email_relay, None)


__all__ = [
    "APPLE_JWKS_URL",
    "GOOGLE_JWKS_URL",
    "CurrentDevice",
    "ProviderIdentity",
    "current_device",
    "current_user_id",
    "issue_access_token",
    "issue_refresh_token",
    "new_user_code",
    "optional_current_device",
    "redeem_refresh_token",
    "register_device",
    "resolve_or_create_user",
    "sha256",
    "upsert_user_from_apple",
    "verify_access_token",
    "verify_apple_identity",
    "verify_apple_identity_token",
    "verify_google_id_token",
]
