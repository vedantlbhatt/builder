"""Your own prompts that worked, and the ones that did not, side by side.

THE FEATURE THIS IS. Everybody using these tools is guessing at how to ask. The advice
online is generic and the only prompts that are actually calibrated to your project, your
stack and your habits are the ones you already typed. Some of them produced a commit
without you touching the wheel; some produced forty tool calls and a correction. Nobody
has ever been shown their own two piles next to each other.

WHAT MAKES A PROMPT "WORKED", and every part of this is measured rather than judged:

  * something LANDED after it: a file written, a test run, or a commit
  * you did not have to take it back: no interrupt, no corrective prompt afterwards
  * it did not stall: the agent did not run past `STALL_TOOL_CALLS` with NOTHING landing

A prompt that scores on all three is one that worked. One that failed the second or third
is one that cost you a round trip. The comparison is the product: not "write better
prompts", but "here are yours, these ones you never had to correct".

Everything here needs PROMPT TEXT, which never leaves the machine
(privacy/upload-contract.json). What the author chooses to publish is their own words.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Sequence

#: A run this long WITH NOTHING LANDING is a prompt that stalled.
#:
#: The first version of this counted a long run as a failure on its own, and it was wrong
#: on the first real corpus: the best prompt in it produced 44 landed things over 484 tool
#: calls and got filed under "cost a round trip". A long autonomous run is what some people
#: are asking for; the failure is a long run with nothing to show, which is a different
#: measurement and the one `patterns.SPIN_TOOL_CALLS` also watches for.
STALL_TOOL_CALLS = 40

#: Below this many words there is nothing to learn from: "fix it" is not a technique.
MIN_PROMPT_WORDS = 6

#: Both piles need this many prompts before showing them side by side means anything.
MIN_GROUP = 3

#: Kept out of both piles: a prompt that is only steering the previous one is not a way
#: of asking for work, it is the cost of the last one. `profile.is_corrective` decides.
_TRAILING = re.compile(r"\s+")


@dataclasses.dataclass(frozen=True)
class Attempt:
    """One typed prompt and what measurably happened after it."""

    session_id: str
    ts: float
    text: str
    #: Files written, tests run and commits made before the next thing the human said.
    landed: int
    tool_calls: int
    #: Did the human take it back: an interrupt, or a corrective prompt next.
    corrected: bool
    stalled: bool

    @property
    def worked(self) -> bool:
        return self.landed > 0 and not self.corrected and not self.stalled

    @property
    def words(self) -> int:
        return len(self.text.split())


def attempts(sessions: Sequence) -> list[Attempt]:
    """Every typed prompt long enough to be a technique, with what followed it.

    `sessions` are `patterns.SessionEvents`.
    """
    from . import patterns as pat
    from .profile import is_corrective

    out: list[Attempt] = []
    for s in sessions:
        events = list(s.events)
        for i, e in enumerate(events):
            if e.kind != "prompt" or not e.text:
                continue
            if len(e.text.split()) < MIN_PROMPT_WORDS:
                continue
            landed = calls = 0
            corrected = False
            for nxt in events[i + 1 :]:
                if nxt.kind == "interrupt":
                    corrected = True
                    break
                if nxt.kind == "prompt":
                    corrected = bool(nxt.text) and is_corrective(nxt.text)
                    break
                if nxt.kind == "tool":
                    calls += 1
                    if pat._wrote(nxt) or pat._committed(nxt) or pat._tested(nxt):
                        landed += 1
            out.append(
                Attempt(
                    session_id=s.session_id,
                    ts=e.ts,
                    text=" ".join(e.text.split()),
                    landed=landed,
                    tool_calls=calls,
                    corrected=corrected,
                    stalled=calls > STALL_TOOL_CALLS and landed == 0,
                )
            )
    return out


def split(all_attempts: Sequence[Attempt]) -> tuple[list[Attempt], list[Attempt]]:
    """(the ones that worked, the ones that cost a round trip), best and worst first.

    Ranked by what LANDED, not by length. "Longer prompts work better" is the conclusion
    every version of this feature reaches if you let it sort by size, and it is the same
    empty advice the internet already gives.
    """
    worked = sorted(
        (a for a in all_attempts if a.worked), key=lambda a: (-a.landed, a.tool_calls)
    )
    cost = sorted(
        (a for a in all_attempts if not a.worked), key=lambda a: (-a.tool_calls, -a.landed)
    )
    return worked, cost


def summary(all_attempts: Sequence[Attempt]) -> dict:
    """The one number the two piles are worth: how often a prompt lands cleanly.

    Refused rather than estimated below `MIN_GROUP` a side, for the reason everything else
    in this package is: a rate over two prompts is not a rate.
    """
    worked, cost = split(all_attempts)
    n = len(all_attempts)
    if len(worked) < MIN_GROUP or len(cost) < MIN_GROUP:
        return {
            "value": None,
            "n": n,
            "reason": (
                f"{len(worked)} clean and {len(cost)} costly, {MIN_GROUP} of each needed"
            ),
        }
    return {
        "value": round(len(worked) / n, 3),
        "n": n,
        "worked": len(worked),
        "cost": len(cost),
        "reason": None,
    }


__all__ = [
    "MIN_GROUP",
    "MIN_PROMPT_WORDS",
    "STALL_TOOL_CALLS",
    "Attempt",
    "attempts",
    "split",
    "summary",
]
