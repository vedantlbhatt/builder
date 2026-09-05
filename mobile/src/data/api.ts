import Constants from 'expo-constants';
import * as SecureStore from 'expo-secure-store';

import type { SessionAnalysis } from '../generated/analysis';

/**
 * The phone's view of the server.
 *
 * These are READ shapes — what `server/builder/routes/sessions.py` serves — not the upload
 * wire type in `generated/contract.ts`. The two are deliberately unrelated: the phone never
 * uploads a session, and a `SessionDetail` that extended `SessionWire` would suggest the
 * phone can see fields (machine_id, repo_hash, tool_calls) that the server never returns
 * to it.
 */

/** Row from `session_stats`, as `GET /v1/sessions/{id}` serialises it. */
export interface SessionStats {
  tokens_reported: boolean;
  /** Null, never zero, when the harness does not report tokens. Cursor never does. */
  tok_in: number | null;
  tok_out: number | null;
  tok_cache_read: number | null;
  tok_cache_w5m: number | null;
  tok_cache_w1h: number | null;
  models: { model_id: string; output_token_share: number }[] | null;
  model_state: string;
  human_prompt_count: number;
  prompt_count_basis: string;
  files_touched: number;
  lines_added_agent: number;
  commit_count: number;
  agent_line_bucket: string;
  attrib_confidence: string;
  // Forward-compatible: a newer server may add a stat this build does not name yet, and
  // the card reads stats through a loose Record anyway.
  [key: string]: unknown;
}

export interface SessionStrip {
  /** base64 of the 1024-byte column array; decode with `strip/decode.ts`. */
  cols: string;
  /** [[ms, kind], ...] */
  marks: number[][];
  t0_ms: number;
  t1_ms: number;
}

/**
 * `live` is an open or idle session the Mac is still uploading snapshots of; `final` is
 * one that has ended. The server keeps ONE row per session and flips its state, so the id
 * is stable across the transition — the cache upserts by id and never sees two rows.
 */
export type SessionState = 'live' | 'final';

/** See docs/session-boundaries.md. `still_running` is the end reason of a live snapshot. */
export type EndReason = 'idle_gap' | 'human_returned' | 'day_boundary' | 'still_running';

export interface SessionDetail {
  id: string;
  client_session_id: string;
  harness: string;
  repo_name: string | null;
  started_at: string;
  ended_at: string;
  active_seconds: number;
  idle_seconds: number;
  local_date: string;
  title: string | null;
  title_source: string | null;
  notable: boolean;
  unattended: boolean;
  timeline_fidelity: string;
  is_shared: boolean;
  /** Only on the detail endpoint; absent from the list. Null when no strip was stored. */
  strip?: SessionStrip | null;
  stats?: SessionStats | null;

  // ---- Session boundaries v2. Every field is optional on READ: a server older than the
  // ---- split omits them all, and the screens read `?? 0` / `?? 'final'` rather than
  // ---- inventing a number the server never sent.
  state?: SessionState;
  end_reason?: EndReason;
  /** Active seconds while a human was evidently present. */
  attended_seconds?: number;
  /** Active seconds after the human went quiet for longer than tauAutonomousSec. */
  autonomous_seconds?: number;
  /** Count of presence signals (typed prompts, interrupts, human edits). */
  presence_count?: number;
  /**
   * The model-written reading of the session (spec/analysis.v1.json). Undefined when the
   * endpoint did not include it; null when the server has none. A live session may carry
   * a checkpoint analysis.
   */
  analysis?: SessionAnalysis | null;
  updated_at?: string;
}

export interface Profile {
  graph: { date: string; active_seconds: number }[];
  totals: { sessions: number; active_seconds: number };
  /** Ranked by ATTENDED time on a v2 server — a robot cannot hold the record. */
  longest_session: {
    id: string;
    active_seconds: number;
    started_at: string;
    attended_seconds?: number;
  } | null;
  projects: {
    key: string;
    name: string | null;
    sessions: number;
    active_seconds: number;
    first_at: string;
    last_at: string;
  }[];
  attribution: {
    agent_lines: number;
    human_edit_events: number;
    prompts: number;
    attended_seconds?: number;
    autonomous_seconds?: number;
  };
  /** Sessions the Mac is still uploading. Absent on a server older than the split. */
  live?: SessionDetail[];
}

export interface TokenPair {
  access_token: string;
  refresh_token: string;
  expires_in?: number;
}

export type RepoVisibility = 'public' | 'anonymous' | 'excluded';

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

/** The two secrets the app holds. Injected so the class is testable without a keychain. */
export interface TokenStorage {
  get(key: string): Promise<string | null>;
  set(key: string, value: string): Promise<void>;
  remove(key: string): Promise<void>;
}

const ACCESS_KEY = 'builder.access';
const REFRESH_KEY = 'builder.refresh';
const TIMEOUT_MS = 20_000;

const secureStorage: TokenStorage = {
  get: (k) => SecureStore.getItemAsync(k),
  set: (k, v) => SecureStore.setItemAsync(k, v),
  remove: (k) => SecureStore.deleteItemAsync(k),
};

function appVersion(): string {
  return Constants.expoConfig?.version ?? 'ios';
}

/**
 * ONE refresh in flight at a time, across every concurrent request.
 *
 * Refresh tokens rotate: redeeming one issues a new one and burns the old. If two requests
 * both hit 401 and both call /refresh with the same token, the second redemption is
 * "reuse" and the server revokes every token for the device — the user is signed out by
 * their own app for scrolling too fast. So the second caller waits on the first's promise.
 */
let refreshing: Promise<void> | null = null;

export class Api {
  private readonly baseUrl: string;
  private readonly storage: TokenStorage;
  // undefined = not read from storage yet; null = read, and absent.
  private access: string | null | undefined;
  private refresh: string | null | undefined;

  constructor(baseUrl: string, storage: TokenStorage = secureStorage) {
    this.baseUrl = baseUrl.replace(/\/+$/, '');
    this.storage = storage;
  }

  // ----------------------------------------------------------------- tokens

  private async loadTokens(): Promise<void> {
    if (this.access !== undefined && this.refresh !== undefined) return;
    try {
      const [a, r] = await Promise.all([
        this.storage.get(ACCESS_KEY),
        this.storage.get(REFRESH_KEY),
      ]);
      this.access = a;
      this.refresh = r;
    } catch {
      this.access = null;
      this.refresh = null;
    }
  }

  async isSignedIn(): Promise<boolean> {
    await this.loadTokens();
    return Boolean(this.access && this.refresh);
  }

  async setTokens(access: string, refresh: string): Promise<void> {
    this.access = access;
    this.refresh = refresh;
    await Promise.all([this.storage.set(ACCESS_KEY, access), this.storage.set(REFRESH_KEY, refresh)]);
  }

  async clearTokens(): Promise<void> {
    this.access = null;
    this.refresh = null;
    await Promise.all([this.storage.remove(ACCESS_KEY), this.storage.remove(REFRESH_KEY)]);
  }

  // ------------------------------------------------------------------- auth

  signInWithApple(identityToken: string, machineId: string): Promise<TokenPair> {
    return this.request<TokenPair>('POST', '/v1/auth/apple', {
      body: {
        identity_token: identityToken,
        machine_id: machineId,
        label: 'iPhone',
        platform: 'ios',
        agent_version: appVersion(),
      },
      auth: false,
    });
  }

  signInWithGoogle(
    idToken: string,
    machineId: string,
    platform: 'ios' | 'android'
  ): Promise<TokenPair> {
    return this.request<TokenPair>('POST', '/v1/auth/google', {
      body: {
        id_token: idToken,
        machine_id: machineId,
        label: 'Phone',
        platform,
        agent_version: appVersion(),
      },
      auth: false,
    });
  }

  approvePairing(code: string): Promise<{ status: string; label: string; platform: string }> {
    return this.request('POST', '/v1/auth/device/approve', { body: { user_code: code } });
  }

  deleteAccount(): Promise<{ status: string; row_counts: Record<string, number>; receipt: string }> {
    return this.request('POST', '/v1/account/delete');
  }

  // ------------------------------------------------------------------- data

  profile(days = 119): Promise<Profile> {
    return this.request('GET', `/v1/profile?days=${encodeURIComponent(days)}`);
  }

  sessions(opts: { limit?: number; before?: string | null; notable_only?: boolean } = {}): Promise<{
    sessions: SessionDetail[];
    next_before: string | null;
  }> {
    const q = new URLSearchParams();
    if (opts.limit !== undefined) q.set('limit', String(opts.limit));
    if (opts.before) q.set('before', opts.before);
    if (opts.notable_only !== undefined) q.set('notable_only', String(opts.notable_only));
    const qs = q.toString();
    return this.request('GET', `/v1/sessions${qs ? `?${qs}` : ''}`);
  }

  session(id: string): Promise<SessionDetail> {
    return this.request('GET', `/v1/sessions/${encodeURIComponent(id)}`);
  }

  /** Sessions in state `live`, newest updated first, at most 10. */
  liveSessions(): Promise<{ sessions: SessionDetail[] }> {
    return this.request('GET', '/v1/sessions/live');
  }

  registerPush(
    token: string,
    environment: 'sandbox' | 'production'
  ): Promise<{ status: string; environment: string }> {
    return this.request('POST', '/v1/push/register', { body: { token, environment } });
  }

  setRepoVisibility(
    repoHash: string,
    visibility: RepoVisibility
  ): Promise<{ status: string; visibility: string; sessions_deleted: number }> {
    return this.request('POST', '/v1/repos/visibility', {
      body: { repo_hash: repoHash, visibility },
    });
  }

  // -------------------------------------------------------------- transport

  private async request<T>(
    method: 'GET' | 'POST',
    path: string,
    opts: { body?: unknown; auth?: boolean } = {}
  ): Promise<T> {
    const auth = opts.auth ?? true;
    if (auth) await this.loadTokens();

    let res = await this.fetchJson(method, path, opts.body, auth ? this.access : null);

    if (res.status === 401 && auth && this.refresh) {
      // Retry exactly once. A second 401 after a fresh token is a real authorisation
      // failure, not a stale one, and looping would hammer /refresh.
      await this.refreshTokens();
      res = await this.fetchJson(method, path, opts.body, this.access);
    }

    const parsed = await parseBody(res);
    if (!res.ok) throw new ApiError(res.status, errorMessage(parsed, res));
    return parsed as T;
  }

  private async fetchJson(
    method: string,
    path: string,
    body: unknown,
    bearer: string | null | undefined
  ): Promise<Response> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
    const headers: Record<string, string> = { Accept: 'application/json' };
    if (body !== undefined) headers['Content-Type'] = 'application/json';
    if (bearer) headers.Authorization = `Bearer ${bearer}`;
    try {
      return await fetch(`${this.baseUrl}${path}`, {
        method,
        headers,
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: controller.signal,
      });
    } catch (e) {
      if ((e as { name?: string }).name === 'AbortError') {
        throw new ApiError(0, 'request timed out');
      }
      throw new ApiError(0, e instanceof Error ? e.message : 'network error');
    } finally {
      clearTimeout(timer);
    }
  }

  private refreshTokens(): Promise<void> {
    if (!refreshing) {
      refreshing = this.doRefresh().finally(() => {
        refreshing = null;
      });
    }
    return refreshing;
  }

  private async doRefresh(): Promise<void> {
    const token = this.refresh;
    if (!token) throw new ApiError(401, 'not signed in');
    let res: Response;
    let parsed: unknown;
    try {
      res = await this.fetchJson('POST', '/v1/auth/refresh', { refresh_token: token }, null);
      parsed = await parseBody(res);
    } catch (e) {
      // A network failure or timeout mid-refresh is not proof the token is dead. Keep the
      // pair; the caller shows the banner and the next sync retries. Clearing here signed
      // people out on every flaky connection.
      throw e;
    }
    if (!res.ok) {
      // Only a definitive auth answer ends the session. A 5xx is the server's problem.
      if (res.status === 401 || res.status === 403) await this.clearTokens();
      throw new ApiError(res.status, errorMessage(parsed, res));
    }
    const pair = parsed as Partial<TokenPair>;
    if (!pair.access_token || !pair.refresh_token) {
      await this.clearTokens();
      throw new ApiError(res.status, 'malformed refresh response');
    }
    await this.setTokens(pair.access_token, pair.refresh_token);
  }
}

async function parseBody(res: Response): Promise<unknown> {
  const text = await res.text();
  if (!text) return null;
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return text;
  }
}

function errorMessage(body: unknown, res: Response): string {
  if (body && typeof body === 'object') {
    const b = body as { detail?: unknown; error?: unknown };
    const m = b.detail ?? b.error;
    if (typeof m === 'string') return m;
    if (m !== undefined && m !== null) return JSON.stringify(m);
  }
  return res.statusText || `HTTP ${res.status}`;
}
