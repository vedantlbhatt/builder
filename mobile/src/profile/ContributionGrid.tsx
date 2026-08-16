import React from 'react';
import { View } from 'react-native';
import Svg, { Rect } from 'react-native-svg';

import { colors, graphLevel } from '../theme';

interface Props {
  days: { date: string; active_seconds: number }[];
  width: number;
  /**
   * Weeks shown at once. 17, not 52.
   *
   * A year of columns on a 390pt screen gives each day under 6pt, which is below the
   * threshold where the shading is readable at all. 17 weeks is a quarter, fits at a
   * comfortable cell size, and pages horizontally for the rest — a squeezed year is a
   * texture, not a graph.
   */
  weeksPerPage?: number;
}

export function ContributionGrid({ days, width, weeksPerPage = 17 }: Props) {
  const c = colors('dark');
  const gap = 3;
  const cell = Math.floor((width - gap * (weeksPerPage - 1)) / weeksPerPage);
  const height = cell * 7 + gap * 6;

  // Monday-first, and the first column is padded so weekdays line up across columns. A
  // grid whose rows do not mean the same day is unreadable.
  const leading = days.length ? (new Date(days[0]!.date).getUTCDay() + 6) % 7 : 0;

  return (
    <View style={{ width, height }}>
      <Svg width={width} height={height}>
        {days.map((d, i) => {
          const slot = i + leading;
          const week = Math.floor(slot / 7);
          const weekday = slot % 7;
          const level = graphLevel(d.active_seconds);
          return (
            <Rect
              key={d.date}
              x={week * (cell + gap)}
              y={weekday * (cell + gap)}
              width={cell}
              height={cell}
              rx={2}
              fill={c.graph[Math.min(level, c.graph.length - 1)]}
            />
          );
        })}
      </Svg>
    </View>
  );
}
