/**
 * The animal pack — eight tiny two-colour creatures.
 *
 * Bit (`sprites.ts`) is the app's mascot and stays as it is: six palette roles, a face,
 * props. THIS pack is the opposite discipline, and the discipline is the point. Each
 * animal is a flat silhouette in exactly TWO colours — `b`, the body, and `d`, the
 * accent — with no outline, no shading and no third value. The reference is Anthropic's
 * Claude crab: it reads at 16 px because there is nothing in it to lose.
 *
 * Frames are the same 16×16 strings the mascot uses (`frames.ts`), so `validateFrame`,
 * `runsFor`, `mirror` and the whole renderer work here unchanged. `d` is the ACCENT
 * role, not necessarily the darker one — a grey cat has amber eyes (`palette.ts`).
 *
 * Two rules keep the pack subtle, and both are tested:
 *
 *   1. A loop is 2 to 4 frames. Anything longer stops reading as one gesture.
 *   2. Consecutive frames differ by a handful of pixels — the tip of a tail, one claw,
 *      a pair of eyelids. Whole-body movement is NOT drawn into the frames; it is the
 *      renderer's 1-pixel `drift` (`ANIMAL_MOTION` in `motion.ts`), so a crab sidesteps
 *      and a bee hovers without every frame change repainting the animal.
 *
 * A transparent pixel inside a body reads as the background — that is how eyes and the
 * gaps between a bee's stripes are drawn, and it costs no third colour.
 */

import type { Frame } from './frames';

export type { Frame } from './frames';

/**
 * The pack, in presentation order: the picker in Settings and the contact sheet both
 * read this list, so the order here is the order a person sees.
 */
export const ANIMALS = [
  'crab',
  'octopus',
  'dog',
  'cat',
  'owl',
  'fox',
  'whale',
  'bee',
] as const;

export type Animal = (typeof ANIMALS)[number];

/** The only two glyphs an animal frame may draw. See the module note on `d`. */
export const ANIMAL_GLYPHS = ['b', 'd'] as const;
export type AnimalGlyph = (typeof ANIMAL_GLYPHS)[number];

/** Display names. Lower case, because every other caption in the app is. */
export const ANIMAL_LABELS: Record<Animal, string> = {
  crab: 'crab',
  octopus: 'octopus',
  dog: 'dog',
  cat: 'cat',
  owl: 'owl',
  fox: 'fox',
  whale: 'whale',
  bee: 'bee',
};

// ─── crab ────────────────────────────────────────────────────────────────────────────
// Claws snip (the pincer gap closes) and the feet swap; the sidestep itself is the
// renderer's ±1 px drift, so the shell never redraws.

const CRAB_0: Frame = [
  '................',
  '................',
  '.bb..........bb.',
  'bbbb........bbbb',
  'b.bb........bb.b',
  '.bb...bbbb...bb.',
  '..bb.bbbbbb.bb..',
  '...bbdbbbbdbb...',
  '...bbbbbbbbbb...',
  '..bbbbbbbbbbbb..',
  '..bbbbbbbbbbbb..',
  '...bbbbbbbbbb...',
  '...b.b.bb.b.b...',
  '..b..b....b..b..',
  '................',
  '................',
];

const CRAB_1: Frame = [
  '................',
  '................',
  '.bb..........bb.',
  'bbbb........bbbb',
  'bbbb........bbbb',
  '.bb...bbbb...bb.',
  '..bb.bbbbbb.bb..',
  '...bbdbbbbdbb...',
  '...bbbbbbbbbb...',
  '..bbbbbbbbbbbb..',
  '..bbbbbbbbbbbb..',
  '...bbbbbbbbbb...',
  '...b.b.bb.b.b...',
  '...b.b....b.b...',
  '................',
  '................',
];

// ─── octopus ─────────────────────────────────────────────────────────────────────────
// The mantle is still; only the last two rows of the tentacles move — curled out, straight,
// curled in — with the accent on the tips so the wiggle is legible at 16 px.

const OCTO_0: Frame = [
  '................',
  '.....bbbbbb.....',
  '...bbbbbbbbbb...',
  '..bbbbbbbbbbbb..',
  '..bbbbbbbbbbbb..',
  '..bbddbbbbddbb..',
  '..bbbbbbbbbbbb..',
  '..bbbbbbbbbbbb..',
  '...bbbbbbbbbb...',
  '..bb.bb..bb.bb..',
  '..bb.bb..bb.bb..',
  '.bb..bb..bb..bb.',
  '.d....d..d....d.',
  '................',
  '................',
  '................',
];

const OCTO_1: Frame = [
  '................',
  '.....bbbbbb.....',
  '...bbbbbbbbbb...',
  '..bbbbbbbbbbbb..',
  '..bbbbbbbbbbbb..',
  '..bbddbbbbddbb..',
  '..bbbbbbbbbbbb..',
  '..bbbbbbbbbbbb..',
  '...bbbbbbbbbb...',
  '..bb.bb..bb.bb..',
  '..bb.bb..bb.bb..',
  '..bb.bb..bb.bb..',
  '..d...d..d...d..',
  '................',
  '................',
  '................',
];

const OCTO_2: Frame = [
  '................',
  '.....bbbbbb.....',
  '...bbbbbbbbbb...',
  '..bbbbbbbbbbbb..',
  '..bbbbbbbbbbbb..',
  '..bbddbbbbddbb..',
  '..bbbbbbbbbbbb..',
  '..bbbbbbbbbbbb..',
  '...bbbbbbbbbb...',
  '..bb.bb..bb.bb..',
  '..bb.bb..bb.bb..',
  '...bbbb..bbbb...',
  '...d..d..d..d...',
  '................',
  '................',
  '................',
];

// ─── dog ─────────────────────────────────────────────────────────────────────────────
// Side view, facing left. Only the tail moves: down, mid, up. Ear, nose and paws carry
// the accent so the head reads without a face.

const DOG_0: Frame = [
  '................',
  '................',
  '.dd.............',
  '.bbb.........bb.',
  'bbbbb.......bb..',
  'b.bbb.......b...',
  'bbbbbbbbbbbbbb..',
  'dbbbbbbbbbbbbb..',
  '.bbbbbbbbbbbbb..',
  '.bbbbbbbbbbbbb..',
  '..bbbbbbbbbbb...',
  '..bb.....bbb....',
  '..bb.....bbb....',
  '..dd.....ddd....',
  '................',
  '................',
];

const DOG_1: Frame = [
  '................',
  '................',
  '.dd.............',
  '.bbb........bb..',
  'bbbbb.......bb..',
  'b.bbb.......b...',
  'bbbbbbbbbbbbbb..',
  'dbbbbbbbbbbbbb..',
  '.bbbbbbbbbbbbb..',
  '.bbbbbbbbbbbbb..',
  '..bbbbbbbbbbb...',
  '..bb.....bbb....',
  '..bb.....bbb....',
  '..dd.....ddd....',
  '................',
  '................',
];

const DOG_2: Frame = [
  '................',
  '................',
  '.dd.............',
  '.bbb.......bb...',
  'bbbbb......bb...',
  'b.bbb.......b...',
  'bbbbbbbbbbbbbb..',
  'dbbbbbbbbbbbbb..',
  '.bbbbbbbbbbbbb..',
  '.bbbbbbbbbbbbb..',
  '..bbbbbbbbbbb...',
  '..bb.....bbb....',
  '..bb.....bbb....',
  '..dd.....ddd....',
  '................',
  '................',
];

// ─── cat ─────────────────────────────────────────────────────────────────────────────
// Sitting, facing left, tail up the right side. Frame 1 flicks the tail tip; frame 2
// twitches the near ear. Eyes are the accent — a grey cat with amber eyes.

const CAT_0: Frame = [
  '................',
  '................',
  '.b.....b........',
  '.bb...bb........',
  '.bbbbbbb........',
  '.bdbbbdb........',
  '.bbbbbbb........',
  '..bbbbb.........',
  '..bbbbbb........',
  '..bbbbbbb...bb..',
  '..bbbbbbb..bb...',
  '..bbbbbbb..bb...',
  '..bbbbbbbbbb....',
  '..dbbbbbbbd.....',
  '................',
  '................',
];

const CAT_1: Frame = [
  '................',
  '................',
  '.b.....b........',
  '.bb...bb........',
  '.bbbbbbb........',
  '.bdbbbdb........',
  '.bbbbbbb........',
  '..bbbbb.........',
  '..bbbbbb....bb..',
  '..bbbbbbb...bb..',
  '..bbbbbbb..bb...',
  '..bbbbbbb..bb...',
  '..bbbbbbbbbb....',
  '..dbbbbbbbd.....',
  '................',
  '................',
];

const CAT_2: Frame = [
  '................',
  '................',
  '.b....bb........',
  '.bb...bb........',
  '.bbbbbbb........',
  '.bdbbbdb........',
  '.bbbbbbb........',
  '..bbbbb.........',
  '..bbbbbb........',
  '..bbbbbbb...bb..',
  '..bbbbbbb..bb...',
  '..bbbbbbb..bb...',
  '..bbbbbbbbbb....',
  '..dbbbbbbbd.....',
  '................',
  '................',
];

// ─── owl ─────────────────────────────────────────────────────────────────────────────
// Front on, ear tufts, a facial disc drawn only by the accent. Frame 1 is the blink (the
// upper eye row becomes body); frame 2 turns the head one pixel to the left.

const OWL_0: Frame = [
  '................',
  '................',
  '..bb........bb..',
  '..bbb......bbb..',
  '...bbbbbbbbbb...',
  '..bbbbbbbbbbbb..',
  '..bddbbbbbbddb..',
  '..bddbbbbbbddb..',
  '..bbbbbddbbbbb..',
  '..bbbbbbbbbbbb..',
  '..bdbbbbbbbbdb..',
  '..bdbbbbbbbbdb..',
  '...bbbbbbbbbb...',
  '....bbbbbbbb....',
  '....dd....dd....',
  '................',
];

const OWL_1: Frame = [
  '................',
  '................',
  '..bb........bb..',
  '..bbb......bbb..',
  '...bbbbbbbbbb...',
  '..bbbbbbbbbbbb..',
  '..bbbbbbbbbbbb..',
  '..bddbbbbbbddb..',
  '..bbbbbddbbbbb..',
  '..bbbbbbbbbbbb..',
  '..bdbbbbbbbbdb..',
  '..bdbbbbbbbbdb..',
  '...bbbbbbbbbb...',
  '....bbbbbbbb....',
  '....dd....dd....',
  '................',
];

const OWL_2: Frame = [
  '................',
  '................',
  '..bb........bb..',
  '..bbb......bbb..',
  '...bbbbbbbbbb...',
  '..bbbbbbbbbbbb..',
  '..bddbbbbbddbb..',
  '..bddbbbbbddbb..',
  '..bbbbddbbbbbb..',
  '..bbbbbbbbbbbb..',
  '..bdbbbbbbbbdb..',
  '..bdbbbbbbbbdb..',
  '...bbbbbbbbbb...',
  '....bbbbbbbb....',
  '....dd....dd....',
  '................',
];

// ─── fox ─────────────────────────────────────────────────────────────────────────────
// Side view, facing left, the brush sweeping behind. Bone accent on the tail tip, the
// chest and the ear insides — the only white a fox needs.

const FOX_0: Frame = [
  '................',
  '................',
  '.b.....b........',
  '.bd...db.....bb.',
  '.bbbbbbb....bbdd',
  'bbbbbbbb...bbbdd',
  'bbbbbbbbbbbbbb..',
  'dbbbbbbbbbbbb...',
  '.dbbbbbbbbbbb...',
  '..bbbbbbbbbbb...',
  '..bbbbbbbbbbb...',
  '..bb.....bbb....',
  '..bb.....bbb....',
  '..bb.....bbb....',
  '................',
  '................',
];

const FOX_1: Frame = [
  '................',
  '................',
  '.b.....b........',
  '.bd...db........',
  '.bbbbbbb.....bb.',
  'bbbbbbbb....bbdd',
  'bbbbbbbbbbbbbbdd',
  'dbbbbbbbbbbbb...',
  '.dbbbbbbbbbbb...',
  '..bbbbbbbbbbb...',
  '..bbbbbbbbbbb...',
  '..bb.....bbb....',
  '..bb.....bbb....',
  '..bb.....bbb....',
  '................',
  '................',
];

// ─── whale ───────────────────────────────────────────────────────────────────────────
// Facing left, fluke up at the right. The spout puffs: nothing, a bud, a full plume. The
// gentle bob is the renderer's drift.

const WHALE_0: Frame = [
  '................',
  '................',
  '................',
  '................',
  '................',
  '....bbbbbb......',
  '..bbbbbbbbbb..b.',
  '.bbbbbbbbbbbb.bb',
  '.b.bbbbbbbbbbbbb',
  '.bbbbbbbbbbbb.bb',
  '..bbbbbbbbbb..b.',
  '...dddddddd.....',
  '................',
  '................',
  '................',
  '................',
];

const WHALE_1: Frame = [
  '................',
  '................',
  '................',
  '................',
  '......d.........',
  '....bbbbbb......',
  '..bbbbbbbbbb..b.',
  '.bbbbbbbbbbbb.bb',
  '.b.bbbbbbbbbbbbb',
  '.bbbbbbbbbbbb.bb',
  '..bbbbbbbbbb..b.',
  '...dddddddd.....',
  '................',
  '................',
  '................',
  '................',
];

const WHALE_2: Frame = [
  '................',
  '................',
  '.....d.d........',
  '.....ddd........',
  '......d.........',
  '....bbbbbb......',
  '..bbbbbbbbbb..b.',
  '.bbbbbbbbbbbb.bb',
  '.b.bbbbbbbbbbbbb',
  '.bbbbbbbbbbbb.bb',
  '..bbbbbbbbbb..b.',
  '...dddddddd.....',
  '................',
  '................',
  '................',
  '................',
];

// ─── bee ─────────────────────────────────────────────────────────────────────────────
// Wings up, wings blurred flat: two frames at a fast beat, plus the hover drift. Stripes
// are transparent gaps, so the body stays one colour and the bee stays two.

const BEE_0: Frame = [
  '................',
  '................',
  '................',
  '................',
  '................',
  '.....dd..dd.....',
  '....ddd..ddd....',
  '....bbbbbbbb....',
  '..bbbbb.bbb.bb..',
  '..b.bbb.bbb.bbd.',
  '..bbbbb.bbb.bb..',
  '....bbbbbbbb....',
  '................',
  '................',
  '................',
  '................',
];

const BEE_1: Frame = [
  '................',
  '................',
  '................',
  '................',
  '................',
  '................',
  '...dddd..dddd...',
  '....bbbbbbbb....',
  '..bbbbb.bbb.bb..',
  '..b.bbb.bbb.bbd.',
  '..bbbbb.bbb.bb..',
  '....bbbbbbbb....',
  '................',
  '................',
  '................',
  '................',
];

// ─── table ───────────────────────────────────────────────────────────────────────────

export const ANIMAL_FRAMES: Record<Animal, Frame[]> = {
  crab: [CRAB_0, CRAB_1],
  // Four beats, not three: the wiggle PING-PONGS through the straight pose. Played
  // out · straight · in · out the loop's wrap is a two-step swing (12 pixels) where
  // every other change is one (8) — the tentacles would snap back once a loop.
  octopus: [OCTO_0, OCTO_1, OCTO_2, OCTO_1],
  // Same reason: a wag that only ever goes one way is a propeller.
  dog: [DOG_0, DOG_1, DOG_2, DOG_1],
  cat: [CAT_0, CAT_1, CAT_2],
  owl: [OWL_0, OWL_1, OWL_2],
  fox: [FOX_0, FOX_1],
  whale: [WHALE_0, WHALE_1, WHALE_2],
  bee: [BEE_0, BEE_1],
};

export function framesForAnimal(animal: Animal): Frame[] {
  return ANIMAL_FRAMES[animal];
}

export function isAnimal(value: unknown): value is Animal {
  return typeof value === 'string' && (ANIMALS as readonly string[]).includes(value);
}

// ─── archetype → animal ──────────────────────────────────────────────────────────────

/**
 * One animal per builder archetype, so the profile screen can show a creature rather than
 * a word. These six keys are the six archetypes in `generated/analysis.ts` — the ones a
 * MODEL assigns to a single session. That file is generated from the spec, so a seventh
 * archetype breaks this table's type rather than silently falling through to the default.
 *
 * The obvious pairs first: an architect gets the owl, a velocity machine the bee, a
 * quality guardian the crab (it walks sideways and checks everything twice).
 *
 * NIGHT OWL DOES NOT GET THE OWL. The architect has it, and two archetypes sharing a
 * creature would make the picture ambiguous exactly where it is supposed to be the
 * shorthand — so the night owl gets the CAT, which is the other thing that is awake at
 * 3 a.m. The remaining two follow the same logic: an explorer gets the octopus (eight
 * arms in eight places) and a firefighter the fox (fast, and never where it was).
 */
export const ARCHETYPE_ANIMALS = {
  architect: 'owl',
  velocity_machine: 'bee',
  quality_guardian: 'crab',
  night_owl: 'cat',
  explorer: 'octopus',
  firefighter: 'fox',
} as const satisfies Record<string, Animal>;

/**
 * The two archetypes the CORPUS profile can reach that a single session cannot.
 *
 * `analysis/profile.py:ARCHETYPE_RULES` scores a whole corpus deterministically, and its
 * six names are not the same six the model picks from per session: it can return
 * `director` (a high autonomy score: you brief and leave) and `skeptic` (test runs per
 * active hour), neither of which is in the spec's per-session enum. Without these two
 * entries a director's profile would show the fallback crab and read as a bug.
 *
 * They are written out here rather than generated because the corpus rules are not in
 * `spec/analysis.v1.json` — they are computation, not a wire enum. The union of the two
 * tables is eight archetypes over the pack's eight animals, and the test asserts that
 * mapping stays one-to-one, so a new archetype on either side cannot quietly share a
 * creature with an existing one.
 */
export const CORPUS_ARCHETYPE_ANIMALS = {
  director: 'dog',
  skeptic: 'whale',
} as const satisfies Record<string, Animal>;

export type ArchetypeKey = keyof typeof ARCHETYPE_ANIMALS;

/** The animal shown when nobody has chosen one and there is no archetype yet. */
export const DEFAULT_ANIMAL: Animal = 'crab';

/**
 * The animal for an archetype. Anything unknown — no analysis yet, a null modal, an
 * archetype from a newer server — falls back to `DEFAULT_ANIMAL` rather than throwing:
 * a profile screen is not the place to discover a spec change.
 */
export function animalForArchetype(archetype: string | null | undefined): Animal {
  if (typeof archetype !== 'string') return DEFAULT_ANIMAL;
  const hit =
    (ARCHETYPE_ANIMALS as Record<string, Animal>)[archetype] ??
    (CORPUS_ARCHETYPE_ANIMALS as Record<string, Animal>)[archetype];
  return hit ?? DEFAULT_ANIMAL;
}

// ─── the picker ──────────────────────────────────────────────────────────────────────

export interface AnimalChoice {
  id: Animal;
  label: string;
}

/** Every animal, in pack order, ready for a Settings list. */
export function animalChoices(): AnimalChoice[] {
  return ANIMALS.map((id) => ({ id, label: ANIMAL_LABELS[id] }));
}

/**
 * What a person's saved choice resolves to: their own pick if it is still a real animal,
 * otherwise their archetype's. A stored id that no longer exists reads as "unset" rather
 * than rendering nothing.
 */
export function resolveAnimal(chosen: string | null | undefined, archetype?: string | null): Animal {
  return isAnimal(chosen) ? chosen : animalForArchetype(archetype);
}
