import React, { useEffect, useRef } from 'react';
import { Animated, Pressable, Text, View } from 'react-native';

import type { SessionDetail } from '../data/api';
import { colors, space } from '../theme';
import { livePresenceLine, liveStatusLine } from './format';

/**
 * The block at the top of Sessions and Profile while the Mac is still working.
 *
 * A live row is a snapshot, not a session: it may carry a checkpoint analysis and its
 * numbers move every minute. It gets its own block rather than a place in the list so the
 * list stays a record of things that finished.
 */

const c = colors('dark');

export function LiveSessions({
  sessions,
  onPress,
}: {
  sessions: SessionDetail[];
  onPress: (id: string) => void;
}) {
  if (sessions.length === 0) return null;
  return (
    <View style={{ marginBottom: space.md }}>
      <Text
        style={{
          color: c.textDim,
          fontSize: 11,
          fontWeight: '700',
          letterSpacing: 0.8,
          marginBottom: space.sm,
        }}
      >
        LIVE NOW
      </Text>
      {sessions.map((s) => (
        <Pressable
          key={s.id}
          onPress={() => onPress(s.id)}
          style={({ pressed }) => [row, pressed && { opacity: 0.6 }]}
        >
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: space.sm }}>
            <PulsingDot />
            <Text style={{ color: c.text, fontWeight: '600', fontSize: 15, flex: 1 }} numberOfLines={1}>
              {s.repo_name ?? 'private repo'}
            </Text>
          </View>
          {s.title ? (
            <Text style={{ color: c.textDim, fontSize: 13, marginTop: 2 }} numberOfLines={1}>
              {s.title}
            </Text>
          ) : null}
          <Text style={{ color: c.accent, fontSize: 13, marginTop: space.sm, fontVariant: ['tabular-nums'] }}>
            {liveStatusLine(s)}
          </Text>
          <Text style={{ color: c.textDim, fontSize: 12, marginTop: 2 }}>{livePresenceLine(s)}</Text>
        </Pressable>
      ))}
    </View>
  );
}

function PulsingDot() {
  const opacity = useRef(new Animated.Value(1)).current;
  useEffect(() => {
    const loop = Animated.loop(
      Animated.sequence([
        Animated.timing(opacity, { toValue: 0.2, duration: 700, useNativeDriver: true }),
        Animated.timing(opacity, { toValue: 1, duration: 700, useNativeDriver: true }),
      ])
    );
    loop.start();
    return () => loop.stop();
  }, [opacity]);
  return (
    <Animated.View
      style={{ width: 8, height: 8, borderRadius: 4, backgroundColor: c.accent, opacity }}
    />
  );
}

const row = {
  backgroundColor: c.card,
  borderRadius: 12,
  borderWidth: 1,
  borderColor: c.border,
  padding: space.md,
  marginBottom: space.sm,
} as const;
