import { Link, useFocusEffect, useRouter } from 'expo-router';
import React, { useCallback, useState } from 'react';
import {
  ActivityIndicator,
  Pressable,
  RefreshControl,
  ScrollView,
  Text,
  useWindowDimensions,
  View,
} from 'react-native';

import { api, SAMPLE_SESSION } from '../src/data/client';
import * as cache from '../src/data/cache';
import type { SessionDetail } from '../src/data/api';
import { LiveSessions } from '../src/live/LiveSessions';
import { PixelBadge } from '../src/pixel/PixelBadge';
import { TimelineStrip } from '../src/strip/TimelineStrip';
import { decodeMarks } from '../src/strip/decode';
import { colors, duration, space } from '../src/theme';

const c = colors('dark');

/** How often the list re-syncs while it is on screen; matches the Mac's live upload cadence. */
const LIVE_REFRESH_MS = 60_000;

export default function SessionsScreen() {
  const { width } = useWindowDimensions();
  const router = useRouter();
  const [sessions, setSessions] = useState<SessionDetail[]>([]);
  const [live, setLive] = useState<SessionDetail[]>([]);
  const [signedIn, setSignedIn] = useState<boolean | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    // Cache first, always. On a cold launch over cellular this is the difference between
    // real content immediately and a spinner — and if the network is gone, stale data is
    // a far better answer than an empty screen.
    const cached = await cache.listSessions(50);
    if (cached.length) setSessions(cached);
    setLive(await cache.listLive());

    const isIn = await api.isSignedIn();
    setSignedIn(isIn);
    if (!isIn) {
      setSessions(cached.length ? cached : [SAMPLE_SESSION]);
      setLive([]);
      return;
    }

    try {
      await cache.sync(api);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'could not reach the server');
    }
    // Whatever the sync managed to save is shown, banner or not.
    setSessions(await cache.listSessions(50));
    setLive(await cache.listLive());
  }, []);

  // On focus, not on mount: coming back from Settings after signing in should show the
  // user's own sessions without a manual pull-to-refresh. While focused, re-sync once a
  // minute so a live session's numbers move; the interval dies on blur.
  useFocusEffect(
    useCallback(() => {
      void load();
      const timer = setInterval(() => {
        void load();
      }, LIVE_REFRESH_MS);
      return () => clearInterval(timer);
    }, [load])
  );

  const onRefresh = useCallback(async () => {
    setRefreshing(true);
    await load();
    setRefreshing(false);
  }, [load]);

  if (signedIn === null) {
    return (
      <View style={{ flex: 1, justifyContent: 'center' }}>
        <ActivityIndicator color={c.accent} />
      </View>
    );
  }

  const stripWidth = width - space.md * 2 - space.md * 2;

  return (
    <ScrollView
      style={{ flex: 1, backgroundColor: c.bg }}
      contentContainerStyle={{ padding: space.md, paddingBottom: space.xxl }}
      refreshControl={
        <RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={c.accent} />
      }
    >
      <View style={{ flexDirection: 'row', gap: space.sm, marginBottom: space.md }}>
        <Link href="/feed" asChild>
          <Pressable style={pill}>
            <Text style={{ color: c.text }}>Feed</Text>
          </Pressable>
        </Link>
        <Link href="/profile" asChild>
          <Pressable style={pill}>
            <Text style={{ color: c.text }}>Profile</Text>
          </Pressable>
        </Link>
        <Link href="/settings" asChild>
          <Pressable style={pill}>
            <Text style={{ color: c.text }}>Settings</Text>
          </Pressable>
        </Link>
      </View>

      <LiveSessions sessions={live} onPress={(id) => router.push(`/session/${id}`)} />

      {!signedIn && (
        <PixelBadge
          state="waving"
          title="You are browsing a sample session"
          text="Sign in on the Settings tab to see your own. Nothing syncs until you do."
          style={banner}
        />
      )}

      {error && (
        <View style={[banner, { borderColor: c.textDim }]}>
          <Text style={{ color: c.textDim, fontSize: 13 }}>
            Showing saved sessions — {error}
          </Text>
        </View>
      )}

      {signedIn && sessions.length === 0 && !error && (
        <PixelBadge
          state="waving"
          text="No sessions yet. Pair your Mac in Settings and the first one you finish lands here."
          style={{ paddingHorizontal: 0 }}
        />
      )}

      {sessions.map((s) => (
        <Pressable
          key={s.id}
          onPress={() => router.push(`/session/${s.id}`)}
          style={({ pressed }) => [row, pressed && { opacity: 0.6 }]}
        >
          <View style={{ flexDirection: 'row', alignItems: 'baseline' }}>
            <Text style={{ color: c.text, fontWeight: '600', fontSize: 15, flex: 1 }} numberOfLines={1}>
              {s.title ?? duration(s.active_seconds)}
            </Text>
            <Text style={{ color: c.textDim, fontSize: 13, fontVariant: ['tabular-nums'] }}>
              {duration(s.active_seconds)}
            </Text>
          </View>

          {s.strip ? (
            <TimelineStrip
              cols={s.strip.cols}
              marks={decodeMarks(s.strip.marks)}
              spanMs={Math.max(1, s.strip.t1_ms - s.strip.t0_ms)}
              preset="row"
              width={stripWidth}
              style={{ marginVertical: space.sm }}
            />
          ) : (
            // A header-only session has no per-event detail — Cursor drops message bodies
            // at ~60 days. Saying so is better than drawing an empty bar that reads as a bug.
            <Text style={{ color: c.textDim, fontSize: 11, marginVertical: space.sm }}>
              timeline not available for this session
            </Text>
          )}

          <View style={{ flexDirection: 'row', gap: space.sm }}>
            <Text style={meta}>{s.repo_name ?? 'private repo'}</Text>
            <Text style={meta}>·</Text>
            <Text style={meta}>{new Date(s.started_at).toLocaleDateString()}</Text>
            {s.unattended && <Text style={meta}>· unattended</Text>}
          </View>
        </Pressable>
      ))}
    </ScrollView>
  );
}

const row = {
  backgroundColor: c.card,
  borderRadius: 12,
  padding: space.md,
  marginBottom: space.sm,
} as const;

const pill = {
  backgroundColor: c.card,
  borderRadius: 999,
  paddingHorizontal: space.md,
  paddingVertical: space.sm,
} as const;

const banner = {
  backgroundColor: c.card,
  borderRadius: 12,
  borderWidth: 1,
  borderColor: c.accent,
  padding: space.md,
  marginBottom: space.md,
} as const;

const meta = { color: c.textDim, fontSize: 12 } as const;
