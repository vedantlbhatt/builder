import base64
import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text

from .. import notify
from ..auth import CurrentDevice, current_device
from ..contract import SessionUpload
from ..db import db_session
from ..strip import COLUMNS, non_idle_seconds
from . import push

router = APIRouter(prefix="/v1/sync", tags=["sync"])
log = logging.getLogger("builder.sync")

#: Mirrors `Tuning.notableMinActiveSec` (1200 s). The Mac's rule is
#: `unattended = presence_count == 0 && active_seconds >= notableMinActiveSec`; below the
#: floor nothing downstream reads the flag, so the server only pins it above it.
NOTABLE_MIN_ACTIVE_SEC = 1200

#: A live snapshot younger than this is exempt from the strip-vs-active tolerance. The
#: strip has 1024 columns over the session span, so at 300 s that is 0.3 s per column and
#: a session that has done one thing so far is mostly one bucket; the 25% tolerance is
#: calibrated for finished sessions and rejects nearly every honest first snapshot.
LIVE_STRIP_GRACE_SEC = 300


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

    # `still_running` IS the live upload (docs/session-boundaries.md). A final session
    # that says it is still running, or a live one that claims an end, is a client that
    # set one of the two fields and forgot the other.
    if (p.state == "live") != (p.end_reason == "still_running"):
        return f"state={p.state!r} does not agree with end_reason={p.end_reason!r}"

    # The two clocks must sum to the headline. They are computed from the same gap walk
    # on the client, so anything beyond a rounding second means one of them was taken
    # from a different event set than the other.
    split = p.attended_seconds + p.autonomous_seconds
    if abs(split - p.active_seconds) > 1:
        return (
            f"attended_seconds {p.attended_seconds} + autonomous_seconds "
            f"{p.autonomous_seconds} = {split}, but active_seconds is {p.active_seconds}"
        )

    # `unattended` is derived, not observed, and the server can check the derivation.
    # Zero presence over a notable span with unattended=false is the 5h40m robot about to
    # become a personal record again; unattended=true with a presence signal is a real
    # sitting being denied its notification.
    if p.presence_count == 0 and p.active_seconds >= NOTABLE_MIN_ACTIVE_SEC and not p.unattended:
        return (
            f"presence_count is 0 over {p.active_seconds}s of active time but unattended is false"
        )
    if p.presence_count > 0 and p.unattended:
        return f"unattended is true with presence_count {p.presence_count}"

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
    young_live = p.state == "live" and p.active_seconds < LIVE_STRIP_GRACE_SEC
    if (
        not young_live
        and p.active_seconds > 0
        and abs(strip_active - p.active_seconds) > 0.25 * p.active_seconds
    ):
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

    Completion pushes are decided and RECORDED inside the transaction (notify.plan) and
    sent only after it commits. The order is the point: a push failure — APNs down, a
    bad token, a bug in the HTTP client — can never roll back an upload, and a request
    that dies between commit and send loses a banner rather than storing a session
    twice. Failures are logged, not raised; the Mac's sync must not retry a whole batch
    because a phone was unreachable.
    """
    if len(body.sessions) > 250:
        raise HTTPException(413, "at most 250 sessions per batch")

    accepted = 0
    unchanged = 0
    rejected: list[dict] = []
    pending: list[notify.PendingPush] = []

    with db_session(viewer_id=str(device.user_id)) as db:
        for p in body.sessions:
            if (reason := sanity_gate(p)) is not None:
                rejected.append({"client_session_id": p.client_session_id, "reason": reason})
                continue

            # `state` rides along so the notification decision can tell a live row
            # becoming final (news) from a final row being refreshed (not news).
            existing = db.execute(
                text(
                    "SELECT id, content_hash, state FROM sessions "
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
            _upsert_analysis(db, session_id, p)
            if (push_plan := notify.plan(db, session_id, p, existing)) is not None:
                pending.append(push_plan)
            accepted += 1

        db.execute(
            text("UPDATE devices SET last_seen_at = now() WHERE id = :d"),
            {"d": str(device.device_id)},
        )

    # Committed. Everything below is best-effort. Called through the module attribute
    # rather than an imported name so a test can replace it at `push.send_session_finished`.
    for n in pending:
        try:
            push.send_session_finished(
                str(device.user_id), n.title, n.body, n.session_id, unattended=n.unattended
            )
        except Exception:
            log.exception("push for session %s (%s) failed; not retried", n.session_id, n.kind)

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
    """Insert or replace in place.

    The local day is the Mac's day, not the calendar's. `Tuning.dayBoundaryHour = 4`:
    a session that starts at 01:30 local belongs to the evening before, because that is
    how the person will describe it and because a late night landing alone on a new date
    manufactures a streak break. The server used to take the plain calendar date, so any
    session started between midnight and four disagreed with the menu bar about which day
    it was — three definitions of "day" is exactly what the Tuning comment warns against.
    `local_date` and `local_dow` both carry the four-hour shift; `local_hour` does NOT,
    because it is a clock reading (a 01:30 start is hour 1, not hour 21) and shifting it
    would store a plausible wrong number that no consumer would ever question.

    ON CONFLICT refreshes everything a later upload can legitimately change. A `live`
    snapshot becomes `final` in place; a `day_boundary` cut moves `started_at` to 04:00,
    and with it the local date; a re-sync from a re-paired Mac moves `device_id`.
    `harness` is not in the list — a session cannot change which tool wrote it.
    """
    row = db.execute(
        text(
            """
            INSERT INTO sessions (
              user_id, device_id, client_session_id, content_hash,
              sessionizer_version, active_calc_version, harness, repo_id,
              started_at, ended_at, active_seconds, idle_seconds, tz_offset_minutes,
              attended_seconds, autonomous_seconds, presence_count, end_reason,
              local_date, local_hour, local_dow,
              state, visible, notable, unattended, time_quality, timeline_fidelity,
              title, title_source, agent_observed_at
            ) VALUES (
              :user_id, :device_id, :csid, :chash,
              :sv, :acv, :harness, :repo_id,
              :started, :ended, :active, :idle, :tz,
              :attended, :autonomous, :presence, :end_reason,
              -- local wall clock, then the 4 am day boundary (see the docstring)
              ((:started AT TIME ZONE 'UTC' + make_interval(mins => :tz))
                 - interval '4 hours')::date,
              EXTRACT(hour FROM
                (:started AT TIME ZONE 'UTC' + make_interval(mins => :tz)))::smallint,
              EXTRACT(isodow FROM
                ((:started AT TIME ZONE 'UTC' + make_interval(mins => :tz))
                   - interval '4 hours'))::smallint - 1,
              :state, :visible, :notable, :unattended, :tq, :fidelity,
              :title, :title_source, :observed
            )
            ON CONFLICT (user_id, client_session_id) DO UPDATE SET
              content_hash = EXCLUDED.content_hash,
              device_id = EXCLUDED.device_id,
              sessionizer_version = EXCLUDED.sessionizer_version,
              active_calc_version = EXCLUDED.active_calc_version,
              repo_id = EXCLUDED.repo_id,
              started_at = EXCLUDED.started_at,
              ended_at = EXCLUDED.ended_at,
              active_seconds = EXCLUDED.active_seconds,
              idle_seconds = EXCLUDED.idle_seconds,
              tz_offset_minutes = EXCLUDED.tz_offset_minutes,
              attended_seconds = EXCLUDED.attended_seconds,
              autonomous_seconds = EXCLUDED.autonomous_seconds,
              presence_count = EXCLUDED.presence_count,
              end_reason = EXCLUDED.end_reason,
              local_date = EXCLUDED.local_date,
              local_hour = EXCLUDED.local_hour,
              local_dow = EXCLUDED.local_dow,
              state = EXCLUDED.state,
              visible = EXCLUDED.visible,
              notable = EXCLUDED.notable,
              unattended = EXCLUDED.unattended,
              time_quality = EXCLUDED.time_quality,
              timeline_fidelity = EXCLUDED.timeline_fidelity,
              title = EXCLUDED.title,
              title_source = EXCLUDED.title_source,
              agent_observed_at = EXCLUDED.agent_observed_at,
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
            "attended": p.attended_seconds,
            "autonomous": p.autonomous_seconds,
            "presence": p.presence_count,
            "end_reason": p.end_reason,
            "state": p.state,
            "visible": p.visible,
            "notable": p.notable,
            # From the payload, checked against presence_count by the gate. v1 hardcoded
            # False here, which is how an autonomous run could become a record.
            "unattended": p.unattended,
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


def _upsert_analysis(db, session_id, p: SessionUpload):
    """Store the model-written analysis, if the payload carries one.

    A payload WITHOUT an analysis leaves any stored one alone. The field is opt-in and
    omitted from the wire when off, so its absence means "nothing to say this time" — an
    agent whose user turned analysis upload off does not retract what it already sent by
    resyncing, and a live checkpoint that arrived with an analysis is not wiped by the next
    60-second snapshot that did not run one. Deletion is a separate, explicit action.
    """
    a = p.analysis
    if a is None:
        return
    db.execute(
        text(
            """
            INSERT INTO session_analysis (
              session_id, analysis_version, model, generated_at, digest_hash, body
            ) VALUES (
              :sid, :version, :model, :generated_at, :digest_hash, CAST(:body AS jsonb)
            )
            ON CONFLICT (session_id) DO UPDATE SET
              analysis_version = EXCLUDED.analysis_version,
              model = EXCLUDED.model,
              generated_at = EXCLUDED.generated_at,
              digest_hash = EXCLUDED.digest_hash,
              body = EXCLUDED.body,
              updated_at = now()
            """
        ),
        {
            "sid": session_id,
            "version": a.analysis_version,
            "model": a.model,
            "generated_at": a.generated_at,
            "digest_hash": a.digest_hash,
            # mode="json" so generated_at is an ISO string inside the document too, and
            # the phone reads the same bytes whichever path they arrive by.
            "body": json.dumps(a.model_dump(mode="json")),
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
