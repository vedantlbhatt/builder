import * as Device from 'expo-device';
import * as Notifications from 'expo-notifications';

import type { Api } from '../data/api';

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
