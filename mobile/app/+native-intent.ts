/**
 * Rewrites incoming system URLs before expo-router routes them.
 *
 * The Google redirect (`builder://auth/google#id_token=...`) is consumed by the Linking
 * listener in `_layout.tsx`; there is no `auth/google` route, so without this the router
 * would show "Unmatched route" over the top of a successful sign-in.
 *
 * `builder://session/<id>?recap=1` — the link in a completion push's data, the one the Mac
 * opens, the one a share sheet carries — routes to the session detail with the recap sheet
 * raised. The router would match `/session/<id>` on its own; the rewrite is what makes the
 * shapes people actually type or paste land on the same screen: the `/recap` suffix form,
 * a stray `session` without the leading slash, `builder:///` with three slashes.
 */
export function redirectSystemPath({ path }: { path: string; initial: boolean }): string {
  if (path.includes('auth/google')) return '/settings';
  return recapPath(path) ?? path;
}

/**
 * The normalised route for a session link, or null when `path` is not one. Pure, so bun
 * can pin it. Accepts the path expo-router hands over (`/session/abc?recap=1`), the same
 * without the leading slash, the `/session/abc/recap` spelling, and the full URL.
 */
export function recapPath(path: string): `/session/${string}` | null {
  const m = /^(?:[a-z][a-z0-9+.-]*:\/{2,3})?\/*session\/([^/?#\s]+)(\/recap\/?)?(?:\?([^#]*))?(?:#.*)?$/i.exec(
    path.trim()
  );
  if (!m?.[1]) return null;
  const id = m[1];
  const suffixRecap = Boolean(m[2]);
  const query = new URLSearchParams(m[3] ?? '');
  const recap = suffixRecap || query.get('recap') === '1';
  query.delete('recap');
  if (recap) query.set('recap', '1');
  const qs = query.toString();
  return `/session/${id}${qs ? `?${qs}` : ''}`;
}
