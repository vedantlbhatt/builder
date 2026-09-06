import React, { useEffect, useState } from 'react';
import { Text, View } from 'react-native';

import type { SessionAnalysis } from '../generated/analysis';
import { PixelSprite } from '../pixel/PixelSprite';
import { colors, space } from '../theme';
import { analysisFooter, celebrationFor, labelize, pct, SENSITIVE_WARNING } from './format';

/**
 * The model-written reading of a session, below the card and the numbers.
 *
 * SHORT ON PURPOSE (spec/analysis.v1.json, docs/analysis.md). The reading is a headline,
 * at most two sentences and up to three highlights; the long blocks this screen used to
 * render (features, work mix, pivots, friction) were removed from the schema because
 * nobody read them. The corpus numbers people actually want — planning ratio, steer rate,
 * velocity, archetype — are COMPUTED and live on the profile, not here.
 *
 * Every section is still skipped when the model left it empty: the spec's honesty rule
 * says a field the model could not ground is null/empty, never guessed, and an empty card
 * would turn that silence into a claim. Nothing here is computed; this view only lays out
 * what the analysis already says.
 */

const c = colors('dark');

/** How long Bit cheers beside a shipped headline before settling into a still idle pose. */
export const CELEBRATION_MS = 3000;

export function AnalysisView({ analysis: a }: { analysis: SessionAnalysis }) {
  const highlights = a.highlights ?? [];
  const dimensions = a.dimensions ?? [];
  const moves = a.decision_patterns ?? [];
  const growth = a.growth_edge ?? [];
  const tags = a.tags ?? [];
  const style = a.build_style;
  const prompting = a.prompting;
  // At most one Bit per card. A shipped session gets the cheer beside its headline; any
  // other outcome gets the quiet idle pose beside the archetype chip. Two mascots in one
  // section would make him the subject of the analysis rather than a companion to it.
  const celebration = celebrationFor(a);

  return (
    <>
      <Section title="Analysis">
        {a.headline ? (
          <View style={{ flexDirection: 'row', alignItems: 'flex-start', gap: space.md }}>
            <Text style={{ color: c.text, fontSize: 22, fontWeight: '700', lineHeight: 28, flex: 1 }}>
              {a.headline}
            </Text>
            {celebration ? <CelebrationSprite /> : null}
          </View>
        ) : null}
        {a.summary ? (
          <Text style={{ color: c.text, fontSize: 14, lineHeight: 20, marginTop: space.sm }}>
            {a.summary}
          </Text>
        ) : null}
        {highlights.length > 0 && (
          <View style={{ marginTop: space.md, gap: 4 }}>
            {highlights.map((h, i) => (
              <View key={i} style={{ flexDirection: 'row', gap: space.sm }}>
                <Text style={{ color: c.accent, fontSize: 14 }}>•</Text>
                <Text style={{ color: c.text, fontSize: 14, lineHeight: 20, flex: 1 }}>{h}</Text>
              </View>
            ))}
          </View>
        )}
        {(a.outcome || a.archetype) && (
          <View style={{ flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', gap: space.sm, marginTop: space.md }}>
            {a.outcome ? <Chip label={labelize(a.outcome)} tone="accent" /> : null}
            {a.archetype ? <Chip label={labelize(a.archetype)} /> : null}
            {a.archetype && !celebration ? <PixelSprite state="idle" size={32} fps={2} /> : null}
          </View>
        )}
      </Section>

      {style && (
        <Section title="How you built it">
          <Row label="Planning" value={labelize(style.planning)} />
          <Row label="Iteration" value={labelize(style.iteration)} />
          <Row label="Steering" value={labelize(style.steering)} />
          <Row label="Verification" value={labelize(style.verification)} />
          <Row label="Scope" value={labelize(style.scope_control)} />
          {style.architecture_note ? (
            <Text style={[dim, { marginTop: space.sm }]}>{style.architecture_note}</Text>
          ) : null}
        </Section>
      )}

      {dimensions.length > 0 && (
        <Section title="Dimensions">
          {dimensions.map((d) => (
            <View key={d.dimension} style={{ paddingVertical: 6 }}>
              <View style={{ flexDirection: 'row', justifyContent: 'space-between', marginBottom: 4 }}>
                <Text style={{ color: c.text, fontSize: 14 }}>{labelize(d.dimension)}</Text>
                <Text style={{ color: c.text, fontSize: 14, fontWeight: '600', fontVariant: ['tabular-nums'] }}>
                  {Math.round(d.score)}
                </Text>
              </View>
              <Bar value={d.score} />
              {d.rationale ? <Text style={[dim, { marginTop: 4 }]}>{d.rationale}</Text> : null}
            </View>
          ))}
        </Section>
      )}

      {moves.length > 0 && (
        <Section title="Your moves">
          {moves.map((m, i) => (
            <View key={`${m.pattern}-${i}`} style={{ paddingVertical: 6 }}>
              <Text style={{ color: c.text, fontSize: 14, fontWeight: '700' }}>{m.pattern}</Text>
              {m.prompt_excerpt ? (
                <View
                  style={{
                    borderLeftWidth: 2,
                    borderLeftColor: c.accent,
                    paddingLeft: space.sm,
                    marginTop: 4,
                  }}
                >
                  <Text style={{ color: c.text, fontSize: 13, fontStyle: 'italic', lineHeight: 18 }}>
                    “{m.prompt_excerpt}”
                  </Text>
                </View>
              ) : null}
              {m.effect ? <Text style={[dim, { marginTop: 4 }]}>{m.effect}</Text> : null}
            </View>
          ))}
        </Section>
      )}

      {prompting && (
        <Section title="Prompting">
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: space.sm }}>
            <Chip label={labelize(prompting.tone)} />
          </View>
          <View style={{ marginTop: space.md }}>
            <View style={{ flexDirection: 'row', justifyContent: 'space-between', marginBottom: 4 }}>
              <Text style={{ color: c.textDim, fontSize: 14 }}>Specificity</Text>
              <Text style={{ color: c.text, fontSize: 14, fontWeight: '600', fontVariant: ['tabular-nums'] }}>
                {Math.round(prompting.specificity)}
              </Text>
            </View>
            <Bar value={prompting.specificity} />
          </View>
          <View style={{ flexDirection: 'row', gap: space.lg, marginTop: space.md }}>
            <Stat label="Corrections" value={pct(prompting.correction_share)} />
            <Stat label="Questions" value={pct(prompting.question_share)} />
          </View>
          {prompting.note ? <Text style={[dim, { marginTop: space.sm }]}>{prompting.note}</Text> : null}
        </Section>
      )}

      {growth.length > 0 && (
        <Section title="Growth edge">
          {growth.map((g, i) => (
            <View key={i} style={{ flexDirection: 'row', paddingVertical: 4, gap: space.sm }}>
              <Text style={{ color: c.accent, fontSize: 14 }}>•</Text>
              <Text style={{ color: c.text, fontSize: 14, lineHeight: 20, flex: 1 }}>{g}</Text>
            </View>
          ))}
        </Section>
      )}

      {tags.length > 0 && (
        <Section title="Tags">
          <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: space.sm }}>
            {tags.map((t) => (
              <Chip key={t} label={t} />
            ))}
          </View>
        </Section>
      )}

      <Text style={[dim, { marginTop: space.md, fontSize: 11 }]}>{analysisFooter(a)}</Text>
      {a.contains_sensitive ? (
        <Text style={{ color: c.accent, fontSize: 12, marginTop: space.xs }}>{SENSITIVE_WARNING}</Text>
      ) : null}
    </>
  );
}

const dim = { color: c.textDim, fontSize: 13, lineHeight: 18 } as const;

/**
 * One cheer, then stillness. Mounts celebrating, and after CELEBRATION_MS switches to a
 * paused idle frame — the headline is the thing to read, and a mascot that keeps
 * bouncing beside it for the whole scroll is a distraction, not a reward.
 */
function CelebrationSprite() {
  const [done, setDone] = useState(false);
  useEffect(() => {
    const t = setTimeout(() => setDone(true), CELEBRATION_MS);
    return () => clearTimeout(t);
  }, []);
  return <PixelSprite state={done ? 'idle' : 'celebrating'} size={48} fps={4} paused={done} />;
}

// Same styles as the Numbers section on the session screen, so the analysis reads as a
// continuation of it rather than a second design.
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
      <View style={{ backgroundColor: c.card, borderRadius: 12, padding: space.md }}>{children}</View>
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

export function Chip({ label, tone, color }: { label: string; tone?: 'accent'; color?: string }) {
  const fg = color ?? (tone === 'accent' ? c.accent : c.text);
  return (
    <View
      style={{
        borderRadius: 999,
        borderWidth: 1,
        borderColor: tone === 'accent' ? c.accent : c.border,
        backgroundColor: c.bg,
        paddingHorizontal: 10,
        paddingVertical: 3,
      }}
    >
      <Text style={{ color: fg, fontSize: 12, fontWeight: '600' }}>{label}</Text>
    </View>
  );
}

/** 0-100 as a horizontal bar. Plain Views: no SVG is needed for one rectangle. */
export function Bar({ value }: { value: number }) {
  const w = Number.isFinite(value) ? Math.min(100, Math.max(0, value)) : 0;
  return (
    <View style={{ height: 6, borderRadius: 3, backgroundColor: c.border, overflow: 'hidden' }}>
      <View style={{ width: `${w}%`, height: '100%', backgroundColor: c.accent }} />
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
