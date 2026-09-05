import { useLocalSearchParams } from 'expo-router';
import React, { useEffect, useRef, useState } from 'react';
import {
  ActivityIndicator,
  Alert,
  Image,
  Modal,
  Pressable,
  ScrollView,
  Switch,
  Text,
  TextInput,
  useWindowDimensions,
  View,
} from 'react-native';

import { AnalysisView } from '../../src/analysis/AnalysisView';
import { describeEnd } from '../../src/analysis/format';
import { RecapCard, toCardModel, type CardModel } from '../../src/card/RecapCard';
import { shareCard } from '../../src/card/export';
import {
  PRESIGN_UNCONFIGURED_MESSAGE,
  type FeedItem,
  type SessionDetail,
  type Visibility,
} from '../../src/data/api';
import * as cache from '../../src/data/cache';
import { api, SAMPLE_SESSION } from '../../src/data/client';
import { VISIBILITIES, visibilityLabel } from '../../src/social/format';
import { rememberMyHandle } from '../../src/social/identity';
import {
  formatClock,
  mediaPlan,
  type MediaJob,
  type PickedPhoto,
  type RecordedAudio,
} from '../../src/social/media';
import { MediaPicker } from '../../src/social/MediaPicker';
import { PixelBadge } from '../../src/pixel/PixelBadge';
import { runUploads, type UploadState } from '../../src/social/upload';
import { TimelineStrip } from '../../src/strip/TimelineStrip';
import { classShare, decodeColumns, decodeMarks } from '../../src/strip/decode';
import { StripClass } from '../../src/generated/strip';
import { colors, compactNumber, duration, space } from '../../src/theme';

const c = colors('dark');

export default function SessionScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const { width } = useWindowDimensions();
  const [model, setModel] = useState<CardModel | null>(null);
  const [session, setSession] = useState<SessionDetail | null>(null);
  const [sharing, setSharing] = useState(false);
  const [composing, setComposing] = useState(false);
  const [post, setPost] = useState<FeedItem | null>(null);
  const cardRef = useRef(null);

  useEffect(() => {
    const show = (s: SessionDetail, code: string) => {
      setSession(s);
      setModel(toCardModel(s, code));
    };
    (async () => {
      if (id === 'sample') {
        show(SAMPLE_SESSION, 'builder.dev/s/sample');
        return;
      }
      const cached = await cache.getDetail(id!);
      if (cached) show(cached, `builder.dev/s/${id!.slice(0, 6)}`);
      try {
        const fresh = await api.session(id!);
        await cache.putDetail(fresh);
        show(fresh, `builder.dev/s/${id!.slice(0, 6)}`);
      } catch {
        // Offline with a cached copy is fine; offline without one shows the spinner.
      }
    })();
  }, [id]);

  if (!model || !session) {
    return (
      <View style={{ flex: 1, justifyContent: 'center', backgroundColor: c.bg, paddingHorizontal: space.md }}>
        <PixelBadge state="thinking" text="Reading your session…" />
      </View>
    );
  }

  const contentWidth = width - space.md * 2;
  const share = model.strip ? classShare(decodeColumns(model.strip)) : null;

  // Boundary fields are optional on read: an older server omits them, and the row is
  // skipped rather than shown as "0s / 0s".
  const state = session.state ?? 'final';
  const hasSplit = session.attended_seconds !== undefined || session.autonomous_seconds !== undefined;
  const endNote = describeEnd(session);

  return (
    <ScrollView
      style={{ flex: 1, backgroundColor: c.bg }}
      contentContainerStyle={{ padding: space.md, paddingBottom: space.xxl }}
    >
      {/* The card, rendered at the width it will be captured at. Live preview rather than
          a separate "export" path: what you see is literally the view that gets captured. */}
      <View ref={cardRef} collapsable={false} style={{ borderRadius: 14, overflow: 'hidden' }}>
        <RecapCard model={model} width={contentWidth} />
      </View>

      <Pressable
        onPress={async () => {
          setSharing(true);
          try {
            await shareCard(cardRef, model);
          } finally {
            setSharing(false);
          }
        }}
        style={({ pressed }) => [
          {
            backgroundColor: c.accent,
            borderRadius: 12,
            paddingVertical: space.md,
            alignItems: 'center',
            marginTop: space.md,
          },
          pressed && { opacity: 0.8 },
        ]}
      >
        <Text style={{ color: c.onAccent, fontWeight: '700', fontSize: 16 }}>
          {sharing ? 'Preparing…' : 'Share this session'}
        </Text>
      </Pressable>

      {/* Sharing to the feed is an act, never automatic, and only for a finished session:
          a live card's numbers keep moving and a recap that moves is not a recap. */}
      {state === 'final' && id !== 'sample' && (
        post ? (
          <View style={[postedBox, { flexDirection: 'row', alignItems: 'center' }]}>
            <Text style={{ color: c.text, fontSize: 14, flex: 1 }}>
              Posted · {visibilityLabel(post.visibility)}
            </Text>
            <Pressable
              hitSlop={8}
              onPress={() =>
                Alert.alert('Delete post?', 'It disappears from every feed immediately.', [
                  { text: 'Cancel', style: 'cancel' },
                  {
                    text: 'Delete',
                    style: 'destructive',
                    onPress: async () => {
                      try {
                        await api.deletePost(post.id);
                        setPost(null);
                      } catch (e) {
                        Alert.alert('Could not delete', e instanceof Error ? e.message : 'try again');
                      }
                    },
                  },
                ])
              }
            >
              <Text style={{ color: c.danger, fontWeight: '600', fontSize: 14 }}>Delete</Text>
            </Pressable>
          </View>
        ) : (
          <Pressable
            onPress={() => setComposing(true)}
            style={({ pressed }) => [postedBox, { alignItems: 'center' }, pressed && { opacity: 0.8 }]}
          >
            <Text style={{ color: c.text, fontWeight: '700', fontSize: 15 }}>Post to the feed</Text>
          </Pressable>
        )
      )}

      <ComposeModal
        visible={composing}
        sessionId={session.id}
        hasAnalysis={Boolean(session.analysis)}
        onClose={() => setComposing(false)}
        onPosted={(p) => {
          setPost(p);
          setComposing(false);
        }}
      />

      <Section title="Timeline">
        {model.strip ? (
          <>
            <TimelineStrip
              cols={model.strip}
              marks={decodeMarks(model.marks)}
              spanMs={Math.max(1, model.wallSeconds * 1000)}
              preset="hero"
              width={contentWidth - space.md * 2}
            />
            {/* The legend lives HERE, not on the shared card. A route map does not explain
                its own encoding; the moment an artifact does, it is a chart. */}
            <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: space.md, marginTop: space.sm }}>
              <LegendItem klass={StripClass.prompting} label="you prompting" share={share} />
              <LegendItem klass={StripClass.agent} label="agent working" share={share} />
              <LegendItem klass={StripClass.human_edit} label="your edits" share={share} />
              <LegendItem klass={StripClass.idle} label="idle" share={share} />
            </View>
          </>
        ) : (
          <Text style={{ color: c.textDim, fontSize: 13 }}>
            This session predates the detail your editor keeps. Its hours still count.
          </Text>
        )}
      </Section>

      <Section title="Numbers">
        {state === 'live' && <Row label="Status" value="Live" />}
        <Row label="Active" value={duration(model.activeSeconds)} />
        {hasSplit && (
          <Row
            label="Attended / autonomous"
            value={`${duration(session.attended_seconds ?? 0)} / ${duration(session.autonomous_seconds ?? 0)}`}
          />
        )}
        <Row label="Elapsed" value={duration(model.wallSeconds)} />
        <Row label="Prompts you typed" value={`${model.prompts}`} />
        <Row label="Files touched" value={`${model.filesTouched}`} />
        <Row label="Lines from the agent" value={model.agentLines.toLocaleString()} />
        {model.commits > 0 && <Row label="Commits" value={`${model.commits}`} />}
        {model.tokensReported ? (
          <Row label="Tokens" value={compactNumber(model.totalTokens)} />
        ) : (
          // Absent, not zero. Cursor accounts usage server-side and writes {0,0} locally,
          // so a "0" here would be a claim about the session rather than about Cursor.
          <Row label="Tokens" value="not recorded by this editor" dim />
        )}
        {endNote && (
          <Text style={{ color: c.textDim, fontSize: 12, lineHeight: 17, marginTop: space.sm }}>
            {endNote}
          </Text>
        )}
      </Section>

      {session.analysis ? (
        <AnalysisView analysis={session.analysis} />
      ) : (
        <Section title="Analysis">
          {state === 'final' ? (
            // Quiet, and no mascot: nothing is coming. A final session without an analysis
            // will not grow one by waiting, and a thinking Bit would promise otherwise.
            <Text style={{ color: c.textDim, fontSize: 13 }}>
              Analysis not available for this session
            </Text>
          ) : (
            <PixelBadge
              state="thinking"
              text="Analysis runs when the session ends"
              style={{ padding: 0 }}
            />
          )}
        </Section>
      )}
    </ScrollView>
  );
}

const CAPTION_MAX = 1000;

/** One line per planned upload while the sheet is in its upload phase. */
type UploadRow = { job: MediaJob; state: UploadState };

/**
 * Visibility, a caption, whether the whole analysis travels with the post (the feed shows
 * headline + summary either way), up to six photos and one voice note.
 *
 * Two phases. `compose` is the form. Post creates the row first — the post exists the
 * moment the server answers, media or not — then `upload` runs the plan against it one
 * object at a time and shows each line's state. A failed line can be retried; a presign
 * 503 (object storage not configured on this server) is said once, in plain words, and
 * the post stands without its media rather than being rolled back.
 */
function ComposeModal({
  visible,
  sessionId,
  hasAnalysis,
  onClose,
  onPosted,
}: {
  visible: boolean;
  sessionId: string;
  hasAnalysis: boolean;
  onClose: () => void;
  onPosted: (post: FeedItem) => void;
}) {
  const [visibility, setVisibility] = useState<Visibility>('followers');
  const [caption, setCaption] = useState('');
  const [shareAnalysis, setShareAnalysis] = useState(false);
  const [photos, setPhotos] = useState<PickedPhoto[]>([]);
  const [audio, setAudio] = useState<RecordedAudio | null>(null);
  const [busy, setBusy] = useState(false);
  const [created, setCreated] = useState<FeedItem | null>(null);
  const [rows, setRows] = useState<UploadRow[]>([]);
  const saidUnconfigured = useRef(false);

  // The sheet stays mounted between openings. Reopening it (after deleting the post, say)
  // must start a fresh compose, not land in the previous post's upload list.
  useEffect(() => {
    if (!visible) return;
    setCreated(null);
    setRows([]);
    setPhotos([]);
    setAudio(null);
    setBusy(false);
    saidUnconfigured.current = false;
  }, [visible]);

  const setRow =(i: number, state: UploadState) =>
    setRows((prev) => prev.map((r, k) => (k === i ? { ...r, state } : r)));

  const run = async (post: FeedItem, indices: number[], all: UploadRow[]) => {
    setBusy(true);
    try {
      const jobs = indices.map((i) => all[i]!.job);
      await runUploads(post.id, jobs, (k, state) => {
        const i = indices[k]!;
        setRow(i, state);
        if (state.phase === 'failed' && state.unconfigured && !saidUnconfigured.current) {
          saidUnconfigured.current = true;
          Alert.alert('Posted without media', PRESIGN_UNCONFIGURED_MESSAGE);
        }
      });
    } finally {
      setBusy(false);
    }
  };

  const submit = async () => {
    if (busy) return;
    let jobs: MediaJob[];
    try {
      jobs = mediaPlan(photos, audio);
    } catch (e) {
      Alert.alert('Check the media', e instanceof Error ? e.message : 'try again');
      return;
    }
    setBusy(true);
    let p: FeedItem;
    try {
      p = await api.createPost({
        session_id: sessionId,
        caption: caption.trim() || null,
        visibility,
        share_analysis: shareAnalysis,
      });
      void rememberMyHandle(p.author.handle);
    } catch (e) {
      setBusy(false);
      Alert.alert('Could not post', e instanceof Error ? e.message : 'try again');
      return;
    }
    if (jobs.length === 0) {
      setBusy(false);
      onPosted(p);
      return;
    }
    const all: UploadRow[] = jobs.map((job) => ({ job, state: { phase: 'queued' } }));
    setCreated(p);
    setRows(all);
    await run(
      p,
      all.map((_, i) => i),
      all
    );
  };

  const retry = () => {
    if (!created || busy) return;
    const failed = rows
      .map((r, i) => (r.state.phase === 'failed' && !r.state.unconfigured ? i : -1))
      .filter((i) => i >= 0);
    if (failed.length === 0) return;
    void run(created, failed, rows);
  };

  const finish = async () => {
    if (!created) return;
    // Re-read so the "Posted" row and any feed cache carry the attached media with urls.
    let final = created;
    try {
      final = await api.post(created.id);
    } catch {
      // The post exists either way; the feed will show the media on its next refresh.
    }
    onPosted(final);
  };

  const uploading = created !== null;
  const allDone = uploading && rows.every((r) => r.state.phase === 'done');
  const retryable = rows.some((r) => r.state.phase === 'failed' && !r.state.unconfigured);

  // Every upload landed: close on its own. Anything else waits for a tap so the failures
  // are read rather than flashed.
  useEffect(() => {
    if (allDone && !busy) void finish();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [allDone, busy]);

  return (
    <Modal
      visible={visible}
      animationType="slide"
      presentationStyle="pageSheet"
      onRequestClose={() => (uploading ? void finish() : onClose())}
    >
      <ScrollView
        style={{ flex: 1, backgroundColor: c.bg }}
        contentContainerStyle={{ padding: space.md, paddingBottom: space.xxl }}
        keyboardShouldPersistTaps="handled"
      >
        <View style={{ flexDirection: 'row', alignItems: 'center', marginBottom: space.lg }}>
          {uploading ? (
            <View style={{ width: 48 }} />
          ) : (
            <Pressable onPress={onClose} hitSlop={8} disabled={busy}>
              <Text style={{ color: c.textDim, fontSize: 15 }}>Cancel</Text>
            </Pressable>
          )}
          <Text style={{ color: c.text, fontSize: 17, fontWeight: '700', flex: 1, textAlign: 'center' }}>
            {uploading ? 'Uploading' : 'Post session'}
          </Text>
          {uploading ? (
            <Pressable onPress={() => void finish()} disabled={busy} hitSlop={8}>
              <Text style={{ color: c.accent, fontSize: 15, fontWeight: '700', opacity: busy ? 0.5 : 1 }}>
                Done
              </Text>
            </Pressable>
          ) : (
            <Pressable onPress={() => void submit()} disabled={busy} hitSlop={8}>
              <Text style={{ color: c.accent, fontSize: 15, fontWeight: '700', opacity: busy ? 0.5 : 1 }}>
                {busy ? 'Posting…' : 'Post'}
              </Text>
            </Pressable>
          )}
        </View>

        {uploading ? (
          <View>
            <Text style={{ color: c.textDim, fontSize: 13, lineHeight: 18, marginBottom: space.md }}>
              Your post is up. Its photos and voice note are on their way.
            </Text>
            {rows.map((r, i) => (
              <UploadLine key={i} row={r} />
            ))}
            {!busy && retryable && (
              <Pressable
                onPress={retry}
                style={({ pressed }) => [
                  {
                    backgroundColor: c.accent,
                    borderRadius: 12,
                    paddingVertical: space.md,
                    alignItems: 'center',
                    marginTop: space.md,
                  },
                  pressed && { opacity: 0.8 },
                ]}
              >
                <Text style={{ color: c.onAccent, fontWeight: '700', fontSize: 15 }}>Retry failed uploads</Text>
              </Pressable>
            )}
          </View>
        ) : (
          <>
            <Text style={composeLabel}>WHO CAN SEE IT</Text>
            <View style={{ flexDirection: 'row', backgroundColor: c.card, borderRadius: 10, padding: 3 }}>
              {VISIBILITIES.map((v) => (
                <Pressable
                  key={v}
                  onPress={() => setVisibility(v)}
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

            <Text style={[composeLabel, { marginTop: space.lg }]}>CAPTION</Text>
            <TextInput
              value={caption}
              onChangeText={(t) => setCaption(t.slice(0, CAPTION_MAX))}
              placeholder="What did you build?"
              placeholderTextColor={c.textDim}
              multiline
              style={{
                color: c.text,
                backgroundColor: c.card,
                borderRadius: 10,
                padding: space.md,
                minHeight: 96,
                fontSize: 15,
                textAlignVertical: 'top',
              }}
            />
            <Text style={{ color: c.textDim, fontSize: 11, textAlign: 'right', marginTop: 4 }}>
              {caption.length}/{CAPTION_MAX}
            </Text>

            <View style={{ marginTop: space.lg }}>
              <MediaPicker
                photos={photos}
                onPhotos={setPhotos}
                audio={audio}
                onAudio={setAudio}
                disabled={busy}
              />
            </View>

            <View style={{ flexDirection: 'row', alignItems: 'center', marginTop: space.lg, backgroundColor: c.card, borderRadius: 10, padding: space.md }}>
              <View style={{ flex: 1 }}>
                <Text style={{ color: c.text, fontSize: 15 }}>Include full analysis</Text>
                <Text style={{ color: c.textDim, fontSize: 12, marginTop: 2 }}>
                  {hasAnalysis
                    ? 'Off shares only the headline and summary.'
                    : 'This session has no analysis yet.'}
                </Text>
              </View>
              <Switch
                value={shareAnalysis}
                onValueChange={setShareAnalysis}
                disabled={!hasAnalysis}
                trackColor={{ true: c.accent }}
              />
            </View>
          </>
        )}
      </ScrollView>
    </Modal>
  );
}

/** One upload's line: a thumbnail or the note's length, and where it is. */
function UploadLine({ row }: { row: UploadRow }) {
  const { job, state } = row;
  const what =
    job.kind === 'photo' ? `Photo ${job.index + 1}` : `Voice note · ${formatClock(job.duration_ms)}`;
  const status =
    state.phase === 'queued'
      ? 'waiting'
      : state.phase === 'uploading'
        ? 'uploading…'
        : state.phase === 'done'
          ? 'done'
          : state.unconfigured
            ? 'not configured'
            : `failed · ${state.message}`;
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
        <View style={{ width: 40, height: 40, borderRadius: 6, backgroundColor: c.border, alignItems: 'center', justifyContent: 'center' }}>
          <Text style={{ color: c.text, fontSize: 16 }}>▶</Text>
        </View>
      )}
      <Text style={{ color: c.text, fontSize: 14, flex: 1 }}>{what}</Text>
      {state.phase === 'uploading' ? (
        <ActivityIndicator color={c.accent} />
      ) : (
        <Text
          style={{ color: failed ? c.danger : state.phase === 'done' ? c.accent : c.textDim, fontSize: 12, fontWeight: '600', maxWidth: 160 }}
          numberOfLines={2}
        >
          {status}
        </Text>
      )}
    </View>
  );
}

const postedBox = {
  backgroundColor: c.card,
  borderRadius: 12,
  paddingVertical: space.md,
  paddingHorizontal: space.md,
  marginTop: space.sm,
} as const;

const composeLabel = {
  color: c.textDim,
  fontSize: 11,
  fontWeight: '700',
  letterSpacing: 0.8,
  marginBottom: space.sm,
} as const;

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <View style={{ marginTop: space.lg }}>
      <Text
        style={{
          color: c.textDim,
          fontSize: 11,
          fontWeight: '700',
          letterSpacing: 0.8,
          marginBottom: space.sm,
        }}
      >
        {title.toUpperCase()}
      </Text>
      <View style={{ backgroundColor: c.card, borderRadius: 12, padding: space.md }}>
        {children}
      </View>
    </View>
  );
}

function Row({ label, value, dim }: { label: string; value: string; dim?: boolean }) {
  return (
    <View style={{ flexDirection: 'row', justifyContent: 'space-between', paddingVertical: 6 }}>
      <Text style={{ color: c.textDim, fontSize: 14 }}>{label}</Text>
      <Text style={{ color: dim ? c.textDim : c.text, fontSize: 14, fontWeight: dim ? '400' : '600' }}>
        {value}
      </Text>
    </View>
  );
}

function LegendItem({
  klass,
  label,
  share,
}: {
  klass: StripClass;
  label: string;
  share: Record<StripClass, number> | null;
}) {
  const pct = share ? Math.round(share[klass] * 100) : null;
  return (
    <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
      <View
        style={{ width: 18, height: 10, borderRadius: 2, backgroundColor: c.strip[klass] }}
      />
      <Text style={{ color: c.textDim, fontSize: 12 }}>
        {label}
        {pct !== null ? ` ${pct}%` : ''}
      </Text>
    </View>
  );
}
