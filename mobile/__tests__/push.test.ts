/**
 * Where a tapped notification goes. The server writes `data: {kind, session_id, url}`
 * (server/builder/notify.py::push_data); the phone must open the recap for both finish
 * kinds, the plain detail for anything else it can name, and nothing for anything it
 * cannot. And it must find the data where the platform actually put it.
 */

import { describe, expect, test } from 'bun:test';

import { dataFromResponse, routeForNotification, sessionIdFromUrl } from '../src/push/route';

const SID = '0b6d7a1e-2f44-4a4a-9d2e-5d2a5d7c0a11';

describe('routeForNotification', () => {
  test('a finished session opens the recap', () => {
    expect(
      routeForNotification({ kind: 'session_finished', session_id: SID, url: `builder://session/${SID}?recap=1` })
    ).toBe(`/session/${SID}?recap=1`);
  });

  test('a finished agent run opens the same recap; the headline is the recap’s job', () => {
    expect(routeForNotification({ kind: 'agent_run_finished', session_id: SID })).toBe(
      `/session/${SID}?recap=1`
    );
  });

  test('a kind this build does not know opens the detail, never a compose sheet', () => {
    expect(routeForNotification({ kind: 'kudos', session_id: SID })).toBe(`/session/${SID}`);
  });

  test('no kind at all is the older payload, which only ever meant finished', () => {
    expect(routeForNotification({ session: SID, unattended: false })).toBe(`/session/${SID}?recap=1`);
  });

  test('missing id → null', () => {
    expect(routeForNotification({ kind: 'session_finished' })).toBeNull();
    expect(routeForNotification({ kind: 'session_finished', session_id: '' })).toBeNull();
    expect(routeForNotification({ kind: 'session_finished', session_id: 42 })).toBeNull();
  });

  test('unknown data → null', () => {
    expect(routeForNotification(null)).toBeNull();
    expect(routeForNotification(undefined)).toBeNull();
    expect(routeForNotification('session_finished')).toBeNull();
    expect(routeForNotification({})).toBeNull();
    expect(routeForNotification({ aps: { alert: 'x' } })).toBeNull();
  });

  test('falls back to the url when only that names the session', () => {
    expect(routeForNotification({ kind: 'session_finished', url: `builder://session/${SID}?recap=1` })).toBe(
      `/session/${SID}?recap=1`
    );
  });

  test('an id that would break the route is not an id', () => {
    expect(routeForNotification({ session_id: '../feed' })).toBeNull();
    expect(routeForNotification({ session_id: 'a b' })).toBeNull();
    expect(routeForNotification({ session_id: 'abc?recap=0' })).toBeNull();
  });
});

describe('sessionIdFromUrl', () => {
  test('reads the one shape the server builds', () => {
    expect(sessionIdFromUrl(`builder://session/${SID}?recap=1`)).toBe(SID);
    expect(sessionIdFromUrl(`builder:///session/${SID}`)).toBe(SID);
  });
  test('rejects anything else', () => {
    expect(sessionIdFromUrl('builder://feed')).toBeNull();
    expect(sessionIdFromUrl('https://example.com/session/')).toBeNull();
    expect(sessionIdFromUrl('')).toBeNull();
  });
});

describe('dataFromResponse', () => {
  const data = { kind: 'session_finished', session_id: SID, url: `builder://session/${SID}?recap=1` };
  const wrap = (content: unknown, trigger: unknown) => ({
    actionIdentifier: 'expo.modules.notifications.actions.DEFAULT',
    notification: { date: 0, request: { identifier: 'n1', content, trigger } },
  });

  test('a local or Expo-service notification carries it on content.data', () => {
    expect(dataFromResponse(wrap({ data }, null))).toEqual(data);
  });

  test('a raw APNs push on iOS carries it on trigger.payload.data, with content.data null', () => {
    // EXNotificationSerializer.m: for a remote notification, content.data is
    // userInfo["body"], which our server never sets. The whole userInfo is the payload.
    const userInfo = { aps: { alert: { title: 't', body: 'b' } }, session: SID, unattended: false, data };
    expect(dataFromResponse(wrap({ data: null }, { type: 'push', payload: userInfo }))).toEqual(data);
  });

  test('an older server without data still routes off the top-level session key', () => {
    const userInfo = { aps: { alert: { title: 't' } }, session: SID, unattended: true };
    const got = dataFromResponse(wrap({ data: null }, { type: 'push', payload: userInfo }));
    expect(routeForNotification(got)).toBe(`/session/${SID}?recap=1`);
  });

  test('Android FCM puts it on trigger.remoteMessage.data', () => {
    expect(
      dataFromResponse(wrap({ data: null }, { type: 'push', remoteMessage: { data } }))
    ).toEqual(data);
  });

  test('nothing usable anywhere → routes nowhere', () => {
    expect(routeForNotification(dataFromResponse(wrap({ data: null }, null)))).toBeNull();
    expect(routeForNotification(dataFromResponse(null))).toBeNull();
    expect(routeForNotification(dataFromResponse({}))).toBeNull();
  });
});
