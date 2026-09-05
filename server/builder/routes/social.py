"""Social: posts, kudos, comments, follows, factions, and the feed that ties them together.

docs/social.md is the spec; migration 0007 is the schema. Two things shape everything in
this file:

Visibility is decided by the database, not here. Every read goes through `db_session`
with the caller as viewer, and the policies on `posts` (via the SECURITY DEFINER
`can_view_post`) decide what comes back. The routes add ownership checks so that the
right status code is returned — 403 for a post you can see but do not own, 404 for one
you cannot see — but a route that forgot a check would leak nothing, only misreport.

A feed item is self-contained. One query per page joins the post to its author, session,
strip and analysis; one more fetches the page's media. Nothing fans out per row, because
the phone renders the screen from one response over a cellular connection.
"""

import base64
import re
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from ..auth import CurrentDevice, current_device, new_user_code
from ..db import db_session
from ..objectstore import configured as object_store_configured
from ..objectstore import presign_put, public_url
from .sessions import _row_to_session

router = APIRouter(prefix="/v1", tags=["social"])

VISIBILITIES = ("private", "followers", "public")

#: Feed page size, and the ceiling a client may ask for. Thirty recap cards is about four
#: screens of scrolling; more than that per request is bandwidth spent on cards that are
#: never seen when the person pulls to refresh instead.
FEED_PAGE = 30

#: docs/social.md: up to 6 photos, one voice note of at most 90 s.
MAX_PHOTOS = 6
MAX_AUDIO_MS = 90_000

#: Upload ceilings, in bytes. The phone downsizes to a 2048-px long edge at JPEG q=0.85
#: before upload, which lands well under 2 MB; 12 MB leaves room for a PNG screenshot
#: that was not recompressed. 90 s of AAC at 128 kb/s is about 1.4 MB.
MAX_PHOTO_BYTES = 12 * 1024 * 1024
MAX_AUDIO_BYTES = 8 * 1024 * 1024

#: Content types a presign will sign, with the extension the object key gets. The type
#: is part of the signature, so the phone cannot upload something else under this key.
PHOTO_TYPES = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/heic": "heic"}
AUDIO_TYPES = {"audio/mp4": "m4a", "audio/m4a": "m4a", "audio/aac": "aac", "audio/mpeg": "mp3"}

#: Mirrors `Tuning.dayBoundaryHour` and the `interval '4 hours'` in sync.py. Sessions
#: already carry it in `local_date`; the board needs it once more to decide which Monday
#: "this week" started on, so that at 01:00 on Monday the board has not reset yet.
DAY_BOUNDARY_HOUR = 4

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,39}$")
_WEEK_RE = re.compile(r"^(\d{4})-W(\d{2})$")


# ---------------------------------------------------------------------------- helpers


def _uuid(value: str, what: str = "id") -> str:
    try:
        return str(uuid.UUID(value))
    except (ValueError, AttributeError, TypeError) as e:
        raise HTTPException(422, f"{what} is not a uuid") from e


def _before(value: str | None) -> datetime | None:
    if value is None:
        return None
    # `next_before` ends in "+00:00". A client that pastes it into a query string without
    # encoding delivers "2026-08-15T10:00:00 00:00" — the plus became a space — and the
    # only honest reading of a trailing " HH:MM" is the offset it used to be.
    value = re.sub(r" (\d{2}:\d{2})$", r"+\1", value.strip())
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as e:
        raise HTTPException(422, "before must be an ISO 8601 timestamp") from e
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _author(handle, display_name) -> dict:
    return {"handle": handle, "display_name": display_name}


def _media_dict(m) -> dict:
    return {
        "id": str(m.id),
        "kind": m.kind,
        "object_key": m.object_key,
        "width": m.width,
        "height": m.height,
        "duration_ms": m.duration_ms,
        "position": m.position,
        "url": public_url(m.object_key),
    }


# One statement per page. `s.*` supplies everything `_row_to_session` reads; the post's
# own columns are aliased so nothing collides with the session's.
_ITEM_SELECT = """
    SELECT p.id AS post_id, p.caption, p.visibility, p.share_analysis,
           p.kudos_count, p.comment_count,
           p.created_at AS post_created_at, p.updated_at AS post_updated_at,
           u.handle, u.display_name,
           s.*, r.public_name,
           st.cols, st.marks, st.t0_ms, st.t1_ms,
           a.body AS analysis_body,
           EXISTS (SELECT 1 FROM kudos k
                   WHERE k.post_id = p.id AND k.user_id = CAST(:viewer AS uuid)) AS you_kudosed
    FROM posts p
    JOIN users u ON u.id = p.user_id
    JOIN sessions s ON s.id = p.session_id
    LEFT JOIN repos r ON r.id = s.repo_id
    LEFT JOIN session_strips st ON st.session_id = s.id
    LEFT JOIN session_analysis a ON a.session_id = s.id
"""


def _item(r, media: list) -> dict:
    strip = None
    if r.cols is not None:
        strip = {
            "cols": base64.b64encode(r.cols).decode(),
            "marks": r.marks,
            "t0_ms": r.t0_ms,
            "t1_ms": r.t1_ms,
        }
    # Headline and summary only, unless the author opted the whole document in. The
    # owner reads their own full analysis through /v1/sessions/{id}; a feed item has one
    # shape whoever is looking at it.
    analysis = None
    if r.analysis_body is not None:
        body = r.analysis_body
        if r.share_analysis:
            analysis = body
        else:
            analysis = {"headline": body.get("headline"), "summary": body.get("summary")}
    photos = [_media_dict(m) for m in media if m.kind == "photo"]
    audio = next((_media_dict(m) for m in media if m.kind == "audio"), None)
    return {
        "id": str(r.post_id),
        "author": _author(r.handle, r.display_name),
        "caption": r.caption,
        "visibility": r.visibility,
        "share_analysis": r.share_analysis,
        "created_at": r.post_created_at.isoformat(),
        "updated_at": r.post_updated_at.isoformat(),
        "session": _row_to_session(r),
        "strip": strip,
        "analysis": analysis,
        "photos": photos,
        "audio": audio,
        "kudos_count": r.kudos_count,
        "comment_count": r.comment_count,
        "you_kudosed": bool(r.you_kudosed),
    }


def _page(
    db,
    viewer: str,
    where: str,
    params: dict,
    *,
    before: datetime | None = None,
    before_id: str | None = None,
    limit: int = FEED_PAGE,
) -> dict:
    """A keyset page of feed items, newest first.

    Keyset on (created_at, id) rather than OFFSET: people post while others scroll, and an
    offset under concurrent insertion skips and repeats rows. The id is the tiebreaker;
    a client that only echoes `next_before` still works, it just risks skipping a post
    committed in the same microsecond as the boundary one.
    """
    clauses = [where]
    params = {**params, "viewer": viewer, "limit": limit}
    if before is not None:
        params["before"] = before
        if before_id is not None:
            params["before_id"] = _uuid(before_id, "before_id")
            clauses.append("(p.created_at, p.id) < (:before, CAST(:before_id AS uuid))")
        else:
            clauses.append("p.created_at < :before")
    rows = db.execute(
        text(
            f"""
            {_ITEM_SELECT}
            WHERE {" AND ".join(clauses)}
            ORDER BY p.created_at DESC, p.id DESC
            LIMIT :limit
            """
        ),
        params,
    ).all()

    media: dict[Any, list] = {}
    if rows:
        for m in db.execute(
            text(
                """
                SELECT id, post_id, kind, object_key, width, height, duration_ms, position
                FROM post_media
                WHERE post_id = ANY(CAST(:ids AS uuid[]))
                ORDER BY position, created_at
                """
            ),
            {"ids": [str(r.post_id) for r in rows]},
        ).all():
            media.setdefault(m.post_id, []).append(m)

    full = len(rows) == limit
    return {
        "items": [_item(r, media.get(r.post_id, [])) for r in rows],
        "next_before": rows[-1].post_created_at.isoformat() if full else None,
        "next_before_id": str(rows[-1].post_id) if full else None,
    }


def _post_row(db, post_id: str):
    return db.execute(
        text("SELECT id, user_id, session_id, visibility FROM posts WHERE id = CAST(:p AS uuid)"),
        {"p": post_id},
    ).first()


def _owned_post(db, post_id: str, user_id: str):
    """The post, if the caller owns it: 404 when invisible, 403 when visible but not theirs."""
    row = _post_row(db, post_id)
    if row is None:
        raise HTTPException(404, "post not found")
    if str(row.user_id) != user_id:
        raise HTTPException(403, "not your post")
    return row


def _can_view(db, post_id: str) -> bool:
    return bool(db.execute(text("SELECT can_view_post(CAST(:p AS uuid))"), {"p": post_id}).scalar())


def _set_shared(db, session_id, visibility: str) -> None:
    """Keep `sessions.is_shared` in step with the post.

    `is_shared` is what `sessions_public` reads, and a session it is true for is readable
    by anyone holding its uuid — strip, stats and analysis included. That is the right
    thing for a `followers` or `public` post (docs/social.md: posts reuse `sessions_public`
    through the session id) and the wrong thing for a `private` one, which means "only
    you". So private posts leave the flag down; the owner still sees their own post and
    session through the owner policies.
    """
    shared = visibility != "private"
    db.execute(
        text(
            """
            UPDATE sessions
               SET is_shared = :shared,
                   shared_at = CASE WHEN :shared THEN COALESCE(shared_at, now()) ELSE NULL END,
                   updated_at = now()
             WHERE id = :sid
            """
        ),
        {"shared": shared, "sid": str(session_id)},
    )


def _week_start(week: str | None, tz: str) -> date:
    """The Monday a board week starts on, in the faction's zone with the 04:00 rule."""
    if week is None:
        local = datetime.now(ZoneInfo(tz)) - timedelta(hours=DAY_BOUNDARY_HOUR)
        year, number, _ = local.date().isocalendar()
    else:
        m = _WEEK_RE.match(week)
        if m is None:
            raise HTTPException(422, "week must look like 2026-W33")
        year, number = int(m.group(1)), int(m.group(2))
    try:
        return date.fromisocalendar(year, number, 1)
    except ValueError as e:
        raise HTTPException(422, f"no such ISO week: {week}") from e


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40]
    return slug if _SLUG_RE.match(slug or "") else ""


def _normalize_code(code: str) -> str:
    raw = re.sub(r"[^A-Z0-9]", "", code.upper())
    return f"{raw[:4]}-{raw[4:]}" if len(raw) == 8 else code.strip().upper()


# ------------------------------------------------------------------------------ posts


class PostCreate(BaseModel):
    session_id: str
    caption: str | None = Field(default=None, max_length=1000)
    visibility: str = "private"
    share_analysis: bool = False


class PostPatch(BaseModel):
    caption: str | None = Field(default=None, max_length=1000)
    visibility: str | None = None
    share_analysis: bool | None = None


@router.post("/posts", status_code=201)
def create_post(body: PostCreate, device: CurrentDevice = Depends(current_device)):
    """Share a session. Never automatic; this call is the act.

    The session must be the caller's and finished: a live snapshot is replaced in place
    every minute, and a card whose numbers keep moving is not a recap. A repository the
    caller has excluded cannot be posted at all — the sweep normally deletes those
    sessions, but the check does not depend on the sweep having run.
    """
    if body.visibility not in VISIBILITIES:
        raise HTTPException(422, f"visibility must be one of {list(VISIBILITIES)}")
    uid = str(device.user_id)
    sid = _uuid(body.session_id, "session_id")

    with db_session(viewer_id=uid) as db:
        s = db.execute(
            text("SELECT id, user_id, state, repo_id FROM sessions WHERE id = CAST(:s AS uuid)"),
            {"s": sid},
        ).first()
        if s is None:
            raise HTTPException(404, "session not found")
        if str(s.user_id) != uid:
            raise HTTPException(403, "not your session")
        if s.state == "live":
            raise HTTPException(409, "a live session cannot be shared until it finishes")
        if s.repo_id is not None:
            excluded = db.execute(
                text("SELECT session_repo_excluded(CAST(:u AS uuid), CAST(:r AS uuid))"),
                {"u": uid, "r": str(s.repo_id)},
            ).scalar()
            if excluded:
                raise HTTPException(403, "this repository is excluded and cannot be shared")
        if db.execute(
            text("SELECT 1 FROM posts WHERE session_id = CAST(:s AS uuid)"), {"s": sid}
        ).first():
            raise HTTPException(409, "this session is already shared")

        post_id = db.execute(
            text(
                """
                INSERT INTO posts (session_id, user_id, caption, visibility, share_analysis)
                VALUES (CAST(:s AS uuid), CAST(:u AS uuid), :caption, :vis, :share)
                RETURNING id
                """
            ),
            {
                "s": sid,
                "u": uid,
                "caption": body.caption,
                "vis": body.visibility,
                "share": body.share_analysis,
            },
        ).scalar()
        _set_shared(db, sid, body.visibility)
        page = _page(db, uid, "p.id = CAST(:pid AS uuid)", {"pid": str(post_id)}, limit=1)
    return page["items"][0]


@router.get("/posts/{post_id}")
def get_post(post_id: str, device: CurrentDevice = Depends(current_device)):
    """One post, for deep links. Not in the spec's API block; a push or a shared link
    needs somewhere to land that is not a whole feed."""
    uid = str(device.user_id)
    pid = _uuid(post_id, "post id")
    with db_session(viewer_id=uid) as db:
        page = _page(db, uid, "p.id = CAST(:pid AS uuid)", {"pid": pid}, limit=1)
    if not page["items"]:
        raise HTTPException(404, "post not found")
    return page["items"][0]


@router.patch("/posts/{post_id}")
def patch_post(post_id: str, body: PostPatch, device: CurrentDevice = Depends(current_device)):
    uid = str(device.user_id)
    pid = _uuid(post_id, "post id")
    if body.visibility is not None and body.visibility not in VISIBILITIES:
        raise HTTPException(422, f"visibility must be one of {list(VISIBILITIES)}")

    sets, params = [], {"pid": pid}
    # `model_fields_set` tells a caption that was omitted from one that was set to null:
    # the second clears it, the first leaves it alone.
    if "caption" in body.model_fields_set:
        sets.append("caption = :caption")
        params["caption"] = body.caption
    if body.visibility is not None:
        sets.append("visibility = :vis")
        params["vis"] = body.visibility
    if body.share_analysis is not None:
        sets.append("share_analysis = :share")
        params["share"] = body.share_analysis
    if not sets:
        raise HTTPException(422, "nothing to change")

    with db_session(viewer_id=uid) as db:
        post = _owned_post(db, pid, uid)
        assignments = ", ".join(sets)
        db.execute(
            text(
                f"UPDATE posts SET {assignments}, updated_at = now() WHERE id = CAST(:pid AS uuid)"
            ),
            params,
        )
        if body.visibility is not None:
            _set_shared(db, post.session_id, body.visibility)
        page = _page(db, uid, "p.id = CAST(:pid AS uuid)", {"pid": pid}, limit=1)
    return page["items"][0]


@router.delete("/posts/{post_id}", status_code=204)
def delete_post(post_id: str, device: CurrentDevice = Depends(current_device)):
    """Un-share. The session becomes private again and leaves every feed and board now;
    the photos leave the bucket on the deletion sweep, which reads `post_media` rows that
    no longer have a post — so the DB row is what goes, not the object."""
    uid = str(device.user_id)
    pid = _uuid(post_id, "post id")
    with db_session(viewer_id=uid) as db:
        post = _owned_post(db, pid, uid)
        _set_shared(db, post.session_id, "private")
        db.execute(text("DELETE FROM posts WHERE id = CAST(:pid AS uuid)"), {"pid": pid})
    return Response(status_code=204)


# ------------------------------------------------------------------------------ media


class PresignRequest(BaseModel):
    kind: str
    content_type: str
    bytes: int = Field(gt=0)


class MediaAttach(BaseModel):
    object_key: str = Field(min_length=1, max_length=512)
    kind: str = "photo"
    width: int | None = Field(default=None, gt=0)
    height: int | None = Field(default=None, gt=0)
    duration_ms: int | None = Field(default=None, gt=0)


def _media_counts(db, post_id: str) -> tuple[int, int]:
    row = db.execute(
        text(
            """
            SELECT COUNT(*) FILTER (WHERE kind = 'photo') AS photos,
                   COUNT(*) FILTER (WHERE kind = 'audio') AS audio
            FROM post_media WHERE post_id = CAST(:p AS uuid)
            """
        ),
        {"p": post_id},
    ).one()
    return int(row.photos), int(row.audio)


def _check_media_room(kind: str, photos: int, audio: int) -> None:
    if kind == "photo" and photos >= MAX_PHOTOS:
        raise HTTPException(409, f"a post carries at most {MAX_PHOTOS} photos")
    if kind == "audio" and audio >= 1:
        raise HTTPException(409, "a post carries at most one voice note")


@router.post("/posts/{post_id}/media:presign")
def presign_media(
    post_id: str, body: PresignRequest, device: CurrentDevice = Depends(current_device)
):
    """A presigned PUT the phone uploads to directly. The server never sees the bytes.

    503, not 500, when storage is unconfigured: the rest of social works without it, and
    the message says which knobs to set. Room on the post is checked here for a helpful
    early error and again on attach, where it is enforced.
    """
    uid = str(device.user_id)
    pid = _uuid(post_id, "post id")
    if body.kind not in ("photo", "audio"):
        raise HTTPException(422, "kind must be photo or audio")
    types = PHOTO_TYPES if body.kind == "photo" else AUDIO_TYPES
    if body.content_type not in types:
        raise HTTPException(422, f"content_type must be one of {sorted(types)}")
    ceiling = MAX_PHOTO_BYTES if body.kind == "photo" else MAX_AUDIO_BYTES
    if body.bytes > ceiling:
        raise HTTPException(413, f"{body.kind} uploads are limited to {ceiling} bytes")
    if not object_store_configured():
        raise HTTPException(
            503,
            "media upload is not configured on this server: set OBJECT_STORE_ENDPOINT, "
            "OBJECT_STORE_BUCKET, OBJECT_STORE_KEY and OBJECT_STORE_SECRET",
        )

    with db_session(viewer_id=uid) as db:
        _owned_post(db, pid, uid)
        photos, audio = _media_counts(db, pid)
    _check_media_room(body.kind, photos, audio)

    # Namespaced under the post, so attach can verify the key belongs here and the
    # deletion sweep can list everything a post owned with one prefix.
    object_key = f"posts/{pid}/{uuid.uuid4().hex}.{types[body.content_type]}"
    expires = 900
    return {
        "upload_url": presign_put(object_key, body.content_type, expires),
        "object_key": object_key,
        "method": "PUT",
        # Signed into the URL; the upload must send exactly this.
        "headers": {"Content-Type": body.content_type},
        "expires_in": expires,
    }


@router.post("/posts/{post_id}/media", status_code=201)
def attach_media(post_id: str, body: MediaAttach, device: CurrentDevice = Depends(current_device)):
    uid = str(device.user_id)
    pid = _uuid(post_id, "post id")
    if body.kind not in ("photo", "audio"):
        raise HTTPException(422, "kind must be photo or audio")
    if not body.object_key.startswith(f"posts/{pid}/"):
        raise HTTPException(422, "object_key does not belong to this post")
    if body.kind == "photo":
        if body.width is None or body.height is None:
            raise HTTPException(422, "a photo needs width and height")
        if body.duration_ms is not None:
            raise HTTPException(422, "a photo has no duration")
    else:
        if body.duration_ms is None:
            raise HTTPException(422, "a voice note needs duration_ms")
        if body.duration_ms > MAX_AUDIO_MS:
            raise HTTPException(422, f"a voice note is at most {MAX_AUDIO_MS} ms")

    with db_session(viewer_id=uid) as db:
        _owned_post(db, pid, uid)
        photos, audio = _media_counts(db, pid)
        _check_media_room(body.kind, photos, audio)
        try:
            row = db.execute(
                text(
                    """
                    INSERT INTO post_media
                      (post_id, kind, object_key, width, height, duration_ms, position)
                    VALUES (CAST(:p AS uuid), :kind, :key, :w, :h, :d, :pos)
                    RETURNING id, kind, object_key, width, height, duration_ms, position
                    """
                ),
                {
                    "p": pid,
                    "kind": body.kind,
                    "key": body.object_key,
                    "w": body.width if body.kind == "photo" else None,
                    "h": body.height if body.kind == "photo" else None,
                    "d": body.duration_ms if body.kind == "audio" else None,
                    "pos": photos + audio,
                },
            ).one()
        except IntegrityError as e:
            raise HTTPException(409, "that object is already attached") from e
    return _media_dict(row)


# ------------------------------------------------------------------------------ kudos


def _kudos_state(db, post_id: str, user_id: str) -> dict:
    row = db.execute(
        text(
            """
            SELECT p.kudos_count,
                   EXISTS (SELECT 1 FROM kudos k
                           WHERE k.post_id = p.id AND k.user_id = CAST(:u AS uuid)) AS mine
            FROM posts p WHERE p.id = CAST(:p AS uuid)
            """
        ),
        {"p": post_id, "u": user_id},
    ).one()
    return {"kudos_count": row.kudos_count, "you_kudosed": bool(row.mine)}


@router.post("/posts/{post_id}/kudos")
def give_kudos(post_id: str, device: CurrentDevice = Depends(current_device)):
    """One tap, idempotent. The count is bumped by a trigger, not here, because the
    giver has no UPDATE right on someone else's post row."""
    uid = str(device.user_id)
    pid = _uuid(post_id, "post id")
    with db_session(viewer_id=uid) as db:
        if not _can_view(db, pid):
            raise HTTPException(404, "post not found")
        db.execute(
            text(
                """
                INSERT INTO kudos (user_id, post_id) VALUES (CAST(:u AS uuid), CAST(:p AS uuid))
                ON CONFLICT (user_id, post_id) DO NOTHING
                """
            ),
            {"u": uid, "p": pid},
        )
        return _kudos_state(db, pid, uid)


@router.delete("/posts/{post_id}/kudos")
def take_kudos(post_id: str, device: CurrentDevice = Depends(current_device)):
    uid = str(device.user_id)
    pid = _uuid(post_id, "post id")
    with db_session(viewer_id=uid) as db:
        if not _can_view(db, pid):
            raise HTTPException(404, "post not found")
        db.execute(
            text(
                "DELETE FROM kudos WHERE user_id = CAST(:u AS uuid) AND post_id = CAST(:p AS uuid)"
            ),
            {"u": uid, "p": pid},
        )
        return _kudos_state(db, pid, uid)


# --------------------------------------------------------------------------- comments


class CommentCreate(BaseModel):
    body: str = Field(min_length=1, max_length=500)


def _comment_dict(c) -> dict:
    return {
        "id": str(c.id),
        "post_id": str(c.post_id),
        "author": _author(c.handle, c.display_name),
        "body": c.body,
        "created_at": c.created_at.isoformat(),
    }


@router.post("/posts/{post_id}/comments", status_code=201)
def create_comment(
    post_id: str, body: CommentCreate, device: CurrentDevice = Depends(current_device)
):
    uid = str(device.user_id)
    pid = _uuid(post_id, "post id")
    with db_session(viewer_id=uid) as db:
        if not _can_view(db, pid):
            raise HTTPException(404, "post not found")
        cid = db.execute(
            text(
                """
                INSERT INTO comments (post_id, user_id, body)
                VALUES (CAST(:p AS uuid), CAST(:u AS uuid), :body)
                RETURNING id
                """
            ),
            {"p": pid, "u": uid, "body": body.body.strip()},
        ).scalar()
        row = db.execute(
            text(
                """
                SELECT c.id, c.post_id, c.body, c.created_at, u.handle, u.display_name,
                       (SELECT comment_count FROM posts WHERE id = c.post_id) AS comment_count
                FROM comments c JOIN users u ON u.id = c.user_id
                WHERE c.id = :c
                """
            ),
            {"c": cid},
        ).one()
    return {**_comment_dict(row), "comment_count": row.comment_count}


@router.get("/posts/{post_id}/comments")
def list_comments(post_id: str, device: CurrentDevice = Depends(current_device)):
    """Flat and oldest-first, the way a conversation reads. Not paginated: comments are
    capped at 500 characters and threads are deliberately absent, and a recap card that
    draws hundreds of comments is a problem the spec would rather not have."""
    uid = str(device.user_id)
    pid = _uuid(post_id, "post id")
    with db_session(viewer_id=uid) as db:
        if not _can_view(db, pid):
            raise HTTPException(404, "post not found")
        rows = db.execute(
            text(
                """
                SELECT c.id, c.post_id, c.body, c.created_at, u.handle, u.display_name
                FROM comments c JOIN users u ON u.id = c.user_id
                WHERE c.post_id = CAST(:p AS uuid) AND c.deleted_at IS NULL
                ORDER BY c.created_at, c.id LIMIT 500
                """
            ),
            {"p": pid},
        ).all()
    return {"comments": [_comment_dict(c) for c in rows]}


@router.delete("/comments/{comment_id}", status_code=204)
def delete_comment(comment_id: str, device: CurrentDevice = Depends(current_device)):
    """Soft delete by the author. The row stays; the body stops being served and the
    count comes down through the same trigger that put it up."""
    uid = str(device.user_id)
    cid = _uuid(comment_id, "comment id")
    with db_session(viewer_id=uid) as db:
        result = db.execute(
            text(
                """
                UPDATE comments SET deleted_at = now()
                WHERE id = CAST(:c AS uuid) AND user_id = CAST(:u AS uuid) AND deleted_at IS NULL
                """
            ),
            {"c": cid, "u": uid},
        )
        if not result.rowcount:
            raise HTTPException(404, "comment not found")
    return Response(status_code=204)


# ------------------------------------------------------------------------------- feed


@router.get("/feed")
def feed(
    device: CurrentDevice = Depends(current_device),
    before: str | None = None,
    before_id: str | None = None,
    limit: int = Query(FEED_PAGE, ge=1, le=FEED_PAGE),
):
    """People you follow, people in your factions, and you. Reverse chronological; the
    only ranking anywhere in social is the faction board, and that is a sum."""
    uid = str(device.user_id)
    with db_session(viewer_id=uid) as db:
        return _page(
            db,
            uid,
            """
            (p.user_id = CAST(:viewer AS uuid)
             OR p.user_id IN (SELECT followee_id FROM follows
                              WHERE follower_id = CAST(:viewer AS uuid) AND state = 'accepted')
             OR p.user_id IN (SELECT them.user_id
                              FROM faction_members me
                              JOIN faction_members them ON them.faction_id = me.faction_id
                              WHERE me.user_id = CAST(:viewer AS uuid)))
            """,
            {},
            before=_before(before),
            before_id=before_id,
            limit=limit,
        )


def _member_faction(db, slug: str):
    """The faction behind a slug, for a member. 404 when it is invisible or missing, 403
    when it is open (so visible) but the caller has not joined."""
    f = db.execute(
        text("SELECT id, slug, name, open, tz, created_by FROM factions WHERE slug = :s"),
        {"s": slug},
    ).first()
    if f is None:
        raise HTTPException(404, "faction not found")
    member = db.execute(
        text("SELECT is_faction_member(CAST(:f AS uuid))"), {"f": str(f.id)}
    ).scalar()
    if not member:
        raise HTTPException(403, "join this faction to see it")
    return f


@router.get("/feed/faction/{slug}")
def faction_feed(
    slug: str,
    device: CurrentDevice = Depends(current_device),
    before: str | None = None,
    before_id: str | None = None,
    limit: int = Query(FEED_PAGE, ge=1, le=FEED_PAGE),
):
    """The same shape as /feed, scoped to one faction's members. Visibility per post
    is unchanged: a member's followers-only post shows here only to their followers."""
    uid = str(device.user_id)
    with db_session(viewer_id=uid) as db:
        f = _member_faction(db, slug)
        return _page(
            db,
            uid,
            "p.user_id IN (SELECT user_id FROM faction_members "
            "WHERE faction_id = CAST(:f AS uuid))",
            {"f": str(f.id)},
            before=_before(before),
            before_id=before_id,
            limit=limit,
        )


# ---------------------------------------------------------------------------- follows


def _user_by_handle(db, handle: str):
    row = db.execute(
        text(
            """
            SELECT id, handle, display_name, profile_public, created_at
            FROM users WHERE handle = :h AND deleted_at IS NULL
            """
        ),
        {"h": handle},
    ).first()
    if row is None:
        raise HTTPException(404, "no such user")
    return row


def _follow_state(db, follower: str, followee: str) -> str | None:
    return db.execute(
        text(
            """
            SELECT state FROM follows
            WHERE follower_id = CAST(:a AS uuid) AND followee_id = CAST(:b AS uuid)
            """
        ),
        {"a": follower, "b": followee},
    ).scalar()


# Declared before `/follows/{handle}`: a path parameter matches `[^/]+`, so without the
# ordering `alice:accept` would arrive at the follow route as a handle.
@router.post("/follows/{handle}:accept")
def accept_follow(handle: str, device: CurrentDevice = Depends(current_device)):
    """The caller is the followee; `handle` is who asked."""
    uid = str(device.user_id)
    with db_session(viewer_id=uid) as db:
        follower = _user_by_handle(db, handle)
        result = db.execute(
            text(
                """
                UPDATE follows SET state = 'accepted'
                WHERE follower_id = :a AND followee_id = CAST(:b AS uuid) AND state = 'pending'
                """
            ),
            {"a": follower.id, "b": uid},
        )
        if not result.rowcount:
            raise HTTPException(404, "no pending request from that user")
    return {"handle": follower.handle, "state": "accepted"}


@router.post("/follows/{handle}")
def follow(handle: str, device: CurrentDevice = Depends(current_device)):
    """Follow. Immediate for a public profile, a request otherwise — and the policy on
    `follows` enforces that split too, so the state cannot be forged past this route."""
    uid = str(device.user_id)
    with db_session(viewer_id=uid) as db:
        target = _user_by_handle(db, handle)
        if str(target.id) == uid:
            raise HTTPException(422, "you cannot follow yourself")
        db.execute(
            text(
                """
                INSERT INTO follows (follower_id, followee_id, state)
                VALUES (CAST(:a AS uuid), :b, :state)
                ON CONFLICT (follower_id, followee_id) DO NOTHING
                """
            ),
            {"a": uid, "b": target.id, "state": "accepted" if target.profile_public else "pending"},
        )
        state = _follow_state(db, uid, str(target.id))
    return {"handle": target.handle, "state": state}


@router.delete("/follows/{handle}", status_code=204)
def unfollow(handle: str, device: CurrentDevice = Depends(current_device)):
    uid = str(device.user_id)
    with db_session(viewer_id=uid) as db:
        target = _user_by_handle(db, handle)
        db.execute(
            text("DELETE FROM follows WHERE follower_id = CAST(:a AS uuid) AND followee_id = :b"),
            {"a": uid, "b": target.id},
        )
    return Response(status_code=204)


@router.get("/users/{handle}")
def user_profile(
    handle: str,
    device: CurrentDevice = Depends(current_device),
    before: str | None = None,
    before_id: str | None = None,
    limit: int = Query(FEED_PAGE, ge=1, le=FEED_PAGE),
):
    """Profile plus the posts the CALLER may see — public ones for a stranger, followers
    ones too once accepted, everything for the owner. RLS decides; the query only scopes."""
    uid = str(device.user_id)
    with db_session(viewer_id=uid) as db:
        u = _user_by_handle(db, handle)
        page = _page(
            db,
            uid,
            "p.user_id = :target",
            {"target": u.id},
            before=_before(before),
            before_id=before_id,
            limit=limit,
        )
        is_you = str(u.id) == uid
        following = None if is_you else _follow_state(db, uid, str(u.id))
    return {
        "profile": {
            "handle": u.handle,
            "display_name": u.display_name,
            "profile_public": u.profile_public,
            "created_at": u.created_at.isoformat(),
            "is_you": is_you,
            # 'accepted', 'pending', or null. Follower counts are absent on purpose: the
            # viewer can only see follows they are party to, so a count would be wrong.
            "follow_state": following,
        },
        "posts": page["items"],
        "next_before": page["next_before"],
        "next_before_id": page["next_before_id"],
    }


# --------------------------------------------------------------------------- factions


class FactionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    slug: str | None = None
    open: bool = False
    tz: str | None = None


class FactionJoin(BaseModel):
    code: str | None = None
    slug: str | None = None


class MembershipPatch(BaseModel):
    share_hours: bool


def _faction_dict(f, role: str | None = None) -> dict:
    out = {"slug": f.slug, "name": f.name, "open": f.open, "tz": f.tz}
    if role is not None:
        out["role"] = role
    # The invite code goes to admins only. Members can read the row, but a code that
    # every member's phone caches is a code that ends up in a screenshot.
    if role == "admin":
        out["join_code"] = f.join_code
    return out


@router.post("/factions", status_code=201)
def create_faction(body: FactionCreate, device: CurrentDevice = Depends(current_device)):
    """Create a club; the creator is its first admin. Timezone defaults to the creator's."""
    uid = str(device.user_id)
    slug = (body.slug or "").strip().lower() or _slugify(body.name)
    if not _SLUG_RE.match(slug):
        raise HTTPException(422, "slug must be 2-40 characters of a-z, 0-9 and '-'")

    with db_session(viewer_id=uid) as db:
        tz = body.tz or db.execute(text("SELECT tz FROM users WHERE id = :u"), {"u": uid}).scalar()
        tz = tz or "UTC"
        try:
            ZoneInfo(tz)
        except (ZoneInfoNotFoundError, ValueError) as e:
            raise HTTPException(422, f"unknown timezone {tz!r}") from e

        # Uniqueness of slug and code is the constraint's job, not a pre-check's: under
        # RLS this viewer cannot see other people's factions, so a SELECT-before-INSERT
        # here would be blind and pass every time.
        try:
            f = db.execute(
                text(
                    """
                    INSERT INTO factions (slug, name, join_code, open, tz, created_by)
                    VALUES (:slug, :name, :code, :open, :tz, CAST(:u AS uuid))
                    RETURNING id, slug, name, join_code, open, tz
                    """
                ),
                {
                    "slug": slug,
                    "name": body.name.strip(),
                    "code": new_user_code(),
                    "open": body.open,
                    "tz": tz,
                    "u": uid,
                },
            ).one()
        except IntegrityError as e:
            raise HTTPException(409, f"the slug {slug!r} is taken") from e
        db.execute(
            text(
                """
                INSERT INTO faction_members (faction_id, user_id, role)
                VALUES (:f, CAST(:u AS uuid), 'admin')
                """
            ),
            {"f": f.id, "u": uid},
        )
    return _faction_dict(f, role="admin")


@router.post("/factions:join")
def join_faction(body: FactionJoin, device: CurrentDevice = Depends(current_device)):
    """Join by code, or by slug when the faction is open. The lookup and the insert both
    run inside the SECURITY DEFINER `join_faction`: the joiner cannot see the row yet."""
    uid = str(device.user_id)
    code = _normalize_code(body.code) if body.code else None
    slug = body.slug.strip().lower() if body.slug else None
    if not code and not slug:
        raise HTTPException(422, "send a code or a slug")
    with db_session(viewer_id=uid) as db:
        fid = db.execute(
            text("SELECT join_faction(:code, :slug)"), {"code": code, "slug": slug}
        ).scalar()
        if fid is None:
            raise HTTPException(404, "no faction matches that code")
        f = db.execute(
            text("SELECT id, slug, name, join_code, open, tz FROM factions WHERE id = :f"),
            {"f": fid},
        ).one()
        role = db.execute(
            text(
                "SELECT role FROM faction_members "
                "WHERE faction_id = :f AND user_id = CAST(:u AS uuid)"
            ),
            {"f": fid, "u": uid},
        ).scalar()
    return _faction_dict(f, role=role)


@router.get("/factions/{slug}/board")
def faction_board(
    slug: str,
    device: CurrentDevice = Depends(current_device),
    week: str | None = Query(None, description="ISO week, e.g. 2026-W33; default: this week"),
):
    """The weekly board: attended hours, sessions, longest attended session, per member,
    ranked by attended hours. Members who keep their hours private are listed with
    zeros. The week runs Monday 04:00 to Monday 04:00 in the faction's zone."""
    uid = str(device.user_id)
    with db_session(viewer_id=uid) as db:
        f = _member_faction(db, slug)
        start = _week_start(week, f.tz)
        rows = db.execute(
            text("SELECT * FROM faction_board(CAST(:f AS uuid), :start)"),
            {"f": str(f.id), "start": start},
        ).all()
        people = {
            r.id: r
            for r in db.execute(
                text(
                    "SELECT id, handle, display_name FROM users "
                    "WHERE id = ANY(CAST(:ids AS uuid[]))"
                ),
                {"ids": [str(r.member_id) for r in rows]},
            ).all()
        }
        my_role = next((r.member_role for r in rows if str(r.member_id) == uid), None)
        code = (
            db.execute(text("SELECT join_code FROM factions WHERE id = :f"), {"f": f.id}).scalar()
            if my_role == "admin"
            else None
        )

    iso_year, iso_week, _ = start.isocalendar()
    out = {
        "faction": {"slug": f.slug, "name": f.name, "open": f.open, "tz": f.tz, "role": my_role},
        "week": f"{iso_year}-W{iso_week:02d}",
        "week_start": start.isoformat(),
        "week_end": (start + timedelta(days=6)).isoformat(),
        "members": [
            {
                **_author(
                    people[r.member_id].handle if r.member_id in people else None,
                    people[r.member_id].display_name if r.member_id in people else None,
                ),
                "role": r.member_role,
                "share_hours": r.member_share_hours,
                "you": str(r.member_id) == uid,
                "attended_seconds": int(r.attended_seconds),
                "sessions": int(r.session_count),
                "longest_attended_seconds": int(r.longest_attended_seconds),
            }
            for r in rows
        ],
    }
    if code:
        out["faction"]["join_code"] = code
    return out


@router.patch("/factions/{slug}/members/me")
def patch_membership(
    slug: str, body: MembershipPatch, device: CurrentDevice = Depends(current_device)
):
    """Opt your hours in or out of the board. Out means zeros, not absence."""
    uid = str(device.user_id)
    with db_session(viewer_id=uid) as db:
        f = _member_faction(db, slug)
        db.execute(
            text(
                """
                UPDATE faction_members SET share_hours = :share
                WHERE faction_id = :f AND user_id = CAST(:u AS uuid)
                """
            ),
            {"share": body.share_hours, "f": f.id, "u": uid},
        )
    return {"slug": f.slug, "share_hours": body.share_hours}
