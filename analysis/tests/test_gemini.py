"""python3 -m unittest analysis.tests.test_gemini

Two layers. The generated expectation catches drift; the hand-counted invariants are
independent of the loader and are what make agreement evidence rather than tautology
(the fixture is read by eye: 2 prompts — a `/help` and a functionResponse-only user record
are NOT prompts — 8 tool calls, 1 `status: "error"`, +12/-2 lines across one write_file
(5 lines), two replaces (+3/-1, +1/-1) and one heredoc (3 lines), 1 `git commit`, 2 pytest
runs, 3 text replies after a `$rewindTo` removes a fourth, and a gemini message appended
three times whose tokens a naive per-line sum counts three times).
"""

from __future__ import annotations

import json
import os
import pathlib
import tempfile
import unittest

from analysis import digest, gemini

ROOT = pathlib.Path(__file__).resolve().parents[2]
FIX = ROOT / "spec" / "fixtures" / "gemini"
JSONL = FIX / "synthetic_session.jsonl"
LEGACY = FIX / "synthetic_session_legacy.json"
EXPECTED = FIX / "synthetic_session.expected.json"
CODEX = ROOT / "spec" / "fixtures" / "codex" / "synthetic_session.jsonl"
CC_BOUNDARIES = ROOT / "spec" / "fixtures" / "boundaries"
CC_REMOTE = CC_BOUNDARIES / "remote_sdk_prompts.jsonl"

META = {"sessionId": "s1", "projectHash": "p1", "startTime": "2025-01-01T00:00:00.000Z"}


def _write(lines: list, suffix: str = ".jsonl") -> pathlib.Path:
    fd, name = tempfile.mkstemp(suffix=suffix)
    with os.fdopen(fd, "w") as f:
        for ln in lines:
            f.write(ln if isinstance(ln, str) else json.dumps(ln))
            f.write("\n")
    return pathlib.Path(name)


def _msg(mid: str, typ: str, content, sec: int = 1, **kw) -> dict:
    return {
        "id": mid,
        "timestamp": f"2025-01-01T00:00:{sec:02d}.000Z",
        "type": typ,
        "content": content,
        **kw,
    }


class FixtureStats(unittest.TestCase):
    def test_reproduces_expected(self):
        exp = json.loads(EXPECTED.read_text())
        events = gemini.load_events(JSONL)
        self.assertEqual(digest.stats(events), exp["stats"])
        self.assertEqual(gemini.usage(JSONL), exp["usage"])
        self.assertEqual(len(events), exp["events"])

    def test_legacy_json_yields_identical_stats(self):
        self.assertEqual(
            digest.stats(gemini.load_events(LEGACY)), digest.stats(gemini.load_events(JSONL))
        )
        self.assertEqual(gemini.scan(LEGACY).diagnostics["container"], "json")
        self.assertTrue(gemini.usage(LEGACY)["naive_equals_deduped"])

    def test_hand_counted_invariants(self):
        events = gemini.load_events(JSONL)
        st = digest.stats(events)
        self.assertEqual(st["prompts_sent"], 2)
        self.assertEqual(st["replies_received"], 3)
        self.assertEqual(st["tool_calls"], 8)
        self.assertEqual(
            st["tool_mix"], {"run_shell_command": 4, "replace": 2, "read_file": 1, "write_file": 1}
        )
        self.assertEqual(st["errors"], 1)
        self.assertEqual(st["lines_added_agent"], 12)
        self.assertEqual(st["lines_removed_agent"], 2)
        self.assertEqual(st["files_edited"], 4)
        self.assertEqual(st["files_written_via_shell"], 1)
        self.assertEqual(st["git_commits_run"], 1)
        self.assertEqual(st["test_runs"], 2)
        self.assertEqual(st["models"], {"gemini-2.5-pro": 3})
        self.assertEqual(st["interrupts"], 0)
        self.assertGreater(st["wall_seconds"], 0)
        prompts = [e.text for e in events if e.kind == "prompt"]
        # displayContent (what was typed) wins over the @file-expanded content
        self.assertEqual(
            prompts[0], "@scripts/deploy.sh add a --dry-run flag and keep the tests green"
        )
        self.assertEqual(prompts[1], "commit it")
        self.assertFalse(any("Pushing to main" in e.text for e in events))  # rewound
        err = next(e for e in events if e.kind == "result_error")
        self.assertEqual(err.tool, "replace")
        self.assertIn("could not find", err.text)
        w = next(e for e in events if e.tool == "write_file")
        self.assertEqual((w.path, w.added, w.removed), ("/Users/dev/proj/tests/helpers.py", 5, 0))
        r = next(e for e in events if e.tool == "replace")
        self.assertEqual((r.added, r.removed), (3, 1))
        h = next(e for e in events if e.tool == "run_shell_command" and e.added is not None)
        self.assertEqual((h.path, h.added), ("tests/conftest.py", 3))

    def test_usage_naive_overcounts_rewritten_records(self):
        u = gemini.usage(JSONL)
        self.assertEqual(u["gemini_messages_with_tokens"], 4)
        self.assertEqual(u["naive_records_with_tokens"], 6)
        self.assertEqual(u["deduped_by_message_id"]["input"], 51500)
        self.assertGreater(u["naive_sum_all_records"]["input"], u["deduped_by_message_id"]["input"])
        self.assertFalse(u["naive_equals_deduped"])

    def test_meta_and_diagnostics(self):
        m = gemini.meta(JSONL)
        self.assertEqual(m["harness"], "gemini")
        self.assertEqual(m["kind"], "main")
        self.assertEqual(m["model"], "gemini-2.5-pro")
        d = gemini.diagnostics(JSONL)
        self.assertEqual(d["no_timestamp"], 1)
        self.assertEqual(d["record_kinds"]["rewind"], 1)
        self.assertEqual(d["record_kinds"]["message_rewrite"], 2)
        self.assertEqual(d["derivation"]["user_tool_response_records"], 1)
        self.assertEqual(d["derivation"]["prompt_ignored"], 1)
        self.assertEqual(d["derivation"]["function_call_part_deduped"], 1)

    def test_digest_build_dispatches(self):
        d = digest.build(JSONL)
        self.assertIn("harness: gemini", d["text"])
        self.assertEqual(d["stats"]["prompts_sent"], 2)
        self.assertIn("ERROR from replace", d["text"])


class Detection(unittest.TestCase):
    def test_gemini_fixtures_detect_as_gemini(self):
        self.assertEqual(digest.detect_harness(JSONL), "gemini")
        self.assertEqual(digest.detect_harness(LEGACY), "gemini")

    def test_codex_fixture_unchanged(self):
        self.assertEqual(digest.detect_harness(CODEX), "codex")

    def test_claude_code_fixtures_unchanged(self):
        files = sorted(CC_BOUNDARIES.glob("*.jsonl"))
        self.assertTrue(files)
        for f in files:
            self.assertEqual(digest.detect_harness(f), "claude_code", f)

    def test_claude_code_stats_unchanged(self):
        # Measured before the Gemini loader existed; any drift here is a regression.
        st = digest.stats(digest.load_events(CC_REMOTE))
        self.assertEqual(
            (
                st["events"],
                st["prompts_sent"],
                st["tool_calls"],
                st["interrupts"],
                st["wall_seconds"],
            ),
            (58, 3, 54, 1, 433),
        )
        self.assertEqual(st["tool_mix"], {"Bash": 54})

    def test_claude_code_record_with_sessionid_is_not_gemini(self):
        p = _write(
            [
                {
                    "parentUuid": None,
                    "sessionId": "s",
                    "uuid": "u1",
                    "type": "user",
                    "timestamp": "2025-01-01T00:00:00.000Z",
                    "promptSource": "typed",
                    "message": {"role": "user", "content": "hi"},
                }
            ]
        )
        try:
            self.assertEqual(digest.detect_harness(p), "claude_code")
        finally:
            p.unlink()


class Robustness(unittest.TestCase):
    def test_unknown_part_types_and_records_are_counted_not_raised(self):
        p = _write(
            [
                META,
                _msg("u1", "user", [{"text": "hello"}, {"hologram": {"x": 1}}], 1),
                _msg(
                    "g1",
                    "gemini",
                    [{"text": "hi"}, {"inlineData": {"mimeType": "image/png", "data": ""}}],
                    2,
                ),
                _msg("x1", "wibble", "??", 3),
                {"$frobnicate": 1},
                "{this is not json",
                _msg(
                    "g2",
                    "gemini",
                    "",
                    4,
                    toolCalls=[
                        {
                            "id": "c1",
                            "name": "mystery_tool",
                            "args": {"a": 1},
                            "status": "weird",
                            "timestamp": "2025-01-01T00:00:04.000Z",
                        }
                    ],
                ),
            ]
        )
        try:
            events = gemini.load_events(p)  # must not raise
            d = gemini.diagnostics(p)
            self.assertEqual(d["malformed_lines"], 1)
            self.assertEqual(d["record_kinds"]["unknown"], 1)
            self.assertEqual(d["unknown_types"], {"wibble": 1})
            self.assertEqual(d["derivation"]["unknown_part_hologram"], 1)
            self.assertEqual(d["derivation"]["message_type_unknown_wibble"], 1)
            self.assertEqual(d["derivation"]["tool_status_unknown_weird"], 1)
            self.assertEqual([e.kind for e in events], ["prompt", "assistant", "tool"])
        finally:
            p.unlink()

    def test_partial_trailing_line_is_not_consumed(self):
        p = _write([META, _msg("u1", "user", "a", 1)])
        with p.open("a") as f:
            f.write(
                '{"id": "u2", "timestamp": "2025-01-01T00:00:02.000Z", "type": "user", "content": "b'
            )
        try:
            d = gemini.diagnostics(p)
            self.assertTrue(d["partial_trailing_line"])
            self.assertEqual(d["records"], 2)
            self.assertEqual(digest.stats(gemini.load_events(p))["prompts_sent"], 1)
        finally:
            p.unlink()

    def test_tool_responses_and_slash_commands_are_not_prompts(self):
        p = _write(
            [
                META,
                _msg("u0", "user", "/model", 1),
                _msg("u1", "user", "?help", 2),
                _msg("u2", "user", "<session_context>x</session_context>", 3),
                _msg(
                    "u3",
                    "user",
                    [{"functionResponse": {"id": "c", "name": "t", "response": {"output": "ok"}}}],
                    4,
                ),
                _msg("u4", "user", "real prompt", 5),
            ]
        )
        try:
            events = gemini.load_events(p)
            self.assertEqual([e.text for e in events], ["real prompt"])
            d = gemini.diagnostics(p)["derivation"]
            self.assertEqual(d["prompt_ignored"], 3)
            self.assertEqual(d["user_tool_response_records"], 1)
        finally:
            p.unlink()

    def test_rewind_to_unknown_id_clears_everything(self):
        p = _write(
            [META, _msg("u1", "user", "a", 1), _msg("g1", "gemini", "b", 2), {"$rewindTo": "nope"}]
        )
        try:
            self.assertEqual(gemini.load_events(p), [])
            self.assertEqual(
                gemini.diagnostics(p)["record_kinds"]["rewind_unknown_id_cleared_all"], 1
            )
        finally:
            p.unlink()

    def test_set_messages_rebuilds_and_function_call_parts_stand_in(self):
        p = _write(
            [
                META,
                _msg("u1", "user", "old", 1),
                {
                    "$set": {
                        "messages": [
                            _msg("u2", "user", "new", 2),
                            _msg(
                                "g1",
                                "gemini",
                                [
                                    {
                                        "functionCall": {
                                            "name": "read_file",
                                            "args": {"absolute_path": "/w/a.py"},
                                        }
                                    }
                                ],
                                3,
                            ),
                        ]
                    }
                },
            ]
        )
        try:
            events = gemini.load_events(p)
            self.assertEqual([e.kind for e in events], ["prompt", "tool"])
            self.assertEqual(events[0].text, "new")
            self.assertEqual((events[1].tool, events[1].path), ("read_file", "/w/a.py"))
            self.assertEqual(gemini.diagnostics(p)["derivation"]["tool_from_function_call_part"], 1)
        finally:
            p.unlink()

    def test_missing_timestamp_uses_session_start_and_is_counted(self):
        p = _write(
            [
                META,
                {"id": "u1", "type": "user", "content": "no stamp"},
                _msg("g1", "gemini", "r", 5),
            ]
        )
        try:
            events = gemini.load_events(p)
            self.assertEqual(events[0].ts, digest._ts(META["startTime"]))
            self.assertEqual(gemini.diagnostics(p)["derivation"]["message_no_timestamp"], 1)
            self.assertEqual(digest.stats(events)["wall_seconds"], 5)
        finally:
            p.unlink()

    def test_error_detected_from_response_error_without_status(self):
        p = _write(
            [
                META,
                _msg(
                    "g1",
                    "gemini",
                    "",
                    1,
                    toolCalls=[
                        {
                            "id": "c1",
                            "name": "run_shell_command",
                            "args": {"command": "make"},
                            "result": [
                                {
                                    "functionResponse": {
                                        "id": "c1",
                                        "name": "run_shell_command",
                                        "response": {"error": "boom"},
                                    }
                                }
                            ],
                            "status": "success",
                            "timestamp": "2025-01-01T00:00:02.000Z",
                        }
                    ],
                ),
            ]
        )
        try:
            events = gemini.load_events(p)
            self.assertEqual([e.kind for e in events], ["tool", "result_error"])
            self.assertEqual(events[1].text, "boom")
        finally:
            p.unlink()


if __name__ == "__main__":
    unittest.main()
