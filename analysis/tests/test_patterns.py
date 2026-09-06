"""Comparative findings: the sentence, and the bars that stop it being written.

Every case is small enough to check by hand. The refusals matter more than the values:
a "pattern" over three prompts against four is exactly the plausible wrong number this
codebase exists to refuse, so most of these tests assert that nothing was said.
"""

from __future__ import annotations

import datetime as dt
import unittest

from analysis import patterns as pt
from analysis.digest import Ev

#: 2026-09-01 09:00:00 UTC, a Tuesday morning: daylight in every timezone used here.
T0 = dt.datetime(2026, 9, 1, 9, 0, tzinfo=dt.UTC).timestamp()


def ev(n, ts, kind, text="", tool=None, added=None, path=None):
    return Ev(n, ts, kind, text, tool=tool, added=added, path=path)


def sess(events, *, sid="s", start=T0, active=3600.0, tz=0):
    return pt.SessionEvents(
        session_id=sid,
        started_at=start,
        ended_at=start + active,
        active_seconds=active,
        attended_seconds=active,
        tz_offset_minutes=tz,
        events=events,
    )


def by_id(found, fid):
    return next((f for f in found if f.id == fid), None)


class ShortPrompts(unittest.TestCase):
    """A short prompt followed by a correction, against a long one that lands.

    One prompt per session, because a follow-up prompt is itself a prompt: writing the
    correction as another `prompt` event would put it in one of the two groups being
    compared and quietly move the number this test is checking. An interrupt is the
    steering signal here, and a session that just ends is a prompt that landed.
    """

    SHORT = "fix it"
    LONG = "please refactor the parser so that it reads the trailing line safely and fast"

    def build(self, shorts, longs):
        out = []
        for group, text in ((shorts, self.SHORT), (longs, self.LONG)):
            for i, corrected in enumerate(group):
                events = [ev(0, T0, "prompt", text), ev(1, T0 + 60, "tool", "ls", tool="Bash")]
                if corrected:
                    events.append(ev(2, T0 + 120, "interrupt"))
                out.append(sess(events, sid=f"{text[:5]}{i}"))
        return out

    def test_a_wide_gap_is_reported_with_both_counts(self):
        # 5 short prompts, 4 corrected; 5 long prompts, 0 corrected.
        found = by_id(
            pt.findings(self.build([True] * 4 + [False], [False] * 5)),
            "short_prompts_get_corrected",
        )
        self.assertIsNotNone(found)
        self.assertEqual(found.left["n"], 5)
        self.assertEqual(found.left["corrected"], 4)
        self.assertIn("4 of 5", found.text)

    def test_four_against_four_is_refused_however_wide_the_gap(self):
        # The gap is total. The sample is one below the bar, so nothing is said.
        found = pt.findings(self.build([True] * 4, [False] * 4))
        self.assertIsNone(by_id(found, "short_prompts_get_corrected"))

    def test_a_narrow_gap_over_a_big_sample_is_refused(self):
        # 10 v 10, one more correction on the short side: 10 points, under the 15 bar.
        found = pt.findings(self.build([True] * 2 + [False] * 8, [True] + [False] * 9))
        self.assertIsNone(by_id(found, "short_prompts_get_corrected"))


class OpeningPrompt(unittest.TestCase):
    @staticmethod
    def corpus(first_len, rest_len, sessions=5):
        return [
            sess(
                [
                    ev(0, T0, "prompt", "a" * first_len),
                    ev(1, T0 + 60, "prompt", "b" * rest_len),
                    ev(2, T0 + 120, "prompt", "c" * rest_len),
                ],
                sid=f"s{i}",
            )
            for i in range(sessions)
        ]

    def test_a_front_loaded_brief_is_reported(self):
        found = by_id(pt.findings(self.corpus(400, 100)), "the_opening_prompt")
        self.assertIsNotNone(found)
        self.assertEqual(found.left["mean_chars"], 400)
        self.assertEqual(found.right["mean_chars"], 100)
        self.assertEqual(found.lift, 4.0)

    def test_an_opening_prompt_barely_longer_is_not_a_pattern(self):
        # 1.3x, under MIN_LIFT.
        self.assertIsNone(by_id(pt.findings(self.corpus(130, 100)), "the_opening_prompt"))

    def test_four_sessions_is_refused(self):
        self.assertIsNone(
            by_id(pt.findings(self.corpus(400, 100, sessions=4)), "the_opening_prompt")
        )


class TheLeash(unittest.TestCase):
    def test_the_longest_run_is_reported_against_the_median(self):
        events, n, t = [], 0, T0
        for calls in (2, 2, 2, 2, 2, 40):
            events.append(ev(n, t, "prompt", "go"))
            n, t = n + 1, t + 60
            for _ in range(calls):
                events.append(ev(n, t, "tool", "ls", tool="Bash"))
                n, t = n + 1, t + 10
        found = by_id(pt.findings([sess(events)]), "the_leash")
        self.assertIsNotNone(found)
        self.assertEqual(found.right["tool_calls"], 2)
        self.assertEqual(found.left["tool_calls"], 40)
        self.assertIn("40 calls in a row", found.text)

    def test_an_even_leash_is_not_a_finding(self):
        events, n, t = [], 0, T0
        for _ in range(8):
            events.append(ev(n, t, "prompt", "go"))
            n, t = n + 1, t + 60
            for _ in range(3):
                events.append(ev(n, t, "tool", "ls", tool="Bash"))
                n, t = n + 1, t + 10
        self.assertIsNone(by_id(pt.findings([sess(events)]), "the_leash"))


class NightSessions(unittest.TestCase):
    @staticmethod
    def corpus(night_minutes, day_minutes, *, n=5):
        # tz 0: 23:00 UTC is night, 09:00 UTC is day.
        night_start = T0 + 14 * 3600
        return [
            sess([], sid=f"n{i}", start=night_start + i * 86400, active=night_minutes * 60)
            for i in range(n)
        ] + [
            sess([], sid=f"d{i}", start=T0 + i * 86400, active=day_minutes * 60) for i in range(n)
        ]

    def test_longer_nights_are_reported(self):
        found = by_id(pt.findings(self.corpus(120, 40)), "night_sessions")
        self.assertIsNotNone(found)
        self.assertEqual(found.left["mean_minutes"], 120)
        self.assertIn("longer", found.text)

    def test_shorter_nights_are_reported_the_other_way(self):
        found = by_id(pt.findings(self.corpus(30, 120)), "night_sessions")
        self.assertIsNotNone(found)
        self.assertIn("shorter", found.text)
        self.assertLess(found.lift, 1.0)

    def test_four_nights_is_refused(self):
        self.assertIsNone(by_id(pt.findings(self.corpus(120, 40, n=4)), "night_sessions"))

    def test_the_local_hour_decides_not_the_utc_one(self):
        # The same instants read in UTC-8: 23:00 UTC becomes 15:00 local (daylight) and
        # 09:00 UTC becomes 01:00 local (before the 04:00 day boundary, so night). The
        # two groups swap wholesale, which is the point: a timezone bug here would report
        # somebody's mornings back to them as their late nights.
        shifted = [
            pt.SessionEvents(
                session_id=s.session_id,
                started_at=s.started_at,
                ended_at=s.ended_at,
                active_seconds=s.active_seconds,
                attended_seconds=s.attended_seconds,
                tz_offset_minutes=-480,
                events=s.events,
            )
            for s in self.corpus(120, 40)
        ]
        utc = by_id(pt.findings(self.corpus(120, 40)), "night_sessions")
        local = by_id(pt.findings(shifted), "night_sessions")
        self.assertEqual(utc.left["mean_minutes"], 120)
        self.assertEqual(local.left["mean_minutes"], 40)
        self.assertEqual(local.right["mean_minutes"], 120)


class VerificationHabit(unittest.TestCase):
    def test_a_tested_corpus_reports_its_share(self):
        # 6 bursts, each written then tested, in six separate sessions.
        sessions = [
            sess(
                [
                    ev(0, T0, "tool", "", tool="Edit", added=3, path="a.py"),
                    ev(1, T0 + 10, "tool", "pytest -q", tool="Bash"),
                ],
                sid=f"s{i}",
            )
            for i in range(6)
        ]
        found = by_id(pt.findings(sessions), "verification_habit")
        self.assertIsNotNone(found)
        self.assertEqual(found.left["count"], 6)
        self.assertIn("100%", found.text)

    def test_an_untested_burst_at_the_end_of_a_session_counts_against(self):
        sessions = [
            sess(
                [
                    ev(0, T0, "tool", "", tool="Edit", added=3, path="a.py"),
                    ev(1, T0 + 10, "tool", "pytest -q", tool="Bash"),
                ],
                sid=f"s{i}",
            )
            for i in range(3)
        ] + [
            sess([ev(0, T0, "tool", "", tool="Edit", added=3, path="b.py")], sid=f"x{i}")
            for i in range(3)
        ]
        found = by_id(pt.findings(sessions), "verification_habit")
        self.assertIsNotNone(found)
        self.assertEqual(found.left["count"], 3)
        self.assertEqual(found.right["count"], 3)
        self.assertIn("3 of 6", found.text)

    def test_four_bursts_is_refused(self):
        sessions = [
            sess(
                [
                    ev(0, T0, "tool", "", tool="Edit", added=3, path="a.py"),
                    ev(1, T0 + 10, "tool", "pytest -q", tool="Bash"),
                ],
                sid=f"s{i}",
            )
            for i in range(4)
        ]
        self.assertIsNone(by_id(pt.findings(sessions), "verification_habit"))


class Rework(unittest.TestCase):
    @staticmethod
    def touch(n, path, times):
        return [ev(n + i, T0 + i * 60, "tool", "", tool="Edit", added=2, path=path) for i in range(times)]

    def test_the_most_reworked_file_is_named_with_its_count(self):
        events = self.touch(0, "/repo/parser.py", 7) + self.touch(10, "/repo/a.py", 1)
        events += self.touch(20, "/repo/b.py", 1) + self.touch(30, "/repo/c.py", 1)
        events += self.touch(40, "/repo/d.py", 1) + self.touch(50, "/repo/e.py", 1)
        found = by_id(pt.findings([sess(events)]), "rework")
        self.assertIsNotNone(found)
        self.assertIn("parser.py", found.text)
        self.assertIn("7 times", found.text)
        self.assertEqual(found.left["count"], 1)
        self.assertEqual(found.right["count"], 5)

    def test_twice_is_not_rework(self):
        events = self.touch(0, "/repo/a.py", 2) + self.touch(10, "/repo/b.py", 2)
        events += self.touch(20, "/repo/c.py", 2) + self.touch(30, "/repo/d.py", 2)
        events += self.touch(40, "/repo/e.py", 2) + self.touch(50, "/repo/f.py", 2)
        self.assertIsNone(by_id(pt.findings([sess(events)]), "rework"))

    def test_a_read_is_not_a_write(self):
        # `added is None` means the tool reported no line count: Read, Grep, `sed -i`.
        events = [
            ev(i, T0 + i * 60, "tool", "", tool="Read", added=None, path="/repo/parser.py")
            for i in range(9)
        ]
        self.assertIsNone(by_id(pt.findings([sess(events)]), "rework"))


class Ordering(unittest.TestCase):
    def test_the_most_striking_finding_comes_first(self):
        events, n, t = [], 0, T0
        for calls in (1, 1, 1, 1, 1, 200):
            events.append(ev(n, t, "prompt", "a" * 400 if n == 0 else "b" * 100))
            n, t = n + 1, t + 60
            for _ in range(calls):
                events.append(ev(n, t, "tool", "ls", tool="Bash"))
                n, t = n + 1, t + 10
        found = pt.findings([sess(events, sid=f"s{i}") for i in range(5)])
        self.assertGreaterEqual(len(found), 2)
        self.assertEqual(found[0].id, "the_leash")


class Empty(unittest.TestCase):
    def test_no_sessions_says_nothing_rather_than_zero(self):
        self.assertEqual(pt.findings([]), [])

    def test_one_session_says_nothing(self):
        self.assertEqual(pt.findings([sess([ev(0, T0, "prompt", "hello there")])]), [])


if __name__ == "__main__":
    unittest.main()
