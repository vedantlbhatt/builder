import { useRouter } from 'expo-router';
import React, { useEffect, useState } from 'react';
import { Pressable, ScrollView, Text, useWindowDimensions, View } from 'react-native';

import * as cache from '../src/data/cache';
import { api } from '../src/data/client';
import type { Profile } from '../src/data/api';
import { LiveSessions } from '../src/live/LiveSessions';
import { PixelSprite } from '../src/pixel/PixelSprite';
import { BuilderProfileCard } from '../src/profile/BuilderProfileCard';
import { ContributionGrid } from '../src/profile/ContributionGrid';
import { colors, duration, space } from '../src/theme';

const c = colors('dark');

export default function ProfileScreen() {
  const { width } = useWindowDimensions();
  const router = useRouter();
  const [profile, setProfile] = useState<Profile | null>(null);

  useEffect(() => {
    (async () => {
      setProfile(await cache.getProfile());
      try {
        const fresh = await api.profile();
        await cache.putProfile(fresh);
        setProfile(fresh);
      } catch {
        // Cached profile is a fine answer offline.
      }
    })();
  }, []);

  if (!profile) {
    return (
      <View style={{ flex: 1, backgroundColor: c.bg, padding: space.md }}>
        <Text style={{ color: c.textDim }}>Sign in to see your profile.</Text>
      </View>
    );
  }

  const contentWidth = width - space.md * 4;
  const recentDays = profile.graph.slice(-119);
  const attended = profile.attribution.attended_seconds;
  const autonomous = profile.attribution.autonomous_seconds;
  // Only a server that splits the clocks gets the pair; "0 / 0" from an older one would
  // be a plausible wrong number.
  const hasSplit = attended !== undefined || autonomous !== undefined;
  // The record is attended time on a v2 server. An older server ranks by active time and
  // omits `attended_seconds`, so the label follows the field rather than claiming a split
  // that was never made.
  const longest = profile.longest_session;
  const longestLabel = longest?.attended_seconds !== undefined ? 'Longest attended' : 'Longest session';
  const longestValue = longest ? duration(longest.attended_seconds ?? longest.active_seconds) : null;

  return (
    <ScrollView
      style={{ flex: 1, backgroundColor: c.bg }}
      contentContainerStyle={{ padding: space.md, paddingBottom: space.xxl }}
    >
      <LiveSessions sessions={profile.live ?? []} onPress={(id) => router.push(`/session/${id}`)} />

      <Pressable
        onPress={() => router.push('/factions')}
        style={({ pressed }) => [
          {
            backgroundColor: c.card,
            borderRadius: 12,
            padding: space.md,
            marginBottom: space.md,
            flexDirection: 'row',
            alignItems: 'center',
          },
          pressed && { opacity: 0.7 },
        ]}
      >
        <Text style={{ color: c.text, fontSize: 15, fontWeight: '600', flex: 1 }}>Factions</Text>
        <Text style={{ color: c.textDim, fontSize: 13 }}>weekly board ›</Text>
      </Pressable>

      <Card>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: space.md }}>
          <View style={{ flex: 1 }}>
            <Text style={{ color: c.text, fontSize: 30, fontWeight: '700' }}>
              {duration(profile.totals.active_seconds)}
            </Text>
            <Text style={{ color: c.textDim, fontSize: 13 }}>
              across {profile.totals.sessions} sessions
            </Text>
          </View>
          <PixelSprite state="idle" size={48} fps={2} />
        </View>
        {(hasSplit || longestValue) && (
          <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: space.xl, marginTop: space.md }}>
            {hasSplit && <Stat label="Attended" value={duration(attended ?? 0)} />}
            {hasSplit && <Stat label="Autonomous" value={duration(autonomous ?? 0)} />}
            {longestValue && <Stat label={longestLabel} value={longestValue} />}
          </View>
        )}
      </Card>

      {/* Only a server that computes the aggregate gets the card; an older one is silent
          rather than told to analyse three sessions it will never aggregate. */}
      {profile.builder_profile !== undefined && (
        <Section title="How you build">
          <BuilderProfileCard profile={profile.builder_profile} />
        </Section>
      )}

      <Section title="Active hours">
        <ContributionGrid days={recentDays} width={contentWidth} />
        <Text style={{ color: c.textDim, fontSize: 11, marginTop: space.sm }}>
          Coloured by hours, not tokens. Hours are the metric every editor has.
        </Text>
      </Section>

      <Section title="Projects">
        {profile.projects.map((p, i) => (
          <View
            key={p.key}
            style={{ flexDirection: 'row', justifyContent: 'space-between', paddingVertical: 6 }}
          >
            <Text style={{ color: c.text, fontSize: 14 }} numberOfLines={1}>
              {/* A private repo's key is a hash. Showing it reads as a bug; a letter per
                  repo tells them apart, which is the only job the label has. */}
              {p.name ?? `Private repo ${String.fromCharCode(65 + i)}`}
            </Text>
            <Text style={{ color: c.textDim, fontSize: 13 }}>
              {duration(p.active_seconds)} · {p.sessions}
            </Text>
          </View>
        ))}
      </Section>

      <Section title="Human vs agent">
        <Row label="Lines from the agent" value={profile.attribution.agent_lines.toLocaleString()} />
        <Row label="Your edits outside the agent" value={`${profile.attribution.human_edit_events} events`} />
        <Row label="Prompts you typed" value={profile.attribution.prompts.toLocaleString()} />
        <Text style={{ color: c.textDim, fontSize: 11, marginTop: space.sm }}>
          Three separately measured numbers, deliberately not combined into a percentage.
          Your edits are recorded as events without line counts, so any ratio would be
          false precision.
        </Text>
      </Section>
    </ScrollView>
  );
}

function Card({ children }: { children: React.ReactNode }) {
  return (
    <View style={{ backgroundColor: c.card, borderRadius: 12, padding: space.md }}>{children}</View>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <View style={{ marginTop: space.lg }}>
      <Text style={{ color: c.textDim, fontSize: 11, fontWeight: '700', letterSpacing: 0.8, marginBottom: space.sm }}>
        {title.toUpperCase()}
      </Text>
      <Card>{children}</Card>
    </View>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <View>
      <Text style={{ color: c.text, fontSize: 18, fontWeight: '700', fontVariant: ['tabular-nums'] }}>
        {value}
      </Text>
      <Text style={{ color: c.textDim, fontSize: 11 }}>{label}</Text>
    </View>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <View style={{ flexDirection: 'row', justifyContent: 'space-between', paddingVertical: 6 }}>
      <Text style={{ color: c.textDim, fontSize: 14 }}>{label}</Text>
      <Text style={{ color: c.text, fontSize: 14, fontWeight: '600' }}>{value}</Text>
    </View>
  );
}
