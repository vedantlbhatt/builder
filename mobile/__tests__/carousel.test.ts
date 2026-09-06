import { describe, expect, test } from 'bun:test';

import { ANIMALS, type Animal } from '../src/pixel/animals';
import { indexOf, openOn, step, view } from '../src/pixel/carousel';

/**
 * Wrapping and identity are the only things that can be wrong here, and both are
 * invisible in a screenshot: an off-by-one at the seam means one chevron press lands on
 * the wrong creature, which a person hits on their first pass through the pack.
 */
describe('stepping', () => {
  test('the right chevron moves forward one', () => {
    expect(step('crab', 1)).toBe(ANIMALS[1]!);
  });

  test('the left chevron moves back one', () => {
    expect(step(ANIMALS[1]!, -1)).toBe('crab');
  });

  test('the right chevron on the last creature wraps to the first', () => {
    expect(step(ANIMALS[ANIMALS.length - 1]!, 1)).toBe(ANIMALS[0]!);
  });

  test('the left chevron on the first creature wraps to the last', () => {
    // `-1 % 8` is `-1` in JavaScript, so the naive version indexes off the front.
    expect(step(ANIMALS[0]!, -1)).toBe(ANIMALS[ANIMALS.length - 1]!);
  });

  test('one press each way comes back to where it started, from every creature', () => {
    for (const a of ANIMALS) {
      expect(step(step(a, 1), -1)).toBe(a);
      expect(step(step(a, -1), 1)).toBe(a);
    }
  });

  test('pressing forward once per creature visits every one and comes home', () => {
    let at: Animal = ANIMALS[0]!;
    const seen = new Set<Animal>([at]);
    for (let i = 0; i < ANIMALS.length - 1; i++) {
      at = step(at, 1);
      seen.add(at);
    }
    expect(seen.size).toBe(ANIMALS.length);
    expect(step(at, 1)).toBe(ANIMALS[0]!);
  });

  test('an unknown creature does not crash the picker', () => {
    expect(step('philosopher', 1)).toBe(ANIMALS[1]!);
    expect(step(null, 1)).toBe(ANIMALS[1]!);
    expect(indexOf(undefined)).toBe(0);
  });
});

describe('the view', () => {
  test('the position is one based because nobody counts creatures from zero', () => {
    expect(view(ANIMALS[0]!).position).toBe(1);
    expect(view(ANIMALS[2]!).position).toBe(3);
    expect(view(ANIMALS[0]!).total).toBe(ANIMALS.length);
  });

  test('it carries both neighbours so the chevrons can preview them', () => {
    const v = view(ANIMALS[0]!);
    expect(v.previous).toBe(ANIMALS[ANIMALS.length - 1]!);
    expect(v.next).toBe(ANIMALS[1]!);
  });

  test('every creature has a label', () => {
    for (const a of ANIMALS) expect(view(a).label.length).toBeGreaterThan(0);
  });

  test('an unknown creature falls back rather than rendering nothing', () => {
    expect(view('nonsense').animal).toBe(ANIMALS[0]!);
  });
});

describe('what it opens on', () => {
  test('their own choice wins', () => {
    expect(openOn('owl', 'crab')).toBe('owl');
  });

  test('the archetype creature when they have not chosen', () => {
    // Opening on the pack's first creature would throw away the only personalised thing
    // the app knows at that moment.
    expect(openOn(null, 'whale')).toBe('whale');
  });

  test('the first in the pack when there is neither', () => {
    expect(openOn(null, null)).toBe(ANIMALS[0]!);
  });

  test('nonsense on either side still opens on something real', () => {
    expect(openOn('philosopher', 'wizard')).toBe(ANIMALS[0]!);
  });
});
