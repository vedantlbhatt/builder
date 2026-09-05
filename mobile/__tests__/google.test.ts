/**
 * The pure half of Google sign-in: the URL we open, and whether the redirect that comes
 * back answers OUR request. Signature and audience are the server's job; this is the
 * client's, and it is what stops a token from another request being injected via the
 * deep link.
 */
import { describe, expect, test } from 'bun:test';

import {
  buildGoogleAuthUrl,
  checkGoogleRedirect,
  decodeJwtPayload,
  isGoogleRedirect,
  parseGoogleRedirect,
  randomToken,
} from '../src/auth/google';

function jwt(payload: Record<string, unknown>): string {
  const b64url = (s: string) =>
    Buffer.from(s, 'utf8').toString('base64').replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  return `${b64url(JSON.stringify({ alg: 'RS256', typ: 'JWT' }))}.${b64url(JSON.stringify(payload))}.sig`;
}

describe('buildGoogleAuthUrl', () => {
  test('asks for an id_token in the fragment with the nonce and state', () => {
    const url = buildGoogleAuthUrl({
      clientId: '123.apps.googleusercontent.com',
      redirectUri: 'builder://auth/google',
      nonce: 'n0nce',
      state: 'st4te',
    });
    expect(url.startsWith('https://accounts.google.com/o/oauth2/v2/auth?')).toBe(true);
    expect(url).toContain('client_id=123.apps.googleusercontent.com');
    expect(url).toContain('redirect_uri=builder%3A%2F%2Fauth%2Fgoogle');
    expect(url).toContain('response_type=id_token');
    expect(url).toContain('response_mode=fragment');
    expect(url).toContain('scope=openid%20email');
    expect(url).toContain('nonce=n0nce');
    expect(url).toContain('state=st4te');
  });
});

describe('parseGoogleRedirect / isGoogleRedirect', () => {
  test('reads the fragment', () => {
    const r = parseGoogleRedirect('builder://auth/google#id_token=a.b.c&state=xyz&authuser=0');
    expect(r).toEqual({ idToken: 'a.b.c', state: 'xyz', error: null });
  });
  test('falls back to the query, fragment winning on conflict', () => {
    expect(parseGoogleRedirect('builder://auth/google?error=access_denied&state=s')).toEqual({
      idToken: null,
      state: 's',
      error: 'access_denied',
    });
    expect(parseGoogleRedirect('builder://auth/google?state=q#id_token=t&state=f').state).toBe('f');
  });
  test('recognises the redirect and nothing else', () => {
    expect(isGoogleRedirect('builder://auth/google#id_token=x')).toBe(true);
    expect(isGoogleRedirect('builder:///auth/google#id_token=x')).toBe(true);
    expect(isGoogleRedirect('builder://session/abc')).toBe(false);
    expect(isGoogleRedirect('https://builder.dev/pair/BCDF-GHJK')).toBe(false);
  });
});

describe('decodeJwtPayload', () => {
  test('decodes base64url with no padding and UTF-8 in the payload', () => {
    expect(decodeJwtPayload(jwt({ nonce: 'n', name: 'Zoë' }))).toEqual({ nonce: 'n', name: 'Zoë' });
  });
  test('rejects anything that is not three dot-separated parts of JSON', () => {
    expect(decodeJwtPayload('nope')).toBeNull();
    expect(decodeJwtPayload('a.b')).toBeNull();
    expect(decodeJwtPayload('a.!!!.c')).toBeNull();
  });
});

describe('checkGoogleRedirect', () => {
  const expected = { nonce: 'N1', state: 'S1' };
  test('accepts a token whose nonce and state match', () => {
    const r = parseGoogleRedirect(`builder://auth/google#id_token=${jwt({ nonce: 'N1' })}&state=S1`);
    expect(checkGoogleRedirect(r, expected)).toEqual({ ok: true });
  });
  test('rejects a nonce or state that belongs to another request', () => {
    const wrongNonce = parseGoogleRedirect(`builder://auth/google#id_token=${jwt({ nonce: 'N2' })}&state=S1`);
    expect(checkGoogleRedirect(wrongNonce, expected)).toEqual({ ok: false, reason: 'nonce_mismatch' });
    const wrongState = parseGoogleRedirect(`builder://auth/google#id_token=${jwt({ nonce: 'N1' })}&state=S9`);
    expect(checkGoogleRedirect(wrongState, expected)).toEqual({ ok: false, reason: 'state_mismatch' });
  });
  test('a token without a nonce claim is left to the server; a missing token is malformed', () => {
    const noNonce = parseGoogleRedirect(`builder://auth/google#id_token=${jwt({ sub: '1' })}&state=S1`);
    expect(checkGoogleRedirect(noNonce, expected)).toEqual({ ok: true });
    expect(checkGoogleRedirect(parseGoogleRedirect('builder://auth/google#state=S1'), expected)).toEqual({
      ok: false,
      reason: 'malformed',
    });
  });
});

describe('randomToken', () => {
  test('is URL-safe, the requested length, and not constant', () => {
    const a = randomToken(32);
    const b = randomToken(32);
    expect(a).toMatch(/^[A-Za-z0-9]{32}$/);
    expect(a).not.toBe(b);
  });
});
