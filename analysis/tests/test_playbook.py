"""Your own prompts, sorted by what happened after them.

The whole feature is a comparison, so what matters is which pile a prompt lands in. Two
cases below are corrections to rules that were wrong on the first real corpus, and both
are marked: sorting by length, and treating a long run as a failure.
"""

from __future__ import annotations

import datetime as dt
import unittest

from analysis import patterns as pt
from analysis import playbook as pb
from analysis.digest import Ev

T0 = dt.datetime(2026, 9, 1, 9, 0, tzinfo=dt.UTC).timestamp()

BRIEF = "please add a settings screen with a handle field and wire it to the users endpoint"


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


def run(prompt, *, landed=1, calls=1, then=None, sid="s"):
    """One prompt, `calls` tool calls of which `landed` produced something, then `then`."""
    out, n, t = [ev(0, T0, "prompt", prompt)], 1, T0 + 10
    for i in range(calls):
        if i < landed:
            out.append(ev(n, t, "tool", "", tool="Edit", added=3, path="/repo/a.py"))
        else:
            out.append(ev(n, t, "tool", "ls", tool="Bash"))
        n, t = n + 1, t + 5
    if then is not None:
        out.append(then(n, t))
    return sess(out, sid)


class Worked(unittest.TestCase):
    def test_a_prompt_that_landed_and_was_never_taken_back_worked(self):
        at = pb.attempts([run(BRIEF, landed=3, calls=5)])[0]
        self.assertTrue(at.worked)
        self.assertEqual(at.landed, 3)

    def test_an_interrupt_afterwards_means_it_cost_a_round_trip(self):
        at = pb.attempts(
            [run(BRIEF, landed=3, calls=5, then=lambda n, t: ev(n, t, "interrupt"))]
        )[0]
        self.assertFalse(at.worked)
        self.assertTrue(at.corrected)

    def test_a_corrective_prompt_afterwards_means_the_same(self):
        at = pb.attempts(
            [run(BRIEF, landed=3, calls=5, then=lambda n, t: ev(n, t, "prompt", "no, undo that"))]
        )[0]
        self.assertTrue(at.corrected)

    def test_a_follow_up_that_is_not_a_correction_does_not_count_against_it(self):
        at = pb.attempts(
            [
                run(
                    BRIEF,
                    landed=3,
                    calls=5,
                    then=lambda n, t: ev(n, t, "prompt", "great, now add the same for photos"),
                )
            ]
        )[0]
        self.assertTrue(at.worked)

    def test_nothing_landing_is_not_a_prompt_that_worked(self):
        at = pb.attempts([run(BRIEF, landed=0, calls=5)])[0]
        self.assertFalse(at.worked)

    def test_a_long_run_that_landed_a_lot_is_a_good_prompt(self):
        """FOUND BY RUNNING IT. The first version counted any long run as a failure, and
        the best prompt in the reference corpus produced 44 landed things over 484 tool
        calls and got filed under "cost a round trip". A long autonomous run is what some
        people are asking for."""
        at = pb.attempts([run(BRIEF, landed=44, calls=484)])[0]
        self.assertTrue(at.worked)
        self.assertFalse(at.stalled)

    def test_a_long_run_with_nothing_landing_is_a_stall(self):
        at = pb.attempts([run(BRIEF, landed=0, calls=100)])[0]
        self.assertTrue(at.stalled)
        self.assertFalse(at.worked)

    def test_a_short_run_with_nothing_landing_is_not_called_a_stall(self):
        at = pb.attempts([run(BRIEF, landed=0, calls=3)])[0]
        self.assertFalse(at.stalled)


class WhatCounts(unittest.TestCase):
    def test_a_one_liner_is_not_a_technique(self):
        self.assertEqual(pb.attempts([run("fix it", landed=3, calls=5)]), [])

    def test_a_test_run_counts_as_something_landing(self):
        events = [
            ev(0, T0, "prompt", BRIEF),
            ev(1, T0 + 10, "tool", "pytest -q", tool="Bash"),
        ]
        self.assertEqual(pb.attempts([sess(events)])[0].landed, 1)

    def test_a_commit_counts_as_something_landing(self):
        events = [
            ev(0, T0, "prompt", BRIEF),
            ev(1, T0 + 10, "tool", "git commit -m x", tool="Bash"),
        ]
        self.assertEqual(pb.attempts([sess(events)])[0].landed, 1)

    def test_a_read_does_not(self):
        events = [
            ev(0, T0, "prompt", BRIEF),
            ev(1, T0 + 10, "tool", "", tool="Read", path="/repo/a.py"),
        ]
        self.assertEqual(pb.attempts([sess(events)])[0].landed, 0)


class Split(unittest.TestCase):
    def corpus(self):
        return [
            run(BRIEF, landed=5, calls=8, sid="w1"),
            run(BRIEF + " and the tests", landed=2, calls=3, sid="w2"),
            run("please " * 60 + "do the thing carefully", landed=1, calls=2, sid="w3"),
            run(BRIEF, landed=0, calls=90, sid="c1"),
            run(BRIEF, landed=1, calls=9, sid="c2", then=lambda n, t: ev(n, t, "interrupt")),
            run(BRIEF, landed=0, calls=2, sid="c3"),
        ]

    def test_the_pile_that_worked_is_ranked_by_what_landed_not_by_length(self):
        """"Longer prompts work better" is the conclusion this feature reaches if you let
        it sort by size, and it is the advice the internet already gives for free."""
        worked, _ = pb.split(pb.attempts(self.corpus()))
        self.assertEqual([a.landed for a in worked], [5, 2, 1])
        self.assertLess(len(worked[0].text), len(worked[-1].text))

    def test_the_costly_pile_leads_with_the_most_expensive(self):
        _, cost = pb.split(pb.attempts(self.corpus()))
        self.assertEqual(cost[0].tool_calls, 90)

    def test_the_rate_is_the_share_that_landed_cleanly(self):
        stats = pb.summary(pb.attempts(self.corpus()))
        self.assertEqual(stats["worked"], 3)
        self.assertEqual(stats["cost"], 3)
        self.assertAlmostEqual(stats["value"], 0.5)

    def test_two_a_side_is_refused_rather_than_reported(self):
        stats = pb.summary(pb.attempts(self.corpus()[:2] + self.corpus()[3:5]))
        self.assertIsNone(stats["value"])
        self.assertIn("needed", stats["reason"])

    def test_no_prompts_is_refused_not_zero(self):
        self.assertIsNone(pb.summary([])["value"])


if __name__ == "__main__":
    unittest.main()
