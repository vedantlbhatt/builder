"""Several agents at once: who ran, for how long, and whether the delegation paid off.

THE GAP THIS FILLS. Every tool that reads these transcripts reads the ROOT one and stops.
That is correct for counting: a subagent's tokens are already reported in aggregate by the
parent's `Agent` tool result, so adding the sidecars inflates the bill (CLAUDE.md, the
globbing revert). It is also why nobody can tell you anything about the agents themselves,
and on this container that is 12,236 records and 119 distinct agent instances of the work
being completely invisible.

This module reads them, and it NEVER contributes a token, a line or a commit to any total.
It answers a different question: how many agents, of what kind, running at once or one
after another, doing what, and did handing that piece off actually work.

WHERE THE DATA IS. `<projectdir>/<session uuid>/subagents/agent-<agentId>.jsonl`, beside
the root transcript. Every record carries `agentId` (the instance), `sessionId` (the
parent sitting) and `isSidechain: true`. The parent's `Agent` tool call carries
`subagent_type` and a `description`, which is what the agent was ASKED to do, and that is
the half that makes the rest legible.

CONSECUTIVE AND CONCURRENT ARE DIFFERENT QUESTIONS and both matter:

  * consecutive: a chain of handoffs, each one a decision the person made
  * concurrent: agents overlapping in time, which is the thing that makes an hour of wall
    clock into four hours of work, and the thing that makes a session impossible to read
    as a single timeline

`parallelism` below is the ratio of agent-seconds to wall seconds, which is the honest
statement of that: 1.0 is one agent at a time, 4.0 is four hours of agent work inside one
hour of your life.
"""

from __future__ import annotations

import collections
import dataclasses
import json
import pathlib
import re
from collections.abc import Iterable, Sequence

#: `<projectdir>/<session uuid>/subagents/agent-<id>.jsonl`. An ALLOWLIST on path shape,
#: never a denylist on the directory name: the tree has sibling `workflows/` and
#: `tool-results/` directories and a denylist waves them through (CLAUDE.md).
SIDECAR_DIR = "subagents"
_SESSION_DIR = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

#: Two agents overlapping by less than this are a handoff with a ragged edge, not work
#: happening at the same time. UNMEASURED JUDGEMENT CALL: a second is the resolution of
#: the timestamps and a handoff routinely straddles one.
MIN_OVERLAP_SEC = 2.0


@dataclasses.dataclass(frozen=True)
class AgentSpan:
    """One agent instance: when it ran, what it was for, what it touched."""

    agent_id: str
    #: `general-purpose`, `workflow-subagent`, `Explore`, or None when the sidecar never
    #: said. Never guessed from the description.
    agent_type: str | None
    #: What the parent ASKED for, from the `Agent` tool call that spawned it. None when
    #: the spawn could not be matched, which is a real state and not an empty string.
    asked: str | None
    started_at: float
    ended_at: float
    records: int
    tool_calls: int
    #: Tool calls that wrote a file, ran a test or made a commit. The delegation's output.
    landed: int
    #: Tool calls whose result was an error.
    failures: int

    @property
    def seconds(self) -> float:
        return max(0.0, self.ended_at - self.started_at)

    @property
    def produced(self) -> bool:
        """Did handing this piece off actually produce anything."""
        return self.landed > 0


@dataclasses.dataclass(frozen=True)
class Fanout:
    """What the agents in one sitting did, together."""

    agents: int
    #: The most that were running at the same moment. 1 means everything was consecutive.
    max_concurrent: int
    #: Total seconds of agent work, which can far exceed the sitting itself.
    agent_seconds: float
    wall_seconds: float
    by_type: dict[str, int]
    spans: tuple[AgentSpan, ...]

    @property
    def parallelism(self) -> float:
        """Agent-seconds per wall second. 1.0 is one at a time, 4.0 is four at once."""
        return round(self.agent_seconds / self.wall_seconds, 2) if self.wall_seconds > 0 else 0.0

    @property
    def produced(self) -> int:
        return sum(1 for s in self.spans if s.produced)


def sidecar_paths(root_transcript: pathlib.Path) -> list[pathlib.Path]:
    """Every subagent file belonging to one root transcript.

    A root transcript is `<projectdir>/<uuid>.jsonl`; its subagents live in the directory
    named for the same uuid. Matching on that shape rather than searching for the id
    anywhere keeps a second session's sidecars out.
    """
    session_dir = root_transcript.parent / root_transcript.stem
    if not _SESSION_DIR.match(session_dir.name) or not session_dir.is_dir():
        return []
    sub = session_dir / SIDECAR_DIR
    return sorted(sub.glob("*.jsonl")) if sub.is_dir() else []


def _delegations(root_transcript: pathlib.Path) -> list[tuple[float, str, str | None]]:
    """(timestamp, description, subagent_type) for every `Agent` call in the parent.

    Read from the ROOT transcript, because that is where the asking happened. The
    subagent's own file records what it did and never what it was for.
    """
    out: list[tuple[float, str, str | None]] = []
    for rec in _iter_records(root_transcript):
        msg = rec.get("message")
        if not isinstance(msg, dict):
            continue
        for block in msg.get("content") or []:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            if block.get("name") not in ("Agent", "Task"):
                continue
            inp = block.get("input") if isinstance(block.get("input"), dict) else {}
            desc = inp.get("description") or inp.get("prompt") or ""
            out.append(
                (
                    rec["_ts"],
                    " ".join(str(desc).split())[:200],
                    inp.get("subagent_type") if isinstance(inp.get("subagent_type"), str) else None,
                )
            )
    out.sort()
    return out


def _iter_records(path: pathlib.Path) -> Iterable[dict]:
    """Timestamped records, with the partial trailing line NEVER consumed.

    A sidecar is appended to while it is read exactly like a root transcript, so the last
    line is routinely half written and committing an offset mid-line loses it forever
    (CLAUDE.md).
    """
    import datetime as dt

    try:
        fh = path.open("rb")
    except OSError:
        return
    with fh:
        for line in fh:
            if not line.endswith(b"\n"):
                break  # partial trailing line: never consumed
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            raw = rec.get("timestamp")
            if not isinstance(raw, str):
                continue
            try:
                ts = dt.datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
            except ValueError:
                continue
            rec["_ts"] = ts
            yield rec


def _tool_blocks(rec: dict) -> list[dict]:
    msg = rec.get("message")
    if not isinstance(msg, dict):
        return []
    return [
        b
        for b in (msg.get("content") or [])
        if isinstance(b, dict) and b.get("type") == "tool_use"
    ]


_LANDED = re.compile(r"\b(git commit|pytest|bun test|npm test|swift test|make test|cargo test)\b")


def _is_landed(block: dict) -> bool:
    """Did this call write a file, run a test or make a commit."""
    from . import digest as dg

    name = block.get("name") or ""
    if name in dg.EDIT_TOOLS:
        return True
    if name in dg.SHELL_TOOLS:
        inp = block.get("input") if isinstance(block.get("input"), dict) else {}
        cmd = str(inp.get("command", ""))
        return bool(_LANDED.search(cmd)) or ">" in cmd
    return False


def spans(root_transcript: pathlib.Path) -> list[AgentSpan]:
    """One `AgentSpan` per agent instance spawned by this sitting, in start order."""
    asked = _delegations(root_transcript)
    used: set[int] = set()
    out: list[AgentSpan] = []

    for path in sidecar_paths(root_transcript):
        first = last = None
        agent_id = path.stem.removeprefix("agent-")
        agent_type: str | None = None
        records = calls = landed = failures = 0
        for rec in _iter_records(path):
            records += 1
            first = rec["_ts"] if first is None else min(first, rec["_ts"])
            last = rec["_ts"] if last is None else max(last, rec["_ts"])
            if isinstance(rec.get("agentId"), str):
                agent_id = rec["agentId"]
            if isinstance(rec.get("attributionAgent"), str):
                agent_type = rec["attributionAgent"]
            if rec.get("type") == "user" and _has_error(rec):
                failures += 1
            for block in _tool_blocks(rec):
                calls += 1
                if _is_landed(block):
                    landed += 1
        if first is None or last is None:
            continue

        # Match the delegation: the latest `Agent` call at or before this agent's first
        # record, each used once. Time order is the only link the files give, and a
        # first-come claim keeps two agents spawned in the same turn from sharing a brief.
        brief = None
        for i, (ts, desc, sub_type) in enumerate(asked):
            if i in used or ts > first:
                continue
            brief = (i, desc, sub_type)
        if brief is not None:
            used.add(brief[0])
            if agent_type is None:
                agent_type = brief[2]

        out.append(
            AgentSpan(
                agent_id=agent_id,
                agent_type=agent_type,
                asked=brief[1] if brief else None,
                started_at=first,
                ended_at=last,
                records=records,
                tool_calls=calls,
                landed=landed,
                failures=failures,
            )
        )
    out.sort(key=lambda s: (s.started_at, s.agent_id))
    return out


def _has_error(rec: dict) -> bool:
    msg = rec.get("message")
    if not isinstance(msg, dict):
        return False
    for b in msg.get("content") or []:
        if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("is_error"):
            return True
    return False


def fanout(agent_spans: Sequence[AgentSpan], wall_seconds: float) -> Fanout:
    """How much of the sitting was several agents at once.

    Concurrency is a sweep over the interval endpoints rather than a pairwise check: with
    a dozen agents the pairwise version is both slower and easy to get wrong at the
    boundaries, and the sweep gives the exact maximum by construction.
    """
    events: list[tuple[float, int]] = []
    for s in agent_spans:
        if s.seconds < MIN_OVERLAP_SEC:
            # A span shorter than the timestamp resolution cannot be shown to overlap
            # anything; it still counts as an agent, just not as concurrency.
            continue
        events.append((s.started_at, 1))
        events.append((s.ended_at, -1))
    # Ends before starts at the same instant: a handoff is not two agents at once.
    events.sort(key=lambda e: (e[0], e[1]))
    current = peak = 0
    for _, delta in events:
        current += delta
        peak = max(peak, current)

    by_type: collections.Counter[str] = collections.Counter()
    for s in agent_spans:
        by_type[s.agent_type or "unknown"] += 1

    return Fanout(
        agents=len(agent_spans),
        max_concurrent=max(peak, 1 if agent_spans else 0),
        agent_seconds=sum(s.seconds for s in agent_spans),
        wall_seconds=wall_seconds,
        by_type=dict(by_type),
        spans=tuple(agent_spans),
    )


__all__ = ["AgentSpan", "Fanout", "MIN_OVERLAP_SEC", "fanout", "sidecar_paths", "spans"]
