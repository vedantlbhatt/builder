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
 * EVERY ANIMAL FACES FORWARD. The pack shipped with four head-on (crab, octopus, cat,
 * owl) and four in profile (dog, fox, whale, bee), and side by side the profiles read as a
 * different set of icons: no eye contact, no symmetry, and a silhouette that changes
 * meaning depending on which way it happens to point. The four were redrawn head-on. It
 * costs the obvious side-view gestures — a tail wagging across the frame, a brush
 * sweeping — and buys a pack that looks like one pack, which is the entire job of an icon
 * set. The gestures that replaced them are in the per-animal notes below.
 *
 * Three rules keep the pack subtle, and all three are tested:
 *
 *   1. A loop is 2 to 4 frames. Anything longer stops reading as one gesture.
 *   2. Consecutive frames differ by a handful of pixels — the tip of a tail, one claw,
 *      a pair of eyelids. Whole-body movement is NOT drawn into the frames; it is the
 *      renderer's 1-pixel `drift` (`ANIMAL_MOTION` in `motion.ts`), so a crab sidesteps
 *      and a bee hovers without every frame change repainting the animal.
 *   3. Two animals must not share a silhouette. The dog's ears hang and the fox's stand
 *      up; that one difference is what tells them apart head-on at 16 px, so neither is
 *      free to borrow the other's.
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
// Front on. Long accent ears hanging past the jaw are the whole silhouette: the fox's
// ears go UP and the dog's go DOWN, which is the only thing that reliably separates two
// pointy-faced animals at 16 px. Eyes and the gap under the muzzle are holes in the body.
//
// The tail is what moves, and head on you never see the tail, only its TIP appearing past
// one flank and then the other. Two pixels a beat.

const DOG_0: Frame = [
  '................',
  '.....bbbbbb.....',
  '..ddbbbbbbbbdd..',
  '..ddb.bbbb.bdd..',
  '..ddb.bbbb.bdd..',
  '..ddbbbbbbbbdd..',
  '..ddbbddddbbdd..',
  '..dd.bbbbbb.dd..',
  '...d.bbbbbb.d...',
  '.....bbbbbb.....',
  '....bbbbbbbb....',
  '...bbbbbbbbbb...',
  '..bbbbbbbbbbbb..',
  '..dd.bbbbbb.dd..',
  '................',
  '................',
];

/** The tail tip swings out past the near flank. */
const DOG_1: Frame = [
  '................',
  '.....bbbbbb.....',
  '..ddbbbbbbbbdd..',
  '..ddb.bbbb.bdd..',
  '..ddb.bbbb.bdd..',
  '..ddbbbbbbbbdd..',
  '..ddbbddddbbdd..',
  '..dd.bbbbbb.dd..',
  '...d.bbbbbb.d...',
  '.....bbbbbb.....',
  '....bbbbbbbb....',
  '...bbbbbbbbbb...',
  'ddbbbbbbbbbbbb..',
  '..dd.bbbbbb.dd..',
  '................',
  '................',
];

/** And then the far one. */
const DOG_2: Frame = [
  '................',
  '.....bbbbbb.....',
  '..ddbbbbbbbbdd..',
  '..ddb.bbbb.bdd..',
  '..ddb.bbbb.bdd..',
  '..ddbbbbbbbbdd..',
  '..ddbbddddbbdd..',
  '..dd.bbbbbb.dd..',
  '...d.bbbbbb.d...',
  '.....bbbbbb.....',
  '....bbbbbbbb....',
  '...bbbbbbbbbb...',
  '..bbbbbbbbbbbbdd',
  '..dd.bbbbbb.dd..',
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
// Front on. Tall pointed ears carry the whole read, so they carry the motion too: one tip
// folds and the ear leans, then the other. Bone marks the three places a red fox is white
// and nowhere else — the ear insides, the muzzle and the chest bib.
//
// No tail. Cheek ruffs were tried and dropped: flared at the sides they put two bone nubs
// on the silhouette and the face read as a raccoon's mask.

const FOX_0: Frame = [
  '...b........b...',
  '..bb........bb..',
  '..bdb......bdb..',
  '..bdbb....bbdb..',
  '...bbbbbbbbbb...',
  '...b..bbbb..b...',
  '...bbbbbbbbbb...',
  '...bbbbddbbbb...',
  '....bbddddbb....',
  '.....bbbbbb.....',
  '....bbbbbbbb....',
  '....bbddddbb....',
  '...bbbddddbbb...',
  '...bbbbbbbbbb...',
  '................',
  '................',
];

/** The near ear flicks. */
const FOX_1: Frame = [
  '............b...',
  '.bbb........bb..',
  '..bdb......bdb..',
  '..bdbb....bbdb..',
  '...bbbbbbbbbb...',
  '...b..bbbb..b...',
  '...bbbbbbbbbb...',
  '...bbbbddbbbb...',
  '....bbddddbb....',
  '.....bbbbbb.....',
  '....bbbbbbbb....',
  '....bbddddbb....',
  '...bbbddddbbb...',
  '...bbbbbbbbbb...',
  '................',
  '................',
];

/** The far one answers. */
const FOX_2: Frame = [
  '...b............',
  '..bb........bbb.',
  '..bdb......bdb..',
  '..bdbb....bbdb..',
  '...bbbbbbbbbb...',
  '...b..bbbb..b...',
  '...bbbbbbbbbb...',
  '...bbbbddbbbb...',
  '....bbddddbb....',
  '.....bbbbbb.....',
  '....bbbbbbbb....',
  '....bbddddbb....',
  '...bbbddddbbb...',
  '...bbbbbbbbbb...',
  '................',
  '................',
];

// ─── whale ───────────────────────────────────────────────────────────────────────────
// Head on at the surface: a broad rounded head, eyes as holes at the corners, pectoral
// flippers spread the full width, and a bone mouth line. The spout puffs above the
// blowhole — nothing, a bud, a full plume — and the gentle bob is the renderer's drift.

const WHALE_0: Frame = [
  '................',
  '................',
  '................',
  '................',
  '.....bbbbbb.....',
  '...bbbbbbbbbb...',
  '..bbbbbbbbbbbb..',
  '..b..bbbbbb..b..',
  '..bbbbbbbbbbbb..',
  'bbbbbbbbbbbbbbbb',
  '.bbbbbbbbbbbbbb.',
  '..bbbbbbbbbbbb..',
  '...dddddddddd...',
  '....bbbbbbbb....',
  '................',
  '................',
];

const WHALE_1: Frame = [
  '................',
  '................',
  '................',
  '.......dd.......',
  '.....bbbbbb.....',
  '...bbbbbbbbbb...',
  '..bbbbbbbbbbbb..',
  '..b..bbbbbb..b..',
  '..bbbbbbbbbbbb..',
  'bbbbbbbbbbbbbbbb',
  '.bbbbbbbbbbbbbb.',
  '..bbbbbbbbbbbb..',
  '...dddddddddd...',
  '....bbbbbbbb....',
  '................',
  '................',
];

const WHALE_2: Frame = [
  '................',
  '......d..d......',
  '.......dd.......',
  '.......dd.......',
  '.....bbbbbb.....',
  '...bbbbbbbbbb...',
  '..bbbbbbbbbbbb..',
  '..b..bbbbbb..b..',
  '..bbbbbbbbbbbb..',
  'bbbbbbbbbbbbbbbb',
  '.bbbbbbbbbbbbbb.',
  '..bbbbbbbbbbbb..',
  '...dddddddddd...',
  '....bbbbbbbb....',
  '................',
  '................',
];

// ─── bee ─────────────────────────────────────────────────────────────────────────────
// Head on and hovering: wings out to both sides, eyes as holes, and stripes drawn as
// transparent gaps that stop short of the edges so the body stays one connected shape.
// The beat is the wings losing their top row, which is what a blur looks like when you
// only have two frames, and the hover is the renderer's 2 px drift.

const BEE_0: Frame = [
  '................',
  '................',
  '..dd........dd..',
  '.dddd......dddd.',
  '..dd...bb...dd..',
  '.....bbbbbb.....',
  '....b.bbbb.b....',
  '....b.bbbb.b....',
  '....bbbbbbbb....',
  '....b......b....',
  '....bbbbbbbb....',
  '....b......b....',
  '....bbbbbbbb....',
  '.....bbbbbb.....',
  '................',
  '................',
];

const BEE_1: Frame = [
  '................',
  '................',
  '................',
  '.dddd......dddd.',
  '..dd...bb...dd..',
  '.....bbbbbb.....',
  '....b.bbbb.b....',
  '....b.bbbb.b....',
  '....bbbbbbbb....',
  '....b......b....',
  '....bbbbbbbb....',
  '....b......b....',
  '....bbbbbbbb....',
  '.....bbbbbb.....',
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
  // Back through the resting pose between swings, for the octopus's reason: a wag that
  // only ever goes one way is a propeller.
  dog: [DOG_0, DOG_1, DOG_0, DOG_2],
  cat: [CAT_0, CAT_1, CAT_2],
  owl: [OWL_0, OWL_1, OWL_2],
  // One ear at a time, through the resting pose, for the octopus's reason: a loop that
  // flicked both at once would read as a shrug.
  fox: [FOX_0, FOX_1, FOX_0, FOX_2],
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
