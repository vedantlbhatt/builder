"""python3 -m unittest analysis.tests.test_claude_code

Line credit for the two ways a Claude Code agent writes a file from scratch, held to what
REAL sessions wrote (Claude Code 2.1.261, `claude -p`, 2026-09-05) and to what git said
about the same files. Both records below are the real ones with paths shortened.

* `Write` → `toolUseResult: {"type": "create", "content": …}`: hello.py's content ends in
  a newline; `wc -l` says 6 and the next commit said `6 insertions(+)`. The digest said 7.
* Bash heredoc followed by more commands on the same line: the file has 2 lines and the
  commit said `2 insertions(+)`. The digest said 3 — it counted the terminator line.
"""

from __future__ import annotations

import json
import os
import pathlib
import tempfile
import unittest

from analysis import digest

HELLO = (
    "#!/usr/bin/env python3\n\ndef main():\n    print('hi')\n\nif __name__ == '__main__': main()\n"
)
HEREDOC_CMD = (
    "mkdir -p tests && cat > tests/test_fail.py <<'EOF'\n"
    "def test_fail():\n    assert 1 == 2\nEOF\n"
    "git add -A && git commit -m 'add failing test'"
)


def _write(lines: list[dict]) -> pathlib.Path:
    fd, name = tempfile.mkstemp(suffix=".jsonl")
    with os.fdopen(fd, "w") as f:
        for r in lines:
            f.write(json.dumps(r) + "\n")
    return pathlib.Path(name)


def _tool_use(ts: str, tid: str, name: str, inp: dict) -> dict:
    return {
        "type": "assistant",
        "timestamp": ts,
        "uuid": f"u-{tid}",
        "sessionId": "s",
        "message": {
            "id": f"msg_{tid}",
            "model": "claude-sonnet-5",
            "role": "assistant",
            "content": [{"type": "tool_use", "id": tid, "name": name, "input": inp}],
            "usage": {"input_tokens": 2, "output_tokens": 10},
        },
    }


def _tool_result(ts: str, tid: str, content: str, tur) -> dict:
    return {
        "type": "user",
        "timestamp": ts,
        "uuid": f"r-{tid}",
        "sessionId": "s",
        "message": {
            "role": "user",
            "content": [{"tool_use_id": tid, "type": "tool_result", "content": content}],
        },
        "toolUseResult": tur,
    }


class WriteCreateLines(unittest.TestCase):
    def test_trailing_newline_is_not_an_extra_line(self):
        path = _write(
            [
                _tool_use(
                    "2026-09-05T17:42:38.000Z",
                    "toolu_1",
                    "Write",
                    {"file_path": "/repo/hello.py", "content": HELLO},
                ),
                _tool_result(
                    "2026-09-05T17:42:41.156Z",
                    "toolu_1",
                    "File created successfully at: /repo/hello.py",
                    {
                        "type": "create",
                        "filePath": "/repo/hello.py",
                        "content": HELLO,
                        "structuredPatch": [],
                    },
                ),
            ]
        )
        try:
            events = digest.load_claude_code_events(path)
        finally:
            path.unlink()
        w = next(e for e in events if e.tool == "Write")
        self.assertEqual((w.added, w.removed, w.path), (6, 0, "/repo/hello.py"))
        self.assertEqual(HELLO.count("\n"), 6)  # what `wc -l` and git counted

    def test_unterminated_last_line_still_counts(self):
        path = _write(
            [
                _tool_use("2026-09-05T17:42:38.000Z", "t", "Write", {"file_path": "/r/a"}),
                _tool_result(
                    "2026-09-05T17:42:39.000Z",
                    "t",
                    "ok",
                    {"type": "create", "filePath": "/r/a", "content": "one\ntwo"},
                ),
                _tool_use("2026-09-05T17:42:40.000Z", "e", "Write", {"file_path": "/r/b"}),
                _tool_result(
                    "2026-09-05T17:42:41.000Z",
                    "e",
                    "ok",
                    {"type": "create", "filePath": "/r/b", "content": ""},
                ),
            ]
        )
        try:
            events = digest.load_claude_code_events(path)
        finally:
            path.unlink()
        added = {e.path: e.added for e in events if e.kind == "tool"}
        self.assertEqual(added, {"/r/a": 2, "/r/b": 0})


class HeredocLines(unittest.TestCase):
    def test_terminator_and_following_commands_are_not_content(self):
        self.assertEqual(digest._bash_file_effect(HEREDOC_CMD), ("tests/test_fail.py", 2))

    def test_heredoc_that_ends_the_command(self):
        cmd = "cat > a.py <<'EOF'\nx = 1\ny = 2\nz = 3\nEOF"
        self.assertEqual(digest._bash_file_effect(cmd), ("a.py", 3))
        # Tab-indented `<<-` terminator, unquoted delimiter, append mode.
        cmd = "cat >> b.txt <<-END\n\tline\n\tEND\necho done"
        self.assertEqual(digest._bash_file_effect(cmd), ("b.txt", 1))

    def test_truncated_heredoc_counts_what_is_there(self):
        # No terminator (the command was cut off): count the body lines present.
        self.assertEqual(digest._bash_file_effect("cat > c <<'EOF'\na\nb\n"), ("c", 2))
        self.assertEqual(digest._bash_file_effect("cat > c <<'EOF'\n"), ("c", 0))

    def test_non_heredoc_writes_unchanged(self):
        self.assertEqual(digest._bash_file_effect("sed -i 's/a/b/' src/x.py"), ("src/x.py", None))
        self.assertEqual(digest._bash_file_effect("printf 'x\\n' >> hello.py"), (None, None))


if __name__ == "__main__":
    unittest.main()
