/**
 * The system-URL rewrite. `builder://session/<id>?recap=1` is what a push carries, what
 * the Mac opens and what a share sheet pastes; every spelling of it must land on the
 * detail with the recap raised, and nothing else may be touched.
 */

import { describe, expect, test } from 'bun:test';

import { recapPath, redirectSystemPath } from '../app/+native-intent';

const SID = '0b6d7a1e-2f44-4a4a-9d2e-5d2a5d7c0a11';

describe('recapPath', () => {
  test('the canonical link, as expo-router hands it over', () => {
    expect(recapPath(`/session/${SID}?recap=1`)).toBe(`/session/${SID}?recap=1`);
  });
  test('the full URL, two or three slashes', () => {
    expect(recapPath(`builder://session/${SID}?recap=1`)).toBe(`/session/${SID}?recap=1`);
    expect(recapPath(`builder:///session/${SID}?recap=1`)).toBe(`/session/${SID}?recap=1`);
  });
  test('the /recap suffix spelling and a missing leading slash', () => {
    expect(recapPath(`session/${SID}/recap`)).toBe(`/session/${SID}?recap=1`);
    expect(recapPath(`/session/${SID}/recap/`)).toBe(`/session/${SID}?recap=1`);
  });
  test('a plain session link stays a plain session link', () => {
    expect(recapPath(`/session/${SID}`)).toBe(`/session/${SID}`);
    expect(recapPath(`/session/${SID}?recap=0`)).toBe(`/session/${SID}`);
  });
  test('other query parameters survive, recap normalised to 1', () => {
    expect(recapPath(`/session/${SID}?from=mac&recap=1`)).toBe(`/session/${SID}?from=mac&recap=1`);
  });
  test('not a session link → null', () => {
    expect(recapPath('/feed')).toBeNull();
    expect(recapPath('/session/')).toBeNull();
    expect(recapPath('/sessions/abc')).toBeNull();
    expect(recapPath(`/post/${SID}`)).toBeNull();
  });
});

describe('redirectSystemPath', () => {
  test('google auth still goes to settings', () => {
    expect(redirectSystemPath({ path: '/auth/google#id_token=x', initial: true })).toBe('/settings');
  });
  test('a session link is normalised; anything else passes through unchanged', () => {
    expect(redirectSystemPath({ path: `builder://session/${SID}?recap=1`, initial: false })).toBe(
      `/session/${SID}?recap=1`
    );
    expect(redirectSystemPath({ path: '/feed', initial: false })).toBe('/feed');
    expect(redirectSystemPath({ path: `/post/${SID}`, initial: true })).toBe(`/post/${SID}`);
  });
});
