/**
 * Bit, the pixel mascot: frame geometry, glyph vocabulary, and the palette.
 *
 * A frame with one dropped character shifts the face sideways in that row; a frame with
 * an unknown glyph renders a hole. Neither crashes, both look wrong on a phone in a way
 * that is easy to miss on a diff and hard to unsee afterwards, so every frame is checked.
 */

import { describe, expect, test } from 'bun:test';

import {
  BODY_GLYPHS,
  GLYPHS,
  GRID,
  ascii,
  countGlyphs,
  isValidFrame,
  mirror,
  runsFor,
  validateFrame,
  type Frame,
} from '../src/pixel/frames';
import { BODY_DARK_FACTOR, scale, spritePalette } from '../src/pixel/palette';
import { SPRITES, SPRITE_STATES, framesFor } from '../src/pixel/sprites';

const BODY_TOLERANCE = 4;

describe('sprite states', () => {
  test('every declared state has frames and every table entry is declared', () => {
    expect(Object.keys(SPRITES).sort()).toEqual([...SPRITE_STATES].sort());
    expect(SPRITE_STATES).toEqual([
      'idle',
      'blink',
      'building',
      'sleeping',
      'celebrating',
      'thinking',
      'waving',
    ]);
  });

  for (const state of SPRITE_STATES) {
    describe(state, () => {
      const frames = framesFor(state);

      test('has 2 to 4 frames', () => {
        expect(frames.length).toBeGreaterThanOrEqual(2);
        expect(frames.length).toBeLessThanOrEqual(4);
      });

      test(`every frame is ${GRID}x${GRID} and uses only known glyphs`, () => {
        frames.forEach((frame, i) => {
          expect(validateFrame(frame), `${state}[${i}]\n${ascii(frame)}`).toEqual([]);
          expect(isValidFrame(frame)).toBe(true);
        });
      });

      test(`body pixel count is consistent within ±${BODY_TOLERANCE}`, () => {
        const counts = frames.map((f) => countGlyphs(f, BODY_GLYPHS));
        const min = Math.min(...counts);
        const max = Math.max(...counts);
        expect(max - min, `${state} body counts ${counts.join(', ')}`).toBeLessThanOrEqual(
          BODY_TOLERANCE
        );
      });

      test('every frame draws a body and no frame is a duplicate of its neighbour', () => {
        for (const f of frames) expect(countGlyphs(f, BODY_GLYPHS)).toBeGreaterThan(40);
        for (let i = 1; i < frames.length; i++) {
          expect(frames[i]!.join('\n')).not.toBe(frames[i - 1]!.join('\n'));
        }
      });
    });
  }

  test('the character is one character: every state keeps the same eye glyph count', () => {
    // Open eyes are 8 pixels (w+e), closed are 4. Nothing else is allowed.
    for (const state of SPRITE_STATES) {
      for (const f of framesFor(state)) {
        const eyePixels = countGlyphs(f, ['e']) + countGlyphs(f, ['w']) - sparks(f);
        expect([4, 8], `${state}\n${ascii(f)}`).toContain(eyePixels);
      }
    }
  });

  test('building frames read as hammering: the tool head is in every frame and moves', () => {
    const frames = framesFor('building');
    // Antenna bulb is 4 h pixels; the hammer head is 3 to 6 more (3x2 face-on, 1x3 edge-on).
    for (const f of frames) expect(countGlyphs(f, ['h'])).toBeGreaterThanOrEqual(7);
    const headRows = frames.map((f) => {
      let sum = 0;
      let n = 0;
      f.forEach((row, y) => {
        for (let x = 11; x < row.length; x++) if (row[x] === 'h') { sum += y; n += 1; }
      });
      return sum / n;
    });
    // Raised, level, struck: the head descends across the first three frames.
    expect(headRows[0]!).toBeLessThan(headRows[1]!);
    expect(headRows[1]!).toBeLessThan(headRows[2]!);
    // The strike frame has sparks; the raised frame has none.
    expect(sparks(framesFor('building')[2]!)).toBeGreaterThan(0);
    expect(sparks(framesFor('building')[0]!)).toBe(0);
  });

  test('sleeping frames have closed eyes and a z trail that grows', () => {
    const zs = framesFor('sleeping').map((f) => countGlyphs(f, ['z']));
    for (let i = 1; i < zs.length; i++) expect(zs[i]!).toBeGreaterThan(zs[i - 1]!);
    for (const f of framesFor('sleeping')) expect(countGlyphs(f, ['w'])).toBe(0);
  });

  test('blink starts from the idle pose', () => {
    expect(framesFor('blink')[0]).toBe(framesFor('idle')[0]);
  });
});

/** `w` pixels that are not eye highlights, i.e. sparks and confetti. */
function sparks(frame: Frame): number {
  let n = 0;
  frame.forEach((row) => {
    for (let x = 0; x < row.length; x++) {
      if (row[x] !== 'w') continue;
      const l = row[x - 1];
      const r = row[x + 1];
      // A highlight is always horizontally adjacent to the eye colour.
      if (l !== 'e' && r !== 'e') n += 1;
    }
  });
  return n;
}

describe('runsFor', () => {
  test('merges runs and preserves order', () => {
    expect(runsFor('..bbb.b')).toEqual([
      { start: 0, length: 2, ch: '.' },
      { start: 2, length: 3, ch: 'b' },
      { start: 5, length: 1, ch: '.' },
      { start: 6, length: 1, ch: 'b' },
    ]);
  });

  test('a uniform row is one run; an empty row is none', () => {
    expect(runsFor('bbbbbbbbbbbbbbbb')).toEqual([{ start: 0, length: 16, ch: 'b' }]);
    expect(runsFor('')).toEqual([]);
  });

  test('runs tile the row exactly', () => {
    for (const state of SPRITE_STATES) {
      for (const f of framesFor(state)) {
        for (const row of f) {
          const runs = runsFor(row);
          expect(runs.reduce((n, r) => n + r.length, 0)).toBe(row.length);
          let cursor = 0;
          for (const r of runs) {
            expect(r.start).toBe(cursor);
            expect(row.slice(r.start, r.start + r.length)).toBe(r.ch.repeat(r.length));
            cursor += r.length;
          }
          for (let i = 1; i < runs.length; i++) expect(runs[i]!.ch).not.toBe(runs[i - 1]!.ch);
        }
      }
    }
  });
});

describe('validateFrame', () => {
  test('rejects the wrong row count', () => {
    expect(validateFrame(['.'.repeat(16)])).toContain('expected 16 rows, got 1');
  });

  test('rejects a short row and an unknown glyph, naming the position', () => {
    const f = Array.from({ length: 16 }, () => '.'.repeat(16));
    f[3] = '.'.repeat(15);
    f[5] = '....X...........';
    const problems = validateFrame(f);
    expect(problems).toContain('row 3: expected 16 columns, got 15');
    expect(problems).toContain("row 5 col 4: unknown glyph 'X'");
    expect(problems).toHaveLength(2);
  });
});

describe('mirror', () => {
  test('is an involution on every sprite frame', () => {
    for (const state of SPRITE_STATES) {
      for (const f of framesFor(state)) {
        expect(mirror(mirror(f))).toEqual(f);
        expect(mirror(f)).not.toBe(f);
      }
    }
  });

  test('flips horizontally', () => {
    expect(mirror(['b...', '.dw.'])).toEqual(['...b', '.wd.']);
  });

  test('the idle pose is symmetric apart from the eye highlights', () => {
    const idle = framesFor('idle')[0]!;
    const noHighlight = idle.map((row) => row.replace(/w/g, 'e'));
    expect(mirror(noHighlight)).toEqual(noHighlight);
  });
});

describe('palette', () => {
  test('dark scheme yields six distinct hex colours, one per glyph', () => {
    const p = spritePalette('dark');
    expect(Object.keys(p).sort()).toEqual([...GLYPHS].sort());
    const values = Object.values(p);
    for (const v of values) expect(v).toMatch(/^#[0-9A-F]{6}$/i);
    expect(new Set(values).size).toBe(6);
  });

  test('light scheme is also six distinct colours and differs from dark where it should', () => {
    const light = spritePalette('light');
    const dark = spritePalette('dark');
    expect(new Set(Object.values(light)).size).toBe(6);
    expect(light.b).toBe(dark.b); // the amber is the amber in both schemes
    expect(light.e).not.toBe(dark.e);
    expect(light.w).not.toBe(dark.w);
  });

  test('body dark is the accent multiplied, and is still amber', () => {
    const p = spritePalette('dark');
    expect(p.d).toBe(scale(p.b, BODY_DARK_FACTOR));
    expect(scale('#FFB300', 0.62)).toBe('#9E6F00');
    expect(scale('#ffffff', 2)).toBe('#FFFFFF');
    expect(scale('#000000', 0.5)).toBe('#000000');
    expect(scale('not-a-colour', 0.5)).toBe('not-a-colour');
  });
});
