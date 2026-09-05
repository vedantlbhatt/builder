import React from 'react';
import { ActivityIndicator, Image, Text, View } from 'react-native';

import { colors, space } from '../theme';
import { uploadStatus, uploadWhat, type UploadRow } from './composeFlow';

const c = colors('dark');

/** One upload's line: a thumbnail or the note's length, and where it is. */
export function UploadLine({ row }: { row: UploadRow }) {
  const { job, state } = row;
  const failed = state.phase === 'failed';
  return (
    <View
      style={{
        flexDirection: 'row',
        alignItems: 'center',
        gap: space.sm,
        backgroundColor: c.card,
        borderRadius: 10,
        padding: space.sm,
        marginBottom: space.sm,
      }}
    >
      {job.kind === 'photo' ? (
        <Image
          source={{ uri: job.uri }}
          resizeMode="cover"
          accessibilityIgnoresInvertColors
          style={{ width: 40, height: 40, borderRadius: 6, backgroundColor: c.border }}
        />
      ) : (
        <View
          style={{
            width: 40,
            height: 40,
            borderRadius: 6,
            backgroundColor: c.border,
            alignItems: 'center',
            justifyContent: 'center',
          }}
        >
          <Text style={{ color: c.text, fontSize: 16 }}>▶</Text>
        </View>
      )}
      <Text style={{ color: c.text, fontSize: 14, flex: 1 }}>{uploadWhat(job)}</Text>
      {state.phase === 'uploading' ? (
        <ActivityIndicator color={c.accent} />
      ) : (
        <Text
          style={{
            color: failed ? c.danger : state.phase === 'done' ? c.accent : c.textDim,
            fontSize: 12,
            fontWeight: '600',
            maxWidth: 160,
          }}
          numberOfLines={2}
        >
          {uploadStatus(state)}
        </Text>
      )}
    </View>
  );
}

/** The upload phase's body: a sentence, the lines, and Retry when it would do anything. */
export function UploadList({
  rows,
  busy,
  retryable,
  onRetry,
  intro = 'Your post is up. Its photos and voice note are on their way.',
}: {
  rows: readonly UploadRow[];
  busy: boolean;
  retryable: boolean;
  onRetry: () => void;
  intro?: string;
}) {
  return (
    <View>
      <Text style={{ color: c.textDim, fontSize: 13, lineHeight: 18, marginBottom: space.md }}>{intro}</Text>
      {rows.map((r, i) => (
        <UploadLine key={i} row={r} />
      ))}
      {!busy && retryable && (
        <Text
          onPress={onRetry}
          accessibilityRole="button"
          style={{
            backgroundColor: c.accent,
            color: c.onAccent,
            fontWeight: '700',
            fontSize: 15,
            borderRadius: 12,
            paddingVertical: space.md,
            textAlign: 'center',
            marginTop: space.md,
            overflow: 'hidden',
          }}
        >
          Retry failed uploads
        </Text>
      )}
    </View>
  );
}
