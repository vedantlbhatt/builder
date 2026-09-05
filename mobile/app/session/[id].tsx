import { useLocalSearchParams } from 'expo-router';
import React, { useEffect, useRef, useState } from 'react';
import {
  ActivityIndicator,
  Pressable,
  ScrollView,
  Text,
  useWindowDimensions,
  View,
} from 'react-native';

import { AnalysisView } from '../../src/analysis/AnalysisView';
import { describeEnd } from '../../src/analysis/format';
import { RecapCard, toCardModel, type CardModel } from '../../src/card/RecapCard';
import { shareCard } from '../../src/card/export';
import type { SessionDetail } from '../../src/data/api';
import * as cache from '../../src/data/cache';
import { api, SAMPLE_SESSION } from '../../src/data/client';
import { TimelineStrip } from '../../src/strip/TimelineStrip';
import { classShare, decodeColumns, decodeMarks } from '../../src/strip/decode';
import { StripClass } from '../../src/generated/strip';
import { colors, compactNumber, duration, space } from '../../src/theme';

const c = colors('dark');

export default function SessionScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const { width } = useWindowDimensions();
  const [model, setModel] = useState<CardModel | null>(null);
  const [session, setSession] = useState<SessionDetail | null>(null);
  const [sharing, setSharing] = useState(false);
  const cardRef = useRef(null);

  useEffect(() => {
    const show = (s: SessionDetail, code: string) => {
      setSession(s);
      setModel(toCardModel(s, code));
    };
    (async () => {
      if (id === 'sample') {
        show(SAMPLE_SESSION, 'builder.dev/s/sample');
        return;
      }
      const cached = await cache.getDetail(id!);
      if (cached) show(cached, `builder.dev/s/${id!.slice(0, 6)}`);
      try {
        const fresh = await api.session(id!);
        await cache.putDetail(fresh);
        show(fresh, `builder.dev/s/${id!.slice(0, 6)}`);
      } catch {
        // Offline with a cached copy is fine; offline without one shows the spinner.
      }
    })();
  }, [id]);

  if (!model || !session) {
    return (
      <View style={{ flex: 1, justifyContent: 'center', backgroundColor: c.bg }}>
        <ActivityIndicator color={c.accent} />
      </View>
    );
  }

  const contentWidth = width - space.md * 2;
  const share = model.strip ? classShare(decodeColumns(model.strip)) : null;

  // Boundary fields are optional on read: an older server omits them, and the row is
  // skipped rather than shown as "0s / 0s".
  const state = session.state ?? 'final';
  const hasSplit = session.attended_seconds !== undefined || session.autonomous_seconds !== undefined;
  const endNote = describeEnd(session);

  return (
    <ScrollView
      style={{ flex: 1, backgroundColor: c.bg }}
      contentContainerStyle={{ padding: space.md, paddingBottom: space.xxl }}
    >
      {/* The card, rendered at the width it will be captured at. Live preview rather than
          a separate "export" path: what you see is literally the view that gets captured. */}
      <View ref={cardRef} collapsable={false} style={{ borderRadius: 14, overflow: 'hidden' }}>
        <RecapCard model={model} width={contentWidth} />
      </View>

      <Pressable
        onPress={async () => {
          setSharing(true);
          try {
            await shareCard(cardRef, model);
          } finally {
            setSharing(false);
          }
        }}
        style={({ pressed }) => [
          {
            backgroundColor: c.accent,
            borderRadius: 12,
            paddingVertical: space.md,
            alignItems: 'center',
            marginTop: space.md,
          },
          pressed && { opacity: 0.8 },
        ]}
      >
        <Text style={{ color: '#1C1917', fontWeight: '700', fontSize: 16 }}>
          {sharing ? 'Preparing…' : 'Share this session'}
        </Text>
      </Pressable>

      <Section title="Timeline">
        {model.strip ? (
          <>
            <TimelineStrip
              cols={model.strip}
              marks={decodeMarks(model.marks)}
              spanMs={Math.max(1, model.wallSeconds * 1000)}
              preset="hero"
              width={contentWidth - space.md * 2}
            />
            {/* The legend lives HERE, not on the shared card. A route map does not explain
                its own encoding; the moment an artifact does, it is a chart. */}
            <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: space.md, marginTop: space.sm }}>
              <LegendItem klass={StripClass.prompting} label="you prompting" share={share} />
              <LegendItem klass={StripClass.agent} label="agent working" share={share} />
              <LegendItem klass={StripClass.human_edit} label="your edits" share={share} />
              <LegendItem klass={StripClass.idle} label="idle" share={share} />
            </View>
          </>
        ) : (
          <Text style={{ color: c.textDim, fontSize: 13 }}>
            This session predates the detail your editor keeps. Its hours still count.
          </Text>
        )}
      </Section>

      <Section title="Numbers">
        {state === 'live' && <Row label="Status" value="Live" />}
        <Row label="Active" value={duration(model.activeSeconds)} />
        {hasSplit && (
          <Row
            label="Attended / autonomous"
            value={`${duration(session.attended_seconds ?? 0)} / ${duration(session.autonomous_seconds ?? 0)}`}
          />
        )}
        <Row label="Elapsed" value={duration(model.wallSeconds)} />
        <Row label="Prompts you typed" value={`${model.prompts}`} />
        <Row label="Files touched" value={`${model.filesTouched}`} />
        <Row label="Lines from the agent" value={model.agentLines.toLocaleString()} />
        {model.commits > 0 && <Row label="Commits" value={`${model.commits}`} />}
        {model.tokensReported ? (
          <Row label="Tokens" value={compactNumber(model.totalTokens)} />
        ) : (
          // Absent, not zero. Cursor accounts usage server-side and writes {0,0} locally,
          // so a "0" here would be a claim about the session rather than about Cursor.
          <Row label="Tokens" value="not recorded by this editor" dim />
        )}
        {endNote && (
          <Text style={{ color: c.textDim, fontSize: 12, lineHeight: 17, marginTop: space.sm }}>
            {endNote}
          </Text>
        )}
      </Section>

      {session.analysis ? (
        <AnalysisView analysis={session.analysis} />
      ) : (
        <Section title="Analysis">
          {/* Quiet, and no spinner: nothing is loading. A final session without an analysis
              will not grow one by waiting. */}
          <Text style={{ color: c.textDim, fontSize: 13 }}>
            {state === 'final'
              ? 'Analysis not available for this session'
              : 'Analysis arrives when the session finishes, or at the next checkpoint while it runs unattended.'}
          </Text>
        </Section>
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

function Row({ label, value, dim }: { label: string; value: string; dim?: boolean }) {
  return (
    <View style={{ flexDirection: 'row', justifyContent: 'space-between', paddingVertical: 6 }}>
      <Text style={{ color: c.textDim, fontSize: 14 }}>{label}</Text>
      <Text style={{ color: dim ? c.textDim : c.text, fontSize: 14, fontWeight: dim ? '400' : '600' }}>
        {value}
      </Text>
    </View>
  );
}

function LegendItem({
  klass,
  label,
  share,
}: {
  klass: StripClass;
  label: string;
  share: Record<StripClass, number> | null;
}) {
  const pct = share ? Math.round(share[klass] * 100) : null;
  return (
    <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
      <View
        style={{ width: 18, height: 10, borderRadius: 2, backgroundColor: c.strip[klass] }}
      />
      <Text style={{ color: c.textDim, fontSize: 12 }}>
        {label}
        {pct !== null ? ` ${pct}%` : ''}
      </Text>
    </View>
  );
}
