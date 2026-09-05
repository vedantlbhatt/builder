/**
 * Where Bit stands and what he does there — decided by pure functions so the rule that
 * picks a sprite is the same rule that writes the sentence beside it. A mascot that
 * hammers under "Running unattended for 3h" would be a plausible wrong picture.
 */
import { describe, expect, test } from 'bun:test';

import { celebrationFor } from '../src/analysis/format';
import type { BuilderProfile } from '../src/data/api';
import {
  AUTONOMOUS_NOTE_SECONDS,
  TEMPO_FRESH,
  TEMPO_FRESH_SECONDS,
  TEMPO_STALE,
  TEMPO_STALE_SECONDS,
  livePresenceLine,
  spriteForLive,
  tempoForLive,
} from '../src/live/format';
import { clampTempo } from '../src/pixel/motion';
import { SPRITE_STATES } from '../src/pixel/sprites';
import {
  BUILDER_PROFILE_PENDING,
  archetypeLine,
  builderProfileFooter,
  dimensionRows,
  meanLabel,
  pct,
  topPatterns,
  topTags,
  trendGlyph,
  trendLabel,
} from '../src/profile/builderProfile';
import { TAP_TARGET, colors, hitSlopToReach } from '../src/theme';

describe('spriteForLive', () => {
  test('building while attended, sleeping past tauAutonomousSec', () => {
    expect(spriteForLive({})).toBe('building');
    expect(spriteForLive({ autonomous_seconds: 0 })).toBe('building');
    expect(spriteForLive({ autonomous_seconds: AUTONOMOUS_NOTE_SECONDS })).toBe('building');
    expect(spriteForLive({ autonomous_seconds: AUTONOMOUS_NOTE_SECONDS + 1 })).toBe('sleeping');
    expect(spriteForLive({ autonomous_seconds: 11_100 })).toBe('sleeping');
  });

  test('agrees with the presence line at every second around the threshold', () => {
    for (const secs of [0, 1799, 1800, 1801, 7200]) {
      const s = { autonomous_seconds: secs };
      const unattended = livePresenceLine(s as never).startsWith('Running unattended');
      expect(spriteForLive(s)).toBe(unattended ? 'sleeping' : 'building');
    }
  });

  test('only returns declared sprite states', () => {
    expect(SPRITE_STATES).toContain(spriteForLive({}));
    expect(SPRITE_STATES).toContain(spriteForLive({ autonomous_seconds: 99_999 }));
  });
});

describe('tempoForLive', () => {
  const now = Date.parse('2026-09-05T12:00:00Z');
  const at = (secondsAgo: number) => ({ ended_at: new Date(now - secondsAgo * 1000).toISOString() });

  test('fresh rows run quick, quiet rows run slow, the middle is normal', () => {
    expect(tempoForLive(at(0), now)).toBe(TEMPO_FRESH);
    expect(tempoForLive(at(TEMPO_FRESH_SECONDS), now)).toBe(TEMPO_FRESH);
    expect(tempoForLive(at(TEMPO_FRESH_SECONDS + 1), now)).toBe(1);
    expect(tempoForLive(at(TEMPO_STALE_SECONDS), now)).toBe(1);
    expect(tempoForLive(at(TEMPO_STALE_SECONDS + 1), now)).toBe(TEMPO_STALE);
    expect(tempoForLive(at(86_400), now)).toBe(TEMPO_STALE);
  });

  test('an unparseable timestamp is normal speed, not a NaN the sprite has to survive', () => {
    expect(tempoForLive({ ended_at: 'not a date' }, now)).toBe(1);
  });

  test('every value it returns is already inside the sprite\'s clamp', () => {
    for (const secs of [0, 200, 5000]) {
      const t = tempoForLive(at(secs), now);
      expect(clampTempo(t)).toBe(t);
    }
  });
});

describe('celebrationFor', () => {
  test('shipped cheers; every other outcome, and no analysis, does not', () => {
    expect(celebrationFor({ outcome: 'shipped' })).toBe('celebrating');
    for (const outcome of ['progressed', 'explored', 'blocked', 'abandoned', 'maintenance'] as const) {
      expect(celebrationFor({ outcome })).toBeNull();
    }
    expect(celebrationFor(null)).toBeNull();
    expect(celebrationFor(undefined)).toBeNull();
  });
});

describe('builder profile formatting', () => {
  const bp: BuilderProfile = {
    window_days: 90,
    sessions_analysed: 5,
    confidence_mean: 0.716,
    dimensions: {
      planning: { mean: 41.2, sessions: 5, trend: -3.4 },
      steering: { mean: 72.6, sessions: 5, trend: 3.24 },
      execution: { mean: 65, sessions: 5, trend: 0 },
      engineering: { mean: 58.4, sessions: 4, trend: null },
    },
    archetype: {
      modal: 'architect',
      share: 0.6,
      with_archetype: 5,
      distribution: { architect: 3, explorer: 2 },
    },
    build_style: {},
    prompting: {
      specificity_mean: 61,
      correction_share_mean: 0.2,
      question_share_mean: 0.1,
      tone_distribution: { terse: 4, neutral: 1 },
    },
    tags: [
      { tag: 'refactor', sessions: 4 },
      { tag: 'tests', sessions: 4 },
      { tag: 'auth', sessions: 2 },
      { tag: 'ci', sessions: 2 },
      { tag: 'docs', sessions: 1 },
      { tag: 'infra', sessions: 1 },
      { tag: 'zebra', sessions: 1 },
    ],
    decision_patterns: [
      { pattern: 'Asks for a plan first', sessions: 4, example: 'plan before you touch anything' },
      { pattern: 'Demands tests', sessions: 3, example: 'add a test for this first' },
      { pattern: 'Narrows scope', sessions: 3, example: 'only the parser, nothing else' },
      { pattern: 'Reverts fast', sessions: 1, example: 'undo that' },
    ],
  };

  test('trendGlyph: sign only, nothing for flat or unknown', () => {
    expect(trendGlyph(3.2)).toBe('▲');
    expect(trendGlyph(-0.1)).toBe('▼');
    expect(trendGlyph(0)).toBe('');
    expect(trendGlyph(null)).toBe('');
    expect(trendGlyph(undefined)).toBe('');
    expect(trendGlyph(Number.NaN)).toBe('');
  });

  test('trendLabel carries the magnitude with the glyph as its sign', () => {
    expect(trendLabel(3.24)).toBe('▲ 3.2');
    expect(trendLabel(-1.5)).toBe('▼ 1.5');
    expect(trendLabel(0)).toBe('');
    expect(trendLabel(null)).toBe('');
  });

  test('pct: 0-1 to a whole percent, clamped', () => {
    expect(pct(0.6)).toBe('60%');
    expect(pct(0.716)).toBe('72%');
    expect(pct(1.4)).toBe('100%');
    expect(pct(-1)).toBe('0%');
  });

  test('meanLabel rounds and clamps to the 0-100 scale', () => {
    expect(meanLabel(72.6)).toBe('73');
    expect(meanLabel(0.4)).toBe('0');
    expect(meanLabel(104)).toBe('100');
  });

  test('dimensionRows: spec order, only scored dimensions, trend null when unknown', () => {
    const rows = dimensionRows(bp);
    expect(rows.map((r) => r.dimension)).toEqual(['steering', 'execution', 'engineering', 'planning']);
    expect(rows.map((r) => r.label)).toEqual(['steering', 'execution', 'engineering', 'planning']);
    expect(rows[2]).toEqual({ dimension: 'engineering', label: 'engineering', mean: 58.4, sessions: 4, trend: null });
    expect(rows[0]!.trend).toBe(3.24);
    expect(dimensionRows({ dimensions: {} })).toEqual([]);
  });

  test('archetypeLine names the mode, its share, and the sessions it is out of', () => {
    expect(archetypeLine(bp)).toBe('architect · 60% of 5 sessions');
    expect(
      archetypeLine({ archetype: { modal: 'night_owl', share: 1, with_archetype: 1, distribution: { night_owl: 1 } } })
    ).toBe('night owl · 100% of 1 session');
    expect(
      archetypeLine({ archetype: { modal: 'explorer', share: null, with_archetype: 0, distribution: {} } })
    ).toBe('explorer');
    expect(
      archetypeLine({ archetype: { modal: null, share: null, with_archetype: 0, distribution: {} } })
    ).toBeNull();
  });

  test('topTags: five, most sessions first, ties by name', () => {
    expect(topTags(bp).map((t) => t.tag)).toEqual(['refactor', 'tests', 'auth', 'ci', 'docs']);
    // Re-ranks a server that sent them out of order rather than trusting the wire.
    const shuffled = { tags: [...bp.tags].reverse() };
    expect(topTags(shuffled).map((t) => t.tag)).toEqual(['refactor', 'tests', 'auth', 'ci', 'docs']);
    expect(topTags({ tags: [] })).toEqual([]);
  });

  test('topPatterns: three, most sessions first, ties by name', () => {
    expect(topPatterns(bp).map((p) => p.pattern)).toEqual([
      'Asks for a plan first',
      'Demands tests',
      'Narrows scope',
    ]);
    expect(topPatterns(bp, 1)).toHaveLength(1);
  });

  test('footer says how many sessions it stands on; confidence only when known', () => {
    expect(builderProfileFooter(bp)).toBe('5 sessions analysed · last 90 days · confidence 72%');
    expect(builderProfileFooter({ sessions_analysed: 1, window_days: 30, confidence_mean: null })).toBe(
      '1 session analysed · last 30 days'
    );
  });

  test('the pending caption names the threshold the server enforces', () => {
    expect(BUILDER_PROFILE_PENDING).toBe('Analyse three sessions to see how you build');
  });
});

describe('theme additions', () => {
  test('hitSlopToReach grows a small control to the tap target and leaves a big one alone', () => {
    expect(hitSlopToReach(32)).toEqual({ top: 6, bottom: 6, left: 6, right: 6 });
    expect(hitSlopToReach(20)).toEqual({ top: 12, bottom: 12, left: 12, right: 12 });
    expect(hitSlopToReach(44)).toEqual({ top: 0, bottom: 0, left: 0, right: 0 });
    expect(hitSlopToReach(60)).toEqual({ top: 0, bottom: 0, left: 0, right: 0 });
    // Odd remainders round up: 44 - 31 = 13 → 7 each side, never 6.5.
    expect(hitSlopToReach(31).top * 2 + 31).toBeGreaterThanOrEqual(TAP_TARGET);
  });

  test('onAccent is the light scheme text colour, identical in both schemes', () => {
    expect(String(colors('dark').onAccent)).toBe(colors('light').text);
    expect(String(colors('dark').onAccent)).toBe(colors('light').onAccent);
    expect(colors('dark').danger).toMatch(/^#[0-9A-F]{6}$/i);
  });
});
