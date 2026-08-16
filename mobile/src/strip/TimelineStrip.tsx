import React, { useMemo } from 'react';
import { View, type ViewStyle } from 'react-native';
import Svg, { Rect } from 'react-native-svg';

import { DENSITY_ALPHAS, MARK_DEDUPE_MIN_PX, StripClass } from '../generated/strip';
import { colors, type Scheme } from '../theme';
import { classShare, decodeStrip, layoutMarks, resampleColumns, type Mark } from './decode';

export type Preset = 'sparkline' | 'row' | 'hero';

const TRACK_HEIGHT: Record<Preset, number> = {
  sparkline: 8,
  row: 12,
  hero: 28,
};

const CORNER: Record<Preset, number> = { sparkline: 2, row: 3, hero: 6 };

interface Props {
  /** base64, exactly 1024 bytes. */
  cols: string;
  marks?: Mark[];
  spanMs?: number;
  preset?: Preset;
  scheme?: Scheme;
  width: number;
  style?: ViewStyle;
}

/**
 * The session timeline strip, on the phone.
 *
 * Renders from the same 1024-byte array and the same ordinals as the SwiftUI version,
 * with the same nearest-neighbour resample. `__tests__/strip.test.ts` and
 * `StripGoldenTests.swift` assert both decoders agree on the same fixtures, because a
 * class-ordinal swap produces a strip that is plausible, non-empty and completely wrong.
 *
 * Marks are drawn ON TOP and are never resampled away. A typed prompt occupies seconds of
 * a session that may run for hours, so at any render width its column is dominated by the
 * agent run that follows it; without the overlay the human disappears from their own
 * timeline.
 */
export function TimelineStrip({
  cols,
  marks = [],
  spanMs = 0,
  preset = 'row',
  scheme = 'dark',
  width,
  style,
}: Props) {
  const height = TRACK_HEIGHT[preset];

  const { rects, markRects, label } = useMemo(() => {
    let bytes: Uint8Array;
    try {
      bytes = decodeStrip(cols);
    } catch {
      // A malformed strip must degrade to an empty track, never crash a feed row.
      return { rects: [], markRects: [], label: 'Session timeline unavailable' };
    }

    // One rect per pixel column would be ~400 SVG nodes per row. Runs of the same colour
    // are merged instead, which on a real session is an order of magnitude fewer: the
    // strip is mostly long stretches of agent work broken by idle.
    const columns = resampleColumns(bytes, Math.max(1, Math.round(width)));
    const colWidth = width / columns.length;

    const merged: { x: number; w: number; fill: string; opacity: number }[] = [];
    for (let i = 0; i < columns.length; i++) {
      const col = columns[i]!;
      const fill = colors(scheme).strip[col.klass];
      const opacity =
        col.klass === StripClass.idle
          ? 1
          : DENSITY_ALPHAS[
              preset === 'sparkline' ? (col.density >= 2 ? DENSITY_ALPHAS.length - 1 : 0) : col.density
            ] ?? 1;

      const last = merged[merged.length - 1];
      if (last && last.fill === fill && last.opacity === opacity) {
        last.w += colWidth;
      } else {
        merged.push({ x: i * colWidth, w: colWidth, fill, opacity });
      }
    }

    const laid =
      preset === 'sparkline' || spanMs <= 0
        ? []
        : layoutMarks(marks, spanMs, width, MARK_DEDUPE_MIN_PX);

    const markWidth = preset === 'hero' ? 2.5 : 1.5;
    const markRects = laid.map((m) => ({
      x: Math.max(0, Math.min(width - markWidth, m.x - markWidth / 2)),
      w: markWidth,
      fill: colors(scheme).mark[m.kind],
    }));

    const share = classShare(columns);
    const pct = (v: number) => Math.round(v * 100);
    const label =
      `Session timeline: ${pct(share[StripClass.agent])} percent agent working, ` +
      `${pct(share[StripClass.prompting])} percent prompting, ` +
      `${pct(share[StripClass.human_edit])} percent your edits, ` +
      `${pct(share[StripClass.idle])} percent idle. ${marks.length} prompts.`;

    return { rects: merged, markRects, label };
  }, [cols, marks, spanMs, preset, scheme, width]);

  return (
    <View
      style={[{ width, height, borderRadius: CORNER[preset], overflow: 'hidden' }, style]}
      accessible
      accessibilityRole="image"
      accessibilityLabel={label}
    >
      <Svg width={width} height={height}>
        {rects.map((r, i) => (
          <Rect
            key={`c${i}`}
            x={r.x}
            y={0}
            width={r.w + 0.5}
            height={height}
            fill={r.fill}
            opacity={r.opacity}
          />
        ))}
        {markRects.map((m, i) => (
          <Rect key={`m${i}`} x={m.x} y={0} width={m.w} height={height} fill={m.fill} />
        ))}
      </Svg>
    </View>
  );
}
