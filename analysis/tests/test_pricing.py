"""What a session would cost at list prices, and every refusal to say.

The number this module produces is the one most likely to be quoted out loud, so the
cases below are mostly about what it must NOT do: price a model nobody has published a
rate for, treat an unreported token count as zero, or bill a cache read at the input rate.
"""

from __future__ import annotations

import datetime as dt
import unittest

from analysis import pricing as pr

M = 1_000_000


class TheTable(unittest.TestCase):
    def test_every_price_is_a_real_positive_rate(self):
        for model, p in pr.PRICES.items():
            self.assertGreater(p.input, 0, model)
            self.assertGreater(p.output, p.input, f"{model}: output must cost more than input")
            self.assertLess(p.cache_read, p.input, f"{model}: a cache read must be cheaper")

    def test_a_cache_read_is_a_tenth_of_input_except_where_it_is_not(self):
        # 0.1x everywhere, and 0.025x on Fable 5.1 ($0.25/MTok). Stated per model rather
        # than as a multiplier for exactly this reason.
        for model, p in pr.PRICES.items():
            if model in ("claude-fable-5-1", "claude-mythos-5-1"):
                self.assertAlmostEqual(p.cache_read, 0.25, msg=model)
            else:
                self.assertAlmostEqual(p.cache_read, p.input * 0.1, places=6, msg=model)

    def test_a_cache_write_costs_more_than_the_input_it_saves(self):
        p = pr.PRICES["claude-opus-5"]
        self.assertAlmostEqual(p.cache_write_5m, p.input * 1.25)
        self.assertAlmostEqual(p.cache_write_1h, p.input * 2.0)
        self.assertGreater(p.cache_write_1h, p.cache_write_5m)

    def test_the_table_says_when_it_was_read(self):
        self.assertIsInstance(pr.PRICES_READ_ON, dt.date)
        self.assertIn("anthropic", pr.PRICES_SOURCE.lower())

    def test_a_table_nobody_has_checked_in_half_a_year_says_so(self):
        old = pr.PRICES_READ_ON + dt.timedelta(days=pr.PRICE_STALE_DAYS + 1)
        self.assertTrue(pr.prices_are_stale(old))
        self.assertFalse(pr.prices_are_stale(pr.PRICES_READ_ON + dt.timedelta(days=1)))


class Lookup(unittest.TestCase):
    def test_a_context_suffix_does_not_change_the_price(self):
        # The wire preserves `[1m]` verbatim because it says which window was in play.
        self.assertEqual(pr.normalize("claude-opus-5[1m]"), "claude-opus-5")
        self.assertIsNotNone(pr.price_for("claude-opus-5[1m]"))
        self.assertEqual(pr.price_for("CLAUDE-OPUS-5"), pr.price_for("claude-opus-5"))

    def test_an_unknown_model_is_refused_never_defaulted(self):
        self.assertIsNone(pr.price_for("gpt-4"))
        self.assertIsNone(pr.cost_usd(pr.Tokens(input=M), "gpt-4"))

    def test_the_family_name_is_the_longest_match_not_the_first(self):
        self.assertEqual(pr.family("claude-opus-4-8"), "Opus 4.8")
        self.assertEqual(pr.family("claude-opus-5"), "Opus 5")
        self.assertEqual(pr.family("claude-fable-5-1"), "Fable 5.1")
        self.assertEqual(pr.family("claude-fable-5"), "Fable 5")

    def test_an_unknown_model_keeps_its_raw_id_rather_than_being_labelled_wrong(self):
        self.assertEqual(pr.family("some-other-model"), "some-other-model")


class Cost(unittest.TestCase):
    def test_one_million_of_each_bucket_is_the_table_read_back(self):
        p = pr.PRICES["claude-sonnet-5"]
        for bucket, rate in (
            ("input", p.input),
            ("output", p.output),
            ("cache_read", p.cache_read),
            ("cache_w5m", p.cache_write_5m),
            ("cache_w1h", p.cache_write_1h),
        ):
            got = pr.cost_usd(pr.Tokens(**{bucket: M}), "claude-sonnet-5")
            self.assertAlmostEqual(got, rate, places=6, msg=bucket)

    def test_a_cache_read_is_not_billed_at_the_input_rate(self):
        """The reason there is no `total` bucket. Adding the five up and multiplying by
        one price overcharges cache-heavy work by a factor that grows with the hit rate."""
        heavy = pr.Tokens(input=10_000, cache_read=2 * M)
        naive = (10_000 + 2 * M) * pr.PRICES["claude-opus-5"].input / M
        real = pr.cost_usd(heavy, "claude-opus-5")
        self.assertLess(real, naive / 5)

    def test_no_tokens_is_a_refusal_not_a_free_session(self):
        # Cursor writes {0, 0} on all 14,565 of its message rows. Absent, not zero.
        usd, basis = pr.session_cost(pr.Tokens(), {"claude-opus-5": 1.0})
        self.assertIsNone(usd)
        self.assertEqual(basis, pr.BASIS_TOKENS_ABSENT)

    def test_no_model_is_a_refusal(self):
        usd, basis = pr.session_cost(pr.Tokens(output=M), {})
        self.assertIsNone(usd)
        self.assertEqual(basis, pr.BASIS_UNKNOWN_MODEL)

    def test_one_unknown_model_refuses_the_whole_session(self):
        """A total missing an unknown amount reads as complete and is not."""
        usd, basis = pr.session_cost(
            pr.Tokens(output=M), {"claude-opus-5": 0.9, "mystery-model": 0.1}
        )
        self.assertIsNone(usd)
        self.assertEqual(basis, pr.BASIS_UNKNOWN_MODEL)

    def test_a_single_model_session_is_exact(self):
        usd, basis = pr.session_cost(pr.Tokens(output=M), {"claude-opus-5": 1.0})
        self.assertAlmostEqual(usd, 25.0)
        self.assertEqual(basis, pr.BASIS_LIST_PRICE)

    def test_a_split_session_prices_each_half_at_its_own_rate(self):
        # Half of a million output tokens on each: 0.5M x $25 + 0.5M x $10.
        usd, _ = pr.session_cost(
            pr.Tokens(output=M), {"claude-opus-5": 0.5, "claude-sonnet-5": 0.5}
        )
        self.assertAlmostEqual(usd, 12.5 + 5.0, places=4)

    def test_shares_that_do_not_sum_to_one_are_normalised(self):
        # The wire rounds `output_token_share` to four places, so three models can sum to
        # 0.9999. Renormalising is right; treating the remainder as unbilled is not.
        a, _ = pr.session_cost(pr.Tokens(output=M), {"claude-opus-5": 0.9999})
        b, _ = pr.session_cost(pr.Tokens(output=M), {"claude-opus-5": 1.0})
        self.assertAlmostEqual(a, b, places=4)


class SplitByModel(unittest.TestCase):
    def test_input_follows_the_output_split_because_nothing_else_is_measured(self):
        parts = pr.split_by_model(
            pr.Tokens(input=1000, output=500, cache_read=2000),
            {"claude-opus-5": 0.75, "claude-sonnet-5": 0.25},
        )
        self.assertEqual(parts["claude-opus-5"].input, 750)
        self.assertEqual(parts["claude-sonnet-5"].cache_read, 500)

    def test_the_split_conserves_the_tokens_it_was_given(self):
        parts = pr.split_by_model(
            pr.Tokens(input=1001, output=999), {"a": 0.5, "b": 0.5}
        )
        self.assertEqual(sum(p.output for p in parts.values()), 1000)  # rounding, not loss


class Money(unittest.TestCase):
    def test_small_change_keeps_its_cents(self):
        self.assertEqual(pr.money(0.4213), "$0.42")

    def test_a_real_number_keeps_two_places(self):
        self.assertEqual(pr.money(12.3), "$12.30")

    def test_a_big_number_drops_the_cents_and_keeps_the_comma(self):
        self.assertEqual(pr.money(1204.7), "$1,205")


if __name__ == "__main__":
    unittest.main()
