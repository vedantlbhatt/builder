import base64

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text

from ..auth import CurrentDevice, current_device
from ..builder_profile import DEFAULT_WINDOW_DAYS, MAX_WINDOW_DAYS, MIN_SESSIONS, builder_profile
from ..contract import ENUM_VALUES
from ..db import db_session

router = APIRouter(prefix="/v1", tags=["sessions"])

#: How many live snapshots a viewer can have at once, in practice. One Mac produces one
#: live session per open harness; ten is a Mac with every editor open, and the phone's
#: "right now" strip has room for about three before it scrolls.
LIVE_LIMIT = 10


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
        # The two clocks (docs/session-boundaries.md). Records read attended, never active.
        "attended_seconds": r.attended_seconds,
        "autonomous_seconds": r.autonomous_seconds,
        "presence_count": r.presence_count,
        "state": r.state,
        "end_reason": r.end_reason,
        "local_date": r.local_date.isoformat(),
        "title": r.title,
        "title_source": r.title_source,
        "notable": r.notable,
        "unattended": r.unattended,
        "timeline_fidelity": r.timeline_fidelity,
        "is_shared": r.is_shared,
        # The VIEWER'S OWN post for this session, or null. Every query feeding this mapper
        # LEFT JOINs posts on `p.session_id = s.id AND p.user_id = <viewer>`, so a stranger
        # reading a shared session through `sessions_public` gets null even for a public
        # post they could otherwise see: the phone uses this key to offer "edit / unshare",
        # and a post it may only look at is reached from the feed, which already carries
        # its id. `posts.session_id` is UNIQUE, so the join can never multiply rows.
        "post_id": str(r.post_id) if r.post_id else None,
    }


def _live_rows(db, user_id: str) -> list[dict]:
    """The viewer's live snapshots, newest update first.

    Shared by `/sessions/live` and `/profile`, so the phone's cold-start request and its
    pull-to-refresh agree on what "right now" means. `updated_at` is included because a
    live row's `ended_at` is the last record the Mac had seen, and the phone wants to say
    "as of 40 s ago" rather than pretend the snapshot is the present.
    """
    rows = db.execute(
        text(
            """
            SELECT s.*, r.public_name, p.id AS post_id
            FROM sessions s
            LEFT JOIN repos r ON r.id = s.repo_id
            LEFT JOIN posts p ON p.session_id = s.id AND p.user_id = CAST(:u AS uuid)
            WHERE s.user_id = :u AND s.state = 'live'
            ORDER BY s.updated_at DESC LIMIT :limit
            """
        ),
        {"u": user_id, "limit": LIVE_LIMIT},
    ).all()
    return [{**_row_to_session(r), "updated_at": r.updated_at.isoformat()} for r in rows]


@router.get("/sessions")
def list_sessions(
    device: CurrentDevice = Depends(current_device),
    limit: int = Query(50, le=200),
    before: str | None = None,
    notable_only: bool = True,
    state: str = Query("final"),
    include_live: bool = False,
):
    """Reverse-chronological, keyset-paginated.

    Keyset rather than OFFSET: the phone scrolls while the Mac is still syncing, and an
    offset-paginated list under concurrent insertion silently skips and repeats rows.

    Final only by default. A live row is a moving target — its `started_at` can shift on a
    day-boundary cut and its `ended_at` moves every minute — which is the wrong thing to
    paginate over; `include_live` folds them in for the one screen that wants both.
    """
    if state not in ENUM_VALUES["state"]:
        raise HTTPException(422, f"state must be one of {ENUM_VALUES['state']}")

    clauses = ["s.user_id = :u"]
    params: dict = {"u": str(device.user_id), "limit": limit, "state": state}
    if include_live:
        clauses.append("(s.state = CAST(:state AS sess_state) OR s.state = 'live')")
    else:
        clauses.append("s.state = CAST(:state AS sess_state)")
    if notable_only:
        clauses.append("s.notable")
    if before:
        clauses.append("s.started_at < :before")
        params["before"] = before

    with db_session(viewer_id=str(device.user_id)) as db:
        rows = db.execute(
            text(
                f"""
                SELECT s.*, r.public_name, p.id AS post_id
                FROM sessions s
                LEFT JOIN repos r ON r.id = s.repo_id
                LEFT JOIN posts p ON p.session_id = s.id AND p.user_id = CAST(:u AS uuid)
                WHERE {" AND ".join(clauses)}
                ORDER BY s.started_at DESC LIMIT :limit
                """
            ),
            params,
        ).all()

    return {
        "sessions": [_row_to_session(r) for r in rows],
        "next_before": rows[-1].started_at.isoformat() if len(rows) == limit else None,
    }


# Declared BEFORE `/sessions/{session_id}`: FastAPI matches routes in order, and a path
# parameter would otherwise swallow the literal "live" and hand it to a uuid comparison.
@router.get("/sessions/live")
def live_sessions(device: CurrentDevice = Depends(current_device)):
    """What the Mac is doing right now, per open session."""
    with db_session(viewer_id=str(device.user_id)) as db:
        live = _live_rows(db, str(device.user_id))
    return {"sessions": live}


@router.get("/sessions/{session_id}")
def get_session(session_id: str, device: CurrentDevice = Depends(current_device)):
    uid = str(device.user_id)
    with db_session(viewer_id=uid) as db:
        # The posts join is filtered to the VIEWER's post, not the session owner's. RLS on
        # `posts` would already hide a private post from a stranger, but relying on it
        # would hand a follower or a public reader the id of someone else's post under a
        # key the phone treats as "mine"; the explicit predicate makes the meaning of the
        # field independent of how the policies happen to be written.
        row = db.execute(
            text(
                """
                SELECT s.*, r.public_name, p.id AS post_id
                FROM sessions s
                LEFT JOIN repos r ON r.id = s.repo_id
                LEFT JOIN posts p ON p.session_id = s.id AND p.user_id = CAST(:u AS uuid)
                WHERE s.id = :id
                """
            ),
            {"id": session_id, "u": uid},
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
        # Read under the same viewer as the session row, so RLS on session_analysis is what
        # decides whether a shared session's analysis travels with it.
        analysis = db.execute(
            text("SELECT body FROM session_analysis WHERE session_id = :id"), {"id": session_id}
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
    # Null, never absent: the phone distinguishes "no analysis for this session" from an
    # older server that does not know the key.
    out["analysis"] = analysis.body if analysis else None
    return out


#: `window_days` for the builder profile, shared by both routes below.
WindowDays = Query(DEFAULT_WINDOW_DAYS, ge=1, le=MAX_WINDOW_DAYS)


@router.get("/profile")
def profile(
    device: CurrentDevice = Depends(current_device),
    days: int = Query(119, le=400),
    window_days: int = WindowDays,
):
    """Everything the profile tab needs, in one request.

    One round trip rather than four: the phone renders this screen on cold launch over a
    cellular connection, and four sequential requests is four chances to show a spinner.

    Live rows are returned separately under `live` and excluded from every aggregate. A
    live session's numbers move every minute; folding them into the graph and the totals
    would make the profile disagree with itself between two pulls, and the phone adds
    today's live minutes to today's cell on its own.

    `builder_profile` is the aggregate of the session analyses (builder_profile.py): null
    until three analysed sessions exist, because docs/analysis.md forbids reading an
    archetype off one run. `/profile/builder` serves it alone for a refresh.
    """
    uid = str(device.user_id)
    with db_session(viewer_id=uid) as db:
        graph = db.execute(
            text(
                """
                SELECT local_date, SUM(active_seconds) AS secs
                FROM sessions
                WHERE user_id = :u AND visible AND state <> 'live'
                  AND local_date > (CURRENT_DATE - make_interval(days => :days))
                GROUP BY local_date ORDER BY local_date
                """
            ),
            {"u": uid, "days": days},
        ).all()

        totals = db.execute(
            text(
                """
                SELECT COUNT(*) AS n, COALESCE(SUM(active_seconds), 0) AS secs
                FROM sessions WHERE user_id = :u AND visible AND state <> 'live'
                """
            ),
            {"u": uid},
        ).one()

        # Records rank by ATTENDED time (docs/session-boundaries.md). Ordered by active,
        # the winner in the reference corpus was a 5h40m autonomous run with zero typed
        # prompts; ordered by attended, a kickoff prompt plus eight robot hours scores
        # its attended minutes. Backed by sessions_record_idx from 0006.
        longest = db.execute(
            text(
                """
                SELECT id, attended_seconds, active_seconds, started_at FROM sessions
                WHERE user_id = :u AND notable AND NOT unattended AND state <> 'live'
                ORDER BY attended_seconds DESC LIMIT 1
                """
            ),
            {"u": uid},
        ).first()

        arcs = db.execute(
            text(
                """
                SELECT r.public_name, r.repo_hash, COUNT(*) AS n,
                       SUM(s.active_seconds) AS secs,
                       MIN(s.started_at) AS first_at, MAX(s.started_at) AS last_at
                FROM sessions s JOIN repos r ON r.id = s.repo_id
                WHERE s.user_id = :u AND s.visible AND s.state <> 'live'
                GROUP BY r.public_name, r.repo_hash
                ORDER BY secs DESC LIMIT 20
                """
            ),
            {"u": uid},
        ).all()

        attribution = db.execute(
            text(
                """
                SELECT COALESCE(SUM(st.lines_added_agent), 0) AS agent_lines,
                       COALESCE(SUM(st.human_edit_events), 0) AS human_edits,
                       COALESCE(SUM(st.human_prompt_count), 0) AS prompts,
                       COALESCE(SUM(s.attended_seconds), 0) AS attended,
                       COALESCE(SUM(s.autonomous_seconds), 0) AS autonomous
                FROM session_stats st JOIN sessions s ON s.id = st.session_id
                WHERE s.user_id = :u AND s.state <> 'live'
                """
            ),
            {"u": uid},
        ).one()

        live = _live_rows(db, uid)
        builder, _analysed = builder_profile(db, uid, window_days)

    return {
        "graph": [{"date": g.local_date.isoformat(), "active_seconds": int(g.secs)} for g in graph],
        "totals": {"sessions": totals.n, "active_seconds": int(totals.secs)},
        "longest_session": (
            {
                "id": str(longest.id),
                "attended_seconds": longest.attended_seconds,
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
        # Five separately measured numbers, deliberately not combined into a percentage.
        # attended + autonomous is the split of the hours total, not a sixth statistic.
        "attribution": {
            "agent_lines": int(attribution.agent_lines),
            "human_edit_events": int(attribution.human_edits),
            "prompts": int(attribution.prompts),
            "attended_seconds": int(attribution.attended),
            "autonomous_seconds": int(attribution.autonomous),
        },
        "live": live,
        "builder_profile": builder,
    }


@router.get("/profile/builder")
def profile_builder(device: CurrentDevice = Depends(current_device), window_days: int = WindowDays):
    """The builder profile alone, so the phone can refresh it without the whole tab.

    The count and the minimum travel beside the (possibly null) profile so the screen can
    say "2 of 3 sessions analysed" rather than show an empty card: null alone cannot
    distinguish "not enough yet" from "nothing was ever analysed".
    """
    uid = str(device.user_id)
    with db_session(viewer_id=uid) as db:
        builder, analysed = builder_profile(db, uid, window_days)
    return {
        "builder_profile": builder,
        "sessions_analysed": analysed,
        "min_sessions": MIN_SESSIONS,
        "window_days": window_days,
    }
