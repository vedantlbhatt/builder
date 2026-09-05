#!/usr/bin/env python3
"""Generate the synthetic opencode fixture and the stats the loader must reproduce.

    spec/fixtures/opencode/opencode.db                          the SQLite store (current)
    spec/fixtures/opencode/storage/session/<project>/<ses>.json  the pre-SQLite JSON store
    spec/fixtures/opencode/storage/message/<ses>/<msg>.json
    spec/fixtures/opencode/storage/part/<msg>/<prt>.json
    spec/fixtures/opencode/storage/project/<project>.json
    spec/fixtures/opencode/export.json                          `opencode export` of the session
    spec/fixtures/opencode/expected.json

The same two sessions (one root, one subagent child) are written into all three
containers so the tests can hold them to identical stats. Everything is SYNTHETIC, in the
shapes verified from the opencode source (see analysis/opencode.py for per-shape
provenance), not a captured store. It exercises every branch the loader has:

* an assistant turn with THREE `step-finish` parts whose `tokens` are each step's usage
  while the message's own `tokens` is the LAST step only and `cost` is the running sum —
  the undercount trap; the session row carries the message sum, as the backfill did;
* `edit` with `metadata.filediff` line counts, `write` with content, a `bash` heredoc, a
  `bash` whose `metadata.exit` is 1 (pytest failing), an `edit` whose state is `error`;
* an aborted turn: `error.name = "MessageAbortedError"` plus a tool part rewritten to
  `error` / "Tool execution aborted" / `metadata.interrupted` (one interrupt, not an error);
* a manual compaction: a user message holding only a `compaction` part, then an assistant
  message with `summary: true` whose text is not a reply but whose tokens are real;
* the `/shell` path: a synthetic "The following tool was executed by the user" user
  message and a zero-token assistant message carrying the `bash` part;
* a `retry` part (an APIError that was retried), a `websearch`, and a `task` call whose
  `metadata.sessionId` names the child session — which has a non-synthetic user text
  part nobody typed.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import sqlite3
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from analysis import digest, opencode

OUT = ROOT / "spec" / "fixtures" / "opencode"
BASE_MS = 1_749_564_000_000  # 2025-06-10T14:00:00Z
PROJECT_ID = "3f2a9c1e7b6d5a4c8e9f0a1b2c3d4e5f6a7b8c9d"  # first commit sha, as opencode uses
WORKTREE = "/Users/dev/proj"
# `ses_` + 26 chars, DESCENDING, so the child (created later) sorts before the parent
SESSION_ID = "ses_7fa2c1e9b3d4f5a6b7c8d9e0f1a2"
CHILD_ID = "ses_7fa2c1e9b3d4f5a6b7c8d9e0f1a1"
MODEL = "claude-sonnet-4-5"
PROVIDER = "anthropic"
VERSION = "1.18.29"
TASK = "Add a --dry-run flag to scripts/deploy.sh and keep the tests green"

OLD = "set -e\n"
NEW = 'set -euo pipefail\nDRY_RUN=0\nif [ "${1:-}" = "--dry-run" ]; then DRY_RUN=1; fi\n'
WRITE_CONTENT = (
    "import subprocess\n\n\ndef run(args):\n"
    "    return subprocess.call(['scripts/deploy.sh', *args])\n"
)
HEREDOC = "cat > tests/conftest.py <<'EOF'\nimport os\n\nDRY = True\nEOF"
PYTEST_FAIL = "F.\nFAILED tests/test_deploy.py::test_dry_run - AssertionError\n1 failed, 1 passed"
PYTEST_PASS = "...\n3 passed"
REPLY_1 = "I'll read the script first, then add the flag."
REPLY_2 = "Tests pass now. `scripts/deploy.sh` accepts `--dry-run`."
REPLY_3 = "Committed as abc1234."
REPLY_4 = "Common convention is `--dry-run`; `-n` is the short form in most tools."
SUMMARY = "Summary: added --dry-run to scripts/deploy.sh, fixed tests, committed abc1234."
CHILD_PROMPT = "Check the project docs for how flags are documented and report the convention."
CHILD_REPLY = "Flags are documented in docs/cli.md as `--flag` with a one-line description."

_msg_n = 0
_prt_n = 0


def ms(sec: float) -> int:
    return BASE_MS + int(sec * 1000)


def msg_id() -> str:
    global _msg_n
    _msg_n += 1
    return f"msg_{_msg_n:026d}"


def prt_id() -> str:
    global _prt_n
    _prt_n += 1
    return f"prt_{_prt_n:026d}"


def tokens(inp, out, cache_read=0, cache_write=0, reasoning=0) -> dict:
    return {
        "input": inp,
        "output": out,
        "reasoning": reasoning,
        "cache": {"read": cache_read, "write": cache_write},
    }


ZERO = tokens(0, 0)


class Session:
    def __init__(self, sid: str, parent: str | None, title: str, created: float):
        self.id = sid
        self.parent = parent
        self.title = title
        self.created = created
        self.messages: list[tuple[dict, list[dict]]] = []

    def user(self, sec: float, parts: list[dict]) -> str:
        info = {
            "id": msg_id(),
            "sessionID": self.id,
            "role": "user",
            "time": {"created": ms(sec)},
            "agent": "build",
            "model": {"providerID": PROVIDER, "modelID": MODEL},
        }
        self.messages.append((info, [self._part(info["id"], p) for p in parts]))
        return info["id"]

    def assistant(
        self,
        sec: float,
        parent: str,
        parts: list[dict],
        *,
        completed: float | None = None,
        toks: dict = ZERO,
        cost: float = 0,
        finish: str | None = "stop",
        error: dict | None = None,
        summary: bool = False,
        agent: str = "build",
    ) -> str:
        info = {
            "id": msg_id(),
            "sessionID": self.id,
            "role": "assistant",
            "parentID": parent,
            "modelID": MODEL,
            "providerID": PROVIDER,
            "mode": agent,
            "agent": agent,
            "path": {"cwd": WORKTREE, "root": WORKTREE},
            "cost": cost,
            "tokens": toks,
            "time": {"created": ms(sec), **({"completed": ms(completed)} if completed else {})},
        }
        if finish:
            info["finish"] = finish
        if error:
            info["error"] = error
        if summary:
            info["summary"] = True
        self.messages.append((info, [self._part(info["id"], p) for p in parts]))
        return info["id"]

    def _part(self, mid: str, p: dict) -> dict:
        return {"id": prt_id(), "sessionID": self.id, "messageID": mid, **p}


def text(t: str, start: float | None = None, **kw) -> dict:
    p = {"type": "text", "text": t, **kw}
    if start is not None:
        p["time"] = {"start": ms(start), "end": ms(start + 0.5)}
    return p


def tool(name, call, inp, start, end=None, *, output="", title="", metadata=None, error=None):
    if error is not None:
        state = {
            "status": "error",
            "input": inp,
            "error": error,
            "time": {"start": ms(start), "end": ms(end)},
        }
        if metadata is not None:
            state["metadata"] = metadata
    elif end is None:
        state = {"status": "running", "input": inp, "time": {"start": ms(start)}}
    else:
        state = {
            "status": "completed",
            "input": inp,
            "output": output,
            "title": title,
            "metadata": metadata or {},
            "time": {"start": ms(start), "end": ms(end)},
        }
    return {"type": "tool", "callID": call, "tool": name, "state": state}


def step_start(snapshot="a1b2c3") -> dict:
    return {"type": "step-start", "snapshot": snapshot}


def step_finish(toks: dict, cost: float, reason="tool-calls") -> dict:
    return {
        "type": "step-finish",
        "reason": reason,
        "snapshot": "a1b2c3",
        "cost": cost,
        "tokens": toks,
    }


def bash(call, cmd, start, end, output, exit_code=0, description=None) -> dict:
    inp = {"command": cmd, "timeout": 120000}
    if description:
        inp["description"] = description
    return tool(
        "bash",
        call,
        inp,
        start,
        end,
        output=output,
        title=cmd,
        metadata={"output": output, "exit": exit_code, "truncated": False},
    )


def build() -> tuple[Session, Session]:
    root = Session(SESSION_ID, None, TASK, 0)
    child = Session(CHILD_ID, SESSION_ID, "Check docs (@explore subagent)", 112)

    # turn 1: three steps; message.tokens is the LAST step, cost is the sum
    m1 = root.user(0, [text(TASK)])
    s1 = tokens(9000, 40, 0, 2000)
    s2 = tokens(12000, 60, 8000, 0)
    s3 = tokens(14000, 30, 8000, 0)
    root.assistant(
        1,
        m1,
        [
            step_start(),
            text(REPLY_1, start=3),
            tool(
                "read",
                "call_01",
                {"filePath": f"{WORKTREE}/scripts/deploy.sh"},
                4,
                5,
                output="<file>\n00001| #!/bin/bash\n00002| set -e\n</file>",
                title="scripts/deploy.sh",
                metadata={"preview": "#!/bin/bash\nset -e", "truncated": False, "loaded": []},
            ),
            step_finish(s1, 0.03),
            step_start(),
            tool(
                "edit",
                "call_02",
                {"filePath": f"{WORKTREE}/scripts/deploy.sh", "oldString": OLD, "newString": NEW},
                7,
                8,
                output="Edit applied successfully.",
                title="scripts/deploy.sh",
                metadata={
                    "diff": "--- a\n+++ b\n@@ -1 +1,3 @@\n-set -e\n+set -euo pipefail\n+DRY_RUN=0\n+if …",
                    "filediff": {
                        "file": f"{WORKTREE}/scripts/deploy.sh",
                        "patch": "…",
                        "additions": 3,
                        "deletions": 1,
                    },
                    "diagnostics": {},
                },
            ),
            tool(
                "write",
                "call_03",
                {"filePath": f"{WORKTREE}/tests/helpers.py", "content": WRITE_CONTENT},
                10,
                11,
                output="Wrote file successfully.",
                title="tests/helpers.py",
                metadata={
                    "diagnostics": {},
                    "filepath": f"{WORKTREE}/tests/helpers.py",
                    "exists": False,
                },
            ),
            bash("call_04", "pytest -q tests/", 12, 14, PYTEST_FAIL, exit_code=1),
            step_finish(s2, 0.04),
            step_start(),
            tool(
                "edit",
                "call_05",
                {
                    "filePath": f"{WORKTREE}/tests/test_deploy.py",
                    "oldString": "expect 1\n",
                    "newString": "expect 0\n",
                },
                16,
                17,
                error="oldString not found in file. Make sure it matches exactly, including whitespace.",
            ),
            bash("call_06", HEREDOC, 18, 19, ""),
            bash("call_07", "pytest -q tests/", 21, 23, PYTEST_PASS),
            text(REPLY_2, start=26),
            step_finish(s3, 0.045, reason="stop"),
        ],
        completed=27,
        toks=s3,
        cost=0.115,
    )

    # turn 2: aborted by the human mid-command
    m2 = root.user(58, [text("commit it")])
    root.assistant(
        59,
        m2,
        [
            step_start(),
            tool(
                "bash",
                "call_08",
                {"command": "git commit -am 'Add --dry-run'", "timeout": 120000},
                60,
                61,
                error="Tool execution aborted",
                metadata={"output": "", "interrupted": True},
            ),
        ],
        completed=61,
        finish=None,
        error={"name": "MessageAbortedError", "data": {"message": "The operation was aborted."}},
    )

    # turn 3: the commit
    m3 = root.user(62, [text("go ahead and commit")])
    s4 = tokens(15500, 12, 8000, 0)
    root.assistant(
        63,
        m3,
        [
            step_start(),
            bash(
                "call_09", "git commit -am 'Add --dry-run'", 64, 65, "[main abc1234] Add --dry-run"
            ),
            text(REPLY_3, start=66),
            step_finish(s4, 0.05, reason="stop"),
        ],
        completed=67,
        toks=s4,
        cost=0.05,
    )

    # manual /compact: a user message that is only a compaction part, then the summary
    m4 = root.user(90, [{"type": "compaction", "auto": False}])
    s5 = tokens(16000, 400, 0, 0)
    root.assistant(
        91,
        m4,
        [step_start(), text(SUMMARY, start=93), step_finish(s5, 0.06, reason="stop")],
        completed=95,
        toks=s5,
        cost=0.06,
        summary=True,
        agent="compaction",
    )

    # the /shell path: `!ls` — no model call, a synthetic user text, a bash part
    m5 = root.user(100, [text("The following tool was executed by the user", synthetic=True)])
    root.assistant(
        100,
        m5,
        [
            tool(
                "bash",
                "call_10",
                {"command": "ls"},
                100,
                100.4,
                output="scripts\ntests",
                title="",
                metadata={"output": "scripts\ntests"},
            )
        ],
        completed=100.4,
        finish=None,
    )

    # turn 4: a retried API error, a web search, a subagent task
    m6 = root.user(
        110, [text("search the web for the flag convention and have explore check the docs")]
    )
    s6 = tokens(17000, 25, 8000, 0)
    root.assistant(
        111,
        m6,
        [
            step_start(),
            {
                "type": "retry",
                "attempt": 1,
                "error": {
                    "name": "APIError",
                    "data": {"message": "Overloaded", "statusCode": 529, "isRetryable": True},
                },
                "time": {"created": ms(111.5)},
            },
            tool(
                "websearch",
                "call_11",
                {"query": "cli dry-run flag convention"},
                112,
                114,
                output="…",
                title="cli dry-run flag convention",
                metadata={},
            ),
            tool(
                "task",
                "call_12",
                {
                    "description": "Check docs",
                    "prompt": CHILD_PROMPT,
                    "subagent_type": "explore",
                },
                112,
                115,
                output=f"<task_result>\n{CHILD_REPLY}\n</task_result>",
                title="Check docs",
                metadata={
                    "parentSessionId": SESSION_ID,
                    "sessionId": CHILD_ID,
                    "model": {"modelID": MODEL, "providerID": PROVIDER},
                },
            ),
            text(REPLY_4, start=116),
            step_finish(s6, 0.055, reason="stop"),
        ],
        completed=117,
        toks=s6,
        cost=0.055,
    )

    # the child session: its "prompt" was written by the parent agent
    c1 = child.user(112, [text(CHILD_PROMPT)])
    s7 = tokens(3000, 50, 0, 0)
    child.assistant(
        112.2,
        c1,
        [
            step_start(),
            tool(
                "read",
                "call_13",
                {"filePath": f"{WORKTREE}/docs/cli.md"},
                113,
                113.5,
                output="<file>…</file>",
                title="docs/cli.md",
                metadata={"preview": "…", "truncated": False, "loaded": []},
            ),
            text(CHILD_REPLY, start=114),
            step_finish(s7, 0.01, reason="stop"),
        ],
        completed=114.8,
        toks=s7,
        cost=0.01,
        agent="explore",
    )
    return root, child


def session_info(s: Session) -> dict:
    """Session.Info as the JSON store / export carry it (schema/src/v1/session.ts
    `SessionInfo`). tokens/cost are the message sums, as the session_usage backfill did."""
    msum = tokens(0, 0)
    cost = 0.0
    for info, _ in s.messages:
        if info["role"] != "assistant":
            continue
        t = info["tokens"]
        msum["input"] += t["input"]
        msum["output"] += t["output"]
        msum["reasoning"] += t["reasoning"]
        msum["cache"]["read"] += t["cache"]["read"]
        msum["cache"]["write"] += t["cache"]["write"]
        cost += info["cost"]
    last = max((info["time"].get("completed") or info["time"]["created"]) for info, _ in s.messages)
    out = {
        "id": s.id,
        "slug": "add-dry-run-flag" if s.parent is None else "check-docs",
        "projectID": PROJECT_ID,
        "directory": WORKTREE,
        "title": s.title,
        "version": VERSION,
        "agent": "build" if s.parent is None else "explore",
        "model": {"id": MODEL, "providerID": PROVIDER, "variant": "default"},
        "summary": {"additions": 11, "deletions": 1, "files": 3} if s.parent is None else None,
        "cost": round(cost, 6),
        "tokens": msum,
        "time": {"created": ms(s.created), "updated": last},
    }
    if s.parent:
        out["parentID"] = s.parent
    return {k: v for k, v in out.items() if v is not None}


SCHEMA = """
CREATE TABLE `project` (
  `id` text PRIMARY KEY, `worktree` text NOT NULL, `vcs` text, `name` text, `icon_url` text,
  `icon_url_override` text, `icon_color` text, `time_created` integer NOT NULL,
  `time_updated` integer NOT NULL, `time_initialized` integer, `sandboxes` text NOT NULL,
  `commands` text
);
CREATE TABLE `session` (
  `id` text PRIMARY KEY, `project_id` text NOT NULL, `workspace_id` text, `parent_id` text,
  `slug` text NOT NULL, `directory` text NOT NULL, `path` text, `title` text NOT NULL,
  `version` text NOT NULL, `share_url` text, `summary_additions` integer,
  `summary_deletions` integer, `summary_files` integer, `summary_diffs` text, `metadata` text,
  `cost` real DEFAULT 0 NOT NULL, `tokens_input` integer DEFAULT 0 NOT NULL,
  `tokens_output` integer DEFAULT 0 NOT NULL, `tokens_reasoning` integer DEFAULT 0 NOT NULL,
  `tokens_cache_read` integer DEFAULT 0 NOT NULL, `tokens_cache_write` integer DEFAULT 0 NOT NULL,
  `revert` text, `permission` text, `agent` text, `model` text, `time_created` integer NOT NULL,
  `time_updated` integer NOT NULL, `time_compacting` integer, `time_archived` integer,
  CONSTRAINT `fk_session_project_id_project_id_fk` FOREIGN KEY (`project_id`)
    REFERENCES `project`(`id`) ON DELETE CASCADE
);
CREATE TABLE `message` (
  `id` text PRIMARY KEY, `session_id` text NOT NULL, `time_created` integer NOT NULL,
  `time_updated` integer NOT NULL, `data` text NOT NULL,
  CONSTRAINT `fk_message_session_id_session_id_fk` FOREIGN KEY (`session_id`)
    REFERENCES `session`(`id`) ON DELETE CASCADE
);
CREATE TABLE `part` (
  `id` text PRIMARY KEY, `message_id` text NOT NULL, `session_id` text NOT NULL,
  `time_created` integer NOT NULL, `time_updated` integer NOT NULL, `data` text NOT NULL,
  CONSTRAINT `fk_part_message_id_message_id_fk` FOREIGN KEY (`message_id`)
    REFERENCES `message`(`id`) ON DELETE CASCADE
);
CREATE TABLE `session_message` (
  `id` text PRIMARY KEY, `session_id` text NOT NULL, `type` text NOT NULL,
  `seq` integer NOT NULL, `time_created` integer NOT NULL, `time_updated` integer NOT NULL,
  `data` text NOT NULL
);
CREATE TABLE `migration` (id TEXT PRIMARY KEY, time_completed INTEGER NOT NULL);
CREATE INDEX `message_session_time_created_id_idx` ON `message` (`session_id`,`time_created`,`id`);
CREATE INDEX `part_message_id_id_idx` ON `part` (`message_id`,`id`);
CREATE INDEX `part_session_idx` ON `part` (`session_id`);
CREATE INDEX `session_project_idx` ON `session` (`project_id`);
CREATE INDEX `session_parent_idx` ON `session` (`parent_id`);
"""

# VERIFIED: packages/core/src/database/migration.gen.ts, first and last three entries.
MIGRATIONS = [
    "20260127222353_familiar_lady_ursula",
    "20260510033149_session_usage",
    "20260601010001_normalize_storage_paths",
    "20260622142730_simplify_session_context_epoch",
    "20260622170816_reset_v2_session_state",
    "20260622202450_simplify_session_input",
]


def write_sqlite(path: pathlib.Path, sessions: list[Session]) -> None:
    if path.exists():
        path.unlink()
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    con.executemany(
        "INSERT INTO migration VALUES (?, ?)", [(m, BASE_MS - 86_400_000) for m in MIGRATIONS]
    )
    con.execute(
        "INSERT INTO project VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            PROJECT_ID,
            WORKTREE,
            "git",
            "proj",
            None,
            None,
            None,
            BASE_MS - 3_600_000,
            BASE_MS - 3_600_000,
            BASE_MS - 3_600_000,
            "[]",
            None,
        ),
    )
    for s in sessions:
        info = session_info(s)
        t = info["tokens"]
        con.execute(
            "INSERT INTO session (id, project_id, parent_id, slug, directory, path, title, version,"
            " summary_additions, summary_deletions, summary_files, cost, tokens_input,"
            " tokens_output, tokens_reasoning, tokens_cache_read, tokens_cache_write, agent,"
            " model, time_created, time_updated) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                s.id,
                PROJECT_ID,
                s.parent,
                info["slug"],
                WORKTREE,
                "",
                s.title,
                VERSION,
                (info.get("summary") or {}).get("additions"),
                (info.get("summary") or {}).get("deletions"),
                (info.get("summary") or {}).get("files"),
                info["cost"],
                t["input"],
                t["output"],
                t["reasoning"],
                t["cache"]["read"],
                t["cache"]["write"],
                info["agent"],
                json.dumps(info["model"]),
                info["time"]["created"],
                info["time"]["updated"],
            ),
        )
        for m, parts in s.messages:
            data = {k: v for k, v in m.items() if k not in ("id", "sessionID")}
            con.execute(
                "INSERT INTO message VALUES (?,?,?,?,?)",
                (
                    m["id"],
                    s.id,
                    m["time"]["created"],
                    m["time"].get("completed") or m["time"]["created"],
                    json.dumps(data),
                ),
            )
            for p in parts:
                data = {k: v for k, v in p.items() if k not in ("id", "sessionID", "messageID")}
                # the row clock is the INSERT time, not the part's own time (import.ts)
                con.execute(
                    "INSERT INTO part VALUES (?,?,?,?,?,?)",
                    (
                        p["id"],
                        m["id"],
                        s.id,
                        BASE_MS + 200_000,
                        BASE_MS + 200_000,
                        json.dumps(data),
                    ),
                )
    con.commit()
    con.close()


def write_json_dir(root: pathlib.Path, sessions: list[Session]) -> None:
    if root.exists():
        shutil.rmtree(root)
    (root / "project").mkdir(parents=True)
    (root / "project" / f"{PROJECT_ID}.json").write_text(
        json.dumps(
            {
                "id": PROJECT_ID,
                "vcs": "git",
                "worktree": WORKTREE,
                "time": {"created": BASE_MS - 3_600_000, "initialized": BASE_MS - 3_600_000},
            },
            indent=2,
        )
        + "\n"
    )
    (root / "migration").write_text("2")
    for s in sessions:
        sdir = root / "session" / PROJECT_ID
        sdir.mkdir(parents=True, exist_ok=True)
        (sdir / f"{s.id}.json").write_text(json.dumps(session_info(s), indent=2) + "\n")
        for m, parts in s.messages:
            mdir = root / "message" / s.id
            mdir.mkdir(parents=True, exist_ok=True)
            (mdir / f"{m['id']}.json").write_text(json.dumps(m, indent=2) + "\n")
            pdir = root / "part" / m["id"]
            pdir.mkdir(parents=True, exist_ok=True)
            for p in parts:
                (pdir / f"{p['id']}.json").write_text(json.dumps(p, indent=2) + "\n")


def write_export(path: pathlib.Path, s: Session) -> None:
    path.write_text(
        json.dumps(
            {
                "info": session_info(s),
                "messages": [{"info": m, "parts": parts} for m, parts in s.messages],
            },
            indent=2,
        )
        + "\n"
    )


def main() -> int:
    root, child = build()
    OUT.mkdir(parents=True, exist_ok=True)
    write_sqlite(OUT / "opencode.db", [root, child])
    write_json_dir(OUT / "storage", [root, child])
    write_export(OUT / "export.json", root)

    def expect(path: pathlib.Path) -> dict:
        s = opencode.scan(path)
        events, derivation = opencode._derive(s)
        return {
            "harness": digest.detect_harness(path),
            "events": len(events),
            "stats": digest.stats(events),
            "usage": s.usage,
            "meta": {k: v for k, v in s.meta.items() if k not in ("path", "db_path")},
            "diagnostics": dict(s.diagnostics, derivation=derivation),
        }

    expected = {
        "_generated_by": "scripts/gen_opencode_fixture.py — do not hand-edit",
        "sqlite": expect(OUT / "opencode.db" / SESSION_ID),
        "sqlite_child": expect(OUT / "opencode.db" / CHILD_ID),
        "json_dir": expect(OUT / "storage" / "session" / PROJECT_ID / f"{SESSION_ID}.json"),
        "export_json": expect(OUT / "export.json"),
    }
    (OUT / "expected.json").write_text(json.dumps(expected, indent=1, ensure_ascii=False) + "\n")
    n = expected["sqlite"]["events"]
    print(
        f"wrote {OUT / 'opencode.db'} ({len(root.messages) + len(child.messages)} messages), "
        f"storage/, export.json, expected.json ({n} events)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
