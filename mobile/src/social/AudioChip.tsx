import { Audio, type AVPlaybackStatus } from 'expo-av';
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Pressable, Text, type StyleProp, type ViewStyle } from 'react-native';

import { colors } from '../theme';
import {
  AUDIO_FAILED_CAPTION,
  AUDIO_FAILED_CAPTION_MS,
  audioChipLabel,
  formatClock,
  isPlaybackError,
} from './media';

const c = colors('dark');

/**
 * The one sound playing anywhere in the app. A feed is a list of chips; two voice notes
 * talking over each other is the clunky feed docs/social.md forbids, so the chip that
 * starts unloads whoever was current and tells that chip to reset.
 */
let current: { sound: Audio.Sound; evict: () => void } | null = null;

async function stopCurrent(except?: Audio.Sound | null): Promise<void> {
  if (!current || current.sound === except) return;
  const { sound, evict } = current;
  current = null;
  evict();
  try {
    await sound.unloadAsync();
  } catch {
    // Already unloaded, or the native side went away; nothing to release.
  }
}

/**
 * "▶ 0:42" — the voice note on a post, played inline. `uri` null (the server omits the
 * url when OBJECT_STORE_PUBLIC_BASE is unset) renders the chip inert with its length, so
 * the note is at least visibly there.
 */
export function AudioChip({
  uri,
  durationMs,
  style,
}: {
  uri: string | null;
  durationMs: number | null;
  style?: StyleProp<ViewStyle>;
}) {
  const [playing, setPlaying] = useState(false);
  const [positionMs, setPositionMs] = useState(0);
  const [failed, setFailed] = useState(false);
  const soundRef = useRef<Audio.Sound | null>(null);
  const finishedRef = useRef(false);
  const busyRef = useRef(false);
  const failedTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const reset = useCallback(() => {
    soundRef.current = null;
    finishedRef.current = false;
    setPlaying(false);
    setPositionMs(0);
  }, []);

  const clearFailed = useCallback(() => {
    if (failedTimer.current) clearTimeout(failedTimer.current);
    failedTimer.current = null;
    setFailed(false);
  }, []);

  // Playback failed, either at load or mid-note. Drop the sound (it is unloaded already,
  // or unusable), say so briefly, and go back to the play glyph so the next tap is a
  // fresh attempt rather than a pauseAsync on a dead handle.
  const fail = useCallback(() => {
    const s = soundRef.current;
    if (s) {
      if (current?.sound === s) current = null;
      void s.unloadAsync().catch(() => undefined);
    }
    reset();
    if (failedTimer.current) clearTimeout(failedTimer.current);
    setFailed(true);
    failedTimer.current = setTimeout(() => {
      failedTimer.current = null;
      setFailed(false);
    }, AUDIO_FAILED_CAPTION_MS);
  }, [reset]);

  // Unmount: release our sound if it is still ours. A row scrolled out of a FlatList
  // must not keep talking.
  useEffect(
    () => () => {
      if (failedTimer.current) clearTimeout(failedTimer.current);
      const s = soundRef.current;
      if (!s) return;
      if (current?.sound === s) current = null;
      void s.unloadAsync().catch(() => undefined);
    },
    []
  );

  // A new uri (the same row recycled for another post) drops the old sound.
  useEffect(() => {
    const s = soundRef.current;
    if (s) {
      if (current?.sound === s) current = null;
      void s.unloadAsync().catch(() => undefined);
      reset();
    }
  }, [uri, reset]);

  const onStatus = useCallback(
    (st: AVPlaybackStatus) => {
      if (!st.isLoaded) {
        // `{ isLoaded: false, error }` is how expo-av delivers a failure AFTER a
        // successful load. Without this branch `playing` stays true and the chip shows
        // the stop glyph over silence.
        if (isPlaybackError(st)) fail();
        return;
      }
      setPositionMs(st.positionMillis);
      if (st.didJustFinish) {
        finishedRef.current = true;
        setPlaying(false);
      } else {
        setPlaying(st.isPlaying);
      }
    },
    [fail]
  );

  const toggle = useCallback(async () => {
    if (!uri || busyRef.current) return;
    busyRef.current = true;
    clearFailed();
    try {
      const own = soundRef.current;
      if (playing && own) {
        await own.pauseAsync();
        setPlaying(false);
        return;
      }
      await stopCurrent(own);
      // Playback, not recording: `allowsRecordingIOS` left on routes audio to the earpiece.
      await Audio.setAudioModeAsync({ allowsRecordingIOS: false, playsInSilentModeIOS: true });
      if (own && current?.sound === own) {
        if (finishedRef.current) {
          finishedRef.current = false;
          await own.replayAsync();
        } else {
          await own.playAsync();
        }
        setPlaying(true);
        return;
      }
      const { sound } = await Audio.Sound.createAsync({ uri }, { shouldPlay: true }, onStatus);
      soundRef.current = sound;
      finishedRef.current = false;
      current = { sound, evict: reset };
      setPlaying(true);
    } catch {
      fail();
    } finally {
      busyRef.current = false;
    }
  }, [uri, playing, onStatus, reset, clearFailed, fail]);

  const total = durationMs ?? 0;
  const label = audioChipLabel({ playing, positionMs, totalMs: total });

  return (
    <Pressable
      onPress={() => void toggle()}
      disabled={!uri}
      hitSlop={6}
      accessibilityRole="button"
      accessibilityLabel={playing ? 'Pause voice note' : 'Play voice note'}
      style={({ pressed }) => [
        {
          alignSelf: 'flex-start',
          flexDirection: 'row',
          alignItems: 'center',
          borderRadius: 999,
          borderWidth: 1,
          borderColor: playing ? c.accent : c.border,
          backgroundColor: playing ? c.accent : 'transparent',
          paddingHorizontal: 12,
          paddingVertical: 6,
          opacity: uri ? (pressed ? 0.7 : 1) : 0.5,
        },
        style,
      ]}
    >
      <Text
        style={{
          color: playing ? c.onAccent : c.text,
          fontSize: 13,
          fontWeight: '600',
          fontVariant: ['tabular-nums'],
        }}
      >
        {uri ? label : `Voice note · ${formatClock(total)}`}
      </Text>
      {failed && (
        <Text style={{ color: c.textDim, fontSize: 12, marginLeft: 8 }}>{AUDIO_FAILED_CAPTION}</Text>
      )}
    </Pressable>
  );
}
