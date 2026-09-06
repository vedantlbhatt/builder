"""Recurring failures: what counts as "again", and what never becomes a rule.

The bar is CROSS-SESSION recurrence. Ten failures inside one sitting is debugging, which
is the job; the same failure in three sittings is something nobody wrote down. Most of
these tests are about the normalisation that decides whether two errors are the same one,
because getting that wrong fails SILENTLY in the safe direction: leave a path or a line
number in the fingerprint and every occurrence is unique, the count is zero, and the
feature cheerfully reports that you never repeat yourself.
"""

from __future__ import annotations

import datetime as dt
import logging
import unittest

from analysis import patterns as pt
from analysis import rules as ru
from analysis.digest import Ev

T0 = dt.datetime(2026, 9, 1, 9, 0, tzinfo=dt.UTC).timestamp()


def failing(cmd: str, err: str, n: int = 0, ts: float = T0, tool: str = "Bash"):
    return [
        Ev(n, ts, "tool", cmd, tool=tool),
        Ev(n + 1, ts + 5, "result_error", err),
    ]


def sess(events, sid="s", start=T0):
    return pt.SessionEvents(
        session_id=sid,
        started_at=start,
        ended_at=start + 3600,
        active_seconds=3600.0,
        attended_seconds=3600.0,
        tz_offset_minutes=0,
        events=events,
    )


ERR = "connection failed: connection to server at 127.0.0.1, port 5432 failed: refused"


class Signature(unittest.TestCase):
    def test_the_same_failure_from_two_machines_is_one_signature(self):
        a = ru.signature("ModuleNotFoundError in /home/alice/proj/app/main.py line 42")
        b = ru.signature("ModuleNotFoundError in /home/bob/work/app/main.py line 907")
        self.assertIsNotNone(a)
        self.assertEqual(a, b)

    def test_a_timing_does_not_make_every_run_unique(self):
        a = ru.signature("the request timed out after 30s waiting for the database")
        b = ru.signature("the request timed out after 45s waiting for the database")
        self.assertEqual(a, b)

    def test_a_uuid_does_not_make_every_run_unique(self):
        a = ru.signature("session 3f7e6f9d-b175-4a93-ba74-5eb349625942 could not be resolved here")
        b = ru.signature("session 91aa1234-0000-4aaa-bbbb-5eb349999999 could not be resolved here")
        self.assertEqual(a, b)

    def test_two_genuinely_different_errors_stay_different(self):
        self.assertNotEqual(
            ru.signature("ModuleNotFoundError: no module named requests anywhere on the path"),
            ru.signature("PermissionError: the socket at the usual place refused the connection"),
        )

    def test_an_error_too_short_to_group_on_is_refused(self):
        # "Exit code 1" is every failure at once. Grouping on it would produce one
        # enormous recurrence that says nothing.
        self.assertIsNone(ru.signature("Exit code 1"))
        self.assertIsNone(ru.signature(""))
        self.assertIsNone(ru.signature("   "))

    def test_case_and_whitespace_do_not_split_a_signature(self):
        self.assertEqual(
            ru.signature("Could NOT connect  to the\nserver process here"),
            ru.signature("could not connect to the server process here"),
        )


class Recurring(unittest.TestCase):
    def test_the_same_failure_in_two_sittings_is_a_rule_nobody_wrote_down(self):
        found = ru.recurring(
            [sess(failing("pytest", ERR), "a"), sess(failing("pytest", ERR), "b", T0 + 86400)]
        )
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].sessions, 2)
        self.assertEqual(found[0].occurrences, 2)

    def test_ten_failures_inside_one_sitting_is_debugging_and_not_a_rule(self):
        """The whole bar. A sitting where you fought something and won is the job."""
        events = []
        for i in range(10):
            events += failing("pytest", ERR, n=i * 2, ts=T0 + i * 60)
        self.assertEqual(ru.recurring([sess(events)]), [])

    def test_the_count_within_a_sitting_still_travels_as_the_cost(self):
        a = [e for i in range(5) for e in failing("pytest", ERR, n=i * 2, ts=T0 + i * 60)]
        b = failing("pytest", ERR, ts=T0 + 86400)
        found = ru.recurring([sess(a, "a"), sess(b, "b", T0 + 86400)])
        self.assertEqual(found[0].sessions, 2)
        self.assertEqual(found[0].occurrences, 6)

    def test_the_span_says_how_long_it_went_unwritten(self):
        found = ru.recurring(
            [sess(failing("pytest", ERR), "a"), sess(failing("pytest", ERR, ts=T0 + 7200), "b")]
        )
        self.assertAlmostEqual(found[0].span_seconds, 7200, delta=10)

    def test_the_worst_offender_comes_first(self):
        other = "ImportError: cannot import name Session from the models module here"
        sessions = [
            sess(failing("pytest", ERR, ts=T0 + i * 86400), f"s{i}") for i in range(3)
        ] + [sess(failing("python app.py", other, ts=T0 + i * 86400), f"o{i}") for i in range(2)]
        found = ru.recurring(sessions)
        self.assertEqual([r.sessions for r in found], [3, 2])

    def test_a_successful_call_is_not_a_failure(self):
        ok = [Ev(0, T0, "tool", "pytest", tool="Bash"), Ev(1, T0 + 5, "tool", "ls", tool="Bash")]
        self.assertEqual(ru.recurring([sess(ok, "a"), sess(ok, "b")]), [])

    def test_the_command_and_the_error_are_kept_verbatim_for_the_author(self):
        found = ru.recurring(
            [sess(failing("make test", ERR), "a"), sess(failing("make test", ERR), "b")]
        )
        self.assertEqual(found[0].command, "make test")
        self.assertIn("port 5432", found[0].error)

    def test_no_sessions_is_no_rules(self):
        self.assertEqual(ru.recurring([]), [])


class BuildInput(unittest.TestCase):
    def source(self, n=2):
        found = ru.recurring(
            [sess(failing("make test", ERR), f"s{i}", T0 + i * 86400) for i in range(n)]
        )
        return ru.build_input(project="builder", recurrences=found), found

    def test_the_candidates_are_numbered_so_a_rule_can_point_at_one(self):
        src, _ = self.source()
        self.assertIn("[1]", src)
        self.assertIn("make test", src)
        self.assertIn("port 5432", src)

    def test_the_sitting_count_is_in_front_of_the_model(self):
        src, _ = self.source(3)
        self.assertIn("3 separate sittings", src)


class Verify(unittest.TestCase):
    def setUp(self):
        self.found = ru.recurring(
            [sess(failing("make test", ERR), f"s{i}", T0 + i * 86400) for i in range(2)]
        )

    def test_a_rule_pointing_at_a_real_candidate_survives(self):
        doc, dropped = ru.verify({"rules": [{"rule": "x", "because": "y", "candidate": 1}]}, self.found)
        self.assertEqual(len(doc["rules"]), 1)
        self.assertEqual(dropped, [])

    def test_a_rule_about_a_failure_that_never_happened_is_deleted(self):
        """It would go into a file that steers every future session."""
        doc, dropped = ru.verify(
            {"rules": [{"rule": "never use tabs", "because": "y", "candidate": 9}]}, self.found
        )
        self.assertEqual(doc["rules"], [])
        self.assertEqual(len(dropped), 1)
        self.assertIn("never use tabs", dropped[0])

    def test_a_rule_with_no_candidate_at_all_is_deleted(self):
        doc, _ = ru.verify({"rules": [{"rule": "x", "because": "y"}]}, self.found)
        self.assertEqual(doc["rules"], [])

    def test_no_rules_is_a_real_answer(self):
        doc, dropped = ru.verify({"rules": []}, self.found)
        self.assertEqual(doc["rules"], [])
        self.assertEqual(dropped, [])


class Write(unittest.TestCase):
    def run_write(self, doc):
        found = ru.recurring(
            [sess(failing("make test", ERR), f"s{i}", T0 + i * 86400) for i in range(2)]
        )

        def fake(system, user, schema, model):
            return dict(doc), {"model": "sonnet"}

        real = ru.rn.call_claude
        ru.rn.call_claude = fake
        try:
            return ru.write(project="builder", recurrences=found)
        finally:
            ru.rn.call_claude = real

    def test_a_dash_is_rewritten_and_counted(self):
        out = self.run_write(
            {"rules": [{"rule": "Start Postgres — it is not running", "because": "y", "candidate": 1}]}
        )
        self.assertNotIn("—", out["rules"][0]["rule"])
        self.assertEqual(out["dashes_rewritten"], 1)

    def test_an_invented_rule_is_dropped_counted_and_logged(self):
        with self.assertLogs("analysis.rules", level=logging.WARNING) as log:
            out = self.run_write({"rules": [{"rule": "no tabs", "because": "y", "candidate": 7}]})
        self.assertEqual(out["rules"], [])
        self.assertEqual(out["invented_rules_dropped"], 1)
        self.assertIn("no tabs", log.output[0])


class Prompt(unittest.TestCase):
    def test_the_prompt_bans_the_rule_that_stops_nothing(self):
        text = ru.PROMPT_PATH.read_text()
        self.assertIn("THE RULE MUST STOP THE FAILURE YOU WERE SHOWN", text)
        self.assertIn("NEVER INVENT A FACT ABOUT THE PROJECT", text)

    def test_the_schema_makes_the_model_say_how_sure_it_is(self):
        props = ru.load_schema()["properties"]["rules"]["items"]["properties"]
        self.assertEqual(set(props["confidence"]["enum"]), {"certain", "likely", "guess"})
        self.assertIn("candidate", props)


if __name__ == "__main__":
    unittest.main()
