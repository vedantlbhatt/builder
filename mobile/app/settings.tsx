import * as AppleAuthentication from 'expo-apple-authentication';
import Constants from 'expo-constants';
import { useRouter } from 'expo-router';
import React, { useCallback, useEffect, useState } from 'react';
import { Alert, Pressable, ScrollView, Switch, Text, TextInput, View } from 'react-native';

import { isGoogleConfigured, onGoogleSignIn, startGoogleSignIn } from '../src/auth/googleFlow';
import { ApiError, type Me } from '../src/data/api';
import * as cache from '../src/data/cache';
import { api } from '../src/data/client';
import { getMachineId } from '../src/data/machine';
import { PixelSprite } from '../src/pixel/PixelSprite';
import { registerForPush } from '../src/push/push';
import {
  describeHandleConflict,
  displayNameProblem,
  handleProblem,
  HANDLE_MAX,
  isValidHandle,
  MAX_DISPLAY_NAME,
  normalizeHandle,
} from '../src/social/account';
import { colors, space, TAP_TARGET } from '../src/theme';

const c = colors('dark');

/** "Builder · v0.1.0" — the version is read from the config, never typed here twice. */
function appLine(): string {
  const v = Constants.expoConfig?.version;
  return v ? `Builder · v${v}` : 'Builder';
}

export default function SettingsScreen() {
  const router = useRouter();
  const [signedIn, setSignedIn] = useState(false);
  const [pairCode, setPairCode] = useState('');
  const [status, setStatus] = useState<string | null>(null);
  const [me, setMe] = useState<Me | null>(null);
  const [meError, setMeError] = useState<string | null>(null);
  const googleReady = isGoogleConfigured();

  useEffect(() => {
    void api.isSignedIn().then(setSignedIn);
    // The Google redirect is finished by the root layout; this screen only learns the
    // outcome, so it updates in place when the browser hands control back.
    return onGoogleSignIn((r) => {
      if (r.ok) {
        setSignedIn(true);
        setStatus('Signed in with Google. Pull to refresh on Sessions.');
      } else {
        setStatus(r.message);
      }
    });
  }, []);

  // The viewer's own row, once there is a viewer. Re-read whenever sign-in flips on, so a
  // fresh sign-in shows the handle it already has rather than "not set".
  useEffect(() => {
    if (!signedIn) {
      setMe(null);
      setMeError(null);
      return;
    }
    let cancelled = false;
    api
      .getMe()
      .then((m) => {
        if (!cancelled) setMe(m);
      })
      .catch((e: unknown) => {
        if (!cancelled) setMeError(e instanceof Error ? e.message : 'could not load your profile');
      });
    return () => {
      cancelled = true;
    };
  }, [signedIn]);

  const signIn = useCallback(async () => {
    try {
      const credential = await AppleAuthentication.signInAsync({
        requestedScopes: [AppleAuthentication.AppleAuthenticationScope.EMAIL],
      });
      if (!credential.identityToken) throw new Error('no identity token');

      const machineId = await getMachineId();
      const tokens = await api.signInWithApple(credential.identityToken, machineId);
      await api.setTokens(tokens.access_token, tokens.refresh_token);
      setSignedIn(true);
      setStatus('Signed in. Pull to refresh on Sessions.');
      void registerForPush(api);
    } catch (e) {
      if ((e as { code?: string }).code === 'ERR_REQUEST_CANCELED') return;
      setStatus(e instanceof Error ? e.message : 'sign in failed');
    }
  }, []);

  const signInGoogle = useCallback(async () => {
    try {
      setStatus('Continue in the browser…');
      await startGoogleSignIn();
    } catch (e) {
      setStatus(e instanceof Error ? e.message : 'could not open Google sign-in');
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
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: space.sm }}>
        <PixelSprite state="idle" size={32} fps={2} />
        <Text style={{ color: c.textDim, fontSize: 13, fontVariant: ['tabular-nums'] }}>{appLine()}</Text>
      </View>

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
          <Pressable
            onPress={() => void signInGoogle()}
            disabled={!googleReady}
            style={({ pressed }) => [
              {
                height: 48,
                borderRadius: 10,
                marginTop: space.sm,
                backgroundColor: c.googleButton,
                alignItems: 'center',
                justifyContent: 'center',
                opacity: googleReady ? 1 : 0.4,
              },
              pressed && { opacity: 0.7 },
            ]}
          >
            <Text style={{ color: c.onAccent, fontWeight: '600', fontSize: 16 }}>
              Continue with Google
            </Text>
          </Pressable>
          {!googleReady && (
            <Text style={{ color: c.textDim, fontSize: 11, marginTop: space.xs, textAlign: 'center' }}>
              Google sign-in not configured
            </Text>
          )}
        </Section>
      ) : (
        <>
          <Section title="Pair your Mac">
            <Text style={{ color: c.textDim, fontSize: 13, marginBottom: space.sm }}>
              Run `builder pair` on your Mac, then scan the code it shows or type it.
            </Text>
            <View style={{ flexDirection: 'row', gap: space.sm }}>
              <TextInput
                value={pairCode}
                onChangeText={setPairCode}
                autoCapitalize="characters"
                autoCorrect={false}
                placeholder="XXXX-XXXX"
                placeholderTextColor={c.textDim}
                style={{
                  flex: 1,
                  color: c.text,
                  backgroundColor: c.bg,
                  borderRadius: 8,
                  padding: space.md,
                  fontSize: 20,
                  letterSpacing: 2,
                  textAlign: 'center',
                }}
              />
              <Pressable
                onPress={() => router.push('/pair')}
                style={({ pressed }) => [
                  {
                    backgroundColor: c.bg,
                    borderRadius: 8,
                    paddingHorizontal: space.md,
                    justifyContent: 'center',
                  },
                  pressed && { opacity: 0.6 },
                ]}
              >
                <Text style={{ color: c.accent, fontWeight: '600', fontSize: 14 }}>Scan code</Text>
              </Pressable>
            </View>
            <Button label="Pair" onPress={pair} disabled={pairCode.trim().length < 8} />
          </Section>

          <Section title="Profile">
            {me ? (
              <ProfileFields me={me} onChange={setMe} />
            ) : (
              <Text style={{ color: c.textDim, fontSize: 13 }}>{meError ?? 'Loading your profile…'}</Text>
            )}
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

/**
 * Handle, display name, and whether the profile is public. Each field saves on its own:
 * the handle is the one with a 30-day rule and a uniqueness race, and a person fixing a
 * typo in their display name should not be told their handle is locked.
 */
function ProfileFields({ me, onChange }: { me: Me; onChange: (next: Me) => void }) {
  const [publicBusy, setPublicBusy] = useState(false);

  const saveHandle = useCallback(
    async (raw: string) => {
      const next = await api.patchMe({ handle: normalizeHandle(raw) });
      onChange(next);
    },
    [onChange]
  );

  const saveDisplayName = useCallback(
    async (raw: string) => {
      const next = await api.patchMe({ display_name: raw.trim() || null });
      onChange(next);
    },
    [onChange]
  );

  const setPublic = useCallback(
    async (value: boolean) => {
      if (publicBusy) return;
      setPublicBusy(true);
      // Flip now, keep it only if the server agrees.
      onChange({ ...me, profile_public: value });
      try {
        onChange(await api.patchMe({ profile_public: value }));
      } catch (e) {
        onChange(me);
        Alert.alert('Could not update', e instanceof Error ? e.message : 'try again');
      } finally {
        setPublicBusy(false);
      }
    },
    [me, onChange, publicBusy]
  );

  return (
    <>
      <InlineField
        label="Handle"
        value={me.handle}
        placeholder="pick a handle"
        empty="not set"
        prefix="@"
        maxLength={HANDLE_MAX}
        hint={`3-${HANDLE_MAX} characters: a-z, 0-9 and _. Changeable once every 30 days after the first pick.`}
        normalize={normalizeHandle}
        problem={handleProblem}
        canSave={(raw) => isValidHandle(raw) && normalizeHandle(raw) !== (me.handle ?? '')}
        onSave={saveHandle}
        describeError={(e) => (e.status === 409 ? describeHandleConflict(e.message) : e.message)}
        autoCapitalize="none"
      />
      <InlineField
        label="Display name"
        value={me.display_name}
        placeholder="how your name reads"
        empty="none"
        maxLength={MAX_DISPLAY_NAME + 8}
        hint={`Optional, up to ${MAX_DISPLAY_NAME} characters. Shown next to your handle.`}
        problem={displayNameProblem}
        canSave={(raw) => displayNameProblem(raw) === null && (raw.trim() || null) !== me.display_name}
        onSave={saveDisplayName}
        describeError={(e) => e.message}
      />
      <View style={{ flexDirection: 'row', alignItems: 'center', minHeight: TAP_TARGET, marginTop: space.sm }}>
        <View style={{ flex: 1 }}>
          <Text style={{ color: c.text, fontSize: 14 }}>Public profile</Text>
          <Text style={{ color: c.textDim, fontSize: 11 }}>
            {me.profile_public ? 'Anyone can follow you at once.' : 'Follows need your approval.'}
          </Text>
        </View>
        <Switch
          value={me.profile_public}
          disabled={publicBusy}
          onValueChange={(v) => void setPublic(v)}
          trackColor={{ true: c.accent }}
          accessibilityLabel="Public profile"
        />
      </View>
    </>
  );
}

/**
 * A labelled value with an Edit affordance that turns into a text field, a live rule
 * under it, and Save/Cancel. The rule (`problem`) is the phone's copy of the server's;
 * the server's own refusal (`describeError`) replaces it when the save comes back.
 */
function InlineField({
  label,
  value,
  placeholder,
  empty,
  prefix = '',
  maxLength,
  hint,
  normalize = (raw) => raw,
  problem,
  canSave,
  onSave,
  describeError,
  autoCapitalize,
}: {
  label: string;
  value: string | null;
  placeholder: string;
  empty: string;
  prefix?: string;
  maxLength: number;
  hint: string;
  normalize?: (raw: string) => string;
  problem: (raw: string) => string | null;
  canSave: (raw: string) => boolean;
  onSave: (raw: string) => Promise<void>;
  describeError: (e: ApiError) => string;
  autoCapitalize?: 'none' | 'sentences' | 'words' | 'characters';
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState('');
  const [saving, setSaving] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);

  const begin = () => {
    setDraft(value ?? '');
    setServerError(null);
    setEditing(true);
  };

  const save = async () => {
    if (saving || !canSave(draft)) return;
    setSaving(true);
    setServerError(null);
    try {
      await onSave(draft);
      setEditing(false);
    } catch (e) {
      setServerError(
        e instanceof ApiError ? describeError(e) : e instanceof Error ? e.message : 'could not save'
      );
    } finally {
      setSaving(false);
    }
  };

  if (!editing) {
    return (
      <View style={{ flexDirection: 'row', alignItems: 'center', minHeight: TAP_TARGET }}>
        <View style={{ flex: 1 }}>
          <Text style={{ color: c.textDim, fontSize: 11 }}>{label}</Text>
          <Text style={{ color: value ? c.text : c.textDim, fontSize: 15 }} numberOfLines={1}>
            {value ? `${prefix}${value}` : empty}
          </Text>
        </View>
        <Pressable
          onPress={begin}
          accessibilityRole="button"
          accessibilityLabel={`Edit ${label.toLowerCase()}`}
          style={({ pressed }) => [
            { minHeight: TAP_TARGET, minWidth: TAP_TARGET, justifyContent: 'center', alignItems: 'flex-end' },
            pressed && { opacity: 0.6 },
          ]}
        >
          <Text style={{ color: c.accent, fontSize: 14, fontWeight: '600' }}>Edit</Text>
        </Pressable>
      </View>
    );
  }

  const rule = serverError ?? problem(draft);
  const ok = !saving && canSave(draft);
  return (
    <View style={{ paddingVertical: space.sm }}>
      <Text style={{ color: c.textDim, fontSize: 11, marginBottom: space.xs }}>{label}</Text>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: space.sm }}>
        {prefix ? <Text style={{ color: c.textDim, fontSize: 16 }}>{prefix}</Text> : null}
        <TextInput
          value={draft}
          onChangeText={(t) => {
            setServerError(null);
            setDraft(normalize(t).slice(0, maxLength));
          }}
          placeholder={placeholder}
          placeholderTextColor={c.textDim}
          autoCapitalize={autoCapitalize}
          autoCorrect={false}
          autoFocus
          editable={!saving}
          onSubmitEditing={() => void save()}
          returnKeyType="done"
          style={{
            flex: 1,
            color: c.text,
            backgroundColor: c.bg,
            borderRadius: 8,
            paddingHorizontal: space.md,
            minHeight: TAP_TARGET,
            fontSize: 16,
          }}
        />
      </View>
      <Text style={{ color: rule ? c.danger : c.textDim, fontSize: 11, marginTop: space.xs }}>{rule ?? hint}</Text>
      <View style={{ flexDirection: 'row', justifyContent: 'flex-end', gap: space.md, marginTop: space.xs }}>
        <Pressable
          onPress={() => setEditing(false)}
          disabled={saving}
          accessibilityRole="button"
          style={({ pressed }) => [
            { minHeight: TAP_TARGET, minWidth: TAP_TARGET, justifyContent: 'center', paddingHorizontal: space.sm },
            pressed && { opacity: 0.6 },
          ]}
        >
          <Text style={{ color: c.textDim, fontSize: 14, fontWeight: '600' }}>Cancel</Text>
        </Pressable>
        <Pressable
          onPress={() => void save()}
          disabled={!ok}
          accessibilityRole="button"
          style={({ pressed }) => [
            {
              minHeight: TAP_TARGET,
              minWidth: TAP_TARGET,
              justifyContent: 'center',
              paddingHorizontal: space.md,
              borderRadius: 10,
              backgroundColor: c.accent,
              opacity: ok ? 1 : 0.4,
            },
            pressed && ok && { opacity: 0.7 },
          ]}
        >
          <Text style={{ color: c.onAccent, fontSize: 14, fontWeight: '700' }}>{saving ? 'Saving…' : 'Save'}</Text>
        </Pressable>
      </View>
    </View>
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
      <Text style={{ color: destructive ? c.danger : c.text, fontWeight: '600' }}>{label}</Text>
    </Pressable>
  );
}
