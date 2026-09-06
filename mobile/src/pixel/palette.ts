import { StripClass } from '../generated/strip';
import { colors, type Scheme } from '../theme';
import type { Animal, AnimalGlyph } from './animals';
import type { Glyph } from './frames';

/**
 * Glyph → sRGB hex, per scheme. Every colour comes from `colors(scheme)` so the mascot
 * re-tints with the app; only the dark body amber is derived, and it is derived by
 * multiplying the accent rather than hard-coding a second amber that would drift from
 * `design/tokens.json` the first time someone re-tuned the accent.
 */
export type SpritePalette = Record<Glyph, string>;

/** Body-shadow factor. 0.62 keeps the shadow unmistakably amber, not brown. */
export const BODY_DARK_FACTOR = 0.62;

export function spritePalette(scheme: Scheme): SpritePalette {
  const c = colors(scheme);
  return {
    b: c.accent,
    d: scale(c.accent, BODY_DARK_FACTOR),
    e: c.bg,
    w: c.text,
    h: c.strip[StripClass.human_edit],
    z: c.textDim,
  };
}

/** Multiply each sRGB channel of a `#rrggbb` colour by `factor`, clamped to 0–255. */
export function scale(hex: string, factor: number): string {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex);
  if (!m) return hex;
  const n = parseInt(m[1]!, 16);
  const ch = (shift: number) =>
    Math.max(0, Math.min(255, Math.round(((n >> shift) & 0xff) * factor)))
      .toString(16)
      .padStart(2, '0');
  return `#${ch(16)}${ch(8)}${ch(0)}`.toUpperCase();
}

// ─── the animal pack ─────────────────────────────────────────────────────────────────

/**
 * The two colours of one animal: `b` the body, `d` the accent (`animals.ts`). Not a
 * `SpritePalette` — an animal has two roles and only two, and giving it the mascot's six
 * would let a sixth colour into a frame without a test noticing.
 */
export type AnimalPalette = Record<AnimalGlyph, string>;

/**
 * The four hues an animal may be built from. Each is a SEMANTIC token, so each already
 * flips with the scheme: `bone` is the text colour, which is near-white on the dark
 * background and near-black on the light one. That is why a snowy owl and a grey cat both
 * stay legible in both schemes without a second table — the pack inherits the contrast
 * decision the surface tokens already made.
 */
type Hue = 'amber' | 'teal' | 'bone' | 'grey';

function hues(scheme: Scheme): Record<Hue, string> {
  const c = colors(scheme);
  return {
    amber: c.accent,
    teal: c.strip[StripClass.human_edit],
    bone: c.text,
    grey: c.textDim,
  };
}

/**
 * How one colour is built: take a hue, optionally blend it toward a second, optionally
 * multiply it. Nothing is a literal — every animal colour traces back to `design/tokens.json`,
 * so re-tuning the amber there re-tunes the crab, the fox and the bee with it.
 */
interface Recipe {
  hue: Hue;
  /** Blend toward this hue by `t` (0 = none, 1 = fully the other hue). */
  toward?: { hue: Hue; t: number };
  /** Multiply each channel afterwards. < 1 darkens; the shadow rule from `scale`. */
  by?: number;
}

/** Linear sRGB-hex blend. `t` of 0 is `a`, 1 is `b`. */
export function mix(a: string, b: string, t: number): string {
  const pa = parse(a);
  const pb = parse(b);
  if (!pa || !pb) return a;
  const k = Math.max(0, Math.min(1, t));
  const ch = (i: number) =>
    Math.round(pa[i]! * (1 - k) + pb[i]! * k)
      .toString(16)
      .padStart(2, '0');
  return `#${ch(0)}${ch(1)}${ch(2)}`.toUpperCase();
}

function parse(hex: string): [number, number, number] | null {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex);
  if (!m) return null;
  const n = parseInt(m[1]!, 16);
  return [(n >> 16) & 0xff, (n >> 8) & 0xff, n & 0xff];
}

/**
 * Eight pairs, one per animal.
 *
 * The set has to read as a family (they are all built from the same four tokens) while
 * staying individually recognisable at 16 px, where SHAPE does most of the work and
 * colour only has to say "a different one". So the bodies spread across the hue circle
 * the tokens allow — two ambers of different weight, a gold, a grey, a bone, a teal and a
 * sage — and where two bodies are close their accents are not.
 */
const ANIMAL_COLOURS: Record<Animal, { body: Recipe; accent: Recipe }> = {
  /** Amber shell, dark rust eyes and pincer line — the Claude crab's own reading. */
  crab: { body: { hue: 'amber' }, accent: { hue: 'amber', by: 0.45 } },
  /** Teal mantle, deep teal tentacle tips. */
  octopus: { body: { hue: 'teal' }, accent: { hue: 'teal', by: 0.5 } },
  /**
   * Gold coat, rust ear, nose and paws. The coat is amber blended halfway to GREY, not
   * to bone: bone is the text colour, so blending toward it makes the dog nearly black in
   * the light scheme and the rust accent then has nothing to sit on.
   */
  dog: { body: { hue: 'amber', toward: { hue: 'grey', t: 0.5 } }, accent: { hue: 'amber', by: 0.45 } },
  /** Grey cat, amber eyes and paws. */
  cat: { body: { hue: 'grey' }, accent: { hue: 'amber' } },
  /** Snowy owl: bone body, brown facial disc, wing bars and feet. */
  owl: { body: { hue: 'bone' }, accent: { hue: 'amber', by: 0.6 } },
  /** Deep amber fox, bone ear insides, chest and tail tip. */
  fox: { body: { hue: 'amber', by: 0.78 }, accent: { hue: 'bone' } },
  /** Sage: teal blended halfway to grey, so it is not the octopus. Bone spout and belly. */
  whale: { body: { hue: 'teal', toward: { hue: 'grey', t: 0.5 } }, accent: { hue: 'bone' } },
  /** Amber bee, grey wings — wings are the one thing on a bee you can see through. */
  bee: { body: { hue: 'amber' }, accent: { hue: 'grey' } },
};

function resolve(recipe: Recipe, palette: Record<Hue, string>): string {
  let out = palette[recipe.hue];
  if (recipe.toward) out = mix(out, palette[recipe.toward.hue], recipe.toward.t);
  if (recipe.by !== undefined) out = scale(out, recipe.by);
  return out;
}

/** The two colours of `animal` in `scheme`. */
export function animalPalette(animal: Animal, scheme: Scheme): AnimalPalette {
  const h = hues(scheme);
  const recipe = ANIMAL_COLOURS[animal];
  return { b: resolve(recipe.body, h), d: resolve(recipe.accent, h) };
}
