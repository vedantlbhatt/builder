import React from 'react';
import { Text, View } from 'react-native';

import type { BuilderNarrative, NarrativeClaim } from '../generated/narrative';
import { colors, space } from '../theme';
import { narrativeView } from './narrative';

const c = colors('dark');

/**
 * The "how you work" page: the only prose on this screen, and the only part of the
 * profile a model wrote.
 *
 * Nothing here computes anything, and nothing here paraphrases. Every string is printed
 * exactly as it was stored, because it was already checked against the measurements it
 * came from on the machine that wrote it: a claim carrying a number the input did not
 * contain was deleted before it was ever uploaded (analysis/narrative.py). Rewording it
 * on the phone would put a sentence in front of a person that nothing had verified.
 *
 * Every field is optional in practice. `verify` empties `archetype_line` and can empty
 * `how_you_work` outright when it takes a claim back, so an empty string is a normal
 * state and prints as nothing rather than as a blank heading.
 */
export function NarrativeSection({ narrative }: { narrative: BuilderNarrative }) {
  const view = narrativeView(narrative);
  if (!view) return null;
  const { paragraphs, strengths, watchOuts, experiment, provenance } = view;

  return (
    <View style={{ marginTop: space.lg }}>
      <Text
        style={{
          color: c.textDim,
          fontSize: 11,
          fontWeight: '700',
          letterSpacing: 1,
          marginBottom: space.sm,
        }}
      >
        HOW YOU WORK
      </Text>

      {paragraphs.map((p, i) => (
        <Text
          key={`p${i}`}
          style={{
            color: c.text,
            fontSize: 15,
            lineHeight: 23,
            marginBottom: i === paragraphs.length - 1 ? 0 : space.md,
          }}
        >
          {p}
        </Text>
      ))}

      {strengths.length > 0 && <ClaimList title="What you are good at" items={strengths} tint={c.accent} />}
      {watchOuts.length > 0 && (
        <ClaimList title="What it is costing you" items={watchOuts} tint={c.textDim} />
      )}

      {experiment.length > 0 && (
        <View
          style={{
            marginTop: space.lg,
            padding: space.md,
            borderRadius: 12,
            borderWidth: 1,
            borderColor: c.border,
            backgroundColor: c.card,
          }}
        >
          <Text
            style={{ color: c.accent, fontSize: 11, fontWeight: '700', letterSpacing: 1 }}
          >
            TRY THIS NEXT SESSION
          </Text>
          <Text style={{ color: c.text, fontSize: 15, lineHeight: 22, marginTop: space.xs }}>
            {experiment}
          </Text>
        </View>
      )}

      <Text style={{ color: c.textDim, fontSize: 11, marginTop: space.md }}>
        {/* Said out loud, because the alternative is a person wondering. The bar on
            inventing a figure is enforced by a check that runs after the model, not by
            asking it nicely, and the count of what that check caught is stored. */}
        {provenance}
      </Text>
    </View>
  );
}

function ClaimList({
  title,
  items,
  tint,
}: {
  title: string;
  items: NarrativeClaim[];
  tint: string;
}) {
  return (
    <View style={{ marginTop: space.lg }}>
      <Text style={{ color: c.textDim, fontSize: 11, fontWeight: '700', letterSpacing: 1 }}>
        {title.toUpperCase()}
      </Text>
      {items.map((item, i) => (
        <View key={`c${i}`} style={{ flexDirection: 'row', gap: space.sm, marginTop: space.sm }}>
          <View
            style={{ width: 3, borderRadius: 2, backgroundColor: tint, alignSelf: 'stretch' }}
          />
          <View style={{ flex: 1 }}>
            <Text style={{ color: c.text, fontSize: 14, lineHeight: 20 }}>{item.text}</Text>
            {/* The evidence is never optional on screen. A claim about somebody's habits
                with the number hidden reads exactly like a horoscope. */}
            {item.evidence.trim().length > 0 && (
              <Text style={{ color: c.textDim, fontSize: 12, lineHeight: 17, marginTop: 2 }}>
                {item.evidence}
              </Text>
            )}
          </View>
        </View>
      ))}
    </View>
  );
}
