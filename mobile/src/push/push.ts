import * as Device from 'expo-device';
import * as Notifications from 'expo-notifications';
import { useRootNavigationState, useRouter } from 'expo-router';
import { useEffect, useRef } from 'react';

import type { Api } from '../data/api';
import { dataFromResponse, routeForNotification } from './route';

/**
 * Register for the "your session finished" push.
 *
 * The environment is reported alongside the token because a sandbox token and a
 * production token are indistinguishable by inspection, and sending to the wrong APNs
 * host returns BadDeviceToken. Without it, push breaks during exactly the TestFlight
 * phase — the build is signed for production but installed like a development one.
 */
export async function registerForPush(api: Api): Promise<string | null> {
  if (!Device.isDevice) return null; // the simulator has no APNs token

  const existing = await Notifications.getPermissionsAsync();
  let granted = existing.granted;
  if (!granted && existing.canAskAgain) {
    granted = (await Notifications.requestPermissionsAsync()).granted;
  }
  if (!granted) return null;

  const token = (await Notifications.getDevicePushTokenAsync()).data as string;

  // __DEV__ is a proxy, not a guarantee — a TestFlight build reports false while still
  // holding a sandbox token in some configurations. The server retries against the other
  // host once before giving up on the token, which is what makes this survivable.
  const environment = __DEV__ ? 'sandbox' : 'production';
  await api.registerPush(token, environment);
  return token;
}

Notifications.setNotificationHandler({
  handleNotification: async () => ({
    shouldShowBanner: true,
    shouldShowList: true,
    shouldPlaySound: true,
    shouldSetBadge: false,
  }),
});

// ------------------------------------------------------------------ tap routing
// The pure rules (what a payload opens, where each platform put the data) live in
// `route.ts` so bun can import them without expo; they are re-exported here.

export { dataFromResponse, RECAP_KINDS, routeForNotification, sessionIdFromUrl } from './route';
export type { SessionRoute } from './route';

/**
 * Open the session a tapped notification names. Mount once, at the root.
 *
 * Two deliveries: `getLastNotificationResponseAsync` for a cold start (the tap happened
 * before JS was running) and the response listener for a warm tap. The same response can
 * arrive by both paths on some launches, so the request identifier is remembered and a
 * repeat is ignored. Navigation waits for the root navigator to have state; pushing before
 * that throws "attempted to navigate before mounting the Root Layout".
 */
export function useNotificationResponseRouting(): void {
  const router = useRouter();
  const ready = Boolean(useRootNavigationState()?.key);
  const handled = useRef<string | null>(null);
  const queued = useRef<Notifications.NotificationResponse | null>(null);

  useEffect(() => {
    const open = (response: Notifications.NotificationResponse | null) => {
      if (!response) return;
      if (!ready) {
        queued.current = response;
        return;
      }
      const key = response.notification.request.identifier;
      if (key && handled.current === key) return;
      handled.current = key;
      const href = routeForNotification(dataFromResponse(response));
      if (href) router.push(href);
    };

    if (queued.current && ready) {
      const q = queued.current;
      queued.current = null;
      open(q);
    }
    void Notifications.getLastNotificationResponseAsync()
      .then(open)
      .catch(() => undefined);
    const sub = Notifications.addNotificationResponseReceivedListener(open);
    return () => sub.remove();
  }, [ready, router]);
}
