/**
 * Bit — the pixel mascot.
 *
 * A friendly retro robot on a 16×16 grid: rounded-square amber body, two eyes with a
 * highlight, a teal antenna bulb, two stubby feet and a smile. Every state reuses the
 * same body so it reads as one character whether it is hammering, sleeping or waving.
 *
 * Frames are strings so there are no binary assets: they diff, they review, and they
 * scale to any size as SVG rects. Glyph meanings are in `frames.ts`; colours come from
 * `palette.ts`. The tests assert every frame is 16×16, uses only known glyphs, and keeps
 * its body pixel count within ±4 of its siblings — a dropped character in one row shifts
 * the whole face sideways, which is very easy to miss on a phone and very hard to unsee.
 *
 * Layout of the resting pose (row:col, zero-based):
 *   antenna bulb   rows 1–2, cols 7–8      body   rows 4–13, cols 4–11 (corners cut)
 *   antenna stem   row 3,    cols 7–8      eyes   rows 7–8, cols 5–6 and 9–10
 *   feet           row 14,   cols 5–6 and 9–10
 * Columns 0–3 and 12–15 are free for arms and props.
 */

import type { Frame } from './frames';

export type { Frame } from './frames';

export const SPRITE_STATES = [
  'idle',
  'blink',
  'building',
  'sleeping',
  'celebrating',
  'thinking',
  'waving',
] as const;

export type SpriteState = (typeof SPRITE_STATES)[number];

// ─── idle ────────────────────────────────────────────────────────────────────────────

const IDLE_0: Frame = [
  '................',
  '.......hh.......',
  '.......hh.......',
  '.......dd.......',
  '.....bbbbbb.....',
  '....bbbbbbbb....',
  '....bbbbbbbb....',
  '....bwebbweb....',
  '....beebbeeb....',
  '....bbbbbbbb....',
  '....bdbbbbdb....',
  '....bbddddbb....',
  '....bbbbbbbb....',
  '.....dddddd.....',
  '.....dd..dd.....',
  '................',
];

/** One pixel lower: the breath out. Whole-body so the pixel count is identical. */
const IDLE_1: Frame = [
  '................',
  '................',
  '.......hh.......',
  '.......hh.......',
  '.......dd.......',
  '.....bbbbbb.....',
  '....bbbbbbbb....',
  '....bbbbbbbb....',
  '....bwebbweb....',
  '....beebbeeb....',
  '....bbbbbbbb....',
  '....bdbbbbdb....',
  '....bbddddbb....',
  '....bbbbbbbb....',
  '.....dddddd.....',
  '.....dd..dd.....',
];

// ─── blink ───────────────────────────────────────────────────────────────────────────

const EYES_CLOSED: Frame = [
  '................',
  '.......hh.......',
  '.......hh.......',
  '.......dd.......',
  '.....bbbbbb.....',
  '....bbbbbbbb....',
  '....bbbbbbbb....',
  '....bbbbbbbb....',
  '....beebbeeb....',
  '....bbbbbbbb....',
  '....bdbbbbdb....',
  '....bbddddbb....',
  '....bbbbbbbb....',
  '.....dddddd.....',
  '.....dd..dd.....',
  '................',
];

// ─── building ────────────────────────────────────────────────────────────────────────
// Right arm (b) holds a hammer: handle (d), head (h). Arm + handle is always 4 pixels so
// the body count stays flat across the swing. Sparks are w.

/** Hammer raised overhead. */
const BUILD_0: Frame = [
  '................',
  '.......hh.......',
  '.......hh...hhh.',
  '.......dd...hhh.',
  '.....bbbbbb..d..',
  '....bbbbbbbb.d..',
  '....bbbbbbbb.b..',
  '....bwebbwebb...',
  '....beebbeeb....',
  '....bbbbbbbb....',
  '....bdbbbbdb....',
  '....bbddddbb....',
  '....bbbbbbbb....',
  '.....dddddd.....',
  '.....dd..dd.....',
  '................',
];

/** Mid-swing, hammer level. */
const BUILD_1: Frame = [
  '................',
  '.......hh.......',
  '.......hh.......',
  '.......dd.......',
  '.....bbbbbb.....',
  '....bbbbbbbb....',
  '....bbbbbbbb...h',
  '....bwebbwebbddh',
  '....beebbeebb..h',
  '....bbbbbbbb....',
  '....bdbbbbdb....',
  '....bbddddbb....',
  '....bbbbbbbb....',
  '.....dddddd.....',
  '.....dd..dd.....',
  '................',
];

/** Strike — hammer down, sparks. */
const BUILD_2: Frame = [
  '................',
  '.......hh.......',
  '.......hh.......',
  '.......dd.......',
  '.....bbbbbb.....',
  '....bbbbbbbb....',
  '....bbbbbbbb....',
  '....bwebbweb....',
  '....beebbeebb...',
  '....bbbbbbbb.b..',
  '....bdbbbbdb.dw.',
  '....bbddddbb.d.w',
  '....bbbbbbbbhhh.',
  '.....dddddd.hhhw',
  '.....dd..dd.....',
  '................',
];

/** Rest on the strike — one spark drifting up. */
const BUILD_3: Frame = [
  '................',
  '.......hh.......',
  '.......hh.......',
  '.......dd.......',
  '.....bbbbbb.....',
  '....bbbbbbbb....',
  '....bbbbbbbb....',
  '....bwebbweb....',
  '....beebbeebb..w',
  '....bbbbbbbb.b..',
  '....bdbbbbdb.d..',
  '....bbddddbb.d..',
  '....bbbbbbbbhhh.',
  '.....dddddd.hhh.',
  '.....dd..dd.....',
  '................',
];

// ─── sleeping ────────────────────────────────────────────────────────────────────────
// Slumped one pixel, eyes shut, antenna bulb dimmed to the dark body colour, and a trail
// of z's rising to the top-right that accumulates across the three frames.

const SLEEP_0: Frame = [
  '................',
  '................',
  '.......dd.......',
  '.......dd.......',
  '.......dd.......',
  '.....bbbbbb.....',
  '....bbbbbbbb....',
  '....bbbbbbbb....',
  '....bbbbbbbb.z..',
  '....beebbeeb....',
  '....bbbbbbbb....',
  '....bdbbbbdb....',
  '....bbddddbb....',
  '....bbbbbbbb....',
  '.....dddddd.....',
  '.....dd..dd.....',
];

const SLEEP_1: Frame = [
  '................',
  '................',
  '.......dd.......',
  '.......dd.......',
  '.......dd.......',
  '.....bbbbbb.....',
  '....bbbbbbbb..z.',
  '....bbbbbbbb....',
  '....bbbbbbbb.z..',
  '....beebbeeb....',
  '....bbbbbbbb....',
  '....bdbbbbdb....',
  '....bbddddbb....',
  '....bbbbbbbb....',
  '.....dddddd.....',
  '.....dd..dd.....',
];

const SLEEP_2: Frame = [
  '................',
  '.............zzz',
  '.......dd.....z.',
  '.......dd....zzz',
  '.......dd.......',
  '.....bbbbbb.....',
  '....bbbbbbbb..z.',
  '....bbbbbbbb....',
  '....bbbbbbbb.z..',
  '....beebbeeb....',
  '....bbbbbbbb....',
  '....bdbbbbdb....',
  '....bbddddbb....',
  '....bbbbbbbb....',
  '.....dddddd.....',
  '.....dd..dd.....',
];

// ─── celebrating ─────────────────────────────────────────────────────────────────────
// Both arms up (3 pixels each), mouth open, confetti in teal / bone / dim. The middle
// frame hops one pixel and throws the arms straight up.

const CHEER_0: Frame = [
  '..w.........h...',
  'h......hh......w',
  '....z..hh..z....',
  'w......dd.....h.',
  '..h..bbbbbb..z..',
  '..b.bbbbbbbb.b..',
  '..b.bbbbbbbb.b..',
  '...bbwebbwebb...',
  '....beebbeeb....',
  '....bbbbbbbb....',
  '....bbddddbb....',
  '....bbddddbb....',
  '....bbbbbbbb....',
  '.....dddddd.....',
  '.....dd..dd.....',
  '................',
];

const CHEER_1: Frame = [
  '....w..hh...h...',
  '.h.....hh......w',
  '..h....dd....w..',
  'z....bbbbbb....h',
  'w..bbbbbbbbbb..h',
  '...bbbbbbbbbb.z.',
  '...bbwebbwebb...',
  '....beebbeeb....',
  '....bbbbbbbb....',
  '....bbddddbb....',
  '....bbddddbb....',
  '....bbbbbbbb....',
  '.....dddddd.....',
  '.....dd..dd.....',
  '................',
  '................',
];

const CHEER_2: Frame = [
  '................',
  '...w...hh...z...',
  'h......hh......h',
  '.......dd.......',
  'w....bbbbbb....w',
  '..b.bbbbbbbb.b..',
  '..b.bbbbbbbb.b..',
  '...bbwebbwebb...',
  '.h..beebbeeb..z.',
  '....bbbbbbbb....',
  'z...bbddddbb...h',
  '....bbddddbb....',
  '..w.bbbbbbbb.w..',
  '.....dddddd.....',
  '.....dd..dd.....',
  '................',
];

// ─── thinking ────────────────────────────────────────────────────────────────────────
// Eyes glance up-right toward a thought bubble; the three dots light up in turn.

const THINK_0: Frame = [
  '................',
  '.......hh..w.z.z',
  '.......hh.......',
  '.......dd....z..',
  '.....bbbbbb.....',
  '....bbbbbbbb.z..',
  '....bbbbbbbb....',
  '....bewbbewb....',
  '....beebbeeb....',
  '....bbbbbbbb....',
  '....bdbbbbdb....',
  '....bbddddbb....',
  '....bbbbbbbb....',
  '.....dddddd.....',
  '.....dd..dd.....',
  '................',
];

const THINK_1: Frame = [
  '................',
  '.......hh..z.w.z',
  '.......hh.......',
  '.......dd....z..',
  '.....bbbbbb.....',
  '....bbbbbbbb.z..',
  '....bbbbbbbb....',
  '....bewbbewb....',
  '....beebbeeb....',
  '....bbbbbbbb....',
  '....bdbbbbdb....',
  '....bbddddbb....',
  '....bbbbbbbb....',
  '.....dddddd.....',
  '.....dd..dd.....',
  '................',
];

const THINK_2: Frame = [
  '................',
  '.......hh..z.z.w',
  '.......hh.......',
  '.......dd....z..',
  '.....bbbbbb.....',
  '....bbbbbbbb.z..',
  '....bbbbbbbb....',
  '....bewbbewb....',
  '....beebbeeb....',
  '....bbbbbbbb....',
  '....bdbbbbdb....',
  '....bbddddbb....',
  '....bbbbbbbb....',
  '.....dddddd.....',
  '.....dd..dd.....',
  '................',
];

// ─── waving ──────────────────────────────────────────────────────────────────────────
// Right arm up (4 pixels), swinging between straight up and flung out.

const WAVE_0: Frame = [
  '................',
  '.......hh.......',
  '.......hh.......',
  '.......dd.......',
  '.....bbbbbb..b..',
  '....bbbbbbbb.b..',
  '....bbbbbbbb.b..',
  '....bwebbwebb...',
  '....beebbeeb....',
  '....bbbbbbbb....',
  '....bdbbbbdb....',
  '....bbddddbb....',
  '....bbbbbbbb....',
  '.....dddddd.....',
  '.....dd..dd.....',
  '................',
];

const WAVE_1: Frame = [
  '................',
  '.......hh.......',
  '.......hh.......',
  '.......dd.......',
  '.....bbbbbb....b',
  '....bbbbbbbb..b.',
  '....bbbbbbbb.b..',
  '....bwebbwebb...',
  '....beebbeeb....',
  '....bbbbbbbb....',
  '....bdbbbbdb....',
  '....bbddddbb....',
  '....bbbbbbbb....',
  '.....dddddd.....',
  '.....dd..dd.....',
  '................',
];

// ─── table ───────────────────────────────────────────────────────────────────────────

export const SPRITES: Record<SpriteState, Frame[]> = {
  idle: [IDLE_0, IDLE_1],
  blink: [IDLE_0, IDLE_1, EYES_CLOSED],
  building: [BUILD_0, BUILD_1, BUILD_2, BUILD_3],
  sleeping: [SLEEP_0, SLEEP_1, SLEEP_2],
  celebrating: [CHEER_0, CHEER_1, CHEER_2],
  thinking: [THINK_0, THINK_1, THINK_2],
  waving: [WAVE_0, WAVE_1],
};

export function framesFor(state: SpriteState): Frame[] {
  return SPRITES[state];
}
