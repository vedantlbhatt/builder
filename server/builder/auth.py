import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
from fastapi import Depends, HTTPException, Request
from sqlalchemy import text

from .db import db_session
from .settings import settings

# Human-typeable pairing codes. No vowels, no 0/O/1/I/L — the code is read off a laptop
# screen and typed into a phone, and every ambiguous glyph is a support request.
_CODE_ALPHABET = "BCDFGHJKMNPQRSTVWXYZ23456789"


@dataclass
class CurrentDevice:
    user_id: uuid.UUID
    device_id: uuid.UUID


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
    """
    row = db.execute(
        text(
            """
            SELECT t.id, t.device_id, t.used_at, t.revoked_at, t.expires_at, d.user_id
            FROM device_tokens t JOIN devices d ON d.id = t.device_id
            WHERE t.refresh_hash = :h
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
        raise HTTPException(401, "refresh token reuse detected; all tokens for this device revoked")

    if row.expires_at < datetime.now(UTC):
        raise HTTPException(401, "refresh token expired")

    db.execute(text("UPDATE device_tokens SET used_at = now() WHERE id = :i"), {"i": str(row.id)})
    new_refresh = issue_refresh_token(db, str(row.device_id), prev_id=str(row.id))
    access = issue_access_token(str(row.user_id), str(row.device_id))
    return access, new_refresh, str(row.user_id)


# --------------------------------------------------------------------------- deps


def current_device(request: Request) -> CurrentDevice:
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        raise HTTPException(401, "missing bearer token")
    claims = verify_access_token(header[7:])
    try:
        return CurrentDevice(user_id=uuid.UUID(claims["sub"]), device_id=uuid.UUID(claims["did"]))
    except (KeyError, ValueError) as e:
        raise HTTPException(401, "malformed token claims") from e


def current_user_id(device: CurrentDevice = Depends(current_device)) -> str:
    return str(device.user_id)


# --------------------------------------------------------------------------- SIWA


def verify_apple_identity_token(identity_token: str, expected_audiences: list[str]) -> str:
    """Validate a Sign in with Apple identity token and return its stable `sub`.

    The `sub` is stable across the iOS app, the Mac app and the web only if their App IDs
    are GROUPED under the primary in the Sign in with Apple pane. Ungrouped, Apple scopes
    it per App ID: the same human signs in on two surfaces and gets two accounts with
    split history, and `users.apple_sub UNIQUE` turns the second one into an error that
    looks like a bug rather than a configuration mistake.
    """
    import httpx

    jwks = httpx.get("https://appleid.apple.com/auth/keys", timeout=10).json()
    header = jwt.get_unverified_header(identity_token)
    key_data = next((k for k in jwks["keys"] if k["kid"] == header["kid"]), None)
    if key_data is None:
        raise HTTPException(401, "unknown Apple signing key")

    key = jwt.algorithms.RSAAlgorithm.from_jwk(key_data)
    claims = jwt.decode(
        identity_token,
        key,
        algorithms=["RS256"],
        audience=expected_audiences,
        issuer="https://appleid.apple.com",
    )
    return claims["sub"]


def upsert_user_from_apple(db, apple_sub: str, email_relay: str | None) -> str:
    row = db.execute(
        text(
            """
            INSERT INTO users (apple_sub, email_relay)
            VALUES (:sub, :email)
            ON CONFLICT (apple_sub) DO UPDATE
              SET email_relay = COALESCE(EXCLUDED.email_relay, users.email_relay)
            RETURNING id
            """
        ),
        {"sub": apple_sub, "email": email_relay},
    ).one()
    return str(row.id)


__all__ = [
    "CurrentDevice",
    "current_device",
    "current_user_id",
    "issue_access_token",
    "issue_refresh_token",
    "new_user_code",
    "redeem_refresh_token",
    "sha256",
    "upsert_user_from_apple",
    "verify_access_token",
    "verify_apple_identity_token",
]


def _unused() -> None:  # pragma: no cover
    _ = db_session
