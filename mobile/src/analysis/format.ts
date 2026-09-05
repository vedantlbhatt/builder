import type { EndReason, SessionDetail } from '../data/api';
import type { SessionAnalysis } from '../generated/analysis';

/**
 * Formatting for the analysis section — kept pure so it can be tested without a renderer.
 *
 * Nothing here computes a statistic. Every number shown was either measured by the engine
 * or written by the model with a rationale; this file only decides how to say it.
 */

/** 0-1 → "72%". Clamped: a model that writes 1.2 gets 100%, not a lie with a decimal. */
export function pct(fraction: number): string {
  const f = Number.isFinite(fraction) ? Math.min(1, Math.max(0, fraction)) : 0;
  return `${Math.round(f * 100)}%`;
}

/** Enum values are snake_case on the wire; people read "plan mode", not "plan_mode". */
export function labelize(value: string): string {
  return value.replace(/_/g, ' ');
}

/** "2.5 h" / "8 h" / "40 min". Used where a sentence needs an amount, not a clock reading. */
export function hoursText(seconds: number): string {
  const s = Math.max(0, seconds);
  if (s < 3600) return `${Math.round(s / 60)} min`;
  const h = Math.round((s / 3600) * 10) / 10;
  return `${h % 1 === 0 ? h.toFixed(0) : h.toFixed(1)} h`;
}

type EndFields = Pick<SessionDetail, 'end_reason' | 'autonomous_seconds' | 'state'>;

/**
 * The one-line note under Numbers that explains a non-idle end. `idle_gap` needs no note —
 * the work stopped, which is what a person expects a session end to mean.
 */
export function describeEnd(s: EndFields): string | null {
  const reason: EndReason | undefined = s.end_reason;
  switch (reason) {
    case 'human_returned':
      return `You came back after ${hoursText(s.autonomous_seconds ?? 0)} of autonomous work — that started a new session`;
    case 'day_boundary':
      return 'Split at 04:00 while running unattended';
    default:
      return null;
  }
}

/** "just now", "5m ago", "3h ago", "2d ago", then a date. */
export function relativeTime(iso: string, nowMs: number = Date.now()): string {
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return '';
  const diff = Math.max(0, nowMs - t);
  const min = Math.floor(diff / 60_000);
  if (min < 1) return 'just now';
  if (min < 60) return `${min}m ago`;
  const h = Math.floor(min / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  if (d < 14) return `${d}d ago`;
  return new Date(t).toLocaleDateString();
}

/** The footer under the analysis: who wrote it, how sure it was, and when. */
export function analysisFooter(
  a: Pick<SessionAnalysis, 'model' | 'confidence' | 'generated_at'>,
  nowMs: number = Date.now()
): string {
  const when = relativeTime(a.generated_at, nowMs);
  return `Analysed by ${a.model} · confidence ${pct(a.confidence)}${when ? ` · ${when}` : ''}`;
}

export const SENSITIVE_WARNING =
  'This analysis mentions something that looks sensitive — check before sharing.';

/** "t+38m" / "t+2h 05m" for the pivot timeline. */
export function pivotTime(atMinute: number): string {
  const m = Math.max(0, Math.round(atMinute));
  if (m < 60) return `t+${m}m`;
  const h = Math.floor(m / 60);
  const rem = m % 60;
  return `t+${h}h ${rem.toString().padStart(2, '0')}m`;
}

/**
 * The one-time flourish beside an analysis headline. Only `shipped` earns it: a model that
 * wrote `progressed` or `explored` is saying the work is not done, and a cheering mascot
 * over that headline would contradict the sentence it sits next to. Null means no sprite,
 * not a different one.
 */
export function celebrationFor(
  a: Pick<SessionAnalysis, 'outcome'> | null | undefined
): 'celebrating' | null {
  return a?.outcome === 'shipped' ? 'celebrating' : null;
}
