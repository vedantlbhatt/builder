/**
 * Google sign-in without a native SDK.
 *
 * The OpenID Connect implicit flow: the system browser opens Google's consent page with
 * `response_type=id_token`, Google redirects to the app's own scheme with the token in the
 * URL fragment, and the app posts that token to `/v1/auth/google`. The server verifies the
 * signature and audience; the client checks that the token it got back is the one it asked
 * for (`nonce`, and `state` on the redirect), which is what stops a token minted for some
 * other request from being injected through the deep link.
 *
 * Everything in this file is pure so it can be tested in bun. The side effects — opening
 * the browser, the keychain, the API call — live in `googleFlow.ts`.
 */

export const GOOGLE_AUTH_ENDPOINT = 'https://accounts.google.com/o/oauth2/v2/auth';
export const DEFAULT_GOOGLE_REDIRECT_URI = 'builder://auth/google';

const NONCE_ALPHABET = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789';

/**
 * A random URL-safe string. `crypto.getRandomValues` when the runtime has it; otherwise
 * Math.random, which is enough for a value whose job is to bind one redirect to one
 * request — it is compared, never used as a key.
 */
export function randomToken(length = 32): string {
  const bytes = new Uint8Array(length);
  const g = (globalThis as { crypto?: { getRandomValues?: (a: Uint8Array) => Uint8Array } })
    .crypto;
  if (g?.getRandomValues) {
    g.getRandomValues(bytes);
  } else {
    for (let i = 0; i < length; i += 1) bytes[i] = Math.floor(Math.random() * 256);
  }
  let out = '';
  for (let i = 0; i < length; i += 1) {
    out += NONCE_ALPHABET[(bytes[i] ?? 0) % NONCE_ALPHABET.length];
  }
  return out;
}

export interface GoogleAuthRequest {
  clientId: string;
  redirectUri: string;
  nonce: string;
  state: string;
}

export function buildGoogleAuthUrl(req: GoogleAuthRequest): string {
  const params: [string, string][] = [
    ['client_id', req.clientId],
    ['redirect_uri', req.redirectUri],
    ['response_type', 'id_token'],
    ['response_mode', 'fragment'],
    ['scope', 'openid email'],
    ['nonce', req.nonce],
    ['state', req.state],
    ['prompt', 'select_account'],
  ];
  const qs = params.map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`).join('&');
  return `${GOOGLE_AUTH_ENDPOINT}?${qs}`;
}

/** Does this incoming URL belong to the Google redirect? Anything else is left to the router. */
export function isGoogleRedirect(url: string, redirectUri = DEFAULT_GOOGLE_REDIRECT_URI): boolean {
  if (url.startsWith(redirectUri)) return true;
  // A custom-scheme URL may come back with a doubled slash (`builder:///auth/google`) or
  // host/path split differently by the OS; the presence of the token is the real tell.
  return /[#?&]id_token=/.test(url) && url.includes('auth/google');
}

export interface GoogleRedirect {
  idToken: string | null;
  state: string | null;
  error: string | null;
}

/** Fields from the fragment (Google's default for id_token) or, failing that, the query. */
export function parseGoogleRedirect(url: string): GoogleRedirect {
  const hash = url.indexOf('#');
  const fragment = hash >= 0 ? url.slice(hash + 1) : '';
  const q = url.indexOf('?');
  const query = q >= 0 ? url.slice(q + 1, hash >= 0 ? hash : undefined) : '';
  const fields = { ...parseForm(query), ...parseForm(fragment) };
  return {
    idToken: fields.id_token ?? null,
    state: fields.state ?? null,
    error: fields.error ?? null,
  };
}

function parseForm(s: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const pair of s.split('&')) {
    if (!pair) continue;
    const eq = pair.indexOf('=');
    const k = safeDecode(eq < 0 ? pair : pair.slice(0, eq));
    const v = eq < 0 ? '' : safeDecode(pair.slice(eq + 1));
    if (k) out[k] = v;
  }
  return out;
}

function safeDecode(s: string): string {
  try {
    return decodeURIComponent(s.replace(/\+/g, ' '));
  } catch {
    return s;
  }
}

/** The JWT payload, decoded with atob and nothing else. Null for anything malformed. */
export function decodeJwtPayload(token: string): Record<string, unknown> | null {
  const parts = token.split('.');
  if (parts.length !== 3) return null;
  const b64url = parts[1] ?? '';
  const b64 = b64url.replace(/-/g, '+').replace(/_/g, '/').padEnd(Math.ceil(b64url.length / 4) * 4, '=');
  try {
    const bin = atob(b64);
    // atob yields Latin-1 code units; the payload is UTF-8, so re-decode it before JSON.
    const bytes = Uint8Array.from(bin, (ch) => ch.charCodeAt(0));
    const text = utf8Decode(bytes);
    const parsed: unknown = JSON.parse(text);
    return parsed && typeof parsed === 'object' ? (parsed as Record<string, unknown>) : null;
  } catch {
    return null;
  }
}

function utf8Decode(bytes: Uint8Array): string {
  const TD = (globalThis as { TextDecoder?: new () => { decode(b: Uint8Array): string } })
    .TextDecoder;
  if (TD) return new TD().decode(bytes);
  let s = '';
  for (const b of bytes) s += String.fromCharCode(b);
  return decodeURIComponent(escape(s));
}

export type GoogleTokenCheck =
  | { ok: true }
  | { ok: false; reason: 'malformed' | 'nonce_mismatch' | 'state_mismatch' };

/**
 * The client-side half of verification. Signature and audience are the server's job; the
 * client only confirms the token answers ITS request. `nonce` is checked when the token
 * carries one (Google always echoes it for id_token requests); `state` is checked when the
 * redirect carries one.
 */
export function checkGoogleRedirect(
  redirect: GoogleRedirect,
  expected: { nonce: string; state: string }
): GoogleTokenCheck {
  if (!redirect.idToken) return { ok: false, reason: 'malformed' };
  if (redirect.state !== null && redirect.state !== expected.state) {
    return { ok: false, reason: 'state_mismatch' };
  }
  const payload = decodeJwtPayload(redirect.idToken);
  if (!payload) return { ok: false, reason: 'malformed' };
  const nonce = payload.nonce;
  if (typeof nonce === 'string' && nonce !== expected.nonce) {
    return { ok: false, reason: 'nonce_mismatch' };
  }
  return { ok: true };
}
