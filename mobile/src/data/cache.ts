import { ApiError, type Api, type Profile, type SessionDetail } from './api';

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
            json TEXT NOT NULL,
            live INTEGER NOT NULL DEFAULT 0
          );
          CREATE INDEX IF NOT EXISTS sessions_started ON sessions(started_at);
          CREATE TABLE IF NOT EXISTS profile (k TEXT PRIMARY KEY, json TEXT);
          CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);
        `);
        // Databases created before live sessions existed lack the column. SQLite has no
        // ADD COLUMN IF NOT EXISTS; the duplicate-column error is the "already there" signal.
        try {
          await handle.execAsync(
            'ALTER TABLE sessions ADD COLUMN live INTEGER NOT NULL DEFAULT 0'
          );
        } catch {
          // column exists
        }
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

/** Finished sessions only. Live ones come from `listLive` and render as their own block. */
export async function listSessions(limit: number): Promise<SessionDetail[]> {
  return guarded('listSessions', [], async (d) => {
    const rows = await d.getAllAsync<Row>(
      'SELECT json FROM sessions WHERE live = 0 ORDER BY started_at DESC LIMIT ?',
      limit
    );
    return rows.map((r) => parse(r.json)).filter((s): s is SessionDetail => s !== null);
  });
}

/** Sessions the Mac is still uploading, most recently updated first. */
export async function listLive(): Promise<SessionDetail[]> {
  return guarded('listLive', [], async (d) => {
    const rows = await d.getAllAsync<Row>('SELECT json FROM sessions WHERE live = 1');
    return rows
      .map((r) => parse(r.json))
      .filter((s): s is SessionDetail => s !== null)
      .sort((a, b) => (b.updated_at ?? b.started_at).localeCompare(a.updated_at ?? a.started_at));
  });
}

async function liveIds(): Promise<string[]> {
  return guarded('liveIds', [] as string[], async (d) => {
    const rows = await d.getAllAsync<{ id: string }>('SELECT id FROM sessions WHERE live = 1');
    return rows.map((r) => r.id);
  });
}

async function remove(id: string): Promise<void> {
  await guarded('remove', undefined, async (d) => {
    await d.runAsync('DELETE FROM sessions WHERE id = ?', id);
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
      // The detail endpoint is authoritative for the analysis. A live row may have cached
      // a checkpoint; once the final detail arrives without one, the checkpoint must not
      // outlive the session it was a snapshot of.
      merged.analysis = s.analysis ?? null;
    }
    // The server keeps one row per session and flips `state` when it finalizes, so the id
    // is stable: the same upsert that stored the live snapshot clears the flag.
    const live = merged.state === 'live' ? 1 : 0;
    await d.runAsync(
      `INSERT INTO sessions (id, started_at, json, live) VALUES (?, ?, ?, ?)
       ON CONFLICT(id) DO UPDATE SET
         started_at = excluded.started_at, json = excluded.json, live = excluded.live`,
      merged.id,
      merged.started_at,
      JSON.stringify(merged),
      live
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
 *
 * Live sessions are pulled on the same pass. The list call asks for notable finals only,
 * so a live session that finalizes as NOT notable would never come back through it — a
 * row that was live last time and is missing from the live list now is re-read by id, so
 * its cached copy flips to final (or is dropped on a 404) instead of pulsing forever.
 */
export async function sync(api: Api): Promise<void> {
  let failure: unknown = null;

  const page = await api.sessions({ limit: SYNC_LIST_LIMIT, notable_only: true });
  for (const s of page.sessions) await upsert(s, false);

  const wasLive = await liveIds();
  const staleLive: string[] = [];
  let liveNow: SessionDetail[] | null = null;
  try {
    liveNow = (await api.liveSessions()).sessions;
  } catch (e) {
    // A server older than the split has no /live; that is not an error worth a banner.
    if (!(e instanceof ApiError && e.status === 404)) failure = e;
  }
  if (liveNow !== null) {
    for (const s of liveNow) {
      const cached = await getDetail(s.id);
      // A live row changes under us; re-read its detail whenever the snapshot moved (or
      // the server does not say, or we never had the detail).
      if (
        !cached ||
        !('strip' in cached) ||
        s.updated_at === undefined ||
        cached.updated_at !== s.updated_at
      ) {
        staleLive.push(s.id);
      }
      await upsert({ ...s, state: s.state ?? 'live' }, false);
    }
    const liveIdSet = new Set(liveNow.map((s) => s.id));
    for (const id of wasLive) {
      if (liveIdSet.has(id)) continue;
      try {
        await upsert(await api.session(id), true);
      } catch (e) {
        if (e instanceof ApiError && e.status === 404) await remove(id);
        else if (failure === null) failure = e;
      }
    }
  }

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
  const ids = [...new Set([...staleLive, ...needDetail])];

  for (let i = 0; i < ids.length && failure === null; i += DETAIL_BATCH) {
    const batch = ids.slice(i, i + DETAIL_BATCH);
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
