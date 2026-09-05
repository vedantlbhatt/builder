-- ============================================================================
-- TIER B — cache.sqlite
--
-- A PURE FUNCTION of state.sqlite plus Tuning. DROP + rebuild is the ONLY way anything
-- in this file changes. There are no migrations here, ever.
--
-- Rebuild triggers:
--   meta.tuning_hash != Tuning.version
--   OR new raw rows since meta.built_from_rowid
--   OR the user pressed "rebuild index"
--
-- This is the half of the store where the imessage-analysis house style — drop and
-- rebuild, no migrations, denormalized precomputed columns, one index per access
-- pattern — is exactly right, because everything here is recoverable in ~5 seconds.
-- The half where it would destroy data is state_schema.sql. Keep them separate.
-- ============================================================================

PRAGMA journal_mode = WAL;
PRAGMA synchronous  = NORMAL;
PRAGMA busy_timeout = 5000;

CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

-- ---- session ---------------------------------------------------------------
DROP TABLE IF EXISTS session;
CREATE TABLE session (
  -- sha256("builder-session-v1|" | harness | machine_id | first_event_uid).
  -- No timestamp in the identity: a late-arriving earlier event moves started_at
  -- backwards, and the id would change under every stored reference to it.
  -- No ordinal: a parser_version bump would orphan everything.
  client_session_id   TEXT PRIMARY KEY,
  harness             TEXT NOT NULL,
  started_at          REAL NOT NULL,
  ended_at            REAL NOT NULL,
  wall_seconds        REAL NOT NULL,

  -- |union(turn spans, [t_i, t_i + min(gap_i, activeGapCapSec)])|
  -- The honest headline. Elapsed vs moving, exactly as a running app reports a run that
  -- stopped at a traffic light. INVARIANT to tauSessionSec by construction.
  active_seconds      REAL NOT NULL,
  idle_seconds        REAL NOT NULL,
  active_calc_version INTEGER NOT NULL,
  sessionizer_version INTEGER NOT NULL,

  day                 TEXT,            -- LOCAL date of the session START (see Tuning)
  hour                INTEGER,
  dow                 INTEGER,
  tz_offset_min       INTEGER,
  repo_id_primary     INTEGER,

  title               TEXT,
  title_source        TEXT CHECK (title_source IN ('harness','template')),
  chore_title         INTEGER NOT NULL DEFAULT 0,
  timeline_fidelity   TEXT NOT NULL CHECK (timeline_fidelity IN ('full','coarse','header_only')),
  state               TEXT NOT NULL DEFAULT 'final'
                      CHECK (state IN ('open','idle','finalizing','final')),

  visible             INTEGER NOT NULL,  -- counts toward hours, graph, streaks
  notable             INTEGER NOT NULL,  -- eligible for a card, a record, a notification

  n_prompts           INTEGER,
  prompt_count_basis  TEXT CHECK (prompt_count_basis IN ('typed_promptsource','user_bubble')),
  n_tool_calls        INTEGER,
  n_reads             INTEGER,
  n_edits             INTEGER,
  n_writes            INTEGER,
  n_bash              INTEGER,
  n_files_touched     INTEGER,
  n_files_created     INTEGER,
  n_subagents         INTEGER,
  n_compactions       INTEGER,
  n_human_edit_events INTEGER,          -- edited_text_file. Existence only; carries no line count.

  agent_lines_added   INTEGER,          -- on_live_path = 1 ONLY. Rewound edits never landed.
  agent_lines_removed INTEGER,
  git_commits         INTEGER,
  git_insertions      INTEGER,
  git_deletions       INTEGER,

  -- A 5-way LOWER BOUND, never a percentage on a card. See TokenLedger.swift for the
  -- four independent ways the obvious subtraction fails, all biased the same direction.
  agent_line_bucket   TEXT,
  attrib_confidence   TEXT NOT NULL CHECK (attrib_confidence IN ('high','medium','low','none')),

  tok_in              INTEGER,
  tok_out             INTEGER,
  tok_cache_read      INTEGER,
  tok_cache_w5m       INTEGER,
  tok_cache_w1h       INTEGER,
  abandoned_branch_tokens INTEGER NOT NULL DEFAULT 0,
  tokens_reported     INTEGER NOT NULL, -- 0 for EVERY Cursor session, forever
  token_dedupe        TEXT NOT NULL CHECK (token_dedupe IN ('message_id','request_id','none')),
  token_scope         TEXT NOT NULL CHECK (token_scope IN ('parent_aggregated','mixed')),
  token_coverage      TEXT NOT NULL
                      CHECK (token_coverage IN ('complete','partial','structurally_absent')),

  -- LOCAL DISPLAY ONLY. Never uploaded, never printed on a share card: it is structurally
  -- impossible for Cursor, wrong-by-construction for subscription users, and it is the
  -- fourth thing a stranger reads in the 0.4 seconds they give a screenshot.
  cost_usd            REAL,
  cost_state          TEXT NOT NULL,
  models_json         TEXT,
  model_state         TEXT NOT NULL CHECK (model_state IN ('known','partial','unknown')),

  is_background       INTEGER NOT NULL DEFAULT 0,
  -- ZERO presence signals (typed or remote-human prompt, interrupt, human file edit)
  -- over a notable span. Counts toward hours; never a record, a streak, or a
  -- "session finished" alert. See docs/session-boundaries.md.
  unattended          INTEGER NOT NULL DEFAULT 0,
  time_quality        TEXT NOT NULL DEFAULT 'ok' CHECK (time_quality IN ('ok','mtime_corrected')),
  merge_group_id      TEXT,             -- SEAM for cross-harness merge. Unused today.
  visibility          TEXT NOT NULL DEFAULT 'anonymous',

  -- Two clocks inside active_seconds: attended + autonomous == active_seconds, always.
  -- Attended is active time within Tuning.tauAutonomousSec of a presence signal, and it
  -- is what decides records — a kickoff prompt plus eight autonomous hours scores its
  -- attended minutes, not the machine's night.
  attended_seconds    REAL NOT NULL DEFAULT 0,
  autonomous_seconds  REAL NOT NULL DEFAULT 0,
  n_presence          INTEGER NOT NULL DEFAULT 0,
  -- idle_gap, cleared and switched_repo mean the work stopped, and notify; the two cuts
  -- (human_returned, day_boundary) do not (docs/session-boundaries.md v3).
  end_reason          TEXT NOT NULL DEFAULT 'idle_gap'
                      CHECK (end_reason IN ('idle_gap','human_returned','day_boundary',
                                            'cleared','switched_repo','still_running')),
  -- unattended AND idle_gap: the "Agent run finished" notification class.
  run_finished        INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX session_start_idx   ON session(started_at DESC);
CREATE INDEX session_day_idx     ON session(day);
CREATE INDEX session_notable_idx ON session(notable, started_at DESC);
CREATE INDEX session_record_idx  ON session(attended_seconds DESC)
  WHERE notable = 1 AND unattended = 0 AND time_quality = 'ok';

-- ---- session_repo ----------------------------------------------------------
-- Many-to-many, because .cwd varies within a single transcript file. One repo per
-- session both misattributes git stats AND — the real problem — lets an EXCLUDED
-- repo's events ride along under a merely-anonymous primary.
DROP TABLE IF EXISTS session_repo;
CREATE TABLE session_repo (
  client_session_id TEXT NOT NULL,
  repo_id           INTEGER NOT NULL,
  n_events          INTEGER NOT NULL,
  active_seconds    REAL NOT NULL,
  PRIMARY KEY (client_session_id, repo_id)
);

-- ---- strip -----------------------------------------------------------------
DROP TABLE IF EXISTS strip;
CREATE TABLE strip (
  client_session_id TEXT PRIMARY KEY,
  t0_ms             INTEGER NOT NULL,
  t1_ms             INTEGER NOT NULL,
  -- EXACTLY 1024 bytes. 2-bit class | 2-bit density | 4 bits reserved = 0.
  cols              BLOB NOT NULL,
  -- Marks are stored SEPARATELY from columns so a 5-second prompt cannot be resampled
  -- away. MEASURED: typed prompts are 1,456 events against 23,838 tool calls, so they
  -- are precisely what a segment-list format loses.
  marks             TEXT NOT NULL,     -- json [[ms, kind], ...]
  spec_version      INTEGER NOT NULL DEFAULT 1
);

-- ---- day_rollup ------------------------------------------------------------
DROP TABLE IF EXISTS day_rollup;
CREATE TABLE day_rollup (
  day             TEXT NOT NULL,
  harness         TEXT NOT NULL,
  repo_id         INTEGER NOT NULL DEFAULT -1,
  n_sessions      INTEGER,
  -- UNION of session active intervals, NOT a sum. Two harnesses open in the same repo at
  -- the same time would otherwise double-count the same wall-clock minute, and the
  -- contribution graph is the one number people compare.
  active_seconds  REAL,
  wall_seconds    REAL,
  tok_total       INTEGER,
  tokens_reported INTEGER,
  commits         INTEGER,
  insertions      INTEGER,
  deletions       INTEGER,
  agent_lines     INTEGER,
  level           INTEGER,             -- 0..5 from Tuning.graphHourBuckets, computed once
  PRIMARY KEY (day, harness, repo_id)
);
CREATE INDEX day_rollup_day_idx ON day_rollup(day);

-- ---- project_arc -----------------------------------------------------------
-- The portfolio view: every repo as a timeline from first session to latest.
DROP TABLE IF EXISTS project_arc;
CREATE TABLE project_arc (
  repo_id        INTEGER PRIMARY KEY,
  display_name   TEXT,
  first_session  REAL,
  last_session   REAL,
  n_sessions     INTEGER,
  active_seconds REAL,
  commits        INTEGER,
  agent_lines    INTEGER,
  visibility     TEXT NOT NULL
);

-- ---- record ----------------------------------------------------------------
-- Recomputed from scratch, never accumulated, so a re-derive cannot inflate a record.
-- `most_repos_in_a_week` is deliberately absent: MEASURED, 76% of transcript files on
-- the reference machine belong to one project and 6 of 13 project directories are
-- worktrees of it, so the value would be about 2. A record of "about 2" is not a record.
DROP TABLE IF EXISTS record;
CREATE TABLE record (
  kind              TEXT PRIMARY KEY,
  -- longest_session | longest_streak | biggest_ship_day | most_tokens_day | most_active_day
  value             REAL NOT NULL,
  unit              TEXT NOT NULL,
  day               TEXT,
  client_session_id TEXT,
  previous_value    REAL,
  computed_at       REAL NOT NULL
);
