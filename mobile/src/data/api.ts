import Constants from 'expo-constants';
import * as SecureStore from 'expo-secure-store';

import type { Archetype, Dimension, SessionAnalysis } from '../generated/analysis';

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
export type EndReason =
  | 'idle_gap'
  | 'human_returned'
  | 'day_boundary'
  | 'still_running'
  // v3 (docs/session-boundaries.md): a `/clear`, and a human opening a session in another repo.
  | 'cleared'
  | 'switched_repo';

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
  /**
   * The VIEWER'S OWN post for this session (any visibility), null when there is none. The
   * server sends it on every session shape (list, live, profile live, detail); it is
   * optional here only because a server older than the field omits it, and the screen
   * must read "unknown" rather than "no post" from that absence.
   */
  post_id?: string | null;
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
  /**
   * The aggregate of the session analyses (server/builder/builder_profile.py). Undefined
   * on a server that predates it; null until three analysed sessions exist in the window,
   * because docs/analysis.md forbids reading an archetype off one run.
   */
  builder_profile?: BuilderProfile | null;
}

/** One dimension's aggregate: mean 0-100 over `sessions`, and recent-half minus older-half. */
export interface BuilderDimension {
  mean: number;
  sessions: number;
  /** Points, rounded to 0.1. Null with too few sessions to split into halves. */
  trend: number | null;
}

/** The modal archetype and the whole distribution. `share` is out of `with_archetype`. */
export interface BuilderArchetype {
  modal: Archetype | null;
  share: number | null;
  /** How many analysed sessions had an archetype at all; short sessions get none. */
  with_archetype: number;
  distribution: Record<string, number>;
}

/** The modal value of one build-style key, its share of the sessions that set it, and the counts. */
export interface BuilderMode {
  mode: string | null;
  share: number | null;
  distribution: Record<string, number>;
}

/**
 * `GET /v1/profile` → `builder_profile`, field for field as the server serialises it.
 * `dimensions` is keyed by dimension name, not a list; a session's archetype share is out
 * of the sessions that HAD one (`with_archetype`), not all of them.
 */
export interface BuilderProfile {
  window_days: number;
  sessions_analysed: number;
  confidence_mean: number | null;
  dimensions: Partial<Record<Dimension, BuilderDimension>>;
  archetype: BuilderArchetype;
  build_style: Record<string, BuilderMode>;
  prompting: {
    specificity_mean: number | null;
    correction_share_mean: number | null;
    question_share_mean: number | null;
    tone_distribution: Record<string, number>;
  };
  /** Ranked most-sessions first, ties by name; at most 8. */
  tags: { tag: string; sessions: number }[];
  /** Ranked like tags; at most 5. `example` is the most recent verbatim excerpt. */
  decision_patterns: { pattern: string; sessions: number; example: string }[];
}

// ------------------------------------------------------------------ social
// Read shapes mirror `server/builder/routes/social.py` field for field. A feed item is
// self-contained (one request renders the screen); the cursor pair `next_before` /
// `next_before_id` is null on the last page.

export type Visibility = 'private' | 'followers' | 'public';

export interface Author {
  handle: string | null;
  display_name: string | null;
  /**
   * Compared server-side against the viewer. Optional on READ: a server older than
   * `routes/users.py` omits it, and the screens then simply lack the owner-only controls
   * rather than guessing ownership from a remembered handle.
   */
  is_you?: boolean;
}

export interface PostMedia {
  id: string;
  kind: 'photo' | 'audio';
  object_key: string;
  width: number | null;
  height: number | null;
  duration_ms: number | null;
  position: number;
  url: string | null;
}

/**
 * Headline + summary only, unless the author set `share_analysis`, in which case the
 * whole `SessionAnalysis` document is present. Read the two named fields; treat the rest
 * as optional.
 */
export interface FeedAnalysis extends Partial<Omit<SessionAnalysis, 'headline' | 'summary'>> {
  headline?: string | null;
  summary?: string | null;
}

export interface FeedItem {
  id: string;
  author: Author;
  caption: string | null;
  visibility: Visibility;
  share_analysis: boolean;
  created_at: string;
  updated_at: string;
  session: SessionDetail;
  strip: SessionStrip | null;
  analysis: FeedAnalysis | null;
  photos: PostMedia[];
  audio: PostMedia | null;
  kudos_count: number;
  comment_count: number;
  you_kudosed: boolean;
}

export interface FeedPage {
  items: FeedItem[];
  next_before: string | null;
  next_before_id: string | null;
}

export interface PostCreate {
  session_id: string;
  caption?: string | null;
  visibility: Visibility;
  share_analysis?: boolean;
}

export interface PostPatch {
  caption?: string | null;
  visibility?: Visibility;
  share_analysis?: boolean;
}

export interface KudosState {
  kudos_count: number;
  you_kudosed: boolean;
}

export interface Comment {
  id: string;
  post_id: string;
  author: Author;
  body: string;
  created_at: string;
}

export type FollowState = 'accepted' | 'pending' | null;

export interface UserPage {
  profile: {
    handle: string;
    display_name: string | null;
    profile_public: boolean;
    created_at: string;
    is_you: boolean;
    /** 'accepted', 'pending', or null. Null for yourself as well. */
    follow_state: FollowState;
  };
  posts: FeedItem[];
  next_before: string | null;
  next_before_id: string | null;
}

export interface Faction {
  slug: string;
  name: string;
  open: boolean;
  tz: string;
  role?: 'admin' | 'member' | null;
  /** Admins only; the server withholds it from members. */
  join_code?: string;
}

/**
 * One row of `GET /v1/factions/mine` and of `GET /v1/users/me`'s `factions` — the viewer's
 * own memberships, oldest first (`server/builder/routes/users.py::_my_factions`).
 */
export interface MyFaction {
  slug: string;
  name: string;
  role: 'admin' | 'member';
  share_hours: boolean;
  open: boolean;
  member_count: number;
  joined_at: string;
}

/** `GET /v1/users/me` and the answer to `PATCH /v1/users/me`: the viewer's own row. */
export interface Me {
  id: string;
  /** Null until the person picks one. */
  handle: string | null;
  display_name: string | null;
  profile_public: boolean;
  created_at: string;
  factions: MyFaction[];
}

/**
 * Body of `PATCH /v1/users/me`. Omit a field to leave it alone; `display_name: null`
 * clears it (the server reads the field SET, not its value, for that one).
 */
export interface MePatch {
  handle?: string;
  display_name?: string | null;
  profile_public?: boolean;
}

// ------------------------------------------------------------ capture keys
// Read shapes mirror `server/builder/routes/capture_keys.py`. A key is the non-rotating
// credential a cloud container uploads with (docs/cloud-capture.md); it can reach the two
// sync routes and nothing else, so nothing here ever needs to read one back.

/** One row of `GET /v1/capture-keys`: no secret, only the prefix the phone shows. */
export interface CaptureKey {
  id: string;
  name: string;
  /** `bck_` plus four characters — enough to tell keys apart, never enough to use one. */
  key_prefix: string;
  created_at: string;
  /** Null until the key's first upload; touched at most once a minute after that. */
  last_used_at: string | null;
}

/**
 * The answer to `POST /v1/capture-keys`. `key` is the plaintext and this response is the
 * ONLY time it exists outside the caller's hands: the server stores a hash. Show it once,
 * offer to copy it, and never keep it in state longer than the screen that shows it.
 */
export interface CaptureKeyCreated extends Omit<CaptureKey, 'last_used_at'> {
  key: string;
}

export interface FactionBoardMember extends Author {
  role: 'admin' | 'member';
  share_hours: boolean;
  you: boolean;
  attended_seconds: number;
  sessions: number;
  longest_attended_seconds: number;
}

export interface FactionBoard {
  faction: Faction;
  /** ISO week, e.g. "2026-W33". */
  week: string;
  week_start: string;
  week_end: string;
  /** Ranked by attended hours, the server's order. Opted-out members carry zeros. */
  members: FactionBoardMember[];
}

export interface PresignRequest {
  kind: 'photo' | 'audio';
  content_type: string;
  bytes: number;
}

export interface Presign {
  upload_url: string;
  object_key: string;
  method: 'PUT';
  headers: Record<string, string>;
  expires_in: number;
}

export interface MediaAttach {
  object_key: string;
  kind?: 'photo' | 'audio';
  width?: number;
  height?: number;
  duration_ms?: number;
}

export const PRESIGN_UNCONFIGURED_MESSAGE = "Photo upload isn't configured on this server yet.";

/** Keyset cursor for the feed and the user page. Both fields come from the previous page. */
export interface Cursor {
  before?: string | null;
  beforeId?: string | null;
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

  // ----------------------------------------------------------------- social

  private cursorQuery(cursor: Cursor | undefined, extra: Record<string, string> = {}): string {
    const q = new URLSearchParams(extra);
    if (cursor?.before) {
      q.set('before', cursor.before);
      // The id is the tiebreaker; sending it without a timestamp is meaningless and the
      // server ignores it, so it rides only alongside `before`.
      if (cursor.beforeId) q.set('before_id', cursor.beforeId);
    }
    const qs = q.toString();
    return qs ? `?${qs}` : '';
  }

  /** People you follow, members of your factions, and you. Newest first, ≤ 30 per page. */
  feed(cursor?: Cursor): Promise<FeedPage> {
    return this.request('GET', `/v1/feed${this.cursorQuery(cursor)}`);
  }

  /** Same shape as `feed`, scoped to one faction. 403 until you join it. */
  factionFeed(slug: string, cursor?: Cursor): Promise<FeedPage> {
    return this.request(
      'GET',
      `/v1/feed/faction/${encodeURIComponent(slug)}${this.cursorQuery(cursor)}`
    );
  }

  post(id: string): Promise<FeedItem> {
    return this.request('GET', `/v1/posts/${encodeURIComponent(id)}`);
  }

  /** Share a session. The session must be the caller's and `final`. Returns the feed item. */
  createPost(body: PostCreate): Promise<FeedItem> {
    return this.request('POST', '/v1/posts', { body });
  }

  updatePost(id: string, body: PostPatch): Promise<FeedItem> {
    return this.request('PATCH', `/v1/posts/${encodeURIComponent(id)}`, { body });
  }

  deletePost(id: string): Promise<void> {
    return this.request('DELETE', `/v1/posts/${encodeURIComponent(id)}`);
  }

  kudos(postId: string): Promise<KudosState> {
    return this.request('POST', `/v1/posts/${encodeURIComponent(postId)}/kudos`);
  }

  unkudos(postId: string): Promise<KudosState> {
    return this.request('DELETE', `/v1/posts/${encodeURIComponent(postId)}/kudos`);
  }

  /** Flat, oldest first, not paginated. */
  comments(postId: string): Promise<{ comments: Comment[] }> {
    return this.request('GET', `/v1/posts/${encodeURIComponent(postId)}/comments`);
  }

  addComment(postId: string, body: string): Promise<Comment & { comment_count: number }> {
    return this.request('POST', `/v1/posts/${encodeURIComponent(postId)}/comments`, {
      body: { body },
    });
  }

  deleteComment(commentId: string): Promise<void> {
    return this.request('DELETE', `/v1/comments/${encodeURIComponent(commentId)}`);
  }

  /** Immediate for a public profile (`accepted`), a request otherwise (`pending`). */
  follow(handle: string): Promise<{ handle: string; state: FollowState }> {
    return this.request('POST', `/v1/follows/${encodeURIComponent(handle)}`);
  }

  unfollow(handle: string): Promise<void> {
    return this.request('DELETE', `/v1/follows/${encodeURIComponent(handle)}`);
  }

  /** The caller is the followee; `handle` is who asked. */
  acceptFollow(handle: string): Promise<{ handle: string; state: 'accepted' }> {
    return this.request('POST', `/v1/follows/${encodeURIComponent(handle)}:accept`);
  }

  /** Profile plus the posts the CALLER may see, keyset paginated like the feed. */
  user(handle: string, cursor?: Cursor): Promise<UserPage> {
    return this.request(
      'GET',
      `/v1/users/${encodeURIComponent(handle)}${this.cursorQuery(cursor)}`
    );
  }

  // ----------------------------------------------------------------- account

  /** The viewer's own row plus their factions, in one request. */
  getMe(): Promise<Me> {
    return this.request('GET', '/v1/users/me');
  }

  /**
   * Handle, display name, profile visibility; answers with the same shape as `getMe`.
   * 422 for a malformed or reserved handle, 409 when it is taken OR when the last change
   * was under 30 days ago — that detail names the instant it opens again, and
   * `social/account.ts::describeHandleConflict` turns it into a sentence.
   */
  patchMe(body: MePatch): Promise<Me> {
    return this.request('PATCH', '/v1/users/me', { body });
  }

  // ------------------------------------------------------------ capture keys

  /** Live keys, oldest first. Revoked keys are gone from this list. */
  captureKeys(): Promise<{ keys: CaptureKey[] }> {
    return this.request('GET', '/v1/capture-keys');
  }

  /**
   * Mint a key. 409 when ten live keys already exist (the server's cap); 422 for a blank
   * or over-long name. The plaintext in the answer is shown once and never requested again.
   */
  createCaptureKey(name: string): Promise<CaptureKeyCreated> {
    return this.request('POST', '/v1/capture-keys', { body: { name } });
  }

  /** Revoke. The container holding it gets a 401 from its next upload on. 404 if not yours. */
  revokeCaptureKey(id: string): Promise<void> {
    return this.request('DELETE', `/v1/capture-keys/${encodeURIComponent(id)}`);
  }

  /** Every faction the viewer belongs to, with their role in each. Oldest membership first. */
  myFactions(): Promise<{ factions: MyFaction[] }> {
    return this.request('GET', '/v1/factions/mine');
  }

  /** The creator is the first admin; the response carries `join_code`. */
  createFaction(body: { name: string; slug?: string; open?: boolean; tz?: string }): Promise<Faction> {
    return this.request('POST', '/v1/factions', { body });
  }

  /** By code (`XXXX-XXXX`), or by slug when the faction is open. */
  joinFaction(code: string): Promise<Faction> {
    return this.request('POST', '/v1/factions:join', { body: { code } });
  }

  joinOpenFaction(slug: string): Promise<Faction> {
    return this.request('POST', '/v1/factions:join', { body: { slug } });
  }

  /** `week` is an ISO week like `2026-W33`; omitted means the current one. */
  factionBoard(slug: string, week?: string): Promise<FactionBoard> {
    const qs = week ? `?week=${encodeURIComponent(week)}` : '';
    return this.request('GET', `/v1/factions/${encodeURIComponent(slug)}/board${qs}`);
  }

  setFactionShareHours(
    slug: string,
    shareHours: boolean
  ): Promise<{ slug: string; share_hours: boolean }> {
    return this.request('PATCH', `/v1/factions/${encodeURIComponent(slug)}/members/me`, {
      body: { share_hours: shareHours },
    });
  }

  /**
   * A presigned PUT the phone uploads to directly. The server answers 503 when object
   * storage is not configured; that is a deployment fact, not a bug, so it surfaces as a
   * plain sentence rather than the server's list of env vars.
   */
  async presignMedia(postId: string, body: PresignRequest): Promise<Presign> {
    try {
      return await this.request('POST', `/v1/posts/${encodeURIComponent(postId)}/media:presign`, {
        body,
      });
    } catch (e) {
      if (e instanceof ApiError && e.status === 503) {
        throw new ApiError(503, PRESIGN_UNCONFIGURED_MESSAGE);
      }
      throw e;
    }
  }

  /** After the PUT succeeded: record the object on the post. */
  attachMedia(postId: string, body: MediaAttach): Promise<PostMedia> {
    return this.request('POST', `/v1/posts/${encodeURIComponent(postId)}/media`, { body });
  }

  // -------------------------------------------------------------- transport

  private async request<T>(
    method: 'GET' | 'POST' | 'PATCH' | 'DELETE',
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
      // A 2xx that is not the token pair — a captive portal's page, an intercepting
      // proxy — is not an auth answer either. Keep the pair and report it as transport
      // (status 0), like a timeout; the next sync retries. Clearing here signed people
      // out on hotel wifi. Found by review.
      throw new ApiError(0, 'malformed refresh response');
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
