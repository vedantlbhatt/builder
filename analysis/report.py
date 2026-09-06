"""THE BUILDER REPORT: the second of the two documents, and the one about the person.

docs/analysis-complete.md draws the line these two live on. The session card says what
just happened in one sitting. This says how somebody builds, whether it is changing, and
what they could do about it — and it is the document that has to survive being read twice
a month without going stale, because a profile nobody revisits is a signup screen.

WHAT IS IN IT, AND WHY EACH BLOCK EARNED ITS PLACE:

  trends         you against you, two equal windows. The only comparison available: there
                 is no cohort, and "top decile of what" is not a question a percentile can
                 answer for one person's coding.
  agents         how many subagents ran and how much of it was at once. Read from sidecar
                 transcripts that every other tool on this machine skips, and it never
                 contributes a token, a line or a commit to any total.
  contributions  commits split by whether an agent was in the room. The question a wall of
                 green squares cannot answer and this machine can.
  quality        the two of DORA's four questions a transcript can answer, and the module
                 that refuses the other two rather than greping `fix` out of git log.
  prompting      how often a prompt lands cleanly. A COUNT AND NOTHING ELSE.

WHAT IT DELIBERATELY LEAVES OUT. `analysis/rules.py` turns recurring failures into
CLAUDE.md lines, and those lines quote ERROR TEXT, which carries paths and file names. It
stays on the machine and is written to a file there. The playbook splits PROMPTS by
whether they worked; only the two counts travel, so not one word of a prompt appears in
this document. That is the same line privacy/upload-contract.json draws everywhere else,
and it is drawn here rather than at the server because the server never sees the input.

NULL IS NOT ZERO, in every block. Each one is None when it was refused, and the refusals
carry their reason, because a person looking at an empty chart concludes the product is
broken and a person reading "5 test runs, 5 needed" concludes they should run the tests.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence

from . import playbook as pb_mod
from . import quality as q_mod
from . import trends as tr_mod

#: The window every block looks back over, and the length of EACH of the two trend
#: windows. Thirty days is one month of habit; the trend then reaches sixty days back,
#: which is why the report is not honest about somebody's first month and says so by
#: returning no trends rather than comparing a fortnight against a fortnight.
DEFAULT_WINDOW_DAYS = 30

#: The spec version. `spec/report.v1.json` is the only other place this number appears,
#: and `scripts/gen_report.py` copies it into both generated halves.
REPORT_VERSION = 1

#: Caps from the spec, restated where the document is BUILT rather than only where it is
#: validated. A corpus with two years of commits would otherwise produce a document the
#: server rejects with a 422 the user cannot act on, months after anyone touched this.
MAX_TRENDS = 24
MAX_AGENT_TYPES = 12
MAX_DAYS = 400


def build(
    *,
    trends: Sequence = (),
    fanout=None,
    contributions=None,
    sessions: Sequence = (),
    window_days: int = DEFAULT_WINDOW_DAYS,
    generated_at: float | None = None,
) -> dict:
    """Assemble the report from what the machine already measured.

    `trends`, `fanout` and `contributions` arrive already computed, because both callers
    (`python -m analysis report` and `python -m capture report`) compute them for the
    narrative too and computing them twice is how two commands come to describe one corpus
    with two different numbers. `quality` and `prompting` are derived here, from the same
    `sessions`, because they are pure functions of the events and nothing else wants them.
    """
    ts = dt.datetime.fromtimestamp(generated_at or dt.datetime.now().timestamp(), dt.UTC)
    trimmed = list(trends)[:MAX_TRENDS]
    return {
        "report_version": REPORT_VERSION,
        "generated_at": ts.isoformat().replace("+00:00", "Z"),
        "window_days": window_days,
        "trend_headline": tr_mod.headline(trimmed, window_days) if trimmed else None,
        "trends": [_trend(t) for t in trimmed],
        "agents": _agents(fanout),
        "contributions": _contributions(contributions),
        "quality": _quality(sessions),
        "prompting": _prompting(sessions),
    }


def _trend(t) -> dict:
    return {
        "metric": t.metric,
        "label": t.label,
        "before": round(float(t.before), 4),
        "now": round(float(t.now), 4),
        "move": round(float(t.move), 4),
        "direction": t.direction,
        "good": t.good,
        "sessions_before": t.sessions_before,
        "sessions_now": t.sessions_now,
    }


def _agents(fanout) -> dict | None:
    """The fan-out block, or None when this person has never delegated.

    `produced` is the number worth having beside `agents`: an agent that ran and did
    nothing at all cost tokens and returned air, and the difference between 52 agents and
    51 that produced something is the difference between a boast and a measurement. It is
    `Fanout`'s own property rather than a second count of the same thing here.
    """
    if fanout is None or not fanout.agents:
        return None
    by_type = sorted(fanout.by_type.items(), key=lambda kv: (-kv[1], kv[0]))
    return {
        "agents": fanout.agents,
        "produced": fanout.produced,
        "max_concurrent": fanout.max_concurrent,
        "agent_seconds": round(fanout.agent_seconds, 1),
        "wall_seconds": round(fanout.wall_seconds, 1),
        "busy_seconds": round(fanout.busy_seconds, 1),
        "parallelism": round(fanout.parallelism, 2),
        "by_type": [
            {"name": name, "agents": n} for name, n in by_type[:MAX_AGENT_TYPES]
        ],
    }


def _contributions(c) -> dict | None:
    """Commits by day, most recent `MAX_DAYS` of them.

    The tail is what gets dropped, not the head: a graph missing last week is a broken
    graph, and one missing the same week two years ago is a graph.
    """
    if c is None:
        return None
    days = list(c.days)[-MAX_DAYS:]
    return {
        "assisted": c.assisted,
        "alone": c.alone,
        "active_days": c.active_days,
        "longest_streak": c.longest_streak,
        "current_streak": c.current_streak,
        "days": [
            {"day": d.day.isoformat(), "assisted": d.assisted, "alone": d.alone} for d in days
        ],
    }


def _quality(sessions: Sequence) -> dict | None:
    """Time to green and first try rate, or the refusal with the count that forced it."""
    if not sessions:
        return None
    s = q_mod.summary(sessions)
    green = s.get("time_to_green")
    return {
        "runs": s["runs"],
        "passed": s.get("passed"),
        "failed": s.get("failed"),
        "first_try_rate": s.get("first_try_rate"),
        "time_to_green": (
            {
                "n": green["n"],
                "median_seconds": green["median_seconds"],
                "worst_seconds": green["worst_seconds"],
                "median_attempts": green["median_attempts"],
            }
            if green
            else None
        ),
        "reason": s.get("reason"),
    }


def _prompting(sessions: Sequence) -> dict | None:
    """How often a prompt lands cleanly. THE COUNTS ONLY.

    `playbook.attempts` carries the prompt TEXT — that is the whole point of it on the
    machine, where it prints the two piles for somebody to read. Nothing below touches
    `.text`, and this is the only function in this package that reads attempts and is
    also uploaded.
    """
    if not sessions:
        return None
    s = pb_mod.summary(pb_mod.attempts(sessions))
    return {
        "attempts": s["n"],
        "clean": s.get("worked"),
        "costly": s.get("cost"),
        "clean_share": s.get("value"),
        "reason": s.get("reason"),
    }


__all__ = [
    "DEFAULT_WINDOW_DAYS",
    "MAX_AGENT_TYPES",
    "MAX_DAYS",
    "MAX_TRENDS",
    "REPORT_VERSION",
    "build",
]
