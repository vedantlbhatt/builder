"""Postable cards: what makes the cut, what gets ranked where, and what never ships.

The bar here is not "is it true" (that was settled upstream, in `profile.py` and
`patterns.py`, both of which refuse rather than estimate). It is "would a stranger
scrolling past stop for it", and these cases pin down the judgement calls that encode it.
"""

from __future__ import annotations

import unittest

from analysis import brag
from analysis import patterns as pt


def finding(fid, left, right, lift=2.0, text="x"):
    return pt.Finding(id=fid, text=text, left=left, right=right, lift=lift, basis="test")


def by_id(cards, cid):
    return next((c for c in cards if c.id == cid), None)


PROFILE = {
    "totals": {"total_lines_added": 4089, "total_commits": 85},
    "metrics": {"spend_usd": {"value": 306.4, "unit": "US dollars at API list prices"}},
    "model_costs": [
        {"model": "Sonnet 5", "usd": 6.04, "commits": 12, "usd_per_commit": 0.5},
        {"model": "Opus 5", "usd": 157.22, "commits": 13, "usd_per_commit": 10.09},
    ],
}

SPIN = finding(
    "the_spin",
    {"n": 12, "worst_tool_calls": 59, "worst_seconds": 605, "total_seconds": 7870},
    {"n": 84, "median_tool_calls": 7},
)
LOOP = finding("stuck_in_a_loop", {"n": 2, "longest_run": 7, "total_seconds": 386}, {"n": 1264})
NIGHT = finding(
    "when_the_work_lands",
    {"n": 5, "lines_per_active_hour": 388.9},
    {"n": 7, "lines_per_active_hour": 181.4},
)
TESTS = finding("verification_habit", {"n": 14, "count": 13, "share": 0.929}, {"n": 14, "count": 1})
REWRITE = finding(
    "fighting_one_file",
    {"n": 40, "count": 1, "worst_file_writes": 7, "worst_file_seconds": 2400},
    {"n": 40, "count": 39},
)


class Ranking(unittest.TestCase):
    def test_a_confession_outranks_a_flex_about_the_same_person(self):
        made = brag.cards(PROFILE, [SPIN, TESTS])
        spin, disc = by_id(made, "the_spin"), by_id(made, "the_discipline")
        self.assertIsNotNone(spin)
        self.assertIsNotNone(disc)
        self.assertGreater(spin.postability, disc.postability)
        self.assertLess(made.index(spin), made.index(disc))

    def test_a_number_in_dollars_outranks_the_same_kind_without_one(self):
        self.assertGreater(brag.score("flex", has_money=True), brag.score("flex"))

    def test_a_bigger_multiple_ranks_higher_but_not_without_limit(self):
        self.assertGreater(brag.score("argument", multiple=5), brag.score("argument", multiple=2))
        # A 40x must not sit above everything else in the feed forever.
        self.assertEqual(brag.score("argument", multiple=40), brag.score("argument", multiple=8))

    def test_nothing_scores_over_one(self):
        for kind in brag.KIND_WEIGHT:
            self.assertLessEqual(brag.score(kind, has_money=True, multiple=100), 1.0)

    def test_the_order_is_stable_for_a_tie(self):
        a = [c.id for c in brag.cards(PROFILE, [SPIN, LOOP, NIGHT])]
        b = [c.id for c in brag.cards(PROFILE, [NIGHT, LOOP, SPIN])]
        self.assertEqual(a, b)


class TheModelBill(unittest.TestCase):
    def test_the_cheapest_and_the_dearest_are_named_with_their_rates(self):
        c = by_id(brag.cards(PROFILE, []), "model_bill")
        self.assertIsNotNone(c)
        self.assertIn("Sonnet 5", c.headline)
        self.assertIn("$0.50", c.headline)
        self.assertIn("$10.09", c.headline)
        self.assertEqual(c.numbers["multiple"], 20.2)

    def test_two_models_that_cost_about_the_same_is_not_an_argument(self):
        flat = {
            **PROFILE,
            "model_costs": [
                {"model": "Sonnet 5", "usd": 6.0, "commits": 12, "usd_per_commit": 0.5},
                {"model": "Haiku 4.5", "usd": 4.0, "commits": 10, "usd_per_commit": 0.6},
            ],
        }
        self.assertIsNone(by_id(brag.cards(flat, []), "model_bill"))

    def test_one_model_has_nothing_to_argue_with(self):
        one = {**PROFILE, "model_costs": PROFILE["model_costs"][:1]}
        self.assertIsNone(by_id(brag.cards(one, []), "model_bill"))

    def test_a_model_with_no_commits_cannot_set_a_rate(self):
        # `usd_per_commit` is None when nothing shipped. It must not become zero.
        none = {
            **PROFILE,
            "model_costs": [
                {"model": "Sonnet 5", "usd": 6.0, "commits": 0, "usd_per_commit": None},
                {"model": "Opus 5", "usd": 157.0, "commits": 13, "usd_per_commit": 10.09},
            ],
        }
        self.assertIsNone(by_id(brag.cards(none, []), "model_bill"))

    def test_the_card_says_these_are_list_prices(self):
        c = by_id(brag.cards(PROFILE, []), "model_bill")
        self.assertIn("list prices", c.detail)


class TheConfessions(unittest.TestCase):
    def test_the_spin_leads_with_the_time_it_cost(self):
        c = by_id(brag.cards(PROFILE, [SPIN]), "the_spin")
        self.assertIn("2h 11m", c.headline)
        self.assertIn("59 tool calls", c.detail)

    def test_half_an_hour_of_spinning_is_not_worth_a_post(self):
        small = finding(
            "the_spin",
            {"n": 2, "worst_tool_calls": 30, "worst_seconds": 200, "total_seconds": 900},
            {"n": 40, "median_tool_calls": 7},
        )
        self.assertIsNone(by_id(brag.cards(PROFILE, [small]), "the_spin"))

    def test_a_four_failure_run_is_ordinary_debugging(self):
        short = finding("stuck_in_a_loop", {"n": 1, "longest_run": 4, "total_seconds": 60}, {"n": 9})
        self.assertIsNone(by_id(brag.cards(PROFILE, [short]), "the_loop"))

    def test_the_rewrite_names_the_count_and_the_time(self):
        c = by_id(brag.cards(PROFILE, [REWRITE]), "the_rewrite")
        self.assertIn("7 times", c.headline)
        self.assertIn("40 minutes", c.detail)

    def test_four_passes_at_a_file_is_not_a_confession(self):
        few = finding(
            "fighting_one_file",
            {"n": 40, "count": 1, "worst_file_writes": 4, "worst_file_seconds": 600},
            {"n": 40, "count": 39},
        )
        self.assertIsNone(by_id(brag.cards(PROFILE, [few]), "the_rewrite"))


class TheFlexes(unittest.TestCase):
    def test_the_build_card_carries_a_price_so_it_is_not_only_a_brag(self):
        c = by_id(brag.cards(PROFILE, []), "what_it_cost_to_build")
        self.assertIn("4,089 lines", c.headline)
        self.assertIn("$306", c.headline)

    def test_it_says_out_loud_that_this_is_not_a_bill(self):
        """The most quotable wrong number this app could print is a subscriber being told
        they spent money they did not spend."""
        c = by_id(brag.cards(PROFILE, []), "what_it_cost_to_build")
        self.assertIn("subscription", c.detail)

    def test_no_spend_means_no_card_rather_than_a_free_build(self):
        broke = {**PROFILE, "metrics": {"spend_usd": {"value": None}}}
        self.assertIsNone(by_id(brag.cards(broke, []), "what_it_cost_to_build"))

    def test_a_middling_test_habit_is_not_something_to_post(self):
        weak = finding("verification_habit", {"n": 14, "count": 8, "share": 0.57}, {"n": 14, "count": 6})
        self.assertIsNone(by_id(brag.cards(PROFILE, [weak]), "the_discipline"))


class TheNightShift(unittest.TestCase):
    def test_a_better_night_rate_says_so(self):
        c = by_id(brag.cards(PROFILE, [NIGHT]), "the_night_shift")
        self.assertIn("389 lines an hour", c.headline)
        self.assertIn("real", c.detail)

    def test_a_worse_night_rate_says_the_opposite(self):
        worse = finding(
            "when_the_work_lands",
            {"n": 5, "lines_per_active_hour": 90.0},
            {"n": 7, "lines_per_active_hour": 300.0},
        )
        c = by_id(brag.cards(PROFILE, [worse]), "the_night_shift")
        self.assertIn("not free", c.detail)

    def test_a_narrow_gap_is_not_an_argument(self):
        flat = finding(
            "when_the_work_lands",
            {"n": 5, "lines_per_active_hour": 200.0},
            {"n": 7, "lines_per_active_hour": 180.0},
        )
        self.assertIsNone(by_id(brag.cards(PROFILE, [flat]), "the_night_shift"))


class Shape(unittest.TestCase):
    def test_every_headline_fits_on_a_card(self):
        for c in brag.cards(PROFILE, [SPIN, LOOP, NIGHT, TESTS, REWRITE]):
            self.assertLessEqual(len(c.headline), brag.HEADLINE_MAX, c.id)
            self.assertLessEqual(len(c.detail), brag.DETAIL_MAX, c.id)

    def test_no_card_uses_a_dash_as_punctuation(self):
        for c in brag.cards(PROFILE, [SPIN, LOOP, NIGHT, TESTS, REWRITE]):
            self.assertNotRegex(c.headline + c.detail, r"[—–―−]", c.id)

    def test_no_card_says_a_metric_name_out_loud(self):
        banned = ("steer_rate", "autonomy_score", "usd_per_commit", "lines_per_active_hour", "n=")
        for c in brag.cards(PROFILE, [SPIN, LOOP, NIGHT, TESTS, REWRITE]):
            for word in banned:
                self.assertNotIn(word, c.headline + c.detail, f"{c.id} says {word}")

    def test_every_card_keeps_its_working(self):
        for c in brag.cards(PROFILE, [SPIN, LOOP, NIGHT, TESTS, REWRITE]):
            self.assertTrue(c.numbers, c.id)

    def test_a_headline_is_first_person_because_the_author_posts_it(self):
        # "You test after 93%" is a report about somebody. A post is written BY them.
        for c in brag.cards(PROFILE, [SPIN, LOOP, NIGHT, TESTS, REWRITE]):
            self.assertNotIn("your ", c.headline.lower(), c.id)
            self.assertFalse(c.headline.lower().startswith("you "), c.id)

    def test_an_empty_corpus_produces_no_cards_rather_than_empty_ones(self):
        self.assertEqual(brag.cards({}, []), [])


if __name__ == "__main__":
    unittest.main()
