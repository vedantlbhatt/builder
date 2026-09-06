"""The builder profile: a whole corpus of sessions, read as numbers.

The per-session analysis is one model's reading of one digest. This module is the
opposite: pure arithmetic over every session a person has, no model anywhere, so the
same corpus always produces the same profile and every number can be recomputed by
hand from the transcripts.

THE RULE (CLAUDE.md): the failure mode is not a crash, it is a plausible wrong number.
So every metric here carries

  * `basis` — the data it was computed from, because the same name means different
    things on a machine that has the transcripts and on a server that has only the
    uploaded counts;
  * `n` — the sample it rests on;
  * `value: None` plus a `reason` whenever the sample is too small or the input is
    structurally absent. A metric is never estimated, defaulted or filled in.

Two definitions are unusual enough to state up front, both because the obvious version
is undefined on real data:

  * `planning_ratio` is prompts the agent answered with PROSE before touching a tool,
    over prompts it answered by going straight to a tool. The suggested form (two
    assistant turns before the first EDIT tool, over prompts followed immediately by an
    edit) is 15/0 on the container corpus: an undefined ratio, because that harness
    writes files through the shell (MEASURED: 1 Edit/Write call in 714 tool calls, and
    no prompt whose first agent action was a write). Measured at the first TOOL instead,
    the same corpus splits 20 prose-first against 8 tool-first over 28 prompts, a ratio
    of 2.5.
  * `code_velocity` is agent lines per ACTIVE hour and is a LOWER BOUND: only edit-tool
    line deltas and credited shell writes (heredoc, `sed -i`) are counted, and a corpus
    that writes another way reports None rather than 0. A 0 there would read as "you
    wrote nothing", which is a different and false claim. It also needs
    `MIN_WRITE_EVENTS_FOR_VELOCITY` writes behind it, or `MIN_LINES_FOR_VELOCITY` lines
    where the writes cannot be counted: MEASURED on the seven sessions in the proof
    database, where the stored rows carry 33 attributed lines over 4.4 hours, the rate
    reads 7.6 lines an hour, while reading the same seven sittings from the transcripts
    credits 2,300 lines through heredocs. 7.6 is not a small number, it is a wrong one.

`night_share` splits each session's active seconds across the clock in proportion to
where its elapsed span falls, rather than filing the whole session under its start
hour. A four-hour session that starts at 20:00 is two evening hours and two night ones.

Days are cut at 04:00 local (`DAY_BOUNDARY_HOUR`), the same boundary ingest, derivation
and the graph use. Three definitions of "day" would disagree about streaks in ways that
are impossible to explain (CLAUDE.md, "Today is not the calendar date").
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import math
import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence

PROFILE_VERSION = 1

#: Tuning.dayBoundaryHour. 04:00 local starts a new day, everywhere.
DAY_BOUNDARY_HOUR = 4

#: The night window, local. Closed at 22:00, open at 04:00; six of twenty-four hours, so
#: a person with no bias at all sits at 0.25 (that is the baseline below, not a guess).
NIGHT_START_HOUR = 22
NIGHT_END_HOUR = 4

#: A prompt under ten words is "short" — the brief's definition, kept verbatim so the
#: number means the same thing here and in the UI.
SHORT_PROMPT_WORDS = 10

# --------------------------------------------------------------------------- bases
#: What `tool_calls` was counted from. The server stores an ALLOWLISTED map (unknown and
#: MCP names bucket to `other`/`mcp_other`), so distinct-name diversity there would
#: undercount every corpus by construction and is refused rather than reported.
TOOLS_ALL = "all_tool_names"
TOOLS_ALLOWLIST = "allowlisted_tool_names"
TOOLS_ABSENT = "absent"

#: Test runs are matched against the same truncated command text as commits, so the count
#: is a lower bound and the basis says so rather than the name pretending otherwise.
TEST_RUNS_LOWER_BOUND = "test_commands_in_the_digest_lower_bound"

#: Where the commit count came from. `git log` over the session window is what the
#: uploader stores and what the server therefore has; counting `git commit` shell calls
#: in the digest events is a LOWER BOUND, because the digest truncates a command at 160
#: characters (MEASURED on the container corpus: 19 of 68 `git commit` calls survive that
#: cut, so a count taken from the event text is 3.5x low).
COMMITS_GIT_LOG = "git_log_window"
COMMITS_TOOL_CALLS = "git_commit_tool_calls_lower_bound"
COMMITS_ABSENT = "absent"

#: What agent lines were counted from. `uploaded_agent_lines` is what a stored session
#: carries: since `ShellFileEffect` landed, BOTH clients credit edit tools and shell
#: writes (`cat > f <<'EOF'`, `sed -i`) into one number, and the server cannot see which
#: tool produced them, so it also cannot count the writes behind them.
LINES_EDIT_AND_SHELL = "edit_tools_and_credited_shell_writes"
LINES_EDIT_ONLY = "edit_tools_only"
LINES_UPLOADED = "uploaded_agent_lines"
LINES_ABSENT = "absent"

# ------------------------------------------------------------------- sample floors
#: Below these a metric is None. They are judgement calls, not measurements, and are
#: written here rather than inline so that raising one is a single visible edit.
MIN_SESSIONS = 3
MIN_PROMPTS = 5
MIN_PROMPTS_FOR_RATIO = 8
MIN_ACTIVE_SEC_FOR_SHARES = 3600.0
MIN_ACTIVE_SEC_FOR_AUTONOMY = 1800.0
MIN_ACTIVE_SEC_FOR_VELOCITY = 1800.0
MIN_WRITE_EVENTS_FOR_VELOCITY = 10
#: Or this many attributed lines, when the writes behind them cannot be counted (the
#: server sees one line total and no tool names). UNMEASURED JUDGEMENT CALL, sitting an
#: order of magnitude above the artefact and an order below the real total: 33 lines over
#: 4.4 hours in the proof database was the artefact, 2,300 read from the same sittings'
#: transcripts was the work.
MIN_LINES_FOR_VELOCITY = 200
MIN_TOOL_CALLS_FOR_DIVERSITY = 5
MIN_SESSIONS_FOR_DIVERSITY = 3


# --------------------------------------------------------------------------- input
@dataclasses.dataclass(frozen=True)
class PromptFact:
    """One human prompt. `text` is present only where the transcript is."""

    ts: float
    text: str | None = None
    #: True when the agent's first act after this prompt was prose, False when it was a
    #: tool call, None when the surrounding events were not available.
    agent_opened_with_text: bool | None = None
    #: This prompt arrived directly after the human interrupted the agent.
    after_interrupt: bool = False


@dataclasses.dataclass(frozen=True)
class SessionFact:
    """One session, as much of it as the caller can honestly supply.

    Built from digest events by `session_fact_from_events` on a machine that has the
    transcripts, and from the stored rows by `server/builder/builder_profile.py`.
    """

    session_id: str
    started_at: float
    ended_at: float
    active_seconds: float
    attended_seconds: float
    autonomous_seconds: float
    tz_offset_minutes: int = 0
    prompt_count: int = 0
    interrupts: int | None = None
    tool_calls: Mapping[str, int] = dataclasses.field(default_factory=dict)
    tool_basis: str = TOOLS_ABSENT
    lines_added_agent: int = 0
    lines_basis: str = LINES_ABSENT
    #: How many tool calls actually WROTE a file (edit tools, plus credited shell writes
    #: where they are visible). A line rate over almost no writes is not a rate.
    write_events: int | None = None
    commit_count: int = 0
    commit_basis: str = COMMITS_ABSENT
    #: Wall-clock times of the commits, when they are known one by one. The stored strip
    #: marks are deduped for rendering, so the server passes None and the commit night
    #: share comes back None rather than wrong.
    commit_times: tuple[float, ...] | None = None
    output_tokens_by_model: Mapping[str, int] = dataclasses.field(default_factory=dict)
    prompts: tuple[PromptFact, ...] | None = None
    test_runs: int | None = None
    unattended: bool = False

    @property
    def local_day(self) -> dt.date:
        return local_day(self.started_at, self.tz_offset_minutes)


def local_day(ts: float, tz_offset_minutes: int) -> dt.date:
    """The local day a moment belongs to, with the day starting at 04:00."""
    local = dt.datetime.fromtimestamp(ts, dt.UTC) + dt.timedelta(minutes=tz_offset_minutes)
    return (local - dt.timedelta(hours=DAY_BOUNDARY_HOUR)).date()


# ------------------------------------------------------------------ corrective text
#: A prompt is CORRECTIVE when one of these appears in its first 25 words. The window is
#: bounded so that a 260-word brief which happens to contain "no" deep inside is not
#: flagged for it.
#:
#: MEASURED on the container corpus (29 typed prompts, every prompt hand-read): a first
#: list containing bare `\bnot\b` and bare `\bstop\b` flagged 14, of which 2 were plainly
#: not corrections — "keep working do not stop until you have tested it" and "whats done
#: and whats not". Both markers were dropped; `stop` survives only where it is not
#: preceded by "not". The list below flags 11 of those 29 (10 of the 28 in counted
#: sessions), and every one was checked by hand.
#: It under-counts (six prompts a human would call a redirect carry no marker, e.g.
#: "Yes there is man"), which is the safer direction for a number shown as "you steer
#: hard".
_CORRECTION_MARKERS: tuple[tuple[str, str], ...] = (
    ("negation", r"\b(no|nope|wrong|incorrect)\b"),
    ("negation_of_state", r"\bnot (what|working|done|right|tested|it)\b"),
    ("halt", r"(?<!not )\b(stop|nevermind|never mind|hold on)\b"),
    ("redo", r"\b(actually|instead|revert|undo|rather|go back)\b"),
    ("blame", r"\b(you|u) ?(said|didn'?t|did not|never|missed|broke|forgot|just|keep)\b"),
    ("i_meant", r"\bi (said|meant|mean)\b"),
    ("contraction", r"\b(doesn'?t|isn'?t|didn'?t|don'?t|dont)\b"),
    ("why", r"\bwhy (are|is|was|did|do|does|no|dont|don'?t|the)\b"),
    ("expectation", r"\bshould (be|work|have|already|just)\b"),
    ("exasperation", r"\b(wtf|what the (fuck|hell|heck|ruck))\b"),
    ("not_seeing", r"\b(do|are) (you|u) (not|even)\b"),
)

_CORRECTION_WINDOW_WORDS = 25
_CORRECTION_RE = tuple((name, re.compile(p, re.IGNORECASE)) for name, p in _CORRECTION_MARKERS)


def correction_markers(text: str) -> list[str]:
    """Which correction markers a prompt carries. Empty means not corrective."""
    head = " ".join((text or "").split()[:_CORRECTION_WINDOW_WORDS])
    return [name for name, rx in _CORRECTION_RE if rx.search(head)]


def is_corrective(text: str) -> bool:
    return bool(correction_markers(text))


# ---------------------------------------------------------- building facts from events
def session_fact_from_events(
    *,
    session_id: str,
    events: Sequence,
    started_at: float,
    ended_at: float,
    attended_seconds: float,
    autonomous_seconds: float,
    tz_offset_minutes: int,
    output_tokens_by_model: Mapping[str, int] | None = None,
    unattended: bool = False,
) -> SessionFact:
    """A `SessionFact` from one session's digest events (`analysis.digest.Ev`).

    Imported here rather than at module scope so that a caller with no transcripts (the
    server) can use this module without `analysis.digest` being importable at all.
    """
    from . import digest as dg

    prompts: list[PromptFact] = []
    tools: Counter[str] = Counter()
    lines = 0
    writes = 0
    commits = 0
    commit_times: list[float] = []
    tests = 0
    interrupts = 0

    for i, e in enumerate(events):
        if e.kind == "interrupt":
            interrupts += 1
            continue
        if e.kind == "tool":
            tools[e.tool or "unknown"] += 1
            lines += e.added or 0
            if e.tool in dg.EDIT_TOOLS or (e.tool in dg.SHELL_TOOLS and e.added is not None):
                writes += 1
            if e.tool in dg.COMMIT_TOOLS or (
                e.tool in dg.SHELL_TOOLS and re.search(r"\bgit commit\b", e.text or "")
            ):
                commits += 1
                commit_times.append(e.ts)
            if e.tool in dg.SHELL_TOOLS and re.search(
                r"\b(pytest|bun test|npm test|swift test|jest|cargo test|go test|make test)\b",
                e.text or "",
            ):
                tests += 1
            continue
        if e.kind != "prompt":
            continue
        opened_with_text: bool | None = None
        for later in events[i + 1 :]:
            if later.kind == "prompt":
                break
            if later.kind in ("assistant", "tool"):
                opened_with_text = later.kind == "assistant"
                break
        after_interrupt = i > 0 and events[i - 1].kind == "interrupt"
        prompts.append(
            PromptFact(
                ts=e.ts,
                text=e.text,
                agent_opened_with_text=opened_with_text,
                after_interrupt=after_interrupt,
            )
        )

    return SessionFact(
        session_id=session_id,
        started_at=started_at,
        ended_at=ended_at,
        active_seconds=attended_seconds + autonomous_seconds,
        attended_seconds=attended_seconds,
        autonomous_seconds=autonomous_seconds,
        tz_offset_minutes=tz_offset_minutes,
        prompt_count=len(prompts),
        interrupts=interrupts,
        tool_calls=dict(tools),
        tool_basis=TOOLS_ALL if tools else TOOLS_ABSENT,
        lines_added_agent=lines,
        lines_basis=LINES_EDIT_AND_SHELL,
        write_events=writes,
        commit_count=commits,
        commit_basis=COMMITS_TOOL_CALLS,
        commit_times=tuple(commit_times),
        output_tokens_by_model=dict(output_tokens_by_model or {}),
        prompts=tuple(prompts),
        test_runs=tests,
        unattended=unattended,
    )


# ------------------------------------------------------------------------- metrics
def _metric(value, unit: str, n: int, basis: str, reason: str | None = None, **extra) -> dict:
    return {"value": value, "unit": unit, "n": n, "basis": basis, "reason": reason, **extra}


def _round(x: float | None, digits: int) -> float | None:
    return None if x is None else round(x, digits)


def _median(xs: Sequence[float]) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    mid = len(s) // 2
    return float(s[mid]) if len(s) % 2 else (s[mid - 1] + s[mid]) / 2


def _active_by_hour(sessions: Sequence[SessionFact]) -> dict[int, float]:
    """Active seconds spread across local hours, in proportion to elapsed time.

    A session's active seconds are not stamped with the clock; only its span is. So the
    span is diced by hour and each hour gets active * (its share of the span). That is
    stated rather than hidden because it is the one approximation in this module.
    """
    out: dict[int, float] = dict.fromkeys(range(24), 0.0)
    for s in sessions:
        span = max(s.ended_at - s.started_at, 0.0)
        if span <= 0 or s.active_seconds <= 0:
            continue
        scale = s.active_seconds / span
        off = s.tz_offset_minutes * 60
        t = s.started_at
        while t < s.ended_at:
            local = t + off
            hour = int(local // 3600) % 24
            next_hour = (math.floor(local / 3600) + 1) * 3600 - off
            chunk_end = min(s.ended_at, next_hour)
            out[hour] += (chunk_end - t) * scale
            t = chunk_end
    return out


def corpus_profile(sessions: Iterable[SessionFact], *, now: float | None = None) -> dict:
    """Every corpus metric, each with its basis, its sample and its reason for absence."""
    ss = [s for s in sessions]
    ss.sort(key=lambda s: s.started_at)
    n_sessions = len(ss)

    active = sum(s.active_seconds for s in ss)
    attended = sum(s.attended_seconds for s in ss)
    autonomous = sum(s.autonomous_seconds for s in ss)
    active_hours = active / 3600.0

    all_prompts: list[PromptFact] = [p for s in ss if s.prompts for p in s.prompts]
    prompt_total = sum(s.prompt_count for s in ss)
    texts = [p.text for p in all_prompts if p.text]
    tool_total = sum(sum(s.tool_calls.values()) for s in ss)
    tool_known = [s for s in ss if s.tool_basis != TOOLS_ABSENT]
    lines_total = sum(s.lines_added_agent for s in ss)
    lines_known = [s for s in ss if s.lines_basis != LINES_ABSENT]
    commits_total = sum(s.commit_count for s in ss)

    totals = {
        "total_sessions": n_sessions,
        "total_hours": round(active_hours, 2),
        "total_prompts": prompt_total,
        "total_lines_added": lines_total,
        "total_commits": commits_total,
        "commit_basis": next(
            (x.commit_basis for x in ss if x.commit_basis != COMMITS_ABSENT), COMMITS_ABSENT
        ),
        "total_tool_calls": tool_total,
    }

    m: dict[str, dict] = {}

    # ---- prompt shape
    if len(texts) >= MIN_PROMPTS:
        chars = [len(t) for t in texts]
        words = [len(t.split()) for t in texts]
        short = sum(1 for w in words if w < SHORT_PROMPT_WORDS)
        m["avg_prompt_chars"] = _metric(
            round(sum(chars) / len(chars), 1), "chars", len(chars), "prompt_text"
        )
        m["median_prompt_chars"] = _metric(_median(chars), "chars", len(chars), "prompt_text")
        m["short_prompt_share"] = _metric(
            round(short / len(words), 3),
            "share",
            len(words),
            "prompt_text",
            note=f"under {SHORT_PROMPT_WORDS} words",
        )
    else:
        why = (
            "prompt text is not stored server side"
            if not texts
            else f"{len(texts)} prompts with text, {MIN_PROMPTS} needed"
        )
        for key in ("avg_prompt_chars", "median_prompt_chars", "short_prompt_share"):
            unit = "share" if key.endswith("share") else "chars"
            m[key] = _metric(None, unit, len(texts), "prompt_text", why)

    # ---- planning ratio
    classified = [p for p in all_prompts if p.agent_opened_with_text is not None]
    prose_first = sum(1 for p in classified if p.agent_opened_with_text)
    tool_first = len(classified) - prose_first
    if len(classified) >= MIN_PROMPTS_FOR_RATIO and tool_first > 0:
        m["planning_ratio"] = _metric(
            round(prose_first / tool_first, 2),
            "ratio",
            len(classified),
            "prose_before_first_tool",
            planning_prompts=prose_first,
            execution_prompts=tool_first,
        )
    else:
        m["planning_ratio"] = _metric(
            None,
            "ratio",
            len(classified),
            "prose_before_first_tool",
            (
                "no prompt went straight to a tool, so the ratio has a zero denominator"
                if classified and tool_first == 0
                else "what the agent did after each prompt is not stored server side"
                if not any(s.prompts for s in ss)
                else f"{len(classified)} classified prompts, {MIN_PROMPTS_FOR_RATIO} needed"
            ),
            planning_prompts=prose_first,
            execution_prompts=tool_first,
        )

    # ---- iteration depth
    if prompt_total >= MIN_PROMPTS and tool_known:
        m["iteration_depth"] = _metric(
            round(tool_total / prompt_total, 1),
            "tool calls per prompt",
            prompt_total,
            tool_known[0].tool_basis,
        )
    else:
        m["iteration_depth"] = _metric(
            None,
            "tool calls per prompt",
            prompt_total,
            TOOLS_ABSENT if not tool_known else tool_known[0].tool_basis,
            "no tool counts" if not tool_known else f"{prompt_total} prompts, {MIN_PROMPTS} needed",
        )

    # ---- autonomy
    if active >= MIN_ACTIVE_SEC_FOR_AUTONOMY and (attended + autonomous) > 0:
        m["autonomy_score"] = _metric(
            round(autonomous / (attended + autonomous), 3), "share", n_sessions, "two_clocks"
        )
    else:
        m["autonomy_score"] = _metric(
            None,
            "share",
            n_sessions,
            "two_clocks",
            f"{round(active)}s of active time, {round(MIN_ACTIVE_SEC_FOR_AUTONOMY)}s needed",
        )

    # ---- steer rate
    interrupts_known = [s for s in ss if s.interrupts is not None]
    interrupts = sum(s.interrupts or 0 for s in interrupts_known)
    if texts and len(all_prompts) >= MIN_PROMPTS and interrupts_known:
        # An interrupt and the redirect the human types straight after it are ONE act of
        # steering, so a corrective prompt that follows an interrupt is not counted twice.
        corrective = sum(
            1 for p in all_prompts if p.text and is_corrective(p.text) and not p.after_interrupt
        )
        m["steer_rate"] = _metric(
            round((interrupts + corrective) / len(all_prompts), 3),
            "share",
            len(all_prompts),
            "interrupts_and_correction_markers",
            interrupts=interrupts,
            corrective_prompts=corrective,
        )
    else:
        m["steer_rate"] = _metric(
            None,
            "share",
            len(all_prompts),
            "interrupts_and_correction_markers",
            "prompt text and interrupt counts are not stored server side"
            if not texts or not interrupts_known
            else f"{len(all_prompts)} prompts, {MIN_PROMPTS} needed",
        )

    # ---- code velocity
    lines_basis = lines_known[0].lines_basis if lines_known else LINES_ABSENT
    writes_known = [s for s in ss if s.write_events is not None]
    writes_total = sum(s.write_events or 0 for s in writes_known)
    # Enough behind the number to be a rate: ten writes where the writes can be counted,
    # or a line total too large to be an artefact where they cannot.
    enough_writes = (
        writes_total >= MIN_WRITE_EVENTS_FOR_VELOCITY if writes_known else False
    ) or lines_total >= MIN_LINES_FOR_VELOCITY
    if lines_known and lines_total > 0 and active >= MIN_ACTIVE_SEC_FOR_VELOCITY and enough_writes:
        m["code_velocity"] = _metric(
            round(lines_total / active_hours, 1),
            "lines per active hour",
            n_sessions,
            lines_basis,
            write_events=writes_total if writes_known else None,
        )
    else:
        m["code_velocity"] = _metric(
            None,
            "lines per active hour",
            n_sessions,
            lines_basis,
            (
                "no agent lines were attributed: this corpus writes files a way the "
                "line counters do not credit, and 0 would read as 'you wrote nothing'"
                if lines_known and lines_total == 0
                else (
                    f"only {writes_total} tool calls wrote a file and {lines_total} lines "
                    "were attributed, too little to read as a rate"
                    if writes_known
                    else f"{lines_total} attributed lines is too small a total to read as "
                    "a rate, and the writes behind it cannot be counted here"
                )
                if lines_known and not enough_writes
                else f"{round(active)}s of active time, {round(MIN_ACTIVE_SEC_FOR_VELOCITY)}s needed"
                if lines_known
                else "no line counts"
            ),
            write_events=writes_total if writes_known else None,
        )

    # ---- tool diversity and the top tools
    diverse = [s for s in tool_known if sum(s.tool_calls.values()) >= MIN_TOOL_CALLS_FOR_DIVERSITY]
    basis = tool_known[0].tool_basis if tool_known else TOOLS_ABSENT
    if len(diverse) >= MIN_SESSIONS_FOR_DIVERSITY and basis == TOOLS_ALL:
        m["tool_diversity"] = _metric(
            round(sum(len(s.tool_calls) for s in diverse) / len(diverse), 1),
            "distinct tools per session",
            len(diverse),
            basis,
        )
    else:
        m["tool_diversity"] = _metric(
            None,
            "distinct tools per session",
            len(diverse),
            basis,
            (
                "tool names are bucketed to an allowlist here, so distinct names would "
                "undercount every corpus"
                if basis == TOOLS_ALLOWLIST
                else f"{len(diverse)} sessions with {MIN_TOOL_CALLS_FOR_DIVERSITY}+ tool calls"
            ),
        )
    mix: Counter[str] = Counter()
    for s in ss:
        mix.update(s.tool_calls)
    top_tools = [
        {"tool": t, "calls": c, "share": round(c / tool_total, 3)}
        for t, c in sorted(mix.items(), key=lambda kv: (-kv[1], kv[0]))[:5]
    ]

    # ---- test runs per active hour (the quality_guardian rule reads this)
    tests_known = [s for s in ss if s.test_runs is not None]
    tests_total = sum(s.test_runs or 0 for s in tests_known)
    if tests_known and active >= MIN_ACTIVE_SEC_FOR_VELOCITY:
        m["test_runs_per_hour"] = _metric(
            round(tests_total / active_hours, 2),
            "test runs per active hour",
            tests_total,
            TEST_RUNS_LOWER_BOUND,
        )
    else:
        m["test_runs_per_hour"] = _metric(
            None,
            "test runs per active hour",
            tests_total,
            TEST_RUNS_LOWER_BOUND if tests_known else "absent",
            "test runs are not counted server side"
            if not tests_known
            else f"{round(active)}s of active time, {round(MIN_ACTIVE_SEC_FOR_VELOCITY)}s needed",
        )

    # ---- night, peak hour
    by_hour = _active_by_hour(ss)
    night = sum(v for h, v in by_hour.items() if h >= NIGHT_START_HOUR or h < NIGHT_END_HOUR)
    if active >= MIN_ACTIVE_SEC_FOR_SHARES:
        m["night_share"] = _metric(
            round(night / active, 3),
            "share",
            n_sessions,
            "active_seconds_by_local_hour",
            note=f"{NIGHT_START_HOUR}:00 to 0{NIGHT_END_HOUR}:00 local",
        )
        peak = max(by_hour.items(), key=lambda kv: (kv[1], -kv[0]))
        m["peak_hour"] = _metric(peak[0], "local hour", n_sessions, "active_seconds_by_local_hour")
    else:
        why = f"{round(active)}s of active time, {round(MIN_ACTIVE_SEC_FOR_SHARES)}s needed"
        m["night_share"] = _metric(None, "share", n_sessions, "active_seconds_by_local_hour", why)
        m["peak_hour"] = _metric(
            None, "local hour", n_sessions, "active_seconds_by_local_hour", why
        )

    commit_times = [(t, s.tz_offset_minutes) for s in ss if s.commit_times for t in s.commit_times]
    if commit_times:
        night_commits = sum(
            1
            for t, off in commit_times
            if (dt.datetime.fromtimestamp(t, dt.UTC) + dt.timedelta(minutes=off)).hour
            >= NIGHT_START_HOUR
            or (dt.datetime.fromtimestamp(t, dt.UTC) + dt.timedelta(minutes=off)).hour
            < NIGHT_END_HOUR
        )
        m["night_commit_share"] = _metric(
            round(night_commits / len(commit_times), 3),
            "share",
            len(commit_times),
            "commit_timestamps",
            night_commits=night_commits,
        )
    else:
        m["night_commit_share"] = _metric(
            None,
            "share",
            0,
            "commit_timestamps",
            "commit times are not stored: the strip marks are deduped for rendering",
        )

    # ---- days, streak
    days = sorted({s.local_day for s in ss if s.active_seconds > 0})
    streak, best = 0, 0
    prev: dt.date | None = None
    for d in days:
        streak = streak + 1 if prev is not None and (d - prev).days == 1 else 1
        best = max(best, streak)
        prev = d
    by_day: Counter[dt.date] = Counter()
    for s in ss:
        by_day[s.local_day] += s.active_seconds
    busiest = max(by_day.items(), key=lambda kv: (kv[1], kv[0])) if by_day else None
    m["longest_streak_days"] = _metric(best or None, "days", len(days), "local_days_at_04h")
    m["busiest_day"] = _metric(
        busiest[0].isoformat() if busiest else None,
        "date",
        len(days),
        "local_days_at_04h",
        None if busiest else "no active time",
        active_seconds=round(busiest[1]) if busiest else None,
    )

    # ---- model mix
    tokens: Counter[str] = Counter()
    for s in ss:
        tokens.update(s.output_tokens_by_model)
    total_out = sum(tokens.values())
    # A model that wrote no output tokens is not part of the mix: the locally generated
    # error and interrupt placeholders carry a model label and no usage at all.
    model_mix = [
        {"model": mid, "output_tokens": n, "share": round(n / total_out, 4)}
        for mid, n in sorted(tokens.items(), key=lambda kv: (-kv[1], kv[0]))
        if n > 0
    ]

    sample = {
        "sessions": n_sessions,
        "sessions_with_prompt_text": sum(1 for s in ss if s.prompts),
        "prompts": prompt_total,
        "prompts_with_text": len(texts),
        "tool_calls": tool_total,
        "active_hours": round(active_hours, 2),
        "days": len(days),
        "min_sessions": MIN_SESSIONS,
        "enough_sessions": n_sessions >= MIN_SESSIONS,
        "missing": {k: v["reason"] for k, v in m.items() if v["value"] is None and v["reason"]},
    }

    arch = archetype(m, sample)
    profile = {
        "profile_version": PROFILE_VERSION,
        "generated_at": dt.datetime.fromtimestamp(
            now if now is not None else dt.datetime.now(dt.UTC).timestamp(), dt.UTC
        ).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sample": sample,
        "totals": totals,
        "metrics": m,
        "top_tools": top_tools,
        "model_mix": model_mix,
        "archetype": arch,
    }
    profile["facts"] = headline_facts(profile)
    return profile


# ------------------------------------------------------------------------ archetype
#: Each archetype is ONE named metric crossing ONE threshold. The rule is written out in
#: `rule` so the UI can show why, and the threshold's source is written beside it here.
#: Where a threshold is a judgement call it says so; nothing here is fitted.
ARCHETYPE_RULES: tuple[dict, ...] = (
    {
        "name": "architect",
        "metric": "planning_ratio",
        "threshold": 2.4,
        "rule": "prose before the first tool on more than twice as many prompts as go straight to work",
        "source": "Paxel's published example report shows planning_ratio 2.4 for an Architect",
    },
    {
        "name": "velocity_machine",
        "metric": "code_velocity",
        "threshold": 487.0,
        "rule": "agent lines per active hour",
        "source": "Paxel's published example report shows code_velocity 487 lines/hour",
    },
    {
        "name": "quality_guardian",
        "metric": "test_runs_per_hour",
        "threshold": 3.0,
        "rule": "test runs per active hour",
        "source": "UNMEASURED JUDGEMENT CALL: one test run every twenty minutes of active time",
    },
    {
        "name": "night_owl",
        "metric": "night_share",
        "threshold": 0.4,
        "rule": "share of active seconds between 22:00 and 04:00 local",
        "source": "the six night hours are 0.25 of the clock; 0.4 is a clear lean, not a tie",
    },
    {
        "name": "director",
        "metric": "autonomy_score",
        "threshold": 0.5,
        "rule": "share of active time the agent ran with nobody present",
        "source": "half the clock: more of the work happens without you than with you",
    },
    {
        "name": "skeptic",
        "metric": "steer_rate",
        "threshold": 0.4,
        "rule": "interrupts plus corrective prompts, over prompts",
        "source": "Paxel's example copy calls out 'about 4 in 10 prompts' as steering hard",
    },
)


def archetype(metrics: Mapping[str, dict], sample: Mapping) -> dict:
    """The archetype, its confidence, and the runners up with their scores.

    Deterministic: each rule scores `value / threshold`, halved so that meeting the
    threshold exactly scores 0.5, and the winner must have met its threshold. A rule
    whose metric is None does not score and cannot win. Confidence is the winner's
    margin over the runner up, damped by how many sessions the corpus has.
    """
    scored: list[dict] = []
    for rule in ARCHETYPE_RULES:
        value = _rule_value(rule["metric"], metrics)
        if value is None:
            scored.append({**rule, "value": None, "score": None})
            continue
        scored.append(
            {**rule, "value": value, "score": round(min(value / rule["threshold"], 2.0) / 2, 3)}
        )

    ranked = sorted(
        (s for s in scored if s["score"] is not None), key=lambda s: (-s["score"], s["name"])
    )
    if not ranked or ranked[0]["score"] < 0.5 or not sample.get("enough_sessions"):
        return {
            "name": None,
            "confidence": None,
            # The same key set as the winning case: a field that appears only sometimes is
            # a second shape for one object, and the reader has to guess which they hold.
            "metric": None,
            "value": None,
            "threshold": None,
            "rule": None,
            "reason": (
                f"fewer than {sample.get('min_sessions', MIN_SESSIONS)} sessions"
                if not sample.get("enough_sessions")
                else "no archetype rule met its threshold"
            ),
            "scores": [
                {k: s[k] for k in ("name", "metric", "value", "threshold", "score", "rule")}
                for s in scored
            ],
            "runners_up": [],
        }

    top = ranked[0]
    second = ranked[1]["score"] if len(ranked) > 1 else 0.0
    margin = (top["score"] - second) / top["score"] if top["score"] else 0.0
    damp = min(1.0, sample.get("sessions", 0) / 10.0)
    return {
        "name": top["name"],
        "confidence": round(min(1.0, 0.4 + 0.6 * margin) * damp, 2),
        "reason": None,
        "metric": top["metric"],
        "value": top["value"],
        "threshold": top["threshold"],
        "rule": top["rule"],
        "scores": [
            {k: s[k] for k in ("name", "metric", "value", "threshold", "score", "rule")}
            for s in scored
        ],
        "runners_up": [
            {"name": r["name"], "score": r["score"], "metric": r["metric"], "value": r["value"]}
            for r in ranked[1:3]
        ],
    }


def _rule_value(name: str, metrics: Mapping[str, dict]) -> float | None:
    entry = metrics.get(name)
    return None if entry is None else entry.get("value")


# ---------------------------------------------------------------------- the facts
#: What a number is compared against to decide whether it is worth saying out loud.
#: `scale` is the distance at which a difference counts as one unit of unusual, so the
#: ranking does not simply favour whichever metric happens to have the biggest numbers.
BASELINES: dict[str, dict] = {
    "steer_rate": {
        "value": 0.4,
        "scale": 0.2,
        "source": "Paxel example copy: 'stop and redirect about 4 in 10 prompts'",
    },
    "planning_ratio": {
        "value": 2.4,
        "scale": 1.2,
        "source": "Paxel example report: planning_ratio 2.4",
    },
    "code_velocity": {
        "value": 487.0,
        "scale": 250.0,
        "source": "Paxel example report: 487 lines/hour",
    },
    "autonomy_score": {
        "value": 0.82,
        "scale": 0.25,
        "source": "Paxel example report: autonomy_score 0.82",
    },
    "avg_prompt_chars": {
        "value": 156.0,
        "scale": 100.0,
        "source": "Paxel example report: avg prompt length 156 chars",
    },
    "iteration_depth": {
        "value": 16.4,
        "scale": 8.0,
        "source": "CLAUDE.md reference corpus: 23,838 tool calls over 1,456 typed prompts",
    },
    "night_share": {
        "value": 0.25,
        "scale": 0.15,
        "source": "22:00-04:00 is six of twenty four hours, so no bias at all is 0.25",
    },
    "night_commit_share": {
        "value": 0.25,
        "scale": 0.15,
        "source": "same six hours, applied to commit times",
    },
    "short_prompt_share": {
        "value": 0.5,
        "scale": 0.25,
        "source": "UNMEASURED JUDGEMENT CALL: half of prompts under ten words is unremarkable",
    },
    "tool_diversity": {
        "value": 6.0,
        "scale": 3.0,
        "source": "UNMEASURED JUDGEMENT CALL: six distinct tools in a session",
    },
}


def _n(x: float) -> str:
    """A number a person reads out loud: no trailing .0, thousands separated."""
    if x == int(x):
        return f"{int(x):,}"
    return f"{x:,.1f}"


def _pct(x: float) -> str:
    return f"{round(x * 100)}%"


def headline_facts(profile: Mapping) -> list[dict]:
    """One-line facts in the second person, most unusual first.

    Each carries {id, text, value, unit} so the UI can style the number, plus the
    baseline it was ranked against. NO EM DASHES, here or anywhere else a person reads:
    short plain sentences, commas and full stops only.
    """
    m = profile["metrics"]
    out: list[dict] = []

    def add(fact_id: str, text: str, value, unit: str, unusual: float, **extra):
        # `baseline` is always a key, null where the fact was not ranked against one, for
        # the same reason the archetype keys are: one shape, not two.
        out.append(
            {
                "id": fact_id,
                "text": text,
                "value": value,
                "unit": unit,
                "unusualness": round(unusual, 3),
                "baseline": None,
                **extra,
            }
        )

    def unusual(key: str, value: float) -> float:
        b = BASELINES.get(key)
        if not b:
            return 0.0
        return abs(value - b["value"]) / b["scale"]

    v = m["steer_rate"]["value"]
    if v is not None:
        n_in_ten = round(v * 10)
        text = (
            f"You steer hard: {n_in_ten} in 10 prompts stop or redirect the agent"
            if v >= 0.3
            else f"You let it run: {n_in_ten} in 10 prompts stop or redirect the agent"
        )
        add(
            "steer_rate",
            text,
            v,
            "share",
            unusual("steer_rate", v),
            baseline=BASELINES["steer_rate"],
        )

    v = m["night_commit_share"]["value"]
    if v is not None:
        add(
            "night_commit_share",
            f"{_pct(v)} of your commits land after 10pm",
            v,
            "share",
            unusual("night_commit_share", v),
            baseline=BASELINES["night_commit_share"],
        )

    v = m["night_share"]["value"]
    if v is not None:
        add(
            "night_share",
            f"{_pct(v)} of your build time is between 10pm and 4am",
            v,
            "share",
            unusual("night_share", v),
            baseline=BASELINES["night_share"],
        )

    v = m["autonomy_score"]["value"]
    if v is not None:
        add(
            "autonomy_score",
            f"{_pct(v)} of your build time runs without you",
            v,
            "share",
            unusual("autonomy_score", v),
            baseline=BASELINES["autonomy_score"],
        )

    v = m["code_velocity"]["value"]
    if v is not None:
        add(
            "code_velocity",
            f"{_n(v)} lines an hour while the agent is running",
            v,
            "lines per active hour",
            unusual("code_velocity", v),
            baseline=BASELINES["code_velocity"],
        )

    v = m["planning_ratio"]["value"]
    if v is not None:
        text = (
            f"You brief before you build: {_n(v)} prompts get an answer for every one that "
            "goes straight to a tool"
            if v >= 1
            else f"You point and shoot: only {_n(v)} prompts get an answer for every one that "
            "goes straight to a tool"
        )
        add(
            "planning_ratio",
            text,
            v,
            "ratio",
            unusual("planning_ratio", v),
            baseline=BASELINES["planning_ratio"],
        )

    v = m["iteration_depth"]["value"]
    if v is not None:
        add(
            "iteration_depth",
            f"{_n(v)} tool calls per prompt, on average",
            v,
            "tool calls per prompt",
            unusual("iteration_depth", v),
            baseline=BASELINES["iteration_depth"],
        )

    v = m["avg_prompt_chars"]["value"]
    if v is not None:
        add(
            "avg_prompt_chars",
            f"Your prompts run {_n(v)} characters on average",
            v,
            "chars",
            unusual("avg_prompt_chars", v),
            baseline=BASELINES["avg_prompt_chars"],
        )

    v = m["short_prompt_share"]["value"]
    if v is not None:
        add(
            "short_prompt_share",
            f"{_pct(v)} of your prompts are under ten words",
            v,
            "share",
            unusual("short_prompt_share", v),
            baseline=BASELINES["short_prompt_share"],
        )

    v = m["tool_diversity"]["value"]
    if v is not None:
        add(
            "tool_diversity",
            f"{_n(v)} different tools in the average session",
            v,
            "distinct tools per session",
            unusual("tool_diversity", v),
            baseline=BASELINES["tool_diversity"],
        )

    mix = profile.get("model_mix") or []
    if mix and mix[0]["share"] >= 0.5:
        top = mix[0]
        add(
            "model_mix",
            f"You default to {_model_name(top['model'])}: {_pct(top['share'])} of output tokens",
            top["share"],
            "share",
            top["share"] - 0.5,
        )

    tools = profile.get("top_tools") or []
    if tools and tools[0]["share"] >= 0.4:
        add(
            "top_tool",
            f"{tools[0]['tool']} is {_pct(tools[0]['share'])} of every tool call you make",
            tools[0]["share"],
            "share",
            tools[0]["share"] - 0.4,
        )

    v = m["peak_hour"]["value"]
    if v is not None:
        add("peak_hour", f"You build most at {_hour(v)}", v, "local hour", 0.2)

    v = m["longest_streak_days"]["value"]
    if v is not None and v >= 2:
        add(
            "longest_streak_days",
            f"{_n(v)} days in a row with a session",
            v,
            "days",
            min(v / 7.0, 1.0),
        )

    t = profile["totals"]
    if t["total_sessions"]:
        add(
            "totals",
            f"{_n(t['total_hours'])} hours across {_n(t['total_sessions'])} sessions",
            t["total_hours"],
            "hours",
            0.0,
        )

    out.sort(key=lambda f: (-f["unusualness"], f["id"]))
    return out


def _model_name(model_id: str) -> str:
    """`claude-opus-5` reads as Opus. Labels only; the id travels in `value`."""
    for name in ("opus", "sonnet", "haiku", "fable"):
        if name in model_id.lower():
            return name.capitalize()
    return model_id


def _hour(h: int) -> str:
    if h == 0:
        return "midnight"
    if h == 12:
        return "noon"
    return f"{h % 12 or 12}{'am' if h < 12 else 'pm'}"
