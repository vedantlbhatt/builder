import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Animated, Easing, Platform, View, type StyleProp, type ViewStyle } from 'react-native';

import type { Scheme } from '../theme';
import { ANIMAL_FRAMES, ANIMAL_LABELS, type Animal } from './animals';
import { GRID, type Frame } from './frames';
import {
  ANIMAL_MOTION,
  MOTION,
  animalBreathMs,
  animalTimeline,
  clampTempo,
  initLayers,
  layerFrames,
  stepLayers,
  type Layers,
} from './motion';
import { animalPalette, type AnimalPalette } from './palette';
import { FrameSvg, useReducedMotion } from './PixelSprite';

export { ANIMALS, ANIMAL_LABELS, animalChoices, animalForArchetype, resolveAnimal, type Animal } from './animals';
export { animalPalette } from './palette';

interface AnimalProps {
  animal: Animal;
  /** Requested box size in points. Rendered at the largest whole-pixel scale that fits. */
  size?: number;
  scheme?: Scheme;
  paused?: boolean;
  /** 0.5–2, as `PixelSprite`: shortens every beat and the breath. */
  tempo?: number;
  style?: StyleProp<ViewStyle>;
}

/**
 * One of the eight animals, alive.
 *
 * The prop shape is `PixelSprite`'s on purpose — a screen swapping the mascot for an
 * animal should change the tag and the one prop that names the creature, nothing else.
 * The motion is smaller than the mascot's by design: a frame loop, a breath, and at most
 * one 1–2 pixel drift (`ANIMAL_MOTION`). No blinks-on-a-random-gap, no micro-gestures,
 * no overlays — an animal is a companion in the corner of a card, not the subject.
 *
 * `paused` is OR-ed with the OS reduce-motion setting exactly as the mascot does: a
 * caller can stop an animal, and can never start one against that setting.
 */
export function PixelAnimal({ animal, size = 64, scheme = 'dark', paused = false, tempo, style }: AnimalProps) {
  const reduced = useReducedMotion();
  if (paused || reduced) {
    return <AnimalFrameView animal={animal} frame={ANIMAL_FRAMES[animal][0]!} size={size} scheme={scheme} style={style} />;
  }
  return <LiveAnimal animal={animal} size={size} scheme={scheme} tempo={tempo} style={style} />;
}

/** The first frame, static. For list rows, pickers, and anywhere motion would be noise. */
export function PixelAnimalIcon({
  animal,
  size = 24,
  scheme = 'dark',
  style,
}: {
  animal: Animal;
  size?: number;
  scheme?: Scheme;
  style?: StyleProp<ViewStyle>;
}) {
  return <AnimalFrameView animal={animal} frame={ANIMAL_FRAMES[animal][0]!} size={size} scheme={scheme} style={style} />;
}

// ─── the runtime ─────────────────────────────────────────────────────────────────────

const NATIVE = Platform.OS !== 'web';

function timing(value: Animated.Value, toValue: number, duration: number, ease: 'linear' | 'inOut' | 'out') {
  const easing = ease === 'linear' ? Easing.linear : ease === 'out' ? Easing.out(Easing.cubic) : Easing.inOut(Easing.ease);
  return Animated.timing(value, { toValue, duration, easing, useNativeDriver: NATIVE });
}

function LiveAnimal({
  animal,
  size,
  scheme,
  tempo,
  style,
}: {
  animal: Animal;
  size: number;
  scheme: Scheme;
  tempo: number | undefined;
  style?: StyleProp<ViewStyle>;
}) {
  const px = Math.max(1, Math.floor(size / GRID));
  const drawn = px * GRID;
  const palette = useMemo(() => animalPalette(animal, scheme), [animal, scheme]);
  const rate = clampTempo(tempo);
  const frames = ANIMAL_FRAMES[animal];

  const breath = useRef(new Animated.Value(1)).current;
  const settleScale = useRef(new Animated.Value(MOTION.settle.fromScale)).current;
  const settleOpacity = useRef(new Animated.Value(0)).current;
  const drift = useRef(new Animated.Value(0)).current;
  const layerA = useRef(new Animated.Value(1)).current;
  const layerB = useRef(new Animated.Value(0)).current;

  const [layers, setLayers] = useState<Layers<Frame>>(() => initLayers(frames[0]!, Date.now()));

  // Same three-layer cross-fade as the mascot: the pixels the two frames AGREE on are
  // drawn once and opaque, and only the difference fades, so a wagging tail never dims
  // the dog (`splitFrames`).
  const drawn3 = useMemo(() => layerFrames(layers), [layers]);
  useEffect(() => {
    const [front, back] = layers.front === 0 ? [layerA, layerB] : [layerB, layerA];
    if (layers.fade.ms <= 0) {
      front.setValue(1);
      back.setValue(0);
      return;
    }
    const anim = Animated.parallel([
      timing(front, 1, layers.fade.ms, layers.fade.easing === 'inOut' ? 'inOut' : 'linear'),
      timing(back, 0, layers.fade.ms, layers.fade.easing === 'inOut' ? 'inOut' : 'linear'),
    ]);
    anim.start();
    return () => anim.stop();
  }, [layers, layerA, layerB]);

  useEffect(() => {
    const timers = new Set<ReturnType<typeof setTimeout>>();
    const anims = new Set<Animated.CompositeAnimation>();
    const run = (anim: Animated.CompositeAnimation) => {
      anims.add(anim);
      anim.start(({ finished }) => {
        if (finished) anims.delete(anim);
      });
    };
    const after = (ms: number, fn: () => void) => {
      const id = setTimeout(() => {
        timers.delete(id);
        fn();
      }, ms);
      timers.add(id);
    };

    // 1. Entrance.
    setLayers(initLayers(frames[0]!, Date.now()));
    settleOpacity.setValue(0);
    settleScale.setValue(MOTION.settle.fromScale);
    drift.setValue(0);
    run(
      Animated.parallel([
        timing(settleOpacity, 1, MOTION.settle.ms, 'out'),
        timing(settleScale, 1, MOTION.settle.ms, 'out'),
      ])
    );

    // 2. Breath.
    const period = animalBreathMs(animal, rate);
    breath.setValue(1);
    run(
      Animated.loop(
        Animated.sequence([
          timing(breath, MOTION.breath.scale, period / 2, 'inOut'),
          timing(breath, 1, period / 2, 'inOut'),
        ])
      )
    );

    // 3. The drift: −cells → +cells → −cells, ease-in-out, in sprite pixels. Started at
    //    the negative end rather than at zero so the first half-period is a full sweep.
    const d = ANIMAL_MOTION[animal].drift;
    if (d) {
      const amp = d.cells * px;
      const half = Math.round(d.periodMs / rate / 2);
      drift.setValue(-amp);
      run(
        Animated.loop(
          Animated.sequence([timing(drift, amp, half, 'inOut'), timing(drift, -amp, half, 'inOut')])
        )
      );
    }

    // 4. The frame loop.
    const timeline = animalTimeline(animal, rate);
    if (timeline.length > 1) {
      let i = 0;
      const tick = () => {
        const beat = timeline[i]!;
        setLayers((l) => stepLayers(l, frames[beat.frame]!, MOTION.crossfade, Date.now()));
        i = (i + 1) % timeline.length;
        after(beat.ms, tick);
      };
      tick();
    }

    return () => {
      for (const id of timers) clearTimeout(id);
      for (const a of anims) a.stop();
    };
  }, [animal, rate, frames, px, breath, settleScale, settleOpacity, drift]);

  const moving = ANIMAL_MOTION[animal].drift;
  const transform = [
    moving?.axis === 'x' ? { translateX: drift } : { translateY: moving ? drift : 0 },
    { scale: Animated.multiply(breath, settleScale) },
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
      </Animated.View>
    </View>
  );
}

const layer = { position: 'absolute', left: 0, top: 0 } as const;

/**
 * A still animal, centred in the requested box.
 *
 * Labelled rather than hidden: unlike the mascot, which never says anything the caption
 * beside it does not, a person's chosen animal IS the information in a picker row.
 */
function AnimalFrameView({
  animal,
  frame,
  size,
  scheme,
  style,
}: {
  animal: Animal;
  frame: Frame;
  size: number;
  scheme: Scheme;
  style?: StyleProp<ViewStyle>;
}) {
  const px = Math.max(1, Math.floor(size / GRID));
  const palette: AnimalPalette = useMemo(() => animalPalette(animal, scheme), [animal, scheme]);
  return (
    <View
      style={[{ width: size, height: size, alignItems: 'center', justifyContent: 'center' }, style]}
      accessibilityRole="image"
      accessibilityLabel={ANIMAL_LABELS[animal]}
    >
      <FrameSvg frame={frame} drawn={px * GRID} palette={palette} />
    </View>
  );
}
