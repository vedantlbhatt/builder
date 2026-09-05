import { Stack, useLocalSearchParams } from 'expo-router';
import React, { useCallback } from 'react';

import type { Cursor } from '../src/data/api';
import { api } from '../src/data/client';
import { FeedList } from '../src/social/FeedList';

/**
 * The feed. With `?slug=` it is one faction's feed instead — same shape, same list, so
 * there is one screen to keep honest rather than two that drift.
 */
export default function FeedScreen() {
  const { slug } = useLocalSearchParams<{ slug?: string }>();

  const load = useCallback(
    (cursor: Cursor | null) =>
      slug ? api.factionFeed(slug, cursor ?? undefined) : api.feed(cursor ?? undefined),
    [slug]
  );

  return (
    <>
      <Stack.Screen options={{ title: slug ? slug : 'Feed' }} />
      <FeedList
        load={load}
        emptyText={
          slug
            ? 'Nothing shared in this faction yet.'
            : 'Follow a builder or join a faction to fill this up.'
        }
      />
    </>
  );
}
