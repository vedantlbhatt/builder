/**
 * The pure rules under the social screens. No network, no React: the cursor that decides
 * whether to page again, the terse time, the visibility label, the join-code shape the
 * server expects, and the optimistic kudos reducer that must round-trip exactly.
 */

import { describe, expect, test } from 'bun:test';

import type { FeedItem, FeedPage } from '../src/data/api';
import {
  applyKudos,
  isAlreadySharedConflict,
  nextCursor,
  normalizeFactionCode,
  relativeTime,
  replaceItem,
  revertKudos,
  toggleKudos,
  repoLine,
  updateKudos,
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

describe('kudos rollback touches only the two kudos fields', () => {
  const tapped = { id: 'p1', kudos_count: 3, you_kudosed: false, comment_count: 2 };

  test('revertKudos keeps a comment_count that moved while the request was in flight', () => {
    const guessed = toggleKudos(tapped);
    const commented = { ...guessed, comment_count: 3 };
    expect(revertKudos(commented, tapped)).toEqual({ ...tapped, comment_count: 3 });
  });

  test('updateKudos changes one row by id and leaves the rest of that row alone', () => {
    const refreshed = [
      { ...tapped, kudos_count: 4, you_kudosed: true, comment_count: 9 },
      { ...tapped, id: 'p2' },
    ];
    const out = updateKudos(refreshed, 'p1', tapped);
    expect(out[0]).toEqual({ ...tapped, comment_count: 9 });
    expect(out[1]).toBe(refreshed[1]);
  });

  test('updateKudos never resurrects a row a refresh dropped', () => {
    const list = [{ ...tapped, id: 'p2' }];
    expect(updateKudos(list, 'p1', { kudos_count: 1, you_kudosed: true })).toEqual(list);
  });
});

describe('isAlreadySharedConflict', () => {
  test('the "already shared" 409 and nothing else', () => {
    expect(isAlreadySharedConflict({ status: 409, message: 'this session is already shared' })).toBe(true);
    expect(isAlreadySharedConflict({ status: 409, message: 'a live session cannot be shared until it finishes' })).toBe(false);
    expect(isAlreadySharedConflict({ status: 403, message: 'this session is already shared' })).toBe(false);
    expect(isAlreadySharedConflict(new Error('already shared'))).toBe(false);
    expect(isAlreadySharedConflict(null)).toBe(false);
  });
});


describe('a build post is a post about a project, not about a sitting', () => {
  /**
   * `posts.session_id` is nullable since 0017. A build post is about a PROJECT across as
   * many sittings as it took, so it has no session, no duration and no timeline — and the
   * feed row has to branch rather than read `item.session.repo_name` and crash, or show a
   * "0s" duration for a post that was never about a length of time.
   */
  const buildPost = {
    session: null,
    project: 'builder',
  } as unknown as FeedItem;

  const sessionPost = {
    session: { repo_name: 'ridegt' },
    project: null,
  } as unknown as FeedItem;

  test('a build post is labelled with its project', () => {
    expect(repoLine(buildPost)).toBe('builder');
  });

  test('a session post is still labelled from its session', () => {
    expect(repoLine(sessionPost)).toBe('ridegt');
  });

  test('a private repo has no public name on either kind, and gets no label', () => {
    // A hash would read as a bug. Nothing is the honest answer.
    expect(repoLine({ session: { repo_name: null }, project: null } as unknown as FeedItem)).toBeNull();
    expect(repoLine({ session: null, project: null } as unknown as FeedItem)).toBeNull();
  });

  test('a server older than the build post omits the key entirely', () => {
    expect(repoLine({ session: { repo_name: 'builder' } } as unknown as FeedItem)).toBe('builder');
  });
});
