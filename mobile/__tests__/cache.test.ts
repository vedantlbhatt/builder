/**
 * The live → final transition in the cache.
 *
 * The server keeps ONE row per session and flips its state, so the id is stable. The trap
 * is the list call: it asks for notable finals only, so a live session that finalizes as
 * not-notable never comes back through it. A row that stops being live must be re-read by
 * id, or it pulses on the phone forever. bun:sqlite stands in for expo-sqlite.
 */
import { Database } from 'bun:sqlite';
import { beforeAll, describe, expect, mock, test } from 'bun:test';

const sqlite = new Database(':memory:');
const fakeHandle = {
  execAsync: async (sql: string) => {
    sqlite.exec(sql);
  },
  runAsync: async (sql: string, ...params: (string | number | null)[]) => {
    sqlite.query(sql).run(...params);
  },
  getAllAsync: async (sql: string, ...params: (string | number | null)[]) =>
    sqlite.query(sql).all(...params),
  getFirstAsync: async (sql: string, ...params: (string | number | null)[]) =>
    sqlite.query(sql).get(...params) ?? null,
};

mock.module('expo-sqlite', () => ({ openDatabaseSync: () => fakeHandle }));
mock.module('expo-secure-store', () => ({
  getItemAsync: async () => null,
  setItemAsync: async () => {},
  deleteItemAsync: async () => {},
}));
mock.module('expo-constants', () => ({ default: { expoConfig: { version: '0.1.0-test' } } }));

const cache = await import('../src/data/cache');
const { ApiError } = await import('../src/data/api');
type Api = import('../src/data/api').Api;
type SessionDetail = import('../src/data/api').SessionDetail;

function session(id: string, extra: Partial<SessionDetail> = {}): SessionDetail {
  return {
    id,
    client_session_id: id,
    harness: 'claude_code',
    repo_name: 'gt-transit',
    started_at: '2026-09-05T09:00:00Z',
    ended_at: '2026-09-05T10:00:00Z',
    active_seconds: 3600,
    idle_seconds: 0,
    local_date: '2026-09-05',
    title: null,
    title_source: null,
    notable: true,
    unattended: false,
    timeline_fidelity: 'full',
    is_shared: false,
    ...extra,
  };
}

/** The three calls sync makes, scripted per test. */
function fakeApi(script: {
  finals: SessionDetail[];
  live: SessionDetail[] | Error;
  detail: (id: string) => SessionDetail | Error;
}) {
  const detailCalls: string[] = [];
  const api = {
    sessions: async () => ({ sessions: script.finals, next_before: null }),
    liveSessions: async () => {
      if (script.live instanceof Error) throw script.live;
      return { sessions: script.live };
    },
    session: async (id: string) => {
      detailCalls.push(id);
      const r = script.detail(id);
      if (r instanceof Error) throw r;
      return r;
    },
  } as unknown as Api;
  return { api, detailCalls };
}

const ANALYSIS = { headline: 'checkpoint', confidence: 0.5 } as unknown as SessionDetail['analysis'];

beforeAll(async () => {
  await cache.clear();
});

describe('cache live sessions', () => {
  test('a live row is kept out of listSessions and returned by listLive, with detail fetched', async () => {
    const s1 = session('s1');
    const s2 = session('s2', { state: 'live', end_reason: 'still_running', updated_at: 'u1', notable: false });
    const { api, detailCalls } = fakeApi({
      finals: [s1],
      live: [s2],
      detail: (id) => (id === 's2' ? { ...s2, analysis: ANALYSIS } : { ...s1, strip: null }),
    });

    await cache.sync(api);

    expect((await cache.listSessions(50)).map((s) => s.id)).toEqual(['s1']);
    expect((await cache.listLive()).map((s) => s.id)).toEqual(['s2']);
    expect(detailCalls).toContain('s2');
    const d = await cache.getDetail('s2');
    expect(d?.analysis).toEqual(ANALYSIS);
    expect(d?.strip).toBeNull();
  });

  test('an unchanged live snapshot is not re-read; a moved one is', async () => {
    const s2 = session('s2', { state: 'live', updated_at: 'u1', notable: false });
    const same = fakeApi({ finals: [], live: [s2], detail: (id) => session(id) });
    await cache.sync(same.api);
    expect(same.detailCalls).toEqual([]);

    const moved = fakeApi({
      finals: [],
      live: [{ ...s2, updated_at: 'u2' }],
      detail: () => ({ ...s2, updated_at: 'u2', analysis: ANALYSIS }),
    });
    await cache.sync(moved.api);
    expect(moved.detailCalls).toEqual(['s2']);
  });

  test('a row that leaves the live list is re-read by id and flips to final on the SAME id', async () => {
    const s2final = session('s2', {
      state: 'final',
      end_reason: 'idle_gap',
      updated_at: 'u3',
      notable: false,
      // The final detail carries no analysis: the checkpoint must not outlive the run.
    });
    const { api, detailCalls } = fakeApi({
      finals: [], // not notable, so the list never mentions it
      live: [],
      detail: (id) => (id === 's2' ? s2final : new ApiError(404, 'nope')),
    });

    await cache.sync(api);

    expect(detailCalls).toEqual(['s2']);
    expect(await cache.listLive()).toEqual([]);
    const ids = (await cache.listSessions(50)).map((s) => s.id).sort();
    expect(ids).toEqual(['s1', 's2']);
    const d = await cache.getDetail('s2');
    expect(d?.state).toBe('final');
    expect(d?.end_reason).toBe('idle_gap');
    expect(d?.analysis).toBeNull();
  });

  test('a live row the server no longer knows is dropped on 404', async () => {
    const ghost = session('s9', { state: 'live', notable: false });
    await cache.sync(fakeApi({ finals: [], live: [ghost], detail: () => ghost }).api);
    expect((await cache.listLive()).map((s) => s.id)).toEqual(['s9']);

    await cache.sync(fakeApi({ finals: [], live: [], detail: () => new ApiError(404, 'gone') }).api);
    expect(await cache.listLive()).toEqual([]);
    expect(await cache.getDetail('s9')).toBeNull();
  });

  test('a server without /v1/sessions/live is not an error', async () => {
    const { api } = fakeApi({
      finals: [session('s1')],
      live: new ApiError(404, 'Not Found'),
      detail: (id) => session(id),
    });
    await expect(cache.sync(api)).resolves.toBeUndefined();
  });

  test('any other live failure is reported after the finals were saved', async () => {
    const { api } = fakeApi({
      finals: [session('s3', { notable: true })],
      live: new ApiError(503, 'Service Unavailable'),
      detail: (id) => session(id),
    });
    await expect(cache.sync(api)).rejects.toBeInstanceOf(ApiError);
    expect((await cache.listSessions(50)).map((s) => s.id)).toContain('s3');
  });
});
