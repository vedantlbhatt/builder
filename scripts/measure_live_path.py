#!/usr/bin/env python3
"""Measure what the live-path walk classifies as rewound, and whether it is right.

`LivePathResolver.liveEventIDs` (Packages/BuilderKit/Sources/BuilderParse) decides which
events are on the conversation's surviving branch. Everything off it is "rewound": its
edits, tool calls and strip segments are dropped and its tokens are reported apart as
`abandonedBranchTokens`. A wrong answer here is the quiet kind — a live session's Edit
lines silently missing from the card.

    scripts/measure_live_path.py                      # roots under ~/.claude/projects + fixtures
    scripts/measure_live_path.py path/to/one.jsonl    # one file
    scripts/measure_live_path.py --sidecars           # also every sidecar under the projects root
    scripts/measure_live_path.py --json               # machine-readable
    scripts/measure_live_path.py --write-fixture      # (re)write spec/fixtures/live_path/*.jsonl

THE RULE AS FOUND (LivePathResolver.swift, read 2026-09-05), ported to Python record-for-record:

  node      one transcript record. Each record's events carry `nativeEventID = uuid + suffix`
            (`#tu0`, `#th1`, `#tx0`, `#tr0`; no suffix for a bare prompt/noise event) and
            `nativeParentID = parentUuid ?? logicalParentUuid`. A record with no `uuid` gets
            `l<ordinal>`. The walk indexes on the BASE id (everything before `#`), keeping the
            first event per base, so one node per record.
  linked    number of events with a parent id. Zero linked => no DAG (Cursor, Codex, Gemini)
            => every event is live.
  leaf      a node whose base id is claimed as parent by nobody.
  live leaf the leaf with the greatest `ts` (records without a timestamp sort as -inf), ties
            broken by the greater ordinal.
  walk      from the live leaf follow parent ids to a root, on base ids, with a guardrail of
            events+1 hops and a stop on the first repeated id. Every base id visited is live.
  bookkeeping  every event with NO parent whose kind is not substantive (turnDuration,
            compaction, title, noise, unknown) marks its base live — it was never on a branch.
  rewound   everything else. Expanded back to per-block ids: a live record's blocks are live.

The consequence measured here: the walk visits ONE parent per hop, so at a fork it keeps
exactly one child and files every other child's subtree as rewound. The original finding
(225 fork points on the reference corpus) attributed forks to rewinds and message edits. On
the transcripts in this container Claude Code also forks the DAG INSIDE a live turn, in
three shapes, none of which a human undid:

  1. parallel tool calls, result dead-ends   the assistant's blocks chain A -> B (same
     `message.id`); the tool_result for A is written as a child of A, B's result as a child
     of B, and the turn continues from B's result. A's result is a leaf the walk never
     visits. If A was an Edit its lines vanish; if a Read, the file is not "touched".
  2. parallel tool calls, block dead-ends     the mirror image: the turn continues from A's
     result, and block B plus B's result hang off A. B's tool call and its result vanish,
     and B's usage row (when it is the first for that message id) becomes "abandoned".
  3. stop hook                                a stop hook blocks the stop; the harness
     writes a `user` (isMeta) "Stop hook feedback" record F, the assistant continues from
     F, AND a `system/stop_hook_summary` record S is written as a second child of F at the
     same timestamp. The NEXT human turn attaches to S, not to the assistant's last record,
     so the entire continuation — 250 records and 32 minutes of committed work in the
     largest case below — is off the path.

  4. a second root                            one file, two conversations: a headless
     `claude -p` run repeated under the same session id writes two prompts with
     `parentUuid: null`. Only the tree holding the latest leaf is walked; the other tree —
     a real tool call — is "rewound" although nobody went back.
  5. a queued message                         `queue-operation` records carry a timestamp
     but no `uuid` and no parent. One appended after the last conversation record is a
     LEAF, and the newest one, so it is elected the live leaf; the walk covers only itself
     and the WHOLE session is rewound. Measured on the largest root while a message was
     queued: 1,957 of 2,589 records, 1,233 substantive, 411 tool calls, 283 usage rows —
     and `on_live_path` is written once, at ingest, so the damage depends on timing.

THE RULE AS FIXED (`--rule fixed`, the default prints both): a rewind is a human act, and
the DAG is a forest. Every root (no parent, or a parent not in the file) heads a tree that is
resolved from ITS latest leaf. At a fork on a resolved path, the surviving child is either a
human presence record (a prompt or an interrupt — the human went back to this point and
continued differently, so the other children were abandoned) or it is not (the harness wrote
a sibling, so the other children are live too, and are resolved the same way from their own
latest leaf). Shape 1's dead-end result is a sibling of an assistant block; shape 2's block
is a sibling of a tool_result; shape 3's continuation is a sibling of a system record; shapes
4 and 5 are trees of their own. A genuine rewind stays rewound because its surviving child is
the resubmitted prompt, and a rewind never makes a new root. Cheaper discriminators were checked
and rejected: timestamps (in shape 2 the surviving child is LATER than the abandoned block,
exactly like a rewind) and tool_use_id/message.id matching (shapes 3 and 4 share neither).
The old "bookkeeping with no parent is live" clause is subsumed: such a record is a root.

Columns:
  records    JSONL records in the file
  uuids      distinct base uuids
  live/rew   records on / off the live path under the rule
  subst      rewound records with at least one substantive event
  sib_msg    rewound records whose message key (message.id ?? requestId) a LIVE record shares
  sib_uuid   rewound records whose base uuid a LIVE record shares (impossible by construction;
             measured so the claim is a number)
  sib_tool   rewound tool_result records answering a LIVE tool_use          (shape 1)
  harness    rewound records under a fork whose surviving child is NOT a human record
             (shapes 1-3: what the fixed rule rescues)
  tree       rewound records in a tree other than the latest leaf's        (shapes 4, 5)
  human      rewound records under a fork whose surviving child IS a prompt or interrupt
             (a genuine rewind: what the fixed rule must leave alone)
  tu         tool_use blocks on rewound records (dropped from tool counts)
  lines      lines added by Edit/Write results on rewound records (dropped from agent lines)
  msgs       usage-authoritative records on rewound records (moved to abandonedBranchTokens)
  leaf       type of the elected live leaf; `!` if it has no parent link at all
"""

from __future__ import annotations

import argparse
import datetime as dt
import itertools
import json
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from capture.discover import iter_root_transcripts

BOUNDARY_FIXTURES = REPO / "spec" / "fixtures" / "boundaries"
LIVE_PATH_FIXTURES = REPO / "spec" / "fixtures" / "live_path"

INTERRUPT_PREFIX = "[Request interrupted by user"
SYNTHETIC_MODEL = "<synthetic>"
SUBSTANTIVE = {"prompt", "interrupt", "assistantMessage", "thinking", "toolUse", "toolResult", "humanEdit"}
NOISE_TYPES = {
    "mode", "permission-mode", "file-history-snapshot", "file-history-delta", "relocated",
    "worktree-state", "agent-name", "agent-color", "bridge-session", "queue-operation",
    "frame-link", "pr-link", "started", "result", "fork-context-ref",
}


def parse_ts(s: str | None) -> float | None:
    if not s:
        return None
    try:
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def base_id(i: str) -> str:
    return i.split("#", 1)[0]


def content_blocks(content) -> list[dict]:
    """`.message.content` is a plain String on 3,299 records; the parser reads it as one text block."""
    if isinstance(content, list):
        return [b for b in content if isinstance(b, dict)]
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return []


class Node:
    """One record, with exactly what the resolver and the accountants read from it."""

    __slots__ = (
        "id", "kinds", "lines_added", "message_key", "ordinal", "parent", "tool_result_ids",
        "tool_use_ids", "ts", "type", "usage_authoritative",
    )

    def __init__(self, ordinal: int, r: dict, seen_usage: set[str]):
        self.ordinal = ordinal
        self.type = r.get("type") or ""
        uuid = r.get("uuid") if isinstance(r.get("uuid"), str) else None
        self.id = uuid or f"l{ordinal}"
        p = r.get("parentUuid") or r.get("logicalParentUuid")
        self.parent = p if isinstance(p, str) else None
        self.ts = parse_ts(r.get("timestamp"))
        self.kinds: list[str] = []
        self.message_key: str | None = None
        self.tool_use_ids: list[str] = []
        self.tool_result_ids: list[str] = []
        self.usage_authoritative = False
        self.lines_added = 0
        msg = r.get("message") if isinstance(r.get("message"), dict) else {}

        if self.type == "user":
            blocks = content_blocks(msg.get("content"))
            texts = [b.get("text") or "" for b in blocks if b.get("type") == "text"]
            is_meta = r.get("isMeta") is True
            src = r.get("promptSource")
            origin = r.get("origin") if isinstance(r.get("origin"), dict) else {}
            is_prompt = not is_meta and (
                src == "typed" or (src == "sdk" and origin.get("kind") == "human"))
            is_interrupt = bool(texts) and "\n".join(texts).startswith(INTERRUPT_PREFIX)
            res = r.get("toolUseResult") if isinstance(r.get("toolUseResult"), dict) else None
            for b in blocks:
                if b.get("type") != "tool_result":
                    continue
                self.kinds.append("toolResult")
                if isinstance(b.get("tool_use_id"), str):
                    self.tool_result_ids.append(b["tool_use_id"])
                if res is not None:
                    patch = res.get("structuredPatch")
                    if isinstance(patch, list) and patch:
                        for hunk in patch:
                            for line in (hunk.get("lines") or []) if isinstance(hunk, dict) else []:
                                if isinstance(line, str) and line.startswith("+"):
                                    self.lines_added += 1
                    elif res.get("type") == "create" and isinstance(res.get("content"), str):
                        c = res["content"]
                        self.lines_added += 0 if c == "" else c.count("\n") + 1
            if is_prompt:
                self.kinds.append("prompt")
            elif is_interrupt:
                self.kinds.append("interrupt")
            elif not self.kinds:
                self.kinds.append("noise")

        elif self.type == "assistant":
            model = msg.get("model") or None
            synthetic = model == SYNTHETIC_MODEL
            mid = msg.get("id") or r.get("requestId") or None
            self.message_key = mid if isinstance(mid, str) else None
            if (self.message_key and not synthetic and isinstance(msg.get("usage"), dict)
                    and self.message_key not in seen_usage):
                seen_usage.add(self.message_key)
                self.usage_authoritative = True
            blocks = content_blocks(msg.get("content"))
            for b in blocks:
                bt = b.get("type")
                if bt == "tool_use":
                    self.kinds.append("toolUse")
                    if isinstance(b.get("id"), str):
                        self.tool_use_ids.append(b["id"])
                elif bt == "thinking":
                    self.kinds.append("thinking")
                elif bt == "text":
                    self.kinds.append("assistantMessage")
                else:
                    self.kinds.append("unknown")
            if not blocks:
                self.kinds.append("assistantMessage")

        elif self.type == "system":
            sub = r.get("subtype") or ""
            self.kinds.append(
                "turnDuration" if sub == "turn_duration"
                else "compaction" if sub == "compact_boundary" else "noise")
        elif self.type == "attachment":
            att = r.get("attachment") if isinstance(r.get("attachment"), dict) else {}
            self.kinds.append("humanEdit" if att.get("type") == "edited_text_file" else "noise")
        elif self.type in ("ai-title", "last-prompt"):
            self.kinds.append("title")
        elif self.type in NOISE_TYPES:
            self.kinds.append("noise")
        else:
            self.kinds.append("unknown")

    @property
    def substantive(self) -> bool:
        return any(k in SUBSTANTIVE for k in self.kinds)

    @property
    def has_bookkeeping_event(self) -> bool:
        return any(k not in SUBSTANTIVE for k in self.kinds)


def load(path: pathlib.Path) -> list[Node]:
    nodes: list[Node] = []
    seen_usage: set[str] = set()
    with open(path, encoding="utf-8") as fh:
        for i, line in enumerate(fh):
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue  # the parser counts it as malformed and moves on
            if not isinstance(r, dict):
                continue
            nodes.append(Node(i, r, seen_usage))
    return nodes


HUMAN = {"prompt", "interrupt"}


def live_bases(nodes: list[Node], rule: str) -> tuple[set[str], Node | None]:
    """`LivePathResolver.liveEventIDs`, at record granularity. Returns (live base ids, leaf)."""
    by_id: dict[str, Node] = {}
    claimed: set[str] = set()
    children: dict[str, list[Node]] = {}
    linked = 0
    for n in nodes:
        b = base_id(n.id)
        if b not in by_id:
            by_id[b] = n
        if n.parent is not None:
            claimed.add(base_id(n.parent))
            children.setdefault(base_id(n.parent), []).append(n)
            linked += 1

    if linked == 0:
        return {base_id(n.id) for n in nodes}, None

    def latest(cands: list[Node]) -> Node:
        return max(cands, key=lambda n: (n.ts if n.ts is not None else float("-inf"), n.ordinal))

    leaves = [n for n in by_id.values() if base_id(n.id) not in claimed]
    if not leaves:
        return {base_id(n.id) for n in nodes}, None
    leaf = latest(leaves)

    def path_up(start: Node, stop_at: str | None, live: set[str]) -> list[Node]:
        """`start` back to the root (or to `stop_at`, inclusive), on base ids, guarded."""
        out: list[Node] = []
        cursor: Node | None = start
        guardrail = len(nodes) + 1
        while cursor is not None and guardrail > 0:
            guardrail -= 1
            b = base_id(cursor.id)
            if b in live:
                break
            live.add(b)
            out.append(cursor)
            if b == stop_at:
                break
            cursor = by_id.get(base_id(cursor.parent)) if cursor.parent is not None else None
        return out

    def latest_leaf_under(k: Node, live: set[str]) -> Node | None:
        stack = [k]
        seen: set[str] = set()
        sub_leaves: list[Node] = []
        while stack:
            x = stack.pop()
            bx = base_id(x.id)
            if bx in seen or bx in live:
                continue
            seen.add(bx)
            kids = children.get(bx, [])
            if kids:
                stack.extend(kids)
            else:
                sub_leaves.append(x)
        return latest(sub_leaves) if sub_leaves else None

    live: set[str] = set()
    if rule == "found":
        path_up(leaf, None, live)
        for n in nodes:
            if n.parent is None and n.has_bookkeeping_event:
                live.add(base_id(n.id))
    else:
        # (where the walk stops, the leaf to walk from). One entry per tree of the forest;
        # the fork rule below adds one per rescued sibling subtree.
        roots = [n for n in by_id.values()
                 if n.parent is None or base_id(n.parent) not in by_id]
        pending: list[tuple[str | None, Node]] = []
        for r in roots:
            sub_leaf = latest_leaf_under(r, live)
            if sub_leaf is not None:
                pending.append((None, sub_leaf))
        while pending:
            stop_at, sub_leaf = pending.pop()
            path = path_up(sub_leaf, stop_at, live)  # leaf first, root last
            for below, fork in itertools.pairwise(path):
                others = [k for k in children.get(base_id(fork.id), []) if base_id(k.id) not in live]
                if not others:
                    continue
                if any(k in HUMAN for k in below.kinds):
                    continue  # the human continued from `fork` in a new direction: rewound
                for k in others:
                    # Resolve the sibling subtree from ITS latest leaf, bounded at `k`.
                    sub_leaf = latest_leaf_under(k, live)
                    if sub_leaf is not None:
                        pending.append((base_id(k.id), sub_leaf))
    return live, leaf


def measure(path: pathlib.Path, rule: str) -> dict:
    nodes = load(path)
    live, leaf = live_bases(nodes, rule)
    is_live = [base_id(n.id) in live for n in nodes]
    live_nodes = [n for n, ok in zip(nodes, is_live) if ok]
    rew = [n for n, ok in zip(nodes, is_live) if not ok]
    live_msgs = {n.message_key for n in live_nodes if n.message_key}
    live_uuids = {base_id(n.id) for n in live_nodes}
    live_tu = {t for n in live_nodes for t in n.tool_use_ids}
    # For each rewound record: was the fork that abandoned it a human's doing?
    by_id = {base_id(n.id): n for n in nodes}
    children: dict[str, list[Node]] = {}
    for n in nodes:
        if n.parent is not None:
            children.setdefault(base_id(n.parent), []).append(n)
    harness = human = tree = 0
    for n in rew:
        cur: Node | None = n
        while cur is not None and base_id(cur.id) not in live:
            cur = by_id.get(base_id(cur.parent)) if cur.parent is not None else None
        surviving = [] if cur is None else [
            k for k in children.get(base_id(cur.id), []) if base_id(k.id) in live]
        if not surviving:
            tree += 1  # no live continuation above it: a tree of its own
        elif any(kk in HUMAN for k in surviving for kk in k.kinds):
            human += 1
        else:
            harness += 1
    return {
        "name": path.name[:12],
        "path": str(path),
        "records": len(nodes),
        "uuids": len({base_id(n.id) for n in nodes if not n.id.startswith("l")}),
        "live": len(live_nodes),
        "rew": len(rew),
        "subst": sum(1 for n in rew if n.substantive),
        "sib_msg": sum(1 for n in rew if n.message_key and n.message_key in live_msgs),
        "sib_uuid": sum(1 for n in rew if base_id(n.id) in live_uuids),
        "sib_tool": sum(1 for n in rew if any(t in live_tu for t in n.tool_result_ids)),
        "tu": sum(len(n.tool_use_ids) for n in rew),
        "lines": sum(n.lines_added for n in rew),
        "msgs": sum(1 for n in rew if n.usage_authoritative),
        "harness": harness,
        "tree": tree,
        "human": human,
        "rew_types": sorted({n.type for n in rew}),
        "leaf": (leaf.type + ("!" if leaf.parent is None else "")) if leaf else "-",
        "rewound_ids": sorted(base_id(n.id) for n in rew),
    }


COLUMNS = [
    ("name", 13), ("records", 7), ("uuids", 6), ("live", 6), ("rew", 5), ("subst", 5),
    ("sib_msg", 7), ("sib_uuid", 8), ("sib_tool", 8), ("harness", 7), ("tree", 4),
    ("human", 5), ("tu", 4), ("lines", 5), ("msgs", 4), ("leaf", 10),
]


def print_table(title: str, rows: list[dict]) -> None:
    print(f"\n== rule: {title} ==")
    print("  ".join(f"{c:<{w}}" if c == "name" else f"{c:>{w}}" for c, w in COLUMNS))
    for r in rows:
        cells = []
        for c, w in COLUMNS:
            v = r[c]
            cells.append(f"{v:<{w}}" if c == "name" else f"{v:>{w}}")
        print("  ".join(cells))
    tot = {c: sum(r[c] for r in rows) for c, _ in COLUMNS if c not in ("name", "leaf")}
    print("  ".join(
        f"{'TOTAL':<{w}}" if c == "name" else f"{'':>{w}}" if c == "leaf" else f"{tot[c]:>{w}}"
        for c, w in COLUMNS))
    print(f"rewound: {tot['harness']} under harness forks, {tot['tree']} in other trees, "
          f"{tot['human']} under human forks (genuine rewinds)")


# --- The synthetic transcript: one genuine rewind, one parallel tool batch ---------------

SYNTH_SESSION = "00000000-0000-4000-8000-00000000000a"
SYNTH_CWD = "/Users/dev/proj"


def synthetic_rewind_records() -> list[dict]:
    """Eleven records. `u2` is a genuine rewind: the human went back to `u1` and resubmitted,
    abandoning `a1 -> r1 -> a2` (an Edit that added 3 lines and was undone). `a3`/`a3b` is a
    parallel tool batch in the live turn, written the way Claude Code writes one: block `a3b`
    chains off block `a3`, `r3` (the result for `a3`'s Edit, +5 lines) hangs off `a3` as a
    dead-end leaf, and the conversation continues from `r4`."""
    t0 = dt.datetime(2026, 3, 10, 13, 0, 0, tzinfo=dt.timezone.utc)

    def ts(sec: float) -> str:
        t = t0 + dt.timedelta(seconds=sec)
        return t.strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond // 1000:03d}Z"

    def rec(kind: str, uuid: str, parent: str | None, sec: float, **extra) -> dict:
        d = {"type": kind, "uuid": uuid, "parentUuid": parent, "sessionId": SYNTH_SESSION,
             "timestamp": ts(sec), "cwd": SYNTH_CWD, "version": "2.1.0", "isSidechain": False}
        d.update(extra)
        return d

    def user(uuid, parent, sec, text):
        return rec("user", uuid, parent, sec, promptSource="typed",
                   message={"role": "user", "content": text})

    def result(uuid, parent, sec, tool_use_id, tool_use_result):
        return rec("user", uuid, parent, sec, toolUseResult=tool_use_result,
                   message={"role": "user", "content": [
                       {"type": "tool_result", "tool_use_id": tool_use_id, "content": "ok"}]})

    def assistant(uuid, parent, sec, mid, block, usage, index=0):
        return rec("assistant", uuid, parent, sec, requestId="req_" + mid, apiBlockIndex=index,
                   message={"role": "assistant", "model": "claude-sonnet-5", "id": mid,
                            "content": [block], "usage": usage})

    def edit(tid, path):
        return {"type": "tool_use", "id": tid, "name": "Edit",
                "input": {"file_path": path, "old_string": "", "new_string": ""}}

    def patch(n):
        return {"filePath": SYNTH_CWD + "/greet.py",
                "structuredPatch": [{"lines": [f"+line {i}" for i in range(n)]}]}

    return [
        user("u1", None, 0, "add a greeting"),
        assistant("a1", "u1", 2, "msg_A", edit("tuA", SYNTH_CWD + "/greet.py"),
                  {"input_tokens": 100, "output_tokens": 10}),
        result("r1", "a1", 3, "tuA", patch(3)),
        assistant("a2", "r1", 4, "msg_B", {"type": "text", "text": "Done."},
                  {"input_tokens": 120, "output_tokens": 5}),
        # The rewind: parent is u1 again. Everything under a1 is now off the path.
        user("u2", "u1", 30, "add a greeting, in French"),
        assistant("a3", "u2", 32, "msg_C", edit("tuC", SYNTH_CWD + "/greet.py"),
                  {"input_tokens": 200, "output_tokens": 20}),
        assistant("a3b", "a3", 33, "msg_C",
                  {"type": "tool_use", "id": "tuD", "name": "Read",
                   "input": {"file_path": SYNTH_CWD + "/README.md"}},
                  {"input_tokens": 200, "output_tokens": 20}, index=1),
        result("r3", "a3", 35, "tuC", patch(5)),  # dead-end sibling; the observed shape
        result("r4", "a3b", 36, "tuD", {"type": "text", "file": {"filePath": SYNTH_CWD + "/README.md"}}),
        assistant("a4", "r4", 38, "msg_D", {"type": "thinking", "thinking": "..."},
                  {"input_tokens": 300, "output_tokens": 30}),
        assistant("a4b", "a4", 39, "msg_D", {"type": "text", "text": "Bonjour added."},
                  {"input_tokens": 300, "output_tokens": 30}, index=1),
    ]


def synthetic_harness_forks_records() -> list[dict]:
    """Seventeen records, NOTHING rewound: shapes 1-4 as the corpus writes them. `rA` is a
    dead-end result (1); `a2b`+`rD` a dead-end block with its result (2); `F`/`S` a stop hook
    whose continuation `a4..a5` is off the parent-pointer path because the next prompt `u2`
    attaches to `S` (3); `u0`/`a0` an older second root (4)."""
    t0 = dt.datetime(2026, 3, 10, 14, 0, 0, tzinfo=dt.timezone.utc)

    def ts(sec: float) -> str:
        t = t0 + dt.timedelta(seconds=sec)
        return t.strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond // 1000:03d}Z"

    def rec(kind: str, uuid: str, parent: str | None, sec: float, **extra) -> dict:
        d = {"type": kind, "uuid": uuid, "parentUuid": parent, "sessionId": SYNTH_SESSION,
             "timestamp": ts(sec), "cwd": SYNTH_CWD, "version": "2.1.0", "isSidechain": False}
        d.update(extra)
        return d

    def result(uuid, parent, sec, tool_use_id, tool_use_result):
        return rec("user", uuid, parent, sec, toolUseResult=tool_use_result,
                   message={"role": "user", "content": [
                       {"type": "tool_result", "tool_use_id": tool_use_id, "content": "ok"}]})

    def assistant(uuid, parent, sec, mid, block, usage, index=0):
        return rec("assistant", uuid, parent, sec, requestId="req_" + mid, apiBlockIndex=index,
                   message={"role": "assistant", "model": "claude-sonnet-5", "id": mid,
                            "content": [block], "usage": usage})

    def tool(tid, name, **inp):
        return {"type": "tool_use", "id": tid, "name": name, "input": inp}

    def patch(n):
        return {"filePath": SYNTH_CWD + "/greet.py",
                "structuredPatch": [{"lines": [f"+line {i}" for i in range(n)]}]}

    text = {"type": "text", "text": "ok"}
    u = {"input_tokens": 10, "output_tokens": 1}
    py = SYNTH_CWD + "/greet.py"
    return [
        # shape 4: an older, separate root
        rec("user", "u0", None, -100, promptSource="typed",
            message={"role": "user", "content": "smoke"}),
        assistant("a0", "u0", -98, "msg_0", tool("tu0", "Read", file_path=py), u),
        rec("user", "u1", None, 0, promptSource="typed",
            message={"role": "user", "content": "greet"}),
        # shape 1: blocks chain a1 -> a1b, rA dead-ends under a1, the turn continues from rB
        assistant("a1", "u1", 2, "msg_A", tool("tuA", "Edit", file_path=py), u),
        assistant("a1b", "a1", 3, "msg_A", tool("tuB", "Read", file_path=py), u, index=1),
        result("rA", "a1", 5, "tuA", patch(4)),
        result("rB", "a1b", 6, "tuB", {"type": "text", "file": {"filePath": py}}),
        # shape 2: the turn continues from rC; block a2b and its result rD dead-end
        assistant("a2", "rB", 8, "msg_B", tool("tuC", "Edit", file_path=py), u),
        assistant("a2b", "a2", 9, "msg_B", tool("tuD", "Bash", command="true"), u, index=1),
        result("rC", "a2", 10, "tuC", patch(2)),
        result("rD", "a2b", 11, "tuD", {"stdout": "", "stderr": ""}),
        assistant("a3", "rC", 12, "msg_C", text, u),
        # shape 3: stop hook. F is isMeta; S is written at the same instant as F.
        rec("user", "F", "a3", 13, isMeta=True,
            message={"role": "user", "content": "Stop hook feedback:\n[hook]: commit first"}),
        rec("system", "S", "F", 13, subtype="stop_hook_summary", level="suggestion",
            hookCount=1, hookErrors=["[hook]: commit first"]),
        assistant("a4", "F", 15, "msg_D", tool("tuE", "Bash", command="git commit -am x"), u),
        result("rE", "a4", 17, "tuE", {"stdout": "1 file changed", "stderr": ""}),
        assistant("a5", "rE", 18, "msg_E", text, u),
        # the next human turn attaches to S, not to a5
        rec("user", "u2", "S", 60, promptSource="sdk", origin={"kind": "human"},
            message={"role": "user", "content": "thanks"}),
        assistant("a6", "u2", 62, "msg_F", text, u),
    ]


def synthetic_queued_message_records() -> list[dict]:
    """Six records, NOTHING rewound: a four-record conversation followed by the two
    `queue-operation` records Claude Code writes when a message is typed while the agent is
    busy — timestamped, no `uuid`, no parent, and newer than everything else (shape 5)."""
    t0 = dt.datetime(2026, 3, 10, 15, 0, 0, tzinfo=dt.timezone.utc)

    def ts(sec: float) -> str:
        t = t0 + dt.timedelta(seconds=sec)
        return t.strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond // 1000:03d}Z"

    def rec(kind: str, uuid: str, parent: str | None, sec: float, **extra) -> dict:
        d = {"type": kind, "uuid": uuid, "parentUuid": parent, "sessionId": SYNTH_SESSION,
             "timestamp": ts(sec), "cwd": SYNTH_CWD, "version": "2.1.0", "isSidechain": False}
        d.update(extra)
        return d

    u = {"input_tokens": 10, "output_tokens": 1}
    return [
        rec("user", "u1", None, 0, promptSource="typed",
            message={"role": "user", "content": "greet"}),
        rec("assistant", "a1", "u1", 2, requestId="req_msg_A",
            message={"role": "assistant", "model": "claude-sonnet-5", "id": "msg_A", "usage": u,
                     "content": [{"type": "tool_use", "id": "tuA", "name": "Bash",
                                  "input": {"command": "sleep 30"}}]}),
        rec("user", "r1", "a1", 4, toolUseResult={"stdout": "", "stderr": ""},
            message={"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "tuA", "content": ""}]}),
        rec("assistant", "a2", "r1", 6, requestId="req_msg_B",
            message={"role": "assistant", "model": "claude-sonnet-5", "id": "msg_B", "usage": u,
                     "content": [{"type": "text", "text": "done"}]}),
        {"type": "queue-operation", "operation": "enqueue", "content": "and now the tests",
         "sessionId": SYNTH_SESSION, "timestamp": ts(8)},
        {"type": "queue-operation", "operation": "dequeue",
         "sessionId": SYNTH_SESSION, "timestamp": ts(9)},
    ]


FIXTURES = {
    "genuine_rewind": synthetic_rewind_records,
    "harness_forks": synthetic_harness_forks_records,
    "queued_message": synthetic_queued_message_records,
}

# What both implementations must say about the synthetic transcripts. The Swift test
# (LivePathTests.swift) asserts the same rewound sets.
SYNTH_EXPECT = {
    "genuine_rewind": {
        "found": {"rewound": ["a1", "a2", "r1", "r3"], "sib_tool": 1, "harness": 1, "tree": 0,
                  "human": 3, "lines": 8},
        "fixed": {"rewound": ["a1", "a2", "r1"], "sib_tool": 0, "harness": 0, "tree": 0,
                  "human": 3, "lines": 3},
    },
    "harness_forks": {
        "found": {"rewound": ["a0", "a2b", "a4", "a5", "rA", "rD", "rE", "u0"], "sib_tool": 1,
                  "harness": 6, "tree": 2, "human": 0, "lines": 4},
        "fixed": {"rewound": [], "sib_tool": 0, "harness": 0, "tree": 0, "human": 0, "lines": 0},
    },
    "queued_message": {
        "found": {"rewound": ["a1", "a2", "r1", "u1"], "sib_tool": 0, "harness": 0, "tree": 4,
                  "human": 0, "lines": 0},
        "fixed": {"rewound": [], "sib_tool": 0, "harness": 0, "tree": 0, "human": 0, "lines": 0},
    },
}


def write_fixture() -> list[pathlib.Path]:
    LIVE_PATH_FIXTURES.mkdir(parents=True, exist_ok=True)
    out = []
    for name, make in FIXTURES.items():
        path = LIVE_PATH_FIXTURES / f"{name}.jsonl"
        with open(path, "w", encoding="utf-8") as fh:
            fh.writelines(json.dumps(r, separators=(",", ":")) + "\n" for r in make())
        out.append(path)
    return out


def self_check(rows_by_rule: dict[str, list[dict]]) -> bool:
    ok = True
    for rule, rows in rows_by_rule.items():
        for r in rows:
            name = pathlib.Path(r["path"]).stem
            if name not in SYNTH_EXPECT or LIVE_PATH_FIXTURES.name not in r["path"]:
                continue
            exp = SYNTH_EXPECT[name][rule]
            got = {k: r[k] for k in ("sib_tool", "harness", "tree", "human", "lines")}
            got["rewound"] = r["rewound_ids"]
            if got != exp:
                ok = False
                print(f"SELF-CHECK FAILED ({rule}): got {got}, expected {exp}", file=sys.stderr)
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="*", type=pathlib.Path)
    ap.add_argument("--projects", type=pathlib.Path, default=pathlib.Path("~/.claude/projects"))
    ap.add_argument("--sidecars", action="store_true", help="also measure every sidecar transcript")
    ap.add_argument("--rule", choices=["found", "fixed", "both"], default="both")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--write-fixture", action="store_true")
    args = ap.parse_args()

    if args.write_fixture:
        for path in write_fixture():
            print(f"wrote {path}")

    paths: list[pathlib.Path]
    if args.paths:
        paths = list(args.paths)
    else:
        paths = [t.path for t in iter_root_transcripts(args.projects)]
        if args.sidecars:
            root = args.projects.expanduser()
            roots = set(paths)
            paths += sorted(p for p in root.glob("*/*/**/*.jsonl") if p not in roots)
        paths += sorted(BOUNDARY_FIXTURES.glob("*.jsonl"))
        paths += sorted(LIVE_PATH_FIXTURES.glob("*.jsonl"))

    rules = ["found", "fixed"] if args.rule == "both" else [args.rule]
    rows_by_rule = {rule: [measure(p, rule) for p in paths] for rule in rules}

    if args.json:
        json.dump(rows_by_rule, sys.stdout, indent=1)
        print()
    else:
        for rule, rows in rows_by_rule.items():
            print_table(rule, rows)
    return 0 if self_check(rows_by_rule) else 1


if __name__ == "__main__":
    sys.exit(main())
