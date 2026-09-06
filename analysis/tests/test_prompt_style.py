"""The style rules the user actually cares about, held by a test rather than by memory.

Two of them, both about the same failure: a model writes em dashes constantly, and a
"punchy" reading turns back into an essay the moment nobody is checking.

1. The analyst prompt must SAY the rule, in the shared resource the Swift agent embeds
   and in the Python fallback, and it must obey the rule itself.
2. `analysis/run.py` must ENFORCE it on the way out, because a prompt is a request. The
   enforcement is a rewrite rather than a rejection: the call costs minutes and money,
   and a session with no analysis is worse than one with a comma where a dash was.
"""

from __future__ import annotations

import json
import pathlib
import unittest

from analysis import prompt as pr
from analysis import run as rn

DASHES = ("—", "–", "―", "−")
SPEC = json.loads(
    (pathlib.Path(__file__).resolve().parents[2] / "spec/analysis.v1.json").read_text()
)
SCHEMA = json.loads((pathlib.Path(__file__).resolve().parents[1] / "schema.json").read_text())


class Prompt(unittest.TestCase):
    def test_the_prompt_states_the_dash_rule(self):
        self.assertIn("NEVER use an em dash or an en dash", pr.SYSTEM)
        self.assertIn("comma, a\n  full stop or a colon", pr.SYSTEM)

    def test_the_prompt_obeys_its_own_rule(self):
        for d in DASHES:
            self.assertNotIn(d, pr.SYSTEM)

    def test_the_shared_resource_and_the_fallback_are_the_same_text(self):
        """The Swift agent embeds the resource; Python reads it and falls back to the
        literal. Two readers, one text, byte for byte."""
        self.assertEqual(pr.RESOURCE.read_text(encoding="utf-8").strip(), pr._FALLBACK.strip())

    def test_the_prompt_asks_for_the_short_shape(self):
        self.assertIn("AT MOST TWO SENTENCES", pr.SYSTEM)
        self.assertIn("at most 70 characters", pr.SYSTEM)
        self.assertIn("up to 3 lines", pr.SYSTEM)

    def test_the_prompt_no_longer_asks_for_the_blocks_the_schema_dropped(self):
        for gone in ("work_mix", "PIVOTS", "FRICTION", "features:"):
            self.assertNotIn(gone, pr.SYSTEM)


class Schema(unittest.TestCase):
    def test_the_generated_schema_carries_the_short_shape(self):
        self.assertEqual(SCHEMA["properties"]["headline"]["maxLength"], 70)
        self.assertLessEqual(SCHEMA["properties"]["summary"]["maxLength"], 260)
        self.assertEqual(SCHEMA["properties"]["highlights"]["maxItems"], 3)
        for gone in ("features", "work_mix", "pivots", "friction"):
            self.assertNotIn(gone, SCHEMA["properties"])

    def test_the_spec_says_the_dash_rule_where_the_model_will_read_it(self):
        docs = " ".join(f.get("doc", "") for f in SPEC["fields"])
        self.assertIn("No dashes", docs)


class Dedash(unittest.TestCase):
    def test_a_clause_dash_becomes_a_comma(self):
        text = (
            "The agent gave the wrong repo status twice — reporting an empty scaffold "
            "— until the user forced a check"
        )
        out, n = rn.dedash({"summary": text})
        self.assertEqual(n, 2)
        self.assertEqual(
            out["summary"],
            "The agent gave the wrong repo status twice, reporting an empty scaffold, "
            "until the user forced a check",
        )

    def test_a_range_between_digits_becomes_the_word_to(self):
        out, n = rn.dedash({"highlights": ["ran 5–10 tests"]})
        self.assertEqual((out["highlights"][0], n), ("ran 5 to 10 tests", 1))

    def test_every_string_in_the_document_is_covered(self):
        doc = {
            "headline": "shipped the uploader — finally",
            "growth_edge": ["state the test up front — not after"],
            "build_style": {"architecture_note": "one module — two readers"},
            "dimensions": [{"rationale": "clear asks — few retries", "score": 70}],
        }
        out, n = rn.dedash(doc)
        self.assertEqual(n, 4)
        blob = json.dumps(out)
        for d in DASHES:
            self.assertNotIn(d, blob)
        self.assertEqual(out["dimensions"][0]["score"], 70)

    def test_a_prompt_excerpt_keeps_the_users_own_dash(self):
        """An excerpt is verbatim from a prompt. Rewriting it would break the check that
        makes it worth showing, and it would put words in the user's mouth."""
        doc = {"decision_patterns": [{"prompt_excerpt": "no — the other one"}]}
        out, n = rn.dedash(doc)
        self.assertEqual(n, 0)
        self.assertEqual(out["decision_patterns"][0]["prompt_excerpt"], "no — the other one")


if __name__ == "__main__":
    unittest.main()
