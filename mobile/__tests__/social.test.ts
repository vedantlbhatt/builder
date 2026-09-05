/**
 * The pure rules under the social screens. No network, no React: the cursor that decides
 * whether to page again, the terse time, the visibility label, the join-code shape the
 * server expects, and the optimistic kudos reducer that must round-trip exactly.
 */

import { describe, expect, test } from 'bun:test';

import type { Cursor, FeedItem, FeedPage } from '../src/data/api';
import {
  applyKudos,
  findPostForSession,
  isAlreadySharedConflict,
  nextCursor,
  normalizeFactionCode,
  OWN_POST_LOOKUP_MAX_PAGES,
  OWN_POST_LOOKUP_SLACK_MS,
  relativeTime,
  replaceItem,
  revertKudos,
  toggleKudos,
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

describe('findPostForSession', () => {
  const ended = '2026-09-01T12:00:00.000Z';
  const item = (id: string, sessionId: string, createdAt: string) =>
    ({ id, session: { id: sessionId }, created_at: createdAt }) as unknown as FeedItem;

  /** A loader over fixed pages that records the cursors it was asked for. */
  function pages(all: FeedItem[][]) {
    const asked: (Cursor | null)[] = [];
    const load = async (cursor: Cursor | null): Promise<FeedPage> => {
      asked.push(cursor);
      const i = cursor ? Number(cursor.beforeId) : 0;
      const items = all[i] ?? [];
      const more = i + 1 < all.length;
      return {
        items,
        next_before: more ? items[items.length - 1]!.created_at : null,
        next_before_id: more ? String(i + 1) : null,
      };
    };
    return { load, asked };
  }

  test('finds the post on a later page and stops there', async () => {
    const { load, asked } = pages([
      [item('a', 'other', '2026-09-03T00:00:00Z')],
      [item('b', 'mine', '2026-09-02T00:00:00Z')],
      [item('c', 'older', '2026-09-01T00:00:00Z')],
    ]);
    const found = await findPostForSession(load, 'mine', ended);
    expect(found?.id).toBe('b');
    expect(asked.length).toBe(2);
  });

  test('stops once a page is older than the session ended (a post cannot predate its session)', async () => {
    const tooOld = new Date(Date.parse(ended) - OWN_POST_LOOKUP_SLACK_MS - 1000).toISOString();
    const { load, asked } = pages([
      [item('a', 'other', '2026-09-03T00:00:00Z'), item('b', 'other2', tooOld)],
      [item('c', 'mine', '2026-08-01T00:00:00Z')],
    ]);
    expect(await findPostForSession(load, 'mine', ended)).toBeNull();
    expect(asked.length).toBe(1);
  });

  test('a post within the slack of ended_at is still reached', async () => {
    const skewed = new Date(Date.parse(ended) - OWN_POST_LOOKUP_SLACK_MS + 1000).toISOString();
    const { load } = pages([[item('a', 'other', '2026-09-03T00:00:00Z')], [item('b', 'mine', skewed)]]);
    expect((await findPostForSession(load, 'mine', ended))?.id).toBe('b');
  });

  test('stops at the last page and at the page cap', async () => {
    const short = pages([[item('a', 'other', '2026-09-03T00:00:00Z')]]);
    expect(await findPostForSession(short.load, 'mine', ended)).toBeNull();
    expect(short.asked.length).toBe(1);

    const endless = pages(
      Array.from({ length: OWN_POST_LOOKUP_MAX_PAGES + 3 }, (_, i) => [
        item(`p${i}`, `s${i}`, '2026-09-03T00:00:00Z'),
      ])
    );
    expect(await findPostForSession(endless.load, 'mine', ended)).toBeNull();
    expect(endless.asked.length).toBe(OWN_POST_LOOKUP_MAX_PAGES);
  });

  test('an unparseable ended_at disables the date stop but not the cap', async () => {
    const { load, asked } = pages([
      [item('a', 'other', '2020-01-01T00:00:00Z')],
      [item('b', 'mine', '2019-01-01T00:00:00Z')],
    ]);
    expect((await findPostForSession(load, 'mine', 'not a date'))?.id).toBe('b');
    expect(asked.length).toBe(2);
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
