from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text

from ..auth import (
    CurrentDevice,
    ProviderIdentity,
    current_device,
    issue_access_token,
    issue_refresh_token,
    new_user_code,
    optional_current_device,
    redeem_refresh_token,
    register_device,
    resolve_or_create_user,
    sha256,
    verify_apple_identity,
    verify_google_id_token,
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

        # The grant names the user; `register_device` raises the viewer to them before the
        # INSERT. `device_grants` has no RLS, which is why the read above worked without one.
        device_id = register_device(
            db,
            str(grant.user_id),
            grant.machine_id,
            grant.label,
            grant.platform,
            grant.agent_version,
        )

        access = issue_access_token(str(grant.user_id), device_id)
        refresh = issue_refresh_token(db, device_id)

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


# ------------------------------------------------------- Sign in with Apple / Google
#
# ACCOUNT LINKING. Both endpoints accept an OPTIONAL bearer token. Without one, an unknown
# identity creates a new user; with a valid one, it is linked to the caller's existing
# user, and an identity that already belongs to someone else is a 409. A bearer that is
# present but invalid is a 401 rather than "treat as anonymous" — silently creating a
# second account for a person whose token merely expired is the failure linking exists
# to prevent. Nothing is ever merged on email. See `resolve_or_create_user`.


class ProviderSignInRequest(BaseModel):
    machine_id: str
    label: str = "Phone"
    platform: str = "ios"
    agent_version: str = "ios"


class AppleSignInRequest(ProviderSignInRequest):
    identity_token: str
    label: str = "iPhone"


class GoogleSignInRequest(ProviderSignInRequest):
    id_token: str


def _sign_in(
    identity: ProviderIdentity, body: ProviderSignInRequest, linker: CurrentDevice | None
) -> dict:
    with db_session() as db:
        user_id = resolve_or_create_user(
            db,
            identity.provider,
            identity.subject,
            identity.email,
            identity.email_verified,
            link_to=str(linker.user_id) if linker else None,
        )
        device_id = register_device(
            db, user_id, body.machine_id, body.label, body.platform, body.agent_version
        )
        access = issue_access_token(user_id, device_id)
        refresh = issue_refresh_token(db, device_id)

    return {
        "access_token": access,
        "refresh_token": refresh,
        "expires_in": settings().access_token_ttl_seconds,
        "user_id": user_id,
        "linked": linker is not None,
    }


@router.post("/apple")
def apple_sign_in(
    body: AppleSignInRequest, linker: CurrentDevice | None = Depends(optional_current_device)
):
    audiences = [settings().apple_primary_bundle_id]
    if settings().apple_service_id:
        audiences.append(settings().apple_service_id)
    identity = verify_apple_identity(body.identity_token, audiences)
    return _sign_in(identity, body, linker)


@router.post("/google")
def google_sign_in(
    body: GoogleSignInRequest, linker: CurrentDevice | None = Depends(optional_current_device)
):
    """Google Sign-In. `platform` is "ios" or "android"; the token's audience says which
    OAuth client issued it, and all of them are in GOOGLE_CLIENT_IDS."""
    identity = verify_google_id_token(body.id_token, settings().google_client_id_list)
    return _sign_in(identity, body, linker)


class RefreshRequest(BaseModel):
    refresh_token: str


@router.post("/refresh")
def refresh(body: RefreshRequest):
    # Viewer-less on purpose: the token is all the caller has. `redeem_refresh_token`
    # resolves the owner through `device_owner` and sets the viewer itself.
    with db_session() as db:
        access, new_refresh, _ = redeem_refresh_token(db, body.refresh_token)
    return {
        "access_token": access,
        "refresh_token": new_refresh,
        "expires_in": settings().access_token_ttl_seconds,
    }
