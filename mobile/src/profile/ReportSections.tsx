import React from 'react';
import { Text, View } from 'react-native';

import type { BuilderReport } from '../generated/report';
import { colors, space } from '../theme';
import { ShareBars } from './ShareBars';
import {
  assistedShare,
  fanoutLine,
  fanoutWaste,
  greenLine,
  shortDuration,
  streakLine,
  trendValues,
  trendVerdict,
  trendWords,
} from './report';

const c = colors('dark');

/**
 * The measured half of the profile: four sections, each of which is silent when the
 * machine refused it.
 *
 * WHY THESE FOUR AND NOT A DASHBOARD. Every existing tool that reads these logs is an ops
 * dashboard — tokens, cost, calls per hour — and none of them tell a person anything they
 * can act on. Each section here answers a question somebody would actually ask about
 * themselves: am I getting better, what did fanning out buy me, how much of this did I
 * write, and how long do I stay broken.
 *
 * SILENCE IS A FEATURE. Nothing here renders a zero for a refusal. A block that is null
 * does not appear, and a rate that is null shows the reason the module gave — "5 test
 * runs, 5 needed" tells a person what would make the number appear, and an empty chart
 * tells them the product is broken.
 */
export function ReportSections({ report }: { report: BuilderReport }) {
  return (
    <>
      <Trends report={report} />
      <Languages report={report} />
      <Agents report={report} />
      <Commits report={report} />
      <Habits report={report} />
    </>
  );
}

function Trends({ report }: { report: BuilderReport }) {
  if (!report.trends.length) return null;
  return (
    <Section title={`You against you, ${windowWords(report.window_days)}`}>
      {/* The headline first: one sentence is what a person takes away, and the rows under
          it are the receipts. */}
      {report.trend_headline && (
        <Text style={{ color: c.text, fontSize: 15, lineHeight: 22, marginBottom: space.sm }}>
          {report.trend_headline}
        </Text>
      )}
      {report.trends.map((t) => {
        const verdict = trendVerdict(t);
        return (
          <View
            key={t.metric}
            style={{
              flexDirection: 'row',
              alignItems: 'baseline',
              paddingVertical: 6,
              gap: space.sm,
            }}
          >
            <Text style={{ color: c.text, fontSize: 13, flex: 1 }} numberOfLines={1}>
              {t.label}
            </Text>
            <Text
              style={{
                color: c.textDim,
                fontSize: 12,
                fontVariant: ['tabular-nums'],
              }}
            >
              {trendValues(t)}
            </Text>
            <Text
              style={{
                // Dim when the metric has no direction. Painting every move green or red
                // would attach a verdict the measurement does not carry: more hours is not
                // better and a night owl is not broken.
                //
                // AND NOTHING IS RED. `danger` is for destructive actions. A person who
                // tested less this month has not broken anything, and a red row for it
                // reads as a scold from an app that measured them without being asked to
                // judge them. A move the wrong way is full-strength text; a move the right
                // way is the accent. That is the same line `trends.headline` draws, where
                // "which is the way you want it" is appended and nothing is appended for
                // the other direction.
                color: verdict === 'good' ? c.accent : verdict === 'bad' ? c.text : c.textDim,
                fontSize: 12,
                fontWeight: '700',
                minWidth: 64,
                textAlign: 'right',
              }}
            >
              {trendWords(t)}
            </Text>
          </View>
        );
      })}
      <Text style={{ color: c.textDim, fontSize: 11, marginTop: space.sm }}>
        Two windows of the same length, back to back. A move under 15% is called steady,
        because everything here wobbles by a tenth without anything changing about you.
      </Text>
    </Section>
  );
}

function Languages({ report }: { report: BuilderReport }) {
  const l = report.languages;
  if (!l) return null;
  return (
    <Section title="What you build in">
      {l.languages ? (
        <>
          <ShareBars
            tint={c.accent}
            items={l.languages.map((x) => ({
              label: x.name,
              share: x.share,
              detail: `${x.lines.toLocaleString()} lines`,
            }))}
          />
          <Text style={{ color: c.textDim, fontSize: 11, marginTop: space.sm }}>
            Lines the agent added, not files and not time: a file count would weigh a
            one-line config change against a 400-line module.
            {l.generated_lines_excluded > 0
              ? ` ${l.generated_lines_excluded.toLocaleString()} lines nobody wrote — lockfiles and generated code — are left out.`
              : ''}
          </Text>
        </>
      ) : (
        <Reason text={l.reason ?? 'Not enough written yet to split.'} />
      )}
    </Section>
  );
}

function Agents({ report }: { report: BuilderReport }) {
  const a = report.agents;
  if (!a) return null;
  const waste = fanoutWaste(a);
  return (
    <Section title="Agents you ran">
      <Text style={{ color: c.text, fontSize: 15, lineHeight: 22 }}>{fanoutLine(a)}</Text>
      <View style={{ flexDirection: 'row', marginTop: space.md }}>
        <Stat label="At once, on average" value={`${a.parallelism.toFixed(1)}x`} flex />
        <Stat label="Peak" value={String(a.max_concurrent)} flex />
        <Stat label="Agent time" value={shortDuration(a.agent_seconds)} flex />
      </View>
      {a.by_type.length > 1 && (
        <View style={{ marginTop: space.md }}>
          <ShareBars
            tint={c.accent}
            items={a.by_type.map((t) => ({
              label: t.name,
              share: t.agents / a.agents,
              detail: `${t.agents}`,
            }))}
          />
        </View>
      )}
      {waste && (
        <Text style={{ color: c.textDim, fontSize: 12, marginTop: space.sm }}>{waste}</Text>
      )}
      <Text style={{ color: c.textDim, fontSize: 11, marginTop: space.sm }}>
        Read from the subagent transcripts, which no other tool on your machine looks at.
        Their tokens, lines and commits are already inside the totals above and are never
        counted twice here.
      </Text>
    </Section>
  );
}

function Commits({ report }: { report: BuilderReport }) {
  const co = report.contributions;
  if (!co) return null;
  const share = assistedShare(co);
  const streak = streakLine(co);
  return (
    <Section title="What you shipped">
      {/* Three equal columns, not a wrapping row. At `gap: xl` the third stat wrapped to
          its own line with a full row of empty space above it, which reads as a bug rather
          than as a wrap. */}
      <View style={{ flexDirection: 'row' }}>
        <Stat label="With an agent" value={co.assisted.toLocaleString()} flex />
        <Stat label="On your own" value={co.alone.toLocaleString()} flex />
        <Stat label="Days you shipped" value={String(co.active_days)} flex />
      </View>
      {share !== null && (
        <View style={{ marginTop: space.md }}>
          <ShareBars
            tint={c.accent}
            items={[
              { label: 'agent assisted', share },
              { label: 'you alone', share: 1 - share },
            ]}
          />
        </View>
      )}
      {streak && (
        <Text style={{ color: c.text, fontSize: 13, marginTop: space.sm }}>{streak}</Text>
      )}
      <Text style={{ color: c.textDim, fontSize: 11, marginTop: space.sm }}>
        A commit counts as assisted only while a session was actually running. Anything
        outside every session is yours, including work this machine never saw, so the
        split leans toward you rather than toward the agent.
      </Text>
    </Section>
  );
}

function Habits({ report }: { report: BuilderReport }) {
  const q = report.quality;
  const p = report.prompting;
  if (!q && !p) return null;
  const green = q ? greenLine(q) : null;
  return (
    <Section title="How you work">
      {q && q.first_try_rate !== null && q.first_try_rate !== undefined && (
        <Row
          label="Test runs that were already green"
          value={`${Math.round(q.first_try_rate * 100)}% of ${q.runs}`}
        />
      )}
      {green && <Row label="Back to green" value={green} />}
      {p && p.clean_share !== null && p.clean_share !== undefined && (
        <Row
          label="Prompts that landed clean"
          value={`${Math.round(p.clean_share * 100)}% of ${p.attempts}`}
        />
      )}
      {/* The refusals, verbatim. "5 test runs, 5 needed" tells somebody what would make
          the number appear; a blank row tells them the app is broken. */}
      {q && q.reason && <Reason text={`Time to green: ${q.reason}.`} />}
      {p && p.reason && <Reason text={`Clean prompts: ${p.reason}.`} />}
      <Text style={{ color: c.textDim, fontSize: 11, marginTop: space.sm }}>
        A prompt landed clean if it produced something and you did not have to take the
        wheel back. Not one word of any prompt leaves your machine.
      </Text>
    </Section>
  );
}

/** "this month", "this week", or the number of days. The trend rows are always two equal
    windows, so the header has to follow the window it was actually given. */
function windowWords(days: number): string {
  if (days === 7) return 'week on week';
  if (days >= 28 && days <= 31) return 'month on month';
  return `${days} days on ${days}`;
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <View
      style={{
        backgroundColor: c.card,
        borderRadius: 16,
        padding: space.lg,
        marginTop: space.md,
        borderWidth: 1,
        borderColor: c.border,
      }}
    >
      <Text
        style={{
          color: c.textDim,
          fontSize: 11,
          fontWeight: '700',
          letterSpacing: 1,
          marginBottom: space.sm,
          textTransform: 'uppercase',
        }}
      >
        {title}
      </Text>
      {children}
    </View>
  );
}

function Stat({ label, value, flex }: { label: string; value: string; flex?: boolean }) {
  return (
    <View style={flex ? { flex: 1, paddingRight: space.sm } : undefined}>
      <Text style={{ color: c.text, fontSize: 20, fontWeight: '700' }}>{value}</Text>
      <Text style={{ color: c.textDim, fontSize: 12 }}>{label}</Text>
    </View>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <View
      style={{
        flexDirection: 'row',
        justifyContent: 'space-between',
        alignItems: 'baseline',
        paddingVertical: 6,
        gap: space.md,
      }}
    >
      <Text style={{ color: c.textDim, fontSize: 13, flex: 1 }}>{label}</Text>
      <Text style={{ color: c.text, fontSize: 13, fontWeight: '600' }}>{value}</Text>
    </View>
  );
}

function Reason({ text }: { text: string }) {
  return (
    <Text style={{ color: c.textDim, fontSize: 12, paddingVertical: 4 }}>{text}</Text>
  );
}
