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
  hero: 72,
};

/** Hero geometry: a thin lane of your moves, a gap, then the agent's activity. */
const HERO = {
  moves: 12,
  gap: 5,
  activity: 46,
  baseline: 1,
  labels: 8,
  bar: 3,
} as const;

/**
 * How tall an activity bar stands, per density bucket.
 *
 * The old hero painted every non-idle column full height and varied only the alpha, so a
 * 72-minute session was a solid amber block with a few slightly paler stripes: nothing to
 * read. Height carries the density instead, idle draws nothing at all, and the rhythm of
 * the session (burst, pause, burst) is the shape you see.
 */
const DENSITY_HEIGHT = [0.34, 0.58, 0.8, 1.0];

interface Band {
  x: number;
  w: number;
  y: number;
  h: number;
  fill: string;
  opacity: number;
}

interface Drawn {
  rects: Band[];
  markRects: Band[];
  label: string;
}

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

  const { rects, markRects, label } = useMemo<Drawn>(() => {
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
    // One bar per pixel turns an hour of steady work into visual noise: neighbouring
    // columns differ by one density bucket and the eye reads static. The hero averages
    // into bars about `HERO.bar` px wide, which is what makes the burst/pause rhythm
    // legible; the row and sparkline presets stay per-pixel, where they are only ever a
    // texture.
    const target =
      preset === 'hero'
        ? Math.max(1, Math.round(width / HERO.bar))
        : Math.max(1, Math.round(width));
    const columns = resampleColumns(bytes, target);
    const colWidth = width / columns.length;

    const hero = preset === 'hero';
    const activityTop = HERO.moves + HERO.gap;
    const activityH = HERO.activity;

    const merged: Band[] = [];
    for (let i = 0; i < columns.length; i++) {
      const col = columns[i]!;
      // Idle is the absence of work. On the hero it draws nothing, so a pause is a real
      // gap above the baseline rather than a darker shade of busy.
      if (hero && col.klass === StripClass.idle) continue;

      const fill = colors(scheme).strip[col.klass];
      const opacity = hero
        ? 1
        : col.klass === StripClass.idle
          ? 1
          : DENSITY_ALPHAS[
              preset === 'sparkline' ? (col.density >= 2 ? DENSITY_ALPHAS.length - 1 : 0) : col.density
            ] ?? 1;

      // On the hero a prompting column is a full-height tick: seconds of typing next to
      // an hour of agent work would otherwise round away to nothing.
      const frac = hero
        ? col.klass === StripClass.prompting
          ? 1
          : DENSITY_HEIGHT[col.density] ?? 1
        : 1;
      const h = hero ? Math.max(2, activityH * frac) : height;
      const y = hero ? activityTop + (activityH - h) : 0;

      const last = merged[merged.length - 1];
      if (last && last.fill === fill && last.opacity === opacity && last.y === y && last.h === h) {
        last.w += colWidth;
      } else {
        merged.push({ x: i * colWidth, w: colWidth, y, h, fill, opacity });
      }
    }

    const laid =
      preset === 'sparkline' || spanMs <= 0
        ? []
        : layoutMarks(marks, spanMs, width, MARK_DEDUPE_MIN_PX);

    // Your moves ride in their own lane above the activity, so a prompt is never buried
    // under the agent run it started.
    const markWidth = hero ? 3 : 1.5;
    const markRects: Band[] = laid.map((m) => ({
      x: Math.max(0, Math.min(width - markWidth, m.x - markWidth / 2)),
      w: markWidth,
      y: 0,
      h: hero ? HERO.moves : height,
      fill: colors(scheme).mark[m.kind],
      opacity: 1,
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
      style={[
        { width, height, borderRadius: CORNER[preset], overflow: 'hidden' },
        style,
      ]}
      accessible
      accessibilityRole="image"
      accessibilityLabel={label}
    >
      <Svg width={width} height={height}>
        {rects.map((r, i) => (
          <Rect
            key={`c${i}`}
            x={r.x}
            y={r.y}
            width={r.w + 0.5}
            height={r.h}
            rx={preset === 'hero' ? 1 : 0}
            fill={r.fill}
            opacity={r.opacity}
          />
        ))}
        {markRects.map((m, i) => (
          <Rect key={`m${i}`} x={m.x} y={m.y} width={m.w} height={m.h} rx={1} fill={m.fill} />
        ))}
        {preset === 'hero' && (
          // The floor the activity stands on. Without it a long pause reads as a missing
          // strip rather than as quiet.
          <Rect
            x={0}
            y={HERO.moves + HERO.gap + HERO.activity}
            width={width}
            height={HERO.baseline}
            fill={colors(scheme).strip[StripClass.idle]}
          />
        )}
      </Svg>
    </View>
  );
}
