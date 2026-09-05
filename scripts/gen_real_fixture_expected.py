#!/usr/bin/env python3
"""Write `<fixture>.expected.json` for every REAL captured fixture (files written by the
actual tools, kept verbatim), so the loaders are held to what the tools wrote and not only
to synthetic transcripts written from their source.

    spec/fixtures/codex/real_first_records.jsonl      codex-cli 0.153.4, invalid key
    spec/fixtures/codex/real_tools_mock_model.jsonl   codex-cli 0.153.4, local mock model
    spec/fixtures/gemini/real_first_records.jsonl     gemini-cli 0.58.0, invalid key

The provenance of each file (commands, versions, what the tool did) is in the
`VERIFIED ON DISK` section of the loader's docstring. The expected file records the
loader's stats, usage, meta and diagnostics; the hand-counted invariants that make the
agreement evidence live in analysis/tests/test_codex.py and test_gemini.py.
"""

from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from analysis import codex, digest, gemini

FIX = ROOT / "spec" / "fixtures"
REAL = [
    ("codex", FIX / "codex" / "real_first_records.jsonl"),
    ("codex", FIX / "codex" / "real_tools_mock_model.jsonl"),
    ("gemini", FIX / "gemini" / "real_first_records.jsonl"),
]
LOADERS = {"codex": codex, "gemini": gemini}


def expected_for(harness: str, path: pathlib.Path) -> dict:
    mod = LOADERS[harness]
    assert digest.detect_harness(path) == harness, (path, digest.detect_harness(path))
    s = mod.scan(path)
    events, derivation = mod._derive(s)
    meta = dict(s.meta)
    meta.pop("path", None)  # absolute on the capturing machine; not part of the contract
    return {
        "_generated_by": "scripts/gen_real_fixture_expected.py — do not hand-edit",
        "_source": path.name,
        "harness": harness,
        "events": len(events),
        "stats": digest.stats(events),
        "usage": s.usage,
        "meta": meta,
        "diagnostics": dict(s.diagnostics),
        "derivation": derivation,
    }


def main() -> int:
    for harness, path in REAL:
        exp = expected_for(harness, path)
        out = path.with_suffix(".expected.json")
        out.write_text(json.dumps(exp, indent=1, ensure_ascii=False) + "\n")
        print(f"wrote {out.relative_to(ROOT)} ({exp['events']} events)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
