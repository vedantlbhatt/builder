import { useLocalSearchParams, useRouter } from 'expo-router';
import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  ActivityIndicator,
  Alert,
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
  ApiError,
  type FeedItem,
  type SessionDetail,
  type Visibility,
} from '../../src/data/api';
import * as cache from '../../src/data/cache';
import { api, SAMPLE_SESSION } from '../../src/data/client';
import { RecapSheet } from '../../src/recap/RecapSheet';
import { CAPTION_MAX, detailAfterPost, planMedia } from '../../src/social/composeFlow';
import {
  isAlreadySharedConflict,
  VISIBILITIES,
  visibilityLabel,
} from '../../src/social/format';
import type { PickedPhoto, RecordedAudio } from '../../src/social/media';
import { MediaPicker } from '../../src/social/MediaPicker';
import { PixelBadge } from '../../src/pixel/PixelBadge';
import { UploadList } from '../../src/social/UploadLine';
import { useUploadFlow } from '../../src/social/useUploadFlow';
import { TimelineStrip } from '../../src/strip/TimelineStrip';
import { classShare, decodeColumns, decodeMarks } from '../../src/strip/decode';
import { StripClass } from '../../src/generated/strip';
import { colors, compactNumber, duration, hitSlopToReach, space } from '../../src/theme';

const c = colors('dark');

/**
 * The viewer's own post for this session, from the id the detail carries. `post_id` is the
 * server's answer for ANY visibility — a private post too, which `is_shared` deliberately
 * leaves down — and null when there is none. The row itself (visibility, Delete) comes
 * from GET /v1/posts/{id}. A server older than the field omits it; then `is_shared` alone
 * decides, and the row reads "posted" without a Delete.
 */
function sharedFromDetail(s: SessionDetail): boolean {
  if (s.post_id === undefined) return s.is_shared;
  return s.post_id !== null;
}

export default function SessionScreen() {
  const { id, recap } = useLocalSearchParams<{ id: string; recap?: string }>();
  const { width } = useWindowDimensions();
  const router = useRouter();
  const [model, setModel] = useState<CardModel | null>(null);
  const [session, setSession] = useState<SessionDetail | null>(null);
  const [sharing, setSharing] = useState(false);
  const [composing, setComposing] = useState(false);
  const [recapOpen, setRecapOpen] = useState(false);
  const [post, setPost] = useState<FeedItem | null>(null);
  // Whether a post exists for this session, as far as the phone knows: seeded from the
  // server's `is_shared` (true for a followers/public post; a private post leaves it
  // down), raised by a 409 "already shared", lowered by Delete. `post` is the row itself
  // when this mount composed it or the lookup below found it.
  const [shared, setShared] = useState(false);
  const [looking, setLooking] = useState(false);
  const [lookupFailed, setLookupFailed] = useState(false);
  // The last detail load could not reach the server. The screen renders from the cache
  // and the recap says why Post is off, rather than failing at the tap.
  const [offline, setOffline] = useState(false);
  const cardRef = useRef(null);

  const load = useCallback(async () => {
    const show = (s: SessionDetail, code: string) => {
      setSession(s);
      setModel(toCardModel(s, code));
      setShared(sharedFromDetail(s));
    };
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
      setOffline(false);
    } catch (e) {
      // Offline with a cached copy is fine; offline without one shows the spinner. Only
      // a transport failure means "offline" — a 404 or a 500 is the server answering.
      setOffline(e instanceof ApiError && e.status === 0);
    }
  }, [id]);

  useEffect(() => {
    void load();
  }, [load]);

  // A session posted on a previous visit, after a restart, or from another device: the
  // detail names the post and this mount does not hold it. Load the row, so it can carry
  // its visibility and Delete rather than "Post to the feed" and a 409.
  const postId = session?.post_id ?? null;
  useEffect(() => {
    if (!postId || post?.id === postId || !id || id === 'sample') return;
    let cancelled = false;
    setLooking(true);
    setLookupFailed(false);
    api
      .post(postId)
      .then((found) => {
        if (!cancelled) setPost(found);
      })
      .catch(() => {
        // The row still says "posted"; only the Delete is missing, and the feed has it.
        if (!cancelled) setLookupFailed(true);
      })
      .finally(() => {
        if (!cancelled) setLooking(false);
      });
    return () => {
      cancelled = true;
    };
  }, [postId, post, id]);

  // `?recap=1` — a tapped completion push, the Mac's link, the list's chip — raises the
  // recap once the session is here and, when it has a post, once that post is here too,
  // so the sheet opens as an editor rather than posting twice. Once per mount: closing
  // it must not reopen it on the next render.
  const autoOpened = useRef(false);
  const postPending = Boolean(postId) && post?.id !== postId && !lookupFailed;
  useEffect(() => {
    if (recap !== '1' || autoOpened.current || !session || id === 'sample') return;
    if ((session.state ?? 'final') !== 'final') return;
    if (postPending) return;
    autoOpened.current = true;
    setRecapOpen(true);
  }, [recap, session, id, postPending]);

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

  /** A post landed, from either sheet: remember it, then show it. */
  const landed = (p: FeedItem, thenOpen: boolean) => {
    setPost(p);
    setShared(true);
    setComposing(false);
    setRecapOpen(false);
    const next = detailAfterPost(session, p);
    setSession(next);
    void cache.putDetail(next).catch(() => undefined);
    if (thenOpen) router.push(`/post/${p.id}`);
  };

  /**
   * A private post, or one made between the detail load and now: the server says it
   * exists. Switch to the posted state and re-read the detail for its id, which the
   * lookup above turns into the row.
   */
  const alreadyPosted = () => {
    setShared(true);
    setComposing(false);
    setRecapOpen(false);
    if (id && id !== 'sample') {
      void api
        .session(id)
        .then(async (fresh) => {
          setSession(fresh);
          await cache.putDetail(fresh);
        })
        .catch(() => undefined);
    }
  };

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
          <View style={[postedBox, { flexDirection: 'row', alignItems: 'center', gap: space.md }]}>
            <Text style={{ color: c.text, fontSize: 14, flex: 1 }}>
              Posted · {visibilityLabel(post.visibility)}
            </Text>
            <Pressable
              hitSlop={hitSlopToReach(18)}
              accessibilityRole="button"
              onPress={() => setRecapOpen(true)}
            >
              <Text style={{ color: c.accent, fontWeight: '600', fontSize: 14 }}>Edit</Text>
            </Pressable>
            <Pressable
              hitSlop={hitSlopToReach(18)}
              accessibilityRole="button"
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
                        setShared(false);
                        // Forget the id too, or the loader above would fetch a deleted post.
                        setSession((cur) => (cur ? { ...cur, post_id: null, is_shared: false } : cur));
                        // The cached detail is what the next visit shows first.
                        void cache.putDetail({ ...session, is_shared: false }).catch(() => undefined);
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
        ) : shared ? (
          // The post exists but this mount does not hold it (an older server sent no id, or
          // the row failed to load). Never offer to post again: the server would
          // answer 409. The feed is where the post — and its Delete — live.
          <View style={[postedBox, { flexDirection: 'row', alignItems: 'center' }]}>
            <Text style={{ color: c.text, fontSize: 14, flex: 1 }}>Posted to the feed</Text>
            {looking ? (
              <ActivityIndicator color={c.accent} />
            ) : (
              <Pressable
                hitSlop={hitSlopToReach(18)}
                accessibilityRole="button"
                onPress={() => router.push('/feed')}
              >
                <Text style={{ color: c.accent, fontWeight: '600', fontSize: 14 }}>Open feed</Text>
              </Pressable>
            )}
          </View>
        ) : (
          <>
            {/* The recap: title, tiles, analysis, photos, then Post. What a tapped
                completion push opens, reachable here for everyone else. */}
            <Pressable
              onPress={() => setRecapOpen(true)}
              accessibilityRole="button"
              style={({ pressed }) => [postedBox, { alignItems: 'center' }, pressed && { opacity: 0.8 }]}
            >
              <Text style={{ color: c.text, fontWeight: '700', fontSize: 15 }}>Finish & share</Text>
              <Text style={{ color: c.textDim, fontSize: 12, marginTop: 2 }}>
                Add photos and a caption, then post — or keep it private.
              </Text>
            </Pressable>
            <Pressable
              onPress={() => setComposing(true)}
              accessibilityRole="button"
              hitSlop={hitSlopToReach(20)}
              style={({ pressed }) => [{ alignItems: 'center', paddingVertical: space.sm }, pressed && { opacity: 0.6 }]}
            >
              <Text style={{ color: c.textDim, fontSize: 13 }}>Quick post without the recap</Text>
            </Pressable>
          </>
        )
      )}

      <RecapSheet
        visible={recapOpen}
        session={session}
        post={post}
        offline={offline}
        onClose={() => setRecapOpen(false)}
        onPosted={(p) => landed(p, true)}
        onAlreadyPosted={alreadyPosted}
        onRetryConnection={() => void load()}
      />

      <ComposeModal
        visible={composing}
        sessionId={session.id}
        hasAnalysis={Boolean(session.analysis)}
        onClose={() => setComposing(false)}
        onPosted={(p) => landed(p, false)}
        onAlreadyPosted={alreadyPosted}
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

/**
 * Visibility, a caption, whether the whole analysis travels with the post (the feed shows
 * headline + summary either way), up to six photos and one voice note.
 *
 * Two phases. `compose` is the form. Post creates the row first — the post exists the
 * moment the server answers, media or not — then the upload phase (`useUploadFlow`,
 * shared with the recap) runs the plan against it one object at a time and shows each
 * line's state. A failed line can be retried; a presign 503 (object storage not
 * configured on this server) is said once, in plain words, and the post stands without
 * its media rather than being rolled back.
 */
function ComposeModal({
  visible,
  sessionId,
  hasAnalysis,
  onClose,
  onPosted,
  onAlreadyPosted,
}: {
  visible: boolean;
  sessionId: string;
  hasAnalysis: boolean;
  onClose: () => void;
  onPosted: (post: FeedItem) => void;
  /** The server answered 409 "already shared": the session has a post this sheet cannot see. */
  onAlreadyPosted: () => void;
}) {
  const [visibility, setVisibility] = useState<Visibility>('followers');
  const [caption, setCaption] = useState('');
  const [shareAnalysis, setShareAnalysis] = useState(false);
  const [photos, setPhotos] = useState<PickedPhoto[]>([]);
  const [audio, setAudio] = useState<RecordedAudio | null>(null);
  const [posting, setPosting] = useState(false);
  const flow = useUploadFlow(onPosted);
  const { reset } = flow;

  // The sheet stays mounted between openings. Reopening it (after deleting the post, say)
  // must start a fresh compose, not land in the previous post's upload list.
  useEffect(() => {
    if (!visible) return;
    setPhotos([]);
    setAudio(null);
    setPosting(false);
    reset();
  }, [visible, reset]);

  const busy = posting || flow.busy;

  const submit = async () => {
    if (busy) return;
    const planned = planMedia(photos, audio);
    if (!planned.ok) {
      Alert.alert('Check the media', planned.message);
      return;
    }
    setPosting(true);
    let p: FeedItem;
    try {
      p = await api.createPost({
        session_id: sessionId,
        caption: caption.trim() || null,
        visibility,
        share_analysis: shareAnalysis,
      });
    } catch (e) {
      setPosting(false);
      if (isAlreadySharedConflict(e)) {
        Alert.alert('Already posted', 'This session is on the feed already.');
        onAlreadyPosted();
        return;
      }
      Alert.alert('Could not post', e instanceof Error ? e.message : 'try again');
      return;
    }
    setPosting(false);
    flow.start(p, planned.jobs);
  };

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
        <View style={{ flexDirection: 'row', alignItems: 'center', marginBottom: space.lg }}>
          {flow.uploading ? (
            <View style={{ width: 48 }} />
          ) : (
            <Pressable onPress={onClose} hitSlop={8} disabled={busy}>
              <Text style={{ color: c.textDim, fontSize: 15 }}>Cancel</Text>
            </Pressable>
          )}
          <Text style={{ color: c.text, fontSize: 17, fontWeight: '700', flex: 1, textAlign: 'center' }}>
            {flow.uploading ? 'Uploading' : 'Post session'}
          </Text>
          {flow.uploading ? (
            <Pressable onPress={() => void flow.finish()} disabled={busy} hitSlop={8}>
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

        {flow.uploading ? (
          <UploadList rows={flow.rows} busy={flow.busy} retryable={flow.retryable} onRetry={flow.retry} />
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
