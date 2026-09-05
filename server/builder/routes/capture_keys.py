"""Capture keys: the non-rotating credential a cloud container uploads with.

Managed from a signed-in surface (the phone) with an ordinary device token — never with a
capture key, so a leaked key cannot mint its successor. The plaintext is returned by the
POST exactly once and stored only as a hash; the list shows the prefix and when the key
last uploaded, which is how a person notices a key they thought was idle is not.

Revoke sets `revoked_at` and revokes the device row minted with the key. The row stays,
so the 401 the container gets from then on is a lookup that finds a revoked key rather
than a lookup that finds nothing — the same 401 to the caller (`_device_from_capture_key`
does not distinguish them), a different story in the database.
"""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import text

from ..auth import CurrentDevice, create_capture_key, current_device
from ..db import db_session

router = APIRouter(prefix="/v1/capture-keys", tags=["capture-keys"])

#: Matches the CHECK on `capture_keys.name`. The name is what the phone lists ("claude.ai/
#: code", "work laptop") and what the device row is labelled; nothing parses it.
NAME_MAX = 64


class CaptureKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=NAME_MAX)


class CaptureKeyCreated(BaseModel):
    id: str
    name: str
    #: The plaintext. This response is the only time it exists outside the caller's hands.
    key: str
    key_prefix: str
    created_at: datetime


class CaptureKey(BaseModel):
    id: str
    name: str
    key_prefix: str
    created_at: datetime
    last_used_at: datetime | None


class CaptureKeyList(BaseModel):
    keys: list[CaptureKey]


@router.post("", response_model=CaptureKeyCreated, status_code=201)
def create_key(body: CaptureKeyCreate, device: CurrentDevice = Depends(current_device)):
    name = body.name.strip()
    if not name:
        raise HTTPException(422, "name must not be blank")
    with db_session(viewer_id=str(device.user_id)) as db:
        made = create_capture_key(db, str(device.user_id), name)
    return CaptureKeyCreated(**made)


@router.get("", response_model=CaptureKeyList)
def list_keys(device: CurrentDevice = Depends(current_device)):
    """Live keys only, oldest first. A revoked key is gone from the phone's point of view;
    it remains in the table so its hash keeps answering 401 rather than "unknown"."""
    with db_session(viewer_id=str(device.user_id)) as db:
        rows = db.execute(
            text(
                """
                SELECT id, name, key_prefix, created_at, last_used_at
                FROM capture_keys
                WHERE user_id = :u AND revoked_at IS NULL
                ORDER BY created_at, id
                """
            ),
            {"u": str(device.user_id)},
        ).all()
    return CaptureKeyList(
        keys=[
            CaptureKey(
                id=str(r.id),
                name=r.name,
                key_prefix=r.key_prefix,
                created_at=r.created_at,
                last_used_at=r.last_used_at,
            )
            for r in rows
        ]
    )


@router.delete("/{key_id}", status_code=204)
def revoke_key(key_id: str, device: CurrentDevice = Depends(current_device)):
    """Revoke. 404 for a key the viewer does not own — under the owner policy that row is
    indistinguishable from one that does not exist, which is the point. Idempotent: a key
    revoked twice keeps its first `revoked_at`."""
    with db_session(viewer_id=str(device.user_id)) as db:
        row = db.execute(
            text(
                """
                UPDATE capture_keys
                   SET revoked_at = COALESCE(revoked_at, now())
                 WHERE id = CAST(:id AS uuid) AND user_id = :u
                RETURNING device_id
                """
            ),
            {"id": _uuid_or_404(key_id), "u": str(device.user_id)},
        ).first()
        if row is None:
            raise HTTPException(404, "no such capture key")
        db.execute(
            text(
                "UPDATE devices SET revoked_at = COALESCE(revoked_at, now()) "
                "WHERE id = :d AND user_id = :u"
            ),
            {"d": str(row.device_id), "u": str(device.user_id)},
        )
    return Response(status_code=204)


def _uuid_or_404(value: str) -> str:
    try:
        return str(uuid.UUID(value))
    except ValueError as e:
        raise HTTPException(404, "no such capture key") from e
