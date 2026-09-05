/**
 * Web stand-in for `expo-secure-store`. Resolved ONLY when Metro bundles for `platform ===
 * 'web'` (see `metro.config.js`); iOS and Android import the real module.
 *
 * `expo-secure-store` ships `ExpoSecureStore.web.js` as `export default {}`, so on web every
 * call throws `UnavailabilityError`. A browser has no keychain; localStorage is the honest
 * equivalent and the same three async functions keep `src/data/api.ts` unchanged. Keys are
 * stored verbatim, which is also how an end-to-end run signs the page in: set
 * `builder.access` / `builder.refresh` before the app loads and `Api.loadTokens` finds them.
 *
 * Nothing here is secret-grade storage and nothing in this file is compiled into a native
 * build.
 */

function store(): Storage | null {
  try {
    return typeof localStorage === 'undefined' ? null : localStorage;
  } catch {
    return null;
  }
}

export async function getItemAsync(key: string): Promise<string | null> {
  return store()?.getItem(key) ?? null;
}

export async function setItemAsync(key: string, value: string): Promise<void> {
  store()?.setItem(key, value);
}

export async function deleteItemAsync(key: string): Promise<void> {
  store()?.removeItem(key);
}

export async function isAvailableAsync(): Promise<boolean> {
  return store() !== null;
}
