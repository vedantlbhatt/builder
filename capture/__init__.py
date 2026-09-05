"""Builder capture — the uploader that runs where the Mac agent cannot.

Claude Code sessions started from claude.ai/code (web or phone) run in a cloud container.
Their transcript is written to that container's ``~/.claude/projects`` and never reaches
the user's Mac, so the phone never sees them. This package reads those transcripts with
the same rules as the engine, computes exactly the contract v2 payload, and uploads it
through the same endpoints the Mac uses. Python 3.11+, standard library only, so it runs
inside a bare container with nothing installed.

Two commands::

    python -m capture pair --server URL        # RFC 8628 device flow, as `builder pair`
    python -m capture sync [--root DIR] [--dry-run] [--live] [--finalize] [--analyze]

What is shared with the engine, and how:

* boundaries — ``scripts/measure_boundaries.py`` is imported and its ``sessionize`` is
  called unchanged; it is the reference the Swift engine is held to via the fixtures.
* the digest loader — ``analysis.digest.load_claude_code_events`` supplies tool calls,
  agent line deltas, human edits and compactions; nothing here re-parses those.
* the contract — every key in a payload is declared in ``privacy/upload-contract.json``,
  and ``capture/tests/test_contract.py`` walks the nested fields to prove it.
* the strip — ``spec/strip.v1.json`` supplies the ordinals, weights and thresholds;
  ``capture/strip.py`` is a port of ``StripBuilder.swift`` and hand-types no integer.

Read ``docs/cloud-capture.md`` before wiring this into a hook.
"""

from __future__ import annotations

import pathlib

CLIENT_VERSION = "capture-0.1.0"

#: The repository root. `capture/` is a package inside the builder checkout on purpose:
#: it imports the Python reference implementations from `scripts/` and `analysis/` rather
#: than carrying copies that could drift.
ROOT = pathlib.Path(__file__).resolve().parent.parent
