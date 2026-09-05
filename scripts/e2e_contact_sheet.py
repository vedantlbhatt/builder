#!/usr/bin/env python3
"""Lay a `scripts/e2e_mascot.mjs` burst out as one contact sheet.

    python scripts/e2e_contact_sheet.py shots/ shots/mascot-strip.png [--cols 6]

Each cell is one captured frame with its real elapsed time stamped underneath, so a
reader can see the wave beat (260 ms), the cross-fade (75–130 ms), the blink (120 ms)
and the breath (4 s) as a sequence of stills rather than trust a description of them.
Needs Pillow.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

BG = (15, 15, 15)
INK = (200, 200, 200)
DIM = (110, 110, 110)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("shots", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--cols", type=int, default=6)
    args = ap.parse_args()

    meta = json.loads((args.shots / "mascot-frames.json").read_text())
    frames = [(Image.open(args.shots / f["file"]).convert("RGB"), f["ms"]) for f in meta["frames"]]
    if not frames:
        raise SystemExit("no frames in mascot-frames.json")

    w, h = frames[0][0].size
    label = 22
    gap = 6
    cols = max(1, args.cols)
    rows = (len(frames) + cols - 1) // cols
    header = 30
    sheet = Image.new(
        "RGB",
        (cols * (w + gap) + gap, header + rows * (h + label + gap) + gap),
        BG,
    )
    draw = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 13)
    except OSError:
        font = ImageFont.load_default()

    title = f"Bit on {meta['route']}  ·  {len(frames)} frames every {meta['everyMs']} ms  ·  {meta['scale']}x"
    draw.text((gap, 8), title, fill=INK, font=font)

    for i, (img, ms) in enumerate(frames):
        r, c = divmod(i, cols)
        x = gap + c * (w + gap)
        y = header + gap + r * (h + label + gap)
        sheet.paste(img, (x, y))
        draw.text((x + 2, y + h + 3), f"{i:02d}  {ms:>5} ms", fill=DIM, font=font)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(args.out)
    print(f"{args.out}  {sheet.size[0]}x{sheet.size[1]}  ({len(frames)} frames)")


if __name__ == "__main__":
    main()
