"""The build post: what gets described, what gets refused, and what never ships.

The post is PUBLIC. That changes what matters: a wrong number on a private profile is a
bad day, and an invented dependency in a public post is a claim the author never made and
cannot defend. So the checks here are about what the model is not allowed to say.
"""

from __future__ import annotations

import datetime as dt
import logging
import pathlib
import tempfile
import unittest

from analysis import patterns as pt
from analysis import shipped as sh
from analysis.digest import Ev

T0 = dt.datetime(2026, 9, 1, 9, 0, tzinfo=dt.UTC).timestamp()


def ev(n, ts, kind, text="", tool=None, added=None, path=None):
    return Ev(n, ts, kind, text, tool=tool, added=added, path=path)


def sess(events, sid="s"):
    return pt.SessionEvents(
        session_id=sid,
        started_at=T0,
        ended_at=T0 + 3600,
        active_seconds=3600.0,
        attended_seconds=3600.0,
        tz_offset_minutes=0,
        events=events,
    )


def failing(n, times, tool="Bash", text="make test"):
    out, t = [], T0
    for _ in range(times):
        out.append(ev(n, t, "tool", text, tool=tool))
        out.append(ev(n + 1, t + 5, "result_error", "boom"))
        n, t = n + 2, t + 60
    out.append(ev(n, t, "tool", text, tool=tool))
    return out


class Struggles(unittest.TestCase):
    def test_a_failure_loop_names_the_command_that_kept_failing(self):
        found = sh.struggles([sess(failing(0, 6))])
        loop = next(s for s in found if s.kind == "failure_loop")
        self.assertIn("6 failures in a row", loop.text)
        self.assertIn("make test", loop.text)

    def test_a_tool_that_is_not_a_shell_is_named_by_its_name(self):
        """`Ev.text` is the serialised INPUT for a non-shell tool, so using it blindly put
        `{"analysis_version":1,"model":...` in front of the model as the hard part."""
        found = sh.struggles([sess(failing(0, 5, tool="StructuredOutput", text='{"a":1}'))])
        loop = next(s for s in found if s.kind == "failure_loop")
        self.assertIn("StructuredOutput", loop.text)
        self.assertNotIn("{", loop.text)

    def test_three_failures_is_debugging_and_does_not_make_the_list(self):
        self.assertEqual([s for s in sh.struggles([sess(failing(0, 3))]) if s.kind == "failure_loop"], [])

    def test_a_rewritten_file_is_named_with_its_count(self):
        events = [
            ev(i, T0 + i * 60, "tool", "", tool="Edit", added=3, path="/repo/Map.js")
            for i in range(6)
        ]
        found = sh.struggles([sess(events)])
        rewrite = next(s for s in found if s.kind == "rewritten")
        self.assertIn("Map.js rewritten 6 times", rewrite.text)

    def test_a_revert_commit_is_a_struggle(self):
        found = sh.struggles([], ['Revert "the AIRMap interop patch" because it stranded overlays'])
        self.assertEqual(found[0].kind, "reverted")
        self.assertIn("stranded overlays", found[0].text)

    def test_an_ordinary_commit_is_not(self):
        self.assertEqual(sh.struggles([], ["Add a settings screen"]), [])

    def test_the_list_alternates_kinds_rather_than_ranking_purely_by_time(self):
        """Sorting on seconds alone handed the model five identical spin lines, because a
        long spin always outweighs a loop that resolved in ninety seconds. A list with one
        flavour in it gives the post one thing to say."""
        spins = [
            sess([ev(i, T0 + i * 120, "tool", "ls", tool="Bash") for i in range(40)], sid=f"sp{i}")
            for i in range(3)
        ]
        found = sh.struggles(spins + [sess(failing(0, 6))])
        kinds = [s.kind for s in found]
        self.assertEqual(kinds[0], "went_nowhere")
        self.assertIn("failure_loop", kinds[:2], f"the loop must not be buried: {kinds}")

    def test_nothing_stuck_is_an_empty_list_not_an_invented_difficulty(self):
        clean = sess(
            [
                ev(0, T0, "tool", "", tool="Edit", added=5, path="/repo/a.py"),
                ev(1, T0 + 10, "tool", "pytest -q", tool="Bash"),
            ]
        )
        self.assertEqual(sh.struggles([clean]), [])


class BuildInput(unittest.TestCase):
    def source(self, **kw):
        base = dict(project="RideGT", since=dt.date(2026, 9, 1), until=dt.date(2026, 9, 6))
        base.update(kw)
        return sh.build_input(**base)

    def test_what_got_done_comes_before_the_raw_material(self):
        """The narrative page learned this the hard way: a model handed file names at the
        top writes a post made of file names."""
        src = self.source(
            session_summaries=[{"headline": "Made the map stop dropping overlays"}],
            files=["Map.js", "mapSlotPool.js"],
        )
        self.assertLess(src.index("WHAT THE SESSIONS SAY GOT DONE"), src.index("FILES touched"))

    def test_the_struggles_are_handed_over_as_the_only_candidates(self):
        src = self.source(struggle_list=[sh.Struggle("failure_loop", "6 failures in a row", 300)])
        self.assertIn("Do not invent a difficulty", src)
        self.assertIn("6 failures in a row", src)

    def test_no_struggles_says_the_field_is_null_rather_than_asking_for_one(self):
        self.assertIn("`hard_part` is null", self.source())

    def test_nothing_analysed_says_to_say_less_rather_than_guess(self):
        self.assertIn("say less rather than guessing", self.source())

    def test_the_files_are_labelled_as_context_and_not_content(self):
        src = self.source(files=["Map.js"])
        self.assertIn("Never list these in the post", src)

    def test_no_hours_tokens_or_dollars_reach_the_model(self):
        """A build post that leads with effort is a post about the poster."""
        src = self.source(
            session_summaries=[{"headline": "x", "summary": "y", "highlights": ["z"]}],
            commits=["Add a thing"],
            struggle_list=[sh.Struggle("went_nowhere", "40 tool calls with nothing to show", 600)],
        )
        for word in ("token", "$", "active hour", "archetype", "attended"):
            self.assertNotIn(word, src.lower() if word.isalpha() else src)


class StackEvidence(unittest.TestCase):
    """The repository stating its own dependencies. Ground truth, not a guess.

    FOUND BY RUNNING IT: with only file names and commit subjects as evidence, a post
    about an Expo app with a FastAPI backend had `Expo`, `FastAPI` and `Postgres` deleted
    as unsupported and kept `Swift` and `Playwright`, which happened to appear in a
    filename. Every deletion was of a true claim.
    """

    def repo(self, files: dict) -> str:
        d = pathlib.Path(tempfile.mkdtemp())
        for name, body in files.items():
            path = d / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body)
        return str(d)

    def test_a_package_json_names_its_dependencies(self):
        root = self.repo(
            {"package.json": '{"dependencies": {"expo": "1", "react-native": "2"}, '
             '"devDependencies": {"typescript": "5"}}'}
        )
        self.assertEqual(
            sorted(sh.stack_evidence(root)), ["expo", "react-native", "typescript"]
        )

    def test_a_monorepo_is_read_one_directory_down(self):
        root = self.repo(
            {
                "mobile/package.json": '{"dependencies": {"expo": "1"}}',
                "server/requirements.txt": "fastapi==0.1\nuvicorn>=0.2  # comment\n",
            }
        )
        found = sh.stack_evidence(root)
        self.assertIn("expo", found)
        self.assertIn("fastapi", found)
        self.assertIn("uvicorn", found)

    def test_a_version_is_not_a_dependency_name(self):
        root = self.repo({"requirements.txt": "psycopg[binary]==3.1.0\n"})
        self.assertEqual(sh.stack_evidence(root), ["psycopg"])

    def test_a_comment_line_is_skipped(self):
        root = self.repo({"requirements.txt": "# pinned by CI\nredis==5\n"})
        self.assertEqual(sh.stack_evidence(root), ["redis"])

    def test_the_same_dependency_in_two_manifests_is_listed_once(self):
        root = self.repo(
            {
                "mobile/package.json": '{"dependencies": {"react": "1"}}',
                "web/package.json": '{"dependencies": {"React": "1"}}',
            }
        )
        self.assertEqual(sh.stack_evidence(root), ["react"])

    def test_a_repository_with_no_manifest_says_nothing_rather_than_guessing(self):
        self.assertEqual(sh.stack_evidence(self.repo({"README.md": "hi"})), [])

    def test_no_repository_is_no_evidence(self):
        self.assertEqual(sh.stack_evidence(None), [])

    def test_a_broken_manifest_does_not_take_the_draft_down_with_it(self):
        root = self.repo({"package.json": "{not json", "requirements.txt": "redis==5\n"})
        self.assertEqual(sh.stack_evidence(root), ["redis"])


class Verify(unittest.TestCase):
    SOURCE = "PROJECT: RideGT\n  Shipped an Expo app with react-native-maps and a FastAPI backend"

    def test_a_stack_entry_the_work_shows_survives(self):
        post, dropped = sh.verify({"stack": ["Expo", "FastAPI"]}, self.SOURCE)
        self.assertEqual(post["stack"], ["Expo", "FastAPI"])
        self.assertEqual(dropped, [])

    def test_a_multi_word_entry_needs_every_word(self):
        post, _ = sh.verify({"stack": ["react-native-maps"]}, self.SOURCE)
        self.assertEqual(post["stack"], ["react-native-maps"])

    def test_a_dependency_from_the_manifest_survives(self):
        src = sh.build_input(
            project="RideGT", since=None, until=None, dependencies=["expo", "fastapi"]
        )
        post, dropped = sh.verify({"stack": ["Expo", "FastAPI"]}, src)
        self.assertEqual(post["stack"], ["Expo", "FastAPI"])
        self.assertEqual(dropped, [])

    def test_a_plausible_guess_is_deleted(self):
        """"It's a React Native app, so: Redux." Nobody said Redux."""
        post, dropped = sh.verify({"stack": ["Expo", "Redux"]}, self.SOURCE)
        self.assertEqual(post["stack"], ["Expo"])
        self.assertEqual(len(dropped), 1)
        self.assertIn("Redux", dropped[0])

    def test_the_check_is_case_insensitive(self):
        post, _ = sh.verify({"stack": ["expo", "FASTAPI"]}, self.SOURCE)
        self.assertEqual(len(post["stack"]), 2)

    def test_an_empty_stack_is_fine(self):
        post, dropped = sh.verify({"stack": []}, self.SOURCE)
        self.assertEqual(post["stack"], [])
        self.assertEqual(dropped, [])


class Write(unittest.TestCase):
    """`write` without the CLI: the model is stubbed, everything after it is real."""

    def run_write(self, doc, **kw):
        def fake(system, user, schema, model):
            return dict(doc), {"model": "sonnet"}

        real = sh.rn.call_claude
        sh.rn.call_claude = fake
        try:
            return sh.write(project="RideGT", commits=["Add an Expo screen"], **kw)
        finally:
            sh.rn.call_claude = real

    def test_a_dash_is_rewritten_and_counted(self):
        out = self.run_write({"what": "A transit app — for campus", "stack": []})
        self.assertNotIn("—", out["what"])
        self.assertEqual(out["dashes_rewritten"], 1)

    def test_an_invented_dependency_is_dropped_counted_and_logged(self):
        with self.assertLogs("analysis.shipped", level=logging.WARNING) as log:
            out = self.run_write({"what": "A transit app", "stack": ["Expo", "Redux"]})
        self.assertEqual(out["stack"], ["Expo"])
        self.assertEqual(out["unsupported_claims_dropped"], 1)
        self.assertIn("Redux", log.output[0])

    def test_the_version_is_stamped(self):
        out = self.run_write({"what": "x", "stack": []})
        self.assertEqual(out["shipped_version"], sh.SHIPPED_VERSION)
        self.assertEqual(out["model"], "sonnet")


class Prompt(unittest.TestCase):
    def test_the_prompt_bans_what_would_make_a_post_worthless(self):
        text = sh.PROMPT_PATH.read_text()
        self.assertIn("NEVER CLAIM SOMETHING THE WORK DOES NOT SHOW", text)
        self.assertIn("THE HARD PART IS THE POST", text)
        self.assertIn("NEVER LIST FILES", text)

    def test_the_prompt_keeps_effort_out_of_a_post_about_a_thing(self):
        text = sh.PROMPT_PATH.read_text().lower()
        self.assertIn("no hours, tokens, dollars or scores", text)

    def test_the_schema_has_no_field_about_the_person(self):
        props = set(sh.load_schema()["properties"])
        for field in ("archetype", "dimensions", "build_style", "prompting", "growth_edge"):
            self.assertNotIn(field, props)
        self.assertIn("hard_part", props)


if __name__ == "__main__":
    unittest.main()
