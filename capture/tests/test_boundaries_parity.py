"""python3 -m unittest capture.tests.test_boundaries_parity

For every case under spec/fixtures/boundaries the payload capture would send must carry
the fixture's attended / autonomous / presence / end_reason, and `unattended` must be the
server's derivation of them. The fixtures are what `scripts/measure_boundaries.py`
produces and what the Swift engine is held to, so agreement here is agreement with both.
"""

from __future__ import annotations

import json
import pathlib
import unittest
import zoneinfo

from capture import sessions
from capture.discover import Transcript
from capture.tuning import NOTABLE_MIN_ACTIVE_SEC

ROOT = pathlib.Path(__file__).resolve().parents[2]
FIX = ROOT / "spec" / "fixtures" / "boundaries"
TZ = zoneinfo.ZoneInfo("America/New_York")
MACHINE = "0" * 64


def _cases():
    for exp in sorted(FIX.glob("*.expected.json")):
        name = exp.name[: -len(".expected.json")]
        yield name, FIX / f"{name}.jsonl", json.loads(exp.read_text())


class BoundaryParity(unittest.TestCase):
    def test_fixtures_exist(self):
        self.assertGreaterEqual(len(list(_cases())), 9)

    def test_every_case_matches_the_reference(self):
        for name, jsonl, expected in _cases():
            with self.subTest(case=name):
                src = sessions.load_source(Transcript(project_dir="fixture", path=jsonl))
                self.assertEqual(len(src.records), expected["records"])
                last_ts = max(r["ts"] for r in src.records)
                # `now` just after the last record: the open session stays open, exactly
                # as the reference reports it (`still_running`, no trailing credit).
                cut = sessions.sessionize_sources([src], TZ, now=last_ts + 1)
                self.assertEqual(len(cut), len(expected["sessions"]))
                for got, want in zip(cut, expected["sessions"]):
                    p = sessions.build_payload(got, TZ, MACHINE, "test", observed_at=last_ts)
                    self.assertEqual(p["end_reason"], want["end_reason"])
                    self.assertEqual(p["attended_seconds"], round(want["attended_seconds"]))
                    self.assertEqual(p["autonomous_seconds"], round(want["autonomous_seconds"]))
                    self.assertEqual(
                        p["active_seconds"],
                        round(want["attended_seconds"]) + round(want["autonomous_seconds"]),
                    )
                    self.assertEqual(p["presence_count"], want["presence"])
                    self.assertEqual(p["human_prompt_count"], want["prompts"])
                    self.assertEqual(
                        p["unattended"],
                        want["presence"] == 0 and p["active_seconds"] >= NOTABLE_MIN_ACTIVE_SEC,
                    )
                    self.assertAlmostEqual(got.started_at, want["started_at"], places=2)
                    self.assertAlmostEqual(got.ended_at, want["ended_at"], places=2)
                    self.assertEqual(
                        p["state"], "live" if want["end_reason"] == "still_running" else "final"
                    )

    def test_remote_prompts_are_presence(self):
        """The remote fixture: 3 sdk/human prompts and 1 interrupt are 4 presence signals;
        the isMeta injection is neither a prompt nor presence."""
        _, jsonl, _ = next(c for c in _cases() if c[0] == "remote_sdk_prompts")
        src = sessions.load_source(Transcript("fixture", jsonl))
        cut = sessions.sessionize_sources([src], TZ, now=max(r["ts"] for r in src.records) + 1)
        self.assertEqual((cut[0].prompts, cut[0].presence), (3, 4))

    def test_open_session_older_than_tau_is_finalized_with_the_idle_gap_credit(self):
        """A still_running session nobody observed the gap of: ended on an idle gap, the
        boundary gap credited (capped at 120 s) to the clock that was running."""
        _, jsonl, expected = next(c for c in _cases() if c[0] == "lunch_autonomy_no_split")
        src = sessions.load_source(Transcript("fixture", jsonl))
        last = max(r["ts"] for r in src.records)
        want = expected["sessions"][-1]
        cut = sessions.sessionize_sources([src], TZ, now=last + 3600)
        s = cut[-1]
        self.assertEqual((s.state, s.end_reason), ("final", "idle_gap"))
        self.assertAlmostEqual(s.ended_at, last + 120, places=3)
        # The last record was 3 s after a prompt, so the human was present: attended.
        self.assertAlmostEqual(s.attended, want["attended_seconds"] + 120, places=3)
        self.assertAlmostEqual(s.autonomous, want["autonomous_seconds"], places=3)

    def test_finalize_flag_credits_only_the_gap_so_far(self):
        _, jsonl, expected = next(c for c in _cases() if c[0] == "robot_thirty_hours")
        src = sessions.load_source(Transcript("fixture", jsonl))
        last = max(r["ts"] for r in src.records)
        want = expected["sessions"][-1]
        s = sessions.sessionize_sources([src], TZ, now=last + 30, finalize_open=True)[-1]
        self.assertEqual((s.state, s.end_reason), ("final", "idle_gap"))
        self.assertAlmostEqual(s.ended_at, last + 30, places=3)
        # A robot: the credit lands on the autonomous clock.
        self.assertAlmostEqual(s.autonomous, want["autonomous_seconds"] + 30, places=3)
        self.assertEqual(s.attended, want["attended_seconds"])


if __name__ == "__main__":
    unittest.main()
