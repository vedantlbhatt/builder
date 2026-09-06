"""The corpus profile: the numbers, the refusals to produce a number, and the facts.

Every case here is one a person could recompute by hand from the events in the test.
The refusals matter as much as the values: a metric whose sample is too small, or whose
input is structurally absent, must come back None with a reason rather than 0.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import unittest

from analysis import profile as pf
from analysis.digest import Ev

HOUR = 3600.0
#: 2026-09-01 09:00:00 UTC, a Tuesday. Every timestamp below is an offset from it.
T0 = dt.datetime(2026, 9, 1, 9, 0, tzinfo=dt.UTC).timestamp()


def ev(n, ts, kind, text="", tool=None, added=None):
    return Ev(n, ts, kind, text, tool=tool, added=added)


def session(
    events,
    *,
    session_id="s",
    start=T0,
    end=None,
    attended=HOUR,
    autonomous=0.0,
    tz=0,
    tokens=None,
):
    return pf.session_fact_from_events(
        session_id=session_id,
        events=events,
        started_at=start,
        ended_at=end if end is not None else start + attended + autonomous,
        attended_seconds=attended,
        autonomous_seconds=autonomous,
        tz_offset_minutes=tz,
        output_tokens_by_model=tokens or {},
    )


def prompt_session(texts, *, opener="assistant", **kw):
    """One session: each prompt answered by `opener` and then a tool call."""
    events, n, t = [], 0, 0.0
    for text in texts:
        events.append(ev(n, kw.get("start", T0) + t, "prompt", text))
        n, t = n + 1, t + 60
        events.append(
            ev(n, kw.get("start", T0) + t, opener, "ok", tool="Bash" if opener == "tool" else None)
        )
        n, t = n + 1, t + 60
        events.append(ev(n, kw.get("start", T0) + t, "tool", "ls", tool="Bash"))
        n, t = n + 1, t + 60
    return session(events, **kw)


class Corrective(unittest.TestCase):
    def test_the_two_hand_checked_false_positives_stay_unflagged(self):
        """MEASURED on the container corpus: bare `not` and bare `stop` flagged these two
        directives as corrections. Both markers were narrowed; both must stay clean."""
        self.assertEqual(
            pf.correction_markers("Yeah do all of that and keep working do not stop until tested"),
            [],
        )
        self.assertEqual(pf.correction_markers("status <100 words whats done and whats not"), [])

    def test_real_redirects_are_flagged(self):
        for text in (
            "It's in builder do you not see it",
            "Dude I mean literally everything. Go back to my original prompt",
            "why no railway deploy? what is cline?",
            "wtf it should work with just the app",
        ):
            self.assertTrue(pf.is_corrective(text), text)

    def test_a_marker_past_the_window_does_not_flag_a_long_brief(self):
        brief = " ".join(["build the uploader and keep going"] * 10) + " no"
        self.assertEqual(pf.correction_markers(brief), [])


class MetricsFromEvents(unittest.TestCase):
    def test_planning_ratio_counts_prose_first_against_tool_first(self):
        prose = prompt_session(["plan the sync"] * 6, opener="assistant", session_id="a")
        tools = prompt_session(["do it"] * 3, opener="tool", session_id="b", start=T0 + 4 * HOUR)
        p = pf.corpus_profile([prose, tools, session([], session_id="c", start=T0 + 9 * HOUR)])
        m = p["metrics"]["planning_ratio"]
        self.assertEqual((m["planning_prompts"], m["execution_prompts"]), (6, 3))
        self.assertEqual(m["value"], 2.0)

    def test_planning_ratio_is_none_when_nothing_went_straight_to_a_tool(self):
        """The suggested (assistant turns before the first EDIT) form is exactly this on
        the container corpus: a zero denominator, which is not a number."""
        p = pf.corpus_profile(
            [
                prompt_session(["a"] * 5, session_id="a"),
                prompt_session(["b"] * 5, session_id="b", start=T0 + 4 * HOUR),
                session([], session_id="c", start=T0 + 9 * HOUR),
            ]
        )
        m = p["metrics"]["planning_ratio"]
        self.assertIsNone(m["value"])
        self.assertIn("zero denominator", m["reason"])

    def test_steer_rate_counts_an_interrupt_and_its_redirect_once(self):
        events = [
            ev(0, T0, "prompt", "build the uploader"),
            ev(1, T0 + 10, "assistant", "on it"),
            ev(2, T0 + 20, "interrupt"),
            ev(3, T0 + 30, "prompt", "no, stop, do the other one"),  # corrective AND after
            ev(4, T0 + 40, "assistant", "ok"),
            ev(5, T0 + 50, "prompt", "why are we using swift"),  # corrective on its own
            ev(6, T0 + 60, "assistant", "ok"),
            ev(7, T0 + 70, "prompt", "keep going"),
            ev(8, T0 + 80, "assistant", "ok"),
            ev(9, T0 + 90, "prompt", "and push it"),
            ev(10, T0 + 100, "assistant", "ok"),
        ]
        p = pf.corpus_profile([session(events)])
        m = p["metrics"]["steer_rate"]
        self.assertEqual((m["interrupts"], m["corrective_prompts"]), (1, 1))
        self.assertEqual(m["value"], round(2 / 5, 3))

    def test_prompt_shape(self):
        texts = ["one two three"] * 3 + ["a much longer prompt " * 5] * 3
        p = pf.corpus_profile([prompt_session(texts)])
        self.assertEqual(p["metrics"]["short_prompt_share"]["value"], 0.5)
        self.assertIsNotNone(p["metrics"]["median_prompt_chars"]["value"])

    def test_iteration_depth_and_tool_totals(self):
        p = pf.corpus_profile([prompt_session(["a", "b", "c", "d", "e"])])
        self.assertEqual(p["totals"]["total_tool_calls"], 5)
        self.assertEqual(p["metrics"]["iteration_depth"]["value"], 1.0)

    def test_code_velocity_refuses_zero_rather_than_reporting_it(self):
        events = [ev(i, T0 + i * 60, "tool", "ls", tool="Bash") for i in range(10)]
        p = pf.corpus_profile([session(events, attended=2 * HOUR)])
        m = p["metrics"]["code_velocity"]
        self.assertIsNone(m["value"])
        self.assertIn("would read as", m["reason"])

    def test_code_velocity_is_lines_over_active_hours(self):
        events = [
            ev(i, T0 + i * 60, "tool", "cat > a.py <<'EOF'", tool="Bash", added=30)
            for i in range(10)
        ]
        p = pf.corpus_profile([session(events, attended=2 * HOUR)])
        self.assertEqual(p["metrics"]["code_velocity"]["value"], 150.0)

    def test_code_velocity_needs_something_behind_the_number(self):
        """MEASURED on the proof database: 33 attributed lines over 4.4 hours read as 7.6
        lines an hour, while the same seven sittings credit 2,300 lines when the
        transcripts are read. 7.6 is a wrong number, not a small one."""
        events = [ev(0, T0, "tool", "cat > a.py <<'EOF'", tool="Bash", added=33)]
        p = pf.corpus_profile([session(events, attended=2 * HOUR)])
        m = p["metrics"]["code_velocity"]
        self.assertIsNone(m["value"])
        self.assertIn("too little to read as a rate", m["reason"])

    def test_a_line_total_too_large_to_be_an_artefact_is_a_rate_even_uncounted(self):
        """The stored rows carry one line total and no tool names, so the writes behind it
        cannot be counted. 2,000 lines over 2 hours is still a rate; 33 is not."""
        big = pf.SessionFact(
            session_id="a",
            started_at=T0,
            ended_at=T0 + 2 * HOUR,
            active_seconds=2 * HOUR,
            attended_seconds=2 * HOUR,
            autonomous_seconds=0,
            lines_added_agent=2000,
            lines_basis=pf.LINES_UPLOADED,
            write_events=None,
        )
        small = dataclasses.replace(big, lines_added_agent=33)
        self.assertEqual(pf.corpus_profile([big])["metrics"]["code_velocity"]["value"], 1000.0)
        refused = pf.corpus_profile([small])["metrics"]["code_velocity"]
        self.assertIsNone(refused["value"])
        self.assertIn("cannot be counted here", refused["reason"])


class Clocks(unittest.TestCase):
    def test_night_share_splits_a_session_across_the_clock(self):
        """20:00 to 02:00 local: two evening hours, four night ones, so 4/6 of the active
        time is night even though the session started in the evening."""
        start = dt.datetime(2026, 9, 1, 20, 0, tzinfo=dt.UTC).timestamp()
        s = session([], start=start, end=start + 6 * HOUR, attended=6 * HOUR)
        p = pf.corpus_profile([s])
        self.assertAlmostEqual(p["metrics"]["night_share"]["value"], round(4 / 6, 3), places=2)

    def test_peak_hour_is_the_local_hour_holding_the_most_active_time(self):
        start = dt.datetime(2026, 9, 1, 14, 0, tzinfo=dt.UTC).timestamp()
        long_at_two = session([], session_id="a", start=start, end=start + HOUR, attended=HOUR)
        short_at_nine = session(
            [], session_id="b", start=start + 7 * HOUR, end=start + 7 * HOUR + 600, attended=600
        )
        p = pf.corpus_profile([long_at_two, short_at_nine])
        self.assertEqual(p["metrics"]["peak_hour"]["value"], 14)

    def test_the_local_day_starts_at_four_in_the_morning(self):
        two_am = dt.datetime(2026, 9, 2, 2, 0, tzinfo=dt.UTC).timestamp()
        self.assertEqual(pf.local_day(two_am, 0), dt.date(2026, 9, 1))
        five_am = dt.datetime(2026, 9, 2, 5, 0, tzinfo=dt.UTC).timestamp()
        self.assertEqual(pf.local_day(five_am, 0), dt.date(2026, 9, 2))

    def test_streak_counts_consecutive_local_days(self):
        days = [session([], session_id=str(i), start=T0 + i * 24 * HOUR) for i in (0, 1, 2, 5)]
        p = pf.corpus_profile(days)
        self.assertEqual(p["metrics"]["longest_streak_days"]["value"], 3)
        self.assertEqual(p["sample"]["days"], 4)

    def test_autonomy_score_is_the_second_clock_over_both(self):
        s = session([], attended=HOUR, autonomous=3 * HOUR)
        self.assertEqual(pf.corpus_profile([s])["metrics"]["autonomy_score"]["value"], 0.75)


class Refusals(unittest.TestCase):
    def test_a_small_sample_returns_none_with_a_reason(self):
        p = pf.corpus_profile([session([ev(0, T0, "prompt", "hi")], attended=60)])
        for key in ("avg_prompt_chars", "autonomy_score", "night_share", "code_velocity"):
            self.assertIsNone(p["metrics"][key]["value"], key)
            self.assertTrue(p["metrics"][key]["reason"], key)
        self.assertIn("avg_prompt_chars", p["sample"]["missing"])

    def test_no_sessions_at_all_is_empty_rather_than_an_error(self):
        p = pf.corpus_profile([])
        self.assertEqual(p["totals"]["total_sessions"], 0)
        self.assertIsNone(p["archetype"]["name"])
        self.assertFalse(p["sample"]["enough_sessions"])

    def test_tool_diversity_is_refused_on_an_allowlisted_tool_map(self):
        facts = [
            pf.SessionFact(
                session_id=str(i),
                started_at=T0 + i * HOUR,
                ended_at=T0 + (i + 1) * HOUR,
                active_seconds=HOUR,
                attended_seconds=HOUR,
                autonomous_seconds=0,
                tool_calls={"Bash": 20, "Read": 5},
                tool_basis=pf.TOOLS_ALLOWLIST,
            )
            for i in range(4)
        ]
        m = pf.corpus_profile(facts)["metrics"]["tool_diversity"]
        self.assertIsNone(m["value"])
        self.assertIn("allowlist", m["reason"])

    def test_a_model_with_no_output_tokens_is_not_in_the_mix(self):
        s = session([], tokens={"claude-opus-5": 100, "<synthetic>": 0})
        mix = pf.corpus_profile([s])["model_mix"]
        self.assertEqual([m["model"] for m in mix], ["claude-opus-5"])


class Archetype(unittest.TestCase):
    def _corpus(self, night: bool) -> list[pf.SessionFact]:
        hour = 22 if night else 10
        out = []
        for i in range(4):
            start = dt.datetime(2026, 9, 1 + i, hour, tzinfo=dt.UTC).timestamp()
            out.append(session([], session_id=str(i), start=start, attended=4 * HOUR))
        return out

    def test_night_owl_wins_on_its_threshold_and_names_its_runners_up(self):
        a = pf.corpus_profile(self._corpus(night=True))["archetype"]
        self.assertEqual(a["name"], "night_owl")
        self.assertEqual(a["metric"], "night_share")
        self.assertGreater(a["confidence"], 0)
        self.assertLessEqual(len(a["runners_up"]), 2)
        # Every rule reports its score, including the ones that could not be computed.
        self.assertEqual(len(a["scores"]), len(pf.ARCHETYPE_RULES))

    def test_no_rule_met_means_no_archetype(self):
        a = pf.corpus_profile(self._corpus(night=False))["archetype"]
        self.assertIsNone(a["name"])
        self.assertEqual(a["reason"], "no archetype rule met its threshold")

    def test_the_archetype_object_has_one_shape_whether_or_not_a_rule_won(self):
        won = pf.corpus_profile(self._corpus(night=True))["archetype"]
        lost = pf.corpus_profile(self._corpus(night=False))["archetype"]
        self.assertEqual(set(won), set(lost))
        self.assertIsNone(lost["metric"])

    def test_two_sessions_is_not_a_profile(self):
        a = pf.corpus_profile(self._corpus(night=True)[:2])["archetype"]
        self.assertIsNone(a["name"])
        self.assertIn("fewer than", a["reason"])


class Facts(unittest.TestCase):
    def _profile(self):
        events = [
            ev(0, T0, "prompt", "why are we using swift"),
            ev(1, T0 + 10, "assistant", "because"),
            ev(2, T0 + 20, "tool", "cat > a.py <<'EOF'", tool="Bash", added=500),
            ev(3, T0 + 30, "prompt", "no, do the other one"),
            ev(4, T0 + 40, "tool", "ls", tool="Bash"),
            ev(5, T0 + 50, "prompt", "keep going"),
            ev(6, T0 + 60, "assistant", "ok"),
            ev(7, T0 + 70, "prompt", "and push"),
            ev(8, T0 + 80, "assistant", "ok"),
            ev(9, T0 + 90, "prompt", "ship it"),
            ev(10, T0 + 100, "assistant", "ok"),
        ]
        facts = [
            session(events, session_id="a", attended=2 * HOUR, tokens={"claude-opus-5": 1000}),
            session([], session_id="b", start=T0 + 30 * HOUR, attended=HOUR, autonomous=HOUR),
            session([], session_id="c", start=T0 + 60 * HOUR, attended=HOUR),
        ]
        return pf.corpus_profile(facts)

    def test_no_user_facing_string_contains_a_dash_the_user_hates(self):
        """The rule is absolute: em dashes and en dashes never reach a person."""
        p = self._profile()
        strings = [f["text"] for f in p["facts"]]
        strings += [str(v.get("reason")) for v in p["metrics"].values()]
        strings.append(str(p["archetype"].get("reason")))
        for s in strings:
            self.assertNotIn("—", s)
            self.assertNotIn("–", s)

    def test_facts_are_ranked_by_distance_from_a_documented_baseline(self):
        p = self._profile()
        scores = [f["unusualness"] for f in p["facts"]]
        self.assertEqual(scores, sorted(scores, reverse=True))
        for f in p["facts"]:
            # One shape for every fact: `baseline` is a key even when it is null.
            self.assertEqual(
                {"id", "text", "value", "unit", "unusualness", "baseline"} - set(f), set()
            )

    def test_every_ranked_fact_names_a_number_in_its_sentence(self):
        for f in self._profile()["facts"]:
            self.assertTrue(any(ch.isdigit() for ch in f["text"]), f["text"])

    def test_the_model_default_fact_names_the_model(self):
        texts = [f["text"] for f in self._profile()["facts"]]
        self.assertTrue(any("You default to Opus" in t for t in texts), texts)


if __name__ == "__main__":
    unittest.main()


class CommitsCannotSimplyBeSummed(unittest.TestCase):
    """Two sessions running at once in one repo both count the commits in the overlap.

    MEASURED on this container, 2026-09-06: eleven Claude Code sessions summed to 98
    commits where `git log` over the same day counted 75. One session ran 05:45 to 05:57
    entirely inside another running 04:55 to 06:17; two more sat inside a third; and two
    23:0x sessions covered the same three commits. The per-session number is right, and it
    is the SUM that is the wrong number, which is exactly the failure this repo names first.
    """

    @staticmethod
    def _fact(session_id: str, start: float, end: float, commits: int, repo: str | None):
        return pf.SessionFact(
            session_id=session_id,
            started_at=start,
            ended_at=end,
            active_seconds=end - start,
            attended_seconds=end - start,
            autonomous_seconds=0.0,
            commit_count=commits,
            commit_basis=pf.COMMITS_GIT_LOG,
            repo=repo,
        )

    def test_disjoint_windows_in_one_repo_sum_exactly(self):
        day = 1_780_000_000.0
        far = day + 4 * pf.COMMIT_ATTRIBUTION_SEC
        p = pf.corpus_profile(
            [self._fact("a", day, day + 600, 5, "r1"), self._fact("b", far, far + 600, 4, "r1")]
        )
        self.assertEqual(p["totals"]["total_commits"], 9)
        self.assertEqual(p["totals"]["commit_basis"], pf.COMMITS_GIT_LOG)

    def test_an_overlap_in_one_repo_refuses_the_total(self):
        day = 1_780_000_000.0
        p = pf.corpus_profile(
            [
                self._fact("a", day, day + 4_800, 19, "r1"),
                # 05:45 inside 04:55 to 06:17, the real shape from the container.
                self._fact("b", day + 3_000, day + 3_700, 6, "r1"),
            ]
        )
        # None, never 0: a zero here reads as "you committed nothing".
        self.assertIsNone(p["totals"]["total_commits"])
        self.assertEqual(p["totals"]["commit_basis"], pf.COMMITS_OVERLAPPING)

    def test_the_attribution_lookback_counts_as_overlap(self):
        # `capture/sessions.py` asks git from `started_at - 1800`, so two sessions half an
        # hour apart in one repo are still both claiming the commits in between.
        day = 1_780_000_000.0
        p = pf.corpus_profile(
            [
                self._fact("a", day, day + 600, 3, "r1"),
                self._fact("b", day + 900, day + 1_500, 3, "r1"),
            ]
        )
        self.assertIsNone(p["totals"]["total_commits"])

    def test_overlapping_sessions_in_DIFFERENT_repos_still_sum(self):
        # Two repos cannot share a commit, so there is nothing to double count.
        day = 1_780_000_000.0
        p = pf.corpus_profile(
            [self._fact("a", day, day + 4_800, 19, "r1"), self._fact("b", day + 3_000, day + 3_700, 6, "r2")]
        )
        self.assertEqual(p["totals"]["total_commits"], 25)
