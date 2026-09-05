import { Audio } from 'expo-av';
import * as ImagePicker from 'expo-image-picker';
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Alert, Image, Pressable, Text, View } from 'react-native';

import { colors, space } from '../theme';
import { AudioChip } from './AudioChip';
import {
  addPhotos,
  audioMime,
  formatClock,
  MAX_AUDIO_MS,
  MAX_PHOTOS,
  photoRoom,
  type PickedPhoto,
  type RecordedAudio,
} from './media';

const c = colors('dark');

const THUMB = 72;

/**
 * Photos and a voice note for the compose sheet. Owns nothing but the recorder in
 * flight; the chosen photos and the finished recording live in the sheet's state so
 * that Post can turn them into an upload plan after the post row exists.
 *
 * Downscaling happens at upload time, not here: `upload.ts::downscalePhoto` shrinks any
 * photo the plan marks `oversized` to a 2048 long edge at JPEG q=0.85 (docs/social.md).
 * The picker's `quality: 0.85` only recompresses; the thumbnails below show the original.
 */
export function MediaPicker({
  photos,
  onPhotos,
  audio,
  onAudio,
  disabled = false,
}: {
  photos: PickedPhoto[];
  onPhotos: (next: PickedPhoto[]) => void;
  audio: RecordedAudio | null;
  onAudio: (next: RecordedAudio | null) => void;
  disabled?: boolean;
}) {
  const room = photoRoom(photos.length);

  const pick = useCallback(async () => {
    if (room === 0) return;
    let result: ImagePicker.ImagePickerResult;
    try {
      result = await ImagePicker.launchImageLibraryAsync({
        mediaTypes: ['images'],
        allowsMultipleSelection: true,
        selectionLimit: room,
        quality: 0.85,
      });
    } catch (e) {
      Alert.alert('Could not open photos', e instanceof Error ? e.message : 'try again');
      return;
    }
    if (result.canceled) return;
    const picked: PickedPhoto[] = result.assets.map((a) => ({
      uri: a.uri,
      width: a.width,
      height: a.height,
      mime: a.mimeType ?? null,
      bytes: a.fileSize ?? null,
    }));
    onPhotos(addPhotos(photos, picked));
  }, [photos, onPhotos, room]);

  const remove = useCallback(
    (uri: string) => onPhotos(photos.filter((p) => p.uri !== uri)),
    [photos, onPhotos]
  );

  return (
    <View>
      <Text style={label}>PHOTOS</Text>
      <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: space.sm }}>
        {photos.map((p) => (
          <View key={p.uri} style={{ width: THUMB, height: THUMB }}>
            <Image
              source={{ uri: p.uri }}
              resizeMode="cover"
              accessibilityIgnoresInvertColors
              style={{ width: THUMB, height: THUMB, borderRadius: 8, backgroundColor: c.border }}
            />
            {!disabled && (
              <Pressable
                onPress={() => remove(p.uri)}
                hitSlop={8}
                accessibilityLabel="Remove photo"
                style={{
                  position: 'absolute',
                  top: -6,
                  right: -6,
                  width: 22,
                  height: 22,
                  borderRadius: 11,
                  backgroundColor: c.bg,
                  borderWidth: 1,
                  borderColor: c.border,
                  alignItems: 'center',
                  justifyContent: 'center',
                }}
              >
                <Text style={{ color: c.text, fontSize: 13, lineHeight: 15, fontWeight: '700' }}>×</Text>
              </Pressable>
            )}
          </View>
        ))}
        {room > 0 && !disabled && (
          <Pressable
            onPress={() => void pick()}
            accessibilityRole="button"
            style={({ pressed }) => [
              {
                width: THUMB,
                height: THUMB,
                borderRadius: 8,
                borderWidth: 1,
                borderStyle: 'dashed',
                borderColor: c.textDim,
                alignItems: 'center',
                justifyContent: 'center',
              },
              pressed && { opacity: 0.7 },
            ]}
          >
            <Text style={{ color: c.text, fontSize: 22, lineHeight: 26 }}>+</Text>
            <Text style={{ color: c.textDim, fontSize: 10 }}>
              {photos.length === 0 ? 'Add photos' : `${room} more`}
            </Text>
          </Pressable>
        )}
      </View>
      <Text style={{ color: c.textDim, fontSize: 11, marginTop: 6 }}>
        Up to {MAX_PHOTOS}. Screenshots of the thing you built are the point.
      </Text>

      <Text style={[label, { marginTop: space.lg }]}>VOICE NOTE</Text>
      <Recorder audio={audio} onAudio={onAudio} disabled={disabled} />
    </View>
  );
}

/**
 * Record → preview → keep, delete or re-record. One note, at most 90 s: the status
 * callback watches the clock and stops the recorder itself at the cap, so a person who
 * keeps talking loses the tail rather than the whole note to a 422.
 */
function Recorder({
  audio,
  onAudio,
  disabled,
}: {
  audio: RecordedAudio | null;
  onAudio: (next: RecordedAudio | null) => void;
  disabled: boolean;
}) {
  const [recording, setRecording] = useState(false);
  const [elapsedMs, setElapsedMs] = useState(0);
  const recRef = useRef<Audio.Recording | null>(null);
  const stoppingRef = useRef(false);

  const stop = useCallback(async () => {
    const rec = recRef.current;
    if (!rec || stoppingRef.current) return;
    stoppingRef.current = true;
    try {
      try {
        await rec.stopAndUnloadAsync();
      } catch {
        // Already stopped (the cap and a tap can race); the status below still reads.
      }
      const status = await rec.getStatusAsync();
      const uri = rec.getURI();
      // A note that ran to the cap can read a few ms over it by the time the stop lands;
      // the server rejects 90 001, so the number the person kept is the cap itself.
      const durationMs = Math.min(status.durationMillis, MAX_AUDIO_MS);
      if (uri && durationMs > 0) {
        onAudio({ uri, durationMs, mime: audioMime(uri) });
      }
    } finally {
      recRef.current = null;
      stoppingRef.current = false;
      setRecording(false);
      setElapsedMs(0);
      // Back to playback routing, or the preview plays through the earpiece on iOS.
      void Audio.setAudioModeAsync({ allowsRecordingIOS: false, playsInSilentModeIOS: true }).catch(
        () => undefined
      );
    }
  }, [onAudio]);

  const start = useCallback(async () => {
    if (recRef.current) return;
    try {
      const perm = await Audio.requestPermissionsAsync();
      if (!perm.granted) {
        Alert.alert('Microphone', 'Allow microphone access in Settings to record a voice note.');
        return;
      }
      await Audio.setAudioModeAsync({ allowsRecordingIOS: true, playsInSilentModeIOS: true });
      const preset = Audio.RecordingOptionsPresets.HIGH_QUALITY;
      if (!preset) throw new Error('recording preset unavailable');
      onAudio(null);
      const onStatus = (st: Audio.RecordingStatus) => {
        setElapsedMs(st.durationMillis);
        if (st.isRecording && st.durationMillis >= MAX_AUDIO_MS) void stop();
      };
      const { recording: rec } = await Audio.Recording.createAsync(preset, onStatus, 250);
      recRef.current = rec;
      setRecording(true);
    } catch (e) {
      Alert.alert('Could not record', e instanceof Error ? e.message : 'try again');
      recRef.current = null;
      setRecording(false);
    }
  }, [onAudio, stop]);

  // Unmount mid-recording: release the recorder; nothing is kept.
  useEffect(
    () => () => {
      const rec = recRef.current;
      recRef.current = null;
      if (rec) void rec.stopAndUnloadAsync().catch(() => undefined);
    },
    []
  );

  if (recording) {
    const remaining = Math.max(0, MAX_AUDIO_MS - elapsedMs);
    return (
      <View style={box}>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: space.sm }}>
          <View style={{ width: 10, height: 10, borderRadius: 5, backgroundColor: c.danger }} />
          <Text style={{ color: c.text, fontSize: 15, fontWeight: '600', fontVariant: ['tabular-nums'] }}>
            {formatClock(elapsedMs)}
          </Text>
          <Text style={{ color: c.textDim, fontSize: 12, fontVariant: ['tabular-nums'] }}>
            · {formatClock(remaining)} left
          </Text>
          <View style={{ flex: 1 }} />
          <Pressable onPress={() => void stop()} hitSlop={8} style={({ pressed }) => [pill, pressed && { opacity: 0.7 }]}>
            <Text style={{ color: c.onAccent, fontSize: 13, fontWeight: '700' }}>Stop</Text>
          </Pressable>
        </View>
      </View>
    );
  }

  if (audio) {
    return (
      <View style={box}>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: space.sm }}>
          <AudioChip uri={audio.uri} durationMs={audio.durationMs} />
          <View style={{ flex: 1 }} />
          {!disabled && (
            <>
              <Pressable onPress={() => void start()} hitSlop={8}>
                <Text style={{ color: c.text, fontSize: 13, fontWeight: '600' }}>Re-record</Text>
              </Pressable>
              <Pressable onPress={() => onAudio(null)} hitSlop={8} style={{ marginLeft: space.md }}>
                <Text style={{ color: c.danger, fontSize: 13, fontWeight: '600' }}>Delete</Text>
              </Pressable>
            </>
          )}
        </View>
      </View>
    );
  }

  return (
    <Pressable
      onPress={() => void start()}
      disabled={disabled}
      accessibilityRole="button"
      style={({ pressed }) => [box, { opacity: disabled ? 0.5 : pressed ? 0.8 : 1 }]}
    >
      <Text style={{ color: c.text, fontSize: 15, fontWeight: '600' }}>Record a voice note</Text>
      <Text style={{ color: c.textDim, fontSize: 12, marginTop: 2 }}>
        Up to {formatClock(MAX_AUDIO_MS)}. Saying what you built beats a caption.
      </Text>
    </Pressable>
  );
}

const label = {
  color: c.textDim,
  fontSize: 11,
  fontWeight: '700',
  letterSpacing: 0.8,
  marginBottom: space.sm,
} as const;

const box = {
  backgroundColor: c.card,
  borderRadius: 10,
  padding: space.md,
} as const;

const pill = {
  backgroundColor: c.accent,
  borderRadius: 999,
  paddingHorizontal: 14,
  paddingVertical: 6,
} as const;
