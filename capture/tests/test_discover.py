"""python3 -m unittest capture.tests.test_discover

The root allowlist: `<projectdir>/<uuid>.jsonl` and nothing deeper. A denylist on
`subagents/` would wave `workflows/` and `tool-results/` through as roots — and, as the
engine's own test insists, `<uuid>/futuredir/x.jsonl` must be a sidecar too.
"""

from __future__ import annotations

import pathlib
import tempfile
import unittest

from capture import identity
from capture.discover import Transcript, is_root_transcript, iter_root_transcripts

UUID = "00000000-0000-4000-8000-000000000001"


class RootAllowlist(unittest.TestCase):
    def test_shape_rule(self):
        self.assertTrue(is_root_transcript(f"{UUID}.jsonl"))
        self.assertFalse(is_root_transcript(f"{UUID}/subagents/agent-1.jsonl"))
        self.assertFalse(is_root_transcript(f"{UUID}/workflows/w.jsonl"))
        self.assertFalse(is_root_transcript(f"{UUID}/tool-results/t.jsonl"))
        self.assertFalse(is_root_transcript(f"{UUID}/futuredir/x.jsonl"))
        self.assertFalse(is_root_transcript(f"{UUID}.json"))
        self.assertFalse(is_root_transcript("notes.txt"))

    def test_tree_walk_finds_only_roots(self):
        with tempfile.TemporaryDirectory() as d:
            root = pathlib.Path(d)
            proj = root / "-home-user-proj"
            (proj / UUID / "subagents").mkdir(parents=True)
            (proj / UUID / "workflows").mkdir()
            (proj / UUID / "tool-results").mkdir()
            (proj / UUID / "futuredir").mkdir()
            (proj / f"{UUID}.jsonl").write_text("")
            (proj / UUID / "subagents" / "agent-a.jsonl").write_text("")
            (proj / UUID / "workflows" / "w.jsonl").write_text("")
            (proj / UUID / "tool-results" / "r.jsonl").write_text("")
            (proj / UUID / "futuredir" / "x.jsonl").write_text("")
            # A project dir with zero transcripts exists legitimately (worktree entered
            # mid-session) and must simply be skipped.
            (root / "-home-user-worktree" / "abc" / "subagents").mkdir(parents=True)
            (root / "stray.jsonl").write_text("")  # not under a project dir: not a root

            found = iter_root_transcripts(root)
            self.assertEqual([t.path for t in found], [proj / f"{UUID}.jsonl"])
            self.assertEqual(found[0].project_dir, "-home-user-proj")
            self.assertEqual(found[0].descriptor, f"-home-user-proj/{UUID}.jsonl")

    def test_source_id_uses_the_relative_descriptor(self):
        """The engine hashes `<projectdir>/<file>`, never the absolute path, so the same
        transcript under two roots is one source."""
        a = Transcript("-p", pathlib.Path("/tmp/a/-p") / f"{UUID}.jsonl")
        b = Transcript("-p", pathlib.Path("/home/x/.claude/projects/-p") / f"{UUID}.jsonl")
        self.assertEqual(identity.source_id(a.descriptor), identity.source_id(b.descriptor))
        self.assertEqual(
            identity.source_id(a.descriptor),
            identity.sha256_hex(f"builder-source-v1|claude_code|-p/{UUID}.jsonl"),
        )

    def test_client_session_id_is_a_function_of_source_and_first_record(self):
        uid = identity.event_uid(identity.source_id(f"-p/{UUID}.jsonl"), "rec-1")
        self.assertEqual(
            identity.client_session_id(uid),
            identity.sha256_hex(f"builder-session-v1|claude_code|capture|{uid}"),
        )


if __name__ == "__main__":
    unittest.main()
