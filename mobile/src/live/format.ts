import type { SessionDetail } from '../data/api';
import { duration } from '../theme';

/** Past this much quiet the agent is on its own; mirrors Tuning.tauAutonomousSec (30 min). */
export const AUTONOMOUS_NOTE_SECONDS = 1800;

/** "Live · 2h 14m · 34 prompts". The prompt count is omitted, not zeroed, when unknown. */
export function liveStatusLine(s: SessionDetail): string {
  const parts = ['Live', duration(s.active_seconds)];
  const prompts = s.stats?.human_prompt_count;
  if (typeof prompts === 'number') parts.push(`${prompts} prompt${prompts === 1 ? '' : 's'}`);
  return parts.join(' · ');
}

/** The second line: is anyone there? */
export function livePresenceLine(s: SessionDetail): string {
  const autonomous = s.autonomous_seconds ?? 0;
  return autonomous > AUTONOMOUS_NOTE_SECONDS
    ? `Running unattended for ${duration(autonomous)}`
    : "You're at the keyboard";
}

/**
 * Which Bit stands beside a live row. The same threshold as the presence line, so the
 * picture and the sentence can never disagree: `building` while someone is evidently at
 * the keyboard, `sleeping` once the agent has been on its own past tauAutonomousSec.
 */
export function spriteForLive(s: Pick<SessionDetail, 'autonomous_seconds'>): 'building' | 'sleeping' {
  return (s.autonomous_seconds ?? 0) > AUTONOMOUS_NOTE_SECONDS ? 'sleeping' : 'building';
}

/** A live row whose last record is this fresh is being worked right now. */
export const TEMPO_FRESH_SECONDS = 120;
/** Past this the Mac has gone quiet; Bit slows down rather than pretending otherwise. */
export const TEMPO_STALE_SECONDS = 900;
export const TEMPO_FRESH = 1.4;
export const TEMPO_STALE = 0.7;

/**
 * How fast Bit works beside a live row. A live row's `ended_at` is the last record the Mac
 * had seen (server: `_live_rows`), so its age is the one honest measure of "recent
 * activity" the phone has: fresh → quicker, quiet → slower, unparseable → normal. Three
 * steps, not a curve, so two rows a few seconds apart do not visibly run at different speeds.
 */
export function tempoForLive(s: Pick<SessionDetail, 'ended_at'>, nowMs: number = Date.now()): number {
  const last = Date.parse(s.ended_at);
  if (!Number.isFinite(last)) return 1;
  const ageSeconds = (nowMs - last) / 1000;
  if (ageSeconds <= TEMPO_FRESH_SECONDS) return TEMPO_FRESH;
  if (ageSeconds <= TEMPO_STALE_SECONDS) return 1;
  return TEMPO_STALE;
}
