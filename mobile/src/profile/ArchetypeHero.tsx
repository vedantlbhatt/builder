import React from 'react';
import { Text, View } from 'react-native';

import { animalForArchetype } from '../pixel/animals';
import { PixelAnimal } from '../pixel/PixelAnimal';
import { PixelSprite } from '../pixel/PixelSprite';
import { colors, space } from '../theme';
import type { CorpusArchetype } from '../data/api';
import { archetypeTitle, closestRule } from './format';

const c = colors('dark');

/**
 * The top of the profile: a creature, a title, and the one measured rule that earned it.
 *
 * The rule is shown, always. An archetype with nothing under it is a horoscope, and this
 * one is a threshold on a single named metric, so saying which one costs a line and is
 * the difference between a claim and a result.
 *
 * When no rule met its threshold there is no archetype and none is invented. The card
 * says what is missing instead, because "we do not know yet" is a true thing to say and
 * a guessed archetype is not.
 */
export function ArchetypeHero({
  archetype,
  sessions,
  sentence,
}: {
  archetype: CorpusArchetype | null;
  sessions: number;
  /**
   * The narrative's one line about what this label means for THIS person, when a
   * narrative exists and the number check did not take it back. Shown ABOVE the rule,
   * because the rule is the receipt and this is the claim. Absent is the normal state.
   */
  sentence?: string | null;
}) {
  const name = archetype?.name ?? null;
  const runners = (archetype?.runners_up ?? []).filter((r) => r.score !== null);
  const closest = archetype && !name ? closestRule(archetype.scores) : null;

  return (
    <View
      style={{
        backgroundColor: c.card,
        borderRadius: 16,
        padding: space.lg,
        borderWidth: 1,
        borderColor: name ? c.accent : c.border,
      }}
    >
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: space.md }}>
        {/* Bit, not an animal, when there is no type. Every animal in the pack IS an
            archetype, so drawing one here would say "quality guardian" in the picture
            while the words say "no type yet", and the picture is the part people read. */}
        {name ? <PixelAnimal animal={animalForArchetype(name)} size={64} /> : <PixelSprite state="thinking" size={64} />}
        <View style={{ flex: 1 }}>
          <Text style={{ color: c.textDim, fontSize: 11, fontWeight: '700', letterSpacing: 1 }}>
            YOUR BUILDER TYPE
          </Text>
          <Text
            style={{
              color: name ? c.accent : c.textDim,
              fontSize: 26,
              fontWeight: '800',
              marginTop: 2,
            }}
          >
            {name ? archetypeTitle(name) : closest ? 'No clear type' : 'Not enough yet'}
          </Text>
        </View>
      </View>

      {sentence && (
        <Text style={{ color: c.text, fontSize: 16, lineHeight: 24, marginTop: space.md }}>
          {sentence}
        </Text>
      )}

      <Text
        style={{
          color: sentence ? c.textDim : c.text,
          fontSize: sentence ? 12 : 14,
          lineHeight: sentence ? 18 : 20,
          marginTop: sentence ? space.sm : space.md,
        }}
      >
        {/* The rule, verbatim from the server, or the reason there is none. An archetype
            with nothing under it is a horoscope; this one is a threshold on a single
            named metric, so saying which one is the difference between a claim and a
            result. */}
        {archetype?.rule ??
          (closest
            ? `Nothing you do is extreme enough to name yet. Closest is ${archetypeTitle(
                closest.name
              )}: ${closest.rule}, ${closest.value} against ${closest.threshold}.`
            : archetype?.reason ?? 'Build a few more sessions and this fills in.')}
      </Text>

      {name && archetype?.confidence !== null && archetype?.confidence !== undefined && (
        <Text style={{ color: c.textDim, fontSize: 12, marginTop: space.sm }}>
          {Math.round(archetype.confidence * 100)}% confidence over {sessions}{' '}
          {sessions === 1 ? 'session' : 'sessions'}
          {runners.length > 0
            ? `, next closest ${runners.map((r) => archetypeTitle(r.name)).join(' and ')}`
            : ''}
        </Text>
      )}
    </View>
  );
}
