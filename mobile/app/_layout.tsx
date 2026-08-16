import { Stack } from 'expo-router';
import { StatusBar } from 'expo-status-bar';
import React from 'react';

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
        <Stack.Screen name="session/[id]" options={{ title: '' }} />
      </Stack>
    </>
  );
}
