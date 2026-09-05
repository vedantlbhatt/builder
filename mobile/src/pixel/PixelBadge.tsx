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
 * the subject of the screen instead of a companion to it.
 */
export function PixelBadge({
  state,
  text,
  scheme = 'dark',
  size = 48,
  fps,
  paused = false,
  style,
}: {
  state: SpriteState;
  text: string;
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
      accessibilityLabel={text}
    >
      <PixelSprite state={state} size={size} scheme={scheme} fps={fps} paused={paused} />
      <Text style={{ color: c.textDim, fontSize: 15, flex: 1 }} numberOfLines={2}>
        {text}
      </Text>
    </View>
  );
}
