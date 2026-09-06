"""Time to green, and the change failure rate this codebase refuses to invent.

Two of DORA's four questions are answerable from a transcript and two are not. The
refusals matter as much as the numbers: a change failure rate greped out of commit
messages is a rate about how somebody WRITES commit messages, and it scores the
disciplined person worse than the one who writes "wip".
"""

from __future__ import annotations

import datetime as dt
import unittest

from analysis import patterns as pt
from analysis import quality as q
from analysis.digest import Ev

T0 = dt.datetime(2026, 9, 1, 9, 0, tzinfo=dt.UTC).timestamp()


def sess(events, sid="s"):
    return pt.SessionEvents(
        session_id=sid,
        started_at=T0,
        ended_at=T0 + 86400,
        active_seconds=3600.0,
        attended_seconds=3600.0,
        tz_offset_minutes=0,
        events=events,
    )


def run(n, ts, cmd="pytest -q", failed=False):
    out = [Ev(n, ts, "tool", cmd, tool="Bash")]
    if failed:
        out.append(Ev(n + 1, ts + 5, "result_error", "1 failed"))
    return out


class WhatCountsAsATestRun(unittest.TestCase):
    def test_the_usual_runners_are_recognised(self):
        for cmd in ("pytest -q", "make test", "cd x && bun test", "uv run pytest", "go test ./..."):
            self.assertTrue(q.TEST_CMD.search(cmd), cmd)

    def test_looking_at_the_test_runner_is_not_running_it(self):
        """FOUND BY RUNNING IT: `head -1 /root/.local/bin/pytest` was counted as a test
        run, and as a recovery from failure when the next one passed. A bare word boundary
        matches a PATH."""
        for cmd in (
            "head -1 /root/.local/bin/pytest",
            "cat notes/pytest.md",
            "ls ~/.local/bin/pytest",
        ):
            self.assertIsNone(q.TEST_CMD.search(cmd), cmd)

    def test_there_is_one_definition_and_the_other_modules_use_it(self):
        """Three regexes that drift produce three different answers to "how often do you
        test" and no way to tell which is right."""
        import re

        from analysis import agents, patterns

        self.assertIs(patterns._TEST_CMD, q.TEST_CMD)
        # Nothing else may define its own list of test runners.
        for module in (agents, patterns):
            for name, value in vars(module).items():
                if isinstance(value, re.Pattern) and "pytest" in value.pattern:
                    self.assertIs(value, q.TEST_CMD, f"{module.__name__}.{name} is a second copy")


class TimeToGreen(unittest.TestCase):
    def test_a_failure_then_a_pass_is_a_recovery(self):
        events = run(0, T0, failed=True) + run(2, T0 + 300)
        got = q.recoveries([sess(events)])
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0].seconds, 300)
        self.assertEqual(got[0].attempts, 2)

    def test_several_failures_before_the_pass_are_one_recovery(self):
        events = run(0, T0, failed=True) + run(2, T0 + 60, failed=True) + run(4, T0 + 120)
        got = q.recoveries([sess(events)])
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0].attempts, 3)
        self.assertEqual(got[0].seconds, 120)

    def test_a_pass_with_no_failure_before_it_is_not_a_recovery(self):
        self.assertEqual(q.recoveries([sess(run(0, T0))]), [])

    def test_a_failure_that_never_came_back_green_is_left_out(self):
        """Closing it at the session boundary would report the fastest possible number
        for the worst possible outcome."""
        self.assertEqual(q.recoveries([sess(run(0, T0, failed=True))]), [])

    def test_a_gap_of_hours_is_a_different_days_work(self):
        events = run(0, T0, failed=True) + run(2, T0 + q.MAX_GAP_SEC + 60)
        self.assertEqual(q.recoveries([sess(events)]), [])

    def test_two_recoveries_in_one_sitting_are_both_counted(self):
        events = run(0, T0, failed=True) + run(2, T0 + 60)
        events += run(4, T0 + 600, failed=True) + run(6, T0 + 900)
        self.assertEqual(len(q.recoveries([sess(events)])), 2)


class Summary(unittest.TestCase):
    def corpus(self, passes=8, fails=2):
        events, n, t = [], 0, T0
        for _ in range(fails):
            events += run(n, t, failed=True)
            n, t = n + 2, t + 60
            events += run(n, t)
            n, t = n + 1, t + 60
        for _ in range(passes):
            events += run(n, t)
            n, t = n + 1, t + 60
        return [sess(events)]

    def test_the_share_that_passed(self):
        s = q.summary(self.corpus(passes=8, fails=2))
        # 2 failed + 2 recovery passes + 8 clean = 12 runs, 10 passed.
        self.assertEqual(s["runs"], 12)
        self.assertEqual(s["failed"], 2)
        self.assertAlmostEqual(s["first_try_rate"], round(10 / 12, 3))

    def test_the_median_and_the_worst(self):
        s = q.summary(self.corpus())
        self.assertEqual(s["time_to_green"]["n"], 2)
        self.assertEqual(s["time_to_green"]["median_seconds"], 60)

    def test_four_test_runs_is_refused_rather_than_reported(self):
        s = q.summary([sess(run(0, T0) + run(1, T0 + 60) + run(2, T0 + 120))])
        self.assertIsNone(s["first_try_rate"])
        self.assertIn("needed", s["reason"])

    def test_a_corpus_that_never_failed_reports_the_rate_and_no_recovery(self):
        s = q.summary(self.corpus(passes=10, fails=0))
        self.assertEqual(s["first_try_rate"], 1.0)
        self.assertIsNone(s["time_to_green"])
        self.assertIn("nothing failed", s["reason"])

    def test_no_sessions_is_a_refusal(self):
        self.assertIsNone(q.summary([])["first_try_rate"])


class Refusals(unittest.TestCase):
    def test_there_is_no_change_failure_rate_anywhere_in_this_module(self):
        """MEASURED on this repository: 99 commits in seven days, four matched a fix word,
        and three of those four were INTENTIONAL fixes rather than regressions. A rate
        built on that scores somebody disciplined enough to write "fix" worse than
        somebody who writes "wip", which is exactly backwards."""
        self.assertNotIn("change_failure", dir(q))
        self.assertIn("change failure rate", q.__doc__)
        self.assertIn("does not invent one", q.__doc__)


if __name__ == "__main__":
    unittest.main()
