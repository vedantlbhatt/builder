import React from 'react';
import { Text, View } from 'react-native';

import { colors } from '../theme';

const c = colors('dark');

export interface Share {
  label: string;
  share: number;
  detail?: string;
}

/**
 * A short ranked list of shares, drawn as bars.
 *
 * Bars are scaled to the LARGEST share in the list, not to 1.0. These lists are truncated
 * to the top few, so they never sum to 1, and a bar that filled a fifth of the row when
 * the item is the biggest thing in the corpus would read as "barely used".
 */
export function ShareBars({ items, tint }: { items: Share[]; tint: string }) {
  const top = Math.max(...items.map((i) => i.share), 0.0001);
  return (
    <View>
      {items.map((it) => (
        <View key={it.label} style={{ paddingVertical: 5 }}>
          <View style={{ flexDirection: 'row', alignItems: 'baseline', marginBottom: 4 }}>
            <Text style={{ color: c.text, fontSize: 13, flex: 1 }} numberOfLines={1}>
              {it.label}
            </Text>
            <Text
              style={{ color: c.textDim, fontSize: 12, fontVariant: ['tabular-nums'] }}
            >
              {Math.round(it.share * 100)}%{it.detail ? ` · ${it.detail}` : ''}
            </Text>
          </View>
          <View style={{ height: 6, borderRadius: 3, backgroundColor: c.border }}>
            <View
              style={{
                width: `${Math.max(2, (it.share / top) * 100)}%`,
                height: 6,
                borderRadius: 3,
                backgroundColor: tint,
              }}
            />
          </View>
        </View>
      ))}
    </View>
  );
}
