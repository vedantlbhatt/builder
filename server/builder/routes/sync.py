import base64
import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text

from ..auth import CurrentDevice, current_device
from ..contract import SessionUpload
from ..db import db_session
from ..strip import COLUMNS, non_idle_seconds

router = APIRouter(prefix="/v1/sync", tags=["sync"])


class BatchRequest(BaseModel):
    sessions: list[SessionUpload]


class BatchResponse(BaseModel):
    accepted: int
    unchanged: int
    rejected: list[dict]


def sanity_gate(p: SessionUpload) -> str | None:
    """Reject payloads that are internally inconsistent.

    Every one of these catches a specific client bug that would otherwise arrive as
    plausible data and be believed forever. The server cannot recompute these numbers —
    it never sees a transcript — so consistency between them is the only leverage it has.
    """
    span = (p.ended_at - p.started_at).total_seconds()

    # Active time cannot exceed elapsed. The client extends `ended_at` by the trailing
    # gap credit precisely so this holds; a violation means its arithmetic regressed.
    if p.active_seconds > span + 1:
        return f"active_seconds {p.active_seconds} exceeds span {span:.0f}"

    # MEASURED ratio is roughly 16 tool calls per typed prompt. More prompts than tool
    # calls means the prompt filter broke — most likely counting every `type: "user"`
    # record, which inflates by ~13x.
    total_tools = sum(p.tool_calls.values()) if p.tool_calls else 0
    if p.human_prompt_count > max(total_tools, 0) and total_tools > 0:
        return f"human_prompt_count {p.human_prompt_count} exceeds tool_calls {total_tools}"

    # The 1.878x content-block overcount, arriving without a deduplication basis.
    if p.token_dedupe == "none" and p.tokens_reported:
        return "tokens reported with token_dedupe='none'"

    # Tokens must be absent rather than zero when the harness does not report them.
    # Cursor writes {0,0} locally; a stored 0 would read as "used no tokens".
    if not p.tokens_reported and p.tokens is not None:
        return "tokens present but tokens_reported is false"

    # The strip must agree with the session it describes. A strip built from a different
    # event set is the single hardest client bug to notice by eye.
    try:
        cols = base64.b64decode(p.strip_columns, validate=True)
    except Exception:
        return "strip_columns is not valid base64"
    if len(cols) != COLUMNS:
        return f"strip_columns is {len(cols)} bytes, expected {COLUMNS}"
    if any(b >> 4 for b in cols):
        return "strip_columns has non-zero reserved bits"

    strip_active = non_idle_seconds(cols, span)
    if p.active_seconds > 0 and abs(strip_active - p.active_seconds) > 0.25 * p.active_seconds:
        return (
            f"strip disagrees with active_seconds: strip says {strip_active:.0f}, "
            f"payload says {p.active_seconds}"
        )

    # Titles and repo names are public-repo-only fields. Their mere presence on an
    # anonymous session means the client's mode allowlist was not applied.
    if p.repo_name is None and p.title is not None:
        return "title present without repo_name (anonymous sessions carry neither)"

    return None


@router.post("/sessions:batch", response_model=BatchResponse)
def upload_batch(body: BatchRequest, device: CurrentDevice = Depends(current_device)):
    """Idempotent bulk upsert.

    Sized for a first-run backfill in a handful of requests — the whole corpus on the
    reference machine is 557 sessions, roughly three chunks — and for a single session
    arriving fifteen minutes after work stops.

    `content_hash` makes a repeat free: re-uploading an unchanged session touches nothing
    and reports `unchanged`, so a client that loses its sync state and replays everything
    costs bandwidth rather than correctness.
    """
    if len(body.sessions) > 250:
        raise HTTPException(413, "at most 250 sessions per batch")

    accepted = 0
    unchanged = 0
    rejected: list[dict] = []

    with db_session(viewer_id=str(device.user_id)) as db:
        for p in body.sessions:
            if (reason := sanity_gate(p)) is not None:
                rejected.append({"client_session_id": p.client_session_id, "reason": reason})
                continue

            existing = db.execute(
                text(
                    "SELECT id, content_hash FROM sessions "
                    "WHERE user_id = :u AND client_session_id = :c"
                ),
                {"u": str(device.user_id), "c": p.client_session_id},
            ).first()

            if existing and existing.content_hash == p.content_hash:
                unchanged += 1
                continue

            repo_id = _upsert_repo(db, p)
            session_id = _upsert_session(db, device, p, repo_id, existing)
            _upsert_strip(db, session_id, p)
            _upsert_stats(db, session_id, p)
            accepted += 1

        db.execute(
            text("UPDATE devices SET last_seen_at = now() WHERE id = :d"),
            {"d": str(device.device_id)},
        )

    return BatchResponse(accepted=accepted, unchanged=unchanged, rejected=rejected)


def _upsert_repo(db, p: SessionUpload):
    if not p.repo_hash:
        return None
    row = db.execute(
        text(
            """
            INSERT INTO repos (repo_hash, pepper_version, repo_id_basis, public_name)
            VALUES (:h, :pv, :basis, :name)
            ON CONFLICT (repo_hash) DO UPDATE
              SET public_name = COALESCE(EXCLUDED.public_name, repos.public_name)
            RETURNING id
            """
        ),
        {
            "h": p.repo_hash,
            "pv": p.repo_pepper_version,
            "basis": p.repo_id_basis,
            "name": p.repo_name,
        },
    ).one()
    return row.id


def _upsert_session(db, device: CurrentDevice, p: SessionUpload, repo_id, existing):
    row = db.execute(
        text(
            """
            INSERT INTO sessions (
              user_id, device_id, client_session_id, content_hash,
              sessionizer_version, active_calc_version, harness, repo_id,
              started_at, ended_at, active_seconds, idle_seconds, tz_offset_minutes,
              local_date, local_hour, local_dow,
              state, visible, notable, unattended, time_quality, timeline_fidelity,
              title, title_source, agent_observed_at
            ) VALUES (
              :user_id, :device_id, :csid, :chash,
              :sv, :acv, :harness, :repo_id,
              :started, :ended, :active, :idle, :tz,
              (:started AT TIME ZONE 'UTC' + make_interval(mins => :tz))::date,
              EXTRACT(hour FROM (:started AT TIME ZONE 'UTC' + make_interval(mins => :tz)))::smallint,
              EXTRACT(isodow FROM (:started AT TIME ZONE 'UTC' + make_interval(mins => :tz)))::smallint - 1,
              :state, :visible, :notable, :unattended, :tq, :fidelity,
              :title, :title_source, :observed
            )
            ON CONFLICT (user_id, client_session_id) DO UPDATE SET
              content_hash = EXCLUDED.content_hash,
              ended_at = EXCLUDED.ended_at,
              active_seconds = EXCLUDED.active_seconds,
              idle_seconds = EXCLUDED.idle_seconds,
              notable = EXCLUDED.notable,
              unattended = EXCLUDED.unattended,
              visible = EXCLUDED.visible,
              title = EXCLUDED.title,
              title_source = EXCLUDED.title_source,
              timeline_fidelity = EXCLUDED.timeline_fidelity,
              repo_id = EXCLUDED.repo_id,
              updated_at = now()
            RETURNING id
            """
        ),
        {
            "user_id": str(device.user_id),
            "device_id": str(device.device_id),
            "csid": p.client_session_id,
            "chash": p.content_hash,
            "sv": p.sessionizer_version,
            "acv": p.active_calc_version,
            "harness": p.harness,
            "repo_id": repo_id,
            "started": p.started_at,
            "ended": p.ended_at,
            "active": p.active_seconds,
            "idle": p.idle_seconds,
            "tz": p.tz_offset_minutes,
            "state": p.state,
            "visible": p.visible,
            "notable": p.notable,
            "unattended": False,
            "tq": p.time_quality,
            "fidelity": p.timeline_fidelity,
            "title": p.title,
            "title_source": p.title_source,
            "observed": p.agent_observed_at,
        },
    ).one()
    return row.id


def _upsert_strip(db, session_id, p: SessionUpload):
    db.execute(
        text(
            """
            INSERT INTO session_strips (session_id, spec_version, t0_ms, t1_ms, cols, marks)
            VALUES (:sid, 1, :t0, :t1, :cols, CAST(:marks AS jsonb))
            ON CONFLICT (session_id) DO UPDATE SET
              t0_ms = EXCLUDED.t0_ms, t1_ms = EXCLUDED.t1_ms,
              cols = EXCLUDED.cols, marks = EXCLUDED.marks
            """
        ),
        {
            "sid": session_id,
            "t0": int(p.started_at.timestamp() * 1000),
            "t1": int(p.ended_at.timestamp() * 1000),
            "cols": base64.b64decode(p.strip_columns),
            "marks": json.dumps([[m.ms, m.k] for m in p.strip_marks]),
        },
    )


def _upsert_stats(db, session_id, p: SessionUpload):
    t = p.tokens
    db.execute(
        text(
            """
            INSERT INTO session_stats (
              session_id, tokens_reported, tok_in, tok_out, tok_cache_read,
              tok_cache_w5m, tok_cache_w1h, abandoned_branch_tokens,
              token_dedupe, token_scope, token_coverage, models, model_state, tool_calls,
              human_prompt_count, prompt_count_basis, files_touched, files_created,
              lines_added_agent, lines_removed_agent, commit_count, commit_insertions,
              commit_deletions, human_edit_events, agent_line_bucket, attrib_confidence
            ) VALUES (
              :sid, :reported, :tin, :tout, :tcr, :tw5, :tw1, :abandoned,
              :dedupe, :scope, :coverage, CAST(:models AS jsonb), :model_state,
              CAST(:tools AS jsonb),
              :prompts, :basis, :files, :created, :added, :removed,
              :commits, :ins, :del, :human_edits, :bucket, :confidence
            )
            ON CONFLICT (session_id) DO UPDATE SET
              tokens_reported = EXCLUDED.tokens_reported,
              tok_in = EXCLUDED.tok_in, tok_out = EXCLUDED.tok_out,
              tok_cache_read = EXCLUDED.tok_cache_read,
              tok_cache_w5m = EXCLUDED.tok_cache_w5m, tok_cache_w1h = EXCLUDED.tok_cache_w1h,
              abandoned_branch_tokens = EXCLUDED.abandoned_branch_tokens,
              token_dedupe = EXCLUDED.token_dedupe, token_scope = EXCLUDED.token_scope,
              token_coverage = EXCLUDED.token_coverage, models = EXCLUDED.models,
              model_state = EXCLUDED.model_state, tool_calls = EXCLUDED.tool_calls,
              human_prompt_count = EXCLUDED.human_prompt_count,
              prompt_count_basis = EXCLUDED.prompt_count_basis,
              files_touched = EXCLUDED.files_touched, files_created = EXCLUDED.files_created,
              lines_added_agent = EXCLUDED.lines_added_agent,
              lines_removed_agent = EXCLUDED.lines_removed_agent,
              commit_count = EXCLUDED.commit_count,
              commit_insertions = EXCLUDED.commit_insertions,
              commit_deletions = EXCLUDED.commit_deletions,
              human_edit_events = EXCLUDED.human_edit_events,
              agent_line_bucket = EXCLUDED.agent_line_bucket,
              attrib_confidence = EXCLUDED.attrib_confidence
            """
        ),
        {
            "sid": session_id,
            "reported": p.tokens_reported,
            "tin": t.input if t else None,
            "tout": t.output if t else None,
            "tcr": t.cache_read if t else None,
            "tw5": t.cache_w5m if t else None,
            "tw1": t.cache_w1h if t else None,
            "abandoned": p.abandoned_branch_tokens,
            "dedupe": p.token_dedupe,
            "scope": p.token_scope,
            "coverage": p.token_coverage,
            "models": json.dumps([m.model_dump() for m in p.models]),
            "model_state": p.model_state,
            "tools": json.dumps(p.tool_calls),
            "prompts": p.human_prompt_count,
            "basis": p.prompt_count_basis,
            "files": p.files_touched,
            "created": p.files_created,
            "added": p.lines_added_agent,
            "removed": p.lines_removed_agent,
            "commits": p.commit_count,
            "ins": p.commit_insertions,
            "del": p.commit_deletions,
            "human_edits": p.human_edit_events,
            "bucket": p.agent_line_bucket,
            "confidence": p.attrib_confidence,
        },
    )


@router.get("/known")
def known_hashes(device: CurrentDevice = Depends(current_device)):
    """Content hashes the server already has, so the client can skip unchanged sessions.

    Turns a replay of the whole history into one small request plus nothing.
    """
    with db_session(viewer_id=str(device.user_id)) as db:
        rows = db.execute(
            text("SELECT client_session_id, content_hash FROM sessions WHERE user_id = :u"),
            {"u": str(device.user_id)},
        ).all()
    return {"known": {r.client_session_id: r.content_hash for r in rows}}
