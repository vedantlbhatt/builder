"""python3 -m unittest analysis.tests.test_cline

Two layers. The generated expectation catches drift; the hand-counted invariants are
independent of the loader and are what make agreement evidence rather than tautology
(the fixture is read by eye: 2 prompts — a `user_feedback` that is only an
`<environment_details>` block is NOT a prompt — 8 tool calls after an `ask: "tool"` and
its identical `say: "tool"` collapse to one, 2 errors (a `FAILED` pytest output and an
`is_error` tool_result), +12/-2 lines across one newFileCreated (5 lines), two
SEARCH/REPLACE edits (+3/-1, +1/-1) and one heredoc (3 lines), 1 `git commit`, 2 pytest
runs, 3 replies, 1 `user_cancelled` interrupt, 1 human edit from task_metadata, and an
index entry whose totals differ from the per-request sum).
"""

from __future__ import annotations

import json
import pathlib
import shutil
import tempfile
import unittest

from analysis import cline, digest

ROOT = pathlib.Path(__file__).resolve().parents[2]
FIX = ROOT / "spec" / "fixtures" / "cline"
TASK_DIR = FIX / "tasks" / "1730711400000"
EXPECTED = FIX / "expected.json"
GEMINI = ROOT / "spec" / "fixtures" / "gemini"
CODEX = ROOT / "spec" / "fixtures" / "codex" / "synthetic_session.jsonl"
CC_BOUNDARIES = ROOT / "spec" / "fixtures" / "boundaries"
CC_REMOTE = CC_BOUNDARIES / "remote_sdk_prompts.jsonl"

BASE_MS = 1_730_711_400_000


def _task(tmp: pathlib.Path, name: str, ui=None, api=None, meta=None) -> pathlib.Path:
    d = tmp / "tasks" / name
    d.mkdir(parents=True)
    if ui is not None:
        (d / cline.UI_FILE).write_text(json.dumps(ui))
    if api is not None:
        (d / cline.API_FILE).write_text(json.dumps(api))
    if meta is not None:
        (d / cline.META_FILE).write_text(json.dumps(meta))
    return d


def _say(ts, kind, text=None, **kw):
    r = {"ts": ts, "type": "say", "say": kind, **kw}
    if text is not None:
        r["text"] = text
    return r


class FixtureStats(unittest.TestCase):
    def test_reproduces_expected(self):
        exp = json.loads(EXPECTED.read_text())
        events = cline.load_events(TASK_DIR)
        self.assertEqual(digest.stats(events), exp["stats"])
        self.assertEqual(cline.usage(TASK_DIR), exp["usage"])
        self.assertEqual(len(events), exp["events"])

    def test_hand_counted_invariants(self):
        events = cline.load_events(TASK_DIR)
        st = digest.stats(events)
        self.assertEqual(st["prompts_sent"], 2)
        self.assertEqual(st["replies_received"], 3)
        self.assertEqual(st["tool_calls"], 8)
        self.assertEqual(
            st["tool_mix"],
            {"execute_command": 4, "replace_in_file": 2, "read_file": 1, "write_to_file": 1},
        )
        self.assertEqual(st["errors"], 2)
        self.assertEqual(st["lines_added_agent"], 12)
        self.assertEqual(st["lines_removed_agent"], 2)
        self.assertEqual(st["files_edited"], 4)
        self.assertEqual(st["files_written_via_shell"], 1)
        self.assertEqual(st["git_commits_run"], 1)
        self.assertEqual(st["test_runs"], 2)
        self.assertEqual(st["interrupts"], 1)
        self.assertEqual(st["human_edits"], 1)
        self.assertEqual(st["models"], {"claude-sonnet-4-5": 3})
        self.assertEqual(st["wall_seconds"], 64)
        prompts = [e.text for e in events if e.kind == "prompt"]
        self.assertEqual(
            prompts[0], "Add a --dry-run flag to scripts/deploy.sh and keep the tests green"
        )
        self.assertEqual(prompts[1], "commit it")  # the <environment_details> tail is gone
        errs = [e for e in events if e.kind == "result_error"]
        self.assertEqual([e.tool for e in errs], ["execute_command", "replace_in_file"])
        self.assertIn("could not find the string", errs[1].text)
        w = next(e for e in events if e.tool == "write_to_file")
        self.assertEqual((w.path, w.added, w.removed), ("tests/helpers.py", 5, 0))
        r = next(e for e in events if e.tool == "replace_in_file")
        self.assertEqual((r.path, r.added, r.removed), ("scripts/deploy.sh", 3, 1))
        h = next(e for e in events if e.tool == "execute_command" and e.added is not None)
        self.assertEqual((h.path, h.added), ("tests/conftest.py", 3))
        he = next(e for e in events if e.kind == "human_edit")
        self.assertEqual((he.path, he.ts), ("tests/test_deploy.py", (BASE_MS + 40_000) / 1000))
        # events are in ui order under equal timestamps; tool ids come from the api file
        self.assertEqual([e.tool_id for e in events if e.kind == "tool"][:3], ["t1", "t2", "t3"])

    def test_usage_reports_ui_sum_and_index_side_by_side(self):
        u = cline.usage(TASK_DIR)
        self.assertEqual(u["api_requests"], 5)
        self.assertEqual(u["api_requests_with_tokens"], 4)
        self.assertEqual(u["api_requests_cancelled"], {"user_cancelled": 1})
        self.assertEqual(u["ui_api_req_started_sum"]["tokensIn"], 50500)
        self.assertEqual(u["ui_api_req_started_sum"]["tokensOut"], 142)
        self.assertEqual(u["ui_api_req_started_sum"]["cacheReads"], 24000)
        self.assertEqual(u["index_totals"]["tokensIn"], 65000)
        self.assertEqual(u["index_totals"]["tokensOut"], 162)
        self.assertFalse(u["ui_equals_index"])

    def test_meta_and_diagnostics(self):
        m = cline.meta(TASK_DIR)
        self.assertEqual(m["harness"], "cline")
        self.assertEqual(m["task_id"], "1730711400000")
        self.assertEqual(m["started_at"], "2024-11-04T09:10:00Z")
        self.assertEqual(m["started_at_source"], "task_dir_name")
        self.assertEqual(m["model"], "claude-sonnet-4-5")
        self.assertEqual(m["provider"], "anthropic")
        self.assertEqual(m["cli_version"], "3.36.0")
        d = cline.diagnostics(TASK_DIR)
        self.assertEqual(d["files_present"], ["api", "index", "meta", "ui"])
        self.assertEqual(d["ts_epoch_ms"], 24)
        self.assertEqual(d["ts_counter_not_clock"], 0)
        self.assertEqual(d["unknown_types"], {})
        self.assertEqual(d["derivation"]["tool_ask_then_say_deduped"], 1)
        self.assertEqual(d["derivation"]["envelope_environment_details"], 2)
        self.assertEqual(d["derivation"]["prompt_empty_after_strip"], 1)
        self.assertEqual(d["derivation"]["ui_tool_paired_with_api_block"], 8)
        self.assertEqual(d["derivation"]["api_tool_blocks_unpaired_completion"], 2)
        self.assertNotIn("api_tool_unclassified_attempt_completion", d["derivation"])

    def test_either_file_or_the_directory_is_accepted(self):
        by_dir = digest.stats(cline.load_events(TASK_DIR))
        self.assertEqual(digest.stats(cline.load_events(TASK_DIR / cline.UI_FILE)), by_dir)
        self.assertEqual(digest.stats(cline.load_events(TASK_DIR / cline.API_FILE)), by_dir)

    def test_digest_build_dispatches(self):
        d = digest.build(TASK_DIR)
        self.assertIn("harness: cline", d["text"])
        self.assertEqual(d["stats"]["prompts_sent"], 2)
        self.assertIn("ERROR from replace_in_file", d["text"])
        self.assertIn("git commits run: 1", d["text"])


class Degradation(unittest.TestCase):
    """The same task with files missing must degrade, not crash, and must say so."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def _copy(self, *names) -> pathlib.Path:
        d = self.tmp / "tasks" / TASK_DIR.name
        d.mkdir(parents=True)
        for n in names:
            shutil.copy(TASK_DIR / n, d / n)
        return d

    def test_ui_only_keeps_the_timeline_and_loses_the_api_error(self):
        d = self._copy(cline.UI_FILE)
        full = digest.stats(cline.load_events(TASK_DIR))
        st = digest.stats(cline.load_events(d))
        self.assertEqual(st["prompts_sent"], full["prompts_sent"])
        self.assertEqual(st["tool_calls"], full["tool_calls"])
        self.assertEqual(st["tool_mix"], full["tool_mix"])
        self.assertEqual(st["lines_added_agent"], full["lines_added_agent"])  # ui payloads suffice
        self.assertEqual(st["wall_seconds"], full["wall_seconds"])
        self.assertEqual(st["errors"], full["errors"] - 1)  # the is_error tool_result lives in api
        self.assertEqual(st["human_edits"], 0)  # task_metadata absent
        self.assertEqual(st["models"], {})  # no model source without metadata/index
        dg = cline.diagnostics(d)
        self.assertEqual(dg["files_present"], ["ui"])
        self.assertEqual(dg["derivation"]["ui_tool_without_api_block"], 8)
        self.assertIsNone(cline.usage(d)["index_totals"])

    def test_api_only_has_no_clock_and_says_so(self):
        d = self._copy(cline.API_FILE)
        events = cline.load_events(d)
        st = digest.stats(events)
        self.assertEqual(st["prompts_sent"], 2)  # <task> and <feedback> unwrapped
        self.assertEqual(st["replies_received"], 3)  # REPLY_1..3 text blocks
        self.assertEqual(st["tool_calls"], 10)  # includes 2 attempt_completion
        self.assertEqual(st["errors"], 1)
        self.assertEqual(st["lines_added_agent"], 12)
        self.assertEqual(st["wall_seconds"], 0)  # every event sits at the task's start
        self.assertTrue(all(e.ts == BASE_MS / 1000 for e in events))
        dg = cline.diagnostics(d)
        self.assertEqual(dg["derivation"]["timeline_from_api_history_no_clock"], 1)
        self.assertEqual(dg["derivation"]["envelope_task"], 1)
        self.assertEqual(dg["derivation"]["envelope_feedback"], 1)
        self.assertEqual(dg["base_ts_source"], "task_dir_name")

    def test_empty_directory_yields_nothing(self):
        d = self.tmp / "tasks" / "1730711400001"
        d.mkdir(parents=True)
        self.assertEqual(cline.load_events(d), [])
        self.assertEqual(cline.diagnostics(d)["files_present"], [])


class Robustness(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_envelopes_are_not_prompts(self):
        env = "<environment_details>\n# Current Time\nnow\n</environment_details>"
        ui = [
            _say(BASE_MS, "task", f"<task>\nfix the build\n</task>\n\n{env}"),
            _say(BASE_MS + 1000, "user_feedback", env),
            _say(BASE_MS + 2000, "user_feedback", "[TASK RESUMPTION] This task was interrupted."),
            _say(
                BASE_MS + 3000,
                "user_feedback",
                "[TASK RESUMPTION] …\n\nNew instructions for task continuation:\n"
                "<user_message>\nalso run the linter\n</user_message>",
            ),
            _say(BASE_MS + 4000, "user_feedback", "<feedback>\nlooks good\n</feedback>"),
            _say(BASE_MS + 5000, "user_feedback", ""),
        ]
        d = _task(self.tmp, "1730711400000", ui=ui)
        events = cline.load_events(d)
        self.assertEqual(
            [e.text for e in events], ["fix the build", "also run the linter", "looks good"]
        )
        der = cline.diagnostics(d)["derivation"]
        self.assertEqual(der["prompt_empty_after_strip"], 3)
        self.assertEqual(der["task_resumption_message"], 2)
        self.assertEqual(der["envelope_environment_details"], 2)

    def test_counter_ts_is_not_scaled_into_seconds(self):
        # SDK-era rows: ts minted by MessageIdMinter (1, 2, 3 …), the directory is a session id
        ui = [
            _say(1, "task", "hello"),
            _say(2, "text", "hi"),
            _say(3, "command", "ls\nOutput:\na b"),
        ]
        d = _task(
            self.tmp,
            "01JBWQ8K1M2N3P4Q5R6S7T8V9W",
            ui=ui,
            meta={
                "model_usage": [
                    {"ts": BASE_MS, "model_id": "m", "model_provider_id": "p", "mode": "act"}
                ]
            },
        )
        events = cline.load_events(d)
        self.assertEqual([e.kind for e in events], ["prompt", "assistant", "tool"])
        self.assertTrue(all(e.ts == BASE_MS / 1000 for e in events))
        dg = cline.diagnostics(d)
        self.assertEqual(dg["ts_counter_not_clock"], 3)
        self.assertEqual(dg["ts_epoch_ms"], 0)
        self.assertEqual(dg["base_ts_source"], "task_metadata_model_usage")
        self.assertEqual(dg["derivation"]["ui_row_placed_at_task_start"], 3)
        self.assertEqual(digest.stats(events)["wall_seconds"], 0)

    def test_unknown_kinds_and_bad_payloads_are_counted_not_raised(self):
        ui = [
            _say(BASE_MS, "task", "x"),
            _say(BASE_MS + 1, "tool", "{not json"),
            _say(BASE_MS + 2, "tool", json.dumps({"tool": "teleport", "path": "a"})),
            _say(BASE_MS + 3, "hologram", "?"),
            {"ts": BASE_MS + 4, "type": "ask", "ask": "wibble", "text": ""},
            {"ts": None, "type": "say", "say": "text", "text": "no stamp"},
            "not an object",
        ]
        api = [
            {"role": "user", "content": "plain string content"},
            {"role": "assistant", "content": [{"type": "sparkle"}]},
        ]
        d = _task(self.tmp, "1730711400000", ui=ui, api=api)
        (d / cline.META_FILE).write_text("{oops")
        events = cline.load_events(d)  # must not raise
        self.assertEqual(
            [e.kind for e in events], ["prompt", "assistant", "tool"]
        )  # no-ts row sits at task start
        self.assertEqual(events[2].tool, "teleport")
        dg = cline.diagnostics(d)
        self.assertEqual(dg["malformed_files"], ["task_metadata.json"])
        self.assertEqual(dg["ui_rows_not_objects"], 1)
        self.assertEqual(dg["unknown_types"], {"say:hologram": 1, "ask:wibble": 1})
        self.assertEqual(dg["unknown_block_types"], {"sparkle": 1})
        self.assertEqual(dg["api_string_content"], 1)
        self.assertEqual(dg["no_timestamp"], 1)
        self.assertEqual(dg["derivation"]["tool_payload_not_json"], 1)
        self.assertEqual(dg["derivation"]["ui_tool_kind_unknown_teleport"], 1)

    def test_cancel_reasons_other_than_user_are_not_interrupts(self):
        ui = [
            _say(BASE_MS, "task", "x"),
            _say(
                BASE_MS + 1,
                "api_req_started",
                json.dumps(
                    {"cancelReason": "streaming_failed", "streamingFailedMessage": "socket hang up"}
                ),
            ),
            _say(BASE_MS + 2, "api_req_started", json.dumps({"cancelReason": "retries_exhausted"})),
            _say(BASE_MS + 3, "api_req_started", json.dumps({"cancelReason": "user_cancelled"})),
            _say(BASE_MS + 4, "error", "Bad gateway"),
        ]
        d = _task(self.tmp, "1730711400000", ui=ui)
        st = digest.stats(cline.load_events(d))
        self.assertEqual(st["interrupts"], 1)
        self.assertEqual(st["errors"], 2)
        der = cline.diagnostics(d)["derivation"]
        self.assertEqual(der["api_req_cancel_streaming_failed"], 1)
        self.assertEqual(der["api_req_cancel_retries_exhausted"], 1)


class Detection(unittest.TestCase):
    def test_cline_task_detects_as_cline(self):
        self.assertEqual(digest.detect_harness(TASK_DIR), "cline")
        self.assertEqual(digest.detect_harness(TASK_DIR / cline.UI_FILE), "cline")
        self.assertEqual(digest.detect_harness(TASK_DIR / cline.API_FILE), "cline")

    def test_task_metadata_alone_is_not_a_task(self):
        self.assertNotEqual(digest.detect_harness(TASK_DIR / cline.META_FILE), "cline")

    def test_other_fixtures_unchanged(self):
        self.assertEqual(digest.detect_harness(CODEX), "codex")
        self.assertEqual(digest.detect_harness(GEMINI / "synthetic_session.jsonl"), "gemini")
        self.assertEqual(digest.detect_harness(GEMINI / "synthetic_session_legacy.json"), "gemini")
        files = sorted(CC_BOUNDARIES.glob("*.jsonl"))
        self.assertTrue(files)
        for f in files:
            self.assertEqual(digest.detect_harness(f), "claude_code", f)

    def test_claude_code_stats_unchanged(self):
        # The tuple pinned before the Gemini loader existed; the Cline loader must not move it.
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

    def test_probe_walks_a_tasks_root(self):
        from analysis import probe

        self.assertEqual(probe._walk(FIX), [TASK_DIR])
        self.assertEqual(probe._walk(FIX / "tasks"), [TASK_DIR])
        self.assertEqual(probe._walk(TASK_DIR), [TASK_DIR])
        r = probe.probe_file(TASK_DIR)
        self.assertEqual(r["harness"], "cline")
        self.assertIn("ui == index: NO", probe.format_probe(r))


if __name__ == "__main__":
    unittest.main()
