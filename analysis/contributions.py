"""Your commit history, split by whether an agent was in the room.

A contributions graph is a wall of squares that says you were busy. Everybody has one and
nobody learns anything from it. The question this machine can answer and GitHub cannot is
the one underneath: **which of these commits landed while an agent was working, and which
did you write on your own.**

That is not a judgement. Somebody whose share is climbing is changing how they work, and
whichever direction they want that to go, they cannot steer it without seeing it. And it
is the one number here that a person can check against their own memory, which makes the
rest of the profile more believable rather than less.

WHY NOT THE GITHUB API. It needs an OAuth round trip to tell you less: it cannot see work
you have not pushed, it cannot see private repositories you did not grant, and it has no
idea which commits were agent-assisted. `git log` on the machine that did the work knows
all three. The repositories are the ones this machine's own transcripts already resolved
to, which is knowledge rather than a guess (the Aider default-root lesson, CLAUDE.md).

THE ATTRIBUTION RULE, and it is deliberately generous to "alone": a commit counts as
agent-assisted when it lands inside a session window extended `LOOKBACK_SEC` before the
start, which is the same window `profile.attribute_commits` uses for its counts. Anything
outside every window is yours. A commit made in a session the capture never saw is
therefore counted as yours, which is the safe direction: overstating how much an agent did
is the claim nobody could check and everybody would resent.
"""

from __future__ import annotations

import collections
import dataclasses
import datetime as dt
from collections.abc import Sequence

#: A commit made shortly before a sitting's first record still belongs to it: the agent
#: wrote the code, the person committed it as the session was starting. Same constant the
#: per-session counts use, imported rather than re-chosen.
from .profile import COMMIT_ATTRIBUTION_SEC as LOOKBACK_SEC

#: The day boundary. 04:00 everywhere in this product: at 00:20 mid-session the menu bar
#: read "0s active today", which was technically correct and completely wrong.
DAY_BOUNDARY_HOUR = 4

#: A share needs this many commits under it. Below it one commit moves it by a fifth.
MIN_COMMITS = 5


@dataclasses.dataclass(frozen=True)
class Day:
    """One local day of commits, split."""

    day: dt.date
    assisted: int
    alone: int

    @property
    def total(self) -> int:
        return self.assisted + self.alone


@dataclasses.dataclass(frozen=True)
class Contributions:
    """The graph, and the one number worth reading off it."""

    days: tuple[Day, ...]
    assisted: int
    alone: int
    #: Local days with at least one commit. The squares that are lit.
    active_days: int
    #: Longest run of consecutive lit days. A streak of days you SHIPPED, not days you
    #: opened the app: a streak that a tab can extend is a streak about the tab.
    longest_streak: int
    current_streak: int

    @property
    def total(self) -> int:
        return self.assisted + self.alone

    @property
    def assisted_share(self) -> float | None:
        """None below `MIN_COMMITS`: a share over three commits is not a share."""
        return round(self.assisted / self.total, 3) if self.total >= MIN_COMMITS else None


def local_day(ts: float, tz_offset_minutes: int) -> dt.date:
    """The local day a moment belongs to, with the day starting at 04:00."""
    local = dt.datetime.fromtimestamp(ts, dt.UTC) + dt.timedelta(minutes=tz_offset_minutes)
    return (local - dt.timedelta(hours=DAY_BOUNDARY_HOUR)).date()


def _windows(sessions: Sequence[tuple[float, float]]) -> list[tuple[float, float]]:
    """Session windows, merged, so a commit in two overlapping sittings is checked once."""
    spans = sorted((start - LOOKBACK_SEC, end) for start, end in sessions)
    merged: list[list[float]] = []
    for start, end in spans:
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(a, b) for a, b in merged]


def split(
    commits: Sequence[float],
    sessions: Sequence[tuple[float, float]],
    tz_offset_minutes: int = 0,
) -> Contributions:
    """The whole graph. `commits` are unix times; `sessions` are (started, ended) pairs."""
    windows = _windows(sessions)
    by_day: dict[dt.date, list[int]] = collections.defaultdict(lambda: [0, 0])
    assisted = alone = 0
    for ts in commits:
        inside = any(a <= ts <= b for a, b in windows)
        day = local_day(ts, tz_offset_minutes)
        by_day[day][0 if inside else 1] += 1
        if inside:
            assisted += 1
        else:
            alone += 1

    days = tuple(
        Day(day=d, assisted=v[0], alone=v[1]) for d, v in sorted(by_day.items())
    )
    longest, current = _streaks([d.day for d in days])
    return Contributions(
        days=days,
        assisted=assisted,
        alone=alone,
        active_days=len(days),
        longest_streak=longest,
        current_streak=current,
    )


def _streaks(days: Sequence[dt.date]) -> tuple[int, int]:
    """(longest run, the run still going). Both count days you SHIPPED.

    "Still going" allows for today not being over: a streak that ended yesterday is still
    current until a day passes without a commit, or every streak in the world would break
    every morning before the first commit.
    """
    if not days:
        return 0, 0
    longest = run = 1
    for prev, cur in zip(days, days[1:], strict=False):
        run = run + 1 if (cur - prev).days == 1 else 1
        longest = max(longest, run)
    today = local_day(dt.datetime.now(dt.UTC).timestamp(), 0)
    current = run if (today - days[-1]).days <= 1 else 0
    return longest, current


__all__ = [
    "DAY_BOUNDARY_HOUR",
    "LOOKBACK_SEC",
    "MIN_COMMITS",
    "Contributions",
    "Day",
    "local_day",
    "split",
]
