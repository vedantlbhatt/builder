import { useLocalSearchParams } from 'expo-router';
import React, { useEffect, useRef, useState } from 'react';
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
import type { FeedItem, SessionDetail, Visibility } from '../../src/data/api';
import * as cache from '../../src/data/cache';
import { api, SAMPLE_SESSION } from '../../src/data/client';
import { VISIBILITIES, visibilityLabel } from '../../src/social/format';
import { rememberMyHandle } from '../../src/social/identity';
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
      <View style={{ flex: 1, justifyContent: 'center', backgroundColor: c.bg }}>
        <ActivityIndicator color={c.accent} />
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
        <Text style={{ color: '#1C1917', fontWeight: '700', fontSize: 16 }}>
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
              <Text style={{ color: '#E5484D', fontWeight: '600', fontSize: 14 }}>Delete</Text>
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
          {/* Quiet, and no spinner: nothing is loading. A final session without an analysis
              will not grow one by waiting. */}
          <Text style={{ color: c.textDim, fontSize: 13 }}>
            {state === 'final'
              ? 'Analysis not available for this session'
              : 'Analysis arrives when the session finishes, or at the next checkpoint while it runs unattended.'}
          </Text>
        </Section>
      )}
    </ScrollView>
  );
}

const CAPTION_MAX = 1000;

/**
 * Visibility, a caption, and whether the whole analysis travels with the post (the feed
 * shows headline + summary either way). Photos are deliberately absent from this pass:
 * TODO wire an image picker to `api.presignMedia` → PUT → `api.attachMedia`, downscaling
 * to a 2048 long edge first; the server answers 503 with a plain sentence until object
 * storage is configured.
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
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    if (busy) return;
    setBusy(true);
    try {
      const p = await api.createPost({
        session_id: sessionId,
        caption: caption.trim() || null,
        visibility,
        share_analysis: shareAnalysis,
      });
      void rememberMyHandle(p.author.handle);
      onPosted(p);
    } catch (e) {
      Alert.alert('Could not post', e instanceof Error ? e.message : 'try again');
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal visible={visible} animationType="slide" presentationStyle="pageSheet" onRequestClose={onClose}>
      <ScrollView
        style={{ flex: 1, backgroundColor: c.bg }}
        contentContainerStyle={{ padding: space.md, paddingBottom: space.xxl }}
        keyboardShouldPersistTaps="handled"
      >
        <View style={{ flexDirection: 'row', alignItems: 'center', marginBottom: space.lg }}>
          <Pressable onPress={onClose} hitSlop={8}>
            <Text style={{ color: c.textDim, fontSize: 15 }}>Cancel</Text>
          </Pressable>
          <Text style={{ color: c.text, fontSize: 17, fontWeight: '700', flex: 1, textAlign: 'center' }}>
            Post session
          </Text>
          <Pressable onPress={() => void submit()} disabled={busy} hitSlop={8}>
            <Text style={{ color: c.accent, fontSize: 15, fontWeight: '700', opacity: busy ? 0.5 : 1 }}>
              {busy ? 'Posting…' : 'Post'}
            </Text>
          </Pressable>
        </View>

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
              <Text style={{ color: v === visibility ? '#1C1917' : c.text, fontWeight: '600', fontSize: 14 }}>
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
