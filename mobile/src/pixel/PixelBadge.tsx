import React from 'react';
import { Text, View, type StyleProp, type ViewStyle } from 'react-native';

import { colors, space, type Scheme } from '../theme';
import { PixelSprite } from './PixelSprite';
import type { SpriteState } from './sprites';

/**
 * Mascot plus one line of caption — the unit for empty states and loading rows.
 *
 *   <PixelBadge state="thinking" text="Reading your session…" />
 *
 * Deliberately small: a 48pt sprite at 4 fps beside dim text. Anything larger becomes
 * the subject of the screen instead of a companion to it. An optional `title` puts one
 * bold line above the caption for the banners that already had a heading; the sprite
 * stays the same size either way.
 */
export function PixelBadge({
  state,
  text,
  title,
  scheme = 'dark',
  size = 48,
  fps,
  paused = false,
  style,
}: {
  state: SpriteState;
  text: string;
  title?: string;
  scheme?: Scheme;
  size?: number;
  fps?: number;
  paused?: boolean;
  style?: StyleProp<ViewStyle>;
}) {
  const c = colors(scheme);
  return (
    <View
      style={[{ flexDirection: 'row', alignItems: 'center', gap: space.md, padding: space.md }, style]}
      accessibilityRole="text"
      accessibilityLabel={title ? `${title}. ${text}` : text}
    >
      <PixelSprite state={state} size={size} scheme={scheme} fps={fps} paused={paused} />
      <View style={{ flex: 1 }}>
        {title ? (
          <Text style={{ color: c.text, fontWeight: '600', fontSize: 15, marginBottom: 4 }}>
            {title}
          </Text>
        ) : null}
        <Text style={{ color: c.textDim, fontSize: title ? 13 : 15 }} numberOfLines={3}>
          {text}
        </Text>
      </View>
    </View>
  );
}
