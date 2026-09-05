import React from 'react';
import { Image, Text, View, type StyleProp, type ViewStyle } from 'react-native';

import type { PostMedia } from '../data/api';
import { colors, space } from '../theme';

const c = colors('dark');

const GAP = 4;
const COLUMNS = 3;

/**
 * A post's photos. `grid`: square thumbnails, three across, for the feed row. `full`:
 * each photo at full width with its own aspect, for the post screen.
 *
 * `url` is null when the server has no `OBJECT_STORE_PUBLIC_BASE` to build one from; the
 * photo exists but cannot be fetched, so a neutral tile carries the count rather than an
 * `Image` that fails silently.
 */
export function PhotoGrid({
  photos,
  width,
  layout = 'grid',
  style,
}: {
  photos: readonly PostMedia[];
  /** The width the grid may use. */
  width: number;
  layout?: 'grid' | 'full';
  style?: StyleProp<ViewStyle>;
}) {
  if (photos.length === 0) return null;
  const count = photos.length;
  const countLabel = `${count} photo${count === 1 ? '' : 's'}`;

  if (layout === 'full') {
    return (
      <View style={[{ gap: space.sm }, style]}>
        {photos.map((p) => {
          const ratio = p.width && p.height ? p.width / p.height : 4 / 3;
          const h = Math.round(width / ratio);
          return p.url ? (
            <Image
              key={p.id}
              source={{ uri: p.url }}
              resizeMode="cover"
              accessibilityIgnoresInvertColors
              style={{ width, height: h, borderRadius: 10, backgroundColor: c.border }}
            />
          ) : (
            <Placeholder key={p.id} width={width} height={Math.min(h, width)} label={countLabel} />
          );
        })}
      </View>
    );
  }

  const tile = Math.floor((width - GAP * (COLUMNS - 1)) / COLUMNS);
  let labeled = false;
  return (
    <View style={[{ flexDirection: 'row', flexWrap: 'wrap', gap: GAP }, style]}>
      {photos.map((p) => {
        if (p.url) {
          return (
            <Image
              key={p.id}
              source={{ uri: p.url }}
              resizeMode="cover"
              accessibilityIgnoresInvertColors
              style={{ width: tile, height: tile, borderRadius: 8, backgroundColor: c.border }}
            />
          );
        }
        const label = labeled ? null : countLabel;
        labeled = true;
        return <Placeholder key={p.id} width={tile} height={tile} label={label} />;
      })}
    </View>
  );
}

function Placeholder({ width, height, label }: { width: number; height: number; label: string | null }) {
  return (
    <View
      style={{
        width,
        height,
        borderRadius: 8,
        backgroundColor: c.border,
        alignItems: 'center',
        justifyContent: 'center',
      }}
    >
      {label && <Text style={{ color: c.textDim, fontSize: 12, fontWeight: '600' }}>{label}</Text>}
    </View>
  );
}
