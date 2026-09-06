"""python3 -m unittest capture.tests.test_harnesses

Every other tool's sessions, through the same cut and onto the same wire.

The point of `capture/harnesses.py` is that it adds NO session logic: the threshold fit,
lineage folding, the two clocks and the structural ends are the Claude Code code path,
unchanged, reading records this module produced. So these tests check the two things that
are actually new — discovery (what is a root, what is a duplicate) and the record
adapter — and then assert the payloads are contract-shaped, which is what the phone reads.
"""

from __future__ import annotations

import json
import pathlib
import unittest
import zoneinfo

from analysis import digest

from capture import harnesses, sessions

ROOT = pathlib.Path(__file__).resolve().parents[2]
FIX = ROOT / "spec" / "fixtures"
CONTRACT = json.loads((ROOT / "privacy" / "upload-contract.json").read_text())
HARNESS_VALUES = next(f for f in CONTRACT["fields"] if f["name"] == "harness")["values"]
TZ = zoneinfo.ZoneInfo("America/New_York")

#: The fixture directory that stands in for each real store.
ROOTS = {
    "codex": [str(FIX / "codex")],
    "gemini_cli": [str(FIX / "gemini")],
    "cline": [str(FIX / "cline")],
    "opencode": [str(FIX / "opencode")],
    "aider": [str(FIX / "aider")],
}


def _stores() -> list[harnesses.Store]:
    return harnesses.discover(ROOTS)


class Discovery(unittest.TestCase):
    def setUp(self):
        self.stores = _stores()

    def test_every_harness_is_found(self):
        self.assertEqual(
            sorted({s.harness for s in self.stores}),
            ["aider", "cline", "codex", "gemini_cli", "opencode"],
        )

    def test_every_harness_it_reports_is_on_the_wire(self):
        # A harness this module can discover but the contract cannot name would be
        # rejected by the server for every session, forever, with no local symptom.
        for s in self.stores:
            self.assertIn(s.harness, HARNESS_VALUES, s.harness)

    def test_a_gemini_subagent_is_not_a_root(self):
        # The Claude Code version of this mistake was a ~3x token overcount (CLAUDE.md,
        # "Globbing"). A subagent's work is already inside the parent's tool result.
        # Decided by the recording's own `kind`, so it holds for a file that is not under
        # `chats/<parent>/` too.
        paths = {str(s.path) for s in self.stores}
        self.assertNotIn(str(FIX / "gemini" / "synthetic_subagent.jsonl"), paths)
        self.assertIn(str(FIX / "gemini" / "synthetic_session.jsonl"), paths)

    def test_an_opencode_child_session_is_not_a_root(self):
        for s in self.stores:
            if s.harness != "opencode":
                continue
            from analysis import opencode

            self.assertFalse(opencode.meta(s.path).get("is_child"), s.path)

    def test_one_session_in_three_containers_is_uploaded_once(self):
        # The opencode fixture holds the SAME two sessions three ways: the SQLite
        # database, the pre-SQLite `storage/session/**.json` tree a migrated machine keeps,
        # and an `opencode export` file. Taking all three would triple that person's hours.
        oc = [s for s in self.stores if s.harness == "opencode"]
        self.assertEqual(len(oc), 1, [str(s.path) for s in oc])
        self.assertIn("opencode.db", str(oc[0].path))  # the database wins

    def test_a_legacy_gemini_json_does_not_double_the_jsonl(self):
        gem = [s for s in self.stores if s.harness == "gemini_cli"]
        self.assertEqual(len(gem), len({s.session_id for s in gem}))
        self.assertTrue(all(s.path.suffix == ".jsonl" for s in gem), [str(s.path) for s in gem])

    def test_one_aider_file_is_many_sessions_in_one_repo(self):
        # `.aider.chat.history.md` is appended to forever, so the FILE is not a session and
        # the repo is the lineage.
        aider = [s for s in self.stores if s.harness == "aider"]
        self.assertGreater(len(aider), 1)
        self.assertEqual(len({s.pool_dir for s in aider}), 2)  # the fixture holds two repos


class Records(unittest.TestCase):
    def test_a_prompt_is_presence_and_a_tool_call_is_not(self):
        # Presence is the ONLY field the boundary rules read. Getting it wrong does not
        # error: it silently files an attended sitting as an autonomous run, which can
        # never win a record or fire a notification.
        store = next(s for s in _stores() if s.harness == "codex")
        events = digest.load_events(store.path)
        recs = harnesses.records_for(events, store, "srcid")
        by_kind = {e.kind: r for e, r in zip(events, recs)}
        if "prompt" in by_kind:
            self.assertTrue(by_kind["prompt"]["presence"])
        if "tool" in by_kind:
            self.assertFalse(by_kind["tool"]["presence"])
        self.assertEqual(len(recs), len(events))
        self.assertEqual(recs, sorted(recs, key=lambda r: r["ts"]))

    def test_tokens_are_absent_not_zero(self):
        # These loaders report tokens at their own granularity; mapping them into a
        # per-message ledger would produce an authoritative-looking number that is not
        # comparable with the one beside it. Absent is the honest answer (the Cursor rule).
        for store in _stores():
            recs = harnesses.records_for(digest.load_events(store.path), store, "srcid")
            for r in recs:
                self.assertIsNone(r["usage"], store.path)
                self.assertIsNone(r["msg_id"], store.path)
            self.assertFalse(sessions.token_ledger(recs).reported)

    def test_a_compaction_carries_the_subtype_the_strip_reads(self):
        for store in _stores():
            events = digest.load_events(store.path)
            recs = harnesses.records_for(events, store, "srcid")
            for e, r in zip(events, recs):
                if e.kind == "compaction":
                    self.assertEqual(r["subtype"], "compact_boundary")


class Payloads(unittest.TestCase):
    """Every discovered session, cut and built, must be a payload the server accepts."""

    @classmethod
    def setUpClass(cls):
        sources = [harnesses.load(s) for s in _stores()]
        assert sources
        last = max(r["ts"] for s in sources for r in s.records)
        cls.sessions = sessions.sessionize_sources(sources, TZ, now=last + 10_000)
        cls.payloads = [
            sessions.build_payload(s, TZ, "1" * 64, "test", observed_at=last)
            for s in cls.sessions
        ]

    def test_the_payload_says_which_tool_wrote_the_session(self):
        # `harness` is read back off the pool key the session was CUT in, so the wire value
        # and the lineage can never disagree.
        seen = {p["harness"] for p in self.payloads}
        self.assertTrue(seen <= set(HARNESS_VALUES), seen)
        self.assertNotIn("claude_code", seen)  # nothing here was written by Claude Code

    def test_two_tools_in_one_directory_are_two_lineages(self):
        # A Codex rollout and an Aider history in the same folder are two sittings that
        # happen to share a path. Folding them would credit one tool's idle gap to the
        # other's active time.
        keys = {s.pool for s in self.sessions}
        self.assertEqual(len(keys), len({(k.split("|", 1)[0], k) for k in keys}))
        for s in self.sessions:
            self.assertTrue(s.pool.startswith(s.harness + "|"), s.pool)

    def test_every_payload_field_is_declared(self):
        declared = {f["name"] for f in CONTRACT["fields"]}
        for p in self.payloads:
            for key in p:
                self.assertIn(key, declared, f"{key} is sent but not declared")

    def test_the_two_clocks_sum_to_active(self):
        for p in self.payloads:
            self.assertEqual(
                p["attended_seconds"] + p["autonomous_seconds"], p["active_seconds"], p["harness"]
            )

    def test_tokens_are_reported_absent(self):
        for p in self.payloads:
            self.assertFalse(p["tokens_reported"], p["harness"])
