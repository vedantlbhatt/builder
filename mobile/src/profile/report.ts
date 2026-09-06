import type {
  BuilderReport,
  ReportAgents,
  ReportContributions,
  ReportQuality,
  ReportTrend,
} from '../generated/report';

/**
 * Turning the measured report into the words on the screen. NO REACT IN HERE, on purpose:
 * every rule below is a sentence a person reads about themselves, and `bun test` can only
 * hold them to it if they are a function.
 *
 * The one rule that runs through all of it: NULL IS NOT ZERO. Every block of the report is
 * null when the machine refused it, and each refusal carries the count that forced it.
 * Rendering a refusal as "0" is the failure mode this whole product exists to avoid, so
 * the helpers below return `null` for "say nothing" and never a zero standing in for one.
 */

/** How a trend reads out loud: "up 152%", "down 62%", "steady". */
export function trendWords(t: ReportTrend): string {
  if (t.direction === 'steady') return 'steady';
  return `${t.direction} ${Math.round(Math.abs(t.move) * 100)}%`;
}

/**
 * The verdict, or nothing.
 *
 * DIRECTION IS NOT VIRTUE. `good` is null for every metric where the answer depends on
 * what the person wants — more hours is not better, more tokens is not worse, a night owl
 * is not broken — and a screen that put a green arrow on all of them would be inventing an
 * opinion the measurement does not have.
 */
export function trendVerdict(t: ReportTrend): 'good' | 'bad' | null {
  if (t.good === null || t.good === undefined || t.direction === 'steady') return null;
  return t.good ? 'good' : 'bad';
}

/** A trend's two values, at a precision that does not pretend to more than it has. */
export function trendValues(t: ReportTrend): string {
  return `${num(t.before)} → ${num(t.now)}`;
}

function num(v: number): string {
  if (v === 0) return '0';
  if (Math.abs(v) >= 100) return String(Math.round(v));
  if (Math.abs(v) >= 1) return v.toFixed(1);
  return v.toFixed(2);
}

/** m and h, never "0.03 hours". Seconds below a minute round up to one. */
export function shortDuration(seconds: number): string {
  if (seconds < 60) return '1m';
  const m = Math.round(seconds / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  const rest = m % 60;
  return rest ? `${h}h ${rest}m` : `${h}h`;
}

/**
 * The one line about fanning out.
 *
 * `parallelism` is agent-seconds over BUSY seconds, so it is never below 1.0 when anything
 * ran and reads as "this many at once". The peak is reported beside it because an average
 * of 2.9 with a peak of 8 is a different day from a steady 2.9.
 */
export function fanoutLine(a: ReportAgents): string {
  const at = a.max_concurrent > 1 ? `, up to ${a.max_concurrent} at once` : '';
  return `${a.agents} agents, ${shortDuration(a.agent_seconds)} of agent work${at}.`;
}

/**
 * What the delegation actually returned, or nothing.
 *
 * Silent when every agent produced something: "51 of 51" is a line that costs a row and
 * says nothing. It is worth the row precisely when it is not all of them.
 */
export function fanoutWaste(a: ReportAgents): string | null {
  const idle = a.agents - a.produced;
  if (idle <= 0) return null;
  return `${idle} of them produced nothing at all.`;
}

/** The share of commits an agent was in the room for, or null when there are none. */
export function assistedShare(c: ReportContributions): number | null {
  const total = c.assisted + c.alone;
  return total > 0 ? c.assisted / total : null;
}

/**
 * The streak sentence, or nothing.
 *
 * A streak counts days you SHIPPED, not days you opened the app. Zero is not a sentence:
 * telling somebody their streak is 0 is a scold, and this screen is a measurement.
 */
export function streakLine(c: ReportContributions): string | null {
  if (c.current_streak <= 0) return null;
  const d = c.current_streak === 1 ? 'day' : 'days';
  const best = c.longest_streak > c.current_streak ? `, best ${c.longest_streak}` : '';
  return `${c.current_streak} ${d} in a row${best}.`;
}

/**
 * Time to green as the VALUE beside a "Back to green" label, or null when nothing in the
 * window failed and then passed.
 *
 * It does not repeat the label. Found by looking at it: the row read "Back to green — 2m
 * back to green, 2 runs typically", which says the same three words twice in eleven.
 *
 * Null rather than a zero. A refused recovery rendered as "0m" would read as the best
 * possible score for a corpus that has no score at all; the section prints the module's
 * own reason instead, and "5 test runs, 5 needed" tells a person what would make the
 * number appear.
 */
export function greenLine(q: ReportQuality): string | null {
  if (!q.time_to_green) return null;
  const g = q.time_to_green;
  const tries = g.median_attempts > 1 ? `, ${g.median_attempts} runs typically` : '';
  return `${shortDuration(g.median_seconds)}${tries}`;
}

/** Which sections have anything to say at all, so the screen can skip its own header. */
export function hasAnything(r: BuilderReport): boolean {
  return Boolean(
    r.trends.length || r.agents || r.contributions || r.quality || r.prompting
  );
}
