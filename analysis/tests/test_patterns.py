"""Findings that name a cost, and the bars that stop one being written.

Every case is small enough to check by hand. The refusals matter more than the values: a
"pattern" over three sessions against four is exactly the plausible wrong number this
codebase exists to refuse, so most of these tests assert that nothing was said.
"""

from __future__ import annotations

import datetime as dt
import unittest

from analysis import patterns as pt
from analysis.digest import Ev

#: 2026-09-01 09:00:00 UTC, a Tuesday morning: daylight in every timezone used here.
T0 = dt.datetime(2026, 9, 1, 9, 0, tzinfo=dt.UTC).timestamp()


def ev(n, ts, kind, text="", tool=None, added=None, path=None, ok=True):
    return Ev(n, ts, kind, text, tool=tool, added=added, path=path, ok=ok)


def write(n, ts, path="/repo/a.py", added=3):
    return ev(n, ts, "tool", "", tool="Edit", added=added, path=path)


def a_test(n, ts):
    return ev(n, ts, "tool", "pytest -q", tool="Bash")


def commit(n, ts):
    return ev(n, ts, "tool", "git commit -m x", tool="Bash")


def idle(n, ts, cmd="ls"):
    return ev(n, ts, "tool", cmd, tool="Bash")


def sess(events, *, sid="s", start=T0, active=3600.0, tz=0, tokens=None, usd=None, model=None):
    return pt.SessionEvents(
        session_id=sid,
        started_at=start,
        ended_at=start + active,
        active_seconds=active,
        attended_seconds=active,
        tz_offset_minutes=tz,
        events=events,
        output_tokens=tokens,
        cost_usd=usd,
        dominant_model=model,
    )


def by_id(found, fid):
    return next((f for f in found if f.id == fid), None)


class NoJargon(unittest.TestCase):
    """Rule 2 of the module: no word the reader would have to look up.

    Internal metric names belong in `left`/`right`, where a screen can show the working.
    A sentence that says "your steer rate is 0.433" has told the reader nothing, and this
    is the test that keeps that sentence out of the product.
    """

    BANNED = (
        "steer_rate",
        "steer rate",
        "autonomy_score",
        "autonomy score",
        "front-load",
        "front load",
        "planning_ratio",
        "short_prompt_share",
        "night_share",
        "lift",
        "MIN_GROUP",
    )

    def corpus(self):
        # Wide enough that several finders fire at once.
        out = []
        for i in range(8):
            out.append(
                sess(
                    [
                        ev(0, T0, "prompt", "please do the whole thing properly this time ok"),
                        idle(1, T0 + 10),
                        write(2, T0 + 20),
                        a_test(3, T0 + 30),
                        commit(4, T0 + 40),
                    ],
                    sid=f"s{i}",
                    start=T0 + i * 86400,
                    tokens=1000,
                )
            )
        for i in range(8):
            out.append(
                sess(
                    [ev(0, T0, "prompt", "fix it")] + [idle(j + 1, T0 + j * 10) for j in range(40)],
                    sid=f"q{i}",
                    start=T0 + i * 86400,
                    tokens=9000,
                )
            )
        return out

    def test_no_finding_says_a_word_from_the_codebase(self):
        found = pt.findings(self.corpus())
        self.assertGreater(len(found), 0, "the fixture must produce findings to be a test")
        for f in found:
            for word in self.BANNED:
                self.assertNotIn(word.lower(), f.text.lower(), f"{f.id} says {word!r}")

    def test_no_finding_uses_a_dash_as_punctuation(self):
        for f in pt.findings(self.corpus()):
            self.assertNotRegex(f.text, r"[—–―−]")

    def test_every_finding_keeps_its_working(self):
        for f in pt.findings(self.corpus()):
            self.assertIn("n", f.left)
            self.assertIn("group", f.left)
            self.assertIn("group", f.right)


class ShippingSessions(unittest.TestCase):
    """The opening message against whether the sitting ended with anything landing."""

    @staticmethod
    def one(text, shipped, i):
        events = [ev(0, T0, "prompt", text), write(1, T0 + 60)]
        if shipped:
            events.append(commit(2, T0 + 120))
        return sess(events, sid=f"s{i}", start=T0 + i * 86400)

    def corpus(self, long_shipped, short_shipped, n=6):
        out = []
        for i in range(n):
            out.append(self.one("x" * 400, long_shipped, i))
        for i in range(n):
            out.append(self.one("go", short_shipped, n + i))
        return out

    def test_a_real_gap_is_reported_with_both_counts(self):
        f = by_id(pt.findings(self.corpus(True, False)), "what_a_shipping_session_looks_like")
        self.assertIsNotNone(f)
        self.assertEqual(f.left["shipped"], 6)
        self.assertEqual(f.right["shipped"], 0)
        self.assertIn("6 sessions you opened with the most detail, 6 ended with a commit", f.text)

    def test_no_gap_says_nothing(self):
        found = pt.findings(self.corpus(True, True))
        self.assertIsNone(by_id(found, "what_a_shipping_session_looks_like"))

    def test_nine_sessions_is_refused_however_wide_the_gap(self):
        # Ten openers is the floor: five a side.
        corpus = self.corpus(True, False, n=6)[:9]
        self.assertIsNone(
            by_id(pt.findings(corpus), "what_a_shipping_session_looks_like")
        )

    def test_a_session_with_no_typed_prompt_is_not_counted_as_a_short_one(self):
        # A fully autonomous continuation has no opener at all. Filing it under "dived
        # straight in" would blame the person for a session they never opened.
        corpus = self.corpus(True, False) + [
            sess([write(0, T0)], sid=f"auto{i}") for i in range(6)
        ]
        f = by_id(pt.findings(corpus), "what_a_shipping_session_looks_like")
        self.assertEqual(f.left["n"] + f.right["n"], 12)


class TheSpin(unittest.TestCase):
    """The stretches with nothing to show, and the guard that refuses to guess at them."""

    def corpus(self, spin_len, spins=2, normal=8):
        out = []
        for i in range(spins):
            events = [idle(j, T0 + j * 30) for j in range(spin_len)]
            events.append(write(spin_len, T0 + spin_len * 30))
            out.append(sess(events, sid=f"spin{i}"))
        for i in range(normal):
            out.append(
                sess([idle(0, T0), idle(1, T0 + 10), write(2, T0 + 20)], sid=f"ok{i}")
            )
        return out

    def test_a_long_stretch_is_reported_with_what_it_cost(self):
        f = by_id(pt.findings(self.corpus(40)), "the_spin")
        self.assertIsNotNone(f)
        self.assertEqual(f.left["worst_tool_calls"], 40)
        self.assertEqual(f.right["median_tool_calls"], 2)
        self.assertIn("cost you", f.text)

    def test_a_stretch_under_the_bar_is_ordinary_work(self):
        # 20 calls is reading before editing, not spinning.
        self.assertIsNone(by_id(pt.findings(self.corpus(20)), "the_spin"))

    def test_a_test_run_counts_as_progress_not_as_nothing(self):
        # Otherwise a session that ran the suite forty times reads as forty wasted calls.
        events = [idle(j, T0 + j * 30) for j in range(20)]
        events.append(a_test(20, T0 + 600))
        events += [idle(20 + j, T0 + 700 + j * 30) for j in range(20)]
        corpus = [sess(events, sid=f"t{i}") for i in range(6)]
        self.assertIsNone(by_id(pt.findings(corpus), "the_spin"))

    def test_the_trailing_stretch_ends_at_the_last_call_not_the_session_end(self):
        """FOUND BY RUNNING IT: a sitting whose last 50 calls finished in 100 seconds and
        whose window ran two more hours reported a two hour stretch of going nowhere. The
        boundary rules extend `endedAt` by the trailing gap, which is right for active
        time and wrong for this."""
        quick = [idle(i, T0 + i * 2) for i in range(60)]
        # Enough ordinary sittings alongside to clear the checkpoint density guard: below
        # one checkpoint per twenty calls the finding refuses itself, correctly.
        corpus = [sess(quick, sid=f"q{i}", active=7200.0) for i in range(6)] + [
            sess([idle(0, T0), idle(1, T0 + 10), write(2, T0 + 20)], sid=f"ok{i}")
            for i in range(25)
        ]
        f = by_id(pt.findings(corpus), "the_spin")
        self.assertIsNotNone(f)
        # 60 calls two seconds apart is about two minutes, not two hours.
        self.assertLess(f.left["worst_seconds"], 300, "the idle tail was credited to the run")

    def test_a_corpus_where_no_progress_is_visible_is_refused_not_estimated(self):
        """The guard. A session whose edits went through a script the parser cannot read
        would otherwise be reported as one long stretch of wasted time."""
        blind = [sess([idle(j, T0 + j * 30) for j in range(60)], sid=f"b{i}") for i in range(8)]
        self.assertIsNone(by_id(pt.findings(blind), "the_spin"))


class StuckInALoop(unittest.TestCase):
    """Consecutive failures, where a failure is the error event AFTER the call."""

    @staticmethod
    def loop(n_fail, sid="s"):
        events, n, t = [], 0, T0
        for _ in range(n_fail):
            events.append(idle(n, t, "make test"))
            events.append(ev(n + 1, t + 5, "result_error", "boom", ok=False))
            n, t = n + 2, t + 30
        events.append(idle(n, t, "make test"))
        return sess(events, sid=sid)

    def test_a_run_of_failures_is_reported_with_its_length(self):
        f = by_id(pt.findings([self.loop(7), self.loop(5, "b")]), "stuck_in_a_loop")
        self.assertIsNotNone(f)
        self.assertEqual(f.left["longest_run"], 7)
        self.assertEqual(f.left["n"], 2)

    def test_three_failures_is_not_a_loop(self):
        self.assertIsNone(by_id(pt.findings([self.loop(3), self.loop(3, "b")]), "stuck_in_a_loop"))

    def test_a_success_between_failures_breaks_the_run(self):
        events, n, t = [], 0, T0
        for _ in range(6):
            events.append(idle(n, t, "make test"))
            events.append(ev(n + 1, t + 5, "result_error", "boom", ok=False))
            events.append(idle(n + 2, t + 10, "make test"))
            n, t = n + 3, t + 30
        self.assertIsNone(by_id(pt.findings([sess(events)]), "stuck_in_a_loop"))

    def test_a_loop_that_runs_to_the_end_of_the_session_still_counts(self):
        events, n, t = [], 0, T0
        for _ in range(6):
            events.append(idle(n, t, "make test"))
            events.append(ev(n + 1, t + 5, "result_error", "boom", ok=False))
            n, t = n + 2, t + 30
        f = by_id(pt.findings([sess(events)]), "stuck_in_a_loop")
        self.assertIsNotNone(f)
        self.assertEqual(f.left["longest_run"], 6)


class FightingOneFile(unittest.TestCase):
    @staticmethod
    def touch(n, path, times, start=T0):
        return [write(n + i, start + i * 60, path=path) for i in range(times)]

    def test_the_worst_file_is_named_with_its_count_and_its_time(self):
        events = self.touch(0, "/repo/parser.py", 7)
        for i, name in enumerate("abcde"):
            events += self.touch(20 + i, f"/repo/{name}.py", 1)
        f = by_id(pt.findings([sess(events)]), "fighting_one_file")
        self.assertIsNotNone(f)
        self.assertIn("parser.py", f.text)
        self.assertIn("7 times", f.text)
        self.assertEqual(f.left["count"], 1)
        self.assertEqual(f.right["count"], 5)

    def test_three_passes_is_not_a_fight(self):
        events = []
        for i, name in enumerate("abcdef"):
            events += self.touch(i * 10, f"/repo/{name}.py", 3)
        self.assertIsNone(by_id(pt.findings([sess(events)]), "fighting_one_file"))

    def test_a_read_is_not_a_write(self):
        events = [
            ev(i, T0 + i * 60, "tool", "", tool="Read", added=None, path="/repo/parser.py")
            for i in range(9)
        ]
        self.assertIsNone(by_id(pt.findings([sess(events)]), "fighting_one_file"))


class WhenTheWorkLands(unittest.TestCase):
    @staticmethod
    def corpus(late_lines, day_lines, n=5):
        night_start = T0 + 14 * 3600  # 23:00 UTC
        out = []
        for i in range(n):
            out.append(
                sess(
                    [write(0, night_start, added=late_lines)],
                    sid=f"n{i}",
                    start=night_start + i * 86400,
                )
            )
            out.append(sess([write(0, T0, added=day_lines)], sid=f"d{i}", start=T0 + i * 86400))
        return out

    def test_a_worse_late_rate_is_reported_as_a_cost(self):
        f = by_id(pt.findings(self.corpus(40, 200)), "when_the_work_lands")
        self.assertIsNotNone(f)
        self.assertIn("expensive", f.text)
        self.assertEqual(f.left["lines_per_active_hour"], 40.0)

    def test_a_better_late_rate_is_reported_as_a_strength(self):
        f = by_id(pt.findings(self.corpus(200, 40)), "when_the_work_lands")
        self.assertIn("best", f.text)

    def test_four_late_sessions_is_refused(self):
        self.assertIsNone(by_id(pt.findings(self.corpus(40, 200, n=4)), "when_the_work_lands"))

    def test_a_short_sitting_cannot_swing_the_rate(self):
        # Five minutes with one write is 12 lines an hour or 1,200, depending on rounding
        # nobody should trust. Sessions under five minutes are dropped.
        corpus = self.corpus(40, 200) + [
            sess([write(0, T0, added=500)], sid=f"blip{i}", start=T0 + i * 86400, active=60.0)
            for i in range(5)
        ]
        f = by_id(pt.findings(corpus), "when_the_work_lands")
        self.assertEqual(f.right["n"], 5)


class ShortPrompts(unittest.TestCase):
    """One prompt per session: a correction written as another prompt would land in one of
    the two groups being compared and move the number under test."""

    SHORT = "fix it"
    LONG = "please refactor the parser so that it reads the trailing line safely and fast"

    def build(self, shorts, longs):
        out = []
        for group, text in ((shorts, self.SHORT), (longs, self.LONG)):
            for i, corrected in enumerate(group):
                events = [ev(0, T0, "prompt", text), idle(1, T0 + 60)]
                if corrected:
                    events.append(ev(2, T0 + 120, "interrupt"))
                out.append(sess(events, sid=f"{text[:5]}{i}"))
        return out

    def test_a_wide_gap_is_reported_with_both_counts(self):
        f = by_id(
            pt.findings(self.build([True] * 4 + [False], [False] * 5)),
            "short_prompts_get_corrected",
        )
        self.assertIsNotNone(f)
        self.assertEqual(f.left["corrected"], 4)
        self.assertIn("round trip", f.text)

    def test_four_against_four_is_refused_however_wide_the_gap(self):
        self.assertIsNone(
            by_id(pt.findings(self.build([True] * 4, [False] * 4)), "short_prompts_get_corrected")
        )

    def test_a_narrow_gap_over_a_big_sample_is_refused(self):
        found = pt.findings(self.build([True] * 2 + [False] * 8, [True] + [False] * 9))
        self.assertIsNone(by_id(found, "short_prompts_get_corrected"))


class VerificationHabit(unittest.TestCase):
    @staticmethod
    def corpus(tested, untested):
        out = [
            sess([write(0, T0), a_test(1, T0 + 10)], sid=f"t{i}") for i in range(tested)
        ]
        out += [sess([write(0, T0)], sid=f"u{i}") for i in range(untested)]
        return out

    def test_a_disciplined_corpus_is_told_so(self):
        f = by_id(pt.findings(self.corpus(6, 0)), "verification_habit")
        self.assertIsNotNone(f)
        self.assertIn("100%", f.text)
        self.assertIn("strongest thing", f.text)

    def test_an_undisciplined_corpus_is_told_what_it_costs(self):
        f = by_id(pt.findings(self.corpus(1, 5)), "verification_habit")
        self.assertIn("rework", f.text)
        self.assertEqual(f.left["count"], 1)

    def test_four_bursts_is_refused(self):
        self.assertIsNone(by_id(pt.findings(self.corpus(4, 0)), "verification_habit"))


class QuietSessionsCost(unittest.TestCase):
    @staticmethod
    def corpus(quiet_usd, shipped_usd, n=5, priced=True):
        out = []
        for i in range(n):
            out.append(sess([idle(0, T0)], sid=f"q{i}", usd=quiet_usd if priced else None))
            out.append(
                sess(
                    [write(0, T0), commit(1, T0 + 60)],
                    sid=f"s{i}",
                    usd=shipped_usd if priced else None,
                )
            )
        return out

    def test_the_bill_for_sessions_that_shipped_nothing(self):
        f = by_id(pt.findings(self.corpus(8.0, 2.0)), "what_the_quiet_sessions_cost")
        self.assertIsNotNone(f)
        self.assertEqual(f.left["usd"], 40.0)
        self.assertIn("$40.00", f.text)
        self.assertIn("80%", f.text)

    def test_it_is_dollars_and_not_tokens(self):
        """MEASURED: on the reference corpus the sittings that shipped nothing were 23%
        of the output TOKENS and 1% of the money, because the quiet ones were cheap
        Sonnet sittings. The token version pointed at waste that was not there."""
        f = by_id(pt.findings(self.corpus(8.0, 2.0)), "what_the_quiet_sessions_cost")
        self.assertNotIn("token", f.text.lower())
        self.assertIn("$", f.text)

    def test_an_unpriced_corpus_is_refused_not_zeroed(self):
        found = pt.findings(self.corpus(8.0, 2.0, priced=False))
        self.assertIsNone(by_id(found, "what_the_quiet_sessions_cost"))

    def test_a_trivial_share_is_not_worth_a_sentence(self):
        self.assertIsNone(
            by_id(pt.findings(self.corpus(0.1, 9.9)), "what_the_quiet_sessions_cost")
        )

    def test_four_quiet_sessions_is_refused(self):
        self.assertIsNone(
            by_id(pt.findings(self.corpus(8.0, 2.0, n=4)), "what_the_quiet_sessions_cost")
        )


class CheaperModelShipped(unittest.TestCase):
    """The one comparison a stranger can use, which is why it has to be exactly right."""

    @staticmethod
    def corpus(cheap_usd, dear_usd, commits=2, n=5, cheap="claude-sonnet-5", dear="claude-opus-5"):
        out = []
        for i in range(n):
            out.append(
                sess(
                    [write(0, T0)] + [commit(j + 1, T0 + 60 * j) for j in range(commits)],
                    sid=f"c{i}",
                    usd=cheap_usd,
                    model=cheap,
                )
            )
            out.append(
                sess(
                    [write(0, T0)] + [commit(j + 1, T0 + 60 * j) for j in range(commits)],
                    sid=f"d{i}",
                    usd=dear_usd,
                    model=dear,
                )
            )
        return out

    def test_the_two_models_are_named_with_their_rates(self):
        f = by_id(pt.findings(self.corpus(1.0, 20.0)), "the_cheaper_model_shipped")
        self.assertIsNotNone(f)
        self.assertIn("Sonnet 5", f.text)
        self.assertIn("Opus 5", f.text)
        self.assertEqual(f.left["usd_per_commit"], 0.5)
        self.assertEqual(f.right["usd_per_commit"], 10.0)
        self.assertEqual(f.lift, 20.0)

    def test_two_models_that_cost_about_the_same_is_not_a_finding(self):
        self.assertIsNone(by_id(pt.findings(self.corpus(1.0, 1.2)), "the_cheaper_model_shipped"))

    def test_one_model_alone_has_nothing_to_compare_against(self):
        one = self.corpus(1.0, 20.0, dear="claude-sonnet-5")
        self.assertIsNone(by_id(pt.findings(one), "the_cheaper_model_shipped"))

    def test_four_sittings_a_side_is_refused(self):
        self.assertIsNone(
            by_id(pt.findings(self.corpus(1.0, 20.0, n=4)), "the_cheaper_model_shipped")
        )

    def test_a_mixed_sitting_belongs_to_no_model(self):
        """A session's commits belong to the session. Splitting them by output share
        would hand a commit to whichever model wrote the test log."""
        mixed = [
            sess([write(0, T0), commit(1, T0 + 60)], sid=f"m{i}", usd=99.0, model=None)
            for i in range(5)
        ]
        f = by_id(pt.findings(self.corpus(1.0, 20.0) + mixed), "the_cheaper_model_shipped")
        self.assertEqual(f.left["n"], 5, "the mixed sittings joined neither side")
        self.assertEqual(f.right["n"], 5)


class Empty(unittest.TestCase):
    def test_no_sessions_says_nothing_rather_than_zero(self):
        self.assertEqual(pt.findings([]), [])

    def test_one_session_says_nothing(self):
        self.assertEqual(pt.findings([sess([ev(0, T0, "prompt", "hello there")])]), [])


if __name__ == "__main__":
    unittest.main()
