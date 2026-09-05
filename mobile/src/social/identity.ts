import * as cache from '../data/cache';

/**
 * The viewer's own handle, remembered on the phone.
 *
 * `/v1/profile` does not carry it and comments have no `is_you`, so the app learns it from
 * the first response that reveals it — a comment it posted, a post it created, a user page
 * with `is_you` — and keeps it in the cache kv, which `cache.clear()` wipes on sign-out.
 */
export const MY_HANDLE_KEY = 'my_handle';

export async function rememberMyHandle(handle: string | null | undefined): Promise<void> {
  if (handle) await cache.setKv(MY_HANDLE_KEY, handle);
}

export function myHandle(): Promise<string | null> {
  return cache.getKv(MY_HANDLE_KEY);
}

/** Slugs of factions this phone created or joined; the server has no list endpoint. */
export const MY_FACTIONS_KEY = 'my_factions';
