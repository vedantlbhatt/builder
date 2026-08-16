from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text

from ..auth import (
    CurrentDevice,
    current_device,
    issue_access_token,
    issue_refresh_token,
    new_user_code,
    redeem_refresh_token,
    sha256,
    upsert_user_from_apple,
    verify_apple_identity_token,
)
from ..db import db_session
from ..settings import settings

router = APIRouter(prefix="/v1/auth", tags=["auth"])


# --------------------------------------------------------------- device grant (agent)
#
# RFC 8628, the same flow `gh auth login` uses. The Mac agent is open source, so any
# embedded client secret would be public by construction — the device flow exists exactly
# for public clients and asks the user to approve on a surface that already has a session.


class DeviceStartRequest(BaseModel):
    machine_id: str
    label: str
    platform: str = "macos"
    agent_version: str


class DeviceStartResponse(BaseModel):
    device_code: str
    user_code: str
    verification_uri: str
    expires_in: int
    interval: int


@router.post("/device/start", response_model=DeviceStartResponse)
def device_start(body: DeviceStartRequest):
    import secrets

    device_code = secrets.token_urlsafe(32)
    user_code = new_user_code()

    with db_session() as db:
        db.execute(
            text(
                """
                INSERT INTO device_grants
                  (device_code, user_code, machine_id, label, platform, agent_version, expires_at)
                VALUES (:dc, :uc, :mid, :label, :platform, :ver, now() + interval '15 minutes')
                """
            ),
            {
                "dc": sha256(device_code),
                "uc": user_code,
                "mid": body.machine_id,
                "label": body.label,
                "platform": body.platform,
                "ver": body.agent_version,
            },
        )

    return DeviceStartResponse(
        device_code=device_code,
        user_code=user_code,
        verification_uri=f"{settings().base_url}/pair",
        expires_in=900,
        interval=5,
    )


class DevicePollRequest(BaseModel):
    device_code: str


@router.post("/device/poll")
def device_poll(body: DevicePollRequest):
    """Standard device-grant polling.

    `authorization_pending` is not an error condition — it is the expected answer for as
    long as the person has not walked to their phone yet, so it returns 200 with a status
    rather than a 4xx the client has to special-case.
    """
    with db_session() as db:
        grant = db.execute(
            text(
                """
                SELECT device_code, machine_id, label, platform, agent_version,
                       user_id, approved_at, expires_at
                FROM device_grants WHERE device_code = :dc
                """
            ),
            {"dc": sha256(body.device_code)},
        ).first()

        if grant is None:
            raise HTTPException(400, "unknown device_code")
        if grant.expires_at < datetime.now(UTC):
            raise HTTPException(400, "expired_token")
        if grant.approved_at is None or grant.user_id is None:
            return {"status": "authorization_pending"}

        device = db.execute(
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
                "u": str(grant.user_id),
                "label": grant.label,
                "platform": grant.platform,
                "ver": grant.agent_version,
                "mid": grant.machine_id,
            },
        ).one()

        access = issue_access_token(str(grant.user_id), str(device.id))
        refresh = issue_refresh_token(db, str(device.id))

        # Single use. A grant that stayed valid would let anyone who saw the code over a
        # shoulder mint a second device later.
        db.execute(
            text("DELETE FROM device_grants WHERE device_code = :dc"),
            {"dc": sha256(body.device_code)},
        )

    return {
        "status": "ok",
        "access_token": access,
        "refresh_token": refresh,
        "expires_in": settings().access_token_ttl_seconds,
    }


class DeviceApproveRequest(BaseModel):
    user_code: str


@router.post("/device/approve")
def device_approve(body: DeviceApproveRequest, device: CurrentDevice = Depends(current_device)):
    """Approve a pairing code from an already-signed-in surface."""
    with db_session(viewer_id=str(device.user_id)) as db:
        updated = db.execute(
            text(
                """
                UPDATE device_grants SET user_id = :u, approved_at = now()
                WHERE user_code = :uc AND approved_at IS NULL AND expires_at > now()
                RETURNING label, platform
                """
            ),
            {"u": str(device.user_id), "uc": body.user_code.upper()},
        ).first()

    if updated is None:
        raise HTTPException(404, "no pending pairing with that code")
    return {"status": "approved", "label": updated.label, "platform": updated.platform}


# ------------------------------------------------------------------ Sign in with Apple


class AppleSignInRequest(BaseModel):
    identity_token: str
    machine_id: str
    label: str = "iPhone"
    platform: str = "ios"
    agent_version: str = "ios"


@router.post("/apple")
def apple_sign_in(body: AppleSignInRequest):
    audiences = [settings().apple_primary_bundle_id]
    if settings().apple_service_id:
        audiences.append(settings().apple_service_id)

    apple_sub = verify_apple_identity_token(body.identity_token, audiences)

    with db_session() as db:
        user_id = upsert_user_from_apple(db, apple_sub, None)
        device = db.execute(
            text(
                """
                INSERT INTO devices (user_id, label, platform, agent_version, machine_id)
                VALUES (:u, :label, :platform, :ver, :mid)
                ON CONFLICT (user_id, machine_id) DO UPDATE
                  SET agent_version = EXCLUDED.agent_version, revoked_at = NULL
                RETURNING id
                """
            ),
            {
                "u": user_id,
                "label": body.label,
                "platform": body.platform,
                "ver": body.agent_version,
                "mid": body.machine_id,
            },
        ).one()

        access = issue_access_token(user_id, str(device.id))
        refresh = issue_refresh_token(db, str(device.id))

    return {
        "access_token": access,
        "refresh_token": refresh,
        "expires_in": settings().access_token_ttl_seconds,
    }


class RefreshRequest(BaseModel):
    refresh_token: str


@router.post("/refresh")
def refresh(body: RefreshRequest):
    with db_session() as db:
        access, new_refresh, _ = redeem_refresh_token(db, body.refresh_token)
    return {
        "access_token": access,
        "refresh_token": new_refresh,
        "expires_in": settings().access_token_ttl_seconds,
    }


def _unused() -> None:  # pragma: no cover
    _ = timedelta
