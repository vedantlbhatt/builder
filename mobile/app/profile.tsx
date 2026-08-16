import React, { useEffect, useState } from 'react';
import { ScrollView, Text, useWindowDimensions, View } from 'react-native';

import * as cache from '../src/data/cache';
import { api } from '../src/data/client';
import type { Profile } from '../src/data/api';
import { ContributionGrid } from '../src/profile/ContributionGrid';
import { colors, duration, space } from '../src/theme';

const c = colors('dark');

export default function ProfileScreen() {
  const { width } = useWindowDimensions();
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

  return (
    <ScrollView
      style={{ flex: 1, backgroundColor: c.bg }}
      contentContainerStyle={{ padding: space.md, paddingBottom: space.xxl }}
    >
      <Card>
        <Text style={{ color: c.text, fontSize: 30, fontWeight: '700' }}>
          {duration(profile.totals.active_seconds)}
        </Text>
        <Text style={{ color: c.textDim, fontSize: 13 }}>
          across {profile.totals.sessions} sessions
        </Text>
      </Card>

      <Section title="Active hours">
        <ContributionGrid days={recentDays} width={contentWidth} />
        <Text style={{ color: c.textDim, fontSize: 11, marginTop: space.sm }}>
          Coloured by hours, not tokens. Hours are the metric every editor has.
        </Text>
      </Section>

      <Section title="Projects">
        {profile.projects.map((p) => (
          <View
            key={p.key}
            style={{ flexDirection: 'row', justifyContent: 'space-between', paddingVertical: 6 }}
          >
            <Text style={{ color: c.text, fontSize: 14 }} numberOfLines={1}>
              {p.name ?? `private · ${p.key}`}
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

function Row({ label, value }: { label: string; value: string }) {
  return (
    <View style={{ flexDirection: 'row', justifyContent: 'space-between', paddingVertical: 6 }}>
      <Text style={{ color: c.textDim, fontSize: 14 }}>{label}</Text>
      <Text style={{ color: c.text, fontSize: 14, fontWeight: '600' }}>{value}</Text>
    </View>
  );
}
