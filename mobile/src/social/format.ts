import type { Cursor, FeedItem, FeedPage, KudosState, Visibility } from '../data/api';

/**
 * Pure helpers for the social screens. Nothing here touches the network or React, so
 * `__tests__/social.test.ts` can pin every rule down in bun.
 */

/**
 * The keyset cursor for the page after this one, or null on the last page.
 *
 * Both fields travel together: `next_before_id` is the tiebreaker for posts committed in
 * the same microsecond, and the server only reads it alongside `before`. A page shorter
 * than the limit has both null, which is the stop signal — not an empty `items`, which a
 * page can also be when everything in it was deleted between requests.
 */
export function nextCursor(page: Pick<FeedPage, 'next_before' | 'next_before_id'>): Cursor | null {
  if (!page.next_before) return null;
  return { before: page.next_before, beforeId: page.next_before_id ?? null };
}

/**
 * "just now", "4m", "3h", "2d", then a short date. Strava-terse: the feed row has one
 * line for author · time · repo and a full sentence there reads as clutter.
 */
export function relativeTime(iso: string, now: number = Date.now()): string {
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return '';
  const s = Math.max(0, Math.round((now - t) / 1000));
  if (s < 60) return 'just now';
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h`;
  const d = Math.floor(h / 24);
  if (d < 7) return `${d}d`;
  const then = new Date(t);
  const sameYear = then.getFullYear() === new Date(now).getFullYear();
  return then.toLocaleDateString(undefined, {
    month: 'short',
    day: 'numeric',
    ...(sameYear ? {} : { year: 'numeric' }),
  });
}

export const VISIBILITIES: readonly Visibility[] = ['private', 'followers', 'public'];

export function visibilityLabel(v: Visibility): string {
  switch (v) {
    case 'private':
      return 'Private';
    case 'followers':
      return 'Followers';
    case 'public':
      return 'Public';
  }
}

/**
 * A join code the way the server normalises it: uppercase, non-alphanumerics dropped, and
 * `XXXX-XXXX` when eight characters remain. Anything else is passed through trimmed and
 * uppercased so the server's 404 says "no faction matches" rather than the phone guessing.
 */
export function normalizeFactionCode(code: string): string {
  const raw = code.toUpperCase().replace(/[^A-Z0-9]/g, '');
  return raw.length === 8 ? `${raw.slice(0, 4)}-${raw.slice(4)}` : code.trim().toUpperCase();
}

/**
 * Optimistic kudos toggle. The count never goes below zero, and a second tap while the
 * first request is in flight is a plain toggle back, so the two cancel visually and the
 * final server state (`applyKudos`) wins.
 */
export function toggleKudos<T extends KudosState>(item: T): T {
  const on = !item.you_kudosed;
  return {
    ...item,
    you_kudosed: on,
    kudos_count: Math.max(0, item.kudos_count + (on ? 1 : -1)),
  };
}

/** The server's answer replaces the guess. */
export function applyKudos<T extends KudosState>(item: T, state: KudosState): T {
  return { ...item, kudos_count: state.kudos_count, you_kudosed: state.you_kudosed };
}

/** Replace one item in a list by id; unchanged when the id is absent. */
export function replaceItem<T extends { id: string }>(items: T[], next: T): T[] {
  return items.map((it) => (it.id === next.id ? next : it));
}

/** The display name when there is one, else the handle, else a placeholder. */
export function authorName(a: { handle: string | null; display_name: string | null }): string {
  return a.handle ?? a.display_name ?? 'someone';
}

/** What the feed row says about the repo: public name, or nothing at all. */
export function repoLine(item: Pick<FeedItem, 'session'>): string | null {
  return item.session.repo_name ?? null;
}
