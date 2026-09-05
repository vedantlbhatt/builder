#!/usr/bin/env python3
"""Generate the synthetic Gemini CLI recording fixtures and the stats the loader must
reproduce.

    spec/fixtures/gemini/synthetic_session.jsonl          the current on-disk form
    spec/fixtures/gemini/synthetic_session_legacy.json    the same conversation, legacy form
    spec/fixtures/gemini/synthetic_subagent.jsonl         a `kind: "subagent"` recording
    spec/fixtures/gemini/synthetic_session.expected.json  stats for all three

Both are SYNTHETIC, written in the shapes verified from the Gemini CLI source (see the
docstring of analysis/gemini.py for the per-shape provenance), not captured sessions. The
JSONL exercises every branch the loader has: the metadata line; a `/help` slash command
(NOT a prompt); a typed prompt whose `content` is an `@file` expansion and whose
`displayContent` is what was typed; a gemini text reply appended THREE times (bare, then
with `tokens`, then with `toolCalls`) — the same id, which a naive per-line token sum
double-counts; `run_shell_command` calls including `git commit` and `pytest`, one with a
heredoc; `write_file`, `replace` and `read_file`; one `status: "error"` tool result with
`response.error` (a failed `replace`, which must earn NO line or file credit); one
`run_shell_command` that exited non-zero — recorded as `status: "success"` with `Exit
Code: 2` in its output, exactly as tools/shell.ts writes it; the tool-response `type:
"user"` record (functionResponse parts, NOT a prompt); a tool-call-only synthetic gemini
message whose `content` holds a `thought` part and a `functionCall` part duplicated by its
`toolCalls` record; `$set` metadata updates; a `$rewindTo` that removes a wrong-turn reply;
and a trailing message with no `timestamp`.

The legacy `.json` file is the reader's view of the same recording (rewind applied, one
record per id) and must produce identical stats — that equality is asserted in
analysis/tests/test_gemini.py along with the hand-counted invariants.

The subagent recording is what `chatRecordingService.ts` writes under
`chats/<parentSessionId>/<sessionId>.jsonl`: metadata with `kind: "subagent"` and
`directories`, then the PARENT MODEL's instruction recorded as a plain `type: "user"`
message (the same `recordMessage` path a typed prompt takes), a tool round and a reply. The
loader must report zero prompts for it and one `prompt_agent_authored`.
"""

from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from analysis import digest, gemini

OUT = ROOT / "spec" / "fixtures" / "gemini"
SESSION_ID = "5d2c1a0e-4f3b-4c2d-9e8f-7a6b5c4d3e2f"
SUBAGENT_ID = "7c0b3e9d-2a1f-4d6e-8b5c-3f2e1d0c9b8a"
PROJECT_HASH = "9f1c2b3a4d5e6f708192a3b4c5d6e7f8091a2b3c4d5e6f708192a3b4c5d6e7f8"
MODEL = "gemini-2.5-pro"


def ts(sec: float) -> str:
    """Stamps in `new Date().toISOString()` form, spaced seconds apart."""
    whole = int(sec)
    ms = round((sec - whole) * 1000)
    m, s = divmod(whole, 60)
    return f"2025-11-04T09:{m:02d}:{s:02d}.{ms:03d}Z"


def tokens(inp, out, cached, thoughts) -> dict:
    return {
        "input": inp,
        "output": out,
        "cached": cached,
        "thoughts": thoughts,
        "tool": 0,
        "total": inp + out + thoughts,
    }


def call(cid, name, args, sec, output=None, error=None) -> dict:
    resp = {"error": error} if error is not None else {"output": output or ""}
    return {
        "id": cid,
        "name": name,
        "args": args,
        "result": [{"functionResponse": {"id": cid, "name": name, "response": resp}}],
        "status": "error" if error is not None else "success",
        "timestamp": ts(sec),
        "displayName": name,
        "description": "",
        "renderOutputAsMarkdown": False,
    }


def user(mid, sec, content, display=None) -> dict:
    r = {"id": mid, "timestamp": ts(sec), "type": "user", "content": content}
    if display is not None:
        r["displayContent"] = display
    return r


def gem(mid, sec, content, **kw) -> dict:
    r = {"id": mid, "timestamp": ts(sec), "type": "gemini", "content": content}
    r.update(kw)
    return r


PROMPT_1 = "@scripts/deploy.sh add a --dry-run flag and keep the tests green"
PROMPT_1_EXPANDED = (
    "--- Content from referenced files ---\nContent from @scripts/deploy.sh:\n"
    "#!/bin/bash\nset -e\n…\n--- End of content ---\n\n" + PROMPT_1
)
PROMPT_2 = "commit it"
REPLY_1 = "I'll read the script first, then add the flag."
REPLY_2 = "Tests pass now. `scripts/deploy.sh` accepts `--dry-run`."
REPLY_WRONG = "Pushing to main now."
REPLY_3 = "Committed as abc1234."

WRITE_CONTENT = "import subprocess\n\n\ndef run(args):\n    return subprocess.call(['scripts/deploy.sh', *args])\n"
OLD = "set -e\n"
NEW = 'set -euo pipefail\nDRY_RUN=0\nif [ "${1:-}" = "--dry-run" ]; then DRY_RUN=1; fi\n'

META = {
    "sessionId": SESSION_ID,
    "projectHash": PROJECT_HASH,
    "startTime": ts(0),
    "lastUpdated": ts(0),
    "kind": "main",
}

# The gemini reply that arrives in three appends of the same id.
G1 = gem("g1", 6.0, REPLY_1, model=MODEL)
G1_TOK = dict(G1, tokens=tokens(9000, 40, 0, 120), thoughts=[])
G1_TOOLS = dict(
    G1_TOK,
    toolCalls=[
        call(
            "c1",
            "read_file",
            {"file_path": "/Users/dev/proj/scripts/deploy.sh"},
            7.0,
            "#!/bin/bash…",
        ),
        call(
            "c2",
            "replace",
            {
                "file_path": "/Users/dev/proj/scripts/deploy.sh",
                "old_string": OLD,
                "new_string": NEW,
                "instruction": "add dry-run flag",
            },
            9.5,
            "Successfully modified file",
        ),
        call(
            "c3",
            "write_file",
            {"file_path": "/Users/dev/proj/tests/helpers.py", "content": WRITE_CONTENT},
            12.0,
            "Successfully created and wrote to new file",
        ),
        call(
            "c4",
            "run_shell_command",
            {"command": "pytest -q tests/", "description": "Run the tests"},
            15.0,
            "F.\nFAILED tests/test_deploy.py::test_dry_run - AssertionError\n1 failed, 1 passed\n",
        ),
        call(
            "c5",
            "replace",
            {
                "file_path": "/Users/dev/proj/tests/test_deploy.py",
                "old_string": "expect 1",
                "new_string": "expect 0",
            },
            18.0,
            error="Failed to edit, could not find the string to replace.",
        ),
        call(
            "c6",
            "run_shell_command",
            {"command": "cat > tests/conftest.py <<'EOF'\nimport os\n\nDRY = True\nEOF"},
            20.0,
            "",
        ),
        # A non-zero exit. tools/shell.ts returns no `error` for it, so the record says
        # `success`; the exit status is a line of llmContent, wrapped by `wrapUntrusted`.
        call(
            "c7",
            "run_shell_command",
            {"command": "make lint"},
            22.0,
            "<untrusted_context>\nOutput: make: *** No rule to make target 'lint'.  Stop.\n"
            "Exit Code: 2\nProcess Group PGID: 4242\n</untrusted_context>",
        ),
        call("c8", "run_shell_command", {"command": "pytest -q tests/"}, 24.0, "...\n3 passed\n"),
    ],
)

# The tool-call-only synthetic message: thought + functionCall parts, plus the record.
G_SYN = gem(
    "g3",
    62.0,
    [
        {"text": "**Committing**", "thought": True, "thoughtSignature": "sig"},
        {
            "functionCall": {
                "id": "c9",
                "name": "run_shell_command",
                "args": {"command": "git commit -am 'Add --dry-run'"},
            }
        },
    ],
    model=MODEL,
    toolCalls=[
        call(
            "c9",
            "run_shell_command",
            {"command": "git commit -am 'Add --dry-run'"},
            63.0,
            "[main abc1234] Add --dry-run\n",
        )
    ],
    tokens=tokens(15000, 20, 8000, 60),
)


def jsonl_lines() -> list[dict]:
    return [
        META,
        user("u0", 1.0, "/help"),
        user("u1", 2.0, [{"text": PROMPT_1_EXPANDED}], display=[{"text": PROMPT_1}]),
        {"$set": {"lastUpdated": ts(2.0)}},
        G1,
        G1_TOK,
        G1_TOOLS,
        {"$set": {"lastUpdated": ts(24.0)}},
        # tool responses are ALSO recorded as a user message — never a prompt
        user(
            "u2",
            24.5,
            [
                {
                    "functionResponse": {
                        "id": "c8",
                        "name": "run_shell_command",
                        "response": {"output": "...\n3 passed\n"},
                    }
                }
            ],
        ),
        gem("g2", 27.0, REPLY_2, model=MODEL, tokens=tokens(12000, 30, 8000, 80)),
        user("u3", 60.0, PROMPT_2),
        gem("g_wrong", 61.0, REPLY_WRONG, model=MODEL, tokens=tokens(14000, 10, 8000, 10)),
        {"$rewindTo": "g_wrong"},
        G_SYN,
        # a record the writer would never stamp this way — counted, never interpolated
        {
            "id": "g4",
            "type": "gemini",
            "content": REPLY_3,
            "model": MODEL,
            "tokens": tokens(15500, 12, 8000, 0),
        },
        {"$set": {"lastUpdated": ts(64.0), "summary": "Added a --dry-run flag to deploy.sh"}},
    ]


def subagent_lines() -> list[dict]:
    """`chats/<parent>/<SUBAGENT_ID>.jsonl` — VERIFIED shape from chatRecordingService.ts
    (`kind`, `directories` only for subagents) and geminiChat.ts `initialize(…, 'subagent')`."""
    instruction = (
        "Find every caller of run() under tests/ and report which ones pass --dry-run. "
        "Do not edit anything."
    )
    return [
        {
            "sessionId": SUBAGENT_ID,
            "projectHash": PROJECT_HASH,
            "startTime": ts(30.0),
            "lastUpdated": ts(30.0),
            "kind": "subagent",
            "directories": ["/Users/dev/proj"],
        },
        # The parent model's instruction, recorded exactly like a typed prompt.
        user("a_u1", 30.5, [{"text": instruction}]),
        gem(
            "a_g1",
            33.0,
            "",
            model=MODEL,
            toolCalls=[
                call(
                    "a_c1",
                    "grep_search",
                    {"pattern": "run\\(", "dir_path": "tests"},
                    34.0,
                    "tests/test_deploy.py:7: run(['--dry-run'])\n",
                ),
                call(
                    "a_c2",
                    "read_file",
                    {"file_path": "/Users/dev/proj/tests/test_deploy.py"},
                    35.0,
                    "def test_dry_run():\n    assert run(['--dry-run']) == 0\n",
                ),
            ],
            tokens=tokens(3000, 12, 0, 40),
        ),
        user(
            "a_u2",
            35.5,
            [
                {
                    "functionResponse": {
                        "id": "a_c2",
                        "name": "read_file",
                        "response": {"output": "def test_dry_run():\n…"},
                    }
                }
            ],
        ),
        gem(
            "a_g2",
            38.0,
            "One caller: tests/test_deploy.py::test_dry_run passes --dry-run.",
            model=MODEL,
            tokens=tokens(3400, 25, 0, 30),
        ),
    ]


def legacy_record() -> dict:
    """What `loadConversationRecord` yields from the JSONL: rewind applied, one record per
    id in first-insertion order — written as one legacy `.json` object."""
    msgs = [
        user("u0", 1.0, "/help"),
        user("u1", 2.0, [{"text": PROMPT_1_EXPANDED}], display=[{"text": PROMPT_1}]),
        G1_TOOLS,
        user(
            "u2",
            24.5,
            [
                {
                    "functionResponse": {
                        "id": "c8",
                        "name": "run_shell_command",
                        "response": {"output": "...\n3 passed\n"},
                    }
                }
            ],
        ),
        gem("g2", 27.0, REPLY_2, model=MODEL, tokens=tokens(12000, 30, 8000, 80)),
        user("u3", 60.0, PROMPT_2),
        G_SYN,
        {
            "id": "g4",
            "type": "gemini",
            "content": REPLY_3,
            "model": MODEL,
            "tokens": tokens(15500, 12, 8000, 0),
        },
    ]
    return dict(
        META, lastUpdated=ts(64.0), summary="Added a --dry-run flag to deploy.sh", messages=msgs
    )


def main() -> int:
    L = jsonl_lines()
    assert len(L) == 16, len(L)
    OUT.mkdir(parents=True, exist_ok=True)
    jsonl = OUT / "synthetic_session.jsonl"
    with jsonl.open("w") as f:
        for r in L:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    legacy = OUT / "synthetic_session_legacy.json"
    legacy.write_text(json.dumps(legacy_record(), indent=2, ensure_ascii=False) + "\n")

    sub = OUT / "synthetic_subagent.jsonl"
    with sub.open("w") as f:
        for r in subagent_lines():
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    s = gemini.scan(jsonl)
    events, derivation = gemini._derive(s)
    legacy_events = gemini.load_events(legacy)
    ss = gemini.scan(sub)
    sub_events, sub_derivation = gemini._derive(ss)
    expected = {
        "_generated_by": "scripts/gen_gemini_fixture.py — do not hand-edit",
        "harness": digest.detect_harness(jsonl),
        "legacy_harness": digest.detect_harness(legacy),
        "events": len(events),
        "stats": digest.stats(events),
        "legacy_stats_equal": digest.stats(legacy_events) == digest.stats(events),
        "usage": s.usage,
        "legacy_usage": gemini.usage(legacy),
        "meta": {k: v for k, v in s.meta.items() if k != "path"},
        "diagnostics": dict(s.diagnostics, derivation=derivation),
        "subagent": {
            "file": sub.name,
            "harness": digest.detect_harness(sub),
            "events": len(sub_events),
            "stats": digest.stats(sub_events),
            "usage": ss.usage,
            "meta": {k: v for k, v in ss.meta.items() if k != "path"},
            "diagnostics": dict(ss.diagnostics, derivation=sub_derivation),
        },
    }
    (OUT / "synthetic_session.expected.json").write_text(
        json.dumps(expected, indent=1, ensure_ascii=False) + "\n"
    )
    print(
        f"wrote {jsonl} ({len(L)} lines), {legacy.name}, {sub.name} ({len(sub_events)} events) "
        f"and expected stats ({len(events)} events; legacy equal: "
        f"{expected['legacy_stats_equal']})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
