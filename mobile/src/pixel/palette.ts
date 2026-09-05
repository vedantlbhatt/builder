import { StripClass } from '../generated/strip';
import { colors, type Scheme } from '../theme';
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
