import React from 'react';
import { Pressable, Text, View } from 'react-native';

import type { SessionDetail } from '../data/api';
import { PixelSprite } from '../pixel/PixelSprite';
import { colors, space } from '../theme';
import { livePresenceLine, liveStatusLine, spriteForLive, tempoForLive } from './format';

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
            {/* Bit hammers while someone is at the keyboard and sleeps once the agent has
                been alone past tauAutonomousSec — the same rule as the presence line — and
                works quicker the fresher the row's last record is. */}
            <PixelSprite state={spriteForLive(s)} size={32} tempo={tempoForLive(s)} />
            <View style={{ flex: 1 }}>
              <Text style={{ color: c.text, fontWeight: '600', fontSize: 15 }} numberOfLines={1}>
                {s.repo_name ?? 'private repo'}
              </Text>
              {s.title ? (
                <Text style={{ color: c.textDim, fontSize: 13, marginTop: 2 }} numberOfLines={1}>
                  {s.title}
                </Text>
              ) : null}
            </View>
          </View>
          <Text style={{ color: c.accent, fontSize: 13, marginTop: space.sm, fontVariant: ['tabular-nums'] }}>
            {liveStatusLine(s)}
          </Text>
          <Text style={{ color: c.textDim, fontSize: 12, marginTop: 2 }}>{livePresenceLine(s)}</Text>
        </Pressable>
      ))}
    </View>
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
