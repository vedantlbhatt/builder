/**
 * The profile screen's pure decisions. Nothing here renders; these are the rules that
 * turn a computed profile into words, and every one of them is a place a wrong number
 * could be stated confidently.
 */
import { describe, expect, test } from 'bun:test';

import { archetypeTitle, closestRule } from '../src/profile/format';
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
