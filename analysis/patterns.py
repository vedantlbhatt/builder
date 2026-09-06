"""What a person could DO differently, with the two numbers that say why.

`profile.py` measures the corpus: how much, how fast, how often. This module asks the
only question that is worth a paragraph on somebody's profile, which is **what would you
change tomorrow**. Every finding here compares two groups of that person's own sessions
and reports the gap, so each sentence carries its own evidence AND its consequence:

    "Sessions where your first message ran past 200 characters ended in a commit 8 of 9
     times. The ones where you dived straight in: 2 of 7."

THREE RULES, and the first two are the ones a finding usually fails.

1. A FINDING NAMES A COST OR A MOVE. "Your later prompts are shorter than your first"
   is a true sentence with nothing on the other end of it; nobody can act on it and
   nobody asked. The same measurement becomes useful the moment it is attached to
   whether the session shipped. If a comparison cannot be finished with "so it cost
   you X" or "so do Y", it does not belong here.

2. NO WORD THE READER WOULD HAVE TO LOOK UP. Not "steer rate", not "front-loading",
   not "autonomy score 0.361". Say "you had to take the wheel back" and "you let it
   run". Every internal metric name in this file stays in `left`/`right`, where a
   screen can show the working, and out of `text`, which a person reads.

3. BOTH SIDES BIG ENOUGH, GAP BIG ENOUGH, OR SAY NOTHING. `MIN_GROUP = 5` observations
   a side and `MIN_LIFT = 1.4` (or `MIN_SHARE_GAP = 0.15` points). Below either bar the
   finding is dropped, not softened. A sentence that says "you slightly prefer" about
   three sessions against four reads as insight and is noise.

Everything here needs PROMPT TEXT, the tool results around it and the timings between,
so it runs on a machine that has the transcripts. The server never sees prompt wording
(privacy/upload-contract.json) and cannot compute a single one of these.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import re
from collections.abc import Sequence

#: Both sides of a comparison need this many observations. Five is not a lot; it is the
#: point below which one different session swings the whole ratio.
MIN_GROUP = 5

#: A ratio under this is not a pattern, it is two numbers that happen to differ. 1.4 is a
#: judgement call and is labelled as one: it is roughly where a difference stops being
#: explicable by one unusual session in a corpus of ten.
MIN_LIFT = 1.4

#: Share comparisons use points rather than a ratio: 2% against 1% is a 2x lift and means
#: nothing.
MIN_SHARE_GAP = 0.15

#: `short_prompt_share` in profile.py uses the same cut, so the two agree about what a
#: short prompt is.
SHORT_PROMPT_WORDS = 10

#: A stretch of tool calls with nothing to show for it. Below this it is the agent
#: reading the code before it edits, which is work. MEASURED on a 17-session corpus: the
#: median run between two file writes is 4 tool calls, p90 is 18, and the longest is 130.
#: 25 sits above the ninetieth percentile of normal work and well under the outliers.
SPIN_TOOL_CALLS = 25

#: Consecutive failing tool results before it stops being a fix and starts being a loop.
STUCK_FAILURES = 4

#: A file written this many times in one sitting is being fought with.
REWORK_WRITES = 4

#: The local hour a late session starts after (or before 04:00, the day boundary).
NIGHT_FROM = 22
DAY_BOUNDARY_HOUR = 4

_TEST_CMD = re.compile(
    r"\b(pytest|bun test|npm test|swift test|jest|cargo test|go test|make test)\b"
)
_COMMIT_CMD = re.compile(r"\bgit commit\b")


@dataclasses.dataclass(frozen=True)
class Finding:
    """One comparison, with the sentence and both sides of it."""

    id: str
    #: Second person, plain, numbers inline, and it names a cost or a move. This is the
    #: only field a person reads.
    text: str
    #: The two groups, so a screen can show the working and a test can check the sentence.
    #: Internal metric names live HERE, never in `text`.
    left: dict
    right: dict
    #: How many times bigger the left side is. 1.0 means no difference.
    lift: float
    basis: str


@dataclasses.dataclass(frozen=True)
class SessionEvents:
    """One session's digest events plus what the sessionizer decided about it."""

    session_id: str
    started_at: float
    ended_at: float
    active_seconds: float
    attended_seconds: float
    tz_offset_minutes: int
    events: Sequence  # analysis.digest.Ev
    #: Output tokens for this sitting, from the authoritative LEDGER, or None when the
    #: caller does not have it. Never summed off the events: `Ev.tok_out` is set only on
    #: the assistant records that happen to carry usage, and MEASURED on this corpus it
    #: reports 39,487 output tokens for 21 hours of work, which is off by orders of
    #: magnitude. Absent is a refusal; zero would be a lie.
    output_tokens: int | None = None


# ------------------------------------------------------------------ small measurements


def _local_hour(ts: float, tz_offset_minutes: int) -> int:
    return (dt.datetime.fromtimestamp(ts, dt.UTC) + dt.timedelta(minutes=tz_offset_minutes)).hour


def _pct(x: float) -> str:
    return f"{round(x * 100)}%"


def _lift(a: float, b: float) -> float:
    return round(a / b, 2) if b > 0 else 0.0


def _mins(seconds: float) -> str:
    m = round(seconds / 60)
    if m < 60:
        return f"{m} minute{'' if m == 1 else 's'}"
    return f"{m // 60}h {m % 60:02d}m"


def _wrote(e) -> bool:
    """Did this tool call put lines into a file? Edit and Write, and a shell heredoc.

    A shell write counts because it is most of the work: MEASURED on this repository's
    corpus, 2,452 of 2,458 attributable lines came through the shell (CLAUDE.md). `sed -i`
    names a path and returns no count, so it is a touch and not a magnitude.
    """
    from . import digest as dg

    return e.kind == "tool" and (
        e.tool in dg.EDIT_TOOLS or (e.tool in dg.SHELL_TOOLS and e.added is not None)
    )


def _committed(e) -> bool:
    from . import digest as dg

    return e.kind == "tool" and (
        e.tool in dg.COMMIT_TOOLS or (e.tool in dg.SHELL_TOOLS and bool(_COMMIT_CMD.search(e.text)))
    )


def _tested(e) -> bool:
    from . import digest as dg

    return e.kind == "tool" and e.tool in dg.SHELL_TOOLS and bool(_TEST_CMD.search(e.text or ""))


def _lines_added(s: SessionEvents) -> int:
    return sum(e.added or 0 for e in s.events if _wrote(e))


def _commits(s: SessionEvents) -> int:
    return sum(1 for e in s.events if _committed(e))


def _first_prompt(s: SessionEvents):
    return next((e for e in s.events if e.kind == "prompt" and e.text), None)


def findings(sessions: Sequence[SessionEvents]) -> list[Finding]:
    """Every comparison that cleared all three bars, most striking first."""
    out: list[Finding] = []
    for fn in (
        _what_a_shipping_session_looks_like,
        _the_spin,
        _stuck_in_a_loop,
        _fighting_one_file,
        _when_the_work_lands,
        _short_prompts_get_corrected,
        _verification_habit,
        _what_the_quiet_sessions_cost,
    ):
        found = fn(sessions)
        if found is not None:
            out.append(found)
    out.sort(key=lambda f: -abs(f.lift - 1.0))
    return out


# ------------------------------------------------------------------- what ships, and why


def _what_a_shipping_session_looks_like(sessions: Sequence[SessionEvents]) -> Finding | None:
    """Sessions that produced a commit against sessions that did not, on the one thing
    the person controls before the session starts: how much they said up front.

    This is the finding the whole module exists for. It uses the same measurement as "your
    opening prompt is longer than the rest", which on its own is a fact with nothing on the
    end of it, and attaches it to whether the sitting ended with anything landing. Same
    number, and now there is something to do with it.
    """
    openers = [(_first_prompt(s), s) for s in sessions]
    openers = [(f, s) for f, s in openers if f is not None]
    if len(openers) < MIN_GROUP * 2:
        return None
    # Split by RANK, not against a threshold: this person's own longer half of openings
    # against their own shorter half. A threshold ("over 200 characters") is a number from
    # nowhere, and a threshold at the median puts every session on one side the moment the
    # lengths are bimodal, which they are: FOUND BY RUNNING IT, a corpus of six long
    # openers and six one-word ones produced a "with brief" group of zero.
    #
    # Requiring a follow-up prompt as well was worse still: 13 of 18 sessions in the
    # reference corpus have exactly one prompt, so the comparison had five observations to
    # split and could never clear the bar.
    order = sorted(openers, key=lambda pair: len(pair[0].text))
    half = len(order) // 2
    without = [_commits(s) > 0 for _, s in order[:half]]
    with_brief = [_commits(s) > 0 for _, s in order[len(order) - half :]]
    if len(with_brief) < MIN_GROUP or len(without) < MIN_GROUP:
        return None
    a, b = sum(with_brief) / len(with_brief), sum(without) / len(without)
    if a - b < MIN_SHARE_GAP:
        return None
    return Finding(
        id="what_a_shipping_session_looks_like",
        text=(
            f"When you spell the job out before starting, it lands. Of the "
            f"{len(with_brief)} sessions you opened with the most detail, "
            f"{sum(with_brief)} ended with a commit. Of the {len(without)} you opened "
            f"shortest, {sum(without)} did. Same person, different first message."
        ),
        left={
            "group": "the half of your sessions opened with the most detail",
            "n": len(with_brief),
            "shipped": sum(with_brief),
            "share": round(a, 3),
        },
        right={
            "group": "the half opened shortest",
            "n": len(without),
            "shipped": sum(without),
            "share": round(b, 3),
        },
        lift=_lift(a, b),
        basis="first_prompt_length_vs_commits",
    )


# ------------------------------------------------------------------------- going nowhere


def _checkpoint(e) -> bool:
    """Did this tool call produce something a person would count as progress?

    A file written, a commit, or a test run. All three, not just the write, because the
    write is the one this parser sees least reliably: a shell heredoc is caught, but an
    edit made by a script the agent wrote (`python3 - <<PY`) is a Bash call with no line
    count anywhere in it. Counting only writes would turn every such session into one long
    stretch of "nothing happened", which is a statement about the parser dressed up as a
    statement about the person.
    """
    return _wrote(e) or _committed(e) or _tested(e)


#: A corpus with fewer checkpoints than this per tool call cannot support the spin finding:
#: the gaps between them are measuring what the parser could see, not what the person did.
#: MEASURED on this container's corpus: 111 checkpoints in 1,259 tool calls, 1 in 11.
MIN_CHECKPOINT_DENSITY = 1 / 20


def _runs_with_nothing_to_show(s: SessionEvents) -> list[tuple[int, float]]:
    """(tool calls, seconds) for every stretch between two checkpoints."""
    runs, n, start = [], 0, None
    for e in s.events:
        if e.kind != "tool":
            continue
        if _checkpoint(e):
            if n and start is not None:
                runs.append((n, e.ts - start))
            n, start = 0, None
            continue
        n += 1
        if start is None:
            start = e.ts
    if n and start is not None:
        runs.append((n, s.ended_at - start))
    return runs


def _the_spin(sessions: Sequence[SessionEvents]) -> Finding | None:
    """The longest the agent ran without changing anything, and what that cost in total.

    This is the one number that tells a person WHEN to interrupt. A long hands-off run
    that is writing code is the product working; a long hands-off run that is reading,
    grepping and re-reading is the agent circling, and the person is the only one who can
    see it happening.
    """
    calls = sum(1 for s in sessions for e in s.events if e.kind == "tool")
    checkpoints = sum(1 for s in sessions for e in s.events if e.kind == "tool" and _checkpoint(e))
    if not calls or checkpoints / calls < MIN_CHECKPOINT_DENSITY:
        # Refused, not estimated. See MIN_CHECKPOINT_DENSITY: below this the gaps are the
        # parser's blind spots and the finding would report them as the person's wasted time.
        return None
    all_runs = [(run, s) for s in sessions for run in _runs_with_nothing_to_show(s)]
    if len(all_runs) < MIN_GROUP:
        return None
    spins = [(calls, secs) for (calls, secs), _ in all_runs if calls >= SPIN_TOOL_CALLS]
    if not spins:
        return None
    ordinary = [calls for (calls, _), _ in all_runs if calls < SPIN_TOOL_CALLS]
    if len(ordinary) < MIN_GROUP:
        return None
    worst_calls, worst_secs = max(spins)
    typical = sorted(ordinary)[len(ordinary) // 2]
    lost = sum(secs for _, secs in spins)
    return Finding(
        id="the_spin",
        text=(
            f"{len(spins)} time{'' if len(spins) == 1 else 's'} the agent ran a long way "
            f"with nothing to show: no file written, no test, no commit. The worst was "
            f"{worst_calls} tool calls over {_mins(worst_secs)}, against {typical} calls "
            f"in a normal stretch between two of them. Those runs cost you {_mins(lost)} in "
            f"total, and they are the ones worth cutting short."
        ),
        left={
            "group": f"runs of {SPIN_TOOL_CALLS}+ calls with no write, test or commit",
            "n": len(spins),
            "worst_tool_calls": worst_calls,
            "worst_seconds": round(worst_secs),
            "total_seconds": round(lost),
        },
        right={
            "group": "an ordinary stretch between two checkpoints",
            "n": len(ordinary),
            "median_tool_calls": typical,
        },
        lift=_lift(worst_calls, max(typical, 1)),
        basis="tool_calls_between_checkpoints",
    )


def _stuck_in_a_loop(sessions: Sequence[SessionEvents]) -> Finding | None:
    """Consecutive failing tool results: the agent trying the same thing again."""
    loops, longest, longest_secs = [], 0, 0.0
    total_calls = 0
    for s in sessions:
        # A failure is a `result_error` event sitting where the call's result belongs, NOT
        # a flag on the call itself. Walking both kinds and testing `ok` counts the call as
        # a success and its own error as a separate failure, so a run never reaches two and
        # this finder silently never fires. FOUND BY RUNNING IT on a corpus with 128 errors
        # and 0 reported loops.
        seq = [e for e in s.events if e.kind in ("tool", "result_error")]
        run, start = 0, None
        i = 0
        while i < len(seq):
            e = seq[i]
            if e.kind != "tool":
                i += 1
                continue
            total_calls += 1
            failed = i + 1 < len(seq) and seq[i + 1].kind == "result_error"
            if failed:
                run += 1
                if start is None:
                    start = e.ts
                if run > longest:
                    longest, longest_secs = run, e.ts - start
                i += 2
                continue
            if run >= STUCK_FAILURES and start is not None:
                loops.append((run, e.ts - start))
            run, start = 0, None
            i += 1
        if run >= STUCK_FAILURES and start is not None:
            loops.append((run, s.ended_at - start))
    if total_calls < MIN_GROUP or not loops:
        return None
    lost = sum(secs for _, secs in loops)
    return Finding(
        id="stuck_in_a_loop",
        text=(
            f"{len(loops)} time{'' if len(loops) == 1 else 's'} it failed "
            f"{STUCK_FAILURES} or more times in a row before anything changed. The worst "
            f"run was {longest} failures over {_mins(longest_secs)}. That is not debugging, "
            f"it is a loop, and stepping in with the actual error is faster than watching "
            f"it try again."
        ),
        left={
            "group": f"runs of {STUCK_FAILURES}+ consecutive failures",
            "n": len(loops),
            "longest_run": longest,
            "total_seconds": round(lost),
        },
        right={"group": "tool calls in the corpus", "n": total_calls},
        lift=_lift(len(loops) * STUCK_FAILURES, max(total_calls / 100, 1)),
        basis="consecutive_failed_tool_results",
    )


def _fighting_one_file(sessions: Sequence[SessionEvents]) -> Finding | None:
    """The file that got rewritten most in one sitting, and how long that took."""
    worst = None  # (writes, seconds, name, session_id)
    fought = once = 0
    for s in sessions:
        seen: dict[str, list[float]] = {}
        for e in s.events:
            if _wrote(e) and e.path:
                seen.setdefault(e.path, []).append(e.ts)
        for path, times in seen.items():
            if len(times) >= REWORK_WRITES:
                fought += 1
                span = max(times) - min(times)
                if worst is None or len(times) > worst[0]:
                    worst = (len(times), span, path.rsplit("/", 1)[-1], s.session_id)
            else:
                once += 1
    total = fought + once
    if total < MIN_GROUP or worst is None:
        return None
    share = fought / total
    return Finding(
        id="fighting_one_file",
        text=(
            f"{worst[2]} was rewritten {worst[0]} times in one sitting, over "
            f"{_mins(worst[1])}. Across the corpus {fought} of {total} files you touch get "
            f"{REWORK_WRITES} or more passes in the same session. A file on its fourth "
            f"rewrite usually needs a decision from you, not another attempt."
        ),
        left={
            "group": f"files written {REWORK_WRITES}+ times in one session",
            "n": total,
            "count": fought,
            "share": round(share, 3),
            "worst_file_writes": worst[0],
            "worst_file_seconds": round(worst[1]),
        },
        right={"group": "files written fewer times", "n": total, "count": once},
        lift=_lift(worst[0], REWORK_WRITES - 1),
        basis="write_counts_per_path_per_session",
    )


# ---------------------------------------------------------------------- when it goes well


def _when_the_work_lands(sessions: Sequence[SessionEvents]) -> Finding | None:
    """Lines landed per hour, late sessions against daylight ones.

    "Your late sessions run longer" is a fact about your calendar. "Your late sessions
    produce a third as much per hour" is a fact about whether to have them.
    """
    late, day = [], []
    for s in sessions:
        if s.active_seconds < 300:
            continue
        rate = _lines_added(s) / (s.active_seconds / 3600)
        h = _local_hour(s.started_at, s.tz_offset_minutes)
        (late if h >= NIGHT_FROM or h < DAY_BOUNDARY_HOUR else day).append(rate)
    if len(late) < MIN_GROUP or len(day) < MIN_GROUP:
        return None
    la, da = sum(late) / len(late), sum(day) / len(day)
    lift = _lift(la, da)
    if MIN_LIFT > lift > 1 / MIN_LIFT:
        return None
    better = lift >= 1
    return Finding(
        id="when_the_work_lands",
        text=(
            f"Starting after {NIGHT_FROM}:00 is your "
            f"{'best' if better else 'most expensive'} hour for hour: those "
            f"{len(late)} sessions landed {round(la)} lines an hour against {round(da)} "
            f"for the {len(day)} you started in daylight."
            + (
                ""
                if better
                else " The late ones are not cheaper, they are the same hours for less."
            )
        ),
        left={
            "group": f"started after {NIGHT_FROM}:00",
            "n": len(late),
            "lines_per_active_hour": round(la, 1),
        },
        right={
            "group": "started in daylight",
            "n": len(day),
            "lines_per_active_hour": round(da, 1),
        },
        lift=lift,
        basis="lines_added_per_active_hour_by_start_hour",
    )


# -------------------------------------------------------------------------- how you steer


def _was_corrected(session: SessionEvents, index: int) -> bool:
    """Did the human's NEXT act push back on what the agent did with this prompt?

    An interrupt or a corrective next prompt both count, and the first of the two to
    arrive decides it: a redirect typed after an interrupt is the same act of steering, not
    a second one (`profile.corpus_profile` counts it the same way).
    """
    from .profile import is_corrective

    for e in session.events[index + 1 :]:
        if e.kind == "interrupt":
            return True
        if e.kind == "prompt":
            return bool(e.text) and is_corrective(e.text)
    return False


def _short_prompts_get_corrected(sessions: Sequence[SessionEvents]) -> Finding | None:
    """One-line prompts against fuller ones, by whether the next thing you did was undo it."""
    short, long = [], []
    for s in sessions:
        for i, e in enumerate(s.events):
            if e.kind != "prompt" or not e.text:
                continue
            (short if len(e.text.split()) < SHORT_PROMPT_WORDS else long).append((s, i))
    if len(short) < MIN_GROUP or len(long) < MIN_GROUP:
        return None
    sc = sum(1 for s, i in short if _was_corrected(s, i))
    lc = sum(1 for s, i in long if _was_corrected(s, i))
    ss, ls = sc / len(short), lc / len(long)
    if ss - ls < MIN_SHARE_GAP:
        return None
    return Finding(
        id="short_prompts_get_corrected",
        text=(
            f"Your one-line instructions are the ones you end up taking back. "
            f"{sc} of your {len(short)} prompts under {SHORT_PROMPT_WORDS} words were "
            f"followed by you stopping it or correcting it, against {lc} of {len(long)} "
            f"of your fuller ones. Every one of those is a round trip you paid for twice."
        ),
        left={
            "group": f"under {SHORT_PROMPT_WORDS} words",
            "n": len(short),
            "corrected": sc,
            "share": round(ss, 3),
        },
        right={
            "group": f"{SHORT_PROMPT_WORDS} words or more",
            "n": len(long),
            "corrected": lc,
            "share": round(ls, 3),
        },
        lift=_lift(ss, ls),
        basis="prompt_text_and_the_next_human_act",
    )


def _verification_habit(sessions: Sequence[SessionEvents]) -> Finding | None:
    """Whether a burst of edits gets tested before you move on to the next thing."""
    tested, untested = 0, 0
    for s in sessions:
        pending = False
        for e in s.events:
            if e.kind != "tool":
                continue
            if _wrote(e):
                pending = True
            elif _tested(e) and pending:
                tested += 1
                pending = False
        if pending:
            untested += 1
    total = tested + untested
    if total < MIN_GROUP:
        return None
    share = tested / total
    good = share >= 0.5
    return Finding(
        id="verification_habit",
        text=(
            f"You run a test after {_pct(share)} of your editing runs "
            f"({tested} of {total})."
            + (
                " That is the habit that keeps a long autonomous run from quietly going "
                "wrong, and it is the strongest thing in your profile."
                if good
                else " The other runs end with code you have not seen fail, which is where "
                "a long autonomous stretch turns into rework."
            )
        ),
        left={
            "group": "edit bursts that ended in a test",
            "n": total,
            "count": tested,
            "share": round(share, 3),
        },
        right={"group": "edit bursts that did not", "n": total, "count": untested},
        lift=_lift(share, 1 - share) if share < 1 else float(total),
        basis="test_commands_after_writes",
    )


def _what_the_quiet_sessions_cost(sessions: Sequence[SessionEvents]) -> Finding | None:
    """Output tokens spent in sittings that ended with no commit, as a share of the bill.

    Reported as a TOTAL and a share, not as a per-session average. FOUND BY RUNNING IT: on
    the reference corpus the sessions that shipped nothing averaged 36,214 output tokens
    each against 148,932 for the ones that did, so a sentence built on the averages would
    have said "your quiet sessions are the cheap ones" while the number a person actually
    cares about, a fifth of their spend producing nothing they kept, went unsaid. The
    average is the wrong statistic here and the direction it points is worse than useless.

    Both halves are reliably visible: `git commit` is in the command text, and the token
    count comes from the ledger. Sessions with no token count are dropped, never zeroed.
    """
    quiet, shipped = 0, 0
    n_quiet, n_shipped = 0, 0
    for s in sessions:
        if s.output_tokens is None or s.output_tokens <= 0:
            continue
        if _commits(s) > 0:
            shipped += s.output_tokens
            n_shipped += 1
        else:
            quiet += s.output_tokens
            n_quiet += 1
    total = quiet + shipped
    if n_quiet < MIN_GROUP or n_shipped < MIN_GROUP or not total:
        return None
    share = quiet / total
    if share < MIN_SHARE_GAP:
        return None
    return Finding(
        id="what_the_quiet_sessions_cost",
        text=(
            f"{n_quiet} of your sessions ended without a single commit. Between them they "
            f"spent {quiet:,} output tokens, {_pct(share)} of everything you have spent, "
            f"and left nothing behind. The other {n_shipped} spent {shipped:,} and shipped."
        ),
        left={
            "group": "sessions that ended with no commit",
            "n": n_quiet,
            "output_tokens": quiet,
            "share_of_spend": round(share, 3),
        },
        right={
            "group": "sessions that ended with a commit",
            "n": n_shipped,
            "output_tokens": shipped,
        },
        lift=_lift(share, 1 - share) if share < 1 else float(n_quiet),
        basis="ledger_output_tokens_by_commit_outcome",
    )


__all__ = ["Finding", "SessionEvents", "findings", "MIN_GROUP", "MIN_LIFT", "MIN_SHARE_GAP"]
