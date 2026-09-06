"""python3 -m unittest capture.tests.test_contract

Every key in an upload payload must be declared in privacy/upload-contract.json — walking
NESTED fields properly. CLAUDE.md records the mistake this guards against: a check that
compared every scalar path against the flat list of top-level names reported
`tokens.input` and `strip_marks[].ms` as "sent but not declared", and the first person to
run it publicly would have concluded the privacy claim was false. Nested shapes are
checked against the wire models the contract's `type` names (`tokens`, `marks`, `models`,
`analysis`), which is what `server/builder/contract.py` enforces with extra="forbid".
"""

from __future__ import annotations

import base64
import json
import pathlib
import re
import unittest
import zoneinfo

from capture import sessions, strip
from capture.discover import Transcript

ROOT = pathlib.Path(__file__).resolve().parents[2]
CONTRACT = json.loads((ROOT / "privacy" / "upload-contract.json").read_text())
FIELDS = {f["name"]: f for f in CONTRACT["fields"]}
ANALYSIS_SCHEMA = json.loads((ROOT / "analysis" / "schema.json").read_text())
FIX = ROOT / "spec" / "fixtures" / "boundaries"
TZ = zoneinfo.ZoneInfo("America/New_York")
SHA = re.compile(r"^[0-9a-f]{64}$")

#: The insides of the four structured field types, as `server/builder/contract.py`
#: declares them (TokenBucketsWire, StripMarkWire, ModelShareWire, FeedbackNoteWire).
NESTED = {
    "tokens": {"input", "output", "cache_read", "cache_w5m", "cache_w1h"},
    "marks": {"ms", "k"},
    "models": {"model_id", "output_token_share"},
    "feedback": {"id", "seconds", "count"},
}


def _all_payloads() -> list[dict]:
    out = []
    for jsonl in sorted(FIX.glob("*.jsonl")):
        src = sessions.load_source(Transcript("fixture", jsonl))
        last = max(r["ts"] for r in src.records)
        for s in sessions.sessionize_sources([src], TZ, now=last + 1):
            out.append(sessions.build_payload(s, TZ, "1" * 64, "test", observed_at=last))
    return out


class ContractConformance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.payloads = _all_payloads()
        assert cls.payloads

    def test_contract_is_v3(self):
        self.assertEqual(CONTRACT["version"], 3)

    def test_every_key_is_declared_nested_fields_included(self):
        for p in self.payloads:
            undeclared = set(p) - set(FIELDS)
            self.assertEqual(undeclared, set(), f"undeclared top-level keys: {undeclared}")
            for name, value in p.items():
                typ = FIELDS[name]["type"]
                if typ == "tokens" and value is not None:
                    self.assertEqual(set(value) - NESTED["tokens"], set())
                elif typ == "marks":
                    for m in value:
                        self.assertEqual(set(m) - NESTED["marks"], set())
                elif typ == "models":
                    for m in value:
                        self.assertEqual(set(m) - NESTED["models"], set())
                elif typ == "analysis" and value is not None:
                    self.assertEqual(set(value) - set(ANALYSIS_SCHEMA["properties"]), set())
                elif typ == "feedback" and value is not None:
                    for n in value:
                        self.assertEqual(set(n) - NESTED["feedback"], set())
                        self.assertIn(n["id"], FIELDS["feedback"]["values"])
                elif typ in ("toolmap",):
                    self.assertTrue(all(isinstance(v, int) for v in value.values()))
                else:
                    self.assertNotIsInstance(value, dict, f"{name} is an undeclared object")

    def test_anonymous_mode_carries_no_public_only_field(self):
        public_only = {n for n, f in FIELDS.items() if f["modes"] == ["public"]}
        self.assertEqual(public_only, {"repo_name", "title", "title_source"})
        for p in self.payloads:
            self.assertEqual(set(p) & public_only, set())

    def test_required_fields_are_present(self):
        optional = {n for n, f in FIELDS.items() if f.get("nullable")} | {
            "repo_name",
            "title",
            "title_source",
        }
        for p in self.payloads:
            missing = set(FIELDS) - optional - set(p)
            self.assertEqual(missing, set())

    def test_enums_hashes_and_shapes(self):
        for p in self.payloads:
            for name, f in FIELDS.items():
                if name not in p:
                    continue
                if f["type"] == "enum":
                    self.assertIn(p[name], f["values"], name)
                if f["type"] == "sha256hex":
                    self.assertRegex(p[name], SHA, name)
            cols = base64.b64decode(p["strip_columns"], validate=True)
            self.assertEqual(len(cols), strip.COLUMNS)
            self.assertFalse(any(b >> 4 for b in cols), "reserved bits must be zero")

    def test_server_sanity_gate_invariants(self):
        """The checks in server/builder/routes/sync.py::sanity_gate, locally."""
        for p in self.payloads:
            span = _ts(p["ended_at"]) - _ts(p["started_at"])
            self.assertLessEqual(p["active_seconds"], span + 1)
            self.assertEqual(p["state"] == "live", p["end_reason"] == "still_running")
            self.assertEqual(p["attended_seconds"] + p["autonomous_seconds"], p["active_seconds"])
            if p["presence_count"] == 0 and p["active_seconds"] >= 1200:
                self.assertTrue(p["unattended"])
            if p["presence_count"] > 0:
                self.assertFalse(p["unattended"])
            self.assertFalse(p["token_dedupe"] == "none" and p["tokens_reported"])
            if not p["tokens_reported"]:
                self.assertNotIn("tokens", p)
            cols = base64.b64decode(p["strip_columns"])
            strip_active = strip.non_idle_seconds(cols, span)
            if p["active_seconds"] > 0 and not (p["state"] == "live" and p["active_seconds"] < 300):
                self.assertLessEqual(
                    abs(strip_active - p["active_seconds"]), 0.25 * p["active_seconds"]
                )

    def test_tokens_are_deduped_on_message_id(self):
        """The fixtures write one usage object per assistant record with a UNIQUE message
        id each, so the deduped total equals the record count; a second record with the
        same id must add nothing (the 1.878x content-block trap)."""
        jsonl = FIX / "remote_sdk_prompts.jsonl"
        src = sessions.load_source(Transcript("fixture", jsonl))
        led = sessions.token_ledger(src.records)
        n_assistant = sum(1 for r in src.records if r["kind"] == "assistant")
        self.assertEqual(led.buckets["input"], n_assistant)
        dup = [dict(r) for r in src.records if r["kind"] == "assistant"][:1]
        dup[0]["line"] = 10**6
        led2 = sessions.token_ledger(src.records + dup)
        self.assertEqual(led2.buckets["input"], n_assistant)

    def test_content_hash_ignores_only_volatile_fields(self):
        p = dict(self.payloads[0])
        h = sessions.content_hash(p)
        q = dict(p, agent_observed_at="2030-01-01T00:00:00.000Z")
        self.assertEqual(sessions.content_hash(q), h)
        r = dict(p, active_seconds=p["active_seconds"] + 1)
        self.assertNotEqual(sessions.content_hash(r), h)


def _ts(iso: str) -> float:
    import datetime as dt

    return dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


if __name__ == "__main__":
    unittest.main()
