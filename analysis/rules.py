"""The same mistake, made again: recurring errors turned into rules that stop them.

THE FEATURE THIS IS. A person hits an error, fixes it, and three days later hits the same
error in a new session because nothing wrote it down. That is the single most expensive
thing about working with an agent, and it is completely invisible: each session on its own
looks like ordinary debugging, and only the corpus shows it is the fourth time.

This module finds those, and the bar is CROSS-SESSION RECURRENCE. Ten failures inside one
sitting is debugging, which is the job. The same failure in three separate sittings is
something nobody wrote down, and the fix is one line in a rules file (`CLAUDE.md`,
`.cursorrules`, `AGENTS.md`) rather than another twenty minutes.

Nothing here needs a model. The recurrence is arithmetic over error text, and the proposed
rule is drafted separately (`rules_prompt.txt`) from candidates this module produced, never
from a guess about what somebody's project is like.

The whole thing runs on the machine with the transcripts. Error output never leaves it
(privacy/upload-contract.json), and a proposed rule is a file the author edits and keeps.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import pathlib
import re
from collections.abc import Sequence

from . import run as rn

LOG = logging.getLogger(__name__)

HERE = pathlib.Path(__file__).resolve().parent
SCHEMA_PATH = HERE / "rules_schema.json"
PROMPT_PATH = HERE / "rules_prompt.txt"

#: The same error in this many DIFFERENT sittings is a rule nobody wrote down. Two is the
#: smallest number that can mean "again"; below it there is no pattern, only an error.
MIN_SESSIONS = 2

#: How much error text to keep for a signature. Enough to tell two failures apart, short
#: enough that a stack trace's tail does not make every occurrence unique.
SIGNATURE_CHARS = 220

#: Errors this short carry no information to group on ("Exit code 1").
MIN_SIGNATURE_CHARS = 24

#: What gets normalised away before two errors are compared, IN ORDER. Each of these
#: varies between runs of the SAME failure, so leaving any of them in makes every
#: occurrence unique and the recurrence count silently zero.
#:
#: THE ORDER IS LOAD BEARING and it is the one thing here that broke. The uuid rule sat
#: last, so by the time it ran the generic rules had already chewed the uuid up: `0000`
#: had become `<n>` and the long groups `<hex>`, and two ids that should have collapsed
#: into one signature stayed different. Anything specific has to run BEFORE anything
#: general, or the general rule eats its input.
_NOISE: tuple[tuple[re.Pattern, str], ...] = (
    # Specific first. A uuid, whole, before any digit or hex rule can bite into it.
    (
        re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"),
        "<id>",
    ),
    # Absolute paths, which differ per machine, per checkout and per temp directory.
    (re.compile(r"(/[\w.\-]+){2,}/?"), "<path>"),
    # Timings, ports, line and column numbers, counts, durations.
    (re.compile(r"\b\d+(\.\d+)?(ms|s|m|h|%)\b"), "<n>"),
    (re.compile(r"\b0x[0-9a-fA-F]+\b"), "<hex>"),
    (re.compile(r"\b[0-9a-f]{8,}\b"), "<hex>"),
    (re.compile(r"\b\d+\b"), "<n>"),
    (re.compile(r"\s+"), " "),
)


@dataclasses.dataclass(frozen=True)
class Recurrence:
    """One failure that happened in more than one sitting."""

    signature: str
    #: How many separate sittings hit it. THE number: the count within one sitting is
    #: debugging, the count across sittings is a rule nobody wrote down.
    sessions: int
    #: How many times in total, which says how expensive it was.
    occurrences: int
    #: What the tool was called, verbatim, from the first time it happened.
    command: str
    #: What came back, verbatim and trimmed. The author has to recognise it.
    error: str
    tool: str
    first_seen: float
    last_seen: float
    #: Seconds between the first time and the last, which is how long it went unwritten.
    @property
    def span_seconds(self) -> float:
        return max(0.0, self.last_seen - self.first_seen)


def signature(text: str) -> str | None:
    """A fingerprint two occurrences of the same failure share, or None if too thin.

    Normalising is the whole trick and getting it wrong fails SILENTLY in the safe
    direction: leave a path or a line number in and every occurrence is unique, the
    recurrence count is zero, and the feature reports that you never repeat yourself.
    """
    one = " ".join((text or "").split())[:SIGNATURE_CHARS]
    if len(one) < MIN_SIGNATURE_CHARS:
        return None
    for pattern, replacement in _NOISE:
        one = pattern.sub(replacement, one)
    one = one.strip().lower()
    return one if len(one) >= MIN_SIGNATURE_CHARS else None


def _pairs(events: Sequence):
    """(call, error) for every tool call whose result was an error.

    A failure is the `result_error` event AFTER the call, not a flag on the call itself
    (analysis/patterns.py, found by running it on a corpus with 128 errors and no loops).
    """
    seq = [e for e in events if e.kind in ("tool", "result_error")]
    for i, e in enumerate(seq):
        if e.kind == "tool" and i + 1 < len(seq) and seq[i + 1].kind == "result_error":
            yield e, seq[i + 1]


def recurring(sessions: Sequence, min_sessions: int = MIN_SESSIONS) -> list[Recurrence]:
    """Every failure that happened in more than one sitting, most sittings first.

    `sessions` are `patterns.SessionEvents`.
    """
    seen: dict[str, dict] = {}
    for s in sessions:
        here: set[str] = set()
        for call, err in _pairs(s.events):
            sig = signature(err.text)
            if sig is None:
                continue
            row = seen.setdefault(
                sig,
                {
                    "sessions": set(),
                    "occurrences": 0,
                    "command": " ".join((call.text or call.tool or "").split())[:300],
                    "error": " ".join((err.text or "").split())[:400],
                    "tool": call.tool or "?",
                    "first": err.ts,
                    "last": err.ts,
                },
            )
            row["occurrences"] += 1
            row["first"] = min(row["first"], err.ts)
            row["last"] = max(row["last"], err.ts)
            here.add(sig)
        for sig in here:
            seen[sig]["sessions"].add(s.session_id)

    out = [
        Recurrence(
            signature=sig,
            sessions=len(row["sessions"]),
            occurrences=row["occurrences"],
            command=row["command"],
            error=row["error"],
            tool=row["tool"],
            first_seen=row["first"],
            last_seen=row["last"],
        )
        for sig, row in seen.items()
        if len(row["sessions"]) >= min_sessions
    ]
    out.sort(key=lambda r: (-r.sessions, -r.occurrences))
    return out


def build_input(*, project: str, recurrences: Sequence[Recurrence]) -> str:
    """The candidates, worst first. Nothing else: the model proposes a rule per failure,
    and a failure it was not shown is a rule it must not write."""
    lines = [
        f"PROJECT: {project}",
        "",
        "FAILURES THAT HAPPENED IN MORE THAN ONE SITTING. One proposed rule each, at most.",
        "",
    ]
    for i, r in enumerate(recurrences, 1):
        lines.append(f"[{i}] {r.sessions} separate sittings, {r.occurrences} times in total")
        lines.append(f"    tool: {r.tool}")
        lines.append(f"    ran:  {r.command[:200]}")
        lines.append(f"    got:  {r.error[:300]}")
        lines.append("")
    return "\n".join(lines)


def load_schema() -> dict:
    schema = json.loads(SCHEMA_PATH.read_text())
    for k in ("$schema", "$comment", "x-version"):
        schema.pop(k, None)
    return schema


def verify(doc: dict, recurrences: Sequence[Recurrence]) -> tuple[dict, list[str]]:
    """Drop any rule for a candidate that was not in the input.

    A rule numbered 9 out of a list of six is a rule about a failure that never happened,
    and it would go into a file that steers every future session. The `candidate` index is
    also what lets the CLI print the evidence under each rule, so a rule that does not
    point at one has nothing behind it either way.
    """
    kept, dropped = [], []
    for r in doc.get("rules") or []:
        i = r.get("candidate")
        if isinstance(i, int) and 1 <= i <= len(recurrences):
            kept.append(r)
        else:
            dropped.append(f"{r.get('rule', '')!r}  [candidate {i} was not in the input]")
    doc["rules"] = kept
    return doc, dropped


def write(
    *,
    project: str,
    recurrences: Sequence[Recurrence],
    model: str = rn.DEFAULT_MODEL,
) -> dict:
    """Propose a rules-file line per recurring failure, through the user's own `claude`."""
    source = build_input(project=project, recurrences=recurrences)
    doc, envelope = rn.call_claude(PROMPT_PATH.read_text(), source, load_schema(), model)
    doc, dropped = verify(doc, recurrences)
    for claim in dropped:
        LOG.warning("rules: dropped a rule for a failure that was not shown: %s", claim)
    doc, dashes = rn.dedash(doc)
    doc["dashes_rewritten"] = dashes
    doc["invented_rules_dropped"] = len(dropped)
    return doc


__all__ = [
    "MIN_SESSIONS",
    "Recurrence",
    "build_input",
    "load_schema",
    "recurring",
    "signature",
    "verify",
    "write",
]
