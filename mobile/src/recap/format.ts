/**
 * The pure rules under the recap screen: which tiles a session gets, what the headline
 * says, whether a session in the list deserves a "Recap" chip, and what the analysis
 * slot says when there is none. No React, no network, so bun can pin every rule.
 *
 * Nothing here computes a statistic. Every number is the server's, read off the
 * `SessionDetail`; this file decides only how to say it and when to leave it out.
 */

import type { SessionDetail } from '../data/api';
import { compactNumber, duration } from '../theme';

/**
 * How long after a session ends the list still offers the recap, and the analysis slot
 * still says it is coming. One hour: the completion push arrives ~1000 s after the end
 * (server/builder/notify.py, EXPECTED_FINAL_LAG_SEC) and a person opens it in the minutes
 * after; past an hour the moment is gone and the chip would be noise on every row.
 */
export const RECAP_WINDOW_MS = 60 * 60 * 1000;

/** The Mac's line (MenuBarPanel) and the detail's, for a session whose analysis may come. */
export const ANALYSIS_PENDING_COPY = 'Analysis runs when the session ends';
/** The detail's line for a session that will not grow one by waiting. */
export const ANALYSIS_ABSENT_COPY = 'Analysis not available for this session';

export interface StatTile {
  key: string;
  label: string;
  value: string;
  /** "not recorded": the tile is a statement about the editor, not the session. */
  dim?: boolean;
}

/**
 * "Agent run" for an unattended session, else the session's own title, else its attended
 * time. The word "run" is the same one the notification used ("Agent run finished"), so
 * the screen a tap opens says what the banner said.
 */
export function recapHeadline(s: SessionDetail): string {
  if (s.unattended) return `Agent run · ${duration(s.active_seconds)}`;
  if (s.title) return s.title;
  return `${duration(s.attended_seconds ?? s.active_seconds)} session`;
}

/** The title the recap's title field starts with. Never the "Agent run" line: that is a headline, not a name. */
export function defaultTitle(s: SessionDetail): string {
  return s.title ?? '';
}

/**
 * The stat tiles, Strava's distance/pace/elevation row adapted to a build session.
 *
 * Attended and autonomous lead when the server split them (a server older than the split
 * gets one Active tile instead — never "0 / 0"). Prompts are hidden on an unattended run:
 * zero presence signals is the definition, and a "0 prompts" tile would read as a
 * complaint about a machine. Tokens are "not recorded" rather than 0 when the editor
 * never reports them. Tool calls and lines removed are NOT here: the read route
 * (`server/builder/routes/sessions.py`) does not return them to the phone.
 */
export function statTiles(s: SessionDetail): StatTile[] {
  const stats = (s.stats ?? {}) as Record<string, unknown>;
  const n = (k: string) => Number(stats[k] ?? 0);
  const tiles: StatTile[] = [];
  const hasSplit = s.attended_seconds !== undefined || s.autonomous_seconds !== undefined;

  if (hasSplit) {
    tiles.push({ key: 'attended', label: 'Attended', value: duration(s.attended_seconds ?? 0) });
    tiles.push({ key: 'autonomous', label: 'Autonomous', value: duration(s.autonomous_seconds ?? 0) });
  } else {
    tiles.push({ key: 'active', label: 'Active', value: duration(s.active_seconds) });
  }
  if (!s.unattended) {
    tiles.push({ key: 'prompts', label: 'Prompts', value: `${n('human_prompt_count')}` });
  }
  tiles.push({ key: 'lines', label: 'Lines added', value: `+${n('lines_added_agent').toLocaleString()}` });
  tiles.push({ key: 'commits', label: 'Commits', value: `${n('commit_count')}` });
  tiles.push({ key: 'files', label: 'Files touched', value: `${n('files_touched')}` });
  if (stats.tokens_reported) {
    const total =
      n('tok_in') + n('tok_out') + n('tok_cache_read') + n('tok_cache_w5m') + n('tok_cache_w1h');
    tiles.push({ key: 'tokens', label: 'Tokens', value: compactNumber(total) });
  } else {
    // Absent, not zero. Cursor accounts usage server-side and writes {0,0} locally.
    tiles.push({ key: 'tokens', label: 'Tokens', value: 'not recorded', dim: true });
  }
  return tiles;
}

/** Whether the detail says a post exists. `post_id` when the server sends it, else `is_shared`. */
export function hasPost(s: Pick<SessionDetail, 'is_shared' | 'post_id'>): boolean {
  if (s.post_id === undefined) return s.is_shared;
  return s.post_id !== null;
}

/**
 * Whether a session in the list gets the "Recap" chip: finished, ended within
 * `RECAP_WINDOW_MS`, and not yet posted. A live session's numbers still move; a posted
 * one has had its recap. An unattended run qualifies — its push opens the same sheet.
 */
export function recapEligible(
  s: Pick<SessionDetail, 'ended_at' | 'is_shared' | 'post_id' | 'state'>,
  now: number = Date.now()
): boolean {
  if ((s.state ?? 'final') !== 'final') return false;
  if (hasPost(s)) return false;
  const ended = Date.parse(s.ended_at);
  if (!Number.isFinite(ended)) return false;
  const age = now - ended;
  return age >= 0 && age <= RECAP_WINDOW_MS;
}

/**
 * What the analysis slot says with no analysis. Within the recap window the analysis may
 * still be on its way — the Mac runs it after the final and re-uploads — so the line is
 * the Mac's own. Past it, nothing is coming, and the detail's quieter line is the truth.
 */
export function analysisEmptyCopy(
  s: Pick<SessionDetail, 'ended_at' | 'state'>,
  now: number = Date.now()
): string {
  if ((s.state ?? 'final') !== 'final') return ANALYSIS_PENDING_COPY;
  const ended = Date.parse(s.ended_at);
  if (Number.isFinite(ended) && now - ended <= RECAP_WINDOW_MS) return ANALYSIS_PENDING_COPY;
  return ANALYSIS_ABSENT_COPY;
}

/** Why Post is disabled, or null when it is not. */
export function postBlocker(input: { offline: boolean; busy: boolean; sample: boolean }): string | null {
  if (input.sample) return 'The sample session cannot be posted.';
  if (input.offline) return "You're offline. Post when you're back on the network.";
  if (input.busy) return null;
  return null;
}
