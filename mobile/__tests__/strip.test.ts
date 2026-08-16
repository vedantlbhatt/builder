/**
 * The cross-language gate.
 *
 * These fixtures are decoded by the Swift suite too, from the same JSON files, asserting
 * the same expected values. Without that, the Mac and the phone can disagree about what
 * a session looked like while both appear to work — a swapped class ordinal produces a
 * strip that is plausible, non-empty, and completely wrong.
 */

import { describe, expect, test } from 'bun:test';
import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';

import {
  COLUMNS,
  CLASS_WEIGHT,
  DENSITY_ALPHAS,
  MARK_DEDUPE_MIN_PX,
  SPEC_VERSION,
  StripClass,
  StripMarkKind,
  resample,
} from '../src/generated/strip';
import {
  classShare,
  decodeColumns,
  decodeMarks,
  decodeStrip,
  layoutMarks,
  resampleColumns,
  unpackByte,
  StripDecodeError,
} from '../src/strip/decode';

const FIXTURE_DIR = join(import.meta.dir, '..', '..', 'spec', 'fixtures');

interface Fixture {
  name: string;
  spec_version: number;
  span_ms: number;
  cols_b64: string;
  marks: number[][];
  expected_class_per_column: number[];
  expected_density_per_column: number[];
  expected_resampled: Record<string, number[]>;
}

const fixtures: Fixture[] = readdirSync(FIXTURE_DIR)
  .filter((f) => f.startsWith('strip_') && f.endsWith('.json'))
  .map((f) => JSON.parse(readFileSync(join(FIXTURE_DIR, f), 'utf8')));

describe('generated spec constants', () => {
  test('ordinals are wire format and must not drift', () => {
    // If these ever change, every stored strip and every already-shared card silently
    // re-colours. They are asserted literally, in both languages, on purpose.
    expect(StripClass.idle).toBe(0);
    expect(StripClass.prompting).toBe(1);
    expect(StripClass.agent).toBe(2);
    expect(StripClass.human_edit).toBe(3);
    expect(StripMarkKind.prompt).toBe(0);
    expect(StripMarkKind.commit).toBe(1);
    expect(StripMarkKind.compact).toBe(2);
    expect(COLUMNS).toBe(1024);
    expect(SPEC_VERSION).toBe(1);
  });

  test('prompting outweighs agent, so the human stays visible', () => {
    // Prompts are ~6% of events. Under raw argmax the agent takes every mixed column and
    // the person vanishes from their own timeline.
    expect(CLASS_WEIGHT[StripClass.prompting]!).toBeGreaterThan(CLASS_WEIGHT[StripClass.agent]!);
    expect(CLASS_WEIGHT[StripClass.human_edit]!).toBeGreaterThan(CLASS_WEIGHT[StripClass.agent]!);
  });

  test('density floor keeps the identity colour recognisable', () => {
    // Raised from 0.45 after the first real render came out muddy brown: at 4.2s per
    // column most columns land in the lowest bucket, so a low floor dilutes the whole bar.
    expect(DENSITY_ALPHAS[0]!).toBeGreaterThanOrEqual(0.7);
    expect(DENSITY_ALPHAS[DENSITY_ALPHAS.length - 1]).toBe(1);
  });
});

describe('byte layout', () => {
  test('packs and unpacks every class and density', () => {
    for (let k = 0; k <= 3; k++) {
      for (let d = 0; d <= 3; d++) {
        const byte = (k & 0b11) | ((d & 0b11) << 2);
        const out = unpackByte(byte);
        expect(out.klass).toBe(k as StripClass);
        expect(out.density).toBe(d);
        expect(byte >> 4).toBe(0);
      }
    }
  });

  test('rejects a strip with reserved bits set', () => {
    // The reserved nibble is the only expansion room the format has. A non-zero one means
    // a newer client wrote a field this build cannot interpret.
    const bytes = new Uint8Array(COLUMNS).fill(0b0000_0010);
    bytes[3] = 0b0001_0010;
    const b64 = btoa(String.fromCharCode(...bytes));
    expect(() => decodeStrip(b64)).toThrow(StripDecodeError);
  });

  test('rejects a strip of the wrong length', () => {
    const bytes = new Uint8Array(512);
    const b64 = btoa(String.fromCharCode(...bytes));
    expect(() => decodeStrip(b64)).toThrow(StripDecodeError);
  });
});

describe('golden fixtures — must match the Swift suite exactly', () => {
  test('there are fixtures to check', () => {
    expect(fixtures.length).toBeGreaterThan(0);
  });

  for (const f of fixtures) {
    test(`${f.name}: decodes to the expected class per column`, () => {
      const cols = decodeColumns(f.cols_b64);
      expect(cols.length).toBe(COLUMNS);
      expect(cols.map((c) => c.klass as number)).toEqual(f.expected_class_per_column);
    });

    test(`${f.name}: decodes to the expected density per column`, () => {
      const cols = decodeColumns(f.cols_b64);
      expect(cols.map((c) => c.density)).toEqual(f.expected_density_per_column);
    });

    test(`${f.name}: resamples identically at every width`, () => {
      const bytes = decodeStrip(f.cols_b64);
      for (const [width, expected] of Object.entries(f.expected_resampled)) {
        expect(Array.from(resample(bytes, Number(width)))).toEqual(expected);
      }
    });

    test(`${f.name}: resampling never invents a value`, () => {
      const bytes = decodeStrip(f.cols_b64);
      const source = new Set(bytes);
      // Nearest-neighbour, never a box filter: averaging categorical ordinals would
      // produce classes that were never in the data.
      for (const width of [7, 37, 400, 999]) {
        for (const col of resampleColumns(bytes, width)) {
          const byte = (col.klass & 0b11) | ((col.density & 0b11) << 2);
          expect(source.has(byte)).toBe(true);
        }
      }
    });
  }
});

describe('marks', () => {
  test('decodes and sorts the compact wire form', () => {
    const marks = decodeMarks([
      [5000, 0],
      [100, 2],
      [900, 0],
    ]);
    expect(marks.map((m) => m.ms)).toEqual([100, 900, 5000]);
  });

  test('ignores malformed entries rather than throwing', () => {
    // A single bad mark must not take down a session's whole render.
    expect(decodeMarks([[1], 'nope', null, [10, 0]]).length).toBe(1);
    expect(decodeMarks(undefined)).toEqual([]);
  });

  test('a prompt survives collision with a compaction marker', () => {
    // One is the person, the other is bookkeeping. When they land on the same pixel the
    // person wins, or the human disappears from their own timeline at small widths.
    const laid = layoutMarks(
      [
        { ms: 1000, kind: StripMarkKind.compact },
        { ms: 1001, kind: StripMarkKind.prompt },
      ],
      100_000,
      400,
      MARK_DEDUPE_MIN_PX
    );
    expect(laid.length).toBe(1);
    expect(laid[0]!.kind).toBe(StripMarkKind.prompt);
  });

  test('marks are never resampled away', () => {
    // The whole reason marks live outside the column array: a five-second prompt in a
    // six-hour session occupies a fraction of one column, and a segment-list format
    // loses it entirely.
    const laid = layoutMarks(
      [{ ms: 5_000, kind: StripMarkKind.prompt }],
      6 * 60 * 60 * 1000,
      320,
      MARK_DEDUPE_MIN_PX
    );
    expect(laid.length).toBe(1);
  });
});

describe('class share', () => {
  test('sums to one', () => {
    const cols = decodeColumns(fixtures[0]!.cols_b64);
    const share = classShare(cols);
    const total = Object.values(share).reduce((a, b) => a + b, 0);
    expect(total).toBeCloseTo(1, 5);
  });
});
