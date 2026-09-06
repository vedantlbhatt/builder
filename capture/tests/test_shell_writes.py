"""python3 -m unittest capture.tests.test_shell_writes

An agent that writes files through the shell must not upload a session that says it
wrote nothing.

`analysis/digest.py` has read heredocs since it was written; the Mac's ingest parser and
this uploader did not, so one session produced two numbers: the analyst was handed
"agent lines +2450" and the card next to that prose said +0. MEASURED on this
repository's own container corpus (17 root transcripts, 2026-09-06): 2,452 of 2,458
attributable lines were written with `cat > path <<'EOF'`, so the card was showing 0.2%
of the work. `BuilderParse.ShellFileEffect` is the Swift half of this fix.
"""

from __future__ import annotations

import json
import pathlib
import tempfile
import unittest
import zoneinfo

from capture import sessions
from capture.discover import Transcript

TZ = zoneinfo.ZoneInfo("America/New_York")
SID = "00000000-0000-4000-8000-0000000000aa"

BODY = ["def one():", "    return 1", "", "def two():", "    return 2"]
HEREDOC = "mkdir -p pkg && cat > pkg/thing.py <<'EOF'\n" + "\n".join(BODY) + "\nEOF\ngit add -A"


def _rec(n: int, ts: str, **kw) -> dict:
    return {
        "uuid": f"{n:08d}-0000-4000-8000-000000000000",
        "parentUuid": None,
        "sessionId": SID,
        "timestamp": ts,
        "cwd": "/Users/dev/proj",
        "version": "2.1.0",
        **kw,
    }


def _transcript() -> list[dict]:
    """A typed prompt, one heredoc Bash call, one Edit, and enough elapsed time that the
    session is counted."""
    return [
        _rec(
            1,
            "2026-03-10T15:00:00.000Z",
            type="user",
            message={"role": "user", "content": "write the module"},
            promptSource="typed",
        ),
        _rec(
            2,
            "2026-03-10T15:00:20.000Z",
            type="assistant",
            message={
                "role": "assistant",
                "model": "claude-sonnet-5",
                "id": "msg_a",
                "content": [
                    {"type": "tool_use", "id": "tu_a", "name": "Bash", "input": {"command": HEREDOC}}
                ],
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        ),
        _rec(
            3,
            "2026-03-10T15:00:25.000Z",
            type="user",
            message={"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tu_a"}]},
            toolUseResult={"stdout": "", "stderr": ""},
        ),
        _rec(
            4,
            "2026-03-10T15:05:00.000Z",
            type="assistant",
            message={
                "role": "assistant",
                "model": "claude-sonnet-5",
                "id": "msg_b",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tu_b",
                        "name": "Edit",
                        "input": {"file_path": "/Users/dev/proj/pkg/other.py"},
                    }
                ],
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        ),
        _rec(
            5,
            "2026-03-10T15:05:05.000Z",
            type="user",
            message={"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tu_b"}]},
            toolUseResult={
                "filePath": "/Users/dev/proj/pkg/other.py",
                "structuredPatch": [{"lines": ["+added one", "-gone"]}],
            },
        ),
    ]


class ShellWrites(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.TemporaryDirectory()
        path = pathlib.Path(cls.dir.name) / f"{SID}.jsonl"
        path.write_text("\n".join(json.dumps(r) for r in _transcript()) + "\n")
        src = sessions.load_source(Transcript("fixture", path))
        last = max(r["ts"] for r in src.records)
        pool = sessions.sessionize_sources([src], TZ, now=last + 1)
        assert len(pool) == 1, pool
        cls.payload = sessions.build_payload(pool[0], TZ, "1" * 64, "test", observed_at=last)

    @classmethod
    def tearDownClass(cls):
        cls.dir.cleanup()

    def test_the_heredoc_body_is_counted_and_the_edit_still_is(self):
        # 5 body lines from the heredoc + 1 '+' line from the Edit's patch. The `EOF`
        # terminator and the `git add -A` after it are NOT file content: that exact
        # mistake scored +3 on a two-line file against git's own `2 insertions(+)`.
        self.assertEqual(self.payload["lines_added_agent"], len(BODY) + 1)
        self.assertEqual(self.payload["lines_removed_agent"], 1)

    def test_the_shell_written_file_counts_as_touched(self):
        # Two distinct paths: the heredoc's target and the Edit's file. Dropping shell
        # paths reported one.
        self.assertEqual(self.payload["files_touched"], 2)

    def test_a_shell_only_session_does_not_upload_as_zero_lines(self):
        # The regression in one assertion: strip the Edit and the number must survive.
        recs = [r for r in _transcript() if r["uuid"][:8] not in ("00000004", "00000005")]
        with tempfile.TemporaryDirectory() as d:
            path = pathlib.Path(d) / f"{SID}.jsonl"
            path.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
            src = sessions.load_source(Transcript("fixture", path))
            last = max(r["ts"] for r in src.records)
            pool = sessions.sessionize_sources([src], TZ, now=last + 1)
            p = sessions.build_payload(pool[0], TZ, "1" * 64, "test", observed_at=last)
        self.assertEqual(p["lines_added_agent"], len(BODY))
