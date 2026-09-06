"""You against you last month: what counts as a move, and what must never be called one.

There is no cohort to compare a person against, so this is the only comparison that
works. Each case below corresponds to a way the naive version lies.
"""

from __future__ import annotations

import unittest

from analysis import trends as tr


def prof(sessions: int, **metrics):
    return {
        "sample": {"sessions": sessions},
        "metrics": {k: {"value": v} for k, v in metrics.items()},
    }


def by(ts, metric):
    return next((t for t in ts if t.metric == metric), None)


class Windows(unittest.TestCase):
    def test_three_sessions_a_side_is_not_a_trend(self):
        got = tr.compare(prof(3, test_runs_per_hour=6.0), prof(10, test_runs_per_hour=3.0))
        self.assertEqual(got, [])

    def test_a_big_window_against_a_tiny_one_is_refused_from_either_side(self):
        self.assertEqual(tr.compare(prof(40, ships_rate=0.5), prof(2, ships_rate=0.9)), [])

    def test_both_windows_with_a_sample_produce_a_trend(self):
        got = tr.compare(prof(10, test_runs_per_hour=6.0), prof(10, test_runs_per_hour=3.0))
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0].sessions_before, 10)


class Moves(unittest.TestCase):
    def test_a_real_drop_is_a_drop(self):
        t = by(tr.compare(prof(9, test_runs_per_hour=6.0), prof(9, test_runs_per_hour=3.0)), "test_runs_per_hour")
        self.assertEqual(t.direction, "down")
        self.assertAlmostEqual(t.move, -0.5)

    def test_a_small_wobble_is_steady_rather_than_padded_into_a_direction(self):
        t = by(tr.compare(prof(9, ships_rate=0.40), prof(9, ships_rate=0.43)), "ships_rate")
        self.assertTrue(t.steady)
        self.assertIsNone(t.good)

    def test_the_biggest_move_comes_first(self):
        got = tr.compare(
            prof(9, ships_rate=0.40, test_runs_per_hour=6.0),
            prof(9, ships_rate=0.44, test_runs_per_hour=1.0),
        )
        self.assertEqual(got[0].metric, "test_runs_per_hour")


class Direction(unittest.TestCase):
    def test_testing_less_is_the_bad_direction(self):
        t = by(tr.compare(prof(9, test_runs_per_hour=6.0), prof(9, test_runs_per_hour=2.0)), "test_runs_per_hour")
        self.assertIs(t.good, False)

    def test_taking_the_wheel_back_less_is_the_good_direction(self):
        t = by(tr.compare(prof(9, steer_rate=0.5), prof(9, steer_rate=0.2)), "steer_rate")
        self.assertIs(t.good, True)

    def test_a_metric_with_no_better_direction_gets_no_verdict(self):
        """More hours is not better. More night work is not worse. The reader decides."""
        t = by(tr.compare(prof(9, night_share=0.2), prof(9, night_share=0.6)), "night_share")
        self.assertEqual(t.direction, "up")
        self.assertIsNone(t.good)

    def test_every_metric_with_a_verdict_has_a_label_a_person_can_read(self):
        for metric in tr.BETTER:
            self.assertIn(metric, tr.LABEL, f"{metric} has a direction and no wording")


class Refusals(unittest.TestCase):
    def test_a_metric_the_profile_refused_has_no_trend(self):
        """Subtracting None from None and calling it flat turns "we cannot know" into a
        claim. `corpus_profile` returns None with a reason for anything it cannot compute."""
        got = tr.compare(prof(9, code_velocity=None), prof(9, code_velocity=500.0))
        self.assertIsNone(by(got, "code_velocity"))

    def test_a_metric_absent_from_the_earlier_window_has_no_trend(self):
        got = tr.compare(prof(9, ships_rate=0.4), prof(9, ships_rate=0.4, spend_per_hour_usd=12.0))
        self.assertIsNone(by(got, "spend_per_hour_usd"))

    def test_a_move_from_zero_is_not_a_percentage(self):
        # Dividing by zero, or calling it an infinite rise, are both worse than silence.
        got = tr.compare(prof(9, test_runs_per_hour=0.0), prof(9, test_runs_per_hour=5.0))
        self.assertIsNone(by(got, "test_runs_per_hour"))


class Headline(unittest.TestCase):
    def test_it_prefers_a_move_that_means_something(self):
        got = tr.compare(
            prof(9, night_share=0.1, test_runs_per_hour=6.0),
            prof(9, night_share=0.9, test_runs_per_hour=3.0),
        )
        # night_share moved further, and nobody can act on it.
        self.assertEqual(got[0].metric, "night_share")
        self.assertIn("test", tr.headline(got))

    def test_a_good_move_says_so(self):
        got = tr.compare(prof(9, steer_rate=0.5), prof(9, steer_rate=0.2))
        self.assertIn("the way you want it", tr.headline(got))

    def test_a_bad_move_does_not_congratulate_anybody(self):
        got = tr.compare(prof(9, test_runs_per_hour=6.0), prof(9, test_runs_per_hour=2.0))
        self.assertNotIn("want it", tr.headline(got))

    def test_nothing_moved_is_no_headline_rather_than_an_invented_one(self):
        got = tr.compare(prof(9, ships_rate=0.40), prof(9, ships_rate=0.42))
        self.assertIsNone(tr.headline(got))

    def test_no_trends_at_all_is_no_headline(self):
        self.assertIsNone(tr.headline([]))

    def test_the_sentence_names_the_window_it_actually_compared(self):
        """It used to say "on last month" whatever the window was, so a one day
        comparison announced a monthly trend."""
        got = tr.compare(prof(9, test_runs_per_hour=6.0), prof(9, test_runs_per_hour=2.0))
        self.assertIn("on the day before", tr.headline(got, 1))
        self.assertIn("on the 7 days before", tr.headline(got, 7))
        self.assertIn("on last month", tr.headline(got, 30))
        self.assertIn("on the 3 months before", tr.headline(got, 90))

    def test_the_headline_never_uses_a_dash(self):
        got = tr.compare(prof(9, test_runs_per_hour=6.0), prof(9, test_runs_per_hour=2.0))
        self.assertNotRegex(tr.headline(got), r"[—–―−]")


if __name__ == "__main__":
    unittest.main()
