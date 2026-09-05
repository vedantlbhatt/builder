/**
 * The pure rules under the social screens. No network, no React: the cursor that decides
 * whether to page again, the terse time, the visibility label, the join-code shape the
 * server expects, and the optimistic kudos reducer that must round-trip exactly.
 */

import { describe, expect, test } from 'bun:test';

import {
  applyKudos,
  nextCursor,
  normalizeFactionCode,
  relativeTime,
  replaceItem,
  toggleKudos,
  visibilityLabel,
} from '../src/social/format';

describe('nextCursor', () => {
  test('carries both keyset fields from a full page', () => {
    expect(
      nextCursor({ next_before: '2026-08-15T10:00:00+00:00', next_before_id: 'abc' })
    ).toEqual({ before: '2026-08-15T10:00:00+00:00', beforeId: 'abc' });
  });

  test('is null on the last page, even when items were present', () => {
    expect(nextCursor({ next_before: null, next_before_id: null })).toBeNull();
  });

  test('tolerates a server that sends the timestamp without the tiebreaker', () => {
    expect(nextCursor({ next_before: '2026-08-15T10:00:00+00:00', next_before_id: null })).toEqual({
      before: '2026-08-15T10:00:00+00:00',
      beforeId: null,
    });
  });
});

describe('relativeTime', () => {
  const now = Date.parse('2026-09-05T12:00:00Z');
  const at = (secondsAgo: number) => new Date(now - secondsAgo * 1000).toISOString();

  test('buckets', () => {
    expect(relativeTime(at(5), now)).toBe('just now');
    expect(relativeTime(at(59), now)).toBe('just now');
    expect(relativeTime(at(60), now)).toBe('1m');
    expect(relativeTime(at(45 * 60), now)).toBe('45m');
    expect(relativeTime(at(3 * 3600 + 10), now)).toBe('3h');
    expect(relativeTime(at(2 * 86400), now)).toBe('2d');
  });

  test('a week or older is a short date, not "9d"', () => {
    const s = relativeTime(at(9 * 86400), now);
    expect(s).not.toMatch(/^\d+d$/);
    expect(s.length).toBeGreaterThan(0);
  });

  test('a clock ahead of the server never reads negative', () => {
    expect(relativeTime(at(-120), now)).toBe('just now');
  });

  test('garbage in, empty out', () => {
    expect(relativeTime('not a date', now)).toBe('');
  });
});

describe('visibilityLabel', () => {
  test('the three levels', () => {
    expect(visibilityLabel('private')).toBe('Private');
    expect(visibilityLabel('followers')).toBe('Followers');
    expect(visibilityLabel('public')).toBe('Public');
  });
});

describe('normalizeFactionCode', () => {
  test('matches the server: uppercase, strip, hyphenate at eight', () => {
    expect(normalizeFactionCode('abcd1234')).toBe('ABCD-1234');
    expect(normalizeFactionCode(' ab-cd 12.34 ')).toBe('ABCD-1234');
    expect(normalizeFactionCode('ABCD-1234')).toBe('ABCD-1234');
  });

  test('anything else is passed through trimmed and uppercased for the server to reject', () => {
    expect(normalizeFactionCode(' abc ')).toBe('ABC');
    expect(normalizeFactionCode('abcd12345')).toBe('ABCD12345');
  });
});

describe('kudos reducer', () => {
  const item = { id: 'p1', kudos_count: 3, you_kudosed: false, caption: 'x' };

  test('toggle on adds one, toggle off removes it — a round trip is the identity', () => {
    const on = toggleKudos(item);
    expect(on).toEqual({ ...item, kudos_count: 4, you_kudosed: true });
    expect(toggleKudos(on)).toEqual(item);
  });

  test('never below zero, even from an inconsistent starting state', () => {
    expect(toggleKudos({ ...item, kudos_count: 0, you_kudosed: true }).kudos_count).toBe(0);
  });

  test('the server answer replaces the guess and keeps the rest of the row', () => {
    const guessed = toggleKudos(item);
    expect(applyKudos(guessed, { kudos_count: 9, you_kudosed: true })).toEqual({
      ...item,
      kudos_count: 9,
      you_kudosed: true,
    });
  });

  test('replaceItem swaps by id and leaves an unknown id alone', () => {
    const list = [item, { ...item, id: 'p2' }];
    const next = replaceItem(list, { ...item, kudos_count: 7 });
    expect(next[0]?.kudos_count).toBe(7);
    expect(next[1]).toBe(list[1]);
    expect(replaceItem(list, { ...item, id: 'p9' })).toEqual(list);
  });
});
