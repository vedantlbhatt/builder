"""python3 -m unittest analysis.tests.test_codex

Two layers. The generated expectation catches drift; the hand-counted invariants are
independent of the loader and are what make agreement evidence rather than tautology
(the fixture is read by eye: 2 prompts, 8 tool calls, 1 non-zero exit, +4/-2 lines across
two patches, 1 compaction, 1 interrupt, 3 token_count events summing to 30,000 input
naively against a final total of 30,000 input — equal here by construction so the
inequality path is exercised separately below).
"""

from __future__ import annotations

import json
import os
import pathlib
import tempfile
import unittest

from analysis import codex, digest

ROOT = pathlib.Path(__file__).resolve().parents[2]
FIX = ROOT / "spec" / "fixtures" / "codex"
JSONL = FIX / "synthetic_session.jsonl"
EXPECTED = FIX / "synthetic_session.expected.json"
CC_SAMPLE = ROOT / "spec" / "fixtures" / "analysis"


def _write(lines: list) -> pathlib.Path:
    fd, name = tempfile.mkstemp(suffix=".jsonl")
    with os.fdopen(fd, "w") as f:
        for ln in lines:
            f.write(ln if isinstance(ln, str) else json.dumps(ln))
            f.write("\n")
    return pathlib.Path(name)


def _line(ts: str, typ: str, payload) -> dict:
    return {"timestamp": ts, "type": typ, "payload": payload}


class FixtureStats(unittest.TestCase):
    def test_reproduces_expected(self):
        exp = json.loads(EXPECTED.read_text())
        events = codex.load_events(JSONL)
        self.assertEqual(digest.stats(events), exp["stats"])
        self.assertEqual(codex.usage(JSONL), exp["usage"])
        self.assertEqual(len(events), exp["events"])

    def test_hand_counted_invariants(self):
        events = codex.load_events(JSONL)
        st = digest.stats(events)
        self.assertEqual(st["prompts_sent"], 2)
        self.assertEqual(st["interrupts"], 1)
        self.assertEqual(st["compactions"], 1)
        self.assertEqual(st["errors"], 1)
        self.assertEqual(st["tool_calls"], 8)
        self.assertEqual(st["tool_mix"], {"shell": 4, "apply_patch": 2, "exec_command": 2})
        self.assertEqual(st["lines_added_agent"], 4)
        self.assertEqual(st["lines_removed_agent"], 2)
        self.assertEqual(st["files_edited"], 2)
        self.assertEqual(st["git_commits_run"], 1)
        self.assertEqual(st["test_runs"], 2)
        # three agent_message events, each with a response_item copy: replies must be 3
        self.assertEqual(st["replies_received"], 3)
        self.assertEqual(st["models"], {"gpt-5-codex": 3})
        err = next(e for e in events if e.kind == "result_error")
        self.assertEqual(err.tool, "exec_command")
        self.assertIn("Process exited with code 1", err.text)
        patch = next(e for e in events if e.tool == "apply_patch")
        self.assertEqual((patch.path, patch.added, patch.removed), ("scripts/deploy.sh", 3, 1))

    def test_meta_and_usage(self):
        m = codex.meta(JSONL)
        self.assertEqual(m["harness"], "codex")
        self.assertEqual(m["cli_version"], "0.55.0")
        self.assertEqual(m["model"], "gpt-5-codex")
        self.assertEqual(m["cwd"], "/Users/dev/proj")
        u = codex.usage(JSONL)
        self.assertEqual(u["token_count_events"], 3)
        self.assertEqual(u["naive_sum_last_token_usage"]["input_tokens"], 30000)
        self.assertEqual(u["final_total_token_usage"]["input_tokens"], 30000)
        self.assertTrue(u["naive_sum_equals_final_total"])

    def test_digest_build_dispatches(self):
        d = digest.build(JSONL)
        self.assertIn("harness: codex", d["text"])
        self.assertEqual(d["stats"]["prompts_sent"], 2)
        self.assertIn("ERROR from exec_command", d["text"])


class Detection(unittest.TestCase):
    def test_claude_code_file_detects_as_claude_code(self):
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
            self.assertEqual(digest.stats(digest.load_events(p))["prompts_sent"], 1)
        finally:
            p.unlink()

    def test_codex_file_detects_as_codex(self):
        self.assertEqual(digest.detect_harness(JSONL), "codex")

    def test_real_claude_code_fixture_if_present(self):
        for f in sorted(CC_SAMPLE.glob("*.jsonl")) if CC_SAMPLE.exists() else []:
            self.assertEqual(digest.detect_harness(f), "claude_code", f)


class Robustness(unittest.TestCase):
    def test_unknown_record_types_are_counted_not_raised(self):
        p = _write(
            [
                _line("2025-01-01T00:00:00.000Z", "session_meta", {"id": "x", "cwd": "/w"}),
                _line("2025-01-01T00:00:01.000Z", "frobnicate", {"anything": 1}),
                _line("2025-01-01T00:00:02.000Z", "event_msg", {"type": "not_a_real_event"}),
                _line("2025-01-01T00:00:03.000Z", "response_item", {"type": "mystery_item"}),
                _line("2025-01-01T00:00:04.000Z", "world_state", {}),  # known, ignored
                {"type": "event_msg", "payload": {"type": "user_message", "message": "no ts"}},
                "{this is not json",
                _line(
                    "2025-01-01T00:00:05.000Z",
                    "event_msg",
                    {"type": "user_message", "message": "hello"},
                ),
            ]
        )
        try:
            events = codex.load_events(p)  # must not raise
            d = codex.diagnostics(p)
            self.assertEqual(d["unknown_types"], {"frobnicate": 1})
            self.assertEqual(
                d["unknown_payload_types"],
                {"event_msg/not_a_real_event": 1, "response_item/mystery_item": 1},
            )
            self.assertEqual(d["no_timestamp"], 1)
            self.assertEqual(d["malformed_lines"], 1)
            self.assertEqual(d["records"], 7)
            self.assertEqual([e.kind for e in events], ["prompt"])
        finally:
            p.unlink()

    def test_partial_trailing_line_is_not_consumed(self):
        p = _write(
            [
                _line(
                    "2025-01-01T00:00:00.000Z",
                    "event_msg",
                    {"type": "user_message", "message": "a"},
                )
            ]
        )
        with p.open("a") as f:
            f.write(
                '{"timestamp": "2025-01-01T00:00:01.000Z", "type": "event_msg", "payload": {"type": "user_mess'
            )
        try:
            d = codex.diagnostics(p)
            self.assertTrue(d["partial_trailing_line"])
            self.assertEqual(d["records"], 1)
        finally:
            p.unlink()

    def test_environment_context_user_message_is_not_a_prompt(self):
        env = "<environment_context>\n  <cwd>/w</cwd>\n</environment_context>"
        p = _write(
            [
                _line("2025-01-01T00:00:00.000Z", "session_meta", {"id": "x", "cwd": "/w"}),
                _line(
                    "2025-01-01T00:00:00.100Z",
                    "response_item",
                    {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": env}],
                    },
                ),
                _line(
                    "2025-01-01T00:00:00.200Z",
                    "response_item",
                    {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": "real prompt"}],
                    },
                ),
                _line(
                    "2025-01-01T00:00:00.300Z",
                    "event_msg",
                    {"type": "user_message", "message": "real prompt"},
                ),
            ]
        )
        try:
            events = codex.load_events(p)
            prompts = [e for e in events if e.kind == "prompt"]
            self.assertEqual(len(prompts), 1)
            self.assertEqual(prompts[0].text, "real prompt")
            self.assertFalse(any("environment_context" in e.text for e in events))
            d = codex.diagnostics(p)["derivation"]
            self.assertEqual(d["response_item_user_messages"], 2)
            self.assertEqual(d["response_item_user_envelopes"], 1)
        finally:
            p.unlink()

    def test_paginated_item_completed_prompts_and_replies(self):
        p = _write(
            [
                _line(
                    "2025-01-01T00:00:00.000Z",
                    "session_meta",
                    {"id": "x", "cwd": "/w", "history_mode": "paginated"},
                ),
                _line(
                    "2025-01-01T00:00:01.000Z",
                    "event_msg",
                    {
                        "type": "item_completed",
                        "thread_id": "x",
                        "turn_id": "t",
                        "item": {
                            "type": "UserMessage",
                            "id": "i1",
                            "content": [{"type": "text", "text": "fix it"}],
                        },
                    },
                ),
                _line(
                    "2025-01-01T00:00:05.000Z",
                    "response_item",
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "done"}],
                    },
                ),
                _line(
                    "2025-01-01T00:00:05.500Z",
                    "event_msg",
                    {
                        "type": "item_completed",
                        "thread_id": "x",
                        "turn_id": "t",
                        "item": {
                            "type": "AgentMessage",
                            "id": "i2",
                            "content": [{"type": "Text", "text": "done"}],
                        },
                    },
                ),
            ]
        )
        try:
            st = digest.stats(codex.load_events(p))
            self.assertEqual(st["prompts_sent"], 1)
            self.assertEqual(st["replies_received"], 1)
        finally:
            p.unlink()

    def test_naive_sum_diverges_from_final_when_events_repeat(self):
        info = {
            "total_token_usage": {
                "input_tokens": 100,
                "cached_input_tokens": 0,
                "output_tokens": 10,
                "reasoning_output_tokens": 0,
                "total_tokens": 110,
            },
            "last_token_usage": {
                "input_tokens": 100,
                "cached_input_tokens": 0,
                "output_tokens": 10,
                "reasoning_output_tokens": 0,
                "total_tokens": 110,
            },
        }
        p = _write(
            [
                _line(
                    "2025-01-01T00:00:01.000Z",
                    "event_msg",
                    {"type": "token_count", "info": info, "rate_limits": None},
                ),
                _line(
                    "2025-01-01T00:00:02.000Z",
                    "event_msg",
                    {"type": "token_count", "info": info, "rate_limits": None},
                ),
                _line(
                    "2025-01-01T00:00:03.000Z",
                    "event_msg",
                    {"type": "token_count", "info": None, "rate_limits": {}},
                ),
            ]
        )
        try:
            u = codex.usage(p)
            self.assertEqual(u["token_count_events"], 3)
            self.assertEqual(u["token_count_events_with_info"], 2)
            self.assertEqual(u["naive_sum_last_token_usage"]["total_tokens"], 220)
            self.assertEqual(u["final_total_token_usage"]["total_tokens"], 110)
            self.assertFalse(u["naive_sum_equals_final_total"])
        finally:
            p.unlink()

    def test_lenient_timestamps(self):
        for ts in (
            "2025-01-01T00:00:00Z",
            "2025-01-01T00:00:00.5Z",
            "2025-01-01T00:00:00+00:00",
            "2025-01-01T00:00:00.123456Z",
        ):
            p = _write([_line(ts, "event_msg", {"type": "user_message", "message": "x"})])
            try:
                self.assertEqual(len(codex.load_events(p)), 1, ts)
            finally:
                p.unlink()

    def test_shell_heredoc_and_apply_patch_via_shell(self):
        p = _write(
            [
                _line(
                    "2025-01-01T00:00:01.000Z",
                    "response_item",
                    {
                        "type": "function_call",
                        "name": "shell",
                        "arguments": json.dumps(
                            {"command": ["bash", "-lc", "cat > a.py <<'EOF'\nx = 1\ny = 2\nEOF"]}
                        ),
                        "call_id": "c1",
                    },
                ),
                _line(
                    "2025-01-01T00:00:02.000Z",
                    "response_item",
                    {
                        "type": "function_call",
                        "name": "shell",
                        "arguments": json.dumps(
                            {
                                "command": [
                                    "apply_patch",
                                    "*** Begin Patch\n*** Add File: b.py\n+print(1)\n+print(2)\n*** End Patch",
                                ]
                            }
                        ),
                        "call_id": "c2",
                    },
                ),
                _line(
                    "2025-01-01T00:00:03.000Z",
                    "response_item",
                    {
                        "type": "function_call_output",
                        "call_id": "c2",
                        "output": "apply_patch verification failed: bad hunk",
                    },
                ),
            ]
        )
        try:
            events = codex.load_events(p)
            tools = [e for e in events if e.kind == "tool"]
            self.assertEqual((tools[0].path, tools[0].added), ("a.py", 2))
            self.assertEqual((tools[1].path, tools[1].added, tools[1].removed), ("b.py", 2, 0))
            self.assertEqual([e.kind for e in events][-1], "result_error")
            self.assertEqual(digest.stats(events)["files_edited"], 2)
        finally:
            p.unlink()


if __name__ == "__main__":
    unittest.main()
