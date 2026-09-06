"""Comparative findings about how one person works, with the two numbers behind each.

`profile.py` measures the corpus: how much, how fast, how often. This module asks the
next question, which is the one a person actually wants answered — WHEN is it different?
Every finding here compares two groups of that person's own prompts or sessions and
reports the gap, so the sentence carries its own evidence:

    "Your prompts under ten words get corrected 3.2x as often as your longer ones
     (8 of 12 against 3 of 18)."

THE RULE, same as everywhere else in this repo: a finding is only emitted when both
groups are big enough to mean something and the gap is big enough to be worth saying.
Below either bar it is dropped, not softened. A pattern that says "you slightly prefer"
about 3 prompts against 4 is the exact failure this codebase is written to avoid — it
reads as insight and it is noise.

Everything here needs PROMPT TEXT and the surrounding events, so it runs on a machine
that has the transcripts. The server never sees prompt wording
(privacy/upload-contract.json), so `corpus_profile` carries the findings a caller
computed rather than computing them itself.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import re
from collections import Counter
from collections.abc import Sequence

#: Both sides of a comparison need this many observations. Five is not a lot; it is the
#: point below which one different prompt swings the whole ratio.
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

#: The local hour a night session starts after (or before 04:00, the day boundary).
NIGHT_FROM = 22
DAY_BOUNDARY_HOUR = 4

#: Words that mark a prompt as a correction of what the agent just did. Kept in sync with
#: `profile.is_corrective`; imported rather than copied so there is one list.
_TEST_CMD = re.compile(
    r"\b(pytest|bun test|npm test|swift test|jest|cargo test|go test|make test)\b"
)


@dataclasses.dataclass(frozen=True)
class Finding:
    """One comparison, with the sentence and both sides of it."""

    id: str
    #: Second person, plain, numbers inline. This is what a person reads.
    text: str
    #: The two groups, so a screen can show the working and a test can check the sentence.
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


def _local_hour(ts: float, tz_offset_minutes: int) -> int:
    return (dt.datetime.fromtimestamp(ts, dt.UTC) + dt.timedelta(minutes=tz_offset_minutes)).hour


def _pct(x: float) -> str:
    return f"{round(x * 100)}%"


def _lift(a: float, b: float) -> float:
    return round(a / b, 2) if b > 0 else 0.0


def findings(sessions: Sequence[SessionEvents]) -> list[Finding]:
    """Every comparison that cleared both bars, most striking first."""
    out: list[Finding] = []
    for fn in (
        _short_prompts_get_corrected,
        _the_opening_prompt,
        _after_an_interrupt,
        _the_leash,
        _night_sessions,
        _verification_habit,
        _rework,
    ):
        found = fn(sessions)
        if found is not None:
            out.append(found)
    out.sort(key=lambda f: -abs(f.lift - 1.0))
    return out


# --------------------------------------------------------------------------- prompts


def _prompts_with_text(sessions: Sequence[SessionEvents]) -> list[tuple[SessionEvents, int, object]]:
    """(session, index in that session's events, event) for every prompt that has text."""
    out = []
    for s in sessions:
        for i, e in enumerate(s.events):
            if e.kind == "prompt" and e.text:
                out.append((s, i, e))
    return out


def _was_corrected(session: SessionEvents, index: int) -> bool:
    """Did the human's NEXT act push back on what the agent did with this prompt?

    An interrupt or a corrective next prompt both count, and the first of the two to
    arrive is the one that decides it: a redirect typed after an interrupt is the same act
    of steering, not a second one (`profile.corpus_profile` counts it the same way).
    """
    from .profile import is_corrective

    for e in session.events[index + 1 :]:
        if e.kind == "interrupt":
            return True
        if e.kind == "prompt":
            return bool(e.text) and is_corrective(e.text)
    return False


def _short_prompts_get_corrected(sessions: Sequence[SessionEvents]) -> Finding | None:
    short = [(s, i) for s, i, e in _prompts_with_text(sessions) if len(e.text.split()) < SHORT_PROMPT_WORDS]
    long = [(s, i) for s, i, e in _prompts_with_text(sessions) if len(e.text.split()) >= SHORT_PROMPT_WORDS]
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
            f"Your one-line prompts are the ones you end up taking back. "
            f"{_pct(ss)} of prompts under {SHORT_PROMPT_WORDS} words are followed by an "
            f"interrupt or a correction, against {_pct(ls)} of your longer ones "
            f"({sc} of {len(short)} against {lc} of {len(long)})."
        ),
        left={"group": f"under {SHORT_PROMPT_WORDS} words", "n": len(short), "corrected": sc, "share": round(ss, 3)},
        right={"group": f"{SHORT_PROMPT_WORDS} words or more", "n": len(long), "corrected": lc, "share": round(ls, 3)},
        lift=_lift(ss, ls),
        basis="prompt_text_and_the_next_human_act",
    )


def _the_opening_prompt(sessions: Sequence[SessionEvents]) -> Finding | None:
    """How much of the brief lands in the first prompt of a sitting."""
    first, rest = [], []
    for s in sessions:
        seen = False
        for e in s.events:
            if e.kind != "prompt" or not e.text:
                continue
            (first if not seen else rest).append(len(e.text))
            seen = True
    if len(first) < MIN_GROUP or len(rest) < MIN_GROUP:
        return None
    fa, ra = sum(first) / len(first), sum(rest) / len(rest)
    if _lift(fa, ra) < MIN_LIFT:
        return None
    return Finding(
        id="the_opening_prompt",
        text=(
            f"You front-load. The prompt that opens a session averages {round(fa)} characters "
            f"and every one after it averages {round(ra)}, which is {_lift(fa, ra)}x shorter. "
            f"The brief goes in once and the rest of the sitting is steering."
        ),
        left={"group": "first prompt of a session", "n": len(first), "mean_chars": round(fa)},
        right={"group": "every prompt after", "n": len(rest), "mean_chars": round(ra)},
        lift=_lift(fa, ra),
        basis="prompt_text",
    )


def _after_an_interrupt(sessions: Sequence[SessionEvents]) -> Finding | None:
    """Whether stopping the agent makes you explain more."""
    after, other = [], []
    for s in sessions:
        for i, e in enumerate(s.events):
            if e.kind != "prompt" or not e.text:
                continue
            prev_interrupt = i > 0 and s.events[i - 1].kind == "interrupt"
            (after if prev_interrupt else other).append(len(e.text))
    if len(after) < MIN_GROUP or len(other) < MIN_GROUP:
        return None
    aa, oa = sum(after) / len(after), sum(other) / len(other)
    lift = _lift(aa, oa)
    if lift < MIN_LIFT and lift > 1 / MIN_LIFT:
        return None
    longer = lift >= 1
    return Finding(
        id="after_an_interrupt",
        text=(
            f"When you stop the agent mid-run you {'explain yourself' if longer else 'get terse'}: "
            f"the prompt straight after an interrupt averages {round(aa)} characters against "
            f"{round(oa)} for everything else ({len(after)} interrupts in the window)."
        ),
        left={"group": "straight after an interrupt", "n": len(after), "mean_chars": round(aa)},
        right={"group": "every other prompt", "n": len(other), "mean_chars": round(oa)},
        lift=lift,
        basis="prompt_text_after_interrupt",
    )


# -------------------------------------------------------------------------- sessions


def _the_leash(sessions: Sequence[SessionEvents]) -> Finding | None:
    """How long you let it run before saying anything."""
    runs: list[int] = []
    for s in sessions:
        n = 0
        for e in s.events:
            if e.kind == "prompt":
                runs.append(n)
                n = 0
            elif e.kind == "tool":
                n += 1
        runs.append(n)
    runs = [r for r in runs if r > 0]
    if len(runs) < MIN_GROUP:
        return None
    runs.sort()
    median = runs[len(runs) // 2]
    longest = runs[-1]
    if _lift(longest, max(median, 1)) < MIN_LIFT:
        return None
    return Finding(
        id="the_leash",
        text=(
            f"You give it a long leash. Between two of your prompts the agent runs "
            f"{median} tool calls in the median stretch, and your longest hands-off run was "
            f"{longest} calls in a row without a word from you."
        ),
        left={"group": "longest run between prompts", "n": len(runs), "tool_calls": longest},
        right={"group": "median run", "n": len(runs), "tool_calls": median},
        lift=_lift(longest, max(median, 1)),
        basis="tool_calls_between_prompts",
    )


def _night_sessions(sessions: Sequence[SessionEvents]) -> Finding | None:
    """Whether the sessions you start late are different from the ones you start early."""
    night, day = [], []
    for s in sessions:
        h = _local_hour(s.started_at, s.tz_offset_minutes)
        (night if h >= NIGHT_FROM or h < DAY_BOUNDARY_HOUR else day).append(s.active_seconds)
    if len(night) < MIN_GROUP or len(day) < MIN_GROUP:
        return None
    na, da = sum(night) / len(night), sum(day) / len(day)
    lift = _lift(na, da)
    if lift < MIN_LIFT and lift > 1 / MIN_LIFT:
        return None
    return Finding(
        id="night_sessions",
        text=(
            f"The sessions you start after {NIGHT_FROM}:00 run "
            f"{'longer' if lift >= 1 else 'shorter'} than the ones you start in daylight: "
            f"{round(na / 60)} minutes on average against {round(da / 60)} "
            f"({len(night)} night sittings, {len(day)} day)."
        ),
        left={"group": f"started after {NIGHT_FROM}:00", "n": len(night), "mean_minutes": round(na / 60)},
        right={"group": "started in daylight", "n": len(day), "mean_minutes": round(da / 60)},
        lift=lift,
        basis="active_seconds_by_start_hour",
    )


def _verification_habit(sessions: Sequence[SessionEvents]) -> Finding | None:
    """Whether a burst of edits gets tested before you move on."""
    from . import digest as dg

    tested, untested = 0, 0
    for s in sessions:
        pending = False
        for e in s.events:
            if e.kind != "tool":
                continue
            wrote = e.tool in dg.EDIT_TOOLS or (e.tool in dg.SHELL_TOOLS and e.added is not None)
            ran_test = e.tool in dg.SHELL_TOOLS and _TEST_CMD.search(e.text or "")
            if wrote:
                pending = True
            elif ran_test and pending:
                tested += 1
                pending = False
        if pending:
            untested += 1
    total = tested + untested
    if total < MIN_GROUP:
        return None
    share = tested / total
    return Finding(
        id="verification_habit",
        text=(
            f"{_pct(share)} of your editing runs end in a test command "
            f"({tested} of {total} bursts of file writes were followed by a test before the "
            f"next thing you asked for)."
        ),
        left={"group": "edit bursts that ended in a test", "n": total, "count": tested, "share": round(share, 3)},
        right={"group": "edit bursts that did not", "n": total, "count": untested},
        lift=_lift(share, 1 - share) if share < 1 else 0.0,
        basis="test_commands_after_writes",
    )


def _rework(sessions: Sequence[SessionEvents]) -> Finding | None:
    """Files you go back to inside one session."""
    revisits, once = 0, 0
    worst = ("", 0)
    for s in sessions:
        touched: Counter[str] = Counter()
        for e in s.events:
            if e.kind == "tool" and e.path and e.added is not None:
                touched[e.path] += 1
        for path, n in touched.items():
            if n >= 3:
                revisits += 1
                if n > worst[1]:
                    worst = (path, n)
            else:
                once += 1
    total = revisits + once
    if total < MIN_GROUP or revisits == 0:
        return None
    share = revisits / total
    name = worst[0].rsplit("/", 1)[-1]
    return Finding(
        id="rework",
        text=(
            f"{_pct(share)} of the files you touch get rewritten three or more times in the "
            f"same sitting ({revisits} of {total}). The most reworked was {name}, "
            f"{worst[1]} times in one session."
        ),
        left={"group": "files written 3+ times in a session", "n": total, "count": revisits, "share": round(share, 3)},
        right={"group": "files written once or twice", "n": total, "count": once},
        lift=_lift(share, 1 - share) if share < 1 else 0.0,
        basis="write_counts_per_path_per_session",
    )


__all__ = ["Finding", "SessionEvents", "findings", "MIN_GROUP", "MIN_LIFT", "MIN_SHARE_GAP"]
