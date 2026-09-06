"""How long it takes you to get back to green, and what this codebase refuses to guess.

The frameworks everyone quotes are built for teams. DORA measures deploy frequency, lead
time, change failure rate and time to restore; SPACE adds satisfaction and collaboration.
Neither has anything to say to one person and an agent at 2am, and both need a deploy
pipeline this data has never seen.

Two of the four DORA questions ARE answerable from a transcript, and they are the two that
say something about quality rather than speed:

  * **time to green**: a test run fails, and later one passes. That gap is the solo
    builder's mean time to restore, and it is measured from the tool results rather than
    inferred from anything.
  * **how often the first try works**: the share of test runs that passed with no failure
    of the same command before them in the sitting.

## What is REFUSED, and the measurement that forced it

**Change failure rate from commit messages.** The obvious version greps `fix`, `revert`,
`oops`, `broke` out of `git log` and calls the share of matches a failure rate. MEASURED on
this repository, 99 commits in seven days, four matched a fix word, and three of those four
were *intentional* fixes rather than regressions:

    "Fix six defects an adversarial review confirmed"
    "Auth: fix the viewer-less RLS bootstrap, add Google Sign-In"
    "Real tool output: run the real writers, keep what they wrote, fix the four numbers"

A rate built on that is a rate about how somebody writes commit messages. Somebody
disciplined enough to say "fix" when they fix something would score worse than somebody who
writes "wip", which is exactly backwards. There is no honest change failure rate in this
data and this module does not invent one.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Sequence

#: Running the tests. THE ONE DEFINITION: `patterns`, `feedback` and this module all
#: import it, because three regexes that drift produce three different answers to "how
#: often do you test" and no way to tell which is right.
#:
#: The `(?<![\w/.-])` is the whole precision of it. A bare `\bpytest\b` matches
#: `head -1 /root/.local/bin/pytest`, which is somebody LOOKING at the test runner rather
#: than running it. FOUND BY RUNNING IT: that exact command was counted as a test run, and
#: as a recovery from failure when the next one passed.
TEST_CMD = re.compile(
    r"(?<![\w/.-])(pytest|bun test|npm test|swift test|jest|cargo test|go test|make test)\b"
)

#: A gap longer than this is not "getting back to green", it is a different day's work.
#: A test that fails at 6pm and passes at 10am says nothing about how fast anybody fixes
#: anything.
MAX_GAP_SEC = 4 * 3600.0

#: Both numbers need this many observations. Below it one bad afternoon is the metric.
MIN_RUNS = 5


@dataclasses.dataclass(frozen=True)
class Green:
    """One failing test run and the passing one that ended it."""

    failed_at: float
    passed_at: float
    command: str
    #: How many test runs it took, including the first failure and the final pass.
    attempts: int

    @property
    def seconds(self) -> float:
        return self.passed_at - self.failed_at


def recoveries(sessions: Sequence) -> list[Green]:
    """Every failure-to-pass gap. `sessions` are `patterns.SessionEvents`.

    A run that never came back green inside the sitting is NOT recorded: it has no end, and
    the honest thing to do with an unfinished recovery is leave it out rather than close it
    at the session boundary, which would report the fastest possible number for the worst
    possible outcome.
    """
    out: list[Green] = []
    for s in sessions:
        seq = [e for e in s.events if e.kind in ("tool", "result_error")]
        pending: tuple[float, str, int] | None = None
        i = 0
        while i < len(seq):
            e = seq[i]
            if e.kind != "tool" or not TEST_CMD.search(e.text or ""):
                i += 1
                continue
            failed = i + 1 < len(seq) and seq[i + 1].kind == "result_error"
            if failed:
                if pending is None:
                    pending = (e.ts, " ".join((e.text or "").split())[:80], 1)
                else:
                    pending = (pending[0], pending[1], pending[2] + 1)
                i += 2
                continue
            if pending is not None and e.ts - pending[0] <= MAX_GAP_SEC:
                out.append(
                    Green(
                        failed_at=pending[0],
                        passed_at=e.ts,
                        command=pending[1],
                        attempts=pending[2] + 1,
                    )
                )
            pending = None
            i += 1
    return out


def summary(sessions: Sequence) -> dict:
    """Time to green and first-try rate, or a refusal with its reason."""
    runs = passes = fails = 0
    for s in sessions:
        seq = [e for e in s.events if e.kind in ("tool", "result_error")]
        for i, e in enumerate(seq):
            if e.kind != "tool" or not TEST_CMD.search(e.text or ""):
                continue
            runs += 1
            if i + 1 < len(seq) and seq[i + 1].kind == "result_error":
                fails += 1
            else:
                passes += 1

    if runs < MIN_RUNS:
        return {
            "runs": runs,
            "time_to_green": None,
            "first_try_rate": None,
            "reason": f"{runs} test run(s), {MIN_RUNS} needed",
        }

    got = recoveries(sessions)
    times = sorted(g.seconds for g in got)
    return {
        "runs": runs,
        "passed": passes,
        "failed": fails,
        # The share of runs that passed. Not "the first try worked" per feature, which
        # nothing here can see: it is the share of times you ran the tests and they were
        # already green.
        "first_try_rate": round(passes / runs, 3),
        "time_to_green": (
            {
                "n": len(times),
                "median_seconds": round(times[len(times) // 2]),
                "worst_seconds": round(times[-1]),
                "median_attempts": sorted(g.attempts for g in got)[len(got) // 2],
            }
            if times
            else None
        ),
        "reason": None if times else "nothing failed and then passed inside one sitting",
    }


__all__ = ["MAX_GAP_SEC", "MIN_RUNS", "TEST_CMD", "Green", "recoveries", "summary"]
