/**
 * The words around the numbers. None of these compute anything; they decide how a
 * measured or model-written value is said, so a wrong string here is a wrong claim.
 */
import { describe, expect, test } from 'bun:test';

import {
  analysisFooter,
  describeEnd,
  hoursText,
  labelize,
  pct,
  relativeTime,
} from '../src/analysis/format';
import { livePresenceLine, liveStatusLine } from '../src/live/format';
import type { SessionDetail } from '../src/data/api';

describe('pct', () => {
  test('rounds a fraction to a whole percent', () => {
    expect(pct(0.72)).toBe('72%');
    expect(pct(0.005)).toBe('1%');
    expect(pct(0)).toBe('0%');
    expect(pct(1)).toBe('100%');
  });
  test('clamps out-of-range and non-finite input', () => {
    expect(pct(1.2)).toBe('100%');
    expect(pct(-0.3)).toBe('0%');
    expect(pct(Number.NaN)).toBe('0%');
  });
});

describe('describeEnd', () => {
  test('human_returned names the autonomous stretch', () => {
    expect(describeEnd({ end_reason: 'human_returned', autonomous_seconds: 6 * 3600 })).toBe(
      'You came back after 6 h of autonomous work, so this is a new session'
    );
    expect(describeEnd({ end_reason: 'human_returned', autonomous_seconds: 9000 })).toBe(
      'You came back after 2.5 h of autonomous work, so this is a new session'
    );
  });
  test('day_boundary is the 04:00 split', () => {
    expect(describeEnd({ end_reason: 'day_boundary' })).toBe(
      'Split at 04:00 while running unattended'
    );
  });
  test('idle_gap, still_running and an older server say nothing', () => {
    expect(describeEnd({ end_reason: 'idle_gap' })).toBeNull();
    expect(describeEnd({ end_reason: 'still_running', state: 'live' })).toBeNull();
    expect(describeEnd({})).toBeNull();
  });
});

describe('hoursText / labelize', () => {
  test('hoursText', () => {
    expect(hoursText(0)).toBe('0 min');
    expect(hoursText(45 * 60)).toBe('45 min');
    expect(hoursText(3600)).toBe('1 h');
    expect(hoursText(5400)).toBe('1.5 h');
    expect(hoursText(8 * 3600 + 120)).toBe('8 h');
  });
  test('labelize', () => {
    expect(labelize('plan_mode')).toBe('plan mode');
    expect(labelize('product_instinct')).toBe('product instinct');
    expect(labelize('shipped')).toBe('shipped');
  });
});

describe('relativeTime / analysisFooter', () => {
  const now = Date.parse('2026-09-05T12:00:00Z');
  test('relativeTime buckets', () => {
    expect(relativeTime('2026-09-05T11:59:40Z', now)).toBe('just now');
    expect(relativeTime('2026-09-05T11:55:00Z', now)).toBe('5m ago');
    expect(relativeTime('2026-09-05T09:00:00Z', now)).toBe('3h ago');
    expect(relativeTime('2026-09-03T12:00:00Z', now)).toBe('2d ago');
    expect(relativeTime('not a date', now)).toBe('');
  });
  test('footer', () => {
    expect(
      analysisFooter(
        { model: 'claude-opus-5', confidence: 0.72, generated_at: '2026-09-05T09:00:00Z' },
        now
      )
    ).toBe('Analysed by claude-opus-5 · confidence 72% · 3h ago');
  });
});

describe('live lines', () => {
  const base = {
    id: 'x',
    client_session_id: 'x',
    harness: 'claude_code',
    repo_name: 'gt-transit',
    started_at: '2026-09-05T09:00:00Z',
    ended_at: '2026-09-05T11:14:00Z',
    active_seconds: 8040,
    idle_seconds: 0,
    local_date: '2026-09-05',
    title: null,
    title_source: null,
    notable: false,
    unattended: false,
    timeline_fidelity: 'full',
    is_shared: false,
    post_id: null,
    state: 'live',
  } satisfies SessionDetail;

  test('status line includes prompts only when the stats carry them', () => {
    expect(liveStatusLine(base)).toBe('Live · 2h 14m');
    const withStats = {
      ...base,
      stats: { human_prompt_count: 34 } as unknown as SessionDetail['stats'],
    };
    expect(liveStatusLine(withStats)).toBe('Live · 2h 14m · 34 prompts');
    const one = { ...base, stats: { human_prompt_count: 1 } as unknown as SessionDetail['stats'] };
    expect(liveStatusLine(one)).toBe('Live · 2h 14m · 1 prompt');
  });

  test('presence line flips at 30 minutes of autonomy', () => {
    expect(livePresenceLine(base)).toBe("You're at the keyboard");
    expect(livePresenceLine({ ...base, autonomous_seconds: 1800 })).toBe("You're at the keyboard");
    expect(livePresenceLine({ ...base, autonomous_seconds: 11_100 })).toBe(
      'Running unattended for 3h 5m'
    );
  });
});

describe('describeEnd — v3 structural ends', () => {
  test('a /clear and a repo switch each get a note; idle_gap still none', () => {
    expect(describeEnd({ end_reason: 'cleared', autonomous_seconds: 0 })).toContain('/clear');
    expect(describeEnd({ end_reason: 'switched_repo', autonomous_seconds: 0 })).toContain('another repo');
    expect(describeEnd({ end_reason: 'idle_gap', autonomous_seconds: 0 })).toBeNull();
  });
});
