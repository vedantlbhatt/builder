/**
 * Rewrites incoming system URLs before expo-router routes them.
 *
 * The Google redirect (`builder://auth/google#id_token=...`) is consumed by the Linking
 * listener in `_layout.tsx`; there is no `auth/google` route, so without this the router
 * would show "Unmatched route" over the top of a successful sign-in.
 */
export function redirectSystemPath({ path }: { path: string; initial: boolean }): string {
  if (path.includes('auth/google')) return '/settings';
  return path;
}
