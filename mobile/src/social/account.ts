import type { Faction, MyFaction } from '../data/api';

/**
 * Pure rules for the viewer's own account: the handle the phone will let a person type,
 * the sentence a 409 becomes, and how a create/join answer folds into the list that
 * `GET /v1/factions/mine` owns. Nothing here touches the network or React, so
 * `__tests__/account.test.ts` pins every rule down in bun.
 *
 * The handle rule mirrors `server/builder/routes/users.py` (`_HANDLE_RE`,
 * `RESERVED_HANDLES`, `HANDLE_CHANGE_INTERVAL`). The server enforces it; the phone repeats
 * it so the person hears "3-24 characters" as they type, not after a 422.
 */

export const HANDLE_MIN = 3;
export const HANDLE_MAX = 24;
export const HANDLE_RE = /^[a-z0-9_]{3,24}$/;
export const RESERVED_HANDLES: ReadonlySet<string> = new Set([
  'me',
  'admin',
  'builder',
  'api',
  'feed',
  'settings',
  'pair',
  'u',
]);
export const HANDLE_CHANGE_DAYS = 30;
export const MAX_DISPLAY_NAME = 40;

/** The handle the way the server stores it: trimmed and lowercased. */
export function normalizeHandle(raw: string): string {
  return raw.trim().toLowerCase();
}

/**
 * Why `raw` would be refused, or null when it would pass. Empty input is "nothing typed
 * yet", not an error — the field shows no complaint until a character is in it.
 */
export function handleProblem(raw: string): string | null {
  const h = normalizeHandle(raw);
  if (h.length === 0) return null;
  if (h.length < HANDLE_MIN) return `at least ${HANDLE_MIN} characters`;
  if (h.length > HANDLE_MAX) return `at most ${HANDLE_MAX} characters`;
  if (!HANDLE_RE.test(h)) return 'only a-z, 0-9 and _';
  if (RESERVED_HANDLES.has(h)) return `"${h}" is reserved`;
  return null;
}

/** True when `raw` names a handle the server would accept. */
export function isValidHandle(raw: string): boolean {
  const h = normalizeHandle(raw);
  return h.length > 0 && handleProblem(raw) === null;
}

/** Why a display name would be refused, or null. Whitespace-only clears the name server-side. */
export function displayNameProblem(raw: string): string | null {
  return raw.trim().length > MAX_DISPLAY_NAME ? `at most ${MAX_DISPLAY_NAME} characters` : null;
}

/** The instant a 409 "next change allowed at …" names, or null for any other detail. */
export function handleChangeAllowedAt(detail: string): Date | null {
  const m = /allowed at (\S+)/.exec(detail);
  if (!m) return null;
  const t = Date.parse(m[1]!);
  return Number.isNaN(t) ? null : new Date(t);
}

function defaultDateFormat(d: Date): string {
  return d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
}

/**
 * The 409 from `PATCH /v1/users/me` as a sentence.
 *
 * Two conflicts share the status. "the handle 'x' is taken" is already a sentence and is
 * passed through. The 30-day window's detail ends in a raw ISO instant with microseconds
 * and an offset; that becomes a local date, because the person wants to know which DAY to
 * come back, not the microsecond. The formatter is injectable so the test does not depend
 * on bun's locale.
 */
export function describeHandleConflict(
  detail: string,
  format: (d: Date) => string = defaultDateFormat
): string {
  const at = handleChangeAllowedAt(detail);
  if (!at) return detail;
  return `Handles change once every ${HANDLE_CHANGE_DAYS} days. You can change yours again on ${format(at)}.`;
}

// ------------------------------------------------------------------- factions

/**
 * Fold a create/join answer (a `Faction`, which carries no roster count or join date)
 * into the list `/v1/factions/mine` last returned, so the new card appears before the
 * re-fetch lands. Appended, because the server orders by `joined_at` ascending and this
 * one was joined just now; replaced in place when the slug is already there (joining a
 * faction you are in is a no-op server-side and must not duplicate the card).
 */
export function upsertMine(list: readonly MyFaction[], f: Faction, now: Date = new Date()): MyFaction[] {
  const existing = list.find((m) => m.slug === f.slug);
  if (existing) {
    return list.map((m) => (m.slug === f.slug ? { ...m, name: f.name, open: f.open } : m));
  }
  return [
    ...list,
    {
      slug: f.slug,
      name: f.name,
      role: f.role ?? 'member',
      share_hours: true,
      open: f.open,
      member_count: 1,
      joined_at: now.toISOString(),
    },
  ];
}

/**
 * Keep only the per-slug state whose faction is still in `mine`. The boards map is keyed
 * by slug and a membership that ended server-side must take its board with it, or a
 * stale week stays on screen with no card title to explain it.
 */
export function pruneBySlug<T>(mine: readonly Pick<MyFaction, 'slug'>[], bySlug: Record<string, T>): Record<string, T> {
  const keep = new Set(mine.map((m) => m.slug));
  const out: Record<string, T> = {};
  for (const [slug, v] of Object.entries(bySlug)) if (keep.has(slug)) out[slug] = v;
  return out;
}
