import { Stack, useLocalSearchParams } from 'expo-router';
import React, { useCallback, useEffect, useState } from 'react';
import { Alert, Pressable, Text, View } from 'react-native';

import type { Cursor, FollowState, UserPage } from '../../src/data/api';
import { api } from '../../src/data/client';
import { FeedList } from '../../src/social/FeedList';
import { colors, hitSlopToReach, space } from '../../src/theme';

const c = colors('dark');
/** The follow pill's height; the slop takes the tap target to 44 without a taller pill. */
const FOLLOW_HEIGHT = 34;

/**
 * Another builder's page: profile header with a follow button, then the posts the viewer
 * may see. `/v1/users/{handle}` returns both in one response; the list's loader keeps the
 * profile from the first page and pages the posts with the same cursor pair as the feed.
 */
export default function UserScreen() {
  const { handle } = useLocalSearchParams<{ handle: string }>();
  const [profile, setProfile] = useState<UserPage['profile'] | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(
    async (cursor: Cursor | null) => {
      const page = await api.user(handle!, cursor ?? undefined);
      if (!cursor) {
        setProfile(page.profile);
      }
      return { items: page.posts, next_before: page.next_before, next_before_id: page.next_before_id };
    },
    [handle]
  );

  useEffect(() => {
    setProfile(null);
  }, [handle]);

  const toggleFollow = useCallback(async () => {
    if (!profile || busy) return;
    setBusy(true);
    const was: FollowState = profile.follow_state;
    try {
      if (was) {
        await api.unfollow(profile.handle);
        setProfile({ ...profile, follow_state: null });
      } else {
        const r = await api.follow(profile.handle);
        setProfile({ ...profile, follow_state: r.state });
      }
    } catch (e) {
      Alert.alert('Could not update follow', e instanceof Error ? e.message : 'try again');
    } finally {
      setBusy(false);
    }
  }, [busy, profile]);

  const header = profile ? (
    <View style={{ backgroundColor: c.card, borderRadius: 12, padding: space.md, marginBottom: space.md }}>
      <Text style={{ color: c.text, fontSize: 22, fontWeight: '700' }}>
        {profile.display_name ?? profile.handle}
      </Text>
      <Text style={{ color: c.textDim, fontSize: 13 }}>
        @{profile.handle}
        {profile.profile_public ? '' : ' · private'}
      </Text>
      {!profile.is_you && (
        <Pressable
          onPress={() => void toggleFollow()}
          disabled={busy}
          hitSlop={hitSlopToReach(FOLLOW_HEIGHT)}
          accessibilityRole="button"
          style={({ pressed }) => [
            {
              marginTop: space.md,
              alignSelf: 'flex-start',
              borderRadius: 999,
              paddingHorizontal: space.md,
              minHeight: FOLLOW_HEIGHT,
              justifyContent: 'center',
              backgroundColor: profile.follow_state ? c.bg : c.accent,
              borderWidth: 1,
              borderColor: profile.follow_state ? c.border : c.accent,
              opacity: busy ? 0.6 : pressed ? 0.8 : 1,
            },
          ]}
        >
          <Text style={{ color: profile.follow_state ? c.text : c.onAccent, fontWeight: '700', fontSize: 13 }}>
            {profile.follow_state === 'accepted'
              ? 'Following'
              : profile.follow_state === 'pending'
                ? 'Requested'
                : 'Follow'}
          </Text>
        </Pressable>
      )}
    </View>
  ) : null;

  return (
    <>
      <Stack.Screen options={{ title: handle ? `@${handle}` : '' }} />
      <FeedList
        load={load}
        header={header}
        emptyText={
          profile && !profile.is_you && !profile.profile_public && profile.follow_state !== 'accepted'
            ? 'This profile is private. Follow to see their sessions once they accept.'
            : 'No shared sessions yet.'
        }
      />
    </>
  );
}
