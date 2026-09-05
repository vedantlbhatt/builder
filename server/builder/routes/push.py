import contextlib
import json
import time
from datetime import UTC, datetime, timedelta

import httpx
import jwt
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text

from ..auth import CurrentDevice, current_device
from ..db import db_session
from ..settings import settings

router = APIRouter(prefix="/v1/push", tags=["push"])

_token_cache: dict[str, tuple[str, float]] = {}


class RegisterRequest(BaseModel):
    token: str
    environment: str = "sandbox"


@router.post("/register")
def register(body: RegisterRequest, device: CurrentDevice = Depends(current_device)):
    """Register an APNs device token.

    The environment is stored alongside it because a sandbox token and a production token
    are indistinguishable by inspection, and sending to the wrong host returns
    `BadDeviceToken`. Without this, push breaks during exactly the TestFlight phase, when
    the build is signed for production but installed like a development one.
    """
    env = "production" if body.environment == "production" else "sandbox"
    with db_session(viewer_id=str(device.user_id)) as db:
        db.execute(
            text(
                """
                INSERT INTO push_tokens (user_id, token, environment)
                VALUES (:u, :t, :e)
                ON CONFLICT (user_id, token) DO UPDATE
                  SET environment = EXCLUDED.environment, last_used_at = now()
                """
            ),
            {"u": str(device.user_id), "t": body.token, "e": env},
        )
    return {"status": "registered", "environment": env}


def _apns_jwt() -> str:
    """Token-based APNs auth. Cached: Apple rejects tokens refreshed more than once
    every 20 minutes, and requires refresh at least once an hour."""
    cached = _token_cache.get("apns")
    if cached and cached[1] > time.time():
        return cached[0]

    token = jwt.encode(
        {"iss": settings().apns_team_id, "iat": int(time.time())},
        settings().apns_private_key,
        algorithm="ES256",
        headers={"kid": settings().apns_key_id},
    )
    _token_cache["apns"] = (token, time.time() + 30 * 60)
    return token


def send_session_finished(user_id: str, title: str, body: str, session_id: str) -> int:
    """Push a session-complete alert to every device the user has registered.

    A `BadDeviceToken` is retried once against the opposite host before the token is
    dropped: the usual cause is an environment mismatch rather than a dead install, and
    deleting the token on the first failure would silently disable push for a TestFlight
    user with no way to recover short of reinstalling.
    """
    if not settings().apns_private_key:
        return 0

    # `push_tokens` is RLS-protected with an owner policy. This runs from a job, not a
    # request, but it still knows exactly whose tokens it wants — and viewer-less it got
    # zero rows, every time, with no error: the push silently never went out.
    with db_session(viewer_id=user_id) as db:
        rows = db.execute(
            text("SELECT id, token, environment FROM push_tokens WHERE user_id = :u"),
            {"u": user_id},
        ).all()

    if not rows:
        return 0

    payload = {
        "aps": {
            "alert": {"title": title, "body": body},
            "sound": "default",
            "thread-id": "session",
            "interruption-level": "active",
        },
        "session": session_id,
    }

    sent = 0
    auth = _apns_jwt()
    for row in rows:
        for env in [row.environment, _other(row.environment)]:
            host = (
                "https://api.sandbox.push.apple.com"
                if env == "sandbox"
                else "https://api.push.apple.com"
            )
            try:
                with httpx.Client(http2=True, timeout=10) as client:
                    resp = client.post(
                        f"{host}/3/device/{row.token}",
                        content=json.dumps(payload),
                        headers={
                            "authorization": f"bearer {auth}",
                            "apns-topic": settings().apns_topic,
                            "apns-push-type": "alert",
                            # Coalesces repeats for the same session rather than stacking
                            # a second banner if a retry lands.
                            "apns-collapse-id": session_id[:63],
                        },
                    )
            except Exception:
                break

            if resp.status_code == 200:
                sent += 1
                if env != row.environment:
                    with db_session(viewer_id=user_id) as db:
                        db.execute(
                            text("UPDATE push_tokens SET environment = :e WHERE id = :i"),
                            {"e": env, "i": str(row.id)},
                        )
                break

            reason = ""
            with contextlib.suppress(Exception):
                reason = resp.json().get("reason", "")

            if reason in {"Unregistered", "BadDeviceToken"} and env == _other(row.environment):
                # Failed against both hosts: the install is genuinely gone.
                with db_session(viewer_id=user_id) as db:
                    db.execute(text("DELETE FROM push_tokens WHERE id = :i"), {"i": str(row.id)})
            elif reason not in {"BadDeviceToken", "Unregistered"}:
                break

    return sent


def _other(env: str) -> str:
    return "production" if env == "sandbox" else "sandbox"


def _unused() -> None:  # pragma: no cover
    _ = (datetime, timedelta, UTC)
