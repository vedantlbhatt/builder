import * as AppleAuthentication from 'expo-apple-authentication';
import * as Application from 'expo-application';
import React, { useCallback, useEffect, useState } from 'react';
import { Alert, Pressable, ScrollView, Text, TextInput, View } from 'react-native';

import * as cache from '../src/data/cache';
import { api } from '../src/data/client';
import { registerForPush } from '../src/push/push';
import { colors, space } from '../src/theme';

const c = colors('dark');

export default function SettingsScreen() {
  const [signedIn, setSignedIn] = useState(false);
  const [pairCode, setPairCode] = useState('');
  const [status, setStatus] = useState<string | null>(null);

  useEffect(() => {
    void api.isSignedIn().then(setSignedIn);
  }, []);

  const signIn = useCallback(async () => {
    try {
      const credential = await AppleAuthentication.signInAsync({
        requestedScopes: [AppleAuthentication.AppleAuthenticationScope.EMAIL],
      });
      if (!credential.identityToken) throw new Error('no identity token');

      // A per-install identifier, hashed server-side into a device row. Not the IDFV
      // itself as an identity — it is stable per vendor, so treating it as one would let
      // two apps from the same vendor correlate installs.
      const machineId = (Application.getIosIdForVendorAsync
        ? await Application.getIosIdForVendorAsync()
        : null) ?? 'unknown';

      const tokens = await api.signInWithApple(
        credential.identityToken,
        machineId.replace(/-/g, '').padEnd(64, '0').slice(0, 64)
      );
      await api.setTokens(tokens.access_token, tokens.refresh_token);
      setSignedIn(true);
      setStatus('Signed in. Pull to refresh on Sessions.');
      void registerForPush(api);
    } catch (e) {
      if ((e as { code?: string }).code === 'ERR_REQUEST_CANCELED') return;
      setStatus(e instanceof Error ? e.message : 'sign in failed');
    }
  }, []);

  const pair = useCallback(async () => {
    try {
      const result = await api.approvePairing(pairCode.trim().toUpperCase());
      setStatus(`Paired with ${result.label}.`);
      setPairCode('');
    } catch {
      setStatus('That code was not recognised, or it expired.');
    }
  }, [pairCode]);

  const signOut = useCallback(async () => {
    await api.clearTokens();
    // Cached sessions are the user's data, not ours to keep once they leave.
    await cache.clear();
    setSignedIn(false);
    setStatus('Signed out. Local copies deleted.');
  }, []);

  const deleteAccount = useCallback(() => {
    // In-app account deletion, not an email link. App Review guideline 5.1.1(x) treats a
    // "contact us to delete" link as an automatic rejection.
    Alert.alert(
      'Delete your account?',
      'Every session, device and token on the server is deleted immediately. This cannot be undone.',
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Delete everything',
          style: 'destructive',
          onPress: async () => {
            try {
              const result = await api.deleteAccount();
              await api.clearTokens();
              await cache.clear();
              setSignedIn(false);
              setStatus(`Deleted. Receipt ${result.receipt.slice(0, 12)}…`);
            } catch {
              setStatus('Could not reach the server. Nothing was deleted.');
            }
          },
        },
      ]
    );
  }, []);

  return (
    <ScrollView
      style={{ flex: 1, backgroundColor: c.bg }}
      contentContainerStyle={{ padding: space.md, paddingBottom: space.xxl }}
    >
      {!signedIn ? (
        <Section title="Account">
          <Text style={{ color: c.textDim, fontSize: 13, marginBottom: space.md }}>
            Builder works without an account — you are seeing a sample session. Sign in to
            sync your own from the Mac agent.
          </Text>
          <AppleAuthentication.AppleAuthenticationButton
            buttonType={AppleAuthentication.AppleAuthenticationButtonType.SIGN_IN}
            buttonStyle={AppleAuthentication.AppleAuthenticationButtonStyle.WHITE}
            cornerRadius={10}
            style={{ height: 48 }}
            onPress={signIn}
          />
        </Section>
      ) : (
        <>
          <Section title="Pair your Mac">
            <Text style={{ color: c.textDim, fontSize: 13, marginBottom: space.sm }}>
              Run `builder pair` on your Mac and type the code it shows.
            </Text>
            <TextInput
              value={pairCode}
              onChangeText={setPairCode}
              autoCapitalize="characters"
              autoCorrect={false}
              placeholder="XXXX-XXXX"
              placeholderTextColor={c.textDim}
              style={{
                color: c.text,
                backgroundColor: c.bg,
                borderRadius: 8,
                padding: space.md,
                fontSize: 20,
                letterSpacing: 2,
                textAlign: 'center',
              }}
            />
            <Button label="Pair" onPress={pair} disabled={pairCode.trim().length < 8} />
          </Section>

          <Section title="Account">
            <Button label="Sign out" onPress={signOut} />
            <Button label="Delete account and all data" onPress={deleteAccount} destructive />
          </Section>
        </>
      )}

      <Section title="Privacy">
        <Text style={{ color: c.textDim, fontSize: 13, lineHeight: 19 }}>
          Your prompts, your code, your diffs and your file names never leave your machine.
          What syncs is timings, counts, the shape of the session, and — only for
          repositories you mark public — the repository name and the title your editor
          already wrote to your own disk.
          {'\n\n'}
          The Mac agent is open source, and `builder sync --dry-run --print-payload` prints
          every byte it would send without sending it.
        </Text>
      </Section>

      {status && (
        <Text style={{ color: c.accent, fontSize: 13, marginTop: space.lg }}>{status}</Text>
      )}
    </ScrollView>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <View style={{ marginTop: space.lg }}>
      <Text
        style={{
          color: c.textDim,
          fontSize: 11,
          fontWeight: '700',
          letterSpacing: 0.8,
          marginBottom: space.sm,
        }}
      >
        {title.toUpperCase()}
      </Text>
      <View style={{ backgroundColor: c.card, borderRadius: 12, padding: space.md }}>
        {children}
      </View>
    </View>
  );
}

function Button({
  label,
  onPress,
  destructive,
  disabled,
}: {
  label: string;
  onPress: () => void;
  destructive?: boolean;
  disabled?: boolean;
}) {
  return (
    <Pressable
      onPress={onPress}
      disabled={disabled}
      style={({ pressed }) => [
        {
          paddingVertical: space.md,
          alignItems: 'center',
          borderRadius: 10,
          marginTop: space.sm,
          backgroundColor: destructive ? 'transparent' : c.bg,
          opacity: disabled ? 0.4 : 1,
        },
        pressed && { opacity: 0.6 },
      ]}
    >
      <Text style={{ color: destructive ? '#E5484D' : c.text, fontWeight: '600' }}>{label}</Text>
    </Pressable>
  );
}
