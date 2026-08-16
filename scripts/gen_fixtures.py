#!/usr/bin/env python3
"""Generate the golden strip fixtures that Swift and TypeScript both assert against.

These are the only thing standing between two independent renderers and a silent
disagreement. They are generated rather than hand-written so the expected values cannot
be quietly edited to match whichever implementation happens to be wrong.

Each fixture records the encoded bytes AND the fully decoded expectation: class and
density per column, and the resampled result at several widths. A renderer that reads the
wrong ordinals, or rounds a resample differently, fails on a value rather than on a
colour someone might wave off as anti-aliasing.
"""

from __future__ import annotations

import base64
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
SPEC = json.loads((ROOT / "spec" / "strip.v1.json").read_text())
OUT = ROOT / "spec" / "fixtures"

CLASSES = SPEC["classes"]
MARKS = SPEC["marks"]
COLUMNS = SPEC["columns"]


def pack(klass: int, density: int) -> int:
    assert 0 <= klass <= 3 and 0 <= density <= 3
    return (klass & 0b11) | ((density & 0b11) << 2)


def resample(cols: list[int], width: int) -> list[int]:
    """Nearest-neighbour on output column centres — the reference implementation."""
    n = len(cols)
    if width <= 0 or n == 0:
        return []
    out = []
    for i in range(width):
        centre = (i + 0.5) / width
        src = min(n - 1, max(0, int(centre * n)))
        out.append(cols[src])
    return out


def fixture(name: str, cols: list[int], marks: list[tuple[int, int]], span_ms: int) -> dict:
    return {
        "name": name,
        "spec_version": SPEC["spec_version"],
        "span_ms": span_ms,
        "cols_b64": base64.b64encode(bytes(cols)).decode(),
        "marks": [list(m) for m in marks],
        "expected_class_per_column": [c & 0b11 for c in cols],
        "expected_density_per_column": [(c >> 2) & 0b11 for c in cols],
        # Widths chosen to exercise: exact, heavy downsample, upsample, and a width that
        # divides unevenly so the tie-break is actually tested.
        "expected_resampled": {
            str(w): resample(cols, w) for w in (COLUMNS, 400, 64, 37, 8)
        },
    }


def all_agent() -> list[int]:
    return [pack(CLASSES["agent"], 2)] * COLUMNS


def alternating() -> list[int]:
    """Every class and every density, cycling — catches an off-by-one in either field."""
    cols = []
    for i in range(COLUMNS):
        cols.append(pack(i % 4, (i // 4) % 4))
    return cols


def realistic() -> list[int]:
    """A plausible session: agent work broken by idle gaps, with prompts at the seams."""
    cols = []
    for i in range(COLUMNS):
        phase = i % 256
        if phase < 8:
            cols.append(pack(CLASSES["prompting"], 3))
        elif phase < 180:
            cols.append(pack(CLASSES["agent"], 2 if phase % 3 else 3))
        elif phase < 200:
            cols.append(pack(CLASSES["human_edit"], 1))
        else:
            cols.append(pack(CLASSES["idle"], 0))
    return cols


def edges() -> list[int]:
    """Boundary values: the first and last column of every class."""
    cols = [pack(CLASSES["idle"], 0)] * COLUMNS
    cols[0] = pack(CLASSES["prompting"], 3)
    cols[1] = pack(CLASSES["human_edit"], 0)
    cols[COLUMNS - 2] = pack(CLASSES["agent"], 1)
    cols[COLUMNS - 1] = pack(CLASSES["human_edit"], 3)
    return cols


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    span = 71 * 60 * 1000  # the measured mean session, 71 minutes

    fixtures = [
        fixture("all_agent", all_agent(), [], span),
        fixture("alternating", alternating(), [], span),
        fixture(
            "realistic",
            realistic(),
            [(0, MARKS["prompt"]), (span // 4, MARKS["prompt"]),
             (span // 2, MARKS["compact"]), (span - 1000, MARKS["prompt"])],
            span,
        ),
        fixture("edges", edges(), [(0, MARKS["prompt"])], span),
        # A twenty-minute session and a six-hour one must both look right; the resample
        # ratios differ by an order of magnitude.
        fixture("short_session", realistic(), [(0, MARKS["prompt"])], 20 * 60 * 1000),
        fixture("long_session", realistic(), [(0, MARKS["prompt"])], 6 * 60 * 60 * 1000),
    ]

    for f in fixtures:
        path = OUT / f"strip_{f['name']}.json"
        text = json.dumps(f, indent=2) + "\n"
        if not path.exists() or path.read_text() != text:
            path.write_text(text)
            print(f"  wrote {path.relative_to(ROOT)}")

    index = OUT / "index.json"
    names = sorted(f["name"] for f in fixtures)
    text = json.dumps({"fixtures": names}, indent=2) + "\n"
    if not index.exists() or index.read_text() != text:
        index.write_text(text)
        print(f"  wrote {index.relative_to(ROOT)}")


if __name__ == "__main__":
    print("gen_fixtures.py")
    main()
