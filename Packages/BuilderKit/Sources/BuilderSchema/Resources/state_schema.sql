-- ============================================================================
-- TIER A — state.sqlite
--
-- APPEND-ONLY. NEVER DROPPED. FORWARD-ONLY MIGRATIONS ON PRAGMA user_version.
--
-- READ THIS BEFORE EDITING.
--
-- Claude Code runs a 30-day cleanup. `~/.claude/.last-cleanup` advanced from
-- 2026-08-15T23:19Z to 2026-08-16T01:27Z during a single planning session, so the
-- retention job is live and recurring, not theoretical. Cursor keeps 482 conversation
-- headers on the reference machine but message bodies for only 49 composers — 433
-- conversations have already been garbage-collected, and the cliff falls at ~2 months.
--
-- ONCE WE INGEST A DAY, WE ARE THE ONLY COPY OF IT.
--
-- The house style elsewhere (imessage-analysis/schema.sql) is DROP TABLE IF EXISTS plus
-- an unconditional rebuild, which is correct THERE because the source database survives
-- and re-ingest is free. It is not correct here. If you type DROP TABLE in this file you
-- are deleting the user's history. Rebuild-in-place lives in cache_schema.sql instead.
-- ============================================================================

PRAGMA journal_mode = WAL;
PRAGMA synchronous  = NORMAL;
PRAGMA foreign_keys = ON;
PRAGMA busy_timeout = 5000;

-- ---- meta ------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

-- ---- repo ------------------------------------------------------------------
-- Identity is the normalized git origin URL, NOT the folder name.
-- MEASURED: the reference machine's flagship project lives in a directory called
-- `RideGT` while its origin is github.com/vedantlbhatt/gt-transit, and 6 of 13 Claude
-- Code project directories are worktree slugs of that same repository. Resolving with
-- `git rev-parse --show-toplevel` fragments one repo into seven project arcs TODAY.
-- Use --git-common-dir.
CREATE TABLE IF NOT EXISTS repo (
  repo_id         INTEGER PRIMARY KEY,
  origin_url_norm TEXT UNIQUE,          -- 'github.com/vedantlbhatt/gt-transit'
  common_root     TEXT,                 -- dirname(git rev-parse --git-common-dir)
  root_commit     TEXT,                 -- identity fallback: stable across clones AND machines
  display_name    TEXT,
  repo_hash       TEXT UNIQUE,          -- full 64 hex HMAC. NULL when no origin and no commits.
  repo_id_basis   TEXT CHECK (repo_id_basis IN ('origin','root_commit')),
  pepper_version  INTEGER NOT NULL DEFAULT 1,
  visibility      TEXT NOT NULL DEFAULT 'anonymous'
                  CHECK (visibility IN ('public','anonymous','excluded')),
  first_seen_ts   REAL,
  last_seen_ts    REAL
);
CREATE INDEX IF NOT EXISTS repo_hash_idx ON repo(repo_hash);

-- Path -> repo, resolved at INGEST time rather than at derive time. Worktrees get
-- deleted; once the directory is gone `git -C <cwd> rev-parse` fails and the session
-- would lose its project forever. On a miss, callers fall back to the longest matching
-- path prefix already recorded here.
CREATE TABLE IF NOT EXISTS path_repo (
  path            TEXT PRIMARY KEY,
  common_root     TEXT,
  origin_url_norm TEXT,
  root_commit     TEXT,
  repo_id         INTEGER REFERENCES repo(repo_id),
  resolved_at     REAL NOT NULL
);

-- ---- raw_event -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS raw_event (
  -- sha256(harness | source_id | native_event_id).
  -- NO ORDINAL IN THE IDENTITY. Embedding it means a parser_version bump shifts every
  -- uid, INSERT OR IGNORE stops suppressing re-ingested rows, and the partial unique
  -- index below aborts the source's transaction -- which also holds its watermark, so
  -- the source never advances again.
  event_uid           TEXT PRIMARY KEY,
  harness             TEXT NOT NULL,
  source_id           TEXT NOT NULL,   -- hash of a canonical descriptor, not the mutable path
  ordinal             INTEGER NOT NULL,-- file order. NOT time order. Not identity.
  native_session_id   TEXT,
  native_event_id     TEXT,
  native_parent_id    TEXT,            -- .parentUuid ?? .logicalParentUuid
  agent_id            TEXT,
  is_sidechain        INTEGER NOT NULL DEFAULT 0,

  -- NULL until derive. 0 marks an abandoned rewind branch.
  -- MEASURED: 225 fork points. Those branches carry valid timestamps and distinct
  -- message.id values, so message-id dedupe does not touch them.
  on_live_path        INTEGER,

  -- Unix seconds. NULL on ~24,151 of 108,504 records, all bookkeeping. NEVER IMPUTED:
  -- interpolating between file-order neighbours can run the clock backwards, because
  -- MEASURED 2,472 adjacent pairs are already out of chronological order.
  ts                  REAL,
  day                 TEXT,            -- LOCAL date, denormalized for the graph
  hour                INTEGER,
  dow                 INTEGER,
  tz_offset_min       INTEGER,

  kind                TEXT NOT NULL,
  role                TEXT,
  cwd                 TEXT,            -- VARIES WITHIN A FILE. Never from the slugified dir name.
  repo_id             INTEGER REFERENCES repo(repo_id),
  harness_version     TEXT,
  model               TEXT,            -- NULLIF(model,'') at parse; [1m] suffix kept verbatim
  effort              TEXT,
  service_tier        TEXT,

  dedupe_key          TEXT,            -- source_id || '|' || message.id
  usage_authoritative INTEGER NOT NULL DEFAULT 0,
  tok_in              INTEGER,
  tok_out             INTEGER,
  tok_cache_read      INTEGER,
  tok_cache_w5m       INTEGER,         -- 5m and 1h SEPARATE: they price differently
  tok_cache_w1h       INTEGER,

  tool_name           TEXT,
  tool_id             TEXT,
  target_path         TEXT,            -- LOCAL ONLY. Never uploaded.
  lines_added         INTEGER,
  lines_removed       INTEGER,

  duration_ms         INTEGER,         -- system/turn_duration. 1,904 records = 6% coverage.
  segment_source      TEXT CHECK (segment_source IN ('measured','derived')),

  title               TEXT,
  leaf_uuid           TEXT,            -- last-prompt.leafUuid -> which session owns the title
  extra               TEXT,            -- small json. NEVER prompt/code/diff text.
  ingested_at         REAL NOT NULL
);

-- *** THE 1.878x OVERCOUNT KILLER ***
-- MEASURED: 44,419 assistant records carry .message.usage but there are only 22,887
-- distinct .message.id values, because Claude Code writes ONE RECORD PER CONTENT BLOCK
-- and repeats the identical usage object on each one.
--   naive sum   10,922,288,007
--   deduped      5,815,701,063
--   ratio                1.878
-- This index makes a second authoritative row a CONSTRAINT VIOLATION rather than a
-- summation choice someone can get wrong later.
CREATE UNIQUE INDEX IF NOT EXISTS raw_event_usage_uniq
  ON raw_event(dedupe_key) WHERE usage_authoritative = 1;

CREATE INDEX IF NOT EXISTS raw_event_sess_ts_idx ON raw_event(native_session_id, ts);
CREATE INDEX IF NOT EXISTS raw_event_ts_idx      ON raw_event(ts);
CREATE INDEX IF NOT EXISTS raw_event_day_idx     ON raw_event(day, harness);
CREATE INDEX IF NOT EXISTS raw_event_repo_ts_idx ON raw_event(repo_id, ts);
CREATE INDEX IF NOT EXISTS raw_event_source_idx  ON raw_event(source_id, ordinal);
CREATE INDEX IF NOT EXISTS raw_event_parent_idx  ON raw_event(source_id, native_parent_id);
CREATE INDEX IF NOT EXISTS raw_event_edits_idx   ON raw_event(target_path, ts)
  WHERE lines_added IS NOT NULL;

-- ---- ingest_watermark ------------------------------------------------------
CREATE TABLE IF NOT EXISTS ingest_watermark (
  source_id      TEXT PRIMARY KEY,
  harness        TEXT NOT NULL,
  path           TEXT NOT NULL,
  kind           TEXT NOT NULL,        -- jsonl | sqlite

  -- ALWAYS positioned just past a '\n'. A partial trailing line is NEVER consumed:
  -- transcripts are appended to live, so the last line in the file is routinely half
  -- written, and committing an offset mid-line loses that record permanently.
  byte_offset    INTEGER NOT NULL DEFAULT 0,
  line_count     INTEGER NOT NULL DEFAULT 0,

  st_dev         INTEGER,
  st_ino         INTEGER,
  size_bytes     INTEGER,
  mtime          REAL,
  head_sha256    TEXT,                 -- first 64 KiB: catches same-size in-place rewrites

  last_row_key   TEXT,                 -- sqlite sources: max(rowid) or max(lastUpdatedAt)
  parser_version INTEGER NOT NULL DEFAULT 1,

  -- Cursor vacuums while running, so a read can race the GC and see bodies missing for
  -- a conversation that had them moments ago. Fidelity is MONOTONIC UPWARD ONLY; this
  -- column records when bodies were first observed absent so a transient SQLITE_BUSY
  -- cannot permanently downgrade a good session.
  bodies_missing_first_seen_at REAL,

  completed_at   REAL
);

-- ---- git_cache -------------------------------------------------------------
-- Tier A, not Tier B: the repository may be deleted from disk before we could ever
-- recompute this, and then the numbers are gone for good.
CREATE TABLE IF NOT EXISTS git_cache (
  repo_id         INTEGER NOT NULL,
  win_start       REAL NOT NULL,
  win_end         REAL NOT NULL,
  commits         INTEGER,
  insertions      INTEGER,
  deletions       INTEGER,
  files_changed   INTEGER,
  author_filtered INTEGER,
  computed_at     REAL,
  PRIMARY KEY (repo_id, win_start, win_end)
);

-- ---- session_identity ------------------------------------------------------
-- Survives a cache rebuild, because it holds ids the SERVER assigned. Losing these
-- would orphan every synced session and re-upload the entire history as new rows.
CREATE TABLE IF NOT EXISTS session_identity (
  client_session_id TEXT PRIMARY KEY,
  server_session_id TEXT,
  public_slug       TEXT,
  first_event_uid   TEXT NOT NULL,
  synced_at         REAL,
  content_hash      TEXT
);

-- ---- session_lifecycle -----------------------------------------------------
-- The completion state machine, persisted rather than held in memory, so that quitting
-- the app mid-finalization does not lose a session or re-notify about it later.
CREATE TABLE IF NOT EXISTS session_lifecycle (
  client_session_id TEXT PRIMARY KEY,
  state             TEXT NOT NULL CHECK (state IN ('open','idle','finalizing','final')),
  last_event_ts     REAL NOT NULL,
  entered_state_at  REAL NOT NULL,
  finalized_at      REAL
);
CREATE INDEX IF NOT EXISTS session_lifecycle_state_idx ON session_lifecycle(state, last_event_ts);

-- ---- notification_log ------------------------------------------------------
-- Exactly-once guard for "your session finished". Keyed on the session id, so a restart
-- during finalization cannot produce a second alert and a crash cannot silently drop one.
CREATE TABLE IF NOT EXISTS notification_log (
  client_session_id TEXT PRIMARY KEY,
  notified_at       REAL NOT NULL,
  channel           TEXT NOT NULL      -- local | push
);

-- ---- upload_queue ----------------------------------------------------------
CREATE TABLE IF NOT EXISTS upload_queue (
  id                INTEGER PRIMARY KEY,
  client_session_id TEXT NOT NULL,
  payload           TEXT NOT NULL,
  attempts          INTEGER NOT NULL DEFAULT 0,
  next_attempt_at   REAL,
  last_error        TEXT,
  created_at        REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS upload_queue_next_idx ON upload_queue(next_attempt_at);

-- ---- diagnostics -----------------------------------------------------------
-- Every silent-failure mode gets a code here rather than a log line, because the whole
-- correctness story of this product is "the numbers are right", and a parser that
-- degrades quietly is indistinguishable from one that works.
CREATE TABLE IF NOT EXISTS diagnostics (
  id        INTEGER PRIMARY KEY,
  ts        REAL NOT NULL,
  harness   TEXT,
  source_id TEXT,
  code      TEXT NOT NULL,
  --   unknown_model | oversized_line_skipped | sqlite_open_failed | cursor_gc_race
  -- | tool_result_imbalance | write_shape_unknown | turn_duration_mismatch
  -- | workflow_no_usage | fork_branch_abandoned | unknown_record_shape
  -- | timestamp_out_of_order | repo_unresolved
  detail    TEXT,
  count     INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS diagnostics_code_idx ON diagnostics(code, ts);
