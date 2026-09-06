/**
 * The profile screen's pure decisions. Nothing here renders; these are the rules that
 * turn a computed profile into words, and every one of them is a place a wrong number
 * could be stated confidently.
 */
import { describe, expect, test } from 'bun:test';

import { archetypeTitle, closestRule, ruleSentence } from '../src/profile/format';
import { animalForArchetype, CORPUS_ARCHETYPE_ANIMALS, DEFAULT_ANIMAL } from '../src/pixel/animals';

describe('archetypeTitle', () => {
  test('a wire name becomes a title', () => {
    expect(archetypeTitle('velocity_machine')).toBe('Velocity Machine');
    expect(archetypeTitle('architect')).toBe('Architect');
    expect(archetypeTitle('night_owl')).toBe('Night Owl');
  });
  test('an archetype from a newer server is still readable', () => {
    // The screen never validates the name against an enum: the corpus rules are not a
    // wire enum and can grow. A raw `some_new_type` on screen is bad; a title is fine.
    expect(archetypeTitle('some_new_type')).toBe('Some New Type');
  });
});

describe('the hero picks an animal for every archetype the server can send', () => {
  test('the corpus-only archetypes have their own creature', () => {
    // `director` and `skeptic` come only from analysis/profile.py. Before they were
    // mapped they fell back to the default crab, which reads as a bug on a profile whose
    // headline says "Director".
    expect(animalForArchetype('director')).toBe(CORPUS_ARCHETYPE_ANIMALS.director);
    expect(animalForArchetype('skeptic')).toBe(CORPUS_ARCHETYPE_ANIMALS.skeptic);
    expect(animalForArchetype('director')).not.toBe(DEFAULT_ANIMAL);
  });
  test('no archetype at all still draws something', () => {
    expect(animalForArchetype(null)).toBe(DEFAULT_ANIMAL);
  });
});

describe('closestRule', () => {
  const scored = (name: string, value: number | null, threshold: number, score: number | null) => ({
    name,
    metric: name,
    rule: `${name} rule`,
    value,
    threshold,
    score,
  });

  test('picks the highest scorer among the rules that actually scored', () => {
    const best = closestRule([
      scored('architect', null, 2.4, null),
      scored('velocity_machine', 449.5, 487, 0.461),
      scored('night_owl', 0.218, 0.4, 0.272),
    ]);
    expect(best?.name).toBe('velocity_machine');
  });

  test('a refused metric is never "closest"', () => {
    // Null score means the metric it reads could not be computed at all. There is no
    // distance to that threshold, only an absence, and reporting it as nearly-met would
    // be a number the server explicitly declined to state.
    expect(closestRule([scored('architect', null, 2.4, null)])).toBeNull();
    expect(closestRule([])).toBeNull();
  });
});


describe('the rule under the archetype carries the number that earned it', () => {
  /**
   * FOUND BY LOOKING AT IT in a browser: the hero showed "Velocity Machine" over the bare
   * phrase "agent lines per active hour" — the metric's NAME and no number anywhere. The
   * "no clear type" fallback beneath it had been building the full sentence all along, so
   * the case that matters more said less.
   */
  test('the winning rule reads as a result, not a label', () => {
    expect(
      ruleSentence({ rule: 'agent lines per active hour', value: 1086.2, threshold: 487 })
    ).toBe('agent lines per active hour: 1086 against 487.');
  });

  test('a small ratio keeps the precision it has', () => {
    expect(ruleSentence({ rule: 'prompts under ten words', value: 0.44, threshold: 0.3 })).toBe(
      'prompts under ten words: 0.44 against 0.30.'
    );
    expect(ruleSentence({ rule: 'test runs an hour', value: 5.75, threshold: 3 })).toBe(
      'test runs an hour: 5.8 against 3.'
    );
  });

  test('a server that sends no numbers gets the rule alone rather than "null against null"', () => {
    expect(ruleSentence({ rule: 'agent lines per active hour', value: null, threshold: null })).toBe(
      'agent lines per active hour'
    );
  });

  test('no rule is nothing, so the card falls through to its own explanation', () => {
    expect(ruleSentence({ rule: null, value: null, threshold: null })).toBeNull();
  });
});
