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
