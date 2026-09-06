"""Postable cards: the analysis, cut into things somebody would actually put in a feed.

THE BAR CHANGED AND SO DID EVERYTHING ELSE. `profile.py` and `patterns.py` were built
against "would you act on this". This module is built against a harder and more honest
test: **would a stranger scrolling past stop for it.** Most true sentences about somebody's
coding fail that test. "You test after 93% of your editing runs" is a fine private
observation and a dead post. "$0.50 a commit on Sonnet, $10 on Opus" is an argument, and
an argument is a feed.

WHAT MAKES A CARD POSTABLE, and these are judgement calls, labelled as such:

  * A CONFESSION beats a flex. "2h11m watching it go nowhere" gets replies; "I am very
    disciplined" gets scrolled. The `kind` weights below start from that.
  * A number a stranger can calibrate. Dollars and multiples travel; `steer_rate 0.433`
    does not, and a reader who has to ask what a unit means has already moved on.
  * Something to disagree with. Model comparisons are the native argument of this
    audience, so they are weighted above everything else on purpose.

Nothing here computes a statistic. Every number comes from the profile or a finding that
already cleared its sample-size and effect bars, and every card carries its working so a
reader can open it. A card whose number would need explaining does not get made.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping, Sequence

from . import pricing

#: What kind of thing this is, and how far a card of this kind starts up the ranking.
#: UNMEASURED JUDGEMENT CALLS, every one, and the reason they are here rather than inline
#: is so that the day there IS engagement data these become four numbers to fit.
KIND_WEIGHT: dict[str, float] = {
    #: Something that went wrong, said plainly. The most repliable thing a person can post.
    "confession": 0.60,
    #: A comparison other people will argue with. Model wars are this audience's home turf.
    "argument": 0.58,
    #: Something another person can copy tonight.
    "tip": 0.45,
    #: A number that is good. Real, and the least interesting to anybody else.
    "flex": 0.30,
    #: A personal record. Interesting to the poster, rarely to a stranger.
    "milestone": 0.20,
}

#: The weights leave headroom on purpose. The bumps below add up to 0.35, so a kind that
#: started at 0.95 saturated at the cap the moment it had a dollar sign, and a 20x model
#: comparison scored exactly the same as a 2x one. A scale where everything ties is not a
#: ranking. FOUND BY RUNNING IT against the ordering test.
MAX_BUMP = 0.35

#: A card scoring under this is not worth putting in front of anyone. A bare flex sits
#: exactly here: it ships, last, and only because it is true.
MIN_POSTABLE = 0.30

#: The headline has to fit a card without wrapping to four lines on a phone.
HEADLINE_MAX = 90
DETAIL_MAX = 180


@dataclasses.dataclass(frozen=True)
class Card:
    """One postable thing, with the working behind it."""

    id: str
    kind: str
    #: What is on the card, big. Second person is wrong here: a post is written by its
    #: author about themselves, so headlines are first person or bare.
    headline: str
    #: One line under it. May be empty when the headline already carries the whole thing.
    detail: str
    #: Every number in the card, so the app can show the working and a test can check it.
    numbers: dict
    #: How far up the feed composer this should sit. See `score`.
    postability: float

    @property
    def postable(self) -> bool:
        return self.postability >= MIN_POSTABLE


def score(kind: str, *, has_money: bool = False, multiple: float = 1.0) -> float:
    """How postable a card is, from its kind and the shape of its number.

    Two bumps on top of the kind weight, both for the same reason: a stranger needs to be
    able to calibrate the number without being told the units. A dollar figure needs no
    explanation anywhere on earth. A multiple ("3x") is self-describing in a way that a
    rate ("5.3 per active hour") is not, and it grows to a cap so that a 40x does not
    outrank everything else in the feed forever.
    """
    base = KIND_WEIGHT.get(kind, 0.5)
    if has_money:
        base += 0.15
    if multiple >= 2.0:
        base += min(0.2, 0.05 * multiple)
    return round(min(base, 1.0), 3)


def _finding(findings: Sequence, fid: str):
    return next((f for f in findings if f.id == fid), None)


def _metric(profile: Mapping, name: str):
    m = (profile.get("metrics") or {}).get(name) or {}
    return m.get("value"), m


def cards(profile: Mapping, findings: Sequence = ()) -> list[Card]:
    """Every card this corpus can honestly produce, most postable first."""
    out: list[Card] = []
    for build in (
        _model_bill,
        _the_spin,
        _the_loop,
        _the_night_shift,
        _what_it_cost_to_build,
        _the_rewrite,
        _the_discipline,
    ):
        card = build(profile, findings)
        if card is not None and card.postable:
            out.append(card)
    out.sort(key=lambda c: (-c.postability, c.id))
    return out


# ------------------------------------------------------------------------- the cards


def _model_bill(profile: Mapping, findings: Sequence) -> Card | None:
    """The cheapest model against the dearest, per commit. The argument card."""
    rows = [r for r in (profile.get("model_costs") or []) if r.get("usd_per_commit")]
    if len(rows) < 2:
        return None
    rows.sort(key=lambda r: r["usd_per_commit"])
    cheap, dear = rows[0], rows[-1]
    multiple = round(dear["usd_per_commit"] / cheap["usd_per_commit"], 1)
    if multiple < 2:
        return None
    return Card(
        id="model_bill",
        kind="argument",
        headline=(
            f"{cheap['model']} lands me a commit for "
            f"{pricing.money(cheap['usd_per_commit'])}. {dear['model']}: "
            f"{pricing.money(dear['usd_per_commit'])}."
        ),
        detail=(
            f"{multiple}x, at API list prices, over {cheap['commits']} commits against "
            f"{dear['commits']}. Counting only the sittings each one actually wrote."
        ),
        numbers={"cheap": cheap, "dear": dear, "multiple": multiple},
        postability=score("argument", has_money=True, multiple=multiple),
    )


def _the_spin(profile: Mapping, findings: Sequence) -> Card | None:
    """Time spent watching it go nowhere. The confession card."""
    f = _finding(findings, "the_spin")
    if f is None:
        return None
    hours = f.left["total_seconds"] / 3600
    if hours < 0.5:
        return None
    worst = f.left["worst_tool_calls"]
    normal = f.right["median_tool_calls"]
    return Card(
        id="the_spin",
        kind="confession",
        headline=f"{_hm(f.left['total_seconds'])} of watching it go nowhere this month.",
        detail=(
            f"{f.left['n']} runs where nothing was written, tested or committed. Worst was "
            f"{worst} tool calls in a row against a normal {normal}."
        ),
        numbers=dict(f.left, normal_tool_calls=normal),
        postability=score("confession", multiple=worst / max(normal, 1)),
    )


def _the_loop(profile: Mapping, findings: Sequence) -> Card | None:
    """The failure loop. Everybody has seen this one, which is why it plays."""
    f = _finding(findings, "stuck_in_a_loop")
    if f is None or f.left["longest_run"] < 5:
        return None
    return Card(
        id="the_loop",
        kind="confession",
        headline=f"It failed {f.left['longest_run']} times in a row before I stepped in.",
        detail=f"{f.left['n']} runs like that, {_hm(f.left['total_seconds'])} between them.",
        numbers=dict(f.left),
        postability=score("confession"),
    )


def _the_night_shift(profile: Mapping, findings: Sequence) -> Card | None:
    """Rate per hour by time of day. An argument disguised as a stat."""
    f = _finding(findings, "when_the_work_lands")
    if f is None:
        return None
    late = f.left["lines_per_active_hour"]
    day = f.right["lines_per_active_hour"]
    multiple = round(max(late, day) / max(min(late, day), 1), 1)
    if multiple < 1.5:
        return None
    better = late > day
    return Card(
        id="the_night_shift",
        kind="argument",
        headline=(
            f"After 22:00 I land {round(late)} lines an hour. In daylight, {round(day)}."
            if better
            else f"My late sessions land {round(late)} lines an hour. Daylight: {round(day)}."
        ),
        detail=(
            f"{f.left['n']} late sittings against {f.right['n']} in daylight. "
            + ("The night shift is real." if better else "The night shift is not free.")
        ),
        numbers={"late": f.left, "day": f.right, "multiple": multiple},
        postability=score("argument", multiple=multiple),
    )


def _what_it_cost_to_build(profile: Mapping, findings: Sequence) -> Card | None:
    """Lines and dollars together. The flex, with a price tag so it is not just a brag."""
    spend, _ = _metric(profile, "spend_usd")
    lines = (profile.get("totals") or {}).get("total_lines_added")
    commits = (profile.get("totals") or {}).get("total_commits")
    if not spend or not lines:
        return None
    return Card(
        id="what_it_cost_to_build",
        kind="flex",
        headline=(
            f"{lines:,} lines and {commits} commits for {pricing.money(spend)} of API usage."
            if commits
            else f"{lines:,} lines for {pricing.money(spend)} of API usage."
        ),
        detail=(
            f"{pricing.money(spend / max(lines, 1) * 1000)} per thousand lines, at list "
            f"prices. Not a bill: most of us are on a subscription."
        ),
        numbers={"usd": spend, "lines": lines, "commits": commits},
        postability=score("flex", has_money=True),
    )


def _the_rewrite(profile: Mapping, findings: Sequence) -> Card | None:
    """One file, fought with. Relatable, and it names a file, which makes it real."""
    f = _finding(findings, "fighting_one_file")
    if f is None:
        return None
    writes = f.left["worst_file_writes"]
    if writes < 5:
        return None
    return Card(
        id="the_rewrite",
        kind="confession",
        headline=f"Rewrote the same file {writes} times in one sitting.",
        detail=f"{_hm(f.left['worst_file_seconds'])} on one file. It needed a decision, not another attempt.",
        numbers=dict(f.left),
        postability=score("confession", multiple=writes / 3),
    )


def _the_discipline(profile: Mapping, findings: Sequence) -> Card | None:
    """The test habit. Included, ranked low, and honest about why: it is a real thing
    about a person and almost nobody stops scrolling for somebody else's good habit."""
    f = _finding(findings, "verification_habit")
    if f is None or f.left["share"] < 0.8:
        return None
    return Card(
        id="the_discipline",
        kind="flex",
        headline=f"{round(f.left['share'] * 100)}% of my edits end in a test before I move on.",
        detail=f"{f.left['count']} of {f.left['n']} editing runs.",
        numbers=dict(f.left),
        postability=score("flex"),
    )


def _hm(seconds: float) -> str:
    m = round(seconds / 60)
    return f"{m} minutes" if m < 60 else f"{m // 60}h {m % 60:02d}m"


__all__ = ["Card", "HEADLINE_MAX", "KIND_WEIGHT", "MIN_POSTABLE", "cards", "score"]
