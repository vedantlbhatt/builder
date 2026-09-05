import type { Api, Profile, SessionDetail } from './api';

/**
 * The on-device copy of what the server has told us.
 *
 * Cache first, always: on a cold launch over cellular the difference between this and a
 * spinner is the whole first impression, and offline, stale sessions are a far better
 * answer than an empty screen. It is a cache, not a store of record — `clear()` on sign-out
 * deletes it, because the sessions are the user's data and not ours to keep.
 *
 * Every SQLite access is guarded. If the database cannot be opened (a test runtime, a
 * corrupt file, a platform without the native module) the functions degrade to "nothing
 * cached" and the app still renders the sample session.
 */

type Row = { json: string };

// Loaded lazily: importing expo-sqlite at module scope would throw at import time in a
// runtime without the native module, before any try/catch could run.
type Db = {
  execAsync(sql: string): Promise<void>;
  runAsync(sql: string, ...params: (string | number | null)[]): Promise<unknown>;
  getAllAsync<T>(sql: string, ...params: (string | number | null)[]): Promise<T[]>;
  getFirstAsync<T>(sql: string, ...params: (string | number | null)[]): Promise<T | null>;
};

let dbPromise: Promise<Db | null> | null = null;
let warned = false;

function warnOnce(where: string, e: unknown): void {
  if (warned) return;
  warned = true;
  console.warn(`[cache] sqlite unavailable (${where}); running without a cache`, e);
}

function db(): Promise<Db | null> {
  if (!dbPromise) {
    dbPromise = (async () => {
      try {
        const sqlite = await import('expo-sqlite');
        const handle = sqlite.openDatabaseSync('builder-cache.db') as unknown as Db;
        await handle.execAsync(`
          PRAGMA journal_mode = WAL;
          CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            started_at TEXT,
            json TEXT NOT NULL
          );
          CREATE INDEX IF NOT EXISTS sessions_started ON sessions(started_at);
          CREATE TABLE IF NOT EXISTS profile (k TEXT PRIMARY KEY, json TEXT);
          CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);
        `);
        return handle;
      } catch (e) {
        warnOnce('open', e);
        return null;
      }
    })();
  }
  return dbPromise;
}

/** Run one guarded access; any failure logs once and yields the fallback. */
async function guarded<T>(where: string, fallback: T, fn: (d: Db) => Promise<T>): Promise<T> {
  const d = await db();
  if (!d) return fallback;
  try {
    return await fn(d);
  } catch (e) {
    warnOnce(where, e);
    return fallback;
  }
}

function parse(json: string): SessionDetail | null {
  try {
    return JSON.parse(json) as SessionDetail;
  } catch {
    return null;
  }
}

// ---------------------------------------------------------------------- sessions

export async function listSessions(limit: number): Promise<SessionDetail[]> {
  return guarded('listSessions', [], async (d) => {
    const rows = await d.getAllAsync<Row>(
      'SELECT json FROM sessions ORDER BY started_at DESC LIMIT ?',
      limit
    );
    return rows.map((r) => parse(r.json)).filter((s): s is SessionDetail => s !== null);
  });
}

export async function getDetail(id: string): Promise<SessionDetail | null> {
  return guarded('getDetail', null, async (d) => {
    const row = await d.getFirstAsync<Row>('SELECT json FROM sessions WHERE id = ?', id);
    return row ? parse(row.json) : null;
  });
}

/**
 * Upsert with a merge.
 *
 * A detail row replaces a summary row, but a summary must never overwrite a detail: the
 * list endpoint omits `strip` and `stats`, and a naive replace would strip every cached
 * timeline on every sync. Old then new, preserving old.strip/old.stats when the new row
 * lacks them.
 */
export async function putDetail(s: SessionDetail): Promise<void> {
  await upsert(s, true);
}

async function upsert(s: SessionDetail, isDetail: boolean): Promise<void> {
  await guarded('putDetail', undefined, async (d) => {
    const existing = await d.getFirstAsync<Row>('SELECT json FROM sessions WHERE id = ?', s.id);
    const old = existing ? parse(existing.json) : null;
    const merged: SessionDetail = { ...(old ?? {}), ...s } as SessionDetail;
    if (s.strip === undefined && old?.strip !== undefined) merged.strip = old.strip;
    if (s.stats === undefined && old?.stats !== undefined) merged.stats = old.stats;
    // A detail response omits `strip` when the server has none. Normalise to an explicit
    // null so `sync` can tell "fetched, and there is no strip" from "never fetched" and
    // does not re-request header-only sessions forever.
    if (isDetail) {
      if (merged.strip === undefined) merged.strip = null;
      if (merged.stats === undefined) merged.stats = null;
    }
    await d.runAsync(
      `INSERT INTO sessions (id, started_at, json) VALUES (?, ?, ?)
       ON CONFLICT(id) DO UPDATE SET started_at = excluded.started_at, json = excluded.json`,
      merged.id,
      merged.started_at,
      JSON.stringify(merged)
    );
  });
}

const SYNC_LIST_LIMIT = 50;
const DETAIL_BATCH = 6;
const DETAIL_CAP_PER_SYNC = 30;

/**
 * Pull the notable sessions and fill in the ones we have no detail for.
 *
 * Whatever succeeded is persisted BEFORE an error is rethrown: the caller shows the error
 * as a banner over the saved sessions, and losing a half-finished sync to a dropped
 * connection would make the banner the only thing the user got.
 */
export async function sync(api: Api): Promise<void> {
  let failure: unknown = null;

  const page = await api.sessions({ limit: SYNC_LIST_LIMIT, notable_only: true });
  for (const s of page.sessions) await upsert(s, false);

  const needDetail = await guarded('sync.needDetail', [] as string[], async (d) => {
    const rows = await d.getAllAsync<{ id: string; json: string }>(
      'SELECT id, json FROM sessions ORDER BY started_at DESC LIMIT ?',
      SYNC_LIST_LIMIT
    );
    return rows
      .filter((r) => {
        const s = parse(r.json);
        return s !== null && !('strip' in s);
      })
      .map((r) => r.id)
      .slice(0, DETAIL_CAP_PER_SYNC);
  });

  for (let i = 0; i < needDetail.length && failure === null; i += DETAIL_BATCH) {
    const batch = needDetail.slice(i, i + DETAIL_BATCH);
    const results = await Promise.allSettled(batch.map((id) => api.session(id)));
    for (const r of results) {
      if (r.status === 'fulfilled') await upsert(r.value, true);
      else if (failure === null) failure = r.reason;
    }
  }

  await setKv('last_sync_at', new Date().toISOString());
  if (failure !== null) throw failure;
}

// ----------------------------------------------------------------------- profile

export async function getProfile(): Promise<Profile | null> {
  return guarded('getProfile', null, async (d) => {
    const row = await d.getFirstAsync<{ json: string }>(
      "SELECT json FROM profile WHERE k = 'me'"
    );
    if (!row?.json) return null;
    try {
      return JSON.parse(row.json) as Profile;
    } catch {
      return null;
    }
  });
}

export async function putProfile(p: Profile): Promise<void> {
  await guarded('putProfile', undefined, async (d) => {
    await d.runAsync(
      "INSERT INTO profile (k, json) VALUES ('me', ?) ON CONFLICT(k) DO UPDATE SET json = excluded.json",
      JSON.stringify(p)
    );
  });
}

// ---------------------------------------------------------------------------- kv

async function setKv(k: string, v: string): Promise<void> {
  await guarded('setKv', undefined, async (d) => {
    await d.runAsync(
      'INSERT INTO kv (k, v) VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET v = excluded.v',
      k,
      v
    );
  });
}

export async function lastSyncAt(): Promise<string | null> {
  return guarded('lastSyncAt', null, async (d) => {
    const row = await d.getFirstAsync<{ v: string }>("SELECT v FROM kv WHERE k = 'last_sync_at'");
    return row?.v ?? null;
  });
}

/** Sign-out: the cached sessions are the user's data, not ours to keep. */
export async function clear(): Promise<void> {
  await guarded('clear', undefined, async (d) => {
    await d.execAsync('DELETE FROM sessions; DELETE FROM profile; DELETE FROM kv;');
  });
}
