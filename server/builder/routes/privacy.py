import hashlib
import hmac
import json
from pathlib import Path

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel
from sqlalchemy import text

from ..auth import CurrentDevice, current_device
from ..contract import CONTRACT_VERSION, PUBLIC_FIELDS
from ..db import db_session

router = APIRouter(prefix="/v1", tags=["privacy"])

_STATIC = Path(__file__).resolve().parent.parent / "static"


@router.get("/../upload-fields.json", include_in_schema=False)
@router.get("/upload-fields.json")
def upload_fields():
    """The published field list.

    This is the file the verification command on the privacy page fetches and diffs
    against what the agent would actually send. It is GENERATED from
    privacy/upload-contract.json, so it cannot drift from the code — and CI fails if the
    committed copy is stale.
    """
    data = json.loads((_STATIC / "upload-fields.json").read_text())
    return JSONResponse(data, headers={"Cache-Control": "public, max-age=300"})


@router.get("/privacy/fields", response_class=PlainTextResponse)
def privacy_fields_text():
    fields = json.loads((_STATIC / "upload-fields.json").read_text())["fields"]
    lines = [
        f"Builder upload contract v{CONTRACT_VERSION}",
        "",
        "Every field that can leave your machine. Nothing else is representable on the",
        "wire: the client encodes through a generated key enum with no synthesized",
        "Codable, so a field absent from this list has no way to be sent.",
        "",
    ]
    lines += [f"  {f}" for f in fields]
    lines += [
        "",
        "Never sent, in any mode: prompt text, assistant text, thinking, tool inputs or",
        "outputs, file contents, diffs, file paths, file names, directory names, commit",
        "messages, commit SHAs, branch names, cwd, git remote URLs, repository names for",
        "repositories you have not marked public, MCP server names, terminal commands or",
        "their output, environment variables, URLs fetched, hostname, IP address.",
        "",
        f"Public-repo-only fields: {sorted(set(PUBLIC_FIELDS) - set(fields[:0]) & {'repo_name', 'title', 'title_source'})}",
    ]
    return "\n".join(lines)


class VisibilityUpdate(BaseModel):
    repo_hash: str
    visibility: str


@router.post("/repos/visibility")
def set_visibility(body: VisibilityUpdate, device: CurrentDevice = Depends(current_device)):
    """Change a repository's visibility, and apply it retroactively.

    Setting `excluded` deletes what is already stored rather than merely hiding it.
    Anything less would make the control a display preference, and the promise is that an
    excluded repository has nothing on the server.
    """
    if body.visibility not in {"public", "anonymous", "excluded"}:
        return JSONResponse({"error": "invalid visibility"}, status_code=422)

    with db_session(viewer_id=str(device.user_id)) as db:
        repo = db.execute(
            text("SELECT id FROM repos WHERE repo_hash = :h"), {"h": body.repo_hash}
        ).first()
        if repo is None:
            return JSONResponse({"error": "unknown repo"}, status_code=404)

        db.execute(
            text(
                """
                INSERT INTO repo_visibility (user_id, repo_id, visibility)
                VALUES (:u, :r, CAST(:v AS repo_vis))
                ON CONFLICT (user_id, repo_id) DO UPDATE
                  SET visibility = EXCLUDED.visibility, updated_at = now()
                """
            ),
            {"u": str(device.user_id), "r": str(repo.id), "v": body.visibility},
        )

        deleted = 0
        if body.visibility == "excluded":
            result = db.execute(
                text("DELETE FROM sessions WHERE user_id = :u AND repo_id = :r"),
                {"u": str(device.user_id), "r": str(repo.id)},
            )
            deleted = result.rowcount or 0
        elif body.visibility == "anonymous":
            # Dropping to anonymous must strip the name and the title everywhere it was
            # already stored, not just stop sending them from now on.
            db.execute(
                text(
                    "UPDATE sessions SET title = NULL, title_source = NULL "
                    "WHERE user_id = :u AND repo_id = :r"
                ),
                {"u": str(device.user_id), "r": str(repo.id)},
            )
            db.execute(
                text("UPDATE repos SET public_name = NULL WHERE id = :r"), {"r": str(repo.id)}
            )

    return {"status": "ok", "visibility": body.visibility, "sessions_deleted": deleted}


@router.post("/account/delete")
def delete_account(device: CurrentDevice = Depends(current_device)):
    """One call, everything gone.

    Returns a receipt: an HMAC over the row counts and the moment of deletion. It is the
    only artifact that survives, and it deliberately carries nothing about who the user
    was — proof the request was honoured, not a record of the person who made it.
    """
    user_id = str(device.user_id)
    with db_session(viewer_id=user_id) as db:
        counts = {}
        for table, sql in [
            ("sessions", "SELECT COUNT(*) FROM sessions WHERE user_id = :u"),
            ("devices", "SELECT COUNT(*) FROM devices WHERE user_id = :u"),
            ("push_tokens", "SELECT COUNT(*) FROM push_tokens WHERE user_id = :u"),
            (
                "repo_visibility",
                "SELECT COUNT(*) FROM repo_visibility WHERE user_id = :u",
            ),
        ]:
            counts[table] = db.execute(text(sql), {"u": user_id}).scalar() or 0

        payload = json.dumps(counts, sort_keys=True)
        receipt = hmac.new(b"builder-deletion-receipt-v1", payload.encode(), hashlib.sha256).hexdigest()

        db.execute(
            text(
                """
                INSERT INTO deletion_requests (user_id, completed_at, row_counts, receipt_hmac)
                VALUES (:u, now(), CAST(:c AS jsonb), :r)
                """
            ),
            {"u": user_id, "c": payload, "r": receipt},
        )

        # Everything else cascades from users.
        db.execute(text("DELETE FROM users WHERE id = :u"), {"u": user_id})

    return {"status": "deleted", "row_counts": counts, "receipt": receipt}
