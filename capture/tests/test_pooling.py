"""python3 -m unittest capture.tests.test_pooling

Boundaries v3 through capture: pooling by transcript lineage (not per-record cwd), the
fitted idle-gap threshold, and the two structural ends. Each case is a fixture the
reference produced (scripts/gen_boundary_fixtures.py); the shape of the first one was
measured on a real transcript in this workspace, where per-record pooling turned one
sitting into two overlapping uploads.
"""

from __future__ import annotations

import json
import pathlib
import tempfile
import unittest
import zoneinfo
from unittest import mock

from capture import sessions
from capture.discover import Transcript
from capture.repo import RepoIdentity

ROOT = pathlib.Path(__file__).resolve().parents[2]
FIX = ROOT / "spec" / "fixtures" / "boundaries"
TZ = zoneinfo.ZoneInfo("America/New_York")
MACHINE = "0" * 64
PROJ = RepoIdentity(identity="github.com/dev/proj", basis="origin", common_root="/Users/dev/proj")


def _load(name: str, project_dir: str = "fixture", sub: str = "") -> tuple[sessions.Source, dict]:
    d = FIX / sub if sub else FIX
    src = sessions.load_source(Transcript(project_dir=project_dir, path=d / f"{name}.jsonl"))
    return src, json.loads((d / f"{name}.expected.json").read_text())


def _fake_identity(cwd):
    """The coordinator's machine: the repo resolves, the home directory does not."""
    return PROJ if cwd == "/Users/dev/proj" else None


class LineagePooling(unittest.TestCase):
    def test_prompts_at_home_and_commits_in_the_repo_are_one_session(self):
        """Prompts stamped with the shell's home cwd, tool calls with the repo, interleaved
        within seconds. One session, holding both; the repo is its dominant identity."""
        src, expected = _load("cwd_interleaved_one_sitting")
        last = max(r["ts"] for r in src.records)
        with mock.patch.object(sessions.repo, "identity_for", _fake_identity):
            cut = sessions.sessionize_sources([src], TZ, now=last + 1)
            self.assertEqual(len(cut), 1)
            s = cut[0]
            self.assertEqual(len(s.records), expected["records"])
            self.assertEqual(s.prompts, expected["sessions"][0]["prompts"])
            self.assertEqual(s.repo, PROJ, "the repository is an attribute, the dominant cwd")
            p = sessions.build_payload(s, TZ, MACHINE, "test", observed_at=last)
        self.assertEqual(p["human_prompt_count"], 5)
        self.assertGreater(p["tool_calls"].get("Bash", 0), 100)
        self.assertEqual(p["repo_hash"], PROJ.hash)

    def test_one_session_id_written_to_two_project_dirs_is_one_pool(self):
        """Claude Code writes a session's records into the project dir of the CURRENT cwd,
        so one id lands in several directories (the container corpus has one id in
        three). The fold puts them back together."""
        src, expected = _load("cwd_interleaved_one_sitting")
        with tempfile.TemporaryDirectory() as tmp:
            a = pathlib.Path(tmp) / "a.jsonl"
            b = pathlib.Path(tmp) / "b.jsonl"
            lines = (FIX / "cwd_interleaved_one_sitting.jsonl").read_text().splitlines(keepends=True)
            a.write_text("".join(lines[::2]))
            b.write_text("".join(lines[1::2]))
            sa = sessions.load_source(Transcript("-Users-dev", a))
            sb = sessions.load_source(Transcript("-Users-dev-proj", b))
            last = max(r["ts"] for r in src.records)
            cut = sessions.sessionize_sources([sa, sb], TZ, now=last + 1)
        self.assertEqual(len(cut), 1)
        self.assertEqual(len(cut[0].records), expected["records"])


class StructuralEnds(unittest.TestCase):
    def test_clear_ends_a_session_and_is_final_where_it_stands(self):
        src, expected = _load("cleared_twice")
        last = max(r["ts"] for r in src.records)
        cut = sessions.sessionize_sources([src], TZ, now=last + 1)
        self.assertEqual([s.end_reason for s in cut], ["cleared", "cleared"])
        self.assertEqual([s.state for s in cut], ["final", "final"])
        for got, want in zip(cut, expected["sessions"]):
            p = sessions.build_payload(got, TZ, MACHINE, "test", observed_at=last)
            self.assertEqual(p["end_reason"], "cleared")
            self.assertEqual(p["state"], "final")
            self.assertEqual(p["human_prompt_count"], want["prompts"])
            self.assertEqual(p["presence_count"], want["presence"])
            self.assertAlmostEqual(got.ended_at, want["ended_at"], places=2)

    def test_switched_repo_needs_two_lineages(self):
        """The cross-pool fixture split into two transcripts under two project dirs: the
        human opening B cuts A; B is untouched; A's late records open a fresh session."""
        _, expected = _load("switched_repo_two_pools", sub="cross_pool")
        lines = (FIX / "cross_pool" / "switched_repo_two_pools.jsonl").read_text().splitlines(keepends=True)
        with tempfile.TemporaryDirectory() as tmp:
            a = pathlib.Path(tmp) / "a.jsonl"
            b = pathlib.Path(tmp) / "b.jsonl"
            a.write_text("".join(ln for ln in lines if '"sessionId":"00000000-0000-4000-8000-000000000001"' in ln))
            b.write_text("".join(ln for ln in lines if '"sessionId":"00000000-0000-4000-8000-000000000002"' in ln))
            sa = sessions.load_source(Transcript("-Users-dev-a", a))
            sb = sessions.load_source(Transcript("-Users-dev-b", b))
            last = max(r["ts"] for r in sa.records + sb.records)
            cut = sessions.sessionize_sources([sa, sb], TZ, now=last + 1)
        self.assertEqual(
            [s.end_reason for s in cut], [w["end_reason"] for w in expected["sessions"]]
        )
        for got, want in zip(cut, expected["sessions"]):
            self.assertAlmostEqual(got.started_at, want["started_at"], places=2)
            self.assertAlmostEqual(got.ended_at, want["ended_at"], places=2)
            self.assertEqual(got.presence, want["presence"])

    def test_the_same_two_lineages_in_one_project_dir_are_one_pool(self):
        """Two conversations in one directory are one sitting (v1 rule): no switch."""
        src, _ = _load("switched_repo_two_pools", sub="cross_pool")
        last = max(r["ts"] for r in src.records)
        cut = sessions.sessionize_sources([src], TZ, now=last + 1)
        self.assertEqual([s.end_reason for s in cut], ["still_running"])


class FittedTau(unittest.TestCase):
    def test_auto_tau_reproduces_the_reference_fit_and_cut(self):
        src, expected = _load("auto_tau_bimodal")
        last = max(r["ts"] for r in src.records)
        report: dict = {}
        cut = sessions.sessionize_sources([src], TZ, now=last + 1, report=report)
        self.assertAlmostEqual(report["tau"], expected["tau"]["value"], places=6)
        self.assertEqual(report["fit"].source, "fitted")
        self.assertEqual(len(cut), len(expected["sessions"]))
        fallback = sessions.sessionize_sources([src], TZ, now=last + 1, tau=900)
        self.assertEqual(len(fallback), expected["tau"]["sessions_at_fallback"])
        self.assertLess(len(fallback), len(cut))

    def test_a_small_pool_falls_back(self):
        src, _ = _load("attended_afternoon_then_gap")
        last = max(r["ts"] for r in src.records)
        report: dict = {}
        sessions.sessionize_sources([src], TZ, now=last + 1, report=report)
        self.assertEqual((report["tau"], report["fit"].source), (900.0, "fallback"))


if __name__ == "__main__":
    unittest.main()
