import { useRouter } from 'expo-router';
import React, { useEffect, useState } from 'react';
import { Pressable, ScrollView, Text, useWindowDimensions, View } from 'react-native';

import * as cache from '../src/data/cache';
import { api } from '../src/data/client';
import type { BuilderProfileResponse, Profile } from '../src/data/api';
import { LiveSessions } from '../src/live/LiveSessions';
import { ArchetypeHero } from '../src/profile/ArchetypeHero';
import { BuilderProfileCard } from '../src/profile/BuilderProfileCard';
import { ContributionGrid } from '../src/profile/ContributionGrid';
import { FactList } from '../src/profile/FactList';
import { archetypeSentence } from '../src/profile/narrative';
import { NarrativeSection } from '../src/profile/NarrativeSection';
import { ShareBars } from '../src/profile/ShareBars';
import { StripClass } from '../src/generated/strip';
import { colors, duration, space } from '../src/theme';

const c = colors('dark');

/** Where the builder profile is kept between launches, so a cold start is not blank. */
const CORPUS_KEY = 'profile.builder.v1';

export default function ProfileScreen() {
  const { width } = useWindowDimensions();
  const router = useRouter();
  const [profile, setProfile] = useState<Profile | null>(null);
  const [builder, setBuilder] = useState<BuilderProfileResponse | null>(null);

  useEffect(() => {
    (async () => {
      setProfile(await cache.getProfile());
      const stored = await cache.getKv(CORPUS_KEY);
      if (stored) {
        try {
          setBuilder(JSON.parse(stored) as BuilderProfileResponse);
        } catch {
          // A stored blob from an older shape is dropped, not repaired.
        }
      }
      try {
        const fresh = await api.profile();
        await cache.putProfile(fresh);
        setProfile(fresh);
      } catch {
        // Cached profile is a fine answer offline.
      }
      try {
        const b = await api.builderProfile();
        setBuilder(b);
        await cache.setKv(CORPUS_KEY, JSON.stringify(b));
      } catch {
        // A server without the route leaves the cached one, or nothing.
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

  const corpus = builder?.corpus ?? null;
  // Absent on a server older than 0016, and null until this account's own machine has
  // run `python -m capture narrative`. Both are the same thing on screen: nothing.
  const narrative = builder?.narrative ?? null;
  const missing = Object.entries(corpus?.sample.missing ?? {});

  return (
    <ScrollView
      style={{ flex: 1, backgroundColor: c.bg }}
      contentContainerStyle={{ padding: space.md, paddingBottom: space.xxl }}
    >
      <LiveSessions sessions={profile.live ?? []} onPress={(id) => router.push(`/session/${id}`)} />

      {/* The archetype is the top of the screen, not a card buried under the totals: it is
          the one line a person would say out loud about themselves. Only a server that
          computes it gets the hero; an older one keeps the hours card as the opener. */}
      {corpus && (
        <ArchetypeHero
          archetype={corpus.archetype}
          sessions={corpus.sample.sessions}
          sentence={archetypeSentence(narrative)}
        />
      )}

      {/* Directly under the archetype, because it is the sentence that explains it. A
          person who reads "quality guardian" and nothing else has learned a label; the
          paragraph under it is the part that is about them. */}
      {narrative && <NarrativeSection narrative={narrative} />}

      {corpus && corpus.facts.length > 0 && (
        <Section title="What stands out">
          <FactList facts={corpus.facts} />
        </Section>
      )}

      <Section title="The numbers">
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: space.md }}>
          <View style={{ flex: 1 }}>
            <Text style={{ color: c.text, fontSize: 30, fontWeight: '700' }}>
              {duration(profile.totals.active_seconds)}
            </Text>
            <Text style={{ color: c.textDim, fontSize: 13 }}>
              across {profile.totals.sessions} sessions
            </Text>
          </View>
        </View>
        <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: space.xl, marginTop: space.md }}>
          {hasSplit && <Stat label="Attended" value={duration(attended ?? 0)} />}
          {hasSplit && <Stat label="Autonomous" value={duration(autonomous ?? 0)} />}
          {longestValue && <Stat label={longestLabel} value={longestValue} />}
        </View>
        {corpus && (
          <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: space.xl, marginTop: space.md }}>
            <Stat label="Prompts" value={corpus.totals.total_prompts.toLocaleString()} />
            <Stat label="Agent lines" value={corpus.totals.total_lines_added.toLocaleString()} />
            {/* Absent, not zero. Two sessions that overlapped in one repo both counted
                the commits in between, so the sum would read high; the row is dropped
                rather than shown wrong. */}
            {corpus.totals.total_commits !== null && (
              <Stat label="Commits" value={corpus.totals.total_commits.toLocaleString()} />
            )}
            <Stat label="Tool calls" value={corpus.totals.total_tool_calls.toLocaleString()} />
          </View>
        )}
      </Section>

      {corpus && (
        <Section title="Where you stand">
          {/* The three questions a person actually asks about their own profile, and the
              one ranking the app is allowed to make. Every value is null-checked: a
              refused metric prints its reason further down rather than a zero here. */}
          {corpus.session_rank[0] && (
            <Row
              label={`Longest attended session (1 of ${corpus.ranked_sessions})`}
              value={duration(corpus.session_rank[0].attended_seconds)}
            />
          )}
          {typeof corpus.metrics.default_model?.value === 'string' && (
            <Row
              label="Model that did most of the work"
              value={String(corpus.metrics.default_model.value)}
            />
          )}
          {typeof corpus.metrics.shipping_day?.value === 'string' && (
            <Row label="Day the most code landed" value={String(corpus.metrics.shipping_day.value)} />
          )}
          {typeof corpus.metrics.busiest_day?.value === 'string' && (
            <Row label="Day you were at it longest" value={String(corpus.metrics.busiest_day.value)} />
          )}
          <Text style={{ color: c.textDim, fontSize: 11, marginTop: space.sm }}>
            Sessions are ranked on attended time, never on elapsed. An overnight run the
            agent did alone counts toward your hours and can never hold a record.
          </Text>
        </Section>
      )}

      <Section title="Active hours">
        <ContributionGrid days={recentDays} width={contentWidth} />
        <Text style={{ color: c.textDim, fontSize: 11, marginTop: space.sm }}>
          Coloured by hours, not tokens. Hours are the metric every editor has.
        </Text>
      </Section>

      {corpus && corpus.model_mix.length > 0 && (
        <Section title="Which models did the work">
          <ShareBars
            tint={c.accent}
            items={corpus.model_mix.map((m) => ({ label: m.model, share: m.share }))}
          />
          <Text style={{ color: c.textDim, fontSize: 11, marginTop: space.sm }}>
            Share of output tokens, which is the only per-model number that leaves your
            machine.
          </Text>
        </Section>
      )}

      {corpus && corpus.top_tools.length > 0 && (
        <Section title="What you reach for">
          <ShareBars
            tint={c.strip[StripClass.human_edit]}
            items={corpus.top_tools.map((t) => ({
              label: t.tool,
              share: t.share,
              detail: `${t.calls.toLocaleString()} calls`,
            }))}
          />
        </Section>
      )}

      {/* Only a server that computes the aggregate gets the card; an older one is silent
          rather than told to analyse three sessions it will never aggregate. */}
      {profile.builder_profile !== undefined && (
        <Section title="How you build">
          <BuilderProfileCard profile={profile.builder_profile} />
        </Section>
      )}

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

      {/* The refusals, in full. A metric this server cannot honestly compute is null with
          a reason, and printing those reasons is the difference between a profile you can
          trust and one you cannot check. Most of them say the same true thing: the words
          you type never leave your machine, so nothing derived from them can be measured
          here. */}
      {missing.length > 0 && (
        <Section title="What this cannot see">
          {missing.map(([key, reason]) => (
            <View key={key} style={{ paddingVertical: 5 }}>
              <Text style={{ color: c.text, fontSize: 13, fontWeight: '600' }}>
                {key.replace(/_/g, ' ')}
              </Text>
              <Text style={{ color: c.textDim, fontSize: 12, lineHeight: 17 }}>{reason}</Text>
            </View>
          ))}
        </Section>
      )}

      <Pressable
        onPress={() => router.push('/factions')}
        style={({ pressed }) => [
          {
            backgroundColor: c.card,
            borderRadius: 12,
            padding: space.md,
            marginTop: space.lg,
            flexDirection: 'row',
            alignItems: 'center',
          },
          pressed && { opacity: 0.7 },
        ]}
      >
        <Text style={{ color: c.text, fontSize: 15, fontWeight: '600', flex: 1 }}>Factions</Text>
        <Text style={{ color: c.textDim, fontSize: 13 }}>weekly board ›</Text>
      </Pressable>
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
