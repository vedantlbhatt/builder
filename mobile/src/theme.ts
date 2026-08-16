import { tokens } from './generated/tokens';
import { StripClass, StripMarkKind } from './generated/strip';

export type Scheme = 'light' | 'dark';

/**
 * Colours, resolved from the generated tokens.
 *
 * Every value is sRGB hex, which is what `react-native-svg` expects — and the Swift side
 * uses `Color(.sRGB, ...)` rather than the bare initialiser for the same reason. SwiftUI's
 * `Color(red:green:blue:)` is Display P3, so using it would make the Mac card visibly more
 * saturated than the byte-identical phone card, and the difference would only show up when
 * someone put two screenshots side by side.
 */
export function colors(scheme: Scheme) {
  const pick = (pair: { light: string; dark: string }) => pair[scheme];

  return {
    strip: {
      [StripClass.idle]: pick(tokens.strip.idle),
      [StripClass.prompting]: pick(tokens.strip.prompting),
      [StripClass.agent]: pick(tokens.strip.agent),
      [StripClass.human_edit]: pick(tokens.strip.human_edit),
    } as Record<StripClass, string>,

    mark: {
      [StripMarkKind.prompt]: pick(tokens.mark.prompt),
      [StripMarkKind.commit]: pick(tokens.mark.commit),
      [StripMarkKind.compact]: pick(tokens.mark.compact),
    } as Record<StripMarkKind, string>,

    bg: pick(tokens.surface.bg),
    card: pick(tokens.surface.card),
    border: pick(tokens.surface.border),
    text: pick(tokens.surface.text),
    textDim: pick(tokens.surface.textDim),
    accent: pick(tokens.surface.accent),

    graph: tokens.graph.levels[scheme],
  };
}

export const space = tokens.space;
export const radius = tokens.radius;

/** Duration, the way a person says it. "1h 42m", never "102 minutes". */
export function duration(seconds: number): string {
  const s = Math.round(seconds);
  if (s < 60) return `${s}s`;
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  return h > 0 ? `${h}h ${m}m` : `${m}m`;
}

export function compactNumber(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${Math.round(n / 1_000)}k`;
  return `${n}`;
}

/**
 * Contribution-graph level from active hours.
 *
 * Absolute buckets, matching Tuning.graphHourBuckets on the Swift side — self-relative
 * quantiles would make two people's graphs incomparable, which defeats the point of a
 * graph anyone screenshots.
 */
const GRAPH_HOUR_BUCKETS = [0, 0.5, 2, 4, 8];

export function graphLevel(activeSeconds: number): number {
  const hours = activeSeconds / 3600;
  let level = 0;
  for (const edge of GRAPH_HOUR_BUCKETS) if (hours > edge) level += 1;
  return Math.min(level, 5);
}
