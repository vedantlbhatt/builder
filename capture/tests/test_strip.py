"""python3 -m unittest capture.tests.test_strip

The strip carries no text by construction; what can go wrong is the numbers. These pin
that the ordinals come from the spec, that the class channel agrees with the clock the
way the server checks it, and that a prompt paints its own notch.
"""

from __future__ import annotations

import json
import pathlib
import unittest
import zoneinfo

from capture import sessions, strip
from capture.discover import Transcript

ROOT = pathlib.Path(__file__).resolve().parents[2]
SPEC = json.loads((ROOT / "spec" / "strip.v1.json").read_text())
FIX = ROOT / "spec" / "fixtures" / "boundaries"
TZ = zoneinfo.ZoneInfo("America/New_York")


class Strip(unittest.TestCase):
    def test_ordinals_come_from_the_spec(self):
        self.assertEqual(strip.CLASSES, SPEC["classes"])
        self.assertEqual(strip.MARKS, SPEC["marks"])
        self.assertEqual(strip.COLUMNS, SPEC["columns"])
        self.assertEqual(strip.class_of("prompt"), SPEC["classes"]["prompting"])
        self.assertEqual(strip.class_of("interrupt"), SPEC["classes"]["prompting"])
        self.assertEqual(strip.class_of("human_edit"), SPEC["classes"]["human_edit"])
        self.assertEqual(strip.class_of("assistant"), SPEC["classes"]["agent"])
        self.assertEqual(strip.class_of("user_other"), SPEC["classes"]["agent"])

    def test_pack_layout(self):
        self.assertEqual(strip.pack(3, 3), 0b1111)
        self.assertEqual(strip.pack(1, 2), 0b1001)

    def test_prompt_paints_its_own_notch(self):
        t0 = 1_000_000.0
        recs = [
            {"ts": t0, "kind": "prompt"},
            {"ts": t0 + 5, "kind": "assistant"},
            {"ts": t0 + 1000, "kind": "assistant"},
        ]
        cols, marks = strip.build(recs, t0, t0 + 1024)
        self.assertEqual(len(cols), 1024)
        self.assertEqual(cols[0] & 0b11, SPEC["classes"]["prompting"])
        self.assertEqual(cols[6] & 0b11, SPEC["classes"]["agent"])
        # 5 s + 120 s of credit after the second record, then idle until +1000.
        self.assertEqual(cols[500] & 0b11, SPEC["classes"]["idle"])
        self.assertEqual(cols[1001] & 0b11, SPEC["classes"]["agent"])
        self.assertEqual(marks, [{"ms": 0, "k": SPEC["marks"]["prompt"]}])

    def test_weighted_argmax_keeps_the_human_visible(self):
        """A column with 1 s of prompting and 5 s of agent time is prompting (weight 6 vs 1)."""
        t0 = 0.0
        recs = [{"ts": t0, "kind": "prompt"}, {"ts": t0 + 1, "kind": "assistant"}]
        cols, _ = strip.build(recs, t0, t0 + 6 * 1024)  # 6 s per column
        self.assertEqual(cols[0] & 0b11, SPEC["classes"]["prompting"])

    def test_strip_agrees_with_active_on_every_fixture_session(self):
        for jsonl in sorted(FIX.glob("*.jsonl")):
            src = sessions.load_source(Transcript("fixture", jsonl))
            last = max(r["ts"] for r in src.records)
            for s in sessions.sessionize_sources([src], TZ, now=last + 1):
                with self.subTest(case=jsonl.stem, start=s.started_at):
                    cols, marks = strip.build(s.records, s.started_at, s.ended_at)
                    active = round(s.attended) + round(s.autonomous)
                    got = strip.non_idle_seconds(cols, s.ended_at - s.started_at)
                    self.assertLessEqual(
                        abs(got - active),
                        max(0.05 * active, 2 * (s.ended_at - s.started_at) / 1024),
                    )
                    self.assertLessEqual(len(marks), s.prompts)
                    self.assertFalse(any(b >> 4 for b in cols))


if __name__ == "__main__":
    unittest.main()
