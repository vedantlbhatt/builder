"""What this ONE sitting cost you, measured, for the card you read once and close.

`patterns.py` compares groups of sessions and needs a corpus to say anything. That is the
right bar for a profile and the wrong one for the screen you look at when a session ends:
there is exactly one session, the comparison groups do not exist, and "not enough data" is
the least useful thing an app can say about the work you just did.

So this asks a different question, one a single sitting can answer: **where did the time
in THIS session go that you would not have chosen.** Nothing here is a comparison and
nothing here needs a second session.

THE BAR IS DIFFERENT AND STATED. A profile finding must clear a sample-size and an effect
bar because it is a claim about a person. A session note is a claim about one hour that
the reader was present for, so the bar is only: **is it big enough to have been worth their
attention while it was happening.** If they would not have interrupted, it does not go on
the card.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence

#: A stretch with no write, test or commit worth mentioning on a single card. Higher than
#: `patterns.SPIN_TOOL_CALLS` on purpose: the profile is looking for a habit across
#: sittings and can afford to notice a 25 call run, and a card that flags one of those
#: every session is a card people stop reading.
NOTABLE_SPIN_CALLS = 40

#: And it has to have cost real time, not just calls. Forty fast greps is not a problem.
NOTABLE_SPIN_SECONDS = 300.0

#: Consecutive failures worth a line.
NOTABLE_FAILURES = 4

#: One file written this many times in one sitting was being fought with.
NOTABLE_REWRITES = 5

#: Below this the session is too short for any of it to mean anything.
MIN_TOOL_CALLS = 15


@dataclasses.dataclass(frozen=True)
class Note:
    """One thing about this sitting worth a line on the card."""

    id: str
    #: Second person, one sentence, with the number in it.
    text: str
    #: Seconds it cost, when that is knowable. 0 when the note is not about time.
    seconds: float
    numbers: dict


def notes(session) -> list[Note]:
    """Everything about this one sitting worth saying, most expensive first.

    `session` is a `patterns.SessionEvents`.
    """
    from . import patterns as pat

    calls = sum(1 for e in session.events if e.kind == "tool")
    if calls < MIN_TOOL_CALLS:
        return []

    out: list[Note] = []

    # A sitting where this parser can barely see a checkpoint cannot support the spin
    # note, and this guard was missing until the payload was built for the first time.
    # FOUND BY RUNNING IT: 38 of the 45 boundary fixtures came back "the agent ran a long
    # way with nothing to show", one of them for 22 hours. Those fixtures are synthetic
    # and contain no write, test or commit at all, so every stretch in them looks like
    # spinning — and a REAL harness whose transcripts hide file writes (an edit made by a
    # script the agent wrote is a Bash call with no line count anywhere in it) would
    # produce exactly the same card. That is a statement about the parser printed as a
    # statement about the person, on the screen people read most.
    #
    # `patterns.MIN_CHECKPOINT_DENSITY` is the same bar the corpus finding uses, applied
    # here per sitting. Importing it rather than choosing a second number: two thresholds
    # for one question would disagree about the same session on two screens.
    checkpoints = sum(1 for e in session.events if e.kind == "tool" and pat._checkpoint(e))
    can_see_progress = checkpoints / calls >= pat.MIN_CHECKPOINT_DENSITY

    # --- the agent ran a long way with nothing to show
    spins = (
        [
            (n, secs)
            for n, secs in pat._runs_with_nothing_to_show(session)
            if n >= NOTABLE_SPIN_CALLS and secs >= NOTABLE_SPIN_SECONDS
        ]
        if can_see_progress
        else []
    )
    if spins:
        worst_calls, worst_secs = max(spins)
        lost = sum(s for _, s in spins)
        out.append(
            Note(
                id="went_nowhere",
                text=(
                    f"{len(spins)} stretch{'' if len(spins) == 1 else 'es'} with nothing "
                    f"written, tested or committed. The longest ran {worst_calls} tool "
                    f"calls over {_mins(worst_secs)}."
                    + (f" {_mins(lost)} in total." if len(spins) > 1 else "")
                ),
                seconds=lost,
                numbers={"runs": len(spins), "worst_calls": worst_calls, "seconds": round(lost)},
            )
        )

    # --- it kept failing the same way
    longest, secs, what = _worst_failure_run(session)
    if longest >= NOTABLE_FAILURES:
        out.append(
            Note(
                id="failed_in_a_row",
                text=(
                    f"{longest} failures in a row on `{what}` before anything changed, "
                    f"over {_mins(secs)}."
                ),
                seconds=secs,
                numbers={"failures": longest, "seconds": round(secs), "what": what},
            )
        )

    # --- one file, over and over
    worst_file, writes, span = _most_rewritten(session)
    if worst_file and writes >= NOTABLE_REWRITES:
        out.append(
            Note(
                id="one_file_over_and_over",
                text=(
                    f"{worst_file} was rewritten {writes} times across {_mins(span)}. "
                    f"A file on its fifth pass usually needs a decision, not another attempt."
                ),
                seconds=span,
                numbers={"file": worst_file, "writes": writes, "seconds": round(span)},
            )
        )

    out.sort(key=lambda n: -n.seconds)
    return out


def wire(session) -> list[dict] | None:
    """The uploadable form of `notes`: an id, seconds and a count. NOTHING ELSE.

    THE TWO THINGS DROPPED HERE ARE DROPPED ON PURPOSE, and both are in the local note:

      * the failing COMMAND. On this machine the note says "4 failures in a row on
        `bun test`". Terminal commands are on privacy/upload-contract.json's never-list
        and stay there.
      * the FILE NAME. The local note names the file rewritten five times. `analysis` may
        name files because it is opt-in and bounded; this field is neither.

    The SENTENCE is not sent either — the client writes it from the id — so a reworded
    note is a client change and not a re-upload of everybody's history.

    None, never `[]`: a sitting with nothing worth saying and a sitting the parser could
    not read must not look the same on the card, and only one of them has a row.
    """
    out = [
        {"id": n.id, "seconds": int(round(n.seconds)), "count": _count(n)} for n in notes(session)
    ]
    return out or None


def _count(n: Note) -> int:
    """The one integer that makes each note mean something, by note.

    Read off `numbers` by name rather than positionally: the three notes count three
    different things, and a shared "count" that meant stretches in one and failures in
    another would be a plausible wrong number on the card with no error anywhere.
    """
    if n.id == "went_nowhere":
        return int(n.numbers["runs"])
    if n.id == "failed_in_a_row":
        return int(n.numbers["failures"])
    return int(n.numbers["writes"])


def _worst_failure_run(session) -> tuple[int, float, str]:
    """(longest run of consecutive failures, its seconds, what kept failing).

    A failure is the `result_error` event AFTER the call, not a flag on the call itself.
    Walking both kinds and testing `ok` counts the call as a success and its own error as
    a separate failure, so a run never reaches two (found by running it on a corpus with
    128 errors and no reported loops).
    """
    from . import digest as dg

    seq = [e for e in session.events if e.kind in ("tool", "result_error")]
    run = 0
    start = None
    best = (0, 0.0, "")
    i = 0
    while i < len(seq):
        e = seq[i]
        if e.kind != "tool":
            i += 1
            continue
        failed = i + 1 < len(seq) and seq[i + 1].kind == "result_error"
        if failed:
            run += 1
            if start is None:
                start = e.ts
            if run > best[0]:
                label = e.text if (e.tool in dg.SHELL_TOOLS and e.text) else (e.tool or "it")
                best = (run, e.ts - start, " ".join(str(label).split())[:60])
            i += 2
            continue
        run, start = 0, None
        i += 1
    return best


def _most_rewritten(session) -> tuple[str | None, int, float]:
    from . import patterns as pat

    times: dict[str, list[float]] = {}
    for e in session.events:
        if pat._wrote(e) and e.path:
            times.setdefault(e.path, []).append(e.ts)
    if not times:
        return None, 0, 0.0
    path, stamps = max(times.items(), key=lambda kv: len(kv[1]))
    return path.rsplit("/", 1)[-1], len(stamps), max(stamps) - min(stamps)


def _mins(seconds: float) -> str:
    m = round(seconds / 60)
    if m < 1:
        return "under a minute"
    if m < 60:
        return f"{m} minute{'' if m == 1 else 's'}"
    return f"{m // 60}h {m % 60:02d}m"


__all__ = [
    "MIN_TOOL_CALLS",
    "NOTABLE_FAILURES",
    "NOTABLE_REWRITES",
    "NOTABLE_SPIN_CALLS",
    "NOTABLE_SPIN_SECONDS",
    "Note",
    "notes",
    "wire",
]
