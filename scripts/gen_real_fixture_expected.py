#!/usr/bin/env python3
"""Write `<fixture>.expected.json` for every REAL captured fixture (files written by the
actual tools, kept verbatim), so the loaders are held to what the tools wrote and not only
to synthetic transcripts written from their source.

    spec/fixtures/codex/real_first_records.jsonl      codex-cli 0.153.4, invalid key
    spec/fixtures/codex/real_tools_mock_model.jsonl   codex-cli 0.153.4, local mock model
    spec/fixtures/gemini/real_first_records.jsonl     gemini-cli 0.58.0, invalid key
    spec/fixtures/aider/real/                         aider 0.86.2, invalid key — a REPO
                                                      directory: one chat file, two sessions,
                                                      so `expected.json` is keyed by session id

The provenance of each file (commands, versions, what the tool did) is in the
`VERIFIED ON DISK` section of the loader's docstring. The expected file records the
loader's stats, usage, meta and diagnostics; the hand-counted invariants that make the
agreement evidence live in analysis/tests/test_codex.py, test_gemini.py and test_aider.py.
"""

from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from analysis import aider, codex, digest, gemini

FIX = ROOT / "spec" / "fixtures"
REAL = [
    ("codex", FIX / "codex" / "real_first_records.jsonl"),
    ("codex", FIX / "codex" / "real_tools_mock_model.jsonl"),
    ("gemini", FIX / "gemini" / "real_first_records.jsonl"),
    ("aider", FIX / "aider" / "real"),
]
LOADERS = {"codex": codex, "gemini": gemini, "aider": aider}
HEAD = {"_generated_by": "scripts/gen_real_fixture_expected.py — do not hand-edit"}


def _one(mod, path: pathlib.Path) -> dict:
    """The loader's view of one session: one file, or one `<chat file>/<id>` for Aider."""
    s = mod.scan(path)
    events, derivation = mod._derive(s)
    meta = dict(s.meta)
    meta.pop("path", None)  # absolute on the capturing machine; not part of the contract
    return {
        "events": len(events),
        "stats": digest.stats(events),
        "usage": s.usage,
        "meta": meta,
        "diagnostics": dict(s.diagnostics),
        "derivation": derivation,
    }


def expected_for(harness: str, path: pathlib.Path) -> dict:
    mod = LOADERS[harness]
    assert digest.detect_harness(path) == harness, (path, digest.detect_harness(path))
    if path.is_dir():
        # Aider keeps ONE chat file per repo with every session appended to it; the
        # expectation is per session, addressed the way the loader addresses them
        chat = mod.resolve(path)[0]
        sessions = {s.id: _one(mod, chat / s.id) for s in mod.list_sessions(chat)}
        return {
            **HEAD,
            "_source": f"{path.name}/{chat.name}",
            "harness": harness,
            "sessions": sessions,
        }
    return {**HEAD, "_source": path.name, "harness": harness, **_one(mod, path)}


def _events(exp: dict) -> int:
    return exp["events"] if "events" in exp else sum(s["events"] for s in exp["sessions"].values())


def main() -> int:
    for harness, path in REAL:
        exp = expected_for(harness, path)
        out = path / "expected.json" if path.is_dir() else path.with_suffix(".expected.json")
        out.write_text(json.dumps(exp, indent=1, ensure_ascii=False) + "\n")
        print(f"wrote {out.relative_to(ROOT)} ({_events(exp)} events)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
