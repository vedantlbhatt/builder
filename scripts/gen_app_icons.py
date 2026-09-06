#!/usr/bin/env python3
"""
Render the app icon, the Android adaptive foreground, the splash mark and the favicon
from the mascot's resting frame and `design/tokens.json`.

The icon is not a drawing someone made once and dropped in `assets/`. It is Bit's idle
pose at 16x16, the same sixteen strings `mobile/src/pixel/sprites.ts` renders on the home
screen, coloured by the same tokens. Re-tune the accent in `design/tokens.json` and
`make icons` re-renders every store asset to match, so the icon can never drift from the
mascot the app actually shows.

Deliberately NOT part of `make gen`. The generated Swift/TS/Python files are text and
byte-identical everywhere, which is what lets `git diff --exit-code` be a CI gate; a
zlib-compressed PNG is not guaranteed to be byte-identical across zlib builds, so making
it a gate would turn a working runner into a red build for no defect.

No Pillow. The whole encoder below is 40 lines of zlib and struct, and adding an image
library to a repo that has zero of them so far, for four static files, is not a trade.
"""

from __future__ import annotations

import json
import re
import struct
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOKENS = ROOT / "design" / "tokens.json"
SPRITES = ROOT / "mobile" / "src" / "pixel" / "sprites.ts"
OUT = ROOT / "mobile" / "assets"

# Body-shadow factor. Must equal BODY_DARK_FACTOR in mobile/src/pixel/palette.ts, or the
# icon's amber shadow is a different amber from the running mascot's.
BODY_DARK_FACTOR = 0.62

RGB = tuple[int, int, int]


def parse_hex(value: str) -> RGB:
    v = value.lstrip("#")
    return (int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16))


def scale(c: RGB, factor: float) -> RGB:
    return tuple(max(0, min(255, round(ch * factor))) for ch in c)  # type: ignore[return-value]


def read_frame(name: str) -> list[str]:
    """The 16 strings of one sprite frame, straight out of the TypeScript."""
    src = SPRITES.read_text()
    m = re.search(rf"const {name}: Frame = \[(.*?)\];", src, re.S)
    if not m:
        raise SystemExit(f"{name} not found in {SPRITES}")
    rows = re.findall(r"'([^']*)'", m.group(1))
    if len(rows) != 16 or any(len(r) != 16 for r in rows):
        raise SystemExit(f"{name} is not 16x16: {len(rows)} rows")
    return rows


def palette() -> dict[str, RGB]:
    t = json.loads(TOKENS.read_text())
    accent = parse_hex(t["surface"]["accent"]["dark"])
    return {
        "b": accent,
        "d": scale(accent, BODY_DARK_FACTOR),
        "e": parse_hex(t["surface"]["bg"]["dark"]),
        "w": parse_hex(t["surface"]["text"]["dark"]),
        "h": parse_hex(t["strip"]["human_edit"]["dark"]),
        "z": parse_hex(t["surface"]["textDim"]["dark"]),
    }


def bbox(frame: list[str]) -> tuple[int, int, int, int]:
    """Rows/cols actually drawn. The idle pose leaves four empty columns each side for
    arms and props; centring on the 16x16 grid instead would sit the character visibly
    high and small inside its own icon."""
    ys = [y for y, row in enumerate(frame) if row.strip(".")]
    xs = [x for x in range(16) if any(row[x] != "." for row in frame)]
    return min(ys), max(ys), min(xs), max(xs)


def write_png(path: Path, w: int, h: int, rgba: bytearray, alpha: bool) -> None:
    """Minimal PNG: one IHDR, one IDAT, one IEND, filter type 0 on every row."""
    depth = 4 if alpha else 3
    raw = bytearray()
    for y in range(h):
        raw.append(0)
        row = rgba[y * w * 4 : (y + 1) * w * 4]
        if alpha:
            raw += row
        else:
            for x in range(w):
                raw += row[x * 4 : x * 4 + 3]

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6 if alpha else 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(bytes(raw), 9))
    png += chunk(b"IEND", b"")
    path.write_bytes(png)
    print(f"{path.relative_to(ROOT)}  {w}x{h}  {'RGBA' if alpha else 'RGB'}  {len(png)} B")


def render(
    path: Path, size: int, fill: float, background: RGB | None, frame: list[str], pal: dict[str, RGB]
) -> None:
    """`fill` is the fraction of the canvas the character's longest side occupies."""
    y0, y1, x0, x1 = bbox(frame)
    cols, rows = x1 - x0 + 1, y1 - y0 + 1
    cell = max(1, int(size * fill) // max(cols, rows))
    ox = (size - cols * cell) // 2
    oy = (size - rows * cell) // 2

    buf = bytearray(size * size * 4)
    if background is not None:
        r, g, b = background
        for i in range(size * size):
            buf[i * 4 : i * 4 + 4] = bytes((r, g, b, 255))

    for gy in range(rows):
        for gx in range(cols):
            glyph = frame[y0 + gy][x0 + gx]
            if glyph == ".":
                continue
            r, g, b = pal[glyph]
            for py in range(oy + gy * cell, oy + (gy + 1) * cell):
                base = (py * size + ox + gx * cell) * 4
                buf[base : base + cell * 4] = bytes((r, g, b, 255)) * cell

    write_png(path, size, size, buf, alpha=background is None)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    frame = read_frame("IDLE_0")
    pal = palette()
    bg = parse_hex(json.loads(TOKENS.read_text())["surface"]["bg"]["dark"])

    # iOS rejects an icon with an alpha channel outright, so this one is opaque RGB.
    render(OUT / "icon.png", 1024, 0.66, bg, frame, pal)
    # Android masks the adaptive foreground to a shape inside the middle 66% of the
    # canvas. At the icon's 0.66 the feet and the antenna would be cropped by a circle.
    render(OUT / "adaptive-icon.png", 1024, 0.46, None, frame, pal)
    render(OUT / "splash-icon.png", 512, 0.60, None, frame, pal)
    render(OUT / "favicon.png", 64, 0.75, bg, frame, pal)


if __name__ == "__main__":
    main()
