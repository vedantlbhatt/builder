import { useLocalSearchParams } from 'expo-router';
import React, { useCallback, useEffect, useState } from 'react';
import {
  Alert,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  ScrollView,
  Text,
  TextInput,
  View,
} from 'react-native';

import type { Comment, FeedItem } from '../../src/data/api';
import { api } from '../../src/data/client';
import { PixelBadge } from '../../src/pixel/PixelBadge';
import { PostRow } from '../../src/social/FeedList';
import { applyKudos, authorName, relativeTime, toggleKudos } from '../../src/social/format';
import { myHandle, rememberMyHandle } from '../../src/social/identity';
import { colors, hitSlopToReach, space } from '../../src/theme';

const c = colors('dark');
const COMMENT_MAX = 500;

/**
 * One post: the card, its comments oldest-first, and a composer.
 *
 * "Own" for a comment is decided by handle, against the one `social/identity.ts`
 * remembers. Until the app has learned it, comments simply lack the delete button; the
 * server's owner-only policy is the real gate either way.
 */
export default function PostScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const [post, setPost] = useState<FeedItem | null>(null);
  const [comments, setComments] = useState<Comment[] | null>(null);
  const [me, setMe] = useState<string | null>(null);
  const [draft, setDraft] = useState('');
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!id) return;
    try {
      const [p, cs] = await Promise.all([api.post(id), api.comments(id)]);
      setPost(p);
      setComments(cs.comments);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'could not load this post');
      setComments((prev) => prev ?? []);
    }
  }, [id]);

  useEffect(() => {
    void load();
    void myHandle().then((h) => setMe((prev) => prev ?? h));
  }, [load]);

  const onKudos = useCallback(async (item: FeedItem) => {
    const guessed = toggleKudos(item);
    setPost(guessed);
    try {
      const state = guessed.you_kudosed ? await api.kudos(item.id) : await api.unkudos(item.id);
      setPost((p) => (p ? applyKudos(p, state) : p));
    } catch {
      setPost(item);
    }
  }, []);

  const send = useCallback(async () => {
    const body = draft.trim();
    if (!body || !id || sending) return;
    setSending(true);
    try {
      const created = await api.addComment(id, body);
      setComments((prev) => [...(prev ?? []), created]);
      setPost((p) => (p ? { ...p, comment_count: created.comment_count } : p));
      setDraft('');
      // The first comment tells us who we are, if nothing else has yet.
      if (!me && created.author.handle) {
        setMe(created.author.handle);
        void rememberMyHandle(created.author.handle);
      }
    } catch (e) {
      Alert.alert('Could not comment', e instanceof Error ? e.message : 'try again');
    } finally {
      setSending(false);
    }
  }, [draft, id, me, sending]);

  const remove = useCallback(
    (cm: Comment) => {
      Alert.alert('Delete comment?', undefined, [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Delete',
          style: 'destructive',
          onPress: async () => {
            const before = comments ?? [];
            setComments(before.filter((x) => x.id !== cm.id));
            setPost((p) => (p ? { ...p, comment_count: Math.max(0, p.comment_count - 1) } : p));
            try {
              await api.deleteComment(cm.id);
            } catch (e) {
              setComments(before);
              setPost((p) => (p ? { ...p, comment_count: p.comment_count + 1 } : p));
              Alert.alert('Could not delete', e instanceof Error ? e.message : 'try again');
            }
          },
        },
      ]);
    },
    [comments]
  );

  if (!post && !error) {
    return (
      <View style={{ flex: 1, justifyContent: 'center', backgroundColor: c.bg, paddingHorizontal: space.md }}>
        <PixelBadge state="thinking" text="Loading the post…" />
      </View>
    );
  }

  return (
    <KeyboardAvoidingView
      style={{ flex: 1, backgroundColor: c.bg }}
      behavior={Platform.OS === 'ios' ? 'padding' : undefined}
      keyboardVerticalOffset={88}
    >
      <ScrollView contentContainerStyle={{ padding: space.md, paddingBottom: space.xl }}>
        {error && (
          <View style={banner}>
            <Text style={{ color: c.textDim, fontSize: 13 }}>{error}</Text>
          </View>
        )}
        {post && <PostRow item={post} onKudos={onKudos} linkToPost={false} photoLayout="full" />}

        <Text style={sectionTitle}>COMMENTS</Text>
        {comments && comments.length === 0 && (
          <Text style={{ color: c.textDim, fontSize: 13 }}>No comments yet.</Text>
        )}
        {(comments ?? []).map((cm) => (
          <View key={cm.id} style={{ paddingVertical: space.sm, borderBottomWidth: 1, borderBottomColor: c.border }}>
            <View style={{ flexDirection: 'row', alignItems: 'baseline', gap: 6 }}>
              <Text style={{ color: c.text, fontWeight: '600', fontSize: 13 }}>{authorName(cm.author)}</Text>
              <Text style={{ color: c.textDim, fontSize: 12 }}>· {relativeTime(cm.created_at)}</Text>
              <View style={{ flex: 1 }} />
              {me !== null && cm.author.handle === me && (
                <Pressable onPress={() => remove(cm)} hitSlop={hitSlopToReach(16)} accessibilityRole="button">
                  <Text style={{ color: c.textDim, fontSize: 12 }}>Delete</Text>
                </Pressable>
              )}
            </View>
            <Text style={{ color: c.text, fontSize: 14, lineHeight: 19, marginTop: 2 }}>{cm.body}</Text>
          </View>
        ))}
      </ScrollView>

      <View style={{ flexDirection: 'row', gap: space.sm, padding: space.md, borderTopWidth: 1, borderTopColor: c.border, backgroundColor: c.bg }}>
        <TextInput
          value={draft}
          onChangeText={(t) => setDraft(t.slice(0, COMMENT_MAX))}
          placeholder="Say something"
          placeholderTextColor={c.textDim}
          multiline
          style={{
            flex: 1,
            color: c.text,
            backgroundColor: c.card,
            borderRadius: 12,
            paddingHorizontal: space.md,
            paddingVertical: space.sm,
            maxHeight: 120,
            fontSize: 14,
          }}
        />
        <Pressable
          onPress={() => void send()}
          disabled={!draft.trim() || sending}
          style={({ pressed }) => [
            {
              backgroundColor: c.accent,
              borderRadius: 12,
              paddingHorizontal: space.md,
              justifyContent: 'center',
              opacity: !draft.trim() || sending ? 0.5 : pressed ? 0.8 : 1,
            },
          ]}
        >
          <Text style={{ color: c.onAccent, fontWeight: '700' }}>Send</Text>
        </Pressable>
      </View>
    </KeyboardAvoidingView>
  );
}

const sectionTitle = {
  color: c.textDim,
  fontSize: 11,
  fontWeight: '700',
  letterSpacing: 0.8,
  marginTop: space.lg,
  marginBottom: space.sm,
} as const;

const banner = {
  backgroundColor: c.card,
  borderRadius: 12,
  borderWidth: 1,
  borderColor: c.textDim,
  padding: space.md,
  marginBottom: space.md,
} as const;
