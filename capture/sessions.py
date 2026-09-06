"""Transcripts in, contract v2 payloads out.

The pipeline, and where each step's rules live:

1. **records** — `measure_boundaries.load_records` (the reference) reads each root
   transcript with its partial-line rule and `classify`; the `extra` hook carries the
   record uuid, `sessionId`, `message.id`, usage, model and `subtype` alongside, so tokens
   and identities come from the same parse as the boundaries.
2. **pooling** — by the transcript's LINEAGE: the project directory the file lives in,
   with every record of one native session id folded into that id's dominant directory
   (`measure_boundaries.fold_by_session_lineage`). NOT by the repository each record's
   `cwd` resolves to. Claude Code stamps the shell's current cwd on every record, so a
   conversation whose shell `cd`s between home and a repo scatters across two keys —
   MEASURED on the container corpus: 332 cwd runs in one 2,231-record session, all 15
   human prompts under `/home/user` and 833 assistant records under the repo — and pooled
   per record that one sitting uploaded as TWO overlapping sessions, one with the prompts
   and no commits, one with the commits and "0 prompts typed". The repository is an
   attribute of the session (the dominant resolvable cwd, `Session.repo`), never the
   partition key. Two conversations back to back in one directory are one sitting.
3. **sessionize** — `measure_boundaries.sessionize_pools`, unchanged: every pool sees the
   human session starts of the others (rule `switched_repo`), and the idle-gap threshold
   is fitted to the pool's presence intervals (`tau="auto"`, v3) or given. Output sessions
   cover the time-sorted pool contiguously, so each session's records are recovered by
   slicing at the cumulative `records` counts; an assertion checks the slices tile the pool.
4. **the open session** — the last session in a pool is `still_running` (or `cleared`,
   which is final where it stands). Younger than the tau in force it is LIVE (uploaded only with `--live`, or finalized with
   `--finalize` when the container is about to disappear); older, it ended on an idle gap
   nobody was there to observe, and it is finalized the way the reference finalizes an
   idle-gap cut: the boundary gap credited, capped, to whichever clock was running, and
   `ended_at` extended by the same amount so active never exceeds elapsed.
5. **counts** — `analysis.digest.load_claude_code_events` supplies tool calls, edit-tool
   line deltas, human edits and compactions; nothing here re-parses tool inputs.
6. **tokens** — deduped on `(source_id, message.id)`, first record in file order carries
   the usage, `<synthetic>` and sidechain records excluded (`TokenAccountant.ledger`).

**Every branch is treated as live.** The engine excludes records off the surviving DAG
branch from lines, tool counts and the strip, and reports their tokens as
`abandoned_branch_tokens`. Capture does not, and reports 0 there. MEASURED on a remote
transcript (harness 2.1.x) in which nothing was ever rewound: 51 fork points, of which 40
were a tool result and the NEXT assistant record both parented to the same `tool_use`
record and 11 were a `system`/assistant pair — the harness writes siblings without a
rewind. A single-chain walk from the newest leaf classed 35 of 599 assistant records, 14
tool calls and 18 of 216 authoritative usage records (8%) as abandoned work. On that
evidence the filter would subtract real work from ordinary sessions to catch rewinds that
are rare in this surface; the omission is the smaller lie, and it is labelled.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import pathlib
import time
from collections import Counter

from analysis import digest

from . import identity, repo, strip
from .discover import Transcript
from .reference import mb
from .tuning import (
    ACTIVE_CALC_VERSION,
    ACTIVE_GAP_CAP_SEC,
    COUNTED_MIN_ACTIVE_SEC,
    COUNTED_MIN_MEANINGFUL_EVENTS,
    NOTABLE_MIN_ACTIVE_SEC,
    REPO_PEPPER_VERSION,
    SESSIONIZER_VERSION,
    SYNTHETIC_MODEL_SENTINEL,
    TAU_AUTONOMOUS_SEC,
    TAU_COMMIT_ATTRIBUTION_SEC,
)

#: The tool names the Mac uploads counts for. Everything else is not on the wire.
UPLOADED_TOOLS = ("Read", "Edit", "Write", "Bash")

#: What the engine calls "meaningful": prompts, tool calls and human edits
#: (`EventKind.isMeaningful`), the count `Tuning.countedMinMeaningfulEvents` reads.
_MEANINGFUL_EV_KINDS = frozenset({"prompt", "tool", "human_edit"})


# ----------------------------------------------------------------------------- records


def _extra(r: dict, line: int) -> dict:
    """What capture needs per record beyond what the boundary rules read."""
    msg = r.get("message") if isinstance(r.get("message"), dict) else {}
    usage = msg.get("usage") if isinstance(msg.get("usage"), dict) else None
    model = msg.get("model") if isinstance(msg.get("model"), str) and msg.get("model") else None
    return {
        "line": line,
        "uuid": r.get("uuid") if isinstance(r.get("uuid"), str) else None,
        "session_id": r.get("sessionId") if isinstance(r.get("sessionId"), str) else None,
        "msg_id": (msg.get("id") or r.get("requestId")) if r.get("type") == "assistant" else None,
        "usage": usage if r.get("type") == "assistant" else None,
        "model": model,
        "subtype": r.get("subtype") if isinstance(r.get("subtype"), str) else None,
        "sidechain": bool(r.get("isSidechain")),
    }


@dataclasses.dataclass
class Source:
    transcript: Transcript
    source_id: str
    records: list[dict]
    events: list[digest.Ev]


def load_source(t: Transcript) -> Source:
    src_id = identity.source_id(t.descriptor)
    records = mb.load_records(t.path, extra=_extra)
    for r in records:
        r["source_id"] = src_id
        r["path"] = str(t.path)
    events = digest.load_claude_code_events(t.path)
    return Source(t, src_id, records, events)


# ----------------------------------------------------------------------------- pooling


def pool_key(project_dir: str) -> str:
    """The lineage key: harness and project directory. Never a cwd (module docstring)."""
    return f"claude_code|dir:{project_dir}"


def dominant_repo(records: list[dict]) -> repo.RepoIdentity | None:
    """The repository most of a session's resolvable records were stamped with — an
    attribute of the session, decided after the cut. Ties go to the earliest seen."""
    counts: Counter[str] = Counter()
    first: dict[str, repo.RepoIdentity] = {}
    for r in sorted(records, key=lambda r: r["ts"]):
        ident = repo.identity_for(r.get("cwd"))
        if ident is None:
            continue
        counts[ident.identity] += 1
        first.setdefault(ident.identity, ident)
    if not counts:
        return None
    best = max(counts.values())
    for key in first:  # insertion order == earliest seen
        if counts[key] == best:
            return first[key]
    return None


# ----------------------------------------------------------------------------- sessions


@dataclasses.dataclass
class Session:
    """One cut of one pool, with the records and digest events inside it."""

    pool: str
    repo: repo.RepoIdentity | None
    records: list[dict]  # time-sorted, as the reference sorted them
    events: list[digest.Ev]
    started_at: float
    ended_at: float
    attended: float
    autonomous: float
    prompts: int
    presence: int
    end_reason: str
    state: str  # "live" | "final"

    @property
    def last_record_ts(self) -> float:
        return self.records[-1]["ts"]

    @property
    def first_record(self) -> dict:
        return self.records[0]

    @property
    def client_session_id(self) -> str:
        first = self.first_record
        native = first.get("uuid") or f"l{first['line']}"
        return identity.client_session_id(identity.event_uid(first["source_id"], native))

    @property
    def transcript_path(self) -> pathlib.Path:
        """The file holding most of this session's records — what `--analyze` digests."""
        by_path = Counter(r["path"] for r in self.records)
        return pathlib.Path(by_path.most_common(1)[0][0])


def _finalize_open(s: dict, recs: list[dict], now: float) -> None:
    """Apply the reference's idle-gap credit to a `still_running` session.

    The gap from the last record to `now` is credited like any other gap — capped at
    `activeGapCapSec`, to whichever clock was running at the last record — and `ended_at`
    is extended by the same amount, exactly as `sessionize` does when a later record
    reveals the gap. Credit is a property of the pool, not of where the cut lands.
    """
    last_ts = recs[-1]["ts"]
    gap = max(0.0, now - last_ts)
    credit = min(gap, ACTIVE_GAP_CAP_SEC)
    last_presence = max((r["ts"] for r in recs if r["presence"]), default=None)
    autonomous = last_presence is None or (last_ts - last_presence) > TAU_AUTONOMOUS_SEC
    s["active"] += credit
    if autonomous:
        s["autonomous"] += credit
    else:
        s["attended"] += credit
    s["ended_at"] = last_ts + credit
    s["end_reason"] = "idle_gap"


def sessionize_sources(
    sources: list[Source],
    tz: dt.tzinfo,
    now: float | None = None,
    finalize_open: bool = False,
    tau: str | float = "auto",
    report: dict | None = None,
) -> list[Session]:
    """Cut every pool with the reference and attach records and events to each session.

    `tau` is `"auto"` (fit to the presence intervals of every pool together — the v3 rule,
    falling back to 900 s below 200 intervals or when the fit is not bimodal), or seconds.
    `report`, when given, receives `tau` and the `TauFit` so the CLI can print it.
    """
    now = time.time() if now is None else now
    keyed: list[tuple[str, dict]] = []
    events_by_source: dict[str, list[digest.Ev]] = {}
    for src in sources:
        events_by_source[src.source_id] = src.events
        key = pool_key(src.transcript.project_dir)
        keyed.extend((key, r) for r in src.records)
    pools = mb.fold_by_session_lineage(keyed)

    tau_value, fit = mb.resolve_tau(tau, pools)
    if report is not None:
        report["tau"] = tau_value
        report["fit"] = fit
    cuts = mb.sessionize_pools(pools, tz, tau=tau_value)

    out: list[Session] = []
    for key, recs in pools.items():
        cut = cuts[key]
        ordered = sorted(recs, key=lambda r: r["ts"])  # the reference's own sort, stable
        assert sum(s["records"] for s in cut) == len(ordered), "sessions must tile the pool"
        src_ids = {r["source_id"] for r in recs}
        pool_events = sorted(
            (e for sid in src_ids for e in events_by_source[sid]), key=lambda e: e.ts
        )
        offset = 0
        for i, s in enumerate(cut):
            members = ordered[offset : offset + s["records"]]
            offset += s["records"]
            state = "final"
            if s["end_reason"] == "still_running":
                age = now - members[-1]["ts"]
                if age < tau_value and not finalize_open:
                    state = "live"
                else:
                    _finalize_open(s, members, now)
            lo, hi = members[0]["ts"], members[-1]["ts"]
            evs = [e for e in pool_events if lo <= e.ts <= hi]
            out.append(
                Session(
                    pool=key,
                    repo=dominant_repo(members),
                    records=members,
                    events=evs,
                    started_at=s["started_at"],
                    ended_at=s["ended_at"],
                    attended=s["attended"],
                    autonomous=s["autonomous"],
                    prompts=s["prompts"],
                    presence=s["presence"],
                    end_reason=s["end_reason"],
                    state=state,
                )
            )
    out.sort(key=lambda s: s.started_at)
    return out


# ----------------------------------------------------------------------------- tokens


@dataclasses.dataclass
class Ledger:
    reported: bool
    buckets: dict[str, int] | None
    models: list[dict]
    coverage: str


def token_ledger(records: list[dict]) -> Ledger:
    """`TokenAccountant.ledger`: first record per `(source, message.id)` in file order is
    authoritative; sidechain and `<synthetic>` records never contribute."""
    seen: set[tuple[str, str]] = set()
    b = {"input": 0, "output": 0, "cache_read": 0, "cache_w5m": 0, "cache_w1h": 0}
    out_by_model: Counter[str] = Counter()
    saw = False
    for r in sorted(records, key=lambda r: (r["source_id"], r["line"])):
        u = r.get("usage")
        mid = r.get("msg_id")
        if not u or not mid or r.get("sidechain") or r.get("model") == SYNTHETIC_MODEL_SENTINEL:
            continue
        key = (r["source_id"], mid)
        if key in seen:
            continue
        seen.add(key)
        saw = True
        cc = u.get("cache_creation") if isinstance(u.get("cache_creation"), dict) else {}
        w5 = cc.get("ephemeral_5m_input_tokens")
        if w5 is None:
            w5 = u.get("cache_creation_input_tokens")
        one = {
            "input": _int(u.get("input_tokens")),
            "output": _int(u.get("output_tokens")),
            "cache_read": _int(u.get("cache_read_input_tokens")),
            "cache_w5m": _int(w5),
            "cache_w1h": _int(cc.get("ephemeral_1h_input_tokens")),
        }
        for k in b:
            b[k] += one[k]
        if r.get("model"):
            out_by_model[r["model"]] += one["output"]
    if not saw:
        return Ledger(False, None, [], "partial")
    total = max(sum(out_by_model.values()), 1)
    models = [
        {"model_id": m, "output_token_share": round(n / total, 4)}
        for m, n in out_by_model.most_common()
    ]
    return Ledger(True, b, models, "complete")


def _int(v) -> int:
    return v if isinstance(v, int) and not isinstance(v, bool) else 0


# ----------------------------------------------------------------------------- attribution


def attribution(agent_added: int, git_ins: int, git_commits: int, human_edits: int):
    """`TokenAccountant.attribution`: a LOWER BOUND bucket, never a percentage."""
    if git_commits <= 0 or git_ins <= 0:
        if agent_added == 0:
            return "unknown", "none"
        return ("almost_all_agent" if human_edits == 0 else "nine_in_ten"), "low"
    ratio = agent_added / max(git_ins, 1)
    confidence = "medium" if agent_added > git_ins else "high"
    if ratio < 0.5:
        bucket = "mostly_you"
    elif ratio < 0.75:
        bucket = "about_half"
    elif ratio < 0.9:
        bucket = "three_in_four"
    elif ratio < 0.97:
        bucket = "nine_in_ten"
    else:
        bucket = "almost_all_agent"
    return bucket, confidence


# ----------------------------------------------------------------------------- payload


def _iso(ts: float) -> str:
    return (
        dt.datetime.fromtimestamp(ts, dt.UTC)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def build_payload(
    s: Session,
    tz: dt.tzinfo,
    machine_id: str,
    client_version: str,
    observed_at: float | None = None,
    analysis: dict | None = None,
) -> dict:
    """Exactly the contract v2 fields, anonymous mode: no `repo_name`, `title` or
    `title_source` — which repositories are public is a Mac-side setting.

    The two clocks are rounded the same way and the headline is their SUM, never rounded
    independently: the server rejects `attended + autonomous != active` beyond a second.
    """
    observed_at = time.time() if observed_at is None else observed_at
    attended = round(s.attended)
    autonomous = round(s.autonomous)
    active = attended + autonomous
    wall = s.ended_at - s.started_at

    tools = [e for e in s.events if e.kind == "tool"]
    tool_counts = Counter(e.tool for e in tools if e.tool in UPLOADED_TOOLS)
    meaningful = sum(1 for e in s.events if e.kind in _MEANINGFUL_EV_KINDS)
    human_edits = sum(1 for e in s.events if e.kind == "human_edit")
    # Every event that carries a line count, exactly as `TokenAccountant.agentLines`
    # sums them: Edit's structuredPatch, Write's created content, AND a Bash heredoc's
    # body (`ShellFileEffect` on the Swift side, `_bash_file_effect` here). Restricting
    # this to the edit tools is what made the same session read "+0 lines" on the card
    # while the analyst was told "+2450" from the digest, which reads heredocs.
    # MEASURED on this repository's container corpus, 17 root transcripts, 2026-09-06:
    # 2,452 of 2,458 attributable lines came through the shell.
    added = sum(e.added or 0 for e in s.events)
    removed = sum(e.removed or 0 for e in s.events)
    # `Set(evs.compactMap(\.targetPath))` on the Mac: any event naming a file touched it,
    # a shell write and a human edit included.
    touched = {e.path for e in s.events if e.path}

    counted = active >= COUNTED_MIN_ACTIVE_SEC or meaningful >= COUNTED_MIN_MEANINGFUL_EVENTS
    unattended = s.presence == 0 and active >= NOTABLE_MIN_ACTIVE_SEC
    notable = counted and attended >= NOTABLE_MIN_ACTIVE_SEC and not unattended

    git = repo.WindowStats()
    if s.repo is not None:
        git = repo.window_stats(
            s.repo.common_root, s.started_at - TAU_COMMIT_ATTRIBUTION_SEC, s.ended_at
        )
    bucket, confidence = attribution(added, git.insertions, git.commits, human_edits)

    ledger = token_ledger(s.records)
    cols, marks = strip.build(s.records, s.started_at, s.ended_at)

    tz_offset = dt.datetime.fromtimestamp(s.started_at, tz).utcoffset() or dt.timedelta(0)

    p: dict = {
        "client_session_id": s.client_session_id,
        "machine_id": machine_id,
        "content_hash": "",  # filled below
        "client_version": client_version,
        "sessionizer_version": SESSIONIZER_VERSION,
        "active_calc_version": ACTIVE_CALC_VERSION,
        "harness": "claude_code",
        "agent_observed_at": _iso(observed_at),
        "client_clock_offset_ms": 0,
        "started_at": _iso(s.started_at),
        "ended_at": _iso(s.ended_at),
        "active_seconds": active,
        "idle_seconds": max(0, int(wall - active)),
        "tz_offset_minutes": int(tz_offset.total_seconds() // 60),
        "time_quality": "ok",
        "state": s.state,
        "end_reason": s.end_reason,
        "attended_seconds": attended,
        "autonomous_seconds": autonomous,
        "presence_count": s.presence,
        "unattended": unattended,
        "visible": counted,
        "notable": notable,
        "strip_columns": strip.encode_columns(cols),
        "strip_marks": marks,
        "timeline_fidelity": "full",
        "human_prompt_count": s.prompts,
        "prompt_count_basis": "typed_promptsource",
        "tool_calls": dict(tool_counts),
        "files_touched": len(touched),
        # The engine uploads 0 here today (SessionDeriver writes `n_files_created = 0`);
        # capture matches it rather than introduce a number the Mac never sends.
        "files_created": 0,
        "lines_added_agent": added,
        "lines_removed_agent": removed,
        "commit_count": git.commits,
        "commit_insertions": git.insertions,
        "commit_deletions": git.deletions,
        "human_edit_events": human_edits,
        "agent_line_bucket": bucket,
        "attrib_confidence": confidence,
        "tokens_reported": ledger.reported,
        # Every branch is counted as live (module doc); nothing is reported as abandoned.
        "abandoned_branch_tokens": 0,
        "token_dedupe": "message_id",
        "token_scope": "parent_aggregated",
        "token_coverage": ledger.coverage,
        "models": ledger.models,
        "model_state": "known",
        "repo_pepper_version": REPO_PEPPER_VERSION,
        "repo_id_basis": s.repo.basis if s.repo else "origin",
    }
    if ledger.reported:
        p["tokens"] = ledger.buckets
    if s.repo is not None:
        p["repo_hash"] = s.repo.hash
    if analysis is not None:
        p["analysis"] = analysis
    p["content_hash"] = content_hash(p)
    return p


#: Fields that change on every run without the session changing. Excluded from the hash so
#: an unchanged session is `unchanged` on the server and skipped by `/v1/sync/known`.
_VOLATILE = frozenset({"content_hash", "agent_observed_at", "client_clock_offset_ms"})


def content_hash(payload: dict) -> str:
    """sha256 over the canonical JSON of everything that can change.

    The whole payload, not a hand-picked list of fields, and the Mac does the same
    (`SyncCommand`: `Hashing.sha256Hex(encoder().encode(upload))`). It has to: a field
    left off such a list can never make a session look changed, so it would come back
    `unchanged` forever and never reach the phone. The analysis is the field that would
    have been forgotten first, since it arrives on a LATER run than the session it
    describes.
    """
    import json

    body = {k: v for k, v in payload.items() if k not in _VOLATILE}
    return identity.sha256_hex(json.dumps(body, sort_keys=True, separators=(",", ":")))
