import * as Linking from 'expo-linking';
import { Stack } from 'expo-router';
import { StatusBar } from 'expo-status-bar';
import React, { useEffect } from 'react';

import { handleIncomingUrl } from '../src/auth/googleFlow';
import { api } from '../src/data/client';
import { useNotificationResponseRouting } from '../src/push/push';
import { colors } from '../src/theme';

/**
 * Dark by default and not (yet) switchable.
 *
 * The strip's identity colour is an amber that was tuned against a dark ground; a light
 * scheme needs its own pass on those tokens rather than an automatic inversion, and
 * shipping a half-tuned light mode would make the product's one recognisable asset look
 * wrong on half the devices.
 */
export default function RootLayout() {
  const c = colors('dark');

  // A tapped "Session finished" / "Agent run finished" push opens that session's recap.
  // At the root for the same reason the Google redirect is: the tap that launched the
  // app happened before any screen existed.
  useNotificationResponseRouting();

  // Google sign-in comes back through the app scheme (`builder://auth/google#id_token=…`).
  // It is handled here, at the root, rather than in Settings: the redirect can arrive at
  // a cold start, before any screen has mounted. `+native-intent.ts` keeps the router from
  // treating the same URL as a route.
  useEffect(() => {
    const sub = Linking.addEventListener('url', ({ url }) => {
      void handleIncomingUrl(url, api);
    });
    void Linking.getInitialURL().then((url) => {
      if (url) void handleIncomingUrl(url, api);
    });
    return () => sub.remove();
  }, []);

  return (
    <>
      <StatusBar style="light" />
      <Stack
        screenOptions={{
          headerStyle: { backgroundColor: c.bg },
          headerTintColor: c.text,
          headerTitleStyle: { fontWeight: '600' },
          contentStyle: { backgroundColor: c.bg },
        }}
      >
        <Stack.Screen name="index" options={{ title: 'Sessions' }} />
        <Stack.Screen name="profile" options={{ title: 'Profile' }} />
        <Stack.Screen name="settings" options={{ title: 'Settings' }} />
        <Stack.Screen name="pair" options={{ title: 'Connect your Mac' }} />
        <Stack.Screen name="session/[id]" options={{ title: '' }} />
        <Stack.Screen name="feed" options={{ title: 'Feed' }} />
        <Stack.Screen name="post/[id]" options={{ title: 'Post' }} />
        <Stack.Screen name="factions" options={{ title: 'Factions' }} />
        <Stack.Screen name="u/[handle]" options={{ title: '' }} />
      </Stack>
    </>
  );
}
