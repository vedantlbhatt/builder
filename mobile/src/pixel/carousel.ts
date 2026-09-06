import { ANIMALS, ANIMAL_LABELS, type Animal, isAnimal } from './animals';

/**
 * The wrap-around list behind the icon picker: one creature in the middle, a chevron
 * either side.
 *
 * Pure, so `bun test` runs it. The screen is a rendering of these functions and nothing
 * else, because the only things that can be wrong here are wrapping and identity, and
 * both are invisible in a screenshot: an off-by-one at the seam means the last chevron
 * press lands on the wrong creature, which a person notices on their first pass through
 * and no test would ever catch.
 *
 * WHY IT WRAPS RATHER THAN STOPPING. Eight is few enough that a dead end at either edge
 * reads as a broken button rather than as the end of the list. There is no "back to the
 * start" affordance to add and none is needed.
 */

/** Where a chevron press moves: -1 for the left one, +1 for the right. */
export type Step = -1 | 1;

/** The index of an animal, or 0 for anything unknown. */
export function indexOf(animal: string | null | undefined): number {
  const i = ANIMALS.indexOf(animal as Animal);
  return i < 0 ? 0 : i;
}

/**
 * The animal `step` presses away, wrapping at both ends.
 *
 * Modulo in JavaScript keeps the sign of the dividend, so `-1 % 8` is `-1` and a naive
 * version indexes off the front of the array at the left edge. The extra `+ length` is
 * what makes the left chevron on the first creature land on the last one.
 */
export function step(animal: string | null | undefined, by: Step): Animal {
  const n = ANIMALS.length;
  const next = (indexOf(animal) + by + n) % n;
  return ANIMALS[next]!;
}

/** "crab", "3 of 8" and the two neighbours, everything the screen needs to draw a frame. */
export interface CarouselView {
  animal: Animal;
  label: string;
  /** 1-based, for "3 of 8". Never 0-based on screen: nobody counts creatures from zero. */
  position: number;
  total: number;
  previous: Animal;
  next: Animal;
}

export function view(animal: string | null | undefined): CarouselView {
  const current = isAnimal(animal) ? animal : ANIMALS[0]!;
  return {
    animal: current,
    label: ANIMAL_LABELS[current],
    position: indexOf(current) + 1,
    total: ANIMALS.length,
    previous: step(current, -1),
    next: step(current, 1),
  };
}

/**
 * The creature to open the picker on.
 *
 * Their own choice first, then the one their archetype earned, then the first in the
 * pack. Opening on the pack's first creature when the rules already picked one for them
 * would throw away the only personalised thing the app knows at that moment.
 */
export function openOn(
  chosen: string | null | undefined,
  suggested: string | null | undefined
): Animal {
  if (isAnimal(chosen)) return chosen;
  if (isAnimal(suggested)) return suggested;
  return ANIMALS[0]!;
}
