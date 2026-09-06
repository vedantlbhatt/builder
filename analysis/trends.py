"""How you build NOW against how you built BEFORE. The only comparison that works.

There is no cohort to compare somebody against: this app has no users yet, and even when
it does, "top decile of what" is a question a percentile cannot answer honestly for
something as unlike itself as one person's coding. The comparison that always works, from
the second month onward, is the person against themselves.

WHAT A TREND HAS TO SURVIVE TO BE PRINTED, and each of these has killed a version of this
module:

  1. BOTH WINDOWS NEED A SAMPLE. A rate over two sessions against a rate over forty is a
     number that moves when one sitting does. Both sides need `MIN_SESSIONS`.
  2. THE MOVE HAS TO BEAT THE NOISE. Everything here wobbles week to week. A change under
     `MIN_MOVE` is reported as steady, which is a real and useful answer, not padded into
     a direction with an arrow on it.
  3. A METRIC THE PROFILE REFUSED HAS NO TREND. `corpus_profile` returns None with a
     reason for anything it cannot compute honestly; subtracting None from None and
     calling it flat is how a refusal turns into a claim.
  4. DIRECTION IS NOT VIRTUE. More hours is not better. More tokens is not worse. Each
     metric carries which way is GOOD, or `None` when the answer depends on what the
     person wants, and the wording follows that rather than assuming up is up.

The windows are RECENT and BEFORE: the last `window_days` against the `window_days` before
that, so "this month against last month" is the default and the two are the same length.
Comparing a 30 day window against all history would report the trend of the corpus growing.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping, Sequence

#: Both windows need this many sessions. Below it one sitting moves the number.
MIN_SESSIONS = 4

#: A move smaller than this share of the earlier value is noise, and gets called steady.
#: UNMEASURED JUDGEMENT CALL, and a deliberately blunt one: everything in here wobbles by
#: a tenth week to week without anything having changed about the person.
MIN_MOVE = 0.15

#: Which way is better, for the metrics where there IS a better. Anything absent from this
#: table gets reported as a change with no judgement attached, which is the honest default:
#: more hours is not better, more tokens is not worse, and a night owl is not broken.
BETTER: dict[str, str] = {
    "test_runs_per_hour": "up",
    "ships_rate": "up",
    "code_velocity": "up",
    #: Taking the wheel back less often means the briefs are landing.
    "steer_rate": "down",
    "short_prompt_share": "down",
    #: Dollars per hour going down for the same output is the whole point of model choice.
    "spend_per_hour_usd": "down",
}

#: What to call each metric out loud. A trend nobody can read is not a trend.
LABEL: dict[str, str] = {
    "test_runs_per_hour": "how often you test",
    "ships_rate": "sittings that end with a commit",
    "code_velocity": "lines an hour",
    "steer_rate": "how often you take the wheel back",
    "short_prompt_share": "one line prompts",
    "spend_per_hour_usd": "cost an hour",
    "autonomy_score": "time the agent runs alone",
    "planning_ratio": "planning against doing",
    "night_share": "work after 22:00",
    "tool_diversity": "different tools a sitting",
    "iteration_depth": "tool calls between your messages",
}


@dataclasses.dataclass(frozen=True)
class Trend:
    """One metric, then against now."""

    metric: str
    label: str
    before: float
    now: float
    #: Signed share of the earlier value. +0.4 is "up 40%".
    move: float
    #: "up", "down" or "steady".
    direction: str
    #: True when it moved the good way, False when it moved the bad way, None when this
    #: metric has no better direction and the reader decides.
    good: bool | None
    sessions_before: int
    sessions_now: int

    @property
    def steady(self) -> bool:
        return self.direction == "steady"


def _value(profile: Mapping, metric: str) -> float | None:
    m = (profile.get("metrics") or {}).get(metric) or {}
    v = m.get("value")
    return float(v) if isinstance(v, (int, float)) else None


def compare(before: Mapping, now: Mapping, *, metrics: Sequence[str] | None = None) -> list[Trend]:
    """Every metric both windows could compute, biggest move first.

    `before` and `now` are two `profile.corpus_profile` results over equal windows.
    """
    n_before = ((before.get("sample") or {}).get("sessions")) or 0
    n_now = ((now.get("sample") or {}).get("sessions")) or 0
    if n_before < MIN_SESSIONS or n_now < MIN_SESSIONS:
        return []

    names = metrics if metrics is not None else sorted(set(LABEL) | set(BETTER))
    out: list[Trend] = []
    for metric in names:
        a, b = _value(before, metric), _value(now, metric)
        # A metric either window refused has no trend. Treating a refusal as zero turns
        # "we cannot know" into "it fell to nothing", which is the worst reading of it.
        if a is None or b is None or a == 0:
            continue
        move = (b - a) / abs(a)
        if abs(move) < MIN_MOVE:
            direction, good = "steady", None
        else:
            direction = "up" if move > 0 else "down"
            better = BETTER.get(metric)
            good = None if better is None else (direction == better)
        out.append(
            Trend(
                metric=metric,
                label=LABEL.get(metric, metric.replace("_", " ")),
                before=round(a, 3),
                now=round(b, 3),
                move=round(move, 3),
                direction=direction,
                good=good,
                sessions_before=n_before,
                sessions_now=n_now,
            )
        )
    out.sort(key=lambda t: -abs(t.move))
    return out


def window_words(days: int) -> str:
    """"on last month", "on the week before", said the way a person would.

    The headline used to say "on last month" whatever the window was, so a one day
    comparison announced a monthly trend. The window is a parameter; the sentence has to
    follow it or the number is attached to the wrong span of somebody's life.
    """
    if days <= 1:
        return "on the day before"
    if days <= 10:
        return f"on the {days} days before"
    if days <= 45:
        return "on last month"
    return f"on the {round(days / 30)} months before"


def headline(trends: Sequence[Trend], window_days: int = 30) -> str | None:
    """The one sentence worth putting at the top, or None when nothing moved.

    Prefers a move with a direction that MEANS something: "you test half as often" is
    worth a headline, "you used 20% more tools" is a fact with nobody to tell.
    """
    judged = [t for t in trends if t.good is not None and not t.steady]
    pick = judged[0] if judged else next((t for t in trends if not t.steady), None)
    if pick is None:
        return None
    pct = round(abs(pick.move) * 100)
    verb = "up" if pick.direction == "up" else "down"
    tail = "" if pick.good is None else (", which is the way you want it" if pick.good else "")
    return f"{pick.label.capitalize()} is {verb} {pct}% {window_words(window_days)}{tail}."


__all__ = [
    "BETTER",
    "LABEL",
    "MIN_MOVE",
    "MIN_SESSIONS",
    "Trend",
    "compare",
    "headline",
    "window_words",
]
