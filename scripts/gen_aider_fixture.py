#!/usr/bin/env python3
"""Generate the synthetic Aider fixture and the stats the loader must reproduce.

    spec/fixtures/aider/.aider.chat.history.md
    spec/fixtures/aider/.aider.input.history
    spec/fixtures/aider/expected.json

The directory stands in for a repo: Aider keeps both files at the repo root and appends
every session to them. Everything is SYNTHETIC, written by a `Writer` that reproduces
`InputOutput.append_chat_history` / `user_input` / `_tool_message` / `tool_output` /
`confirm_ask` and prompt_toolkit's `FileHistory.store_string` byte for byte (see the
docstring of analysis/aider.py for per-shape provenance), not a captured transcript.

Three sessions in ONE file, which is the point:

1. `20241112-091000` — the `/commit` session: announcements, a typed prompt with a
   SEARCH/REPLACE edit that is applied and auto-committed, an LLM-suggested shell command
   the person confirmed (`Running …`, stamped by Aider's own `/run` history entry), `/add`,
   a two-line prompt, an assistant reply containing a `#### ` heading and a `> ` blockquote
   WITHOUT the two-space suffix (assistant markdown, not a prompt or a tool line), a
   `/commit`, `/exit`.
2. `20241112-142000` — the failed-edit session: two SEARCH/REPLACE blocks of which one
   fails, written exactly as Aider does — `The LLM did not conform to the edit format.`, the
   multi-line failure body through ONE `tool_output` (one `> ` prefix, one suffix), then a
   `Commit` with NO `Applied edit to` for the block that did apply (the partial-apply trap),
   the reflected retry with no `####` line, a `/test` whose non-zero exit adds output (a
   failure) and is answered by the model unprompted, a `/run`, a mid-stream `^C` (an
   interrupt: the partial reply follows it), and a `^C` `^C` quit at the prompt (not one).
3. `20241112-231500` — a bare `/help`: announcements and the command listing, zero events.

The input history carries stamps with and without microseconds, the confirm answers
(`y`, `n`) prompt_toolkit records alongside prompts, and Aider's own `/run …` entry.
"""

from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from analysis import aider, digest

OUT = ROOT / "spec" / "fixtures" / "aider"

CLAUDE = "claude-3-5-sonnet-20241022"
CLAUDE_WEAK = "claude-3-5-haiku-20241022"
GPT = "gpt-4o"
GPT_WEAK = "gpt-4o-mini"


class Writer:
    """`aider.io.InputOutput` as far as the two history files can tell."""

    def __init__(self) -> None:
        self.chat: list[str] = []
        self.inputs: list[str] = []

    # io.py:1117 append_chat_history
    def append(self, text: str, linebreak=False, blockquote=False, strip=True) -> None:
        if blockquote:
            if strip:
                text = text.strip()
            text = "> " + text
        if linebreak:
            if strip:
                text = text.rstrip()
            text = text + "  \n"
        if not text.endswith("\n"):
            text += "\n"
        self.chat.append(text)

    # prompt_toolkit history.py:298 FileHistory.store_string
    def history(self, stamp: str, text: str) -> None:
        self.inputs.append(f"\n# {stamp}\n" + "".join(f"+{ln}\n" for ln in text.split("\n")))

    # io.py:336
    def session_start(self, stamp: str) -> None:
        self.append(f"\n# aider chat started at {stamp}\n\n")

    # io.py:775 user_input (+ the FileHistory entry PromptSession makes on accept)
    def user_input(self, inp: str, stamp: str | None = None) -> None:
        hist = inp.splitlines() if inp else ["<blank>"]
        self.append("\n#### " + "  \n#### ".join(hist), linebreak=True)
        if stamp is not None:
            self.history(stamp, inp)

    # io.py:960 _tool_message — tool_error / tool_warning split per line
    def tool_message(self, message: str, strip=True) -> None:
        if message.strip():
            if "\n" in message:
                for line in message.splitlines():
                    self.append(line, linebreak=True, blockquote=True, strip=strip)
            else:
                self.append(message.strip() if strip else message, linebreak=True, blockquote=True)

    # io.py:995 tool_output — ONE prefix, ONE suffix, however many lines
    def tool_output(self, *messages: str) -> None:
        if messages:
            self.append(" ".join(messages).strip(), linebreak=True, blockquote=True)

    # io.py:793 ai_output
    def ai_output(self, content: str) -> None:
        self.append("\n" + content.strip() + "\n\n")

    # io.py:838-923 confirm_ask: subject via tool_output, then `question answer`
    def confirm(self, question: str, answer: str, stamp: str, subject: str | None = None):
        if subject:
            self.tool_output(subject)
        self.append(f"{question} {answer}", linebreak=True, blockquote=True)
        self.history(stamp, answer)

    def announce(self, *lines: str) -> None:  # base_coder.py:550 show_announcements
        for line in lines:
            self.tool_output(line)


def sr(path: str, old: str, new: str, lang: str = "python") -> str:
    """One SEARCH/REPLACE block in the `diff` edit format (filename above the fence)."""
    return f"{path}\n```{lang}\n<<<<<<< SEARCH\n{old}=======\n{new}>>>>>>> REPLACE\n```"


# ---------------------------------------------------------------- session 1: /commit
P1 = "Add a --dry-run flag to scripts/deploy.sh"
P2 = "also cover the flag in the tests\nkeep it short"
DEPLOY_OLD = "set -e\n"
DEPLOY_NEW = 'set -euo pipefail\nDRY_RUN=0\nif [ "${1:-}" = "--dry-run" ]; then DRY_RUN=1; fi\n'
TEST_OLD = "def test_deploy():\n    assert run([]) == 0\n"
TEST_NEW = TEST_OLD + "\n\ndef test_dry_run():\n    assert run(['--dry-run']) == 0\n"
REPLY_1 = (
    "I'll add the flag and keep the existing behaviour as the default.\n\n"
    + sr("scripts/deploy.sh", DEPLOY_OLD, DEPLOY_NEW, "bash")
    + "\n\nYou can run the tests with:\n\n```bash\npytest -q tests/\n```"
)
REPLY_2 = "#### Notes\n\nA second test exercises the flag.\n\n> remember to run the tests\n\n" + sr(
    "tests/test_deploy.py", TEST_OLD, TEST_NEW
)

# ---------------------------------------------------------------- session 2: failed edit
P3 = "rename run() to invoke() everywhere"
P4 = "explain the module"
HELPER_OLD_WRONG = "def run(args):\n    return subprocess.call(args)\n"
HELPER_OLD = "def run(args):\n    return subprocess.call(['scripts/deploy.sh', *args])\n"
HELPER_NEW = "def invoke(args):\n    return subprocess.call(['scripts/deploy.sh', *args])\n"
REPLY_3 = (
    "Renaming the helper and its call site.\n\n"
    + sr("scripts/deploy.sh", "run_deploy() {\n", "invoke_deploy() {\n", "bash")
    + "\n\n"
    + sr(
        "tests/helpers.py",
        HELPER_OLD_WRONG,
        "def invoke(args):\n    return subprocess.call(args)\n",
    )
)
SR_FAILURE = f"""# 1 SEARCH/REPLACE block failed to match!

## SearchReplaceNoExactMatch: This SEARCH block failed to exactly match lines in tests/helpers.py
<<<<<<< SEARCH
{HELPER_OLD_WRONG}=======
def invoke(args):
    return subprocess.call(args)
>>>>>>> REPLACE

Did you mean to match some of these actual lines from tests/helpers.py?

```
{HELPER_OLD}```

The SEARCH section must exactly match an existing block of lines including all white space, comments, indentation, docstrings, etc

# The other 1 SEARCH/REPLACE block were applied successfully.
Don't re-send them.
Just reply with fixed versions of the block above that failed to match."""
REPLY_4 = "Here is the corrected block.\n\n" + sr("tests/helpers.py", HELPER_OLD, HELPER_NEW)
REPLY_5 = "The test still imports `run`; updating it.\n\n" + sr(
    "tests/test_deploy.py", "from helpers import run\n", "from helpers import invoke\n"
)
REPLY_6 = "The module wraps the deploy script so tests can"  # cut off by ^C

HELP_LINES = (
    "/add             Add files to the chat so aider can edit them or review them in detail",
    "/ask             Ask questions about the code base without editing any files. If no prompt provided, switches to ask mode.",
    "/commit          Commit edits to the repo made outside the chat (commit message optional)",
    "/help            Ask questions about aider",
    "/run             Run a shell command and optionally add the output to the chat (alias: !)",
    "/test            Run a shell command and add the output to the chat on non-zero exit code",
)


def write_fixture() -> Writer:
    w = Writer()

    # ---- session 1 ---------------------------------------------------------------
    w.session_start("2024-11-12 09:10:00")
    w.announce(
        "Aider v0.86.1",
        f"Main model: {CLAUDE} with diff edit format, prompt cache, infinite output",
        f"Weak model: {CLAUDE_WEAK}",
        "Git repo: .git with 42 files",
        "Repo-map: using 4096 tokens, auto refresh",
        "Added scripts/deploy.sh to the chat.",
    )
    w.user_input(P1, "2024-11-12 09:10:30.123456")
    w.ai_output(REPLY_1)
    w.tool_output(
        "Tokens: 8.2k sent, 1.1k cache write, 340 received. Cost: $0.03 message, $0.03 session."
    )
    w.tool_output("Applied edit to scripts/deploy.sh")
    w.tool_output("Commit a1b2c3d feat: add --dry-run flag to deploy.sh")
    w.confirm(
        "Run shell command? (Y)es/(N)o/(D)on't ask again [Yes]:",
        "y",
        "2024-11-12 09:10:58.000001",
        subject="pytest -q tests/",
    )
    w.tool_output("Running pytest -q tests/")
    w.history("2024-11-12 09:11:05.400000", "/run pytest -q tests/")  # base_coder.py:2471
    w.confirm(
        "Add command output to the chat? (Y)es/(N)o/(D)on't ask again [Yes]:",
        "n",
        "2024-11-12 09:11:09",
    )
    w.tool_output("You can use /undo to undo and discard each aider commit.")
    w.user_input("/add tests/test_deploy.py", "2024-11-12 09:12:00")
    w.tool_output("Added tests/test_deploy.py to the chat")
    w.user_input(P2, "2024-11-12 09:12:20.500000")
    w.ai_output(REPLY_2)
    w.tool_output(
        "Tokens: 9.0k sent, 1.1k cache hit, 210 received. Cost: $0.02 message, $0.05 session."
    )
    w.tool_output("Applied edit to tests/test_deploy.py")
    w.tool_output("Commit c3d4e5f test: cover --dry-run")
    w.user_input("/commit", "2024-11-12 09:14:00")
    w.tool_output("Commit d4e5f6a chore: tidy deploy script comments")
    w.user_input("/exit", "2024-11-12 09:14:30")

    # ---- session 2 ---------------------------------------------------------------
    w.session_start("2024-11-12 14:20:00")
    w.announce(
        "Aider v0.86.1",
        f"Main model: {GPT} with diff edit format",
        f"Weak model: {GPT_WEAK}",
        "Git repo: .git with 43 files",
        "Repo-map: using 4096 tokens, auto refresh",
        "Added scripts/deploy.sh to the chat.",
        "Added tests/helpers.py to the chat.",
    )
    w.user_input(P3, "2024-11-12 14:20:15")
    w.ai_output(REPLY_3)
    w.tool_output("Tokens: 7.5k sent, 480 received. Cost: $0.04 message, $0.04 session.")
    w.tool_message("The LLM did not conform to the edit format.")
    w.tool_output("https://aider.chat/docs/troubleshooting/edit-errors.html")
    w.tool_output()
    w.tool_output(SR_FAILURE)
    w.tool_output("Commit e5f6a7b refactor: rename run_deploy to invoke_deploy")
    w.ai_output(REPLY_4)  # the reflected retry: no #### line
    w.tool_output("Tokens: 8.1k sent, 620 received. Cost: $0.05 message, $0.09 session.")
    w.tool_output("Applied edit to tests/helpers.py")
    w.tool_output("Commit f6a7b8c refactor: rename helper to invoke")
    w.user_input("/test pytest -q", "2024-11-12 14:23:00")
    w.tool_output("Added 14 lines of output to the chat.")
    w.ai_output(REPLY_5)  # the failure text was sent as the next message, unprompted
    w.tool_output("Tokens: 8.4k sent, 150 received. Cost: $0.03 message, $0.12 session.")
    w.tool_output("Applied edit to tests/test_deploy.py")
    w.tool_output("Commit a7b8c9d test: import invoke")
    w.user_input("/run pytest -q", "2024-11-12 14:24:10")
    w.confirm(
        "Add 0.1k tokens of command output to the chat? (Y)es/(N)o [Yes]:",
        "n",
        "2024-11-12 14:24:14",
    )
    w.user_input(P4, "2024-11-12 14:25:00")
    w.tool_message("\n\n^C again to exit")  # keyboard_interrupt() from inside send()
    w.ai_output(REPLY_6)  # the partial reply, written by send()'s finally
    w.tool_output("Tokens: 6.1k sent, 90 received. Cost: $0.01 message, $0.13 session.")
    w.tool_message("\n\n^C again to exit")  # ^C at the input prompt …
    w.tool_message("\n\n^C KeyboardInterrupt")  # … and again within 2 s: exit

    # ---- session 3 ---------------------------------------------------------------
    w.session_start("2024-11-12 23:15:00")
    w.announce(
        "Aider v0.86.1",
        f"Main model: {CLAUDE} with diff edit format, prompt cache, infinite output",
        f"Weak model: {CLAUDE_WEAK}",
        "Git repo: .git with 44 files",
        "Repo-map: using 4096 tokens, auto refresh",
        "Added scripts/deploy.sh to the chat.",
    )
    w.user_input("/help", "2024-11-12 23:15:05")
    for line in HELP_LINES:
        w.tool_output(line)
    w.tool_output()
    w.tool_output("Use `/help <question>` to ask questions about how to use aider.")
    return w


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    w = write_fixture()
    chat = OUT / aider.CHAT_FILE
    chat.write_text("".join(w.chat), encoding="utf-8")
    (OUT / aider.INPUT_FILE).write_text("".join(w.inputs), encoding="utf-8")

    sessions = aider.list_sessions(chat)
    assert [s.id for s in sessions] == [
        "20241112-091000",
        "20241112-142000",
        "20241112-231500",
    ], [s.id for s in sessions]
    expected: dict = {
        "_generated_by": "scripts/gen_aider_fixture.py — do not hand-edit",
        "harness": digest.detect_harness(chat),
        "sessions": {},
    }
    for s in sessions:
        sc = aider.scan(chat / s.id)
        events, derivation = aider._derive(sc)
        expected["sessions"][s.id] = {
            "events": len(events),
            "stats": digest.stats(events),
            "usage": sc.usage,
            "meta": {k: v for k, v in sc.meta.items() if k != "path"},
            "diagnostics": dict(sc.diagnostics, derivation=derivation),
        }
    (OUT / "expected.json").write_text(json.dumps(expected, indent=1, ensure_ascii=False) + "\n")
    print(
        f"wrote {chat.name} ({len(w.chat)} writes, {len(sessions)} sessions), "
        f"{aider.INPUT_FILE} ({len(w.inputs)} entries), expected.json"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
