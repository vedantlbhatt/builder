"""The narrative: what the model is allowed to say, and what gets taken back off it.

The model is the only thing in the profile that writes prose, and prose about somebody's
own habits is the most believable place in this app for a wrong number to hide. So the
cases below are mostly about the check that fires AFTER the model has spoken.
"""

from __future__ import annotations

import logging
import unittest

from analysis import narrative as nr

PROFILE = {
    "archetype": {
        "name": "quality_guardian",
        "confidence": 0.62,
        "rule": "test_runs_per_hour above 3.0",
        "value": 5.75,
        "threshold": 3.0,
        "runners_up": [{"name": "velocity_machine", "score": 0.31}],
    },
    "totals": {"sessions": 16, "active_hours": 21.4},
    "metrics": {
        "test_runs_per_hour": {"value": 5.75, "unit": "runs per active hour", "n": 16, "basis": "x"},
        "commits_total": {"value": None, "unit": "commits", "n": 0, "reason": "overlapping_session_windows"},
    },
    "top_tools": [{"tool": "Bash", "calls": 1211, "share": 0.72}],
    "model_mix": [{"model": "fable", "share": 0.5}],
}


class BuildInput(unittest.TestCase):
    def test_a_refused_metric_arrives_with_its_reason_not_as_a_zero(self):
        src = nr.build_input(profile=PROFILE, findings=[])
        self.assertIn("commits_total: REFUSED, overlapping_session_windows", src)
        self.assertNotIn("commits_total: 0", src)

    def test_the_rule_that_chose_the_archetype_is_in_the_input(self):
        src = nr.build_input(profile=PROFILE, findings=[])
        self.assertIn("test_runs_per_hour above 3.0", src)
        self.assertIn("5.75", src)
        self.assertIn("threshold of 3.0", src)

    def test_an_archetype_that_was_refused_says_so(self):
        src = nr.build_input(
            profile={**PROFILE, "archetype": {"name": None, "reason": "no_rule_crossed"}}, findings=[]
        )
        self.assertIn("none: no_rule_crossed", src)

    def test_findings_carry_both_sides(self):
        f = type("F", (), {})()
        f.text, f.left, f.right = "You front-load.", {"mean_chars": 340}, {"mean_chars": 188}
        src = nr.build_input(profile=PROFILE, findings=[f])
        self.assertIn("You front-load.", src)
        self.assertIn("340", src)
        self.assertIn("188", src)


class NumbersIn(unittest.TestCase):
    def test_small_whole_numbers_are_ordinary_english_not_claims(self):
        self.assertEqual(nr.numbers_in("one of your 3 sessions, 12 prompts"), set())

    def test_anything_bigger_is_a_claim(self):
        self.assertEqual(nr.numbers_in("13 of 14 bursts"), {"13", "14"})

    def test_a_small_decimal_is_still_a_claim(self):
        # 0.311 is an autonomy score, not a count. Only whole numbers get the free pass.
        self.assertEqual(nr.numbers_in("autonomy 0.311"), {"0.311"})

    def test_the_same_number_written_two_ways_is_one_number(self):
        self.assertEqual(nr.numbers_in("5.75") | nr.numbers_in("5.750"), {"5.75"})

    def test_a_percentage_is_its_digits(self):
        self.assertEqual(nr.numbers_in("93% of your bursts"), {"93"})

    def test_a_thousands_separator_is_not_two_numbers(self):
        # MEASURED: the first narrative written from a real corpus said "72% of your
        # 1,211 tool calls", copied straight from an input that read `total_tool_calls:
        # 1211`, and the check deleted the sentence because it saw "1" and "211". This is
        # the regression test for a verifier that cried wolf on correct output.
        self.assertEqual(nr.numbers_in("72% of your 1,211 tool calls"), {"72", "1211"})
        self.assertEqual(nr.numbers_in("total_tool_calls: 1211"), {"1211"})

    def test_a_comma_in_a_list_is_still_a_comma(self):
        self.assertEqual(nr.numbers_in("over 30, 45 and 1,200 prompts"), {"30", "45", "1200"})

    def test_two_big_numbers_that_differ_late_are_two_numbers(self):
        # Formatting these through %g would round both to 1.23457e+06 and let an invented
        # token count pass as a real one.
        self.assertNotEqual(nr.numbers_in("1234567"), nr.numbers_in("1234568"))


class Verify(unittest.TestCase):
    SOURCE = "test_runs_per_hour: 5.75 runs per active hour, over n=16\n  Bash: 1211 calls, 72%"

    @staticmethod
    def doc(**kw):
        base = {
            "archetype_line": "You run tests 5.75 times an hour.",
            "how_you_work": ["Bash is 72% of your 1211 calls."],
            "strengths": [{"text": "You verify.", "evidence": "5.75 runs per hour."}],
            "watch_outs": [{"text": "You lean on Bash.", "evidence": "1211 calls, 72%."}],
            "one_experiment": "Try running tests 5.75 times next session.",
        }
        base.update(kw)
        return base

    def test_a_narrative_built_only_from_the_input_survives_intact(self):
        out, dropped = nr.verify(self.doc(), self.SOURCE)
        self.assertEqual(dropped, [])
        self.assertEqual(out["how_you_work"], ["Bash is 72% of your 1211 calls."])
        self.assertEqual(len(out["strengths"]), 1)

    def test_an_invented_number_takes_its_whole_sentence_with_it(self):
        out, dropped = nr.verify(
            self.doc(how_you_work=["Bash is 72% of your 1211 calls.", "You averaged 47 commits a week."]),
            self.SOURCE,
        )
        self.assertEqual(out["how_you_work"], ["Bash is 72% of your 1211 calls."])
        self.assertEqual(len(dropped), 1)
        self.assertIn("47", dropped[0])

    def test_an_invented_number_in_the_evidence_drops_the_strength_too(self):
        out, dropped = nr.verify(
            self.doc(strengths=[{"text": "You verify.", "evidence": "89 of 94 bursts tested."}]),
            self.SOURCE,
        )
        self.assertEqual(out["strengths"], [])
        self.assertEqual(len(dropped), 1)

    def test_an_invented_archetype_line_is_emptied_not_left_standing(self):
        out, dropped = nr.verify(self.doc(archetype_line="You run tests 9.4 times an hour."), self.SOURCE)
        self.assertEqual(out["archetype_line"], "")
        self.assertEqual(len(dropped), 1)

    def test_an_experiment_tied_to_a_number_nobody_measured_is_dropped(self):
        out, _ = nr.verify(self.doc(one_experiment="Cut your 340 character openers in half."), self.SOURCE)
        self.assertEqual(out["one_experiment"], "")

    def test_a_number_that_only_appears_inside_a_bigger_one_does_not_count(self):
        # "121" is a substring of "1211" but is not a number the input contains.
        out, dropped = nr.verify(self.doc(how_you_work=["You made 121 calls."]), self.SOURCE)
        self.assertEqual(out["how_you_work"], [])
        self.assertEqual(len(dropped), 1)

    def test_the_verdict_does_not_mutate_the_document_it_was_handed(self):
        original = self.doc(archetype_line="You run tests 9.4 times an hour.")
        nr.verify(original, self.SOURCE)
        self.assertEqual(original["archetype_line"], "You run tests 9.4 times an hour.")


class Schema(unittest.TestCase):
    def test_the_schema_loads_without_the_header_the_cli_rejects(self):
        schema = nr.load_schema()
        self.assertNotIn("$schema", schema)
        self.assertEqual(
            set(schema["required"]),
            {"archetype_line", "how_you_work", "strengths", "watch_outs", "one_experiment"},
        )

    def test_the_prompt_bans_the_two_things_that_would_make_this_worthless(self):
        text = nr.PROMPT_PATH.read_text()
        self.assertIn("NEVER INVENT A NUMBER", text)
        self.assertIn("EVERY CLAIM CARRIES ITS NUMBER", text)


class Write(unittest.TestCase):
    """`write` without the CLI: the model is stubbed, everything after it is real."""

    def run_write(self, doc, **kw):
        calls = {}

        def fake(system, user, schema, model):
            calls["user"] = user
            return dict(doc), {"model": "sonnet"}

        real = nr.rn.call_claude
        nr.rn.call_claude = fake
        try:
            return nr.write(profile=PROFILE, **kw), calls
        finally:
            nr.rn.call_claude = real

    def test_a_dash_is_rewritten_and_counted_rather_than_rejected(self):
        out, _ = self.run_write(
            {
                "archetype_line": "You run tests 5.75 times an hour — twice the bar.",
                "how_you_work": ["You use Bash."],
                "strengths": [],
                "watch_outs": [],
                "one_experiment": "Keep going.",
            }
        )
        self.assertNotIn("—", out["archetype_line"])
        self.assertEqual(out["dashes_rewritten"], 1)

    def test_a_dropped_claim_is_counted_and_logged_so_it_can_be_read(self):
        with self.assertLogs("analysis.narrative", level=logging.WARNING) as log:
            out, _ = self.run_write(
                {
                    "archetype_line": "",
                    "how_you_work": ["You shipped 4711 commits."],
                    "strengths": [],
                    "watch_outs": [],
                    "one_experiment": "",
                }
            )
        self.assertEqual(out["invented_numbers_dropped"], 1)
        self.assertEqual(out["how_you_work"], [])
        self.assertIn("4711", log.output[0])

    def test_the_version_is_stamped_so_a_stored_page_says_which_rules_made_it(self):
        out, _ = self.run_write(
            {
                "archetype_line": "",
                "how_you_work": [],
                "strengths": [],
                "watch_outs": [],
                "one_experiment": "",
            }
        )
        self.assertEqual(out["narrative_version"], nr.NARRATIVE_VERSION)
        self.assertEqual(out["model"], "sonnet")

    def test_the_model_is_handed_the_measurements_and_nothing_else(self):
        _, calls = self.run_write(
            {
                "archetype_line": "",
                "how_you_work": [],
                "strengths": [],
                "watch_outs": [],
                "one_experiment": "",
            }
        )
        self.assertIn("quality_guardian", calls["user"])
        self.assertIn("REFUSED", calls["user"])


if __name__ == "__main__":
    unittest.main()
