/**
 * The words the profile screen puts around computed numbers.
 *
 * Pure, and free of React Native imports, so `bun test` can run it: these are the rules
 * that turn a wire value into a sentence, and a wrong one here states a wrong thing
 * confidently, which is the failure mode this repo cares about most.
 */

/**
 * `velocity_machine` reads as a bug. `Velocity Machine` reads as a title.
 *
 * Deliberately NOT a lookup table of the known archetypes: the corpus rules in
 * `analysis/profile.py` are computation rather than a wire enum and can grow, and a name
 * this build has never heard of should still arrive on screen as English.
 */
export function archetypeTitle(name: string): string {
  return name
    .split('_')
    .filter(Boolean)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(' ');
}

/**
 * The rule this corpus came CLOSEST to, when no rule met its threshold.
 *
 * "Not enough yet" is the wrong thing to say to somebody with fifty sessions who simply
 * is not an extreme of anything: the sample is fine, the corpus just did not cross a
 * line. Naming the nearest rule and the distance to it turns a blank card into a
 * measurement, and it is the same number the archetype would have been decided on.
 *
 * Rules whose metric was refused have a null score and cannot be "closest": there is no
 * distance to report, only an absence.
 */
export function closestRule<
  T extends { name: string; rule: string; value: number | null; threshold: number; score: number | null },
>(scores: readonly T[]): T | null {
  const scored = scores.filter((s) => s.score !== null && s.value !== null);
  if (scored.length === 0) return null;
  return scored.reduce((best, s) => (s.score! > best.score! ? s : best));
}
