import { useRouter } from 'expo-router';
import React, { useCallback, useEffect, useState } from 'react';
import {
  ActivityIndicator,
  Alert,
  Pressable,
  ScrollView,
  Share,
  Switch,
  Text,
  TextInput,
  type TextStyle,
  View,
} from 'react-native';

import { ApiError, type Faction, type FactionBoard } from '../src/data/api';
import * as cache from '../src/data/cache';
import { api } from '../src/data/client';
import { PixelBadge } from '../src/pixel/PixelBadge';
import { forgetFaction, normalizeFactionCode, parseFactionList, rememberFaction } from '../src/social/format';
import { MY_FACTIONS_KEY } from '../src/social/identity';
import { colors, duration, hitSlopToReach, space } from '../src/theme';

const c = colors('dark');

/**
 * Factions: create, join by code, and the weekly board.
 *
 * The server has no "my factions" endpoint (checked: routes/social.py exposes create,
 * join, board and membership patch only), so the slugs this phone created or joined live in
 * the cache kv. A board that answers 403/404 evicts its slug — that is the membership
 * check, done lazily.
 */
export default function FactionsScreen() {
  const router = useRouter();
  const [mine, setMine] = useState<string[] | null>(null);
  const [boards, setBoards] = useState<Record<string, FactionBoard | 'error'>>({});
  const [name, setName] = useState('');
  const [code, setCode] = useState('');
  const [creating, setCreating] = useState(false);
  const [joining, setJoining] = useState(false);
  const [justCreated, setJustCreated] = useState<Faction | null>(null);

  const persist = useCallback(async (list: string[]) => {
    setMine(list);
    await cache.setKv(MY_FACTIONS_KEY, JSON.stringify(list));
  }, []);

  const loadBoard = useCallback(
    async (slug: string, list: string[]) => {
      try {
        const b = await api.factionBoard(slug);
        setBoards((prev) => ({ ...prev, [slug]: b }));
      } catch (e) {
        if (e instanceof ApiError && (e.status === 403 || e.status === 404)) {
          // Not a member any more, or never was: stop remembering it.
          await persist(forgetFaction(list, slug));
          return;
        }
        setBoards((prev) => ({ ...prev, [slug]: 'error' }));
      }
    },
    [persist]
  );

  useEffect(() => {
    (async () => {
      const list = parseFactionList(await cache.getKv(MY_FACTIONS_KEY));
      setMine(list);
      await Promise.all(list.map((slug) => loadBoard(slug, list)));
    })();
  }, [loadBoard]);

  const create = useCallback(async () => {
    const n = name.trim();
    if (!n || creating) return;
    setCreating(true);
    try {
      const f = await api.createFaction({ name: n });
      setJustCreated(f);
      setName('');
      const list = rememberFaction(mine ?? [], f.slug);
      await persist(list);
      await loadBoard(f.slug, list);
    } catch (e) {
      Alert.alert('Could not create faction', e instanceof Error ? e.message : 'try again');
    } finally {
      setCreating(false);
    }
  }, [creating, loadBoard, mine, name, persist]);

  const join = useCallback(async () => {
    const normalized = normalizeFactionCode(code);
    if (!normalized || joining) return;
    setJoining(true);
    try {
      const f = await api.joinFaction(normalized);
      setCode('');
      const list = rememberFaction(mine ?? [], f.slug);
      await persist(list);
      await loadBoard(f.slug, list);
    } catch (e) {
      Alert.alert('Could not join', e instanceof Error ? e.message : 'try again');
    } finally {
      setJoining(false);
    }
  }, [code, joining, loadBoard, mine, persist]);

  const setShare = useCallback(async (slug: string, share: boolean) => {
    const before = boards[slug];
    // Flip the row now; the board's "you" entry mirrors the membership flag.
    setBoards((prev) => {
      const b = prev[slug];
      if (!b || b === 'error') return prev;
      return {
        ...prev,
        [slug]: { ...b, members: b.members.map((m) => (m.you ? { ...m, share_hours: share } : m)) },
      };
    });
    try {
      await api.setFactionShareHours(slug, share);
      // Opting out zeroes the row server-side; re-read so the numbers match what others see.
      const b = await api.factionBoard(slug);
      setBoards((prev) => ({ ...prev, [slug]: b }));
    } catch (e) {
      if (before) setBoards((prev) => ({ ...prev, [slug]: before }));
      Alert.alert('Could not update', e instanceof Error ? e.message : 'try again');
    }
  }, [boards]);

  if (mine === null) {
    return (
      <View style={{ flex: 1, justifyContent: 'center', backgroundColor: c.bg }}>
        <ActivityIndicator color={c.accent} />
      </View>
    );
  }

  return (
    <ScrollView
      style={{ flex: 1, backgroundColor: c.bg }}
      contentContainerStyle={{ padding: space.md, paddingBottom: space.xxl }}
      keyboardShouldPersistTaps="handled"
    >
      {justCreated && (
        <View style={[card, { borderWidth: 1, borderColor: c.accent }]}>
          <Text style={{ color: c.text, fontWeight: '600' }}>{justCreated.name}</Text>
          <Text style={{ color: c.textDim, fontSize: 12 }}>/{justCreated.slug}</Text>
          <Text style={{ color: c.textDim, fontSize: 12, marginTop: space.md }}>JOIN CODE</Text>
          <Text
            style={{ color: c.accent, fontSize: 36, fontWeight: '800', letterSpacing: 4, fontVariant: ['tabular-nums'] }}
            selectable
          >
            {justCreated.join_code ?? '—'}
          </Text>
          <View style={{ flexDirection: 'row', gap: space.sm, marginTop: space.sm }}>
            {justCreated.join_code && (
              <Pressable
                style={pill}
                onPress={() =>
                  void Share.share({
                    message: `Join ${justCreated.name} on Builder with code ${justCreated.join_code}`,
                  })
                }
              >
                <Text style={{ color: c.text, fontSize: 13 }}>Share code</Text>
              </Pressable>
            )}
            <Pressable style={pill} onPress={() => setJustCreated(null)}>
              <Text style={{ color: c.text, fontSize: 13 }}>Done</Text>
            </Pressable>
          </View>
        </View>
      )}

      <View style={card}>
        <Text style={label}>CREATE</Text>
        <View style={{ flexDirection: 'row', gap: space.sm }}>
          <TextInput
            value={name}
            onChangeText={(t) => setName(t.slice(0, 60))}
            placeholder="Faction name"
            placeholderTextColor={c.textDim}
            style={input}
          />
          <Pressable onPress={() => void create()} disabled={!name.trim() || creating} style={[button, (!name.trim() || creating) && { opacity: 0.5 }]}>
            <Text style={buttonText}>{creating ? '…' : 'Create'}</Text>
          </Pressable>
        </View>
      </View>

      <View style={card}>
        <Text style={label}>JOIN BY CODE</Text>
        <View style={{ flexDirection: 'row', gap: space.sm }}>
          <TextInput
            value={code}
            onChangeText={setCode}
            placeholder="XXXX-XXXX"
            placeholderTextColor={c.textDim}
            autoCapitalize="characters"
            autoCorrect={false}
            style={input}
          />
          <Pressable onPress={() => void join()} disabled={!code.trim() || joining} style={[button, (!code.trim() || joining) && { opacity: 0.5 }]}>
            <Text style={buttonText}>{joining ? '…' : 'Join'}</Text>
          </Pressable>
        </View>
      </View>

      {mine.length === 0 && (
        <View style={{ marginTop: space.sm }}>
          <PixelBadge state="waving" text="Start a faction or join one with a code." style={{ paddingHorizontal: 0 }} />
          <Text style={{ color: c.textDim, fontSize: 12 }}>
            The board ranks attended hours this week.
          </Text>
        </View>
      )}

      {mine.map((slug) => {
        const b = boards[slug];
        return (
          <View key={slug} style={card}>
            <View style={{ flexDirection: 'row', alignItems: 'baseline' }}>
              <Text style={{ color: c.text, fontSize: 18, fontWeight: '700', flex: 1 }}>
                {b && b !== 'error' ? b.faction.name : slug}
              </Text>
              <Pressable onPress={() => router.push({ pathname: '/feed', params: { slug } })} hitSlop={hitSlopToReach(18)} accessibilityRole="button">
                <Text style={{ color: c.accent, fontSize: 13, fontWeight: '600' }}>Feed</Text>
              </Pressable>
            </View>
            {b === undefined && <ActivityIndicator color={c.accent} style={{ marginVertical: space.md }} />}
            {b === 'error' && <Text style={{ color: c.textDim, fontSize: 13 }}>Board unavailable right now.</Text>}
            {b && b !== 'error' && <Board board={b} onShare={(v) => void setShare(slug, v)} />}
          </View>
        );
      })}
    </ScrollView>
  );
}

function Board({ board, onShare }: { board: FactionBoard; onShare: (share: boolean) => void }) {
  const me = board.members.find((m) => m.you);
  return (
    <>
      <Text style={{ color: c.textDim, fontSize: 12, marginBottom: space.sm }}>
        {board.week} · {board.week_start} → {board.week_end}
        {board.faction.join_code ? ` · code ${board.faction.join_code}` : ''}
      </Text>
      <View style={{ flexDirection: 'row', paddingVertical: 4 }}>
        <Text style={[th, { width: 28 }]}>#</Text>
        <Text style={[th, { flex: 1 }]}>builder</Text>
        <Text style={[th, num]}>attended</Text>
        <Text style={[th, num, { width: 44 }]}>sess.</Text>
        <Text style={[th, num]}>longest</Text>
      </View>
      {board.members.map((m, i) => (
        <View key={m.handle ?? `${i}`} style={{ flexDirection: 'row', paddingVertical: 6, borderTopWidth: 1, borderTopColor: c.border }}>
          <Text style={[td, { width: 28 }]}>{i + 1}</Text>
          <Text style={[td, { flex: 1, fontWeight: m.you ? '700' : '400' }]} numberOfLines={1}>
            {m.handle ?? m.display_name ?? '—'}
            {m.role === 'admin' ? ' ·adm' : ''}
          </Text>
          <Text style={[td, num, !m.share_hours && { color: c.textDim }]}>
            {m.share_hours ? duration(m.attended_seconds) : 'private'}
          </Text>
          <Text style={[td, num, { width: 44 }]}>{m.share_hours ? m.sessions : '—'}</Text>
          <Text style={[td, num]}>{m.share_hours ? duration(m.longest_attended_seconds) : '—'}</Text>
        </View>
      ))}
      {me && (
        <View style={{ flexDirection: 'row', alignItems: 'center', marginTop: space.md }}>
          <Text style={{ color: c.text, fontSize: 14, flex: 1 }}>Share my hours on the board</Text>
          <Switch value={me.share_hours} onValueChange={onShare} trackColor={{ true: c.accent }} />
        </View>
      )}
    </>
  );
}

const card = {
  backgroundColor: c.card,
  borderRadius: 12,
  padding: space.md,
  marginBottom: space.md,
} as const;

const label = { color: c.textDim, fontSize: 11, fontWeight: '700', letterSpacing: 0.8, marginBottom: space.sm } as const;
const input = {
  flex: 1,
  color: c.text,
  backgroundColor: c.bg,
  borderRadius: 10,
  paddingHorizontal: space.md,
  paddingVertical: space.sm,
  fontSize: 15,
} as const;
const button = { backgroundColor: c.accent, borderRadius: 10, paddingHorizontal: space.md, justifyContent: 'center' } as const;
const buttonText = { color: c.onAccent, fontWeight: '700' } as const;
const pill = { backgroundColor: c.bg, borderRadius: 999, paddingHorizontal: space.md, paddingVertical: space.sm } as const;
const th = { color: c.textDim, fontSize: 11, fontWeight: '700' } as const;
const td = { color: c.text, fontSize: 13 } as const;
const num: TextStyle = { width: 64, textAlign: 'right', fontVariant: ['tabular-nums'] };
