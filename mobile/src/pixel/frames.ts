/**
 * Frame helpers for the pixel mascot.
 *
 * A frame is 16 rows of 16 characters. '.' is transparent; every other character is a
 * palette ROLE, not a colour — the colour is resolved per scheme at render time
 * (`palette.ts`), so one set of frames serves light and dark and any future re-tint.
 *
 * Everything here is pure and free of React Native imports so it runs under `bun test`.
 */

export type Frame = string[];

export const GRID = 16;

/** The transparent glyph. */
export const EMPTY = '.';

/**
 * Palette roles a frame may use. Anything else fails `validateFrame`, so a typo in a
 * sprite string is a test failure rather than a pixel silently rendered in the fallback
 * colour.
 *
 *   b  body           accent amber
 *   d  body, dark     accent amber, darkened — outline, shadow, mouth, antenna stem
 *   e  eye            background colour
 *   w  eye highlight  text colour (also used for sparks and light confetti)
 *   h  hardhat / tool secondary colour from the strip palette (teal)
 *   z  zzz glyph      dim text colour (also dark confetti, thought-bubble trail)
 */
export const GLYPHS = ['b', 'd', 'e', 'w', 'h', 'z'] as const;
export type Glyph = (typeof GLYPHS)[number];

/** Glyphs that count as "the character's body" when checking frame consistency. */
export const BODY_GLYPHS: readonly Glyph[] = ['b', 'd'];

const KNOWN = new Set<string>([EMPTY, ...GLYPHS]);

export interface Run {
  start: number;
  length: number;
  ch: string;
}

/**
 * Run-length encode one row. `"..bbb.b"` → `[. x2, b x3, . x1, b x1]`.
 *
 * Transparent runs are returned too — this is a plain RLE, and the renderer is the one
 * that decides '.' draws nothing. One Rect per run rather than per pixel keeps a 16×16
 * frame at ~40 nodes instead of 256.
 */
export function runsFor(row: string): Run[] {
  const runs: Run[] = [];
  for (let i = 0; i < row.length; i++) {
    const ch = row[i]!;
    const last = runs[runs.length - 1];
    if (last && last.ch === ch) {
      last.length += 1;
    } else {
      runs.push({ start: i, length: 1, ch });
    }
  }
  return runs;
}

/**
 * Problems with a frame, or an empty array when it is well-formed. Returned rather than
 * thrown so a test can print every fault in a sprite at once instead of the first one.
 */
export function validateFrame(frame: Frame): string[] {
  const problems: string[] = [];
  if (frame.length !== GRID) {
    problems.push(`expected ${GRID} rows, got ${frame.length}`);
  }
  frame.forEach((row, y) => {
    if (row.length !== GRID) {
      problems.push(`row ${y}: expected ${GRID} columns, got ${row.length}`);
    }
    for (let x = 0; x < row.length; x++) {
      const ch = row[x]!;
      if (!KNOWN.has(ch)) problems.push(`row ${y} col ${x}: unknown glyph '${ch}'`);
    }
  });
  return problems;
}

export function isValidFrame(frame: Frame): boolean {
  return validateFrame(frame).length === 0;
}

/** Horizontal flip. `mirror(mirror(f))` is `f`. */
export function mirror(frame: Frame): Frame {
  return frame.map((row) => row.split('').reverse().join(''));
}

/** Count of pixels drawn with any of `glyphs` (default: the body glyphs). */
export function countGlyphs(frame: Frame, glyphs: readonly string[] = BODY_GLYPHS): number {
  const set = new Set(glyphs);
  let n = 0;
  for (const row of frame) for (const ch of row) if (set.has(ch)) n += 1;
  return n;
}

/**
 * Plain-text rendering, for test output and eyeballing. Transparent pixels become a
 * space so the silhouette is readable in a terminal.
 */
export function ascii(frame: Frame): string {
  return frame.map((row) => row.replace(/\./g, ' ')).join('\n');
}
