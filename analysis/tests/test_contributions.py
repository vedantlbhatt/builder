"""The contributions graph, split by whether an agent was in the room.

The split is the whole feature, so the cases here are about the boundary between
"assisted" and "alone" and about the streak, which is the one number people will argue
with. The bias is deliberate and stated: a commit the capture never saw counts as YOURS.
Overstating how much an agent did is the claim nobody can check and everybody resents.
"""

from __future__ import annotations

import datetime as dt
import unittest

from analysis import contributions as co

DAY = 86400.0
#: 2026-09-01 12:00 UTC, comfortably inside one local day at any offset used here.
T0 = dt.datetime(2026, 9, 1, 12, 0, tzinfo=dt.UTC).timestamp()


class TheSplit(unittest.TestCase):
    def test_a_commit_inside_a_session_is_assisted(self):
        c = co.split([T0 + 60], [(T0, T0 + 3600)])
        self.assertEqual((c.assisted, c.alone), (1, 0))

    def test_a_commit_outside_every_session_is_yours(self):
        c = co.split([T0 + 99999], [(T0, T0 + 3600)])
        self.assertEqual((c.assisted, c.alone), (0, 1))

    def test_a_commit_just_before_a_session_belongs_to_it(self):
        """The agent wrote the code, the person committed as the sitting was starting.
        Same lookback the per-session counts use."""
        c = co.split([T0 - 600], [(T0, T0 + 3600)])
        self.assertEqual(c.assisted, 1)
        self.assertGreater(co.LOOKBACK_SEC, 600)

    def test_a_commit_before_the_lookback_is_yours(self):
        c = co.split([T0 - co.LOOKBACK_SEC - 60], [(T0, T0 + 3600)])
        self.assertEqual(c.alone, 1)

    def test_a_commit_in_two_overlapping_sittings_is_counted_once(self):
        c = co.split([T0 + 100], [(T0, T0 + 3600), (T0 + 60, T0 + 7200)])
        self.assertEqual(c.total, 1)
        self.assertEqual(c.assisted, 1)

    def test_no_sessions_means_every_commit_is_yours(self):
        """The safe direction. A sitting the capture never saw must not turn your work
        into the agent's."""
        c = co.split([T0, T0 + 60, T0 + 120], [])
        self.assertEqual(c.alone, 3)
        self.assertEqual(c.assisted, 0)

    def test_no_commits_is_an_empty_graph_and_not_a_crash(self):
        c = co.split([], [(T0, T0 + 3600)])
        self.assertEqual(c.total, 0)
        self.assertEqual(c.days, ())
        self.assertEqual(c.longest_streak, 0)


class TheShare(unittest.TestCase):
    def test_four_commits_is_too_few_to_call_a_share(self):
        c = co.split([T0 + i for i in range(4)], [(T0, T0 + 3600)])
        self.assertIsNone(c.assisted_share)

    def test_five_is_enough(self):
        c = co.split([T0 + i for i in range(5)], [(T0, T0 + 3600)])
        self.assertEqual(c.assisted_share, 1.0)

    def test_the_share_is_assisted_over_everything(self):
        c = co.split([T0, T0 + 1, T0 + 2, T0 + 4 * 3600, T0 + 5 * 3600], [(T0, T0 + 3600)])
        self.assertEqual(c.assisted_share, 0.6)


class Days(unittest.TestCase):
    def test_a_day_starts_at_four_in_the_morning(self):
        """At 00:20 mid-session the menu bar read "0s active today", which was technically
        correct and completely wrong."""
        just_before = dt.datetime(2026, 9, 2, 3, 30, tzinfo=dt.UTC).timestamp()
        just_after = dt.datetime(2026, 9, 2, 4, 30, tzinfo=dt.UTC).timestamp()
        self.assertEqual(co.local_day(just_before, 0), dt.date(2026, 9, 1))
        self.assertEqual(co.local_day(just_after, 0), dt.date(2026, 9, 2))

    def test_the_timezone_decides_the_day_not_utc(self):
        # 02:00 UTC is the previous evening in New York, which is the same local day.
        night = dt.datetime(2026, 9, 2, 2, 0, tzinfo=dt.UTC).timestamp()
        self.assertEqual(co.local_day(night, -300), dt.date(2026, 9, 1))

    def test_each_day_carries_its_own_split(self):
        # Three hours after the second sitting: same local day, well outside its window.
        later = T0 + DAY + 3 * 3600
        c = co.split([T0, T0 + DAY, later], [(T0, T0 + 3600), (T0 + DAY, T0 + DAY + 60)])
        self.assertEqual(len(c.days), 2)
        self.assertEqual((c.days[1].assisted, c.days[1].alone), (1, 1))
        self.assertEqual(c.days[1].total, 2)

    def test_the_days_come_back_in_order(self):
        c = co.split([T0 + 2 * DAY, T0, T0 + DAY], [])
        self.assertEqual(list(c.days), sorted(c.days, key=lambda d: d.day))


class Streaks(unittest.TestCase):
    @staticmethod
    def on_days(*offsets_from_today):
        now = dt.datetime.now(dt.UTC).timestamp()
        return co.split([now - o * DAY for o in offsets_from_today], [])

    def test_a_streak_counts_days_you_shipped(self):
        """Not days you opened the app. A streak a tab can extend is a streak about the
        tab."""
        c = self.on_days(3, 2, 1, 0)
        self.assertEqual(c.longest_streak, 4)
        self.assertEqual(c.current_streak, 4)

    def test_a_gap_breaks_it(self):
        c = self.on_days(9, 8, 4, 3, 2)
        self.assertEqual(c.longest_streak, 3)

    def test_a_streak_that_ended_yesterday_is_still_going(self):
        """Today is not over. Otherwise every streak in the world breaks every morning
        before the first commit."""
        self.assertEqual(self.on_days(2, 1).current_streak, 2)

    def test_a_streak_that_ended_two_days_ago_is_over(self):
        self.assertEqual(self.on_days(4, 3, 2).current_streak, 0)

    def test_two_commits_in_one_day_are_one_day_of_streak(self):
        now = dt.datetime.now(dt.UTC).timestamp()
        c = co.split([now, now - 60, now - 120], [])
        self.assertEqual(c.active_days, 1)
        self.assertEqual(c.longest_streak, 1)
        self.assertEqual(c.total, 3)


if __name__ == "__main__":
    unittest.main()
