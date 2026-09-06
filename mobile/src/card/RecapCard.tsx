import React from 'react';
import { StyleSheet, Text, View } from 'react-native';

import type { SessionDetail } from '../data/api';
import { TimelineStrip } from '../strip/TimelineStrip';
import { decodeMarks } from '../strip/decode';
import { colors, compactNumber, duration, type Scheme } from '../theme';

/**
 * The share card, on the phone.
 *
 * Mirrors `RecapCardView.swift`: same superlative ladder, same layout order, same
 * omissions. The two exist separately because the Mac renders SwiftUI and the phone
 * renders React Native, and that duplication is the real cost of the split — mitigated by
 * the shared spec, the shared fixtures, and the cross-language tests, but any layout
 * change is still two edits.
 *
 * What is deliberately absent, in both:
 *  - COST. Structurally impossible for Cursor, wrong by construction on a subscription,
 *    and a fourth thing competing for the half-second a stranger gives a screenshot.
 *  - A LEGEND. A route map does not explain its own encoding; the moment an artifact
 *    does, it is a chart rather than an identity.
 */

export interface CardModel {
  repoName: string | null;
  startedAt: number;
  activeSeconds: number;
  wallSeconds: number;
  title: string | null;
  choreTitle: boolean;
  prompts: number;
  filesTouched: number;
  agentLines: number;
  commits: number;
  tokensReported: boolean;
  totalTokens: number;
  modelName: string | null;
  agentLineBucket: string;
  attribConfidence: string;
  isPersonalRecord: boolean;
  strip: string;
  marks: number[][];
  shortCode: string;
  harness: string;
}

const CHORE_PATTERN =
  /^(Check|Run|Debug the|Disable|Enable|List|Add file|Say|Clarify|Analyze|Toggle)\b/;

const BUCKET_COPY: Record<string, string> = {
  almost_all_agent: 'Nearly every line came from',
  nine_in_ten: '9 of every 10 lines came from',
  three_in_four: '3 of every 4 lines came from',
  about_half: 'About half the lines came from',
  mostly_you: 'Most of these lines are yours',
};

/**
 * The most remarkable TRUE fact available, in a fixed order of interest.
 *
 * Neither obvious option works alone. A duration is evaluable but not remarkable, and the
 * harness's own title is usually a chore-log entry — reading all 82 on the reference
 * machine turned up "Check backend service running on port 5001" and "Say hi in three
 * words". Leading with either produces a card that reads like a screenshotted ticket.
 */
export function headline(m: CardModel): string {
  if (m.isPersonalRecord) return `${duration(m.activeSeconds)}, longest session yet`;

  if (
    m.attribConfidence !== 'none' &&
    m.agentLineBucket !== 'unknown' &&
    m.agentLines >= 200 &&
    m.modelName
  ) {
    const copy = BUCKET_COPY[m.agentLineBucket];
    if (copy) {
      // "at least" is not decoration: human edits are counted as events with no line
      // count, so this is a lower bound. The hedge is also the more impressive phrasing.
      return m.agentLineBucket === 'mostly_you'
        ? copy
        : `${copy} ${m.modelName}, at least`;
    }
  }

  if (m.commits >= 5) return `${m.commits} commits`;
  if (m.agentLines >= 1000) return `+${m.agentLines.toLocaleString()} lines`;
  if (m.activeSeconds >= 2700) return `${duration(m.activeSeconds)} in one sitting`;
  if (m.title && !m.choreTitle && m.title.length <= 60) return m.title;
  return duration(m.activeSeconds);
}

export function toCardModel(s: SessionDetail, shortCode: string): CardModel {
  const stats = (s.stats ?? {}) as Record<string, number | string | boolean | null>;
  const models = (stats.models as { model_id: string }[] | undefined) ?? [];
  const rawModel = models[0]?.model_id ?? null;

  const started = new Date(s.started_at).getTime() / 1000;
  const ended = new Date(s.ended_at).getTime() / 1000;

  return {
    repoName: s.repo_name,
    startedAt: started,
    activeSeconds: s.active_seconds,
    wallSeconds: Math.max(ended - started, s.active_seconds),
    title: s.title,
    choreTitle: s.title ? CHORE_PATTERN.test(s.title) : false,
    prompts: Number(stats.human_prompt_count ?? 0),
    filesTouched: Number(stats.files_touched ?? 0),
    agentLines: Number(stats.lines_added_agent ?? 0),
    commits: Number(stats.commit_count ?? 0),
    tokensReported: Boolean(stats.tokens_reported),
    totalTokens:
      Number(stats.tok_in ?? 0) +
      Number(stats.tok_out ?? 0) +
      Number(stats.tok_cache_read ?? 0) +
      Number(stats.tok_cache_w5m ?? 0) +
      Number(stats.tok_cache_w1h ?? 0),
    modelName: shortModelName(rawModel),
    agentLineBucket: String(stats.agent_line_bucket ?? 'unknown'),
    attribConfidence: String(stats.attrib_confidence ?? 'none'),
    isPersonalRecord: false,
    strip: s.strip?.cols ?? '',
    marks: s.strip?.marks ?? [],
    shortCode,
    harness: s.harness,
  };
}

/** "claude-opus-5[1m]" -> "Opus 5". The suffix is preserved on the wire, not on a card. */
function shortModelName(raw: string | null): string | null {
  if (!raw) return null;
  let s = raw.split('[')[0] ?? raw;
  s = s.replace('claude-', '');
  return s
    .split('-')
    .map((p) => p.charAt(0).toUpperCase() + p.slice(1))
    .join(' ');
}

const HARNESS_LABEL: Record<string, string> = {
  claude_code: 'Claude Code',
  cursor_ide: 'Cursor',
  cursor_agent: 'cursor-agent',
  codex: 'Codex',
  gemini_cli: 'Gemini CLI',
  cline: 'Cline',
};

interface Props {
  model: CardModel;
  width: number;
  scheme?: Scheme;
}

/** 16:9, matching the Mac's 1600x900. Captured at pixelRatio 2. */
export function RecapCard({ model, width, scheme = 'dark' }: Props) {
  const c = colors(scheme);
  const height = width * (900 / 1600);
  const s = width / 1600;
  const date = new Date(model.startedAt * 1000);

  const stats: [string, string][] = [[duration(model.activeSeconds), 'active']];
  if (model.commits > 0) stats.push([`${model.commits}`, 'commits']);
  if (model.agentLines > 0) stats.push([`+${model.agentLines.toLocaleString()}`, 'lines']);
  if (model.filesTouched > 0) stats.push([`${model.filesTouched}`, 'files']);
  stats.push([`${model.prompts}`, model.prompts === 1 ? 'prompt' : 'prompts']);
  // Tokens only when the harness reports them. Cursor never does, and a "0" there reads
  // as a bug in Builder rather than as a fact about Cursor.
  if (model.tokensReported) stats.push([compactNumber(model.totalTokens), 'tokens']);

  const stripWidth = width - 152 * s;

  return (
    <View style={[styles.card, { width, height, backgroundColor: c.card, padding: 76 * s }]}>
      <View style={styles.header}>
        <Text style={{ fontSize: 30 * s, fontWeight: '600', color: c.text }}>
          {model.repoName ?? 'private repo'}
        </Text>
        <Text style={{ fontSize: 26 * s, color: c.textDim, marginLeft: 16 * s }}>
          {date.toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'long' })}
        </Text>
        <View style={{ flex: 1 }} />
        <Badge text={HARNESS_LABEL[model.harness] ?? model.harness} scheme={scheme} scale={s} />
        {model.modelName ? <Badge text={model.modelName} scheme={scheme} scale={s} /> : null}
      </View>

      <View style={{ flex: 1, justifyContent: 'center' }}>
        <Text
          style={{
            fontSize: 78 * s,
            fontWeight: '700',
            color: c.text,
            letterSpacing: -1.5 * s,
          }}
          numberOfLines={2}
          adjustsFontSizeToFit
        >
          {headline(model)}
        </Text>
      </View>

      <View style={{ marginBottom: 40 * s }}>
        {model.strip ? (
          <TimelineStrip
            cols={model.strip}
            marks={decodeMarks(model.marks)}
            spanMs={Math.max(1, model.wallSeconds * 1000)}
            preset="hero"
            scheme={scheme}
            width={stripWidth}
          />
        ) : null}
        <View style={[styles.header, { marginTop: 14 * s }]}>
          <Text style={{ fontSize: 24 * s, color: c.textDim, fontVariant: ['tabular-nums'] }}>
            {date.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })}
          </Text>
          <View style={{ flex: 1 }} />
          <Text style={{ fontSize: 24 * s, color: c.textDim }}>
            {duration(model.activeSeconds)} active · {duration(model.wallSeconds)} elapsed
          </Text>
          <View style={{ flex: 1 }} />
          <Text style={{ fontSize: 24 * s, color: c.textDim, fontVariant: ['tabular-nums'] }}>
            {new Date((model.startedAt + model.wallSeconds) * 1000).toLocaleTimeString(undefined, {
              hour: '2-digit',
              minute: '2-digit',
            })}
          </Text>
        </View>
      </View>

      <View style={styles.header}>
        {stats.map(([value, label]) => (
          <View key={label} style={{ flex: 1 }}>
            <Text style={{ fontSize: 40 * s, fontWeight: '600', color: c.text }}>{value}</Text>
            <Text style={{ fontSize: 22 * s, color: c.textDim }}>{label}</Text>
          </View>
        ))}
      </View>

      <View style={[styles.header, { marginTop: 'auto', alignItems: 'center' }]}>
        <View
          style={{
            width: 16 * s,
            height: 16 * s,
            borderRadius: 3 * s,
            backgroundColor: c.accent,
            marginRight: 10 * s,
          }}
        />
        <Text style={{ fontSize: 26 * s, fontWeight: '600', color: c.text }}>builder</Text>
        <View style={{ flex: 1 }} />
        <Text style={{ fontSize: 22 * s, color: c.textDim }}>{model.shortCode}</Text>
      </View>
    </View>
  );
}

function Badge({ text, scheme, scale }: { text: string; scheme: Scheme; scale: number }) {
  const c = colors(scheme);
  return (
    <View
      style={{
        borderWidth: 1.5,
        borderColor: c.border,
        borderRadius: 999,
        paddingHorizontal: 16 * scale,
        paddingVertical: 8 * scale,
        marginLeft: 12 * scale,
      }}
    >
      <Text style={{ fontSize: 22 * scale, color: c.textDim }}>{text}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  card: { overflow: 'hidden' },
  header: { flexDirection: 'row', alignItems: 'baseline' },
});
