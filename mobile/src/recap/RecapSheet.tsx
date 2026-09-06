import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  ActivityIndicator,
  Alert,
  Modal,
  Pressable,
  ScrollView,
  Text,
  TextInput,
  useWindowDimensions,
  View,
} from 'react-native';

import { CELEBRATION_MS, Chip } from '../analysis/AnalysisView';
import { labelize } from '../analysis/format';
import { ApiError, type FeedItem, type SessionDetail, type Visibility } from '../data/api';
import { api } from '../data/client';
import { PixelBadge } from '../pixel/PixelBadge';
import {
  CAPTION_MAX,
  planMedia,
  primaryAction,
  publishCaption,
} from '../social/composeFlow';
import { isAlreadySharedConflict, VISIBILITIES, visibilityLabel } from '../social/format';
import { MAX_PHOTOS, type PickedPhoto, type RecordedAudio } from '../social/media';
import { MediaPicker } from '../social/MediaPicker';
import { PhotoGrid } from '../social/PhotoGrid';
import { UploadList } from '../social/UploadLine';
import { useUploadFlow } from '../social/useUploadFlow';
import { decodeMarks } from '../strip/decode';
import { TimelineStrip } from '../strip/TimelineStrip';
import { colors, hitSlopToReach, space } from '../theme';
import { analysisEmptyCopy, defaultTitle, postBlocker, recapHeadline, statTiles } from './format';

const c = colors('dark');

/**
 * Strava's post-activity page, for a build session.
 *
 * Opens over the detail on `?recap=1` (a tapped completion push, the Mac's link, a share
 * sheet) and from "Finish & share". Top to bottom: the strip hero, an editable title, the
 * stat tiles, the analysis headline with Bit cheering for three seconds, photos and a
 * voice note, who can see it, a caption, then Post and Save privately.
 *
 * With an existing post (`post`) the sheet is an editor: caption and visibility PATCH to
 * the post, and photos or a voice note can still be added while the post has room —
 * the media routes attach to any post you own. Photos already on the post cannot be
 * removed here; the post screen owns that. The title field is hidden in this mode: the
 * server has no title on a post, so on creation it became the caption's first line.
 *
 * Two phases, like the compose sheet: the form, then the upload list, shared through
 * `useUploadFlow`. Post creates the row first — the post exists the moment the server
 * answers, media or not.
 */
export function RecapSheet({
  visible,
  session,
  post,
  offline,
  onClose,
  onPosted,
  onAlreadyPosted,
  onRetryConnection,
}: {
  visible: boolean;
  session: SessionDetail;
  /** The viewer's existing post for this session, when there is one: edit mode. */
  post: FeedItem | null;
  /** The detail could not reach the server on its last load; the sheet renders from cache. */
  offline: boolean;
  onClose: () => void;
  /** The post landed, with its media where uploads succeeded. */
  onPosted: (post: FeedItem) => void;
  /** The server answered 409 "already shared": the session has a post this sheet cannot see. */
  onAlreadyPosted: () => void;
  /** Re-read the detail; clears `offline` when the server answers. */
  onRetryConnection: () => void;
}) {
  const { width } = useWindowDimensions();
  // The post this opening started from. Derived live from the prop, `editing` flipped
  // to "Edit post" mid-typing when a fresh detail and its post lookup landed (a post made
  // on another device) — the title field vanished with its text and Save would have
  // PATCHed the other device's caption. Latched at open, until the sheet closes.
  const [shown, setShown] = useState(post);
  const editing = shown !== null;
  const sample = session.id === 'sample';

  const [title, setTitle] = useState('');
  const [caption, setCaption] = useState('');
  const [visibility, setVisibility] = useState<Visibility>('followers');
  const [photos, setPhotos] = useState<PickedPhoto[]>([]);
  const [audio, setAudio] = useState<RecordedAudio | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [cheering, setCheering] = useState(true);

  const flow = useUploadFlow(onPosted);
  const { reset } = flow;

  // The sheet stays mounted between openings. Each opening starts from the session and,
  // in edit mode, the post, not from whatever the last opening left behind.
  useEffect(() => {
    if (!visible) return;
    setTitle(defaultTitle(session));
    setShown(post);
    setCaption(post?.caption ?? '');
    setVisibility(post?.visibility ?? 'followers');
    setPhotos([]);
    setAudio(null);
    setSubmitting(false);
    setProblem(null);
    reset();
    setCheering(true);
    const timer = setTimeout(() => setCheering(false), CELEBRATION_MS);
    return () => clearTimeout(timer);
    // Reads the session and post as they are at opening time; a later re-read of the
    // detail must not wipe what the person has typed.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visible]);

  const tiles = useMemo(() => statTiles(session), [session]);
  const headline = recapHeadline(session);
  const analysis = session.analysis ?? null;
  const blocker = postBlocker({ offline, busy: submitting, sample });
  const busy = submitting || flow.busy;

  const submit = useCallback(
    async (chosen: Visibility) => {
      if (busy || blocker) return;
      const planned = planMedia(photos, audio);
      if (!planned.ok) {
        Alert.alert('Check the media', planned.message);
        return;
      }
      setSubmitting(true);
      setProblem(null);
      let row: FeedItem;
      try {
        if (shown) {
          row = await api.updatePost(shown.id, {
            caption: caption.trim() || null,
            visibility: chosen,
          });
        } else {
          row = await api.createPost({
            session_id: session.id,
            caption: publishCaption({ title, sessionTitle: session.title, caption }),
            visibility: chosen,
            share_analysis: false,
          });
        }
      } catch (e) {
        setSubmitting(false);
        if (isAlreadySharedConflict(e)) {
          Alert.alert('Already posted', 'This session is on the feed already.');
          onAlreadyPosted();
          return;
        }
        if (e instanceof ApiError && e.status === 0) {
          setProblem("Couldn't reach the server. Your recap is still here. Try again in a moment.");
          return;
        }
        setProblem(e instanceof Error ? e.message : 'Could not post. Try again.');
        return;
      }
      setSubmitting(false);
      flow.start(row, planned.jobs);
    },
    [busy, blocker, photos, audio, shown, caption, title, session, onAlreadyPosted, flow]
  );

  const action = primaryAction(visibility, editing);
  const contentWidth = width - space.md * 2;
  const stripWidth = contentWidth - space.md * 2;
  const existingPhotos = shown?.photos.length ?? 0;
  const roomForPhotos = Math.max(0, MAX_PHOTOS - existingPhotos);

  return (
    <Modal
      visible={visible}
      animationType="slide"
      presentationStyle="pageSheet"
      onRequestClose={() => (flow.uploading ? void flow.finish() : onClose())}
    >
      <ScrollView
        style={{ flex: 1, backgroundColor: c.bg }}
        contentContainerStyle={{ padding: space.md, paddingBottom: space.xxl }}
        keyboardShouldPersistTaps="handled"
      >
        <View style={{ flexDirection: 'row', alignItems: 'center', marginBottom: space.md }}>
          {flow.uploading ? (
            <View style={{ width: 56 }} />
          ) : (
            <Pressable onPress={onClose} hitSlop={8} disabled={busy}>
              <Text style={{ color: c.textDim, fontSize: 15 }}>{editing ? 'Cancel' : 'Not now'}</Text>
            </Pressable>
          )}
          <Text style={{ color: c.text, fontSize: 17, fontWeight: '700', flex: 1, textAlign: 'center' }}>
            {flow.uploading ? 'Uploading' : editing ? 'Edit post' : 'Session recap'}
          </Text>
          {flow.uploading ? (
            <Pressable onPress={() => void flow.finish()} disabled={busy} hitSlop={8}>
              <Text style={{ color: c.accent, fontSize: 15, fontWeight: '700', opacity: busy ? 0.5 : 1 }}>
                Done
              </Text>
            </Pressable>
          ) : (
            <View style={{ width: 56 }} />
          )}
        </View>

        {flow.uploading ? (
          <UploadList
            rows={flow.rows}
            busy={flow.busy}
            retryable={flow.retryable}
            onRetry={flow.retry}
            intro={
              editing
                ? 'Your changes are saved. The new photos and voice note are on their way.'
                : 'Your post is up. Its photos and voice note are on their way.'
            }
          />
        ) : (
          <>
            {/* The hero: the session's own shape, at full width, before any number. */}
            <View style={card}>
              {session.strip ? (
                <TimelineStrip
                  cols={session.strip.cols}
                  marks={decodeMarks(session.strip.marks)}
                  spanMs={Math.max(1, session.strip.t1_ms - session.strip.t0_ms)}
                  preset="hero"
                  width={stripWidth}
                />
              ) : (
                <Text style={{ color: c.textDim, fontSize: 13 }}>
                  This session predates the detail your editor keeps. Its hours still count.
                </Text>
              )}
              <Text style={{ color: c.text, fontSize: 20, fontWeight: '700', marginTop: space.md }}>
                {headline}
              </Text>
              <Text style={{ color: c.textDim, fontSize: 12, marginTop: 2 }}>
                {session.repo_name ?? 'private repo'} · {new Date(session.started_at).toLocaleDateString()}
              </Text>
            </View>

            {!editing && (
              <>
                <Text style={[label, { marginTop: space.lg }]}>TITLE</Text>
                <TextInput
                  value={title}
                  onChangeText={(t) => setTitle(t.slice(0, 120))}
                  placeholder={session.unattended ? 'Name this run' : 'Name this session'}
                  placeholderTextColor={c.textDim}
                  editable={!busy}
                  style={input}
                />
                <Text style={{ color: c.textDim, fontSize: 11, marginTop: 4 }}>
                  Goes on your post. The session keeps the title your editor gave it.
                </Text>
              </>
            )}

            <Text style={[label, { marginTop: space.lg }]}>NUMBERS</Text>
            <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: space.sm }}>
              {tiles.map((t) => (
                <View
                  key={t.key}
                  style={{
                    width: (contentWidth - space.sm * 2) / 3,
                    backgroundColor: c.card,
                    borderRadius: 12,
                    paddingVertical: space.md,
                    paddingHorizontal: space.sm,
                  }}
                >
                  <Text
                    style={{
                      color: t.dim ? c.textDim : c.text,
                      fontSize: t.dim ? 13 : 20,
                      fontWeight: t.dim ? '400' : '700',
                      fontVariant: ['tabular-nums'],
                    }}
                    numberOfLines={1}
                  >
                    {t.value}
                  </Text>
                  <Text style={{ color: c.textDim, fontSize: 11, marginTop: 2 }}>{t.label}</Text>
                </View>
              ))}
            </View>

            <Text style={[label, { marginTop: space.lg }]}>ANALYSIS</Text>
            <View style={card}>
              {analysis ? (
                <>
                  <PixelBadge
                    state={cheering ? 'celebrating' : 'idle'}
                    paused={!cheering}
                    title={analysis.headline}
                    text={analysis.summary}
                    style={{ padding: 0 }}
                  />
                  {analysis.archetype ? (
                    <View style={{ flexDirection: 'row', marginTop: space.md }}>
                      <Chip label={labelize(analysis.archetype)} tone="accent" />
                    </View>
                  ) : null}
                </>
              ) : (
                <PixelBadge state="thinking" text={analysisEmptyCopy(session)} style={{ padding: 0 }} />
              )}
            </View>

            <View style={{ marginTop: space.lg }}>
              {editing && shown.photos.length > 0 && (
                <>
                  <Text style={label}>ON THE POST</Text>
                  <PhotoGrid photos={shown.photos} width={contentWidth} style={{ marginBottom: space.md }} />
                </>
              )}
              <MediaPicker
                photos={photos}
                onPhotos={setPhotos}
                audio={audio}
                onAudio={setAudio}
                disabled={busy}
                maxPhotos={roomForPhotos}
                allowAudio={!shown?.audio}
              />
              {editing && shown.audio ? (
                <Text style={{ color: c.textDim, fontSize: 11, marginTop: 6 }}>
                  This post already has its voice note.
                </Text>
              ) : null}
            </View>

            <Text style={[label, { marginTop: space.lg }]}>WHO CAN SEE IT</Text>
            <View style={{ flexDirection: 'row', backgroundColor: c.card, borderRadius: 10, padding: 3 }}>
              {VISIBILITIES.map((v) => (
                <Pressable
                  key={v}
                  onPress={() => setVisibility(v)}
                  disabled={busy}
                  style={{
                    flex: 1,
                    paddingVertical: space.sm,
                    borderRadius: 8,
                    alignItems: 'center',
                    backgroundColor: v === visibility ? c.accent : 'transparent',
                  }}
                >
                  <Text style={{ color: v === visibility ? c.onAccent : c.text, fontWeight: '600', fontSize: 14 }}>
                    {visibilityLabel(v)}
                  </Text>
                </Pressable>
              ))}
            </View>

            <Text style={[label, { marginTop: space.lg }]}>CAPTION</Text>
            <TextInput
              value={caption}
              onChangeText={(t) => setCaption(t.slice(0, CAPTION_MAX))}
              placeholder="What did you build?"
              placeholderTextColor={c.textDim}
              multiline
              editable={!busy}
              style={[input, { minHeight: 96, textAlignVertical: 'top' }]}
            />
            <Text style={{ color: c.textDim, fontSize: 11, textAlign: 'right', marginTop: 4 }}>
              {caption.length}/{CAPTION_MAX}
            </Text>

            {(blocker || problem) && (
              <View style={[card, { marginTop: space.lg, flexDirection: 'row', alignItems: 'center', gap: space.sm }]}>
                <Text style={{ color: c.textDim, fontSize: 13, flex: 1 }}>{problem ?? blocker}</Text>
                {(offline || problem) && !sample && (
                  <Pressable hitSlop={hitSlopToReach(18)} onPress={onRetryConnection} accessibilityRole="button">
                    <Text style={{ color: c.accent, fontWeight: '600', fontSize: 13 }}>Try again</Text>
                  </Pressable>
                )}
              </View>
            )}

            <Pressable
              onPress={() => void submit(visibility)}
              disabled={busy || Boolean(blocker)}
              accessibilityRole="button"
              style={({ pressed }) => [
                primary,
                (busy || blocker) && { opacity: 0.5 },
                pressed && { opacity: 0.8 },
              ]}
            >
              {submitting ? (
                <ActivityIndicator color={c.onAccent} />
              ) : (
                <Text style={{ color: c.onAccent, fontWeight: '700', fontSize: 16 }}>{action.label}</Text>
              )}
            </Pressable>
            {action.showSavePrivately && (
              <Pressable
                onPress={() => void submit('private')}
                disabled={busy || Boolean(blocker)}
                accessibilityRole="button"
                style={({ pressed }) => [secondary, (busy || blocker) && { opacity: 0.5 }, pressed && { opacity: 0.8 }]}
              >
                <Text style={{ color: c.text, fontWeight: '600', fontSize: 15 }}>Save privately</Text>
                <Text style={{ color: c.textDim, fontSize: 11, marginTop: 2 }}>Only you. Nothing reaches a feed.</Text>
              </Pressable>
            )}
          </>
        )}
      </ScrollView>
    </Modal>
  );
}

const card = {
  backgroundColor: c.card,
  borderRadius: 12,
  padding: space.md,
} as const;

const label = {
  color: c.textDim,
  fontSize: 11,
  fontWeight: '700',
  letterSpacing: 0.8,
  marginBottom: space.sm,
} as const;

const input = {
  color: c.text,
  backgroundColor: c.card,
  borderRadius: 10,
  padding: space.md,
  fontSize: 15,
} as const;

const primary = {
  backgroundColor: c.accent,
  borderRadius: 12,
  paddingVertical: space.md,
  alignItems: 'center',
  marginTop: space.lg,
} as const;

const secondary = {
  backgroundColor: c.card,
  borderRadius: 12,
  paddingVertical: space.md,
  alignItems: 'center',
  marginTop: space.sm,
} as const;
