import { CameraView, useCameraPermissions, type BarcodeScanningResult } from 'expo-camera';
import * as Haptics from 'expo-haptics';
import { Link, useLocalSearchParams, useRouter } from 'expo-router';
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Linking, Pressable, Text, View } from 'react-native';

import { api } from '../src/data/client';
import { parsePairingCode } from '../src/pairing/parse';
import { PixelSprite } from '../src/pixel/PixelSprite';
import type { SpriteState } from '../src/pixel/sprites';
import { colors, space } from '../src/theme';

/**
 * "Connect your Mac": scan the code `builder pair` shows instead of typing it.
 *
 * The Mac prints a user code and a QR of the same code (or a URL carrying it). Approving it
 * here attaches that Mac to this account — the same call the typed path makes, so a scan
 * that cannot be read falls back to typing with nothing lost.
 */

const c = colors('dark');

type Status =
  | { kind: 'idle'; text: string }
  | { kind: 'busy'; text: string }
  | { kind: 'ok'; text: string }
  | { kind: 'error'; text: string };

const RESCAN_DELAY_MS = 1500;
/** Long enough to see Bit cheer once (three frames at 4 fps is 750 ms) and read the label. */
const LEAVE_DELAY_MS = 2000;

export default function PairScreen() {
  const router = useRouter();
  // `builder://pair?code=XXXX-XXXX` is the QR's payload. Scanned by the iOS Camera app it
  // opens this screen directly, so the code arrives as a param and no preview is needed.
  const { code: paramCode } = useLocalSearchParams<{ code?: string }>();
  const [permission, requestPermission] = useCameraPermissions();
  const [signedIn, setSignedIn] = useState<boolean | null>(null);
  const [status, setStatus] = useState<Status>({
    kind: 'idle',
    text: 'Point the camera at the code on your Mac.',
  });
  // A QR in frame fires the scanner many times a second; one approval per code.
  const lockRef = useRef(false);
  const timers = useRef<ReturnType<typeof setTimeout>[]>([]);

  useEffect(() => {
    void api.isSignedIn().then(setSignedIn);
    return () => {
      for (const t of timers.current) clearTimeout(t);
    };
  }, []);

  const granted = permission?.granted ?? false;
  const canAsk = permission?.canAskAgain ?? true;
  useEffect(() => {
    if (permission && !granted && canAsk) void requestPermission();
  }, [permission, granted, canAsk, requestPermission]);

  const later = useCallback((fn: () => void, ms: number) => {
    timers.current.push(setTimeout(fn, ms));
  }, []);

  const approve = useCallback(
    async (text: string) => {
      if (lockRef.current) return;
      lockRef.current = true;

      const code = parsePairingCode(text);
      if (!code) {
        setStatus({ kind: 'error', text: 'That is not a Builder pairing code.' });
        later(() => {
          lockRef.current = false;
        }, RESCAN_DELAY_MS);
        return;
      }

      setStatus({ kind: 'busy', text: `Pairing ${code}…` });
      try {
        const paired = await api.approvePairing(code);
        void Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success).catch(() => {});
        setStatus({ kind: 'ok', text: `Paired with ${paired.label}.` });
        later(() => router.back(), LEAVE_DELAY_MS);
      } catch {
        void Haptics.notificationAsync(Haptics.NotificationFeedbackType.Error).catch(() => {});
        setStatus({ kind: 'error', text: 'That code was not recognised, or it expired. Try again.' });
        later(() => {
          lockRef.current = false;
        }, RESCAN_DELAY_MS);
      }
    },
    [later, router]
  );
  const onScanned = useCallback(
    (result: BarcodeScanningResult) => void approve(result.data),
    [approve]
  );

  const deepLinked = typeof paramCode === 'string' && paramCode.length > 0;
  useEffect(() => {
    if (deepLinked && signedIn) void approve(paramCode);
  }, [deepLinked, paramCode, signedIn, approve]);

  if (signedIn === false) {
    return (
      <Notice title="Sign in first">
        Pairing attaches your Mac to your account, so there has to be one.
        <TypeInstead label="Go to Settings" />
      </Notice>
    );
  }

  if (deepLinked) {
    return (
      <Notice title="Connecting your Mac" sprite={status.kind === 'ok' ? 'celebrating' : undefined}>
        {status.kind === 'idle' ? `Pairing ${parsePairingCode(paramCode) ?? paramCode}…` : status.text}
        {status.kind === 'error' ? <TypeInstead /> : null}
      </Notice>
    );
  }

  if (!permission || signedIn === null) {
    return <View style={{ flex: 1, backgroundColor: c.bg }} />;
  }

  if (!granted) {
    return canAsk ? (
      <Notice title="Camera access">
        Builder uses the camera only to read the pairing code on your Mac.
        <Button label="Allow camera" onPress={() => void requestPermission()} />
        <TypeInstead />
      </Notice>
    ) : (
      <Notice title="Camera access is off" sprite="idle">
        Allow the camera in iOS Settings to scan, or type the code on the Settings screen.
        <Button label="Open iOS Settings" onPress={() => void Linking.openSettings()} />
        <TypeInstead />
      </Notice>
    );
  }

  const scanning = status.kind === 'idle' || status.kind === 'error';

  return (
    <View style={{ flex: 1, backgroundColor: c.bg }}>
      <CameraView
        style={{ flex: 1 }}
        facing="back"
        barcodeScannerSettings={{ barcodeTypes: ['qr'] }}
        onBarcodeScanned={scanning ? onScanned : undefined}
      />
      {/* Framing guide, over the preview. Pointer events pass through to nothing; the
          camera does not need touches. */}
      <View pointerEvents="none" style={{ position: 'absolute', top: 0, left: 0, right: 0, bottom: 0, alignItems: 'center', justifyContent: 'center' }}>
        <View
          style={{
            width: 220,
            height: 220,
            borderRadius: 18,
            borderWidth: 2,
            borderColor: status.kind === 'ok' ? c.accent : c.overlayStroke,
          }}
        />
      </View>
      <View style={{ padding: space.md, paddingBottom: space.xl, backgroundColor: c.bg }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: space.md }}>
          {/* Bit appears only for the cheer; the camera preview is the content until then. */}
          {status.kind === 'ok' ? <PixelSprite state="celebrating" size={48} fps={4} /> : null}
          <Text
            style={{
              color: status.kind === 'error' ? c.danger : status.kind === 'ok' ? c.accent : c.text,
              fontSize: 15,
              fontWeight: status.kind === 'idle' ? '400' : '600',
              textAlign: 'center',
              flexShrink: 1,
            }}
          >
            {status.text}
          </Text>
        </View>
        <TypeInstead />
      </View>
    </View>
  );
}

function Notice({
  title,
  sprite,
  children,
}: {
  title: string;
  /** 48pt Bit beside the title: `idle` when the camera is off, `celebrating` on success. */
  sprite?: SpriteState;
  children: React.ReactNode;
}) {
  return (
    <View style={{ flex: 1, backgroundColor: c.bg, padding: space.md }}>
      <View style={{ backgroundColor: c.card, borderRadius: 12, padding: space.md }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: space.md, marginBottom: space.sm }}>
          {sprite ? <PixelSprite state={sprite} size={48} fps={4} /> : null}
          <Text style={{ color: c.text, fontSize: 16, fontWeight: '600', flex: 1 }}>{title}</Text>
        </View>
        <Text style={{ color: c.textDim, fontSize: 13, lineHeight: 19 }}>{children}</Text>
      </View>
    </View>
  );
}

function TypeInstead({ label = 'Type it instead' }: { label?: string }) {
  return (
    <Link href="/settings" asChild>
      <Pressable style={{ alignSelf: 'center', paddingVertical: space.md }}>
        <Text style={{ color: c.accent, fontSize: 14, fontWeight: '600' }}>{label}</Text>
      </Pressable>
    </Link>
  );
}

function Button({ label, onPress }: { label: string; onPress: () => void }) {
  return (
    <Pressable
      onPress={onPress}
      style={({ pressed }) => [
        {
          paddingVertical: space.md,
          alignItems: 'center',
          borderRadius: 10,
          marginTop: space.md,
          backgroundColor: c.bg,
        },
        pressed && { opacity: 0.6 },
      ]}
    >
      <Text style={{ color: c.text, fontWeight: '600' }}>{label}</Text>
    </Pressable>
  );
}
