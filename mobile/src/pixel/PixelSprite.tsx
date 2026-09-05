import React, { useEffect, useMemo, useState } from 'react';
import { AccessibilityInfo, View, type StyleProp, type ViewStyle } from 'react-native';
import Svg, { Rect } from 'react-native-svg';

import type { Scheme } from '../theme';
import { EMPTY, GRID, runsFor, type Frame } from './frames';
import { spritePalette, type SpritePalette } from './palette';
import { SPRITES, type SpriteState } from './sprites';

export { spritePalette } from './palette';
export { SPRITE_STATES, type SpriteState } from './sprites';

interface SpriteProps {
  state: SpriteState;
  /** Requested box size in points. Rendered at the largest whole-pixel scale that fits. */
  size?: number;
  fps?: number;
  scheme?: Scheme;
  paused?: boolean;
  style?: StyleProp<ViewStyle>;
}

/**
 * Whether the person has asked the OS for less motion.
 *
 * `false` until the first async answer arrives, so the first frame can never flash an
 * animation the person opted out of for longer than one interval tick — and the value
 * follows the system toggle afterwards rather than being read once at launch.
 */
export function useReducedMotion(): boolean {
  const [reduced, setReduced] = useState(false);
  useEffect(() => {
    let live = true;
    AccessibilityInfo.isReduceMotionEnabled()
      .then((v) => {
        if (live) setReduced(v);
      })
      .catch(() => {});
    const sub = AccessibilityInfo.addEventListener('reduceMotionChanged', setReduced);
    return () => {
      live = false;
      sub.remove();
    };
  }, []);
  return reduced;
}

/**
 * Bit, animated.
 *
 * Frames advance on a `setInterval` that is cleared on unmount, on pause, and whenever
 * `state` or `fps` changes — the frame index resets with the state so a state change
 * never starts mid-hammer-swing.
 *
 * Reduced motion pauses every sprite on the first frame; `paused` is OR-ed with it, so a
 * caller can stop a sprite but can never force one to move against the OS setting.
 *
 * Scale is snapped to a whole number of points per sprite pixel and the SVG is centred
 * in the requested box. A fractional scale puts rect edges between device pixels and
 * anti-aliasing draws hairline seams between every pixel of the body; at whole-point
 * scales there is nothing to anti-alias.
 */
export function PixelSprite({
  state,
  size = 64,
  fps = 4,
  scheme = 'dark',
  paused = false,
  style,
}: SpriteProps) {
  const frames = SPRITES[state];
  const [index, setIndex] = useState(0);
  const reduced = useReducedMotion();
  const still = paused || reduced;

  useEffect(() => {
    setIndex(0);
  }, [state, still]);

  useEffect(() => {
    if (still || frames.length < 2 || fps <= 0) return;
    const id = setInterval(() => {
      setIndex((i) => (i + 1) % frames.length);
    }, 1000 / fps);
    return () => clearInterval(id);
  }, [still, fps, frames]);

  const frame = frames[index % frames.length] ?? frames[0]!;
  return <FrameView frame={frame} size={size} scheme={scheme} style={style} />;
}

/** The first frame of a state, static. For list rows and anywhere motion would be noise. */
export function PixelIcon({
  state,
  size = 24,
  scheme = 'dark',
  style,
}: {
  state: SpriteState;
  size?: number;
  scheme?: Scheme;
  style?: StyleProp<ViewStyle>;
}) {
  return <FrameView frame={SPRITES[state][0]!} size={size} scheme={scheme} style={style} />;
}

interface Cell {
  x: number;
  y: number;
  w: number;
  fill: string;
}

/** One Rect per run of identical non-transparent pixels in a row. */
export function cellsFor(frame: Frame, palette: SpritePalette): Cell[] {
  const cells: Cell[] = [];
  frame.forEach((row, y) => {
    for (const run of runsFor(row)) {
      if (run.ch === EMPTY) continue;
      const fill = palette[run.ch as keyof SpritePalette];
      if (!fill) continue;
      cells.push({ x: run.start, y, w: run.length, fill });
    }
  });
  return cells;
}

/**
 * Decorative: the mascot never carries information the text beside it does not. Hidden
 * from VoiceOver and TalkBack so a screen reader does not announce "image" between every
 * caption and its number.
 */
function FrameView({
  frame,
  size,
  scheme,
  style,
}: {
  frame: Frame;
  size: number;
  scheme: Scheme;
  style?: StyleProp<ViewStyle>;
}) {
  const px = Math.max(1, Math.floor(size / GRID));
  const drawn = px * GRID;
  const palette = useMemo(() => spritePalette(scheme), [scheme]);
  const cells = useMemo(() => cellsFor(frame, palette), [frame, palette]);

  return (
    <View
      style={[{ width: size, height: size, alignItems: 'center', justifyContent: 'center' }, style]}
      accessibilityElementsHidden
      importantForAccessibility="no-hide-descendants"
    >
      <Svg width={drawn} height={drawn} viewBox={`0 0 ${GRID} ${GRID}`}>
        {cells.map((c, i) => (
          <Rect key={i} x={c.x} y={c.y} width={c.w} height={1} fill={c.fill} />
        ))}
      </Svg>
    </View>
  );
}
