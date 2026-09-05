import { useRouter } from 'expo-router';
import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  ActivityIndicator,
  FlatList,
  Pressable,
  RefreshControl,
  Text,
  useWindowDimensions,
  View,
} from 'react-native';

import type { Cursor, FeedItem, FeedPage } from '../data/api';
import { api } from '../data/client';
import { PixelBadge } from '../pixel/PixelBadge';
import { TimelineStrip } from '../strip/TimelineStrip';
import { decodeMarks } from '../strip/decode';
import { colors, duration, hitSlopToReach, space } from '../theme';
import { AudioChip } from './AudioChip';
import { PhotoGrid } from './PhotoGrid';
import {
  authorName,
  nextCursor,
  relativeTime,
  repoLine,
  toggleKudos,
  updateKudos,
} from './format';

const c = colors('dark');

/**
 * A page loader: the feed, a faction's feed, or a user's posts. The screen supplies it;
 * this list only knows how to page, refresh, and toggle kudos.
 */
export type PageLoader = (cursor: Cursor | null) => Promise<FeedPage>;

interface Props {
  load: PageLoader;
  /** Rendered above the first row, inside the scroll. */
  header?: React.ReactElement | null;
  emptyText: string;
}

export function FeedList({ load, header = null, emptyText }: Props) {
  const [items, setItems] = useState<FeedItem[] | null>(null);
  const [cursor, setCursor] = useState<Cursor | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Paging guard: onEndReached fires repeatedly while the last page is short, and two
  // concurrent fetches of the same cursor would duplicate rows.
  const busy = useRef(false);

  const fetchFirst = useCallback(async () => {
    if (busy.current) return;
    busy.current = true;
    try {
      const page = await load(null);
      setItems(page.items);
      setCursor(nextCursor(page));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'could not reach the server');
      setItems((prev) => prev ?? []);
    } finally {
      busy.current = false;
    }
  }, [load]);

  const fetchMore = useCallback(async () => {
    if (busy.current || !cursor) return;
    busy.current = true;
    setLoadingMore(true);
    try {
      const page = await load(cursor);
      setItems((prev) => [...(prev ?? []), ...page.items]);
      setCursor(nextCursor(page));
    } catch (e) {
      setError(e instanceof Error ? e.message : 'could not reach the server');
    } finally {
      busy.current = false;
      setLoadingMore(false);
    }
  }, [cursor, load]);

  useEffect(() => {
    void fetchFirst();
  }, [fetchFirst]);

  const onRefresh = useCallback(async () => {
    setRefreshing(true);
    await fetchFirst();
    setRefreshing(false);
  }, [fetchFirst]);

  const onKudos = useCallback(async (item: FeedItem) => {
    // Optimistic: flip now, and let the server's answer replace the guess. On error the
    // kudos fields go back to exactly what they were, not to a second toggle. Every step
    // touches ONLY the two kudos fields of whatever row is in the list now: a
    // pull-to-refresh that lands mid-request has replaced the row, and writing the
    // tap-time snapshot back over it would roll the refresh back.
    const guessed = toggleKudos(item);
    setItems((prev) => (prev ? updateKudos(prev, item.id, guessed) : prev));
    try {
      const state = guessed.you_kudosed ? await api.kudos(item.id) : await api.unkudos(item.id);
      setItems((prev) => (prev ? updateKudos(prev, item.id, state) : prev));
    } catch {
      setItems((prev) => (prev ? updateKudos(prev, item.id, item) : prev));
    }
  }, []);

  if (items === null) {
    return (
      <View style={{ flex: 1, justifyContent: 'center', backgroundColor: c.bg }}>
        <ActivityIndicator color={c.accent} />
      </View>
    );
  }

  return (
    <FlatList
      style={{ flex: 1, backgroundColor: c.bg }}
      contentContainerStyle={{ padding: space.md, paddingBottom: space.xxl }}
      data={items}
      keyExtractor={(it) => it.id}
      renderItem={({ item }) => <PostRow item={item} onKudos={onKudos} />}
      ListHeaderComponent={
        <>
          {header}
          {error && (
            <View style={banner}>
              <Text style={{ color: c.textDim, fontSize: 13 }}>{error}</Text>
            </View>
          )}
        </>
      }
      ListEmptyComponent={
        <PixelBadge state="waving" text={emptyText} style={{ paddingVertical: space.xl }} />
      }
      ListFooterComponent={
        loadingMore ? (
          <ActivityIndicator color={c.accent} style={{ marginVertical: space.md }} />
        ) : null
      }
      onEndReached={() => {
        void fetchMore();
      }}
      onEndReachedThreshold={0.6}
      refreshControl={
        <RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={c.accent} />
      }
    />
  );
}

/**
 * One recap card in the feed: author · time · repo, the strip, the analysis headline and
 * summary when the author has one, the caption, and the two counters. It is the whole
 * story of a session in one screen-width, which is the difference between a feed and a
 * log.
 */
export function PostRow({
  item,
  onKudos,
  linkToPost = true,
  photoLayout = 'grid',
}: {
  item: FeedItem;
  onKudos: (item: FeedItem) => void;
  linkToPost?: boolean;
  /** `grid` in the feed (square thumbnails); `full` on the post screen. */
  photoLayout?: 'grid' | 'full';
}) {
  const { width } = useWindowDimensions();
  const router = useRouter();
  const stripWidth = width - space.md * 4;
  const repo = repoLine(item);
  const s = item.session;
  const headline = item.analysis?.headline ?? null;
  const summary = item.analysis?.summary ?? null;

  return (
    <View style={row}>
      <View style={{ flexDirection: 'row', alignItems: 'baseline', gap: 6, flexWrap: 'wrap' }}>
        <Pressable
          onPress={() => item.author.handle && router.push(`/u/${item.author.handle}`)}
          hitSlop={6}
        >
          <Text style={{ color: c.text, fontWeight: '600', fontSize: 14 }}>
            {authorName(item.author)}
          </Text>
        </Pressable>
        <Text style={meta}>· {relativeTime(item.created_at)}</Text>
        {repo && <Text style={meta}>· {repo}</Text>}
        <View style={{ flex: 1 }} />
        <Text style={[meta, { fontVariant: ['tabular-nums'] }]}>
          {duration(s.attended_seconds ?? s.active_seconds)}
        </Text>
      </View>

      {item.strip ? (
        <TimelineStrip
          cols={item.strip.cols}
          marks={decodeMarks(item.strip.marks)}
          spanMs={Math.max(1, item.strip.t1_ms - item.strip.t0_ms)}
          preset="row"
          width={stripWidth}
          style={{ marginVertical: space.sm }}
        />
      ) : (
        <Text style={[meta, { fontSize: 11, marginVertical: space.sm }]}>
          timeline not available for this session
        </Text>
      )}

      {headline ? (
        <Text style={{ color: c.text, fontSize: 17, fontWeight: '700', lineHeight: 22 }}>
          {headline}
        </Text>
      ) : s.title ? (
        <Text style={{ color: c.text, fontSize: 15, fontWeight: '600' }} numberOfLines={2}>
          {s.title}
        </Text>
      ) : null}
      {summary ? (
        <Text style={{ color: c.textDim, fontSize: 13, lineHeight: 18, marginTop: 4 }}>
          {summary}
        </Text>
      ) : null}
      {item.caption ? (
        <Text style={{ color: c.text, fontSize: 14, lineHeight: 19, marginTop: space.sm }}>
          {item.caption}
        </Text>
      ) : null}
      <PhotoGrid
        photos={item.photos}
        width={stripWidth}
        layout={photoLayout}
        style={{ marginTop: space.sm }}
      />
      {item.audio && (
        <AudioChip
          uri={item.audio.url}
          durationMs={item.audio.duration_ms}
          style={{ marginTop: space.sm }}
        />
      )}

      <View style={{ flexDirection: 'row', gap: space.md, marginTop: space.md, alignItems: 'center' }}>
        <Pressable
          onPress={() => onKudos(item)}
          hitSlop={COUNTER_HIT_SLOP}
          accessibilityRole="button"
          style={({ pressed }) => [counter, item.you_kudosed && counterOn, pressed && { opacity: 0.6 }]}
        >
          <Text style={{ color: item.you_kudosed ? c.onAccent : c.text, fontSize: 13, fontWeight: '600' }}>
            {item.you_kudosed ? 'Kudos given' : 'Kudos'} · {item.kudos_count}
          </Text>
        </Pressable>
        <Pressable
          onPress={() => linkToPost && router.push(`/post/${item.id}`)}
          hitSlop={COUNTER_HIT_SLOP}
          accessibilityRole="button"
          style={({ pressed }) => [counter, pressed && { opacity: 0.6 }]}
        >
          <Text style={{ color: c.text, fontSize: 13, fontWeight: '600' }}>
            {item.comment_count} comment{item.comment_count === 1 ? '' : 's'}
          </Text>
        </Pressable>
        {item.visibility !== 'public' && (
          <Text style={meta}>{item.visibility === 'private' ? 'only you' : 'followers'}</Text>
        )}
      </View>
    </View>
  );
}

const row = {
  backgroundColor: c.card,
  borderRadius: 12,
  padding: space.md,
  marginBottom: space.sm,
} as const;

const meta = { color: c.textDim, fontSize: 12 } as const;

/** The pills are 32pt tall; the slop takes the tap target to 44 without fattening them. */
const COUNTER_HEIGHT = 32;
const COUNTER_HIT_SLOP = hitSlopToReach(COUNTER_HEIGHT);

const counter = {
  borderRadius: 999,
  borderWidth: 1,
  borderColor: c.border,
  paddingHorizontal: space.md,
  minHeight: COUNTER_HEIGHT,
  justifyContent: 'center',
} as const;

const counterOn = { backgroundColor: c.accent, borderColor: c.accent } as const;

const banner = {
  backgroundColor: c.card,
  borderRadius: 12,
  borderWidth: 1,
  borderColor: c.textDim,
  padding: space.md,
  marginBottom: space.md,
} as const;
