/**
 * The pure rules behind Settings → Profile and the factions list. The handle rule is a
 * copy of the server's (`server/builder/routes/users.py`), so the cases here are the
 * server's cases: length, alphabet, case-folding, the reserved words, and the two 409s
 * that share a status but mean different things.
 */

import { describe, expect, test } from 'bun:test';

import type { Faction, MyFaction } from '../src/data/api';
import {
  describeHandleConflict,
  displayNameProblem,
  handleChangeAllowedAt,
  handleProblem,
  isValidHandle,
  normalizeHandle,
  pruneBySlug,
  RESERVED_HANDLES,
  upsertMine,
} from '../src/social/account';

describe('handle validation', () => {
  test('normalises the way the server does: trimmed, lowercased', () => {
    expect(normalizeHandle('  Alice_01 ')).toBe('alice_01');
  });

  test('accepts 3-24 of a-z, 0-9 and underscore', () => {
    expect(isValidHandle('abc')).toBe(true);
    expect(isValidHandle('a'.repeat(24))).toBe(true);
    expect(isValidHandle('ved_42')).toBe(true);
    expect(isValidHandle('Alice')).toBe(true); // lowercased before the check
    expect(handleProblem('abc')).toBeNull();
  });

  test('names the rule that failed', () => {
    expect(handleProblem('ab')).toMatch(/at least 3/);
    expect(handleProblem('a'.repeat(25))).toMatch(/at most 24/);
    expect(handleProblem('al-ice')).toMatch(/a-z, 0-9 and _/);
    expect(handleProblem('al ice')).toMatch(/a-z, 0-9 and _/);
    expect(handleProblem('ålice')).toMatch(/a-z, 0-9 and _/);
    expect(isValidHandle('ab')).toBe(false);
  });

  test('empty is "nothing typed yet", not a complaint, and not valid either', () => {
    expect(handleProblem('')).toBeNull();
    expect(handleProblem('   ')).toBeNull();
    expect(isValidHandle('')).toBe(false);
  });

  test('refuses every reserved word, in any case; length is checked first, as on the server', () => {
    for (const h of RESERVED_HANDLES) {
      expect(handleProblem(h)).not.toBeNull();
      expect(handleProblem(h.toUpperCase())).not.toBeNull();
      // `me` and `u` are reserved AND too short; the server's regex fires before its
      // reserved list, so the phone says "at least 3" for those, "reserved" for the rest.
      if (h.length >= 3) expect(handleProblem(h.toUpperCase())).toMatch(/reserved/);
    }
    expect(handleProblem('admin')).toMatch(/"admin" is reserved/);
    expect(handleProblem('me')).toMatch(/at least 3/);
    expect(RESERVED_HANDLES.has('u')).toBe(true);
  });

  test('display name: at most 40 characters after trimming; blank is allowed (it clears)', () => {
    expect(displayNameProblem('')).toBeNull();
    expect(displayNameProblem('  Vedant  ')).toBeNull();
    expect(displayNameProblem('x'.repeat(40))).toBeNull();
    expect(displayNameProblem('x'.repeat(41))).toMatch(/at most 40/);
    expect(displayNameProblem(`${'x'.repeat(40)}   `)).toBeNull();
  });
});

describe('409 from PATCH /users/me', () => {
  const windowDetail =
    'handle can be changed once every 30 days; next change allowed at 2026-10-05T14:03:22.123456+00:00';

  test('reads the instant the window opens out of the server detail', () => {
    const at = handleChangeAllowedAt(windowDetail);
    expect(at?.toISOString()).toBe('2026-10-05T14:03:22.123Z');
  });

  test('turns the window detail into a sentence with the allowed date', () => {
    const msg = describeHandleConflict(windowDetail, (d) => d.toISOString().slice(0, 10));
    expect(msg).toBe('Handles change once every 30 days. You can change yours again on 2026-10-05.');
    expect(msg).not.toContain('123456');
  });

  test('passes the "taken" conflict through unchanged', () => {
    expect(describeHandleConflict("the handle 'alice' is taken")).toBe("the handle 'alice' is taken");
    expect(handleChangeAllowedAt("the handle 'alice' is taken")).toBeNull();
  });

  test('an unparseable date falls back to the raw detail rather than "Invalid Date"', () => {
    const odd = 'next change allowed at sometime';
    expect(handleChangeAllowedAt(odd)).toBeNull();
    expect(describeHandleConflict(odd)).toBe(odd);
  });
});

describe('factions-mine merge', () => {
  const mine: MyFaction[] = [
    { slug: 'night', name: 'Night Shift', role: 'member', share_hours: true, open: false, member_count: 4, joined_at: '2026-08-01T00:00:00+00:00' },
    { slug: 'gt', name: 'GT', role: 'admin', share_hours: false, open: true, member_count: 9, joined_at: '2026-08-02T00:00:00+00:00' },
  ];
  const created: Faction = { slug: 'new', name: 'New One', open: false, tz: 'UTC', role: 'admin', join_code: 'ABCD-EFGH' };

  test('a created or joined faction is appended (the server orders by joined_at)', () => {
    const now = new Date('2026-09-05T10:00:00Z');
    const out = upsertMine(mine, created, now);
    expect(out.map((m) => m.slug)).toEqual(['night', 'gt', 'new']);
    expect(out[2]).toEqual({
      slug: 'new',
      name: 'New One',
      role: 'admin',
      share_hours: true,
      open: false,
      member_count: 1,
      joined_at: '2026-09-05T10:00:00.000Z',
    });
    expect(mine).toHaveLength(2); // input untouched
  });

  test('re-joining a faction already listed updates it in place, never duplicates', () => {
    const again: Faction = { slug: 'gt', name: 'GT renamed', open: false, tz: 'UTC' };
    const out = upsertMine(mine, again);
    expect(out.map((m) => m.slug)).toEqual(['night', 'gt']);
    expect(out[1]).toMatchObject({ name: 'GT renamed', open: false, role: 'admin', member_count: 9 });
  });

  test('a join answer without a role is a member', () => {
    const joined: Faction = { slug: 'j', name: 'J', open: true, tz: 'UTC' };
    expect(upsertMine([], joined)[0]?.role).toBe('member');
  });

  test('pruneBySlug drops the per-slug state of factions the server no longer lists', () => {
    const boards = { night: 'b1', gt: 'b2', gone: 'b3' };
    expect(pruneBySlug(mine, boards)).toEqual({ night: 'b1', gt: 'b2' });
    expect(pruneBySlug([], boards)).toEqual({});
  });
});
