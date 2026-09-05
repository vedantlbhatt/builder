"""The session timeline strip, in Python — a port of `StripBuilder.swift`.

Nothing in this file hand-types a class ordinal, a weight or a threshold: they are read
from `spec/strip.v1.json`, THE ONLY place they live. The spec's own warning is the reason —
two proposals once had classes 1 and 2 swapped, and a renderer fed the other spec's bytes
paints every agent run in the prompt colour, producing a plausible strip of a human who
typed for three hours straight.

The server rejects a payload whose strip disagrees with its own `active_seconds` by more
than 25% (`routes/sync.py`), so the fill rule below is the engine's, step for step:

* the interval AFTER an event belongs to the event that opened it — a prompt paints a
  visible notch of its own rather than vanishing into the agent run that follows;
* everything past `activeGapCapSec` of a gap is idle, not "still working";
* the column class is a PRIORITY-WEIGHTED ARGMAX over milliseconds (weights from the spec),
  because at 4.2 s/column essentially every column holds several event types and raw argmax
  hands all of them to the agent;
* density is events per second in the column, bucketed by the spec's thresholds;
* marks (prompt, compaction) are stored separately and deduped at a 400 pt reference width.

Density counts one event per timestamped RECORD here where the engine counts one per
normalized event; Claude Code writes one record per content block, so the two agree on
assistant records and differ only on multi-block tool-result records. The class channel,
which is what the server checks, is computed identically.

There is no cross-language ENCODE fixture yet — `spec/fixtures/strip*.json` are decode
fixtures — so parity with the Swift encoder rests on this port being read next to
`StripBuilder.swift`, and on the server's gate. That is stated here so nobody mistakes
the gate passing for a byte-level proof.
"""

from __future__ import annotations

import base64
import json

from . import ROOT
from .tuning import ACTIVE_GAP_CAP_SEC

_SPEC = json.loads((ROOT / "spec" / "strip.v1.json").read_text())

SPEC_VERSION: int = _SPEC["spec_version"]
COLUMNS: int = _SPEC["columns"]
CLASSES: dict[str, int] = _SPEC["classes"]
MARKS: dict[str, int] = _SPEC["marks"]
_WEIGHT_BY_ORDINAL: dict[int, float] = {
    CLASSES[name]: float(w) for name, w in _SPEC["classWeights"].items()
}
DENSITY_THRESHOLDS: list[float] = [float(t) for t in _SPEC["densityThresholdsEventsPerSec"]]
MARK_DEDUPE_MIN_PX: int = _SPEC["markDedupeMinPx"]

_IDLE = CLASSES["idle"]
_PROMPTING = CLASSES["prompting"]
_AGENT = CLASSES["agent"]
_HUMAN_EDIT = CLASSES["human_edit"]

#: Reference render width the engine assumes when collapsing marks.
_REFERENCE_WIDTH_PT = 400


def pack(klass: int, density: int) -> int:
    """bits 0-1 class, bits 2-3 density, bits 4-7 reserved and zero."""
    return (klass & 0b11) | ((density & 0b11) << 2)


def class_of(kind: str) -> int:
    """`StripBuilder.classOf`, over the record kinds `measure_boundaries.classify` emits.

    prompt and interrupt open a human interval (the interval after an interrupt is the
    human deciding what to say next); a human edit is its own class; every other
    timestamped record — assistant, tool result, attachment, system, title — is the agent.
    """
    if kind in ("prompt", "interrupt"):
        return _PROMPTING
    if kind == "human_edit":
        return _HUMAN_EDIT
    return _AGENT


def build(
    records: list[dict], started_at: float, ended_at: float
) -> tuple[bytes, list[dict[str, int]]]:
    """(1024 column bytes, marks as [{ms, k}]) for one session's timestamped records.

    `records` carry `ts`, `kind`, and optionally `subtype` (so `system/compact_boundary`
    can place a compaction mark). Rewound records must be filtered out by the caller: a
    rewound edit never reached the file, so painting it shows work that does not exist.
    """
    t0 = started_at
    span = max(ended_at - started_at, 0.001)
    n = COLUMNS
    col_seconds = span / n

    weights = [[0.0, 0.0, 0.0, 0.0] for _ in range(n)]
    events_per_column = [0] * n
    marks: list[tuple[int, int]] = []

    timed = sorted(((r["ts"], r) for r in records), key=lambda t: t[0])

    def column(t: float) -> int:
        if col_seconds <= 0:
            return 0
        return max(0, min(n - 1, int((t - t0) / col_seconds)))

    def paint(frm: float, to: float, klass: int) -> None:
        if to <= frm:
            return
        first, last = column(frm), column(to)
        if first > last:
            return
        for c in range(first, last + 1):
            col_start = t0 + c * col_seconds
            col_end = col_start + col_seconds
            overlap = min(to, col_end) - max(frm, col_start)
            if overlap > 0:
                weights[c][klass] += overlap

    for i, (start, rec) in enumerate(timed):
        nxt = timed[i + 1][0] if i + 1 < len(timed) else ended_at
        active_end = min(nxt, start + ACTIVE_GAP_CAP_SEC)
        paint(start, active_end, class_of(rec["kind"]))
        if nxt > active_end:
            paint(active_end, nxt, _IDLE)
        events_per_column[column(start)] += 1
        if rec["kind"] == "prompt":
            marks.append((int((start - t0) * 1000), MARKS["prompt"]))
        elif rec["kind"] == "system" and rec.get("subtype") == "compact_boundary":
            marks.append((int((start - t0) * 1000), MARKS["compact"]))

    cols = bytearray(pack(_IDLE, 0) for _ in range(n))
    for c in range(n):
        best, best_score = _IDLE, 0.0
        for ordinal, ms in enumerate(weights[c]):
            score = ms * _WEIGHT_BY_ORDINAL.get(ordinal, 1.0)
            if score > best_score:
                best, best_score = ordinal, score
        per_second = events_per_column[c] / col_seconds if col_seconds > 0 else 0.0
        density = sum(1 for t in DENSITY_THRESHOLDS if per_second >= t)
        cols[c] = pack(best, min(density, 3))

    return bytes(cols), _dedupe_marks(marks, span)


def _dedupe_marks(marks: list[tuple[int, int]], span: float) -> list[dict[str, int]]:
    """Collapse marks that would share a pixel at the reference width. The survivor stands
    for both; the density channel already says something busy happened there."""
    if not marks:
        return []
    ordered = sorted(marks, key=lambda m: m[0])
    ms_per_px = span * 1000 / _REFERENCE_WIDTH_PT
    min_gap = MARK_DEDUPE_MIN_PX * ms_per_px
    out = [ordered[0]]
    for m in ordered[1:]:
        if m[0] - out[-1][0] >= min_gap:
            out.append(m)
    return [{"ms": ms, "k": k} for ms, k in out]


def encode_columns(cols: bytes) -> str:
    return base64.b64encode(cols).decode("ascii")


def non_idle_seconds(cols: bytes, span_seconds: float) -> float:
    """What the server's gate computes (`server/builder/strip.py`), for the local check."""
    if not cols:
        return 0.0
    per_col = span_seconds / len(cols)
    return sum(per_col for b in cols if (b & 0b11) != _IDLE)
