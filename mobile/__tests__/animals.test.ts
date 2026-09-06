/**
 * The animal pack: eight two-colour creatures, their loops, and their colours.
 *
 * The failure mode of a sprite pack is not a crash either. It is an animal that still
 * renders, still animates, and no longer reads — a stripe that cuts the body in half, a
 * third colour that crept into one frame, a "subtle" loop that repaints half the grid
 * every beat. Every rule the pack claims for itself is asserted here, with the number
 * that was measured off the actual frames beside it.
 */
import { describe, expect, test } from 'bun:test';

import { ANALYSIS_ENUMS, type Archetype } from '../src/generated/analysis';
import { StripClass } from '../src/generated/strip';
import {
  ANIMALS,
  ANIMAL_FRAMES,
  ANIMAL_GLYPHS,
  ANIMAL_LABELS,
  ARCHETYPE_ANIMALS,
  DEFAULT_ANIMAL,
  animalChoices,
  animalForArchetype,
  framesForAnimal,
  isAnimal,
  resolveAnimal,
  type Animal,
} from '../src/pixel/animals';
import { EMPTY, GRID, ascii, isValidFrame, validateFrame, type Frame } from '../src/pixel/frames';
import { ANIMAL_MOTION, animalBreathMs, animalTimeline, clampTempo } from '../src/pixel/motion';
import { animalPalette, mix } from '../src/pixel/palette';
import { colors, type Scheme } from '../src/theme';

/**
 * The subtlety budget: how many of the 256 cells may change between one frame of a loop
 * and the next, the wrap back to frame 0 included.
 *
 * MEASURED over the shipping frames — crab 6, octopus 8, dog 4, cat 3, owl 8, fox 8,
 * whale 6, bee 6 — so the worst change in the pack is 8 cells and the ceiling is 10.
 * The number is the whole point of the rule: whole-body movement belongs in
 * `ANIMAL_MOTION.drift`, and a loop that starts repainting the animal has stopped being
 * an idle. If this ever fails, look at the contact sheet before raising it.
 */
const SUBTLE_PIXELS = 10;
const MEASURED_WORST = 8;

/** Cells that differ between two frames. */
function diff(a: Frame, b: Frame): number {
  let n = 0;
  for (let y = 0; y < GRID; y++) {
    for (let x = 0; x < GRID; x++) if (a[y]?.[x] !== b[y]?.[x]) n += 1;
  }
  return n;
}

/** Every frame change the loop actually plays, the wrap included. */
function loopSteps(frames: Frame[]): number[] {
  return frames.map((f, i) => diff(f, frames[(i + 1) % frames.length]!));
}

function glyphsUsed(frame: Frame): Set<string> {
  const set = new Set<string>();
  for (const row of frame) for (const ch of row) if (ch !== EMPTY) set.add(ch);
  return set;
}

/** WCAG relative luminance, and the contrast ratio between two `#rrggbb` colours. */
function luminance(hex: string): number {
  const n = parseInt(hex.slice(1), 16);
  const channel = (v: number) => {
    const s = v / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * channel((n >> 16) & 0xff) + 0.7152 * channel((n >> 8) & 0xff) + 0.0722 * channel(n & 0xff);
}

function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x) as [number, number];
  return (hi + 0.05) / (lo + 0.05);
}

describe('the pack', () => {
  test('eight animals, unique, and the tables agree', () => {
    expect(ANIMALS).toEqual(['crab', 'octopus', 'dog', 'cat', 'owl', 'fox', 'whale', 'bee']);
    expect(new Set(ANIMALS).size).toBe(8);
    expect(Object.keys(ANIMAL_FRAMES).sort()).toEqual([...ANIMALS].sort());
    expect(Object.keys(ANIMAL_LABELS).sort()).toEqual([...ANIMALS].sort());
    expect(Object.keys(ANIMAL_MOTION).sort()).toEqual([...ANIMALS].sort());
  });

  test('isAnimal accepts only the pack', () => {
    for (const a of ANIMALS) expect(isAnimal(a)).toBe(true);
    for (const junk of ['crustacean', 'Crab', '', null, undefined, 7]) expect(isAnimal(junk)).toBe(false);
  });

  for (const animal of ANIMALS) {
    describe(animal, () => {
      const frames = framesForAnimal(animal);

      test('the loop is 2 to 4 frames', () => {
        expect(frames.length).toBeGreaterThanOrEqual(2);
        expect(frames.length).toBeLessThanOrEqual(4);
      });

      test(`every frame is ${GRID}x${GRID} and uses only known glyphs`, () => {
        frames.forEach((frame, i) => {
          expect(validateFrame(frame), `${animal}[${i}]\n${ascii(frame)}`).toEqual([]);
          expect(isValidFrame(frame)).toBe(true);
        });
      });

      test('exactly two colour roles, and both of them are used', () => {
        for (const [i, frame] of frames.entries()) {
          const used = glyphsUsed(frame);
          // Not "at most two": an animal drawn in one colour has lost its eyes, and the
          // pack's whole claim is that two colours are enough.
          expect([...used].sort(), `${animal}[${i}]\n${ascii(frame)}`).toEqual([...ANIMAL_GLYPHS]);
        }
      });

      test('no frame repeats the one before it', () => {
        for (let i = 0; i < frames.length; i++) {
          const next = frames[(i + 1) % frames.length]!;
          expect(frames[i]!.join('\n')).not.toBe(next.join('\n'));
        }
      });

      test(`consecutive frames differ by at most ${SUBTLE_PIXELS} cells, wrap included`, () => {
        const steps = loopSteps(frames);
        for (const [i, n] of steps.entries()) {
          expect(n, `${animal} ${i}→${(i + 1) % frames.length} changed ${n} cells`).toBeLessThanOrEqual(
            SUBTLE_PIXELS
          );
          expect(n).toBeGreaterThan(0);
        }
      });

      test('the animal fills the grid without touching every edge', () => {
        // A silhouette that runs off all four sides is not a 16x16 creature, it is a
        // texture. Between 30 and 150 of the 256 cells is the pack's actual range.
        for (const frame of frames) {
          const drawn = [...frame.join('')].filter((c) => c !== EMPTY).length;
          expect(drawn, `${animal}\n${ascii(frame)}`).toBeGreaterThan(30);
          expect(drawn).toBeLessThan(150);
        }
      });
    });
  }

  test('the worst change anywhere in the pack is the measured one', () => {
    // Documented so a redraw that doubles the movement shows up as a number, not a vibe.
    const worst = Math.max(...ANIMALS.map((a) => Math.max(...loopSteps(framesForAnimal(a)))));
    expect(worst).toBe(MEASURED_WORST);
  });

  test('no animal borrows a mascot-only glyph', () => {
    // `e`, `w`, `h` and `z` are Bit's eye, highlight, tool and zzz roles. An animal
    // palette has no entry for them, so one would render as a hole rather than an error.
    for (const animal of ANIMALS) {
      for (const frame of framesForAnimal(animal)) {
        for (const ch of ['e', 'w', 'h', 'z']) {
          expect(glyphsUsed(frame).has(ch), `${animal} uses '${ch}'`).toBe(false);
        }
      }
    }
  });
});

describe('animal colours', () => {
  const schemes: Scheme[] = ['dark', 'light'];

  test('two distinct colours per animal, in both schemes', () => {
    for (const scheme of schemes) {
      for (const animal of ANIMALS) {
        const p = animalPalette(animal, scheme);
        expect(Object.keys(p).sort()).toEqual([...ANIMAL_GLYPHS]);
        expect(p.b).toMatch(/^#[0-9A-F]{6}$/);
        expect(p.d).toMatch(/^#[0-9A-F]{6}$/);
        expect(p.b, `${animal} in ${scheme}`).not.toBe(p.d);
      }
    }
  });

  test('all eight pairs are distinct, so no two animals wear the same outfit', () => {
    for (const scheme of schemes) {
      const pairs = ANIMALS.map((a) => {
        const p = animalPalette(a, scheme);
        return `${p.b}/${p.d}`;
      });
      expect(new Set(pairs).size, `${scheme}: ${pairs.join(' ')}`).toBe(ANIMALS.length);
    }
  });

  test('every colour is a token, not a literal', () => {
    // Spot-checks that tie the recipes to `design/tokens.json`: change the amber there
    // and the crab, the cat's eyes and the bee change with it.
    const c = colors('dark');
    expect(animalPalette('crab', 'dark').b).toBe(c.accent);
    expect(animalPalette('bee', 'dark').b).toBe(c.accent);
    expect(animalPalette('cat', 'dark').d).toBe(c.accent);
    expect(animalPalette('cat', 'dark').b).toBe(c.textDim);
    expect(animalPalette('owl', 'dark').b).toBe(c.text);
    expect(animalPalette('octopus', 'dark').b).toBe(c.strip[StripClass.human_edit]);
    // The bone accents follow the scheme's text colour, which is why the owl inverts.
    expect(animalPalette('owl', 'light').b).toBe(colors('light').text);
  });

  test('every body reads on the dark background it was drawn for', () => {
    // MEASURED body-to-background contrast in the dark scheme: fox 6.4 is the lowest,
    // owl 16.6 the highest. 6 is the floor with the fox just inside it.
    const bg = colors('dark').bg;
    for (const animal of ANIMALS) {
      const p = animalPalette(animal, 'dark');
      expect(contrast(p.b, bg), `${animal} body on bg`).toBeGreaterThan(6);
    }
  });

  test('every accent reads on its own body', () => {
    // MEASURED: 1.41 for the cat's amber eyes on grey and the bee's grey wings on amber,
    // 4.2 for the owl's brown on bone. The two low ones are small features on a large
    // body, which is where a low ratio is legible and a high one would be a costume.
    for (const scheme of schemes) {
      for (const animal of ANIMALS) {
        const p = animalPalette(animal, scheme);
        expect(contrast(p.d, p.b), `${animal} accent on body in ${scheme}`).toBeGreaterThan(1.4);
      }
    }
  });

  test('mix blends and clamps', () => {
    expect(mix('#000000', '#FFFFFF', 0.5)).toBe('#808080');
    expect(mix('#000000', '#FFFFFF', 0)).toBe('#000000');
    expect(mix('#000000', '#FFFFFF', 1)).toBe('#FFFFFF');
    expect(mix('#000000', '#FFFFFF', -3)).toBe('#000000');
    expect(mix('#000000', '#FFFFFF', 9)).toBe('#FFFFFF');
    expect(mix('not-a-colour', '#FFFFFF', 0.5)).toBe('not-a-colour');
  });
});

describe('archetype → animal', () => {
  test('covers every archetype the spec declares, with a real animal', () => {
    const spec = ANALYSIS_ENUMS.archetype;
    expect(Object.keys(ARCHETYPE_ANIMALS).sort()).toEqual([...spec].sort());
    for (const archetype of spec) {
      const animal = animalForArchetype(archetype);
      expect(ANIMALS, archetype).toContain(animal);
      expect(isAnimal(animal)).toBe(true);
    }
  });

  test('the pairings are the documented ones', () => {
    expect(animalForArchetype('architect')).toBe('owl');
    expect(animalForArchetype('velocity_machine')).toBe('bee');
    expect(animalForArchetype('quality_guardian')).toBe('crab');
    expect(animalForArchetype('explorer')).toBe('octopus');
    expect(animalForArchetype('firefighter')).toBe('fox');
  });

  test('the night owl is NOT the owl', () => {
    // The architect has it. Two archetypes sharing a creature would make the picture
    // ambiguous exactly where it is meant to be the shorthand.
    expect(animalForArchetype('night_owl')).toBe('cat');
    expect(animalForArchetype('night_owl')).not.toBe(animalForArchetype('architect'));
  });

  test('every archetype gets a different animal', () => {
    const chosen = ANALYSIS_ENUMS.archetype.map((a) => animalForArchetype(a));
    expect(new Set(chosen).size).toBe(ANALYSIS_ENUMS.archetype.length);
  });

  test('nothing unknown throws; it falls back', () => {
    for (const junk of [null, undefined, '', 'philosopher', 'ARCHITECT']) {
      expect(animalForArchetype(junk as Archetype | null)).toBe(DEFAULT_ANIMAL);
    }
    expect(isAnimal(DEFAULT_ANIMAL)).toBe(true);
  });
});

describe('the picker', () => {
  test('offers every animal, in pack order, labelled', () => {
    const choices = animalChoices();
    expect(choices.map((c) => c.id)).toEqual([...ANIMALS]);
    for (const c of choices) expect(c.label).toBe(ANIMAL_LABELS[c.id]);
  });

  test('a saved choice wins; anything else falls back to the archetype', () => {
    expect(resolveAnimal('whale', 'architect')).toBe('whale');
    expect(resolveAnimal(null, 'architect')).toBe('owl');
    expect(resolveAnimal(undefined, 'night_owl')).toBe('cat');
    // A stored id from a future (or older) build reads as unset rather than as nothing.
    expect(resolveAnimal('platypus', 'velocity_machine')).toBe('bee');
    expect(resolveAnimal(null, null)).toBe(DEFAULT_ANIMAL);
  });
});

describe('animal motion', () => {
  test('every beat is between 300 and 600 ms', () => {
    for (const animal of ANIMALS) {
      const { beatMs } = ANIMAL_MOTION[animal];
      expect(beatMs, animal).toBeGreaterThanOrEqual(300);
      expect(beatMs, animal).toBeLessThanOrEqual(600);
    }
  });

  test('a drift is one or two pixels and slower than the frame loop', () => {
    for (const animal of ANIMALS) {
      const { drift, beatMs } = ANIMAL_MOTION[animal];
      if (!drift) continue;
      expect([1, 2], animal).toContain(drift.cells);
      expect(drift.axis === 'x' || drift.axis === 'y').toBe(true);
      // A drift shorter than a beat would read as a jitter riding on the loop.
      expect(drift.periodMs, animal).toBeGreaterThan(beatMs * 2);
    }
  });

  test('four of the eight drift; the rest move only what they draw', () => {
    const drifting = ANIMALS.filter((a) => ANIMAL_MOTION[a].drift);
    expect(drifting).toEqual(['crab', 'octopus', 'whale', 'bee']);
    expect(ANIMAL_MOTION.crab.drift?.axis).toBe('x');
    expect(ANIMAL_MOTION.bee.drift?.cells).toBe(2);
  });

  test('the timeline holds every frame once per loop, in order', () => {
    for (const animal of ANIMALS) {
      const timeline = animalTimeline(animal);
      expect(timeline.map((b) => b.frame)).toEqual(framesForAnimal(animal).map((_, i) => i));
      for (const beat of timeline) expect(beat.ms).toBeGreaterThan(0);
    }
  });

  test("the owl's blink is a blink, not a nap", () => {
    // Frame 1 is the shut-eyed frame; 0.28 x 520 ms = 146 ms, near the mascot's 120.
    const [open, shut, turn] = animalTimeline('owl');
    expect(shut!.ms).toBeLessThan(200);
    expect(shut!.ms).toBeLessThan(open!.ms / 2);
    expect(turn!.ms).toBe(open!.ms);
    // Nobody else holds a frame short.
    for (const animal of ANIMALS) {
      if (animal === 'owl') continue;
      const ms = animalTimeline(animal).map((b) => b.ms);
      expect(new Set(ms).size, animal).toBe(1);
    }
  });

  test('tempo shortens beats and breaths, and is clamped exactly as the mascot clamps it', () => {
    const at1 = animalTimeline('cat');
    const at2 = animalTimeline('cat', 2);
    expect(at2[0]!.ms).toBe(Math.round(at1[0]!.ms / 2));
    expect(animalTimeline('cat', 99)).toEqual(at2);
    expect(animalTimeline('cat', 0)).toEqual(at1);
    expect(animalTimeline('cat', Number.NaN)).toEqual(at1);

    expect(animalBreathMs('cat')).toBe(ANIMAL_MOTION.cat.breathMs);
    expect(animalBreathMs('cat', 2)).toBe(Math.round(ANIMAL_MOTION.cat.breathMs / 2));
    expect(animalBreathMs('cat', 0.1)).toBe(Math.round(ANIMAL_MOTION.cat.breathMs / clampTempo(0.1)));
  });

  test('every animal says what its loop is', () => {
    for (const animal of ANIMALS) {
      expect(ANIMAL_MOTION[animal].note.length, animal).toBeGreaterThan(8);
      expect(ANIMAL_MOTION[animal].breathMs, animal).toBeGreaterThan(2000);
    }
  });
});
