import * as AppleAuthentication from 'expo-apple-authentication';
import Constants from 'expo-constants';
import { useRouter } from 'expo-router';
import React, { useCallback, useEffect, useState } from 'react';
import * as ReactNative from 'react-native';
import { Alert, Pressable, ScrollView, Switch, Text, TextInput, View } from 'react-native';

import { isGoogleConfigured, onGoogleSignIn, startGoogleSignIn } from '../src/auth/googleFlow';
import { ApiError, type CaptureKey, type CaptureKeyCreated, type Me } from '../src/data/api';
import * as cache from '../src/data/cache';
import {
  appendKey,
  atKeyCap,
  CAPTURE_KEY_DEFAULT_NAME,
  CAPTURE_KEY_MAX_LIVE,
  CAPTURE_KEY_NAME_MAX,
  CAPTURE_KEY_PASTE_HINT,
  captureKeyNameProblem,
  keyLabel,
  lastUsedLabel,
  withoutKey,
  hookInstallSnippet,
} from '../src/data/captureKeys';
import { api, API_BASE_URL } from '../src/data/client';
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
                  // SEEN ON WEB: a text input's intrinsic width (its `size`) is a flex
                  // minimum there, so `flex: 1` alone let it push the Scan button off the
                  // card. minWidth 0 lets it shrink; a no-op on iOS.
                  minWidth: 0,
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
                    flexShrink: 0,
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

          <Section title="Cloud capture">
            <CaptureKeysPanel />
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
 * Capture keys: the credential a Claude Code cloud container uploads with, because the
 * pairing flow's rotating refresh token cannot be shared between containers
 * (docs/cloud-capture.md). The list shows name, prefix and last use; "New key" shows the
 * plaintext ONCE, with a copy button, and forgets it when dismissed — the server keeps a
 * hash, so there is no second look. Revoke asks first: the container holding that key
 * gets a 401 from its next upload on.
 */
function CaptureKeysPanel() {
  const [keys, setKeys] = useState<CaptureKey[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [name, setName] = useState('');
  const [minting, setMinting] = useState(false);
  const [mintError, setMintError] = useState<string | null>(null);
  const [created, setCreated] = useState<CaptureKeyCreated | null>(null);
  const [setupCopied, setSetupCopied] = useState(false);
  const [copied, setCopied] = useState(false);
  const [revoking, setRevoking] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .captureKeys()
      .then((r) => {
        if (!cancelled) setKeys(r.keys);
      })
      .catch((e: unknown) => {
        if (!cancelled) setLoadError(e instanceof Error ? e.message : 'could not load your keys');
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const mint = useCallback(async () => {
    if (minting) return;
    const chosen = name.trim() || CAPTURE_KEY_DEFAULT_NAME;
    if (captureKeyNameProblem(chosen)) return;
    setMinting(true);
    setMintError(null);
    try {
      const made = await api.createCaptureKey(chosen);
      setCreated(made);
      setSetupCopied(false);
      setCopied(false);
      setName('');
      setKeys((prev) => appendKey(prev ?? [], { ...made, last_used_at: null }));
    } catch (e) {
      setMintError(
        e instanceof ApiError && e.status === 409
          ? `You already have ${CAPTURE_KEY_MAX_LIVE} keys. Revoke one first.`
          : e instanceof Error
            ? e.message
            : 'could not create a key'
      );
    } finally {
      setMinting(false);
    }
  }, [minting, name]);

  const copy = useCallback(() => {
    if (!created) return;
    // React Native's Clipboard is deprecated in favour of expo-clipboard, which is not a
    // dependency yet; accessed through the namespace so the deprecation notice fires on
    // the tap, not at app start. The key is also `selectable` below for long-press copy.
    ReactNative.Clipboard.setString(created.key);
    setCopied(true);
  }, [created]);

  const revoke = useCallback((k: CaptureKey) => {
    Alert.alert(
      `Revoke ${k.name}?`,
      `${keyLabel(k.key_prefix)} stops working immediately. Anything still using it will fail to upload until you mint a new key and paste it there.`,
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Revoke',
          style: 'destructive',
          onPress: async () => {
            setRevoking(k.id);
            try {
              await api.revokeCaptureKey(k.id);
              setKeys((prev) => withoutKey(prev ?? [], k.id));
              setCreated((cur) => (cur && cur.id === k.id ? null : cur));
            } catch (e) {
              Alert.alert('Could not revoke', e instanceof Error ? e.message : 'try again');
            } finally {
              setRevoking(null);
            }
          },
        },
      ]
    );
  }, []);

  const nameProblem = name.trim() ? captureKeyNameProblem(name) : null;
  const capped = keys !== null && atKeyCap(keys);
  const canMint = !minting && keys !== null && !capped && nameProblem === null;

  return (
    <>
      <Text style={{ color: c.textDim, fontSize: 13, lineHeight: 19, marginBottom: space.sm }}>
        Sessions from claude.ai/code run in a cloud container the Mac agent never sees. A
        capture key lets that container upload them — and do nothing else.
      </Text>

      {created && (
        <View
          style={{
            backgroundColor: c.bg,
            borderRadius: 10,
            padding: space.md,
            marginBottom: space.md,
            borderWidth: 1,
            borderColor: c.accent,
          }}
        >
          <Text style={{ color: c.text, fontSize: 13, fontWeight: '700' }}>
            {created.name} — copy it now
          </Text>
          <Text style={{ color: c.textDim, fontSize: 11, marginTop: space.xs, lineHeight: 16 }}>
            This is the only time the key is shown. {CAPTURE_KEY_PASTE_HINT}
          </Text>
          <Text
            selectable
            accessibilityLabel="Capture key"
            style={{
              color: c.text,
              fontSize: 13,
              fontFamily: 'Menlo',
              marginTop: space.sm,
              padding: space.sm,
              backgroundColor: c.card,
              borderRadius: 8,
            }}
          >
            {created.key}
          </Text>
          <View style={{ flexDirection: 'row', justifyContent: 'flex-end', gap: space.md, marginTop: space.xs }}>
            <Pressable
              onPress={() => setCreated(null)}
              accessibilityRole="button"
              style={({ pressed }) => [
                { minHeight: TAP_TARGET, minWidth: TAP_TARGET, justifyContent: 'center', paddingHorizontal: space.sm },
                pressed && { opacity: 0.6 },
              ]}
            >
              <Text style={{ color: c.textDim, fontSize: 14, fontWeight: '600' }}>Done</Text>
            </Pressable>
            <Pressable
              onPress={copy}
              accessibilityRole="button"
              accessibilityLabel="Copy capture key"
              style={({ pressed }) => [
                {
                  minHeight: TAP_TARGET,
                  minWidth: TAP_TARGET,
                  justifyContent: 'center',
                  paddingHorizontal: space.md,
                  borderRadius: 10,
                  backgroundColor: c.accent,
                },
                pressed && { opacity: 0.7 },
              ]}
            >
              <Text style={{ color: c.onAccent, fontSize: 14, fontWeight: '700' }}>{copied ? 'Copied' : 'Copy'}</Text>
            </Pressable>
          </View>
          <Text style={{ color: c.text, fontSize: 14, fontWeight: '600', marginTop: space.md }}>
            Set up the hook (paste once in a terminal)
          </Text>
          <Text
            selectable
            style={{
              color: c.textDim,
              fontFamily: 'Menlo',
              fontSize: 11,
              lineHeight: 15,
              marginTop: space.xs,
            }}
          >
            {hookInstallSnippet(API_BASE_URL, created.key)}
          </Text>
          <Pressable
            onPress={() => {
              ReactNative.Clipboard.setString(hookInstallSnippet(API_BASE_URL, created.key));
              setSetupCopied(true);
            }}
            accessibilityRole="button"
            accessibilityLabel="Copy hook setup commands"
            style={({ pressed }) => [
              {
                minHeight: TAP_TARGET,
                justifyContent: 'center',
                alignSelf: 'flex-start',
                paddingHorizontal: space.md,
                borderRadius: 10,
                backgroundColor: c.bg,
                borderWidth: 1,
                borderColor: c.accent,
                marginTop: space.sm,
              },
              pressed && { opacity: 0.7 },
            ]}
          >
            <Text style={{ color: c.accent, fontSize: 14, fontWeight: '700' }}>
              {setupCopied ? 'Setup copied' : 'Copy setup'}
            </Text>
          </Pressable>
        </View>
      )}

      {keys === null ? (
        <Text style={{ color: c.textDim, fontSize: 13 }}>{loadError ?? 'Loading your keys…'}</Text>
      ) : keys.length === 0 ? (
        <Text style={{ color: c.textDim, fontSize: 13 }}>No keys yet.</Text>
      ) : (
        keys.map((k) => (
          <View
            key={k.id}
            style={{ flexDirection: 'row', alignItems: 'center', minHeight: TAP_TARGET, gap: space.sm }}
          >
            <View style={{ flex: 1 }}>
              <Text style={{ color: c.text, fontSize: 15 }} numberOfLines={1}>
                {k.name}
              </Text>
              <Text style={{ color: c.textDim, fontSize: 11, fontVariant: ['tabular-nums'] }}>
                {keyLabel(k.key_prefix)} · {lastUsedLabel(k.last_used_at)}
              </Text>
            </View>
            <Pressable
              onPress={() => revoke(k)}
              disabled={revoking === k.id}
              accessibilityRole="button"
              accessibilityLabel={`Revoke ${k.name}`}
              style={({ pressed }) => [
                { minHeight: TAP_TARGET, minWidth: TAP_TARGET, justifyContent: 'center', alignItems: 'flex-end' },
                (pressed || revoking === k.id) && { opacity: 0.6 },
              ]}
            >
              <Text style={{ color: c.danger, fontSize: 14, fontWeight: '600' }}>
                {revoking === k.id ? 'Revoking…' : 'Revoke'}
              </Text>
            </Pressable>
          </View>
        ))
      )}

      <View style={{ flexDirection: 'row', alignItems: 'center', gap: space.sm, marginTop: space.md }}>
        <TextInput
          value={name}
          onChangeText={(t) => {
            setMintError(null);
            setName(t.slice(0, CAPTURE_KEY_NAME_MAX + 8));
          }}
          placeholder={CAPTURE_KEY_DEFAULT_NAME}
          placeholderTextColor={c.textDim}
          autoCapitalize="none"
          autoCorrect={false}
          editable={!minting}
          onSubmitEditing={() => void mint()}
          returnKeyType="done"
          accessibilityLabel="New key name"
          style={{
            flex: 1,
            color: c.text,
            backgroundColor: c.bg,
            borderRadius: 8,
            paddingHorizontal: space.md,
            minHeight: TAP_TARGET,
            fontSize: 15,
          }}
        />
        <Pressable
          onPress={() => void mint()}
          disabled={!canMint}
          accessibilityRole="button"
          style={({ pressed }) => [
            {
              minHeight: TAP_TARGET,
              justifyContent: 'center',
              paddingHorizontal: space.md,
              borderRadius: 10,
              backgroundColor: c.accent,
              opacity: canMint ? 1 : 0.4,
            },
            pressed && canMint && { opacity: 0.7 },
          ]}
        >
          <Text style={{ color: c.onAccent, fontSize: 14, fontWeight: '700' }}>{minting ? 'Minting…' : 'New key'}</Text>
        </Pressable>
      </View>
      <Text style={{ color: mintError || nameProblem ? c.danger : c.textDim, fontSize: 11, marginTop: space.xs }}>
        {mintError ??
          nameProblem ??
          (capped
            ? `Up to ${CAPTURE_KEY_MAX_LIVE} keys; revoke one to make room.`
            : 'Name it after where it lives. One key per cloud environment is plenty.')}
      </Text>
    </>
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
