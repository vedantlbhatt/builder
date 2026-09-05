#!/usr/bin/env python3
"""Generate the synthetic Cline task fixture and the stats the loader must reproduce.

    spec/fixtures/cline/tasks/1730711400000/ui_messages.json
    spec/fixtures/cline/tasks/1730711400000/api_conversation_history.json
    spec/fixtures/cline/tasks/1730711400000/task_metadata.json
    spec/fixtures/cline/state/taskHistory.json
    spec/fixtures/cline/expected.json

The tree mirrors Cline's data directory (`tasks/<taskId>/…` next to `state/taskHistory.json`)
so the loader's index lookup is exercised. Everything is SYNTHETIC, written in the shapes
verified from the Cline source (see the docstring of analysis/cline.py for per-shape
provenance), not a captured task. It exercises every branch the loader has: the `say:
"task"` opener; an `ask: "tool"` approval followed by the identical `say: "tool"` (one
call, not two); `editedExistingFile` with a SEARCH/REPLACE block and `newFileCreated`;
`say: "command"` rows with the `Output:` marker, one failing (`FAILED`), one a heredoc,
one `git commit`; a replace whose api-side `tool_result` is `is_error` with the
`<error>` envelope; a `user_feedback` row trailing an `<environment_details>` block and
one that is NOTHING but such a block (not a prompt); an `api_req_started` with
`cancelReason: "user_cancelled"` followed by `ask: "resume_task"`; `task_metadata.json`
with a `user_edited` file; and a `taskHistory.json` entry whose totals deliberately differ
from the per-request sum — the two are reported side by side, never reconciled.
"""

from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from analysis import cline, digest

OUT = ROOT / "spec" / "fixtures" / "cline"
BASE_MS = 1_730_711_400_000  # 2024-11-04T09:10:00Z — legacy task ids are `${Date.now()}`
TASK_ID = str(BASE_MS)
MODEL = "claude-sonnet-4-5"
TASK = "Add a --dry-run flag to scripts/deploy.sh and keep the tests green"

ENV = (
    "<environment_details>\n# VSCode Visible Files\nscripts/deploy.sh\n\n"
    "# Current Time\n11/4/2024, 9:10:00 AM (UTC)\n</environment_details>"
)
OLD = "set -e\n"
NEW = 'set -euo pipefail\nDRY_RUN=0\nif [ "${1:-}" = "--dry-run" ]; then DRY_RUN=1; fi\n'
SEARCH_REPLACE_1 = f"------- SEARCH\n{OLD}=======\n{NEW}+++++++ REPLACE"
SEARCH_REPLACE_2 = "------- SEARCH\nexpect 1\n=======\nexpect 0\n+++++++ REPLACE"
WRITE_CONTENT = (
    "import subprocess\n\n\ndef run(args):\n"
    "    return subprocess.call(['scripts/deploy.sh', *args])\n"
)
HEREDOC = "cat > tests/conftest.py <<'EOF'\nimport os\n\nDRY = True\nEOF"
PYTEST_FAIL = "F.\nFAILED tests/test_deploy.py::test_dry_run - AssertionError\n1 failed, 1 passed"
PYTEST_PASS = "...\n3 passed"
TOOL_ERROR = (
    "The tool execution failed with the following error:\n<error>\n"
    "Failed to edit, could not find the string to replace.\n</error>"
)
REPLY_1 = "I'll read the script first, then add the flag."
REPLY_2 = "Tests pass now. `scripts/deploy.sh` accepts `--dry-run`."
REPLY_3 = "Committed as abc1234."


def ms(sec: float) -> int:
    return BASE_MS + int(sec * 1000)


def say(sec, kind, text=None, **kw) -> dict:
    r = {"ts": ms(sec), "type": "say", "say": kind}
    if text is not None:
        r["text"] = text
    r.update(kw)
    return r


def ask(sec, kind, text=None, **kw) -> dict:
    r = {"ts": ms(sec), "type": "ask", "ask": kind}
    if text is not None:
        r["text"] = text
    r.update(kw)
    return r


def req(sec, tokens_in, tokens_out, cache_reads, cost, **kw) -> dict:
    info = {
        "request": f"<task>\n{TASK}\n</task>" if sec == 1 else "[tool results]",
        "tokensIn": tokens_in,
        "tokensOut": tokens_out,
        "cacheWrites": 0,
        "cacheReads": cache_reads,
        "cost": cost,
    }
    info.update(kw)
    return say(sec, "api_req_started", json.dumps(info))


def tool(kind, path, **kw) -> str:
    return json.dumps({"tool": kind, "path": path, **kw})


def command(text, output) -> str:
    return f"{text}\nOutput:\n{output}"


def ui_messages() -> list[dict]:
    read_payload = tool(
        "readFile", "scripts/deploy.sh", content="/Users/dev/proj/scripts/deploy.sh"
    )
    return [
        say(0, "task", TASK),
        req(1, 9000, 40, 0, 0.03),
        say(5, "text", REPLY_1),
        ask(6, "tool", read_payload),  # pending approval …
        say(6, "tool", read_payload),  # … and the approved copy: ONE call
        req(8, 12000, 60, 8000, 0.04),
        say(9, "tool", tool("editedExistingFile", "scripts/deploy.sh", content=SEARCH_REPLACE_1)),
        say(12, "tool", tool("newFileCreated", "tests/helpers.py", content=WRITE_CONTENT)),
        say(15, "command", command("pytest -q tests/", PYTEST_FAIL), commandCompleted=True),
        req(17, 14000, 30, 8000, 0.045),
        say(
            18, "tool", tool("editedExistingFile", "tests/test_deploy.py", content=SEARCH_REPLACE_2)
        ),
        say(20, "command", command(HEREDOC, ""), commandCompleted=True),
        say(24, "command", command("pytest -q tests/", PYTEST_PASS), commandCompleted=True),
        say(27, "completion_result", REPLY_2),
        ask(27, "completion_result", ""),
        say(58, "user_feedback", f"{ENV}"),  # an envelope and nothing else: not a prompt
        say(60, "user_feedback", f"commit it\n\n{ENV}"),
        # cleared mid-request: no cost, cancelReason stamped on save; then resumed
        say(
            61,
            "api_req_started",
            json.dumps({"request": "[tool results]", "cancelReason": "user_cancelled"}),
        ),
        ask(61.5, "resume_task", ""),
        req(62, 15500, 12, 8000, 0.05),
        say(
            63,
            "command",
            command("git commit -am 'Add --dry-run'", "[main abc1234] Add --dry-run"),
            commandCompleted=True,
        ),
        say(64, "completion_result", REPLY_3),
        ask(64, "completion_result", ""),
        say(64.5, "checkpoint_created", lastCheckpointHash="deadbeef"),
    ]


def api_history() -> list[dict]:
    def use(tid, name, inp):
        return {"type": "tool_use", "id": tid, "name": name, "input": inp}

    def result(tid, text, is_error=False):
        b = {"type": "tool_result", "tool_use_id": tid, "content": [{"type": "text", "text": text}]}
        if is_error:
            b["is_error"] = True
        return b

    return [
        {
            "role": "user",
            "content": [{"type": "text", "text": f"<task>\n{TASK}\n</task>\n\n{ENV}"}],
        },
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": REPLY_1},
                use("t1", "read_file", {"path": "scripts/deploy.sh"}),
            ],
        },
        {"role": "user", "content": [result("t1", "#!/bin/bash\nset -e\n…")]},
        {
            "role": "assistant",
            "content": [
                use(
                    "t2", "replace_in_file", {"path": "scripts/deploy.sh", "diff": SEARCH_REPLACE_1}
                )
            ],
        },
        {
            "role": "user",
            "content": [result("t2", "The content was successfully saved to scripts/deploy.sh.")],
        },
        {
            "role": "assistant",
            "content": [
                use("t3", "write_to_file", {"path": "tests/helpers.py", "content": WRITE_CONTENT})
            ],
        },
        {
            "role": "user",
            "content": [result("t3", "The content was successfully saved to tests/helpers.py.")],
        },
        {
            "role": "assistant",
            "content": [
                use(
                    "t4",
                    "execute_command",
                    {"command": "pytest -q tests/", "requires_approval": False},
                )
            ],
        },
        {"role": "user", "content": [result("t4", f"Command executed.\nOutput:\n{PYTEST_FAIL}")]},
        {
            "role": "assistant",
            "content": [
                use(
                    "t5",
                    "replace_in_file",
                    {"path": "tests/test_deploy.py", "diff": SEARCH_REPLACE_2},
                )
            ],
        },
        {"role": "user", "content": [result("t5", TOOL_ERROR, is_error=True)]},
        {
            "role": "assistant",
            "content": [
                use("t6", "execute_command", {"command": HEREDOC, "requires_approval": True})
            ],
        },
        {"role": "user", "content": [result("t6", "Command executed.")]},
        {
            "role": "assistant",
            "content": [
                use(
                    "t7",
                    "execute_command",
                    {"command": "pytest -q tests/", "requires_approval": False},
                )
            ],
        },
        {"role": "user", "content": [result("t7", f"Command executed.\nOutput:\n{PYTEST_PASS}")]},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": REPLY_2},
                use("t8", "attempt_completion", {"result": REPLY_2}),
            ],
        },
        {
            "role": "user",
            "content": [
                result("t8", "The user has provided feedback on the results."),
                {"type": "text", "text": f"<feedback>\ncommit it\n</feedback>\n{ENV}"},
            ],
        },
        {
            "role": "assistant",
            "content": [
                use(
                    "t9",
                    "execute_command",
                    {"command": "git commit -am 'Add --dry-run'", "requires_approval": True},
                )
            ],
        },
        {
            "role": "user",
            "content": [result("t9", "Command executed.\nOutput:\n[main abc1234] Add --dry-run")],
        },
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": REPLY_3},
                use("t10", "attempt_completion", {"result": REPLY_3}),
            ],
        },
    ]


def task_metadata() -> dict:
    return {
        "files_in_context": [
            {
                "path": "scripts/deploy.sh",
                "record_state": "active",
                "record_source": "cline_edited",
                "cline_read_date": ms(6),
                "cline_edit_date": ms(9),
                "user_edit_date": None,
            },
            {
                "path": "tests/helpers.py",
                "record_state": "active",
                "record_source": "cline_edited",
                "cline_read_date": None,
                "cline_edit_date": ms(12),
                "user_edit_date": None,
            },
            {
                "path": "tests/test_deploy.py",
                "record_state": "stale",
                "record_source": "user_edited",
                "cline_read_date": None,
                "cline_edit_date": ms(18),
                "user_edit_date": ms(40),
            },
        ],
        "model_usage": [
            {"ts": ms(0), "model_id": MODEL, "model_provider_id": "anthropic", "mode": "act"}
        ],
        "environment_history": [
            {
                "ts": ms(0),
                "os_name": "darwin",
                "os_version": "24.6.0",
                "os_arch": "arm64",
                "host_name": "vscode",
                "host_version": "1.104.0",
                "cline_version": "3.36.0",
            }
        ],
    }


def task_history() -> list[dict]:
    # The index is written by a different code path at a different moment; its totals
    # are NOT the per-request sum (50,500 in / 142 out / 24,000 cache reads here).
    return [
        {
            "id": TASK_ID,
            "ulid": "01JBWQ8K1M2N3P4Q5R6S7T8V9W",
            "ts": BASE_MS,
            "task": TASK,
            "tokensIn": 65000,
            "tokensOut": 162,
            "cacheWrites": 0,
            "cacheReads": 32000,
            "totalCost": 0.17,
            "size": 48213,
            "cwdOnTaskInitialization": "/Users/dev/proj",
            "modelId": MODEL,
            "apiProvider": "anthropic",
            "isLegacy": True,
        }
    ]


def main() -> int:
    task_dir = OUT / "tasks" / TASK_ID
    task_dir.mkdir(parents=True, exist_ok=True)
    (OUT / "state").mkdir(parents=True, exist_ok=True)
    ui = ui_messages()
    assert len(ui) == 24, len(ui)
    (task_dir / cline.UI_FILE).write_text(json.dumps(ui, indent=2, ensure_ascii=False) + "\n")
    (task_dir / cline.API_FILE).write_text(
        json.dumps(api_history(), indent=2, ensure_ascii=False) + "\n"
    )
    (task_dir / cline.META_FILE).write_text(json.dumps(task_metadata(), indent=2) + "\n")
    (OUT / "state" / "taskHistory.json").write_text(json.dumps(task_history(), indent=2) + "\n")

    s = cline.scan(task_dir)
    events, derivation = cline._derive(s)
    expected = {
        "_generated_by": "scripts/gen_cline_fixture.py — do not hand-edit",
        "harness": digest.detect_harness(task_dir),
        "events": len(events),
        "stats": digest.stats(events),
        "usage": s.usage,
        "meta": {k: v for k, v in s.meta.items() if k != "path"},
        "diagnostics": dict(s.diagnostics, derivation=derivation),
    }
    (OUT / "expected.json").write_text(json.dumps(expected, indent=1, ensure_ascii=False) + "\n")
    print(
        f"wrote {task_dir} ({len(ui)} ui rows), state/taskHistory.json, expected.json ({len(events)} events)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
