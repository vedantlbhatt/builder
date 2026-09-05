"""The hook channel: Claude Code posts its own transcript; the server cuts the sessions.

Zero install on the machine (docs/hooks-capture.md): one entry in Claude Code's
`settings.json` runs a 30-line shell script on `UserPromptSubmit`, `Stop` and
`SessionEnd`, which POSTs the transcript TAIL here with a capture key. Chunks are keyed by
byte offset, so a session that fires the hook forty times appends forty tails, a replay
touches nothing, and a hook that lost its offset file resends from where the server says.

Trade-off, stated in the docs and in PRIVACY.md: this channel sends the RAW transcript,
not the contract fields. The server keeps only what the contract describes plus the
digest, and deletes the raw bytes as soon as the session is final (or after
`RETENTION_DAYS` for a session that never finalises). `privacy/upload-contract.json` is
untouched: it still describes what capture and the Mac send; this is a separate, opt-in
path that a person turns on by installing the hook.
"""

from __future__ import annotations

import gzip
import logging
import re
import zlib

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import ValidationError
from sqlalchemy import text

from .. import hook_ingest
from ..auth import CurrentDevice, current_uploader
from ..contract import SessionUpload
from ..db import db_session
from .sync import send_pending, store_payloads

router = APIRouter(prefix="/v1/ingest", tags=["ingest"])
log = logging.getLogger("builder.ingest")

#: The largest root transcript in the reference container is 10 MB; a whole-file resend
#: after a lost offset must fit, a runaway must not. 64 MB per request.
MAX_BYTES = 64 * 1024 * 1024
#: Raw bytes of a session that never finalised are dropped after a week: long enough for
#: a laptop that slept through the weekend, short enough that a forgotten key does not
#: accumulate someone's conversations forever.
RETENTION_DAYS = 7
#: How old a transcript's newest chunk must be before another session's hook re-cuts it,
#: so a session whose process was killed (no `SessionEnd`) still finishes. Two idle
#: thresholds: the idle rule itself decides final, this only bounds the wasted work.
STALE_RECUT_MINUTES = 30
_SAFE = re.compile(r"^[A-Za-z0-9._-]{1,200}$")

HOOK_SCRIPT = r"""#!/usr/bin/env bash
# Builder hook — ships the transcript Claude Code just wrote to your Builder server.
# Install once:
#   mkdir -p ~/.builder && curl -fsSL "$BUILDER_URL/v1/ingest/hook.sh" -o ~/.builder/hook.sh
# Needs BUILDER_URL and BUILDER_CAPTURE_KEY in the environment or in ~/.builder/env.
# Claude Code runs it on UserPromptSubmit / Stop / SessionEnd (docs/hooks-capture.md).
# It always exits 0: a broken upload must never block Claude Code.
set -u
[ -f "$HOME/.builder/env" ] && . "$HOME/.builder/env"
[ -n "${BUILDER_URL:-}" ] && [ -n "${BUILDER_CAPTURE_KEY:-}" ] || exit 0
input=$(cat)
field() {
  printf '%s' "$input" | sed -n "s/.*\"$1\":[[:space:]]*\"\([^\"]*\)\".*/\1/p" | head -n 1
}
sid=$(field session_id); path=$(field transcript_path)
hook=$(field hook_event_name)
[ -n "$sid" ] && [ -f "$path" ] || exit 0
proj=$(basename "$(dirname "$path")")
state="$HOME/.builder/offsets"; mkdir -p "$state"
off=0; [ -f "$state/$sid" ] && off=$(cat "$state/$sid" 2>/dev/null || echo 0)
case "$off" in ''|*[!0-9]*) off=0;; esac
size=$(wc -c < "$path" | tr -d ' '); [ "$size" -lt "$off" ] && off=0
z=$(date +%z); hh=${z:1:2}; mm=${z:3:2}
tz=$((10#$hh * 60 + 10#$mm))
[ "${z:0:1}" = "-" ] && tz=$((-tz))
resp=$(tail -c +$((off + 1)) "$path" | curl -fsS -X POST "$BUILDER_URL/v1/ingest/transcript" \
  -H "Authorization: Bearer $BUILDER_CAPTURE_KEY" -H "Content-Type: application/x-ndjson" \
  -H "X-Builder-Session-Id: $sid" -H "X-Builder-Project-Dir: $proj" -H "X-Builder-Offset: $off" \
  -H "X-Builder-Hook: ${hook:-Stop}" -H "X-Builder-Tz-Offset-Minutes: $tz" \
  --data-binary @-) || resp=$(curl -sS "$BUILDER_URL/v1/ingest/transcript/$sid/offset" \
  -H "Authorization: Bearer $BUILDER_CAPTURE_KEY" 2>/dev/null) || exit 0
next=$(printf '%s' "$resp" | sed -n 's/.*"next_offset":[[:space:]]*\([0-9]*\).*/\1/p' | head -n 1)
[ -n "$next" ] && printf '%s' "$next" > "$state/$sid"
exit 0
"""


@router.get("/hook.sh", response_class=PlainTextResponse)
def hook_script():
    """The script the settings.json entry runs. Served so install is one curl."""
    return HOOK_SCRIPT


def _end_offset(db, user_id: str, sid: str) -> int:
    return int(
        db.execute(
            text(
                "SELECT COALESCE(MAX(byte_offset + octet_length(bytes)), 0) "
                "FROM transcript_chunks WHERE user_id = :u AND native_session_id = :s"
            ),
            {"u": user_id, "s": sid},
        ).scalar()
        or 0
    )


def _raw(db, user_id: str, sid: str) -> bytes:
    rows = db.execute(
        text(
            "SELECT bytes FROM transcript_chunks WHERE user_id = :u AND native_session_id = :s "
            "ORDER BY byte_offset"
        ),
        {"u": user_id, "s": sid},
    ).all()
    return b"".join(bytes(r[0]) for r in rows)


def _drop(db, user_id: str, sid: str, from_offset: int = 0) -> None:
    db.execute(
        text(
            "DELETE FROM transcript_chunks WHERE user_id = :u AND native_session_id = :s "
            "AND byte_offset >= :o"
        ),
        {"u": user_id, "s": sid, "o": from_offset},
    )


def _cut_and_store(
    db,
    device: CurrentDevice,
    sid: str,
    project_dir: str,
    raw: bytes,
    tz_offset_minutes: int,
    finalize: bool,
) -> dict:
    """Sessionize one transcript's bytes and upsert the sessions through the batch path.
    Deletes the raw chunks once every session in them is final."""
    if not raw:
        return {"accepted": 0, "unchanged": 0, "rejected": [], "live": 0, "final": 0}
    payloads = hook_ingest.payloads_for(
        raw,
        native_session_id=sid,
        project_dir=project_dir,
        tz_offset_minutes=tz_offset_minutes,
        finalize=finalize,
        device_id=str(device.device_id),
    )
    uploads: list[SessionUpload] = []
    rejected: list[dict] = []
    for p in payloads:
        try:
            uploads.append(SessionUpload(**p))
        except ValidationError as e:
            rejected.append(
                {"client_session_id": p.get("client_session_id"), "reason": str(e)[:200]}
            )
    accepted, unchanged, rej2, pending = store_payloads(db, device, uploads)
    live = sum(1 for u in uploads if u.state == "live")
    final = len(uploads) - live
    if live == 0 and (uploads or finalize) and raw.endswith(b"\n"):
        # Retire the conversation, keep the OFFSET: a zero-length chunk at the end so a
        # later tail (the person comes back to the same transcript after an idle gap —
        # a new session by definition) still lands at the byte the script expects, and
        # only the new records are cut. Not while the last line is half-written: its
        # first bytes would be lost and the rest would arrive as a malformed line.
        _drop(db, str(device.user_id), sid)
        db.execute(
            text(
                "INSERT INTO transcript_chunks (user_id, device_id, native_session_id, "
                "project_dir, byte_offset, bytes, hook) VALUES (:u, :d, :s, :p, :o, :b, 'retired')"
            ),
            {
                "u": str(device.user_id),
                "d": str(device.device_id),
                "s": sid,
                "p": project_dir,
                "o": len(raw),
                "b": b"",
            },
        )
    return {
        "accepted": accepted,
        "unchanged": unchanged,
        "rejected": rejected + rej2,
        "live": live,
        "final": final,
        "pending": pending,
    }


@router.post("/transcript")
async def ingest_transcript(
    request: Request,
    device: CurrentDevice = Depends(current_uploader),
    x_builder_session_id: str = Header(...),
    x_builder_project_dir: str = Header(...),
    x_builder_offset: int = Header(0),
    x_builder_hook: str = Header("Stop"),
    x_builder_tz_offset_minutes: int = Header(0),
    content_encoding: str | None = Header(None),
):
    """Append a transcript tail at `X-Builder-Offset`, then cut and store its sessions.

    Offsets: equal to what the server holds → append; less → the client has the file and
    its view wins, everything from that offset is replaced; greater → 409 with
    `next_offset`, and the script resends from there. The response always carries
    `next_offset`, which the script stores for the next hook.
    """
    sid, pdir = x_builder_session_id, x_builder_project_dir
    if not _SAFE.match(sid) or not _SAFE.match(pdir):
        raise HTTPException(422, "session id and project dir must be [A-Za-z0-9._-]")
    if x_builder_offset < 0:
        raise HTTPException(422, "offset must be >= 0")
    body = await request.body()
    if content_encoding == "gzip":
        try:
            body = gzip.decompress(body)
        except (OSError, EOFError, zlib.error) as e:
            raise HTTPException(400, "body is not gzip") from e
    if len(body) > MAX_BYTES:
        raise HTTPException(413, f"at most {MAX_BYTES} bytes per request")

    user_id = str(device.user_id)
    finalize = x_builder_hook == "SessionEnd"
    with db_session(viewer_id=user_id) as db:
        end = _end_offset(db, user_id, sid)
        if x_builder_offset > end:
            return JSONResponse(
                {"next_offset": end, "reason": "gap: resend from next_offset"}, status_code=409
            )
        if x_builder_offset <= end:
            # Below the end: the client has the file and its view wins, everything from
            # here is replaced. AT the end: this clears only a retired zero-length chunk
            # sitting exactly there (a real chunk starts below the end it contributes to).
            _drop(db, user_id, sid, x_builder_offset)
        if body:
            db.execute(
                text(
                    "INSERT INTO transcript_chunks (user_id, device_id, native_session_id, "
                    "project_dir, byte_offset, bytes, hook) VALUES (:u, :d, :s, :p, :o, :b, :h)"
                ),
                {
                    "u": user_id,
                    "d": str(device.device_id),
                    "s": sid,
                    "p": pdir,
                    "o": x_builder_offset,
                    "b": body,
                    "h": x_builder_hook[:32],
                },
            )
        raw = _raw(db, user_id, sid)
        next_offset = len(raw)
        result = _cut_and_store(db, device, sid, pdir, raw, x_builder_tz_offset_minutes, finalize)
        pending = list(result.pop("pending", []))

        # Sessions whose process died without a SessionEnd: re-cut the stale ones so the
        # idle rule can finish them, then the retention sweep.
        stale = db.execute(
            text(
                "SELECT native_session_id, project_dir, MAX(received_at) AS last "
                "FROM transcript_chunks WHERE user_id = :u AND native_session_id != :s "
                "GROUP BY native_session_id, project_dir "
                "HAVING MAX(received_at) < now() - make_interval(mins => :m) LIMIT 5"
            ),
            {"u": user_id, "s": sid, "m": STALE_RECUT_MINUTES},
        ).all()
        recut = 0
        for row in stale:
            r = _cut_and_store(
                db,
                device,
                row[0],
                row[1],
                _raw(db, user_id, row[0]),
                x_builder_tz_offset_minutes,
                False,
            )
            pending.extend(r.get("pending", []))
            recut += 1
        db.execute(
            text(
                "DELETE FROM transcript_chunks WHERE user_id = :u "
                "AND received_at < now() - make_interval(days => :d)"
            ),
            {"u": user_id, "d": RETENTION_DAYS},
        )
        db.execute(
            text("UPDATE devices SET last_seen_at = now() WHERE id = :d"),
            {"d": str(device.device_id)},
        )

    send_pending(device, pending)
    return {"next_offset": next_offset, "recut_stale": recut, **result}


@router.get("/transcript/{native_session_id}/offset")
def transcript_offset(native_session_id: str, device: CurrentDevice = Depends(current_uploader)):
    """What the server holds for this session, so a script that lost its offset file (or
    got a 409) can resend from the right byte. 0 once the raw bytes were retired."""
    with db_session(viewer_id=str(device.user_id)) as db:
        return {"next_offset": _end_offset(db, str(device.user_id), native_session_id)}


@router.delete("/transcript/{native_session_id}", status_code=204)
def delete_transcript(native_session_id: str, device: CurrentDevice = Depends(current_uploader)):
    """Drop the raw bytes now. The sessions already cut from them stay."""
    with db_session(viewer_id=str(device.user_id)) as db:
        _drop(db, str(device.user_id), native_session_id)
    return None
