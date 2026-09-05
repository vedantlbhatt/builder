"""The viewer's own account: `/users/me` and `/factions/mine`.

Three endpoints the phone used to reconstruct from other responses — its own handle from a
feed item's author, its factions from the boards it could open. Both inferences were wrong
at the edges (no posts yet, a faction with no board this week), so the truth is served
directly.

`users` has no RLS: profiles resolve by handle for everyone. Every query here therefore
filters by the viewer's own id explicitly; the policy is not going to do it. Factions and
memberships ARE RLS-protected, and the join in `_my_factions` works under the viewer's own
id because both `members_visible` and `factions_visible` (0007) go through the SECURITY
DEFINER `is_faction_member` — no helper of our own is needed, and test_users.py checks a
non-member's factions stay out of the list by reading past the route with the viewer set.

This router is included BEFORE social's in main.py. FastAPI matches routes in registration
order, and `/users/{handle}` would otherwise swallow the literal "me" — reserving the
handle stops a person from being named that, not the router from routing it.
"""

import re
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from ..auth import CurrentDevice, current_device
from ..db import db_session

router = APIRouter(prefix="/v1", tags=["users"])

#: 3-24 of a-z, 0-9 and underscore, already lowercased. Short enough to fit a board row on
#: a phone next to the hours; the column is citext, so case never distinguishes two people.
_HANDLE_RE = re.compile(r"^[a-z0-9_]{3,24}$")

#: Path segments and product words a handle must not shadow. `me` is the literal this
#: router serves under /users/; `u` and `pair` are the web's short paths.
RESERVED_HANDLES = frozenset({"me", "admin", "builder", "api", "feed", "settings", "pair", "u"})

#: A handle is how other people find and mention you; renaming it every day breaks their
#: bookmarks and follow requests. Once a month is a correction, not a disguise. The FIRST
#: claim of a handle does not start the clock — a typo in the first pick can still be fixed.
HANDLE_CHANGE_INTERVAL = timedelta(days=30)

MAX_DISPLAY_NAME = 40


class MePatch(BaseModel):
    handle: str | None = None
    display_name: str | None = None
    profile_public: bool | None = None


def _my_factions(db, user_id: str) -> list[dict]:
    """The viewer's memberships, oldest first, each with the roster size.

    `member_count` is a subquery rather than a GROUP BY over the join so that a faction
    row is never dropped for having a count the viewer cannot see — it can always see the
    whole roster of a faction it belongs to, but the shape should not depend on that.
    """
    rows = db.execute(
        text(
            """
            SELECT f.slug, f.name, f.open, fm.role, fm.share_hours, fm.joined_at,
                   (SELECT count(*) FROM faction_members m WHERE m.faction_id = f.id)
                     AS member_count
            FROM faction_members fm
            JOIN factions f ON f.id = fm.faction_id
            WHERE fm.user_id = CAST(:u AS uuid)
            ORDER BY fm.joined_at, f.slug
            """
        ),
        {"u": user_id},
    ).all()
    return [
        {
            "slug": r.slug,
            "name": r.name,
            "role": r.role,
            "share_hours": r.share_hours,
            "open": r.open,
            "member_count": int(r.member_count),
            "joined_at": r.joined_at.isoformat(),
        }
        for r in rows
    ]


def _me(db, user_id: str) -> dict:
    u = db.execute(
        text(
            """
            SELECT id, handle, display_name, profile_public, created_at
            FROM users WHERE id = CAST(:u AS uuid) AND deleted_at IS NULL
            """
        ),
        {"u": user_id},
    ).first()
    if u is None:
        raise HTTPException(404, "no such user")
    return {
        "id": str(u.id),
        "handle": u.handle,
        "display_name": u.display_name,
        "profile_public": u.profile_public,
        "created_at": u.created_at.isoformat(),
        "factions": _my_factions(db, user_id),
    }


@router.get("/users/me")
def me(device: CurrentDevice = Depends(current_device)):
    """The viewer's own row, plus their factions, in one request."""
    with db_session(viewer_id=str(device.user_id)) as db:
        return _me(db, str(device.user_id))


@router.patch("/users/me")
def patch_me(body: MePatch, device: CurrentDevice = Depends(current_device)):
    """Handle, display name, profile visibility. Returns the same shape as GET.

    The handle is normalised (trimmed, lowercased) before it is checked, so `Alice` and
    `alice` are one request for one handle, and the citext UNIQUE constraint — not a
    SELECT-before-UPDATE — decides whether it is taken: a pre-check races, the constraint
    does not. A conflict is 409.

    A second change inside `HANDLE_CHANGE_INTERVAL` is also 409, not 429. It is not a
    rate limit on the client; it is the account's current state refusing the transition,
    exactly as a live session refuses to be shared. The detail says when it opens again.
    """
    uid = str(device.user_id)
    sets: list[str] = []
    params: dict = {"u": uid}

    handle: str | None = None
    if body.handle is not None:
        handle = body.handle.strip().lower()
        if not _HANDLE_RE.match(handle):
            raise HTTPException(422, "handle must be 3-24 characters of a-z, 0-9 and '_'")
        if handle in RESERVED_HANDLES:
            raise HTTPException(422, f"the handle {handle!r} is reserved")

    # `model_fields_set` tells a display_name that was omitted from one set to null: the
    # second clears it, the first leaves it alone. Whitespace-only clears it too.
    if "display_name" in body.model_fields_set:
        name = (body.display_name or "").strip() or None
        if name is not None and len(name) > MAX_DISPLAY_NAME:
            raise HTTPException(422, f"display_name is at most {MAX_DISPLAY_NAME} characters")
        sets.append("display_name = :name")
        params["name"] = name
    if body.profile_public is not None:
        sets.append("profile_public = :public")
        params["public"] = body.profile_public

    with db_session(viewer_id=uid) as db:
        current = db.execute(
            text(
                """
                SELECT handle, handle_changed_at FROM users
                WHERE id = CAST(:u AS uuid) AND deleted_at IS NULL
                FOR UPDATE
                """
            ),
            {"u": uid},
        ).first()
        if current is None:
            raise HTTPException(404, "no such user")

        # Same handle in a different case is not a change: citext already holds it.
        changing = handle is not None and (
            current.handle is None or current.handle.lower() != handle
        )
        if changing:
            if current.handle is not None:
                since = current.handle_changed_at
                if since is not None and since + HANDLE_CHANGE_INTERVAL > datetime.now(UTC):
                    opens = (since + HANDLE_CHANGE_INTERVAL).isoformat()
                    raise HTTPException(
                        409,
                        "handle can be changed once every "
                        f"{HANDLE_CHANGE_INTERVAL.days} days; next change allowed at {opens}",
                    )
                sets.append("handle_changed_at = now()")
            sets.append("handle = :handle")
            params["handle"] = handle

        if not sets:
            # A request that names nothing is a mistake; one that re-asserts the current
            # handle is not, and answers with the current row.
            if not body.model_fields_set:
                raise HTTPException(422, "nothing to change")
            return _me(db, uid)

        assignments = ", ".join(sets)
        try:
            db.execute(text(f"UPDATE users SET {assignments} WHERE id = CAST(:u AS uuid)"), params)
        except IntegrityError as e:
            raise HTTPException(409, f"the handle {handle!r} is taken") from e
        return _me(db, uid)


@router.get("/factions/mine")
def my_factions(device: CurrentDevice = Depends(current_device)):
    """Every faction the viewer belongs to, with their role in each."""
    with db_session(viewer_id=str(device.user_id)) as db:
        return {"factions": _my_factions(db, str(device.user_id))}
