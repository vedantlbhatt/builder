import { labelize, pct } from '../analysis/format';
import type { BuilderProfile } from '../data/api';
import { ANALYSIS_ENUMS, type Dimension } from '../generated/analysis';

/**
 * Formatting for the "How you build" card — pure, so it runs under `bun test`.
 *
 * Nothing here computes a statistic. The server already produced every mean, trend and
 * share (builder_profile.py, one SQL query for the dimensions so two pulls agree); this
 * file decides the order things are said in and how a number is written, and refuses to
 * say anything the server did not send.
 */

export { pct } from '../analysis/format';

export interface DimensionRow {
  dimension: Dimension;
  label: string;
  mean: number;
  sessions: number;
  trend: number | null;
}

/**
 * The five dimensions in spec order, skipping any the server did not score. Spec order,
 * not alphabetical: `ANALYSIS_ENUMS.dimension` is the order the Swift and Python sides
 * use too, so the card lines up with the per-session view above it.
 */
export function dimensionRows(bp: Pick<BuilderProfile, 'dimensions'>): DimensionRow[] {
  const rows: DimensionRow[] = [];
  for (const dimension of ANALYSIS_ENUMS.dimension) {
    const d = bp.dimensions[dimension];
    if (!d || !Number.isFinite(d.mean)) continue;
    rows.push({
      dimension,
      label: labelize(dimension),
      mean: d.mean,
      sessions: d.sessions,
      trend: typeof d.trend === 'number' && Number.isFinite(d.trend) ? d.trend : null,
    });
  }
  return rows;
}

/** "▲" for a rising trend, "▼" for a falling one, nothing for flat or unknown. */
export function trendGlyph(trend: number | null | undefined): '▲' | '▼' | '' {
  if (typeof trend !== 'number' || !Number.isFinite(trend) || trend === 0) return '';
  return trend > 0 ? '▲' : '▼';
}

/**
 * "▲ 3.2" / "▼ 1.5", or an empty string when there is no trend to claim. The magnitude is
 * the server's 0.1-rounded points; the glyph carries the sign so the number never needs
 * a minus that reads as a bullet.
 */
export function trendLabel(trend: number | null | undefined): string {
  const g = trendGlyph(trend);
  return g ? `${g} ${Math.abs(trend as number).toFixed(1)}` : '';
}

/** "0-100" mean as a whole number, the way the per-session dimension is shown. */
export function meanLabel(mean: number): string {
  return `${Math.round(Math.min(100, Math.max(0, mean)))}`;
}

/**
 * "architect · 60% of 5 sessions". Null when no session in the window had an archetype
 * (all under fifteen minutes, say) — the card then says nothing rather than "null".
 */
export function archetypeLine(bp: Pick<BuilderProfile, 'archetype'>): string | null {
  const a = bp.archetype;
  const modal = a?.modal ?? null;
  if (!modal) return null;
  const share = typeof a.share === 'number' ? pct(a.share) : null;
  const n = a.with_archetype;
  if (share === null || !n) return labelize(modal);
  return `${labelize(modal)} · ${share} of ${n} session${n === 1 ? '' : 's'}`;
}

/** Most sessions first, ties by name — the server's order, re-applied so it cannot drift. */
function ranked<T extends { sessions: number }>(items: T[], name: (t: T) => string): T[] {
  return [...items].sort((x, y) => y.sessions - x.sessions || name(x).localeCompare(name(y)));
}

export function topTags(bp: Pick<BuilderProfile, 'tags'>, n = 5): { tag: string; sessions: number }[] {
  return ranked(bp.tags ?? [], (t) => t.tag).slice(0, n);
}

export function topPatterns(
  bp: Pick<BuilderProfile, 'decision_patterns'>,
  n = 3
): { pattern: string; sessions: number; example: string }[] {
  return ranked(bp.decision_patterns ?? [], (p) => p.pattern).slice(0, n);
}

/** "5 sessions analysed · last 90 days · confidence 72%". Confidence is omitted when unknown. */
export function builderProfileFooter(
  bp: Pick<BuilderProfile, 'sessions_analysed' | 'window_days' | 'confidence_mean'>
): string {
  const n = bp.sessions_analysed;
  const parts = [`${n} session${n === 1 ? '' : 's'} analysed`, `last ${bp.window_days} days`];
  if (typeof bp.confidence_mean === 'number') parts.push(`confidence ${pct(bp.confidence_mean)}`);
  return parts.join(' · ');
}

/** The caption under Bit while the card has nothing to show yet. */
export const BUILDER_PROFILE_PENDING = 'Analyse three sessions to see how you build';
