import { useRouter } from 'expo-router';
import React, { useEffect, useState } from 'react';
import { Pressable, Text, View } from 'react-native';

import * as cache from '../src/data/cache';
import { PixelAnimal, PixelAnimalIcon } from '../src/pixel/PixelAnimal';
import { ANIMALS, type Animal } from '../src/pixel/animals';
import { openOn, step, view } from '../src/pixel/carousel';
import { colors, space } from '../src/theme';

const c = colors('dark');

/** Where the chosen creature lives. Local: it is a preference, not a fact about the work. */
export const ANIMAL_KEY = 'profile.animal.v1';

/**
 * Pick your creature: one animated in the middle, a chevron either side.
 *
 * The animation runs on the CENTRE one only. Eight looping sprites on one screen is a
 * fairground, and the point of this screen is to look at one creature properly and decide
 * whether it is you. The neighbours are drawn as still first frames so the chevrons say
 * what they lead to without competing.
 *
 * Every wrap and fallback decision is in `src/pixel/carousel.ts` so `bun test` covers it:
 * an off-by-one at the seam sends one chevron press to the wrong creature, which a person
 * hits on their first pass through the pack and no screenshot would ever show.
 */
export default function IconScreen() {
  const router = useRouter();
  const [animal, setAnimal] = useState<Animal>(ANIMALS[0]!);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    (async () => {
      const stored = await cache.getKv(ANIMAL_KEY);
      // No suggestion is passed yet: the archetype lives on the profile response and this
      // screen is reachable before the first sync. `openOn` already falls back for both.
      setAnimal(openOn(stored, null));
      setReady(true);
    })();
  }, []);

  const v = view(animal);

  async function choose() {
    await cache.setKv(ANIMAL_KEY, animal);
    router.back();
  }

  return (
    <View style={{ flex: 1, backgroundColor: c.bg, padding: space.md, justifyContent: 'center' }}>
      <Text
        style={{
          color: c.textDim,
          fontSize: 11,
          fontWeight: '700',
          letterSpacing: 1,
          textAlign: 'center',
        }}
      >
        PICK YOUR CREATURE
      </Text>
      <Text
        style={{
          color: c.text,
          fontSize: 15,
          lineHeight: 21,
          textAlign: 'center',
          marginTop: space.sm,
          paddingHorizontal: space.lg,
        }}
      >
        It goes on your profile and on everything you post. You can change it whenever.
      </Text>

      <View
        style={{
          flexDirection: 'row',
          alignItems: 'center',
          justifyContent: 'center',
          marginTop: space.xl,
          gap: space.md,
        }}
      >
        <Chevron
          dir="left"
          neighbour={v.previous}
          onPress={() => setAnimal(step(animal, -1))}
        />
        <View
          style={{
            width: 168,
            height: 168,
            borderRadius: 20,
            borderWidth: 1,
            borderColor: c.accent,
            backgroundColor: c.card,
            alignItems: 'center',
            justifyContent: 'center',
          }}
        >
          {/* Only this one moves. Eight looping sprites at once is a fairground. */}
          {ready && <PixelAnimal animal={animal} size={128} />}
        </View>
        <Chevron dir="right" neighbour={v.next} onPress={() => setAnimal(step(animal, 1))} />
      </View>

      <Text
        style={{
          color: c.accent,
          fontSize: 22,
          fontWeight: '800',
          textAlign: 'center',
          marginTop: space.lg,
        }}
      >
        {v.label}
      </Text>
      <Text style={{ color: c.textDim, fontSize: 12, textAlign: 'center', marginTop: 2 }}>
        {v.position} of {v.total}
      </Text>

      <Pressable
        onPress={choose}
        accessibilityRole="button"
        accessibilityLabel={`Choose the ${v.label}`}
        style={({ pressed }) => ({
          marginTop: space.xl,
          marginHorizontal: space.xl,
          paddingVertical: space.md,
          borderRadius: 14,
          backgroundColor: c.accent,
          opacity: pressed ? 0.8 : 1,
        })}
      >
        <Text style={{ color: c.bg, fontSize: 16, fontWeight: '800', textAlign: 'center' }}>
          Choose the {v.label}
        </Text>
      </Pressable>
    </View>
  );
}

/**
 * One chevron, with the creature it leads to drawn small and still beside it.
 *
 * A bare arrow makes somebody press it to find out what is there. Showing the neighbour
 * turns eight presses into one glance, and a still frame keeps it from competing with the
 * one in the middle.
 */
function Chevron({
  dir,
  neighbour,
  onPress,
}: {
  dir: 'left' | 'right';
  neighbour: Animal;
  onPress: () => void;
}) {
  return (
    <Pressable
      onPress={onPress}
      accessibilityRole="button"
      accessibilityLabel={`${dir === 'left' ? 'Previous' : 'Next'} creature, the ${neighbour}`}
      hitSlop={16}
      style={({ pressed }) => ({ alignItems: 'center', opacity: pressed ? 0.5 : 1 })}
    >
      <Text style={{ color: c.accent, fontSize: 34, fontWeight: '300', lineHeight: 38 }}>
        {dir === 'left' ? '‹' : '›'}
      </Text>
      <PixelAnimalIcon animal={neighbour} size={28} style={{ opacity: 0.45 }} />
    </Pressable>
  );
}
