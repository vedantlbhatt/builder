/**
 * The recap's pure rules: the tiles a session gets, the headline, the list chip, and
 * what the analysis slot says when there is none.
 */

import { describe, expect, test } from 'bun:test';

import type { SessionDetail } from '../src/data/api';
import {
  ANALYSIS_ABSENT_COPY,
  ANALYSIS_PENDING_COPY,
  analysisEmptyCopy,
  defaultTitle,
  hasPost,
  postBlocker,
  RECAP_WINDOW_MS,
  recapEligible,
  recapHeadline,
  statTiles,
} from '../src/recap/format';

const NOW = Date.parse('2026-09-05T10:00:00Z');

function session(over: Partial<SessionDetail> = {}): SessionDetail {
  return {
    id: 's1',
    client_session_id: 'c1',
    harness: 'claude_code',
    repo_name: 'gt-transit',
    started_at: '2026-09-05T07:00:00Z',
    ended_at: '2026-09-05T09:45:00Z',
    active_seconds: 6120,
    idle_seconds: 300,
    local_date: '2026-09-05',
    title: 'Wire the recap',
    title_source: 'harness',
    notable: true,
    unattended: false,
    timeline_fidelity: 'full',
    is_shared: false,
    post_id: null,
    state: 'final',
    end_reason: 'idle_gap',
    attended_seconds: 5400,
    autonomous_seconds: 720,
    presence_count: 12,
    stats: {
      tokens_reported: true,
      tok_in: 120_000,
      tok_out: 30_000,
      tok_cache_read: 1_000_000,
      tok_cache_w5m: 50_000,
      tok_cache_w1h: 0,
      models: null,
      model_state: 'known',
      human_prompt_count: 10,
      prompt_count_basis: 'typed',
      files_touched: 14,
      lines_added_agent: 1234,
      commit_count: 3,
      agent_line_bucket: 'l',
      attrib_confidence: 'high',
    },
    ...over,
  };
}

const byKey = (s: SessionDetail) => Object.fromEntries(statTiles(s).map((t) => [t.key, t]));

describe('statTiles', () => {
  test('an attended session: the two clocks, prompts, lines, commits, files, tokens', () => {
    const tiles = statTiles(session());
    expect(tiles.map((t) => t.key)).toEqual(['attended', 'autonomous', 'prompts', 'lines', 'commits', 'files', 'tokens']);
    const t = byKey(session());
    expect(t.attended!.value).toBe('1h 30m');
    expect(t.autonomous!.value).toBe('12m');
    expect(t.prompts!.value).toBe('10');
    expect(t.lines!.value).toBe('+1,234');
    expect(t.commits!.value).toBe('3');
    expect(t.files!.value).toBe('14');
    expect(t.tokens!.value).toBe('1.2M');
    expect(t.tokens!.dim).toBeUndefined();
  });

  test('an unattended run hides prompts and the headline says Agent run', () => {
    const run = session({
      unattended: true,
      attended_seconds: 0,
      autonomous_seconds: 11100,
      active_seconds: 11100,
      presence_count: 0,
      title: null,
      stats: { ...session().stats!, human_prompt_count: 0 },
    });
    expect(statTiles(run).map((t) => t.key)).not.toContain('prompts');
    expect(byKey(run).attended!.value).toBe('0s');
    expect(recapHeadline(run)).toBe('Agent run · 3h 5m');
  });

  test('tokens the editor never reports are "not recorded", dim, never 0', () => {
    const cursor = session({ stats: { ...session().stats!, tokens_reported: false, tok_in: null, tok_out: null } });
    expect(byKey(cursor).tokens).toEqual({ key: 'tokens', label: 'Tokens', value: 'not recorded', dim: true });
  });

  test('a server older than the split gets one Active tile, never 0 / 0', () => {
    const old = session({ attended_seconds: undefined, autonomous_seconds: undefined });
    const keys = statTiles(old).map((t) => t.key);
    expect(keys[0]).toBe('active');
    expect(keys).not.toContain('attended');
    expect(byKey(old).active!.value).toBe('1h 42m');
  });

  test('no stats at all still renders zeros rather than crashing', () => {
    const bare = session({ stats: null });
    expect(byKey(bare).prompts!.value).toBe('0');
    expect(byKey(bare).tokens!.dim).toBe(true);
  });
});

describe('recapHeadline / defaultTitle', () => {
  test('the session’s title, else its attended time', () => {
    expect(recapHeadline(session())).toBe('Wire the recap');
    expect(recapHeadline(session({ title: null }))).toBe('1h 30m session');
    expect(recapHeadline(session({ title: null, attended_seconds: undefined, autonomous_seconds: undefined }))).toBe(
      '1h 42m session'
    );
  });
  test('the title field starts with the session title, and never with the Agent run line', () => {
    expect(defaultTitle(session())).toBe('Wire the recap');
    expect(defaultTitle(session({ title: null, unattended: true }))).toBe('');
  });
});

describe('hasPost / recapEligible', () => {
  test('post_id decides when the server sends it; is_shared when it does not', () => {
    expect(hasPost({ is_shared: false, post_id: 'p' })).toBe(true);
    expect(hasPost({ is_shared: false, post_id: null })).toBe(false);
    expect(hasPost({ is_shared: true, post_id: undefined })).toBe(true);
    expect(hasPost({ is_shared: false, post_id: undefined })).toBe(false);
  });

  test('finished in the last hour and unposted → chip', () => {
    expect(recapEligible(session(), NOW)).toBe(true);
  });
  test('just outside the window → no chip; on the edge → chip', () => {
    expect(recapEligible(session(), Date.parse(session().ended_at) + RECAP_WINDOW_MS)).toBe(true);
    expect(recapEligible(session(), Date.parse(session().ended_at) + RECAP_WINDOW_MS + 1)).toBe(false);
  });
  test('posted, live, or a clock from the future → no chip', () => {
    expect(recapEligible(session({ post_id: 'p1' }), NOW)).toBe(false);
    expect(recapEligible(session({ post_id: undefined, is_shared: true }), NOW)).toBe(false);
    expect(recapEligible(session({ state: 'live' }), NOW)).toBe(false);
    expect(recapEligible(session({ ended_at: '2026-09-05T11:00:00Z' }), NOW)).toBe(false);
  });
  test('an unattended run within the hour qualifies; its push opens the same sheet', () => {
    expect(recapEligible(session({ unattended: true }), NOW)).toBe(true);
  });
  test('an unparseable end is not eligible', () => {
    expect(recapEligible(session({ ended_at: 'soon' }), NOW)).toBe(false);
  });
});

describe('analysisEmptyCopy', () => {
  test('within the window the Mac’s line; after it, the detail’s', () => {
    expect(analysisEmptyCopy(session(), NOW)).toBe(ANALYSIS_PENDING_COPY);
    expect(analysisEmptyCopy(session(), NOW + 2 * RECAP_WINDOW_MS)).toBe(ANALYSIS_ABSENT_COPY);
    expect(ANALYSIS_PENDING_COPY).toBe('Analysis runs when the session ends');
  });
  test('a live session is always still coming', () => {
    expect(analysisEmptyCopy(session({ state: 'live' }), NOW + 10 * RECAP_WINDOW_MS)).toBe(ANALYSIS_PENDING_COPY);
  });
});

describe('postBlocker', () => {
  test('offline and the sample block with a reason; otherwise nothing does', () => {
    expect(postBlocker({ offline: true, busy: false, sample: false })).toMatch(/offline/);
    expect(postBlocker({ offline: false, busy: false, sample: true })).toMatch(/sample/);
    expect(postBlocker({ offline: false, busy: true, sample: false })).toBeNull();
    expect(postBlocker({ offline: false, busy: false, sample: false })).toBeNull();
  });
});
