import Constants from 'expo-constants';
import * as Linking from 'expo-linking';
import * as SecureStore from 'expo-secure-store';
import { Platform } from 'react-native';

import type { Api } from '../data/api';
import * as cache from '../data/cache';
import { getMachineId } from '../data/machine';
import { registerForPush } from '../push/push';
import {
  buildGoogleAuthUrl,
  checkGoogleRedirect,
  DEFAULT_GOOGLE_REDIRECT_URI,
  isGoogleRedirect,
  parseGoogleRedirect,
  randomToken,
} from './google';

/**
 * The side-effecting half of Google sign-in: open the browser, remember what we asked for,
 * and finish when the deep link comes back. `app/_layout.tsx` feeds every incoming URL to
 * `handleIncomingUrl`; Settings subscribes to the result so it can update in place.
 */

const NONCE_KEY = 'builder.google.nonce';
const STATE_KEY = 'builder.google.state';

export function googleConfig(): { clientId: string; redirectUri: string } {
  const extra = (Constants.expoConfig?.extra ?? {}) as {
    googleClientId?: string;
    googleRedirectUri?: string;
  };
  return {
    clientId: extra.googleClientId ?? '',
    redirectUri: extra.googleRedirectUri || DEFAULT_GOOGLE_REDIRECT_URI,
  };
}

export function isGoogleConfigured(): boolean {
  return googleConfig().clientId.length > 0;
}

export type GoogleSignInResult = { ok: true } | { ok: false; message: string };
type Listener = (r: GoogleSignInResult) => void;
const listeners = new Set<Listener>();

export function onGoogleSignIn(l: Listener): () => void {
  listeners.add(l);
  return () => {
    listeners.delete(l);
  };
}

function emit(r: GoogleSignInResult): void {
  for (const l of listeners) l(r);
}

// In memory for the common case; the keychain copy survives the app being killed while
// the browser is in front, which is exactly when the redirect arrives at a cold start.
let pending: { nonce: string; state: string } | null = null;

async function loadPending(): Promise<{ nonce: string; state: string } | null> {
  if (pending) return pending;
  try {
    const [nonce, state] = await Promise.all([
      SecureStore.getItemAsync(NONCE_KEY),
      SecureStore.getItemAsync(STATE_KEY),
    ]);
    if (nonce && state) pending = { nonce, state };
  } catch {
    // no keychain; the in-memory copy is all we have
  }
  return pending;
}

async function clearPending(): Promise<void> {
  pending = null;
  try {
    await Promise.all([SecureStore.deleteItemAsync(NONCE_KEY), SecureStore.deleteItemAsync(STATE_KEY)]);
  } catch {
    // nothing to clear
  }
}

export async function startGoogleSignIn(): Promise<void> {
  const { clientId, redirectUri } = googleConfig();
  if (!clientId) throw new Error('Google sign-in not configured');
  const nonce = randomToken(32);
  const state = randomToken(16);
  pending = { nonce, state };
  try {
    await Promise.all([SecureStore.setItemAsync(NONCE_KEY, nonce), SecureStore.setItemAsync(STATE_KEY, state)]);
  } catch {
    // keychain unavailable; the in-memory copy still covers the warm-return case
  }
  await Linking.openURL(buildGoogleAuthUrl({ clientId, redirectUri, nonce, state }));
}

// The same URL can arrive twice (the `url` event and `getInitialURL`); a token must be
// redeemed once.
const handled = new Set<string>();

/** Returns true when the URL was the Google redirect (whether or not sign-in succeeded). */
export async function handleIncomingUrl(url: string, api: Api): Promise<boolean> {
  const { redirectUri } = googleConfig();
  if (!isGoogleRedirect(url, redirectUri)) return false;
  if (handled.has(url)) return true;
  handled.add(url);

  const redirect = parseGoogleRedirect(url);
  if (redirect.error) {
    await clearPending();
    emit({ ok: false, message: redirect.error === 'access_denied' ? 'Sign-in cancelled.' : `Google: ${redirect.error}` });
    return true;
  }
  const expected = await loadPending();
  if (!expected) {
    emit({ ok: false, message: 'No sign-in was in progress. Try again.' });
    return true;
  }
  const check = checkGoogleRedirect(redirect, expected);
  if (!check.ok) {
    await clearPending();
    emit({ ok: false, message: 'That sign-in response did not match this request. Try again.' });
    return true;
  }

  try {
    const machineId = await getMachineId();
    const tokens = await api.signInWithGoogle(
      redirect.idToken!,
      machineId,
      Platform.OS === 'android' ? 'android' : 'ios'
    );
    await api.setTokens(tokens.access_token, tokens.refresh_token);
    await clearPending();
    emit({ ok: true });
    void registerForPush(api).catch(() => {});
    try {
      await cache.sync(api);
    } catch {
      // Sessions shows the banner on its next focus; sign-in itself succeeded.
    }
  } catch (e) {
    emit({ ok: false, message: e instanceof Error ? e.message : 'sign in failed' });
  }
  return true;
}
