import base64

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text

from ..auth import CurrentDevice, current_device
from ..db import db_session

router = APIRouter(prefix="/v1", tags=["sessions"])


def _row_to_session(r) -> dict:
    return {
        "id": str(r.id),
        "client_session_id": r.client_session_id,
        "harness": r.harness,
        "repo_name": r.public_name,
        "started_at": r.started_at.isoformat(),
        "ended_at": r.ended_at.isoformat(),
        "active_seconds": r.active_seconds,
        "idle_seconds": r.idle_seconds,
        "local_date": r.local_date.isoformat(),
        "title": r.title,
        "title_source": r.title_source,
        "notable": r.notable,
        "unattended": r.unattended,
        "timeline_fidelity": r.timeline_fidelity,
        "is_shared": r.is_shared,
    }


@router.get("/sessions")
def list_sessions(
    device: CurrentDevice = Depends(current_device),
    limit: int = Query(50, le=200),
    before: str | None = None,
    notable_only: bool = True,
):
    """Reverse-chronological, keyset-paginated.

    Keyset rather than OFFSET: the phone scrolls while the Mac is still syncing, and an
    offset-paginated list under concurrent insertion silently skips and repeats rows.
    """
    clauses = ["user_id = :u"]
    params: dict = {"u": str(device.user_id), "limit": limit}
    if notable_only:
        clauses.append("notable")
    if before:
        clauses.append("started_at < :before")
        params["before"] = before

    with db_session(viewer_id=str(device.user_id)) as db:
        rows = db.execute(
            text(
                f"""
                SELECT s.*, r.public_name
                FROM sessions s LEFT JOIN repos r ON r.id = s.repo_id
                WHERE {" AND ".join(clauses)}
                ORDER BY started_at DESC LIMIT :limit
                """
            ),
            params,
        ).all()

    return {
        "sessions": [_row_to_session(r) for r in rows],
        "next_before": rows[-1].started_at.isoformat() if len(rows) == limit else None,
    }


@router.get("/sessions/{session_id}")
def get_session(session_id: str, device: CurrentDevice = Depends(current_device)):
    with db_session(viewer_id=str(device.user_id)) as db:
        row = db.execute(
            text(
                """
                SELECT s.*, r.public_name
                FROM sessions s LEFT JOIN repos r ON r.id = s.repo_id
                WHERE s.id = :id
                """
            ),
            {"id": session_id},
        ).first()
        if row is None:
            raise HTTPException(404, "not found")

        strip = db.execute(
            text("SELECT cols, marks, t0_ms, t1_ms FROM session_strips WHERE session_id = :id"),
            {"id": session_id},
        ).first()
        stats = db.execute(
            text("SELECT * FROM session_stats WHERE session_id = :id"), {"id": session_id}
        ).first()

    out = _row_to_session(row)
    if strip:
        out["strip"] = {
            # base64 on the wire: the phone decodes it with the generated TypeScript
            # decoder, which reads the same 2-bit layout as the Swift one.
            "cols": base64.b64encode(strip.cols).decode(),
            "marks": strip.marks,
            "t0_ms": strip.t0_ms,
            "t1_ms": strip.t1_ms,
        }
    if stats:
        out["stats"] = {
            "tokens_reported": stats.tokens_reported,
            "tok_in": stats.tok_in,
            "tok_out": stats.tok_out,
            "tok_cache_read": stats.tok_cache_read,
            "tok_cache_w5m": stats.tok_cache_w5m,
            "tok_cache_w1h": stats.tok_cache_w1h,
            "models": stats.models,
            "model_state": stats.model_state,
            "human_prompt_count": stats.human_prompt_count,
            "prompt_count_basis": stats.prompt_count_basis,
            "files_touched": stats.files_touched,
            "lines_added_agent": stats.lines_added_agent,
            "commit_count": stats.commit_count,
            "agent_line_bucket": stats.agent_line_bucket,
            "attrib_confidence": stats.attrib_confidence,
        }
    return out


@router.get("/profile")
def profile(device: CurrentDevice = Depends(current_device), days: int = Query(119, le=400)):
    """Everything the profile tab needs, in one request.

    One round trip rather than four: the phone renders this screen on cold launch over a
    cellular connection, and four sequential requests is four chances to show a spinner.
    """
    with db_session(viewer_id=str(device.user_id)) as db:
        graph = db.execute(
            text(
                """
                SELECT local_date, SUM(active_seconds) AS secs
                FROM sessions
                WHERE user_id = :u AND visible
                  AND local_date > (CURRENT_DATE - make_interval(days => :days))
                GROUP BY local_date ORDER BY local_date
                """
            ),
            {"u": str(device.user_id), "days": days},
        ).all()

        totals = db.execute(
            text(
                """
                SELECT COUNT(*) AS n, COALESCE(SUM(active_seconds), 0) AS secs
                FROM sessions WHERE user_id = :u AND visible
                """
            ),
            {"u": str(device.user_id)},
        ).one()

        longest = db.execute(
            text(
                """
                SELECT id, active_seconds, started_at FROM sessions
                WHERE user_id = :u AND notable AND NOT unattended
                ORDER BY active_seconds DESC LIMIT 1
                """
            ),
            {"u": str(device.user_id)},
        ).first()

        arcs = db.execute(
            text(
                """
                SELECT r.public_name, r.repo_hash, COUNT(*) AS n,
                       SUM(s.active_seconds) AS secs,
                       MIN(s.started_at) AS first_at, MAX(s.started_at) AS last_at
                FROM sessions s JOIN repos r ON r.id = s.repo_id
                WHERE s.user_id = :u AND s.visible
                GROUP BY r.public_name, r.repo_hash
                ORDER BY secs DESC LIMIT 20
                """
            ),
            {"u": str(device.user_id)},
        ).all()

        attribution = db.execute(
            text(
                """
                SELECT COALESCE(SUM(st.lines_added_agent), 0) AS agent_lines,
                       COALESCE(SUM(st.human_edit_events), 0) AS human_edits,
                       COALESCE(SUM(st.human_prompt_count), 0) AS prompts
                FROM session_stats st JOIN sessions s ON s.id = st.session_id
                WHERE s.user_id = :u
                """
            ),
            {"u": str(device.user_id)},
        ).one()

    return {
        "graph": [{"date": g.local_date.isoformat(), "active_seconds": int(g.secs)} for g in graph],
        "totals": {"sessions": totals.n, "active_seconds": int(totals.secs)},
        "longest_session": (
            {
                "id": str(longest.id),
                "active_seconds": longest.active_seconds,
                "started_at": longest.started_at.isoformat(),
            }
            if longest
            else None
        ),
        "projects": [
            {
                "name": a.public_name,
                # Anonymous repos are identified only by a prefix of their hash, which is
                # enough to group sessions in the UI and carries no name.
                "key": a.repo_hash[:12],
                "sessions": a.n,
                "active_seconds": int(a.secs),
                "first_at": a.first_at.isoformat(),
                "last_at": a.last_at.isoformat(),
            }
            for a in arcs
        ],
        # Three separately measured numbers, deliberately not combined into a percentage.
        "attribution": {
            "agent_lines": int(attribution.agent_lines),
            "human_edit_events": int(attribution.human_edits),
            "prompts": int(attribution.prompts),
        },
    }
