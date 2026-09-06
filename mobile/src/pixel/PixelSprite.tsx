import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  AccessibilityInfo,
  Animated,
  Easing,
  Platform,
  View,
  type StyleProp,
  type ViewStyle,
} from 'react-native';
import Svg, { Rect } from 'react-native-svg';

import type { Scheme } from '../theme';
import { EMPTY, GRID, runsFor, type Frame } from './frames';
import {
  CUT,
  MOTION,
  blinkGapMs,
  blinks,
  breathPeriodMs,
  clampTempo,
  closeEyes,
  crossfadeFor,
  decompose,
  gestureGapMs,
  gestures,
  glanceAside,
  initLayers,
  layerFrames,
  pickGesture,
  settleFor,
  staggered,
  stepLayers,
  timelineFor,
  type EasingName,
  type Fade,
  type Layers,
  type Overlay,
} from './motion';
import { spritePalette, type SpritePalette } from './palette';
import { SPRITES, type SpriteState } from './sprites';

export { spritePalette } from './palette';
export { SPRITE_STATES, type SpriteState } from './sprites';
export { MOTION } from './motion';

interface SpriteProps {
  state: SpriteState;
  /** Requested box size in points. Rendered at the largest whole-pixel scale that fits. */
  size?: number;
  /**
   * Accepted for compatibility and no longer used: every state now runs on the per-state
   * clocks in `motion.ts` (a hammer beat is 380 ms because that is a hammer beat, not
   * because a caller asked for 4 fps). Speed is `tempo`.
   */
  fps?: number;
  scheme?: Scheme;
  paused?: boolean;
  /** 0.5–2: shortens every beat and breath. A live card can hand in recent activity. */
  tempo?: number;
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
 * Bit, alive.
 *
 * Reduced motion (or `paused`) renders the state's first frame, still — `paused` is OR-ed
 * with the OS setting, so a caller can stop a sprite but can never force one to move
 * against it. Otherwise `LiveSprite` runs the motion in `motion.ts`.
 */
export function PixelSprite({
  state,
  size = 64,
  scheme = 'dark',
  paused = false,
  tempo,
  style,
}: SpriteProps) {
  const reduced = useReducedMotion();
  if (paused || reduced) {
    return <FrameView frame={SPRITES[state][0]!} size={size} scheme={scheme} style={style} />;
  }
  return <LiveSprite state={state} size={size} scheme={scheme} tempo={tempo} style={style} />;
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

// ─── the runtime ─────────────────────────────────────────────────────────────────────

/**
 * Web has no native animated module, and react-native-web warns once per animation when
 * asked for it. Every property animated here — opacity and transforms — is on the native
 * allowlist, so on device it all runs off the JS thread.
 */
const NATIVE = Platform.OS !== 'web';

function easing(name: EasingName): (t: number) => number {
  switch (name) {
    case 'linear':
      return Easing.linear;
    case 'inOut':
      return Easing.inOut(Easing.ease);
    case 'out':
      return Easing.out(Easing.cubic);
    case 'outBack':
      return Easing.out(Easing.back(MOTION.celebrating.backS));
  }
}

function timing(value: Animated.Value, toValue: number, duration: number, ease: EasingName) {
  return Animated.timing(value, { toValue, duration, easing: easing(ease), useNativeDriver: NATIVE });
}

/**
 * One `Animated.Value` per overlay, recreated when the state's decomposition changes.
 * Kept outside the engine effect so a re-render never resets a spark mid-fade.
 */
function useOverlayValues(overlays: Overlay[]): Animated.Value[] {
  return useMemo(() => overlays.map(() => new Animated.Value(0)), [overlays]);
}

function LiveSprite({
  state,
  size,
  scheme,
  tempo,
  style,
}: {
  state: SpriteState;
  size: number;
  scheme: Scheme;
  tempo: number | undefined;
  style?: StyleProp<ViewStyle>;
}) {
  const px = Math.max(1, Math.floor(size / GRID));
  const drawn = px * GRID;
  const palette = useMemo(() => spritePalette(scheme), [scheme]);
  const rate = clampTempo(tempo);
  const { base, overlays } = decompose(state);
  const overlayValues = useOverlayValues(overlays);

  // Whole-sprite transforms. `breath` and `settleScale` multiply; `bodyY` (impact) and
  // `sag` (the sleeping breath) add. All native-driver-safe.
  const breath = useRef(new Animated.Value(1)).current;
  const settleScale = useRef(new Animated.Value(MOTION.settle.fromScale)).current;
  const settleOpacity = useRef(new Animated.Value(0)).current;
  const bodyY = useRef(new Animated.Value(0)).current;
  const sag = useRef(new Animated.Value(0)).current;
  const tilt = useRef(new Animated.Value(0)).current;
  const layerA = useRef(new Animated.Value(1)).current;
  const layerB = useRef(new Animated.Value(0)).current;

  const [layers, setLayers] = useState<Layers<Frame>>(() => initLayers(base[0]!, Date.now()));

  // A frame change (stepped by the engine below) fades the front layer in and the back
  // layer out. The layers hold only the pixels that DIFFER between the two frames; what
  // they share sits on an always-opaque layer beneath, so the body never dims mid-fade
  // (see `splitFrames`). The pixels never move: each layer is a crisp SVG whose alpha is
  // what animates. After a completed fade the front is at 1 and the back at 0, which is
  // exactly where the next step's outgoing and incoming layers need to start.
  const drawn3 = useMemo(() => layerFrames(layers), [layers]);
  useEffect(() => {
    const [front, back] = layers.front === 0 ? [layerA, layerB] : [layerB, layerA];
    if (layers.fade.ms <= 0) {
      front.setValue(1);
      back.setValue(0);
      return;
    }
    const anim = Animated.parallel([
      timing(front, 1, layers.fade.ms, layers.fade.easing),
      timing(back, 0, layers.fade.ms, layers.fade.easing),
    ]);
    anim.start();
    return () => anim.stop();
  }, [layers, layerA, layerB]);

  // The engine: everything that happens over time in one state. Torn down and rebuilt
  // whenever the state or tempo changes, so nothing from the old state leaks into the new.
  useEffect(() => {
    const timers = new Set<ReturnType<typeof setTimeout>>();
    // A Set, and one-shots remove themselves on completion: the building state fires
    // two per beat for as long as a live row is on screen — thousands an hour, all
    // retained and all iterated at every cleanup under the old array. Found by review.
    const anims = new Set<Animated.CompositeAnimation>();
    const after = (ms: number, fn: () => void) => {
      const id = setTimeout(() => {
        timers.delete(id);
        fn();
      }, ms);
      timers.add(id);
    };
    const run = (anim: Animated.CompositeAnimation) => {
      anims.add(anim);
      anim.start(({ finished }) => {
        if (finished) anims.delete(anim);
      });
    };

    // Derived frames are memoised per source frame so the layer scheduler, which compares
    // by identity, sees a blink as one change and its end as one change.
    const closed = new Map<Frame, Frame>();
    const aside = new Map<Frame, Frame>();
    const derive = (cache: Map<Frame, Frame>, f: Frame, fn: (f: Frame) => Frame) => {
      let d = cache.get(f);
      if (!d) {
        d = fn(f);
        cache.set(f, d);
      }
      return d;
    };

    let beatFrame = base[0]!;
    let eyesShut = false;
    let glancing = false;
    const show = (fade: Fade) => {
      let f = glancing ? derive(aside, beatFrame, glanceAside) : beatFrame;
      if (eyesShut) f = derive(closed, f, closeEyes);
      setLayers((l) => stepLayers(l, f, fade, Date.now()));
    };

    // 1. Entrance: opacity 0 → 1 with scale fromScale → 1. 220 ms ease-out, or the 600 ms
    //    ease-out-back rise for a celebration.
    const settle = settleFor(state);
    setLayers(initLayers(base[0]!, Date.now()));
    settleOpacity.setValue(0);
    settleScale.setValue(settle.fromScale);
    bodyY.setValue(0);
    sag.setValue(0);
    tilt.setValue(0);
    run(
      Animated.parallel([
        timing(settleOpacity, 1, settle.ms, 'out'),
        timing(settleScale, 1, settle.ms, settle.easing),
      ])
    );

    // 2. Breath: 1 → 1.03 → 1, ease-in-out, on the state's period. Sleeping also sags one
    //    pixel at the bottom of each breath.
    const period = breathPeriodMs(state, rate);
    breath.setValue(1);
    const inhale = [timing(breath, MOTION.breath.scale, period / 2, 'inOut'), timing(breath, 1, period / 2, 'inOut')];
    if (state === 'sleeping') {
      const drop = MOTION.sleeping.settleCells * px;
      run(
        Animated.loop(
          Animated.parallel([
            Animated.sequence(inhale),
            Animated.sequence([timing(sag, drop, period / 2, 'inOut'), timing(sag, 0, period / 2, 'inOut')]),
          ])
        )
      );
    } else {
      run(Animated.loop(Animated.sequence(inhale)));
    }

    // 3. Beats: the frame timeline, cross-faded. The strike beat also drops the body one
    //    pixel (squash, no stretch) and fires that beat's sparks.
    const timeline = timelineFor(state, rate);
    const fade = crossfadeFor(state);
    const onBeat = (frame: number) => {
      if (state === 'building' && frame === MOTION.building.strikeBeat) {
        const b = MOTION.building;
        bodyY.setValue(0);
        run(Animated.sequence([timing(bodyY, b.impactCells * px, b.impactDownMs, 'linear'), timing(bodyY, 0, b.impactUpMs, 'out')]));
      }
      overlays.forEach((o, k) => {
        if (o.kind !== 'spark' || o.beat !== frame) return;
        const v = overlayValues[k]!;
        v.setValue(1);
        run(timing(v, 0, MOTION.building.sparkFadeMs, 'out'));
      });
    };
    if (timeline.length > 1) {
      let i = 0;
      const tick = () => {
        const beat = timeline[i]!;
        beatFrame = base[beat.frame]!;
        show(fade);
        onBeat(beat.frame);
        i = (i + 1) % timeline.length;
        after(beat.ms, tick);
      };
      tick();
    }

    // 4. Blinks: a two-frame cut, 120 ms shut, on a random 3–6 s gap.
    if (blinks(state)) {
      const blink = () =>
        after(blinkGapMs(), () => {
          eyesShut = true;
          show(CUT);
          after(MOTION.blink.closedMs, () => {
            eyesShut = false;
            show(CUT);
            blink();
          });
        });
      blink();
    }

    // 5. Idle micro-gestures every 15–40 s: a 2.5° tilt and back, or a glance aside.
    if (gestures(state)) {
      const g = MOTION.gesture;
      const gesture = () =>
        after(gestureGapMs(), () => {
          if (pickGesture() === 'tilt') {
            run(Animated.sequence([timing(tilt, g.tiltDeg, g.tiltMs / 2, 'inOut'), timing(tilt, 0, g.tiltMs / 2, 'inOut')]));
            gesture();
          } else {
            glancing = true;
            show(fade);
            after(g.asideMs, () => {
              glancing = false;
              show(fade);
              gesture();
            });
          }
        });
      gesture();
    }

    // 6. Continuous overlays. Each is a 0 → 1 progress the render maps to its motion.
    overlays.forEach((o, k) => {
      const v = overlayValues[k]!;
      v.setValue(0);
      switch (o.kind) {
        case 'dot': {
          // 0 → 1, then 1 → 0.3 → 1 forever; 150 ms apart; 1.2 s per loop.
          const half = MOTION.thinking.dotLoopMs / 2;
          run(
            Animated.sequence([
              Animated.delay(staggered(3, MOTION.thinking.dotStaggerMs)[o.index] ?? 0),
              timing(v, 1, half, 'inOut'),
              Animated.loop(Animated.sequence([timing(v, MOTION.thinking.dotLow, half, 'inOut'), timing(v, 1, half, 'inOut')])),
            ])
          );
          break;
        }
        case 'z':
          run(
            Animated.sequence([
              Animated.delay(o.index * MOTION.sleeping.zStaggerMs),
              Animated.loop(timing(v, 1, MOTION.sleeping.zRiseMs, 'out'), { resetBeforeIteration: true }),
            ])
          );
          break;
        case 'confetti':
          run(
            Animated.sequence([
              Animated.delay(o.index * MOTION.celebrating.confettiStaggerMs),
              Animated.loop(timing(v, 1, MOTION.celebrating.confettiMs, 'linear'), { resetBeforeIteration: true }),
            ])
          );
          break;
        case 'spark':
          break; // fired by its beat
      }
    });

    return () => {
      for (const id of timers) clearTimeout(id);
      for (const a of anims) a.stop();
    };
  }, [state, rate, base, overlays, overlayValues, px, breath, settleScale, settleOpacity, bodyY, sag, tilt]);

  /*
   * The breathing scale sits on the View that WRAPS the Svg, never on the Rects. At 1.03,
   * a 64 pt sprite grows by under two points — sub-pixel by design. That is why it reads
   * as breathing rather than resizing, and why the pixel edges inside stay where the
   * integer-scale rule put them.
   */
  const transform = [
    { translateY: Animated.add(bodyY, sag) },
    { scale: Animated.multiply(breath, settleScale) },
    { rotate: tilt.interpolate({ inputRange: [0, 360], outputRange: ['0deg', '360deg'] }) },
  ];

  return (
    <View
      style={[{ width: size, height: size, alignItems: 'center', justifyContent: 'center' }, style]}
      accessibilityElementsHidden
      importantForAccessibility="no-hide-descendants"
    >
      <Animated.View style={{ width: drawn, height: drawn, opacity: settleOpacity, transform }}>
        <View style={layer}>
          <FrameSvg frame={drawn3.shared} drawn={drawn} palette={palette} />
        </View>
        <Animated.View style={[layer, { opacity: layerA }]}>
          <FrameSvg frame={drawn3.a} drawn={drawn} palette={palette} />
        </Animated.View>
        <Animated.View style={[layer, { opacity: layerB }]}>
          <FrameSvg frame={drawn3.b} drawn={drawn} palette={palette} />
        </Animated.View>
        {overlays.map((o, k) => (
          <OverlayView key={`${state}-${k}`} overlay={o} value={overlayValues[k]!} px={px} palette={palette} />
        ))}
      </Animated.View>
    </View>
  );
}

const layer = { position: 'absolute', left: 0, top: 0 } as const;

/**
 * One animated group of pixels — a dot, a z glyph, a spark burst, a piece of confetti —
 * drawn as plain integer-sized Views so it is exactly as crisp as the SVG beneath it. The
 * group's progress value maps to alpha and a vertical drift by kind.
 */
function OverlayView({
  overlay,
  value,
  px,
  palette,
}: {
  overlay: Overlay;
  value: Animated.Value;
  px: number;
  palette: SpritePalette;
}) {
  const cells = useMemo(() => cellsFor(overlay.frame, palette), [overlay.frame, palette]);
  const animated = useMemo(() => {
    switch (overlay.kind) {
      case 'dot':
      case 'spark':
        return { opacity: value };
      case 'z':
        // Launches four cells below where it is drawn, fades in over the first tenth,
        // rises into place while dissolving.
        return {
          opacity: value.interpolate({ inputRange: [0, 0.1, 0.55, 1], outputRange: [0, 1, 1, 0] }),
          transform: [{ translateY: value.interpolate({ inputRange: [0, 1], outputRange: [MOTION.sleeping.zRiseCells * px, 0] }) }],
        };
      case 'confetti':
        return {
          opacity: value.interpolate({ inputRange: [0, 0.1, 0.7, 1], outputRange: [0, 1, 1, 0] }),
          transform: [{ translateY: value.interpolate({ inputRange: [0, 1], outputRange: [0, MOTION.celebrating.confettiFallCells * px] }) }],
        };
    }
  }, [overlay.kind, value, px]);

  return (
    <Animated.View style={[layer, { width: px * GRID, height: px * GRID, pointerEvents: 'none' }, animated]}>
      {cells.map((c, i) => (
        <View
          key={i}
          style={{ position: 'absolute', left: c.x * px, top: c.y * px, width: c.w * px, height: px, backgroundColor: c.fill }}
        />
      ))}
    </Animated.View>
  );
}

// ─── drawing ─────────────────────────────────────────────────────────────────────────

interface Cell {
  x: number;
  y: number;
  w: number;
  fill: string;
}

/**
 * One Rect per run of identical non-transparent pixels in a row.
 *
 * The palette is any glyph → colour map, not `SpritePalette` specifically: the animal
 * pack draws the same frames with a two-role palette (`animalPalette`). A glyph the
 * palette does not name is SKIPPED rather than drawn in a fallback colour — a sprite with
 * a hole in it is a bug someone reports; a sprite with a stray magenta pixel is one
 * someone ships.
 */
export function cellsFor(frame: Frame, palette: Record<string, string | undefined>): Cell[] {
  const cells: Cell[] = [];
  frame.forEach((row, y) => {
    for (const run of runsFor(row)) {
      if (run.ch === EMPTY) continue;
      const fill = palette[run.ch];
      if (!fill) continue;
      cells.push({ x: run.start, y, w: run.length, fill });
    }
  });
  return cells;
}

/**
 * The SVG of one frame at a whole-point scale. A fractional scale puts rect edges between
 * device pixels and anti-aliasing draws hairline seams between every pixel of the body;
 * at whole-point scales there is nothing to anti-alias.
 */
export function FrameSvg({
  frame,
  drawn,
  palette,
}: {
  frame: Frame;
  drawn: number;
  palette: Record<string, string | undefined>;
}) {
  const cells = useMemo(() => cellsFor(frame, palette), [frame, palette]);
  return (
    <Svg width={drawn} height={drawn} viewBox={`0 0 ${GRID} ${GRID}`}>
      {cells.map((c, i) => (
        <Rect key={i} x={c.x} y={c.y} width={c.w} height={1} fill={c.fill} />
      ))}
    </Svg>
  );
}

/**
 * A still frame, centred in the requested box.
 *
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
  const palette = useMemo(() => spritePalette(scheme), [scheme]);
  return (
    <View
      style={[{ width: size, height: size, alignItems: 'center', justifyContent: 'center' }, style]}
      accessibilityElementsHidden
      importantForAccessibility="no-hide-descendants"
    >
      <FrameSvg frame={frame} drawn={px * GRID} palette={palette} />
    </View>
  );
}
