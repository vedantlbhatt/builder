"""python3 -m unittest analysis.tests.test_aider

Two layers. The generated expectation catches drift; the hand-counted invariants are
independent of the loader and are what make agreement evidence rather than tautology.
The fixture is read by eye — three sessions in ONE `.aider.chat.history.md`:

* `20241112-091000`: 2 prompts (the second is two `#### ` lines, one input-history entry),
  2 replies, 6 tool events (2 `Applied edit to`, 3 `Commit`, 1 `Running`), 0 errors,
  +7/-1 lines (SEARCH/REPLACE 1→3 lines, then +4 inserted), 3 commits (two auto, one
  `/commit`), 1 test run, wall 210 s (first prompt 09:10:30 → `/commit` at 09:14:00;
  `/exit` advances nothing visible), a `#### Notes` heading and a `> remember` blockquote
  in a reply that are NOT a prompt or a tool line, one unclassified `> ` line (the confirm
  subject `pytest -q tests/`).
* `20241112-142000`: 2 prompts, 4 replies (one reflected retry and one test-failure
  follow-up carry no `####`), 7 tool events, 2 errors (the edit-format error with the
  failed block's path pulled out of the multi-line body, and `/test` adding output),
  +2/-2, 3 commits of which the first has no `Applied edit to` (partial apply), 2 test
  runs, 1 interrupt (the `^C` followed by a partial reply) and one `^C ^C` quit that is
  not, wall 285 s.
* `20241112-231500`: `/help` and its listing — zero events.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import shutil
import tempfile
import unittest

from analysis import aider, digest

ROOT = pathlib.Path(__file__).resolve().parents[2]
FIX = ROOT / "spec" / "fixtures" / "aider"
CHAT = FIX / aider.CHAT_FILE
INPUT = FIX / aider.INPUT_FILE
EXPECTED = FIX / "expected.json"
S1, S2, S3 = "20241112-091000", "20241112-142000", "20241112-231500"
CLINE_TASK = ROOT / "spec" / "fixtures" / "cline" / "tasks" / "1730711400000"
GEMINI = ROOT / "spec" / "fixtures" / "gemini"
CODEX = ROOT / "spec" / "fixtures" / "codex" / "synthetic_session.jsonl"
CC_BOUNDARIES = ROOT / "spec" / "fixtures" / "boundaries"
CC_REMOTE = CC_BOUNDARIES / "remote_sdk_prompts.jsonl"
CC_REMOTE_STATS_MD5 = "1d509086bab77f372fcb718c23ebdbe1"  # `python3 -m analysis stats … | md5sum`


def _t(stamp: str, micros: str | None = None) -> float:
    return aider._local_ts(stamp, micros)


# ---- a tiny writer with Aider's exact line shapes (see scripts/gen_aider_fixture.py)
def H(stamp: str) -> str:
    return f"\n# aider chat started at {stamp}\n\n"


def U(text: str) -> str:
    return "\n#### " + "  \n#### ".join(text.splitlines() or ["<blank>"]) + "  \n"


def T(*messages: str) -> str:  # tool_error / tool_warning: `_tool_message` splits on newlines
    out = []
    for msg in messages:
        for ln in msg.splitlines() if "\n" in msg else [msg.strip()]:
            out.append(("> " + ln.strip()).rstrip() + "  \n")
    return "".join(out)


def TB(text: str) -> str:  # tool_output with newlines: ONE prefix, ONE suffix
    return "> " + text.strip() + "  \n"


def A(text: str) -> str:
    return "\n" + text.strip() + "\n\n"


def IN(stamp: str, text: str) -> str:
    return f"\n# {stamp}\n" + "".join(f"+{ln}\n" for ln in text.split("\n"))


def SR(path: str, old: str, new: str) -> str:
    return f"{path}\n```python\n<<<<<<< SEARCH\n{old}=======\n{new}>>>>>>> REPLACE\n```"


class FixtureStats(unittest.TestCase):
    def test_reproduces_expected(self):
        exp = json.loads(EXPECTED.read_text())
        self.assertEqual(exp["harness"], "aider")
        self.assertEqual(list(exp["sessions"]), [S1, S2, S3])
        for sid, e in exp["sessions"].items():
            p = CHAT / sid
            events = aider.load_events(p)
            self.assertEqual(len(events), e["events"], sid)
            self.assertEqual(digest.stats(events), e["stats"], sid)
            self.assertEqual(aider.usage(p), e["usage"], sid)
            self.assertEqual({k: v for k, v in aider.meta(p).items() if k != "path"}, e["meta"])
            self.assertEqual(aider.diagnostics(p), e["diagnostics"], sid)

    def test_session_one_hand_counted(self):
        events = aider.load_events(CHAT / S1)
        st = digest.stats(events)
        self.assertEqual(st["prompts_sent"], 2)
        self.assertEqual(st["replies_received"], 2)
        self.assertEqual(st["tool_calls"], 6)
        self.assertEqual(st["tool_mix"], {"commit": 3, "apply_edit": 2, "run": 1})
        self.assertEqual(st["errors"], 0)
        self.assertEqual((st["lines_added_agent"], st["lines_removed_agent"]), (7, 1))
        self.assertEqual(st["files_edited"], 2)
        self.assertEqual(st["git_commits_run"], 3)
        self.assertEqual(st["test_runs"], 1)
        self.assertEqual(st["wall_seconds"], 210)
        self.assertEqual(st["models"], {"claude-3-5-sonnet-20241022": 2})
        prompts = [e for e in events if e.kind == "prompt"]
        self.assertEqual(prompts[0].text, "Add a --dry-run flag to scripts/deploy.sh")
        self.assertEqual(prompts[0].ts, _t("2024-11-12 09:10:30", "123456"))
        self.assertEqual(prompts[1].text, "also cover the flag in the tests\nkeep it short")
        self.assertEqual(prompts[1].ts, _t("2024-11-12 09:12:20", "500000"))
        edits = [e for e in events if e.tool == "apply_edit"]
        self.assertEqual(
            [(e.path, e.added, e.removed) for e in edits],
            [("scripts/deploy.sh", 3, 1), ("tests/test_deploy.py", 4, 0)],
        )
        run = next(e for e in events if e.tool == "run")
        self.assertEqual(run.text, "pytest -q tests/")
        self.assertEqual(run.ts, _t("2024-11-12 09:11:05", "400000"))  # Aider's own `/run` entry
        self.assertEqual(
            [e.tool_id for e in events if e.tool == "commit"], ["a1b2c3d", "c3d4e5f", "d4e5f6a"]
        )
        replies = [e for e in events if e.kind == "assistant"]
        self.assertEqual([e.tok_out for e in replies], [340, 210])
        self.assertTrue(replies[1].text.startswith("#### Notes"))  # a heading, not a prompt
        self.assertEqual(replies[1].ts, prompts[1].ts)  # unstamped: inherits the prompt's clock
        d = aider.diagnostics(CHAT / S1)
        self.assertEqual(d["looks_like_prompt_no_suffix"], 1)
        self.assertEqual(d["looks_like_tool_no_suffix"], 1)
        self.assertEqual(d["unknown_types"], {"pytest -q": 1})
        self.assertEqual(d["derivation"]["prompt_stamped"], 2)
        self.assertEqual(d["derivation"]["running_stamped"], 1)
        self.assertEqual(d["derivation"]["confirm_answer"], 2)
        self.assertEqual(d["input_history"]["entries_in_window"], 8)

    def test_session_two_hand_counted(self):
        events = aider.load_events(CHAT / S2)
        st = digest.stats(events)
        self.assertEqual(st["prompts_sent"], 2)
        self.assertEqual(st["replies_received"], 4)
        self.assertEqual(st["tool_calls"], 7)
        self.assertEqual(st["tool_mix"], {"commit": 3, "apply_edit": 2, "run": 2})
        self.assertEqual(st["errors"], 2)
        self.assertEqual((st["lines_added_agent"], st["lines_removed_agent"]), (2, 2))
        self.assertEqual(st["git_commits_run"], 3)
        self.assertEqual(st["test_runs"], 2)
        self.assertEqual(st["interrupts"], 1)
        self.assertEqual(st["wall_seconds"], 285)
        self.assertEqual(st["models"], {"gpt-4o": 4})
        errs = [e for e in events if e.kind == "result_error"]
        self.assertEqual([e.tool for e in errs], ["apply_edit", "run"])
        self.assertEqual(errs[0].text, "The LLM did not conform to the edit format.")
        self.assertEqual(errs[0].path, "tests/helpers.py")  # from the multi-line body
        self.assertIn("exited non-zero", errs[1].text)
        self.assertEqual([e.tok_out for e in events if e.kind == "assistant"], [480, 620, 150, 90])
        d = aider.diagnostics(CHAT / S2)
        self.assertEqual(d["tool_blocks_multiline"], 1)
        self.assertEqual(d["derivation"]["commit_without_applied_edit"], 1)
        self.assertEqual(d["derivation"]["ctrl_c_without_reply"], 1)
        self.assertEqual(d["derivation"]["ctrl_c_exit"], 1)
        self.assertEqual(d["derivation"]["interrupt"], 1)
        self.assertEqual(d["derivation"]["test_output_added_nonzero_exit"], 1)
        self.assertEqual(d["unknown_types"], {})

    def test_session_three_has_no_events(self):
        self.assertEqual(digest.stats(aider.load_events(CHAT / S3)), {"events": 0})
        d = aider.diagnostics(CHAT / S3)
        self.assertEqual(
            d["derivation"],
            {
                "announcement": 4,
                "announcement_model": 1,
                "command_help": 1,
                "command_stamped": 1,
                "file_added": 1,
                "help_listing_line": 7,
            },
        )
        self.assertEqual(aider.meta(CHAT / S3)["files_in_chat"], 1)

    def test_usage_is_as_printed_and_labelled(self):
        u = aider.usage(CHAT / S1)
        self.assertTrue(u["approximate"])
        self.assertEqual(
            u["tokens_as_printed"],
            {"sent": 17200, "cache_write": 1100, "cache_hit": 1100, "received": 550},
        )
        self.assertEqual((u["sum_message_cost"], u["last_session_cost"]), (0.05, 0.05))
        self.assertTrue(u["session_cost_matches_sum"])

    def test_bare_paths_resolve_to_the_latest_session(self):
        latest = digest.stats(aider.load_events(CHAT / S3))
        for p in (FIX, CHAT, INPUT):
            self.assertEqual(digest.stats(aider.load_events(p)), latest, p)
            self.assertEqual(aider.meta(p)["session_selected_by"], "latest")
            self.assertEqual(aider.meta(p)["session_id"], S3)
        self.assertEqual(aider.meta(CHAT / S1)["session_selected_by"], "path")

    def test_list_sessions(self):
        ss = aider.list_sessions(FIX)
        self.assertEqual(
            [(s.id, s.ordinal, s.started_at) for s in ss],
            [
                (S1, 1, "2024-11-12 09:10:00"),
                (S2, 2, "2024-11-12 14:20:00"),
                (S3, 3, "2024-11-12 23:15:00"),
            ],
        )
        self.assertEqual(aider.list_sessions(CHAT), ss)

    def test_window_filters_events(self):
        events = aider.load_events(CHAT / S1, start=_t("2024-11-12 09:12:00"))
        st = digest.stats(events)
        self.assertEqual(st["prompts_sent"], 1)
        self.assertEqual(st["tool_mix"], {"commit": 2, "apply_edit": 1})

    def test_digest_build_dispatches(self):
        d = digest.build(CHAT / S2)
        self.assertIn("harness: aider", d["text"])
        self.assertIn("ERROR from apply_edit", d["text"])
        self.assertIn("git commits run: 3", d["text"])
        self.assertEqual(d["stats"]["prompts_sent"], 2)


class Robustness(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def _repo(self, chat: str, inputs: str | None = None) -> pathlib.Path:
        (self.tmp / aider.CHAT_FILE).write_text(chat, encoding="utf-8")
        if inputs is not None:
            (self.tmp / aider.INPUT_FILE).write_text(inputs, encoding="utf-8")
        return self.tmp

    def test_no_input_history_places_everything_at_the_header(self):
        repo = self._repo(
            H("2024-11-12 09:10:00")
            + U("fix it")
            + A("done")
            + T("Tokens: 1.0k sent, 10 received.")
        )
        events = aider.load_events(repo)
        self.assertEqual([e.kind for e in events], ["prompt", "assistant"])
        self.assertTrue(all(e.ts == _t("2024-11-12 09:10:00") for e in events))
        d = aider.diagnostics(repo)
        self.assertFalse(d["input_history"]["present"])
        self.assertEqual(d["derivation"]["prompt_without_input_stamp"], 1)
        self.assertEqual(d["no_timestamp"], 2)
        self.assertEqual(digest.stats(events)["wall_seconds"], 0)

    def test_file_without_header_has_no_session(self):
        repo = self._repo("just some notes\n" + U("hello"))
        self.assertEqual(aider.load_events(repo), [])
        d = aider.diagnostics(repo)
        self.assertEqual(d["sessions_in_file"], 0)
        self.assertEqual(d["derivation"], {"no_session": 1})
        self.assertEqual(aider.list_sessions(repo), [])

    def test_partial_trailing_lines_are_never_consumed(self):
        chat = H("2024-11-12 09:10:00") + U("one") + A("ok") + "#### two  "  # no newline yet
        inputs = IN("2024-11-12 09:10:05", "one") + "\n# 2024-11-12 09:10:09\n+tw"
        repo = self._repo(chat, inputs)
        events = aider.load_events(repo)
        self.assertEqual([e.text for e in events if e.kind == "prompt"], ["one"])
        d = aider.diagnostics(repo)
        self.assertTrue(d["partial_trailing_line"])
        self.assertTrue(d["input_history"]["partial_trailing_line"])
        self.assertEqual(d["input_history"]["entries"], 1)

    def test_consecutive_duplicate_prompt_has_one_stamp(self):
        # prompt_toolkit skips an entry identical to the previous one (buffer.py:1365)
        chat = H("2024-11-12 09:10:00") + U("again") + A("a") + U("again") + A("b")
        repo = self._repo(chat, IN("2024-11-12 09:10:07", "again"))
        prompts = [e for e in aider.load_events(repo) if e.kind == "prompt"]
        self.assertEqual([p.ts for p in prompts], [_t("2024-11-12 09:10:07")] * 2)
        der = aider.diagnostics(repo)["derivation"]
        self.assertEqual((der["prompt_stamped"], der["prompt_without_input_stamp"]), (1, 1))

    def test_multiline_prompt_falls_back_to_its_first_line(self):
        # `{ … }` multiline mode records each physical line as its own entry
        chat = H("2024-11-12 09:10:00") + U("first line\nsecond line") + A("ok")
        inputs = (
            IN("2024-11-12 09:10:01", "{")
            + IN("2024-11-12 09:10:03", "first line")
            + IN("2024-11-12 09:10:05", "second line")
            + IN("2024-11-12 09:10:06", "}")
        )
        repo = self._repo(chat, inputs)
        p = next(e for e in aider.load_events(repo) if e.kind == "prompt")
        self.assertEqual((p.text, p.ts), ("first line\nsecond line", _t("2024-11-12 09:10:03")))
        self.assertEqual(aider.diagnostics(repo)["derivation"]["prompt_stamped_by_first_line"], 1)

    def test_heading_and_blockquote_without_suffix_are_assistant_text(self):
        reply = "#### Plan\n\n> a quoted line\n\nmore prose\n#### Step 2"
        repo = self._repo(
            H("2024-11-12 09:10:00") + U("go") + A(reply) + T("Tokens: 900 sent, 50 received.")
        )
        events = aider.load_events(repo)
        self.assertEqual([e.kind for e in events], ["prompt", "assistant"])
        self.assertTrue(events[1].text.startswith("#### Plan"))
        d = aider.diagnostics(repo)
        self.assertEqual((d["looks_like_prompt_no_suffix"], d["looks_like_tool_no_suffix"]), (2, 1))
        self.assertEqual(d["tool_blocks_multiline"], 0)

    def test_multiline_tool_output_is_one_tool_block_not_a_reply(self):
        git_log = "commit 1234567\nAuthor: dev\n\n    tidy\n\ncommit 89abcde\n\n    start"
        chat = (
            H("2024-11-12 09:10:00")
            + U("/git log")
            + TB(git_log)
            + U("/model gpt-4o")
            + TB("Aider v0.86.1\nModel: gpt-4o with diff edit format\nGit repo: .git with 3 files")
            + U("hi")
            + A("hello")
        )
        repo = self._repo(chat)
        events = aider.load_events(repo)
        self.assertEqual([e.kind for e in events], ["tool", "prompt", "assistant"])
        self.assertEqual((events[0].tool, events[0].text), ("run", "git log"))
        self.assertEqual(events[2].model, "gpt-4o")  # the re-announcement was read
        d = aider.diagnostics(repo)
        self.assertEqual(d["tool_blocks_multiline"], 2)
        self.assertEqual(d["derivation"]["tool_block_continuation_line"], 9)
        self.assertEqual(d["derivation"]["tool_block_continuation_unclassified"], 4)
        self.assertEqual(d["derivation"]["announcement_model"], 1)
        self.assertEqual(aider.meta(repo)["model"], "gpt-4o")
        self.assertEqual(digest.stats(events)["replies_received"], 1)

    def test_ctrl_c_is_an_interrupt_only_when_a_partial_reply_follows(self):
        chat = (
            H("2024-11-12 09:10:00")
            + U("a")
            + T("\n\n^C again to exit")
            + A("partial…")
            + T("Tokens: 1.0k sent, 5 received.")
            + U("b")
            + A("full")
            + T("\n\n^C again to exit")
            + T("\n\n^C KeyboardInterrupt")
        )
        repo = self._repo(chat)
        st = digest.stats(aider.load_events(repo))
        self.assertEqual(st["interrupts"], 1)
        der = aider.diagnostics(repo)["derivation"]
        self.assertEqual(
            (der["interrupt"], der["ctrl_c_without_reply"], der["ctrl_c_exit"]), (1, 1, 1)
        )
        self.assertEqual(der["tool_line_empty"], 6)

    def test_tokens_line_shapes(self):
        chat = (
            H("2024-11-12 09:10:00")
            + U("a")
            + A("x")
            + T(
                "Tokens: 12k sent, 2.0k cache write, 8.5k cache hit, 1.2k received.",
                "Cost: $0.04 message, $0.31 session.",
            )
            + U("b")
            + A("y")
            + T("Tokens: 923 sent, 41 received.")
        )
        repo = self._repo(chat)
        u = aider.usage(repo)
        self.assertEqual(
            u["tokens_as_printed"],
            {"sent": 12923, "cache_write": 2000, "cache_hit": 8500, "received": 1241},
        )
        self.assertEqual((u["messages"], u["messages_with_cost"]), (2, 1))
        self.assertEqual((u["sum_message_cost"], u["last_session_cost"]), (0.04, 0.31))
        self.assertFalse(
            u["session_cost_matches_sum"]
        )  # a running total larger than the printed costs
        self.assertEqual(
            [e.tok_out for e in aider.load_events(repo) if e.kind == "assistant"], [1200, 41]
        )
        self.assertEqual(
            (aider._k("923"), aider._k("1.2k"), aider._k("12k"), aider._k("x")),
            (923, 1200, 12000, None),
        )

    def test_edit_credit_shapes(self):
        from collections import Counter

        c = Counter()
        fenced = (
            "```python\napp/main.py\n<<<<<<< SEARCH\na\nb\n=======\na\nc\nd\n>>>>>>> REPLACE\n```"
        )
        udiff = "```diff\n--- lib/x.py\n+++ lib/x.py\n@@ ... @@\n-old\n+new\n+newer\n context\n```"
        patch = "*** Begin Patch\n*** Update File: src/y.py\n@@ def f\n-    return 1\n+    return 2\n*** Add File: src/z.py\n+print(1)\n*** End Patch"
        whole = "docs/readme.md\n```\nline 1\nline 2\n```"
        blocks = aider._edit_blocks(f"{fenced}\n\n{udiff}\n\n{patch}\n\n{whole}", c)
        self.assertEqual(
            blocks,
            {"app/main.py": [2, 1], "lib/x.py": [2, 1], "src/y.py": [1, 1], "src/z.py": [1, 0]},
        )
        self.assertEqual(c["edit_block_search_replace"], 1)
        self.assertEqual(c["edit_block_udiff"], 1)
        self.assertEqual(c["edit_block_patch"], 2)
        self.assertEqual(aider._credit_for("main.py", blocks), (2, 1))  # basename match
        self.assertIsNone(aider._credit_for("docs/readme.md", blocks))  # whole: path only
        # end to end: a whole-format apply gets the path and no lines
        repo = self._repo(
            H("2024-11-12 09:10:00")
            + U("a")
            + A(whole)
            + T("Tokens: 1.0k sent, 5 received.", "Applied edit to docs/readme.md")
        )
        ev = next(e for e in aider.load_events(repo) if e.tool == "apply_edit")
        self.assertEqual((ev.path, ev.added, ev.removed), ("docs/readme.md", None, None))
        self.assertEqual(aider.diagnostics(repo)["derivation"]["edit_credit_path_only"], 1)

    def test_commands_and_shell_shapes(self):
        chat = (
            H("2024-11-12 09:10:00")
            + U("/add a.py")
            + T("Added a.py to the chat")
            + U("/ask")
            + U("/ask why is it slow")
            + A("because")
            + U("!ls -la")
            + U("/git status")
            + U("/run pytest -q")
            + T(
                "Add 0.1k tokens of command output to the chat? (Y)es/(N)o [Yes]: y",
                "Added 3 lines of output to the chat.",
            )
            + U("/test pytest -q")
            + T("Added 9 lines of output to the chat.")
            + U("<blank>")
        )
        repo = self._repo(chat)
        events = aider.load_events(repo)
        self.assertEqual(
            [e.kind for e in events],
            ["prompt", "assistant", "tool", "tool", "tool", "tool", "result_error"],
        )
        self.assertEqual(events[0].text, "/ask why is it slow")
        self.assertEqual(
            [e.text for e in events if e.kind == "tool" and e.tool == "run"],
            ["ls -la", "git status", "pytest -q", "pytest -q"],
        )
        st = digest.stats(events)
        self.assertEqual((st["prompts_sent"], st["test_runs"], st["errors"]), (1, 2, 1))
        der = aider.diagnostics(repo)["derivation"]
        self.assertEqual(
            (der["command_add"], der["command_ask"], der["command_git"], der["prompt_blank"]),
            (1, 1, 1, 1),
        )
        self.assertEqual(
            (der["command_output_added"], der["test_output_added_nonzero_exit"]), (1, 1)
        )

    def test_errors_are_the_exact_strings_aider_prints(self):
        chat = (
            H("2024-11-12 09:10:00")
            + U("a")
            + A("x")
            + T(
                "Unable to commit: nothing to commit",
                "The API provider has rate limited you. Try again later or check your quotas.",
                "Retrying in 0.5 seconds...",
                "Model gpt-4o has hit a token limit!",
                "Something odd happened here",
                "Warning: it's best to only add files that need changes to the chat.",
            )
        )
        repo = self._repo(chat)
        errs = [e for e in aider.load_events(repo) if e.kind == "result_error"]
        self.assertEqual([e.tool for e in errs], ["commit", "api_request", "api_request"])
        der = aider.diagnostics(repo)["derivation"]
        self.assertEqual(
            (der["api_retry"], der["tool_line_unclassified"], der["warning_line"]), (1, 1, 1)
        )
        self.assertEqual(aider.diagnostics(repo)["unknown_types"], {"Something odd": 1})

    def test_input_history_shapes(self):
        inputs = (
            IN("2024-11-12 09:10:05", "one")
            + "garbage line\n"
            + IN("2024-11-12 09:10:09.5", "two\nlines")
            + "\n# not a stamp\n+orphan\n"
        )
        repo = self._repo(
            H("2024-11-12 09:10:00") + U("one") + A("a") + U("two\nlines") + A("b"), inputs
        )
        prompts = [e for e in aider.load_events(repo) if e.kind == "prompt"]
        self.assertEqual(
            [p.ts for p in prompts], [_t("2024-11-12 09:10:05"), _t("2024-11-12 09:10:09", "5")]
        )
        d = aider.diagnostics(repo)["input_history"]
        self.assertEqual((d["entries"], d["malformed_lines"]), (2, 3))

    def test_sessions_in_the_same_second_get_distinct_ids(self):
        chat = H("2024-11-12 09:10:00") + U("a") + H("2024-11-12 09:10:00") + U("b")
        repo = self._repo(chat)
        self.assertEqual(
            [s.id for s in aider.list_sessions(repo)], ["20241112-091000", "20241112-091000-2"]
        )
        self.assertEqual(
            [e.text for e in aider.load_events(repo / aider.CHAT_FILE / "20241112-091000-2")], ["b"]
        )
        self.assertEqual(aider.load_events(repo / aider.CHAT_FILE / "20241112-999999"), [])
        self.assertEqual(
            aider.diagnostics(repo / aider.CHAT_FILE / "20241112-999999")["session_not_found"],
            "20241112-999999",
        )


class Detection(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_aider_paths_detect_as_aider(self):
        for p in (FIX, CHAT, INPUT, CHAT / S1, CHAT / "nonexistent-id"):
            self.assertEqual(digest.detect_harness(p), "aider", p)
        renamed = self.tmp / "notes.md"  # `--chat-history-file` under another name: by content
        shutil.copy(CHAT, renamed)
        self.assertEqual(digest.detect_harness(renamed), "aider")
        other = self.tmp / "other.md"
        other.write_text("# a heading\n\nprose\n")
        self.assertEqual(digest.detect_harness(other), "claude_code")  # the fallback
        lone = self.tmp / aider.INPUT_FILE  # an input history with no chat file beside it
        lone.write_text("\n# 2024-11-12 09:10:05\n+one\n")
        self.assertEqual(digest.detect_harness(lone), "claude_code")

    def test_other_fixtures_unchanged(self):
        self.assertEqual(digest.detect_harness(CODEX), "codex")
        self.assertEqual(digest.detect_harness(GEMINI / "synthetic_session.jsonl"), "gemini")
        self.assertEqual(digest.detect_harness(GEMINI / "synthetic_session_legacy.json"), "gemini")
        self.assertEqual(digest.detect_harness(CLINE_TASK), "cline")
        files = sorted(CC_BOUNDARIES.glob("*.jsonl"))
        self.assertTrue(files)
        for f in files:
            self.assertEqual(digest.detect_harness(f), "claude_code", f)

    def test_claude_code_stats_byte_identical(self):
        # The tuple pinned before the Gemini loader existed, and the md5 of the exact
        # `python3 -m analysis stats` output taken before this loader was added.
        d = digest.build(CC_REMOTE)
        st = d["stats"]
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
        out = json.dumps(st, indent=1) + "\n"
        self.assertEqual(hashlib.md5(out.encode()).hexdigest(), CC_REMOTE_STATS_MD5)

    def test_probe_walks_every_session_in_the_file(self):
        from analysis import probe

        want = [CHAT / S1, CHAT / S2, CHAT / S3]
        self.assertEqual(probe._walk(FIX), want)
        self.assertEqual(probe._walk(CHAT), want)
        self.assertEqual(probe._walk(INPUT), want)
        self.assertEqual(probe._walk(CHAT / S2), [CHAT / S2])
        r = probe.probe_file(CHAT / S2)
        self.assertEqual(r["harness"], "aider")
        self.assertEqual(r["bytes"], CHAT.stat().st_size + INPUT.stat().st_size)
        text = probe.format_probe(r)
        self.assertIn("approximate", text)
        self.assertIn("sessions_in_file=3", text)
        self.assertIn("UNKNOWN: none", text)
        self.assertIn("UNKNOWN: pytest -q 1", probe.format_probe(probe.probe_file(CHAT / S1)))
        self.assertIn("events: 0", probe.format_probe(probe.probe_file(CHAT / S3)))


if __name__ == "__main__":
    unittest.main()
