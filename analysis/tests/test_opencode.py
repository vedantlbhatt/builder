"""python3 -m unittest analysis.tests.test_opencode

Two layers. The generated expectation catches drift; the hand-counted invariants are
independent of the loader and are what make agreement evidence rather than tautology
(the fixture is read by eye: 4 prompts — a compaction-only user message and the
synthetic `/shell` message are NOT prompts — 4 replies with the compaction summary
skipped, 12 tool calls including the one the human ran through `!ls` and the one the
abort cut short, 3 errors (pytest `exit: 1`, an `edit` in state `error`, a retried
APIError), 1 `MessageAbortedError` interrupt, 1 compaction, +11/-1 lines across one
filediff-credited edit (+3/-1), one write (5 lines) and one heredoc (3 lines), 2
`git commit` runs (one aborted), 2 pytest runs; and a message whose three step-finish
parts sum to 35,000 input tokens while its own `tokens.input` says 14,000).
"""

from __future__ import annotations

import json
import pathlib
import shutil
import sqlite3
import tempfile
import unittest

from analysis import digest, opencode

ROOT = pathlib.Path(__file__).resolve().parents[2]
FIX = ROOT / "spec" / "fixtures" / "opencode"
DB = FIX / "opencode.db"
SESSION_ID = "ses_7fa2c1e9b3d4f5a6b7c8d9e0f1a2"
CHILD_ID = "ses_7fa2c1e9b3d4f5a6b7c8d9e0f1a1"
PROJECT_ID = "3f2a9c1e7b6d5a4c8e9f0a1b2c3d4e5f6a7b8c9d"
SQLITE_SESSION = DB / SESSION_ID
JSON_SESSION = FIX / "storage" / "session" / PROJECT_ID / f"{SESSION_ID}.json"
EXPORT = FIX / "export.json"
EXPECTED = FIX / "expected.json"
GEMINI = ROOT / "spec" / "fixtures" / "gemini"
CODEX = ROOT / "spec" / "fixtures" / "codex" / "synthetic_session.jsonl"
CLINE_TASK = ROOT / "spec" / "fixtures" / "cline" / "tasks" / "1730711400000"
CC_BOUNDARIES = ROOT / "spec" / "fixtures" / "boundaries"
CC_REMOTE = CC_BOUNDARIES / "remote_sdk_prompts.jsonl"

BASE_MS = 1_749_564_000_000


class FixtureStats(unittest.TestCase):
    def test_reproduces_expected(self):
        exp = json.loads(EXPECTED.read_text())
        for key, path in (
            ("sqlite", SQLITE_SESSION),
            ("sqlite_child", DB / CHILD_ID),
            ("json_dir", JSON_SESSION),
            ("export_json", EXPORT),
        ):
            with self.subTest(container=key):
                s = opencode.scan(path)
                events, derivation = opencode._derive(s)
                self.assertEqual(digest.stats(events), exp[key]["stats"])
                self.assertEqual(s.usage, exp[key]["usage"])
                self.assertEqual(len(events), exp[key]["events"])
                self.assertEqual(derivation, exp[key]["diagnostics"]["derivation"])

    def test_three_containers_agree(self):
        by = {p: opencode.load_events(p) for p in (SQLITE_SESSION, JSON_SESSION, EXPORT)}
        stats = [digest.stats(e) for e in by.values()]
        self.assertEqual(stats[0], stats[1])
        self.assertEqual(stats[0], stats[2])
        kinds = [[(e.kind, e.tool, e.ts) for e in ev] for ev in by.values()]
        self.assertEqual(kinds[0], kinds[1])
        self.assertEqual(kinds[0], kinds[2])
        self.assertEqual(
            [opencode.scan(p).container for p in by], ["sqlite", "json_dir", "export_json"]
        )

    def test_hand_counted_invariants(self):
        events = opencode.load_events(SQLITE_SESSION)
        st = digest.stats(events)
        self.assertEqual(st["prompts_sent"], 4)
        self.assertEqual(st["replies_received"], 4)
        self.assertEqual(st["tool_calls"], 12)
        self.assertEqual(
            st["tool_mix"],
            {"bash": 6, "edit": 2, "read": 1, "write": 1, "websearch": 1, "task": 1},
        )
        self.assertEqual(st["errors"], 3)
        self.assertEqual(st["interrupts"], 1)
        self.assertEqual(st["compactions"], 1)
        self.assertEqual(st["human_edits"], 0)
        self.assertEqual(st["lines_added_agent"], 11)
        self.assertEqual(st["lines_removed_agent"], 1)
        self.assertEqual(st["files_edited"], 4)  # the failed edit still names its file
        self.assertEqual(st["files_written_via_shell"], 1)
        self.assertEqual(st["git_commits_run"], 2)
        self.assertEqual(st["test_runs"], 2)
        self.assertEqual(st["models"], {"claude-sonnet-4-5": 4})
        self.assertEqual(st["wall_seconds"], 116)
        prompts = [e.text for e in events if e.kind == "prompt"]
        self.assertEqual(
            prompts[0], "Add a --dry-run flag to scripts/deploy.sh and keep the tests green"
        )
        self.assertEqual(prompts[1:3], ["commit it", "go ahead and commit"])
        errs = [e for e in events if e.kind == "result_error"]
        self.assertEqual([e.tool for e in errs], ["bash", "edit", "api_request"])
        self.assertIn("FAILED", errs[0].text)
        self.assertIn("oldString not found", errs[1].text)
        self.assertEqual(errs[2].text, "Overloaded")
        e = next(x for x in events if x.tool == "edit")
        self.assertEqual((e.path, e.added, e.removed), ("/Users/dev/proj/scripts/deploy.sh", 3, 1))
        w = next(x for x in events if x.tool == "write")
        self.assertEqual((w.path, w.added, w.removed), ("/Users/dev/proj/tests/helpers.py", 5, 0))
        h = next(x for x in events if x.tool == "bash" and x.added is not None)
        self.assertEqual((h.path, h.added), ("tests/conftest.py", 3))
        # the interrupt sits at the aborted message's completion, after its bash part
        i = next(x for x in events if x.kind == "interrupt")
        self.assertEqual(i.ts, (BASE_MS + 61_000) / 1000)
        self.assertEqual(
            [x.tool_id for x in events if x.kind == "tool"][:3], ["call_01", "call_02", "call_03"]
        )
        # no text of the compaction summary reaches the digest
        self.assertFalse(any("Summary:" in x.text for x in events))

    def test_usage_reports_message_sum_step_sum_and_row(self):
        u = opencode.usage(SQLITE_SESSION)
        self.assertEqual(u["assistant_messages"], 6)
        self.assertEqual(u["assistant_messages_with_tokens"], 4)
        self.assertEqual(u["summary_messages"], 1)
        self.assertEqual(u["step_finish_parts"], 6)
        self.assertEqual(u["assistant_messages_multi_step"], 1)
        msg, step, row = (
            u["sum_message_tokens"],
            u["sum_step_finish_tokens"],
            u["session_row_tokens"],
        )
        self.assertEqual(
            (msg["input"], msg["output"], msg["cache_read"], msg["cache_write"]),
            (62500, 467, 24000, 0),
        )
        self.assertEqual(
            (step["input"], step["output"], step["cache_read"], step["cache_write"]),
            (83500, 567, 32000, 2000),
        )
        self.assertEqual(row, msg)
        self.assertFalse(u["message_sum_equals_step_sum"])
        self.assertTrue(u["session_row_equals_message_sum"])
        # cost accumulates on the message, so the three agree there
        self.assertEqual(u["sum_message_cost"], 0.28)
        self.assertEqual(u["sum_step_finish_cost"], 0.28)
        self.assertEqual(u["session_row_cost"], 0.28)
        self.assertEqual(u["session_message_projection_rows"], 0)
        self.assertIsNone(opencode.usage(EXPORT)["session_message_projection_rows"])

    def test_meta_and_diagnostics(self):
        m = opencode.meta(SQLITE_SESSION)
        self.assertEqual(m["harness"], "opencode")
        self.assertEqual(m["session_id"], SESSION_ID)
        self.assertEqual(m["project_id"], PROJECT_ID)
        self.assertEqual(m["cli_version"], "1.18.29")
        self.assertEqual(m["model"], "claude-sonnet-4-5")
        self.assertEqual(m["provider"], "anthropic")
        self.assertEqual(m["cwd"], "/Users/dev/proj")
        self.assertEqual(m["worktree"], "/Users/dev/proj")
        self.assertEqual(m["started_at"], "2025-06-10T14:00:00Z")
        self.assertFalse(m["is_child"])
        self.assertEqual(m["child_sessions"], [CHILD_ID])
        self.assertEqual(m["session_selected_by"], "path")
        d = opencode.diagnostics(SQLITE_SESSION)
        self.assertEqual(d["container"], "sqlite")
        self.assertIn("session", d["tables"])
        self.assertEqual(d["last_migration"], "20260622202450_simplify_session_input")
        self.assertEqual(d["unknown_types"], {})
        self.assertEqual(d["unknown_tools"], {})
        self.assertEqual(d["unknown_error_names"], {})
        self.assertEqual(d["assistant_error_names"], {"MessageAbortedError": 1})
        der = d["derivation"]
        self.assertEqual(der["prompt_from_text"], 4)
        self.assertEqual(der["tool_aborted_interrupted"], 1)
        self.assertEqual(der["interrupt_aborted"], 1)
        self.assertEqual(der["summary_text_skipped"], 1)
        self.assertEqual(der["tool_run_by_user"], 1)
        self.assertEqual(der["user_shell_command"], 1)
        self.assertEqual(der["shell_nonzero_exit"], 1)
        self.assertEqual(der["edit_credit_from_filediff"], 1)
        self.assertEqual(der["edit_or_write_failed_no_credit"], 1)  # the errored edit
        self.assertNotIn("edit_credit_from_strings", der)
        self.assertEqual(der["task_child_sessions"], 1)
        self.assertNotIn("prompt_from_subtask", der)

    def test_child_session_has_no_human_prompts(self):
        events = opencode.load_events(DB / CHILD_ID)
        st = digest.stats(events)
        self.assertEqual(st["prompts_sent"], 0)  # the parent agent wrote that text
        self.assertEqual(st["replies_received"], 1)
        self.assertEqual(st["tool_mix"], {"read": 1})
        m = opencode.meta(DB / CHILD_ID)
        self.assertTrue(m["is_child"])
        self.assertEqual(m["parent_id"], SESSION_ID)
        self.assertEqual(
            opencode.diagnostics(DB / CHILD_ID)["derivation"]["prompt_in_child_session"], 1
        )

    def test_bare_database_resolves_to_latest_root_session(self):
        m = opencode.meta(DB)
        self.assertEqual(m["session_id"], SESSION_ID)  # the child is newer but not a root
        self.assertEqual(m["session_selected_by"], "latest_root_session")
        self.assertEqual(digest.stats(digest.load_events(DB))["prompts_sent"], 4)

    def test_digest_build_dispatches(self):
        d = digest.build(SQLITE_SESSION)
        self.assertIn("harness: opencode", d["text"])
        self.assertEqual(d["stats"]["prompts_sent"], 4)
        self.assertIn("ERROR from edit", d["text"])
        self.assertIn("INTERRUPT", d["text"])
        self.assertIn("CONTEXT COMPACTED", d["text"])
        self.assertIn("git commits run: 2", d["text"])


class Robustness(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def _db(self) -> pathlib.Path:
        dst = self.tmp / "opencode.db"
        shutil.copy(DB, dst)
        return dst

    def test_pending_revert_is_skipped_and_counted(self):
        db = self._db()
        con = sqlite3.connect(db)
        # revert to the fourth message (the "commit it" turn): it and everything after go
        con.execute(
            "UPDATE session SET revert = ? WHERE id = ?",
            (json.dumps({"messageID": "msg_00000000000000000000000003"}), SESSION_ID),
        )
        con.commit()
        con.close()
        events = opencode.load_events(db / SESSION_ID)
        st = digest.stats(events)
        self.assertEqual(st["prompts_sent"], 1)
        self.assertEqual(st["interrupts"], 0)
        d = opencode.diagnostics(db / SESSION_ID)
        self.assertEqual(d["derivation"]["reverted_pending_cleanup"], 10)
        self.assertTrue(opencode.meta(db / SESSION_ID)["revert_pending"])

    def test_older_database_without_usage_columns(self):
        db = self._db()
        con = sqlite3.connect(db)
        for col in (
            "cost",
            "tokens_input",
            "tokens_output",
            "tokens_reasoning",
            "tokens_cache_read",
            "tokens_cache_write",
        ):
            con.execute(f"ALTER TABLE session DROP COLUMN {col}")
        con.execute("DROP TABLE session_message")
        con.execute("DROP TABLE migration")
        con.commit()
        con.close()
        u = opencode.usage(db / SESSION_ID)
        self.assertIsNone(u["session_row_tokens"])
        self.assertIsNone(u["session_row_cost"])
        self.assertFalse(u["session_row_equals_message_sum"])
        self.assertIsNone(u["session_message_projection_rows"])
        self.assertEqual(u["sum_step_finish_tokens"]["input"], 83500)
        d = opencode.diagnostics(db / SESSION_ID)
        self.assertNotIn("last_migration", d)
        self.assertEqual(digest.stats(opencode.load_events(db / SESSION_ID))["prompts_sent"], 4)

    def test_unknown_shapes_are_counted_not_raised(self):
        db = self._db()
        con = sqlite3.connect(db)
        mid = "msg_00000000000000000000000099"
        con.execute(
            "INSERT INTO message VALUES (?,?,?,?,?)",
            (
                mid,
                SESSION_ID,
                BASE_MS + 130_000,
                BASE_MS + 130_000,
                json.dumps(
                    {
                        "role": "assistant",
                        "parentID": "msg_00000000000000000000000011",
                        "modelID": "m",
                        "providerID": "p",
                        "time": {"created": BASE_MS + 130_000},
                        "tokens": {
                            "input": 1,
                            "output": 1,
                            "reasoning": 0,
                            "cache": {"read": 0, "write": 0},
                        },
                        "cost": 0,
                        "error": {"name": "FutureError", "data": {"message": "??"}},
                    }
                ),
            ),
        )
        con.execute(
            "INSERT INTO part VALUES (?,?,?,?,?,?)",
            (
                "prt_00000000000000000000000099",
                mid,
                SESSION_ID,
                0,
                0,
                json.dumps({"type": "hologram", "beam": True}),
            ),
        )
        con.execute(
            "INSERT INTO part VALUES (?,?,?,?,?,?)",
            (
                "prt_00000000000000000000000098",
                mid,
                SESSION_ID,
                0,
                0,
                json.dumps(
                    {
                        "type": "tool",
                        "tool": "teleport",
                        "callID": "c",
                        "state": {"status": "warping", "input": {}},
                    }
                ),
            ),
        )
        con.execute(
            "INSERT INTO part VALUES (?,?,?,?,?,?)",
            ("prt_00000000000000000000000097", mid, SESSION_ID, 0, 0, "{not json"),
        )
        con.execute(
            "INSERT INTO message VALUES (?,?,?,?,?)",
            (
                "msg_00000000000000000000000098",
                SESSION_ID,
                BASE_MS + 131_000,
                BASE_MS + 131_000,
                json.dumps(
                    {"role": "user", "agent": "build", "model": {"providerID": "p", "modelID": "m"}}
                ),
            ),
        )
        con.commit()
        con.close()
        events = opencode.load_events(db / SESSION_ID)  # must not raise
        st = digest.stats(events)
        self.assertEqual(st["errors"], 4)  # FutureError is still an error worth showing
        self.assertEqual(events[-1].tool, "api_request")
        self.assertIn("teleport", st["tool_mix"])
        d = opencode.diagnostics(db / SESSION_ID)
        self.assertEqual(d["unknown_types"], {"hologram": 1})
        self.assertEqual(d["unknown_tools"], {"teleport": 1})
        self.assertEqual(d["unknown_error_names"], {"FutureError": 1})
        self.assertEqual(d["malformed_lines"], 1)
        self.assertEqual(d["no_timestamp"], 1)
        self.assertEqual(d["derivation"]["tool_status_unknown_warping"], 1)
        self.assertEqual(d["derivation"]["assistant_error_FutureError"], 1)
        self.assertEqual(d["derivation"]["message_no_timestamp"], 1)  # placed at the row clock

    def test_json_dir_legacy_v0_message_is_counted_not_parsed(self):
        root = self.tmp / "storage"
        shutil.copytree(FIX / "storage", root)
        (root / "message" / SESSION_ID / "msg_v0.json").write_text(
            json.dumps(
                {
                    "id": "msg_v0",
                    "role": "assistant",
                    "parts": [],
                    "metadata": {
                        "time": {"created": BASE_MS},
                        "sessionID": SESSION_ID,
                        "tool": {},
                        "assistant": {
                            "modelID": "m",
                            "providerID": "p",
                            "cost": 0,
                            "tokens": {
                                "input": 5,
                                "output": 5,
                                "reasoning": 0,
                                "cache": {"read": 0, "write": 0},
                            },
                        },
                    },
                }
            )
        )
        path = root / "session" / PROJECT_ID / f"{SESSION_ID}.json"
        d = opencode.diagnostics(path)
        self.assertEqual(d["legacy_v0_message"], 1)
        self.assertEqual(opencode.usage(path)["sum_step_finish_tokens"]["input"], 83500)

    def test_completed_edit_without_filediff_is_credited_from_its_strings(self):
        db = self._db()
        con = sqlite3.connect(db)
        con.execute(
            "UPDATE part SET data = json_remove(data, '$.state.metadata.filediff') "
            "WHERE id = 'prt_00000000000000000000000007'"
        )
        con.commit()
        con.close()
        events = opencode.load_events(db / SESSION_ID)
        e = next(x for x in events if x.tool == "edit")
        self.assertEqual((e.added, e.removed), (3, 1))  # difflib agrees with diffLines here
        der = opencode.diagnostics(db / SESSION_ID)["derivation"]
        self.assertEqual(der["edit_credit_from_strings"], 1)
        self.assertNotIn("edit_credit_from_filediff", der)

    def test_sqlite_is_opened_read_only(self):
        db = self._db()
        before = db.read_bytes()
        opencode.load_events(db / SESSION_ID)
        opencode.list_sessions(db)
        self.assertEqual(db.read_bytes(), before)
        self.assertFalse((self.tmp / "opencode.db-journal").exists())


class Detection(unittest.TestCase):
    def test_opencode_paths_detect_as_opencode(self):
        self.assertEqual(digest.detect_harness(SQLITE_SESSION), "opencode")
        self.assertEqual(digest.detect_harness(DB), "opencode")
        self.assertEqual(digest.detect_harness(JSON_SESSION), "opencode")
        self.assertEqual(digest.detect_harness(EXPORT), "opencode")

    def test_a_plain_sqlite_file_is_not_opencode(self):
        tmp = pathlib.Path(tempfile.mkdtemp())
        try:
            other = tmp / "state.vscdb"
            con = sqlite3.connect(other)
            con.execute("CREATE TABLE ItemTable (key TEXT, value BLOB)")
            con.commit()
            con.close()
            self.assertFalse(opencode.is_database(other))
            self.assertNotEqual(digest.detect_harness(other), "opencode")
            self.assertNotEqual(digest.detect_harness(other / "ses_x"), "opencode")
        finally:
            shutil.rmtree(tmp)

    def test_other_fixtures_unchanged(self):
        self.assertEqual(digest.detect_harness(CODEX), "codex")
        self.assertEqual(digest.detect_harness(GEMINI / "synthetic_session.jsonl"), "gemini")
        self.assertEqual(digest.detect_harness(GEMINI / "synthetic_session_legacy.json"), "gemini")
        self.assertEqual(digest.detect_harness(CLINE_TASK), "cline")
        files = sorted(CC_BOUNDARIES.glob("*.jsonl"))
        self.assertTrue(files)
        for f in files:
            self.assertEqual(digest.detect_harness(f), "claude_code", f)

    def test_claude_code_stats_unchanged(self):
        # The tuple pinned before the Gemini loader existed; the opencode loader must not
        # move it. `python3 -m analysis stats` prints exactly this dict.
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
        self.assertEqual(
            json.dumps(digest.build(CC_REMOTE)["stats"], indent=1) + "\n", EXPECTED_CC_STATS
        )

    def test_probe_walks_a_data_dir(self):
        from analysis import probe

        found = probe._walk(FIX)
        self.assertEqual(
            found,
            [
                EXPORT,
                DB / CHILD_ID,
                SQLITE_SESSION,
                FIX / "storage" / "session" / PROJECT_ID / f"{CHILD_ID}.json",
                JSON_SESSION,
            ],
        )
        self.assertEqual(probe._walk(SQLITE_SESSION), [SQLITE_SESSION])
        r = probe.probe_file(SQLITE_SESSION)
        self.assertEqual(r["harness"], "opencode")
        text = probe.format_probe(r)
        self.assertIn("message sum == step sum: NO", text)
        self.assertIn("children=1", text)
        self.assertIn("parent_id=", probe.format_probe(probe.probe_file(DB / CHILD_ID)))


# `python3 -m analysis stats spec/fixtures/boundaries/remote_sdk_prompts.jsonl`, captured
# before analysis/opencode.py existed (byte-identical after, md5 1d509086…). The test
# above holds the dispatch to exactly this text.
EXPECTED_CC_STATS = """\
{
 "events": 58,
 "wall_seconds": 433,
 "prompts_sent": 3,
 "replies_received": 0,
 "interrupts": 1,
 "human_edits": 0,
 "compactions": 0,
 "prompt_words_avg": 2.3,
 "prompt_words_median": 2,
 "prompt_words_max": 4,
 "tool_calls": 54,
 "tool_mix": {
  "Bash": 54
 },
 "errors": 0,
 "lines_added_agent": 0,
 "lines_removed_agent": 0,
 "files_edited": 0,
 "files_written_via_shell": 0,
 "files_read": 0,
 "git_commits_run": 0,
 "test_runs": 0,
 "models": {},
 "longest_silence_seconds": 8
}
"""


if __name__ == "__main__":
    unittest.main()
