import React from 'react';
import { Text, View } from 'react-native';

import { colors, space } from '../theme';
import type { CorpusFact } from '../data/api';

const c = colors('dark');

/**
 * The ranked one-line facts, most unusual first.
 *
 * The server writes the whole sentence, including its number, because the number and the
 * wording have to agree ("4 in 10 prompts" is not "0.429") and splitting that decision
 * across two languages is how they stop agreeing. The screen's only job is to make the
 * ranking visible: the first fact is the loudest thing true about this person, so it gets
 * the accent, and the rest fade by rank rather than all shouting at once.
 */
export function FactList({ facts, limit = 6 }: { facts: CorpusFact[]; limit?: number }) {
  const shown = facts.slice(0, limit);
  if (shown.length === 0) {
    return (
      <Text style={{ color: c.textDim, fontSize: 13 }}>
        Nothing stands out yet. A few more sessions and this fills in.
      </Text>
    );
  }
  return (
    <View>
      {shown.map((f, i) => (
        <View
          key={f.id}
          style={{
            flexDirection: 'row',
            alignItems: 'flex-start',
            gap: space.sm,
            paddingVertical: 7,
            borderTopWidth: i === 0 ? 0 : 1,
            borderTopColor: c.border,
          }}
        >
          <View
            style={{
              width: 6,
              height: 6,
              borderRadius: 3,
              marginTop: 7,
              backgroundColor: i === 0 ? c.accent : c.textDim,
              opacity: i === 0 ? 1 : 0.5,
            }}
          />
          <Text
            style={{
              flex: 1,
              color: i === 0 ? c.text : c.textDim,
              fontSize: i === 0 ? 15 : 14,
              fontWeight: i === 0 ? '600' : '400',
              lineHeight: 21,
            }}
          >
            {f.text}
          </Text>
        </View>
      ))}
    </View>
  );
}
