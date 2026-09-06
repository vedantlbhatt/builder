import { describe, expect, test } from 'bun:test';

import type {
  ReportAgents,
  ReportContributions,
  ReportQuality,
  ReportTrend,
} from '../src/generated/report';
import {
  assistedShare,
  fanoutLine,
  fanoutWaste,
  greenLine,
  shortDuration,
  streakLine,
  trendValues,
  trendVerdict,
  trendWords,
} from '../src/profile/report';

function trend(over: Partial<ReportTrend> = {}): ReportTrend {
  return {
    metric: 'test_runs_per_hour',
    label: 'how often you test',
    before: 2,
    now: 4,
    move: 1,
    direction: 'up',
    good: true,
    sessions_before: 5,
    sessions_now: 6,
    ...over,
  };
}

function agents(over: Partial<ReportAgents> = {}): ReportAgents {
  return {
    agents: 53,
    produced: 51,
    max_concurrent: 8,
    agent_seconds: 42752,
    wall_seconds: 69562,
    busy_seconds: 14581,
    parallelism: 2.93,
    by_type: [{ name: 'general-purpose', agents: 52 }],
    ...over,
  };
}

describe('a trend reads as a change, never as a verdict it does not have', () => {
  test('the move is a percentage of the earlier value', () => {
    expect(trendWords(trend())).toBe('up 100%');
    expect(trendWords(trend({ direction: 'down', move: -0.62 }))).toBe('down 62%');
  });

  test('steady is a real answer and not an arrow', () => {
    expect(trendWords(trend({ direction: 'steady', move: 0.05 }))).toBe('steady');
    expect(trendVerdict(trend({ direction: 'steady' }))).toBeNull();
  });

  test('a metric with no direction gets no verdict', () => {
    // More hours is not better, more tokens is not worse, a night owl is not broken.
    expect(trendVerdict(trend({ metric: 'night_share', good: null }))).toBeNull();
  });

  test('a metric with a direction gets the one it earned', () => {
    expect(trendVerdict(trend({ good: true }))).toBe('good');
    expect(trendVerdict(trend({ good: false }))).toBe('bad');
  });

  test('the two values keep a precision they can support', () => {
    expect(trendValues(trend({ before: 0.5, now: 0.19 }))).toBe('0.50 → 0.19');
    expect(trendValues(trend({ before: 527, now: 1329 }))).toBe('527 → 1329');
    expect(trendValues(trend({ before: 4.63, now: 4.81 }))).toBe('4.6 → 4.8');
  });
});

describe('durations', () => {
  test('minutes and hours, never a fractional hour', () => {
    expect(shortDuration(117)).toBe('2m');
    expect(shortDuration(3600)).toBe('1h');
    expect(shortDuration(42752)).toBe('11h 53m');
  });

  test('a run shorter than a minute still happened', () => {
    // "0m back to green" reads as a bug; it took a moment, and a moment is one minute.
    expect(shortDuration(20)).toBe('1m');
  });
});

describe('fan-out', () => {
  test('the peak sits beside the average, because they are different days', () => {
    expect(fanoutLine(agents())).toBe('53 agents, 11h 53m of agent work, up to 8 at once.');
  });

  test('one at a time does not claim a peak', () => {
    expect(fanoutLine(agents({ agents: 2, max_concurrent: 1, agent_seconds: 600 }))).toBe(
      '2 agents, 10m of agent work.'
    );
  });

  test('the delegations that returned nothing are named', () => {
    expect(fanoutWaste(agents())).toBe('2 of them produced nothing at all.');
  });

  test('nothing is said when every one of them produced something', () => {
    // "51 of 51" costs a row and says nothing.
    expect(fanoutWaste(agents({ agents: 51, produced: 51 }))).toBeNull();
  });
});

describe('commits, and the streak that is about shipping', () => {
  const contributions = (over: Partial<ReportContributions> = {}): ReportContributions => ({
    assisted: 96,
    alone: 8,
    active_days: 2,
    longest_streak: 2,
    current_streak: 2,
    days: [],
    ...over,
  });

  test('the assisted share is out of every commit', () => {
    expect(assistedShare(contributions())).toBeCloseTo(96 / 104, 6);
  });

  test('no commits is null rather than a zero share', () => {
    expect(assistedShare(contributions({ assisted: 0, alone: 0 }))).toBeNull();
  });

  test('a streak of zero is not a sentence', () => {
    // Telling somebody their streak is 0 is a scold. This screen is a measurement.
    expect(streakLine(contributions({ current_streak: 0 }))).toBeNull();
  });

  test('the best is named only when it beats today', () => {
    expect(streakLine(contributions({ current_streak: 2, longest_streak: 9 }))).toBe(
      '2 days in a row, best 9.'
    );
    expect(streakLine(contributions({ current_streak: 9, longest_streak: 9 }))).toBe(
      '9 days in a row.'
    );
  });

  test('one day is a day', () => {
    expect(streakLine(contributions({ current_streak: 1, longest_streak: 1 }))).toBe(
      '1 day in a row.'
    );
  });
});

describe('time to green', () => {
  const quality = (over: Partial<ReportQuality> = {}): ReportQuality => ({
    runs: 41,
    passed: 36,
    failed: 5,
    first_try_rate: 0.878,
    time_to_green: { n: 3, median_seconds: 117, worst_seconds: 466, median_attempts: 2 },
    reason: null,
    ...over,
  });

  test('the median recovery and how many runs it took', () => {
    expect(greenLine(quality())).toBe('2m back to green, 2 runs typically.');
  });

  test('one run is not worth saying twice', () => {
    expect(
      greenLine(
        quality({
          time_to_green: { n: 3, median_seconds: 60, worst_seconds: 90, median_attempts: 1 },
        })
      )
    ).toBe('1m back to green.');
  });

  test('nothing failed and then passed, so there is no number and no zero', () => {
    // A refused recovery must never render as "0m back to green", which would read as
    // the best possible score for a corpus that has no score at all.
    expect(greenLine(quality({ time_to_green: null }))).toBeNull();
  });
});
