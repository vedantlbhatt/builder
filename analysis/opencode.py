"""Read one opencode (sst/opencode) session into the same `Ev` list the Claude Code loader
produces, so `digest.stats` / `digest.render` / `run.analyze` work unchanged.

The current store is ONE SQLITE DATABASE for every project and session:

    ~/.local/share/opencode/opencode.db          (XDG data dir; see `db_candidates`)
        session   one row per session; `parent_id` set on subagent sessions
        message   one row per message: {id, session_id, time_created, time_updated, data}
        part      one row per part:    {id, message_id, session_id, time_*, data}

`data` is JSON — the v1 `Info` (message) or `Part` minus the id/foreign-key fields that
became columns. A session is addressed as `<db path>/<session id>` (a virtual path: the
last component is the `session.id`); the bare database resolves to its most recently
updated root session and `meta["session_selected_by"]` says so. Two older/side containers
share the same objects and are read by the same code: the pre-SQLite JSON directory store
(`<data>/storage/session/<projectID>/<sessionID>.json` + `storage/message/<sessionID>/
<messageID>.json` + `storage/part/<messageID>/<partID>.json`) and an `opencode export`
file (`{info, messages: [{info, parts}]}`).

Every shape below is marked VERIFIED (read from sst/opencode `dev` — the default branch —
at commit e2894562f8ba943d72172d10b727c24d5f650c16, package version 1.18.29, fetched
2026-09-05 via raw.githubusercontent.com/sst/opencode/dev/<path>) or ASSUMED. The house
rule applies: a parser written from a description ships with a diagnostics-first probe
(`python -m analysis probe ~/.local/share/opencode`), and the first real corpus decides
what this file got wrong before any number reaches a card.

VERIFIED shapes and where they come from
----------------------------------------
* Database path: `Global.Path.data` is `xdgData/opencode` (packages/core/src/global.ts);
  the file is `opencode.db` for the latest/beta/prod channels and `opencode-<channel>.db`
  otherwise, or `$OPENCODE_DB` (packages/core/src/database/database.ts `path()`). It is
  opened with `PRAGMA journal_mode = WAL` (same file), so it is read here with
  `mode=ro` and the connection is closed after every read — never `immutable=1`, which
  skips the WAL and returns stale rows with no error (the Cursor lesson in CLAUDE.md).
* Tables — packages/core/src/session/sql.ts (`SessionTable`, `MessageTable`, `PartTable`)
  and the CREATE TABLE text in packages/core/src/database/schema.gen.ts. `session` has
  `id, project_id, workspace_id, parent_id, slug, directory, path, title, version,
  share_url, summary_*, metadata, cost, tokens_input, tokens_output, tokens_reasoning,
  tokens_cache_read, tokens_cache_write, revert, permission, agent, model, time_created,
  time_updated, time_compacting, time_archived`; `project` has `id, worktree, vcs, name,
  …` (project/sql.ts). `tokens_*`/`cost` were ADDED by migration `20260510033149_session_
  usage`, which backfilled them as the SUM of `message.data.tokens.*` / `.cost` over the
  session's assistant messages; older databases do not have the columns.
* Row contents: `data` is `Omit<SessionV1.Info, "id" | "sessionID">` for a message and
  `Omit<SessionV1.Part, "id" | "sessionID" | "messageID">` for a part (sql.ts type
  parameters; packages/opencode/src/cli/cmd/import.ts writes exactly that, with
  `time_created = info.time.created` for messages and the insert-time default for parts).
  Reads order messages by `(time_created, id)` and parts by `id`
  (packages/opencode/src/session/message-v2.ts `page`, `parts`).
* `SessionV1` — packages/schema/src/v1/session.ts. `User {id, sessionID, role: "user",
  time: {created}, agent, model: {providerID, modelID, variant?}, format?, summary?,
  system?, tools?}`; `Assistant {id, sessionID, role: "assistant", parentID (the user
  message id), modelID, providerID, mode, agent, path: {cwd, root}, time: {created,
  completed?}, error?, summary?: bool, cost, tokens: {total?, input, output, reasoning,
  cache: {read, write}}, finish?, variant?, structured?}`. Every time is epoch MILLISECONDS.
  Ids: `msg_`/`prt_` + 26 chars ascending, `ses_` + 26 chars DESCENDING (session-id.ts).
* Parts (same file): `text {text, synthetic?, ignored?, time?: {start, end?}}`,
  `reasoning`, `file {mime, filename?, url, source?}`, `tool {callID, tool, state,
  metadata?}`, `step-start {snapshot?}`, `step-finish {reason, snapshot?, cost, tokens}`,
  `snapshot {snapshot}`, `patch {hash, files[]}`, `agent {name}`, `retry {attempt, error:
  {name: "APIError", data}, time: {created}}`, `compaction {auto, overflow?,
  tail_start_id?}`, `subtask {prompt, description, agent, model?, command?}`.
  `ToolState` is `pending {input, raw}` | `running {input, title?, metadata?, time:
  {start}}` | `completed {input, output, title, metadata, time: {start, end,
  compacted?}, attachments?}` | `error {input, error, metadata?, time: {start, end}}`.
* THE TOKEN TRAP IS AN UNDERCOUNT, NOT AN OVERCOUNT. On every `step-finish` the processor
  writes a `step-finish` PART with that API call's usage and ASSIGNS the message's tokens
  (`ctx.assistantMessage.tokens = usage.tokens`) while ACCUMULATING cost (`cost +=`)
  — packages/opencode/src/session/processor.ts, `case "step-finish"`. An assistant
  message's `tokens` is therefore its LAST step only; the per-call record is the
  `step-finish` parts. Summing `message.data.tokens` (which is what the session_usage
  backfill did) undercounts every multi-step turn; summing both double counts. `usage()`
  reports the message sum, the step-finish sum and the session row side by side.
* Cache tokens are DISJOINT from `input`: `Session.getUsage` subtracts cache read and
  cache write from the provider's input count (`adjustedInputTokens`), and `output`
  excludes `reasoning` (packages/opencode/src/session/session.ts `getUsage`). The context
  size of a call is input + cache.read + cache.write; `total` is the provider's
  `totalTokens`, when it sent one.
* Tool ids — each `Tool.define("<id>", …)`: `bash` (packages/opencode/src/tool/shell/id.ts
  `ToolID = "bash"`, kept for compatibility; the file is shell.ts), `edit`, `write`,
  `apply_patch`, `read`, `glob`, `grep`, `task`, `webfetch`, `websearch`, `todowrite`,
  `skill`, `question`, `lsp`, `plan_exit`, `invalid` (tool/registry.ts lists them; `execute`
  is experimental code mode). Arguments: `bash {command, timeout?, workdir?,
  description?}`; `edit {filePath, oldString, newString, replaceAll?}`; `write {filePath,
  content}`; `apply_patch {patchText}`; `read {filePath, offset?, limit?}`. `edit`/`write`
  are hidden and `apply_patch` shown for `gpt-*` models (registry.ts `usePatch`).
* Tool result metadata: `bash` completed → `metadata {output, exit, truncated,
  outputPath?}` (shell.ts `run`), so a non-zero `exit` is the error signal even though
  `status` is `completed`; `edit` → `metadata {diff, filediff: {file, patch, additions,
  deletions}, diagnostics}` (edit.ts, counted with `diffLines`); `write` → `metadata
  {filepath, exists, diagnostics}` and `output` "Wrote file successfully." (write.ts);
  `apply_patch` → permission/result `files: [{filePath, relativePath, type, patch,
  additions, deletions}]` (apply_patch.ts); `task` → `metadata {parentSessionId,
  sessionId, model, background?}` where `sessionId` is the CHILD session (task.ts).
* Interrupts: an aborted turn gets `error: {name: "MessageAbortedError", data: {message}}`
  (message-v2.ts `fromError`, prompt.ts `finalizeInterruptedAssistant`) and every tool call
  still open is rewritten to `state: {status: "error", error: "Tool execution aborted",
  metadata: {…, interrupted: true}}` (processor.ts `cleanup`; prompt.ts
  `isOrphanedInterruptedTool`). The message error is the interrupt; the tool marker is
  counted, not emitted a second time as an error.
* Compaction: `SessionCompaction.create` writes a USER message whose only part is
  `compaction {auto, overflow?}`, then `process` writes an ASSISTANT message with
  `summary: true`, `agent: "compaction"` whose text part is the summary; auto-compaction
  may then add a synthetic user "continue" message (compaction.ts). The compaction part is
  the event; the summary message is a real API call (its tokens count) but not a reply.
* Subagents: the `task` tool creates a CHILD SESSION (`sessions.create({parentID:
  ctx.sessionID, …})`, task.ts) and prompts it with the task text as an ordinary
  non-synthetic user text part. A child session therefore has "prompts" nobody typed:
  when `session.parent_id` is set, user text is counted (`prompt_in_child_session`)
  and never emitted as a prompt. Child tokens are separate API calls, not copies of the
  parent's; the parent's `task` part carries no usage.
* User messages that are not prompts: the `/shell` path (`!cmd`) writes a user message
  with ONE synthetic text part "The following tool was executed by the user" plus an
  assistant message with a `bash` tool part and zero tokens (prompt.ts `shellImpl`);
  file attachments expand to synthetic text parts ("Called the Read tool with the
  following input: …"), MCP resources and `@agent` mentions likewise (prompt.ts
  `resolvePart`); auto-compaction continuations are `synthetic: true`. A prompt is the
  join of a user message's text parts with neither `synthetic` nor `ignored` set.
* Revert: `session.revert {messageID, partID?, snapshot?, diff?}` marks a pending revert;
  the next prompt's `cleanup` DELETES those messages/parts (revert.ts) — reverted work is
  removed from the store, not flagged. While pending, messages at/after the mark are
  counted (`reverted_pending_cleanup`) and skipped.
* `session_message` (+ `session_input`, `session_context_epoch`, `event`) is the newer
  event-sourced projection of the SAME turns in a different shape (core/session/sql.ts,
  schema/session-message.ts); migration `20260622170816_reset_v2_session_state` wipes it.
  It is never read for events or tokens here — unioning it with `message` double counts.
  The probe reports its row count for the session.
* JSON directory store layout: packages/opencode/src/storage/storage.ts migration 1 writes
  `<data>/storage/session/<projectID>/<sessionID>.json`, `storage/message/<sessionID>/
  <messageID>.json`, `storage/part/<messageID>/<partID>.json`; migration 2 reads
  `session/*/*.json`. `opencode export` / `import` file: `{info: Session.Info, messages:
  [{info: SessionV1.Info, parts: SessionV1.Part[]}]}` (cli/cmd/import.ts `ExportData`).

ASSUMED (not in the current source; handled leniently and counted in diagnostics)
-------------------------------------------------------------------------------
* The JSON directory store is no longer WRITTEN — every current reader/writer goes through
  drizzle and the `message`/`part` tables — but the files survive an upgrade on disk. The
  message/part JSON there is assumed to be the full `SessionV1.Info` / `Part` object
  (the migration copies the files verbatim and decodes only `{id}`; the import path shows
  the same objects with `id`/`sessionID`/`messageID` split off). An even older form
  (packages/opencode/src/session/message.ts: `{id, role, parts: [...], metadata:
  {assistant: {tokens, …}}}`) is recognised by its `metadata.assistant` and counted as
  `legacy_v0_message`, never parsed.
* How `session.tokens_*` / `cost` are maintained live after the backfill was not found
  (the writer behind `Session.updateMessage` is an event projection not fetched). The
  session row is reported next to both sums, never chosen.
* `write` line credit is the line count of `content` even when `metadata.exists` is true
  (an overwrite); `edit` credit prefers `metadata.filediff.additions/deletions` and falls
  back to a difflib line diff of `oldString` → `newString`; `apply_patch` credit prefers
  the per-file `additions/deletions` in metadata and falls back to counting `+`/`-` lines
  of `patchText`. None has been compared against a corpus.
* `text.ignored: true` parts are not shown to the model (the field name); they are counted
  and skipped.
* A `subtask` part in a user message (an `@agent` mention or a command that dispatches a
  subagent) is a human action; its `description` is emitted as the prompt when the message
  has no human text, counted `prompt_from_subtask`.
* The store records no human file edits (no part type carries one; `patch`/`snapshot`
  parts are the agent's own snapshots), so `human_edits` is always 0 here.

Nothing here reads `reasoning` parts, for the same reason the Claude Code loader does not
read thinking blocks.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import difflib
import json
import pathlib
import re
import sqlite3
from collections import Counter

from . import digest as dg

HARNESS = "opencode"

SQLITE_MAGIC = b"SQLite format 3\x00"
# VERIFIED: the three tables every opencode database has had since the first migration.
REQUIRED_TABLES = frozenset({"session", "message", "part"})
STORAGE_DIR = "storage"

# VERIFIED: tool ids from each `Tool.define` (see docstring).
SHELL_TOOL = "bash"
EDIT_TOOL = "edit"
WRITE_TOOL = "write"
PATCH_TOOL = "apply_patch"
READ_TOOL = "read"
TASK_TOOL = "task"
KNOWN_TOOLS = frozenset(
    {
        SHELL_TOOL,
        EDIT_TOOL,
        WRITE_TOOL,
        PATCH_TOOL,
        READ_TOOL,
        TASK_TOOL,
        "glob",
        "grep",
        "webfetch",
        "websearch",
        "todowrite",
        "skill",
        "question",
        "lsp",
        "plan_exit",
        "invalid",
        "execute",
    }
)
# VERIFIED: schema/src/v1/session.ts `Part` union.
KNOWN_PART_TYPES = frozenset(
    {
        "text",
        "subtask",
        "reasoning",
        "file",
        "tool",
        "step-start",
        "step-finish",
        "snapshot",
        "patch",
        "agent",
        "retry",
        "compaction",
    }
)
KNOWN_TOOL_STATUSES = frozenset({"pending", "running", "completed", "error"})
# VERIFIED: schema/src/v1/session.ts `AssistantErrorSchema` names.
KNOWN_ERROR_NAMES = frozenset(
    {
        "ProviderAuthError",
        "UnknownError",
        "MessageOutputLengthError",
        "MessageAbortedError",
        "StructuredOutputError",
        "ContextOverflowError",
        "ContentFilterError",
        "APIError",
    }
)
ABORTED_ERROR = "MessageAbortedError"
TOOL_ABORTED_TEXT = "Tool execution aborted"  # VERIFIED: processor.ts `cleanup`
USER_SHELL_TEXT = "The following tool was executed by the user"  # VERIFIED: prompt.ts

TOKEN_KEYS = ("input", "output", "reasoning", "cache_read", "cache_write", "total")

_PATCH_PATH = re.compile(
    r"^\*\*\* (?:Update|Add|Move|Delete) File: (?P<path>.+?)\s*$", re.MULTILINE
)
_EPOCH_MS_MIN = 1_000_000_000_000  # 2001-09-09
_EPOCH_MS_MAX = 10_000_000_000_000  # 2286-11-20


# ----------------------------------------------------------------------------- locate


def db_candidates(data_dir: pathlib.Path | None = None) -> list[pathlib.Path]:
    """`opencode.db` and any `opencode-<channel>.db` under the XDG data dir (or `data_dir`).

    VERIFIED naming: database.ts `path()`. `$OPENCODE_DB` is honoured when absolute."""
    import os

    if data_dir is None:
        base = os.environ.get("XDG_DATA_HOME") or str(pathlib.Path.home() / ".local" / "share")
        data_dir = pathlib.Path(base) / "opencode"
    out = []
    env = os.environ.get("OPENCODE_DB")
    if env and env != ":memory:":
        p = pathlib.Path(env) if os.path.isabs(env) else data_dir / env
        if p.is_file():
            out.append(p)
    out += sorted(p for p in data_dir.glob("opencode*.db") if p.is_file())
    return out


def is_database(path: pathlib.Path) -> bool:
    """A file with the SQLite header AND the `session`/`message`/`part` tables. Cursor's
    `state.vscdb` and Codex's `state_N.sqlite` share the header, not the tables."""
    path = pathlib.Path(path)
    try:
        if not path.is_file():
            return False
        with path.open("rb") as f:
            if f.read(16) != SQLITE_MAGIC:
                return False
        return REQUIRED_TABLES <= set(_tables(path))
    except (OSError, sqlite3.Error):
        return False


def _connect(path: pathlib.Path) -> sqlite3.Connection:
    # mode=ro, never immutable=1: the writer runs WAL and a live -wal file would be skipped.
    con = sqlite3.connect(f"file:{pathlib.Path(path).resolve()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def _tables(path: pathlib.Path) -> list[str]:
    con = _connect(path)
    try:
        return [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
    finally:
        con.close()


def split_db_path(path: pathlib.Path) -> tuple[pathlib.Path, str | None] | None:
    """(database file, session id or None) for `<db>` or `<db>/<session id>`; else None."""
    path = pathlib.Path(path)
    if path.is_file():
        return (path, None) if is_database(path) else None
    if not path.exists() and path.parent.is_file() and is_database(path.parent):
        return path.parent, path.name
    return None


def is_session_file(path: pathlib.Path) -> bool:
    """`storage/session/<projectID>/<sessionID>.json` — decided on path shape plus the
    session object's `id`."""
    path = pathlib.Path(path)
    if path.suffix != ".json" or not path.is_file():
        return False
    if path.parent.parent.name != "session":
        return False
    try:
        obj = json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return False
    return isinstance(obj, dict) and isinstance(obj.get("id"), str) and obj["id"].startswith("ses")


def is_export_file(path: pathlib.Path) -> bool:
    """`opencode export` output: `{info: {id: "ses…"}, messages: [...]}`."""
    path = pathlib.Path(path)
    if path.suffix != ".json" or not path.is_file():
        return False
    try:
        obj = json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return False
    return _is_export_object(obj)


def _is_export_object(obj) -> bool:
    return (
        isinstance(obj, dict)
        and isinstance(obj.get("info"), dict)
        and isinstance(obj["info"].get("id"), str)
        and str(obj["info"]["id"]).startswith("ses")
        and isinstance(obj.get("messages"), list)
    )


def detect(path: pathlib.Path) -> str | None:
    """ "sqlite", "json_dir", "export_json" or None."""
    path = pathlib.Path(path)
    if split_db_path(path) is not None:
        return "sqlite"
    if is_session_file(path):
        return "json_dir"
    if is_export_file(path):
        return "export_json"
    return None


def list_sessions(db: pathlib.Path) -> list[dict]:
    """Every session row's id / parent_id / title / time_updated, oldest first."""
    con = _connect(db)
    try:
        rows = con.execute(
            "SELECT id, parent_id, title, time_created, time_updated FROM session "
            "ORDER BY time_created, id"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


# ----------------------------------------------------------------------------- scan


@dataclasses.dataclass
class Scan:
    """Everything one read of a session yields. `load_events`, `meta`, `usage` and
    `diagnostics` are views on this; the probe prints all of it."""

    path: pathlib.Path
    container: str  # sqlite | json_dir | export_json
    session: dict  # session info / row as a dict (keys as stored)
    project: dict | None
    messages: list[tuple[dict, list[dict]]]  # (info, parts) in (time.created, id) order
    meta: dict
    usage: dict
    diagnostics: dict


def _ms(v) -> float | None:
    """Epoch milliseconds → seconds; anything outside the epoch-ms range is not a clock."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    if _EPOCH_MS_MIN <= v < _EPOCH_MS_MAX:
        return v / 1000.0
    return None


def _iso(ts: float | None) -> str | None:
    if ts is None:
        return None
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _num(v) -> int | float | None:
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _json(v):
    if isinstance(v, (bytes, bytearray)):
        v = v.decode("utf-8", "replace")
    if isinstance(v, str):
        try:
            return json.loads(v)
        except json.JSONDecodeError:
            return None
    return v


def _zero_tokens() -> dict:
    return {k: 0 for k in TOKEN_KEYS}


def _tokens_of(t) -> dict | None:
    """Flatten `{input, output, reasoning, cache: {read, write}, total?}`."""
    if not isinstance(t, dict):
        return None
    cache = t.get("cache") if isinstance(t.get("cache"), dict) else {}
    out = _zero_tokens()
    for k, v in (
        ("input", t.get("input")),
        ("output", t.get("output")),
        ("reasoning", t.get("reasoning")),
        ("cache_read", cache.get("read")),
        ("cache_write", cache.get("write")),
        ("total", t.get("total")),
    ):
        n = _num(v)
        if n is not None:
            out[k] = int(n)
    return out


def _add(acc: dict, t: dict | None) -> None:
    if t:
        for k in TOKEN_KEYS:
            acc[k] += t.get(k, 0)


def _read_sqlite(db: pathlib.Path, session_id: str | None, diag: dict):
    """(session row, project row, [(info, parts)], extras) from one read-only connection,
    closed before returning so the writer can checkpoint."""
    con = _connect(db)
    try:
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        diag["tables"] = sorted(tables)
        if "migration" in tables:
            row = con.execute("SELECT count(*) AS n, max(id) AS last FROM migration").fetchone()
            diag["migrations_applied"] = row["n"]
            diag["last_migration"] = row["last"]
        selected_by = "path"
        if session_id is None:
            row = con.execute(
                "SELECT id FROM session WHERE parent_id IS NULL "
                "ORDER BY time_updated DESC, id LIMIT 1"
            ).fetchone()
            if row is None:
                return None, None, [], {"selected_by": "none"}
            session_id = row["id"]
            selected_by = "latest_root_session"
        srow = con.execute("SELECT * FROM session WHERE id = ?", (session_id,)).fetchone()
        if srow is None:
            return None, None, [], {"selected_by": "missing"}
        session = dict(srow)
        project = None
        if "project" in tables and session.get("project_id"):
            prow = con.execute(
                "SELECT * FROM project WHERE id = ?", (session["project_id"],)
            ).fetchone()
            project = dict(prow) if prow else None
        mrows = con.execute(
            "SELECT id, session_id, time_created, time_updated, data FROM message "
            "WHERE session_id = ? ORDER BY time_created, id",
            (session_id,),
        ).fetchall()
        prows = con.execute(
            "SELECT id, message_id, time_created, data FROM part WHERE session_id = ? "
            "ORDER BY message_id, id",
            (session_id,),
        ).fetchall()
        parts_by_msg: dict[str, list[dict]] = {}
        for r in prows:
            d = _json(r["data"])
            if not isinstance(d, dict):
                diag["malformed_lines"] += 1
                continue
            d = dict(d, id=r["id"], messageID=r["message_id"], sessionID=session_id)
            parts_by_msg.setdefault(r["message_id"], []).append(d)
        messages = []
        for r in mrows:
            d = _json(r["data"])
            if not isinstance(d, dict):
                diag["malformed_lines"] += 1
                continue
            d = dict(d, id=r["id"], sessionID=session_id)
            d["_row_time_created"] = r["time_created"]
            messages.append((d, parts_by_msg.pop(r["id"], [])))
        extras = {
            "selected_by": selected_by,
            "orphan_parts": sum(len(v) for v in parts_by_msg.values()),
            "children": [
                dict(r)
                for r in con.execute(
                    "SELECT id, title, time_created FROM session WHERE parent_id = ? "
                    "ORDER BY time_created, id",
                    (session_id,),
                ).fetchall()
            ],
            "projection_rows": None,
        }
        if "session_message" in tables:
            extras["projection_rows"] = con.execute(
                "SELECT count(*) FROM session_message WHERE session_id = ?", (session_id,)
            ).fetchone()[0]
        return session, project, messages, extras
    finally:
        con.close()


def _read_json_dir(session_file: pathlib.Path, diag: dict):
    """The legacy directory store, rooted two levels above the session file."""
    root = session_file.parent.parent.parent  # …/storage
    session = _json(session_file.read_bytes())
    if not isinstance(session, dict):
        diag["malformed_lines"] += 1
        return None, None, [], {"selected_by": "path"}
    sid = session.get("id")
    project = None
    pfile = root / "project" / f"{session.get('projectID')}.json"
    if pfile.is_file():
        p = _json(pfile.read_bytes())
        project = p if isinstance(p, dict) else None
    messages: list[tuple[dict, list[dict]]] = []
    mdir = root / "message" / str(sid)
    legacy_v0 = 0
    for mf in sorted(mdir.glob("*.json")) if mdir.is_dir() else []:
        m = _json(mf.read_bytes())
        if not isinstance(m, dict):
            diag["malformed_lines"] += 1
            continue
        if isinstance(m.get("metadata"), dict) and "assistant" in m["metadata"]:
            legacy_v0 += 1
            continue
        m.setdefault("id", mf.stem)
        m.setdefault("sessionID", sid)
        pdir = root / "part" / str(m["id"])
        parts = []
        for pf in sorted(pdir.glob("*.json")) if pdir.is_dir() else []:
            p = _json(pf.read_bytes())
            if not isinstance(p, dict):
                diag["malformed_lines"] += 1
                continue
            p.setdefault("id", pf.stem)
            p.setdefault("messageID", m["id"])
            p.setdefault("sessionID", sid)
            parts.append(p)
        parts.sort(key=lambda p: str(p.get("id")))
        messages.append((m, parts))
    messages.sort(key=lambda mp: (_num((mp[0].get("time") or {}).get("created")) or 0, mp[0]["id"]))
    children = []
    sdir = session_file.parent
    for f in sorted(sdir.glob("*.json")):
        if f == session_file:
            continue
        o = _json(f.read_bytes())
        if isinstance(o, dict) and o.get("parentID") == sid:
            children.append({"id": o.get("id"), "title": o.get("title")})
    extras = {"selected_by": "path", "orphan_parts": 0, "children": children}
    if legacy_v0:
        extras["legacy_v0_message"] = legacy_v0
    return session, project, messages, extras


def _read_export(path: pathlib.Path, diag: dict):
    obj = _json(path.read_bytes())
    if not _is_export_object(obj):
        diag["malformed_lines"] += 1
        return None, None, [], {"selected_by": "path"}
    session = obj["info"]
    messages = []
    for m in obj["messages"]:
        if not isinstance(m, dict) or not isinstance(m.get("info"), dict):
            diag["malformed_lines"] += 1
            continue
        info = dict(m["info"])
        info.setdefault("sessionID", session.get("id"))
        parts = [p for p in (m.get("parts") or []) if isinstance(p, dict)]
        parts.sort(key=lambda p: str(p.get("id")))
        messages.append((info, parts))
    messages.sort(
        key=lambda mp: (_num((mp[0].get("time") or {}).get("created")) or 0, mp[0].get("id"))
    )
    return session, None, messages, {"selected_by": "path", "orphan_parts": 0, "children": []}


def scan(path: pathlib.Path) -> Scan:
    """One read of the session. Never raises on content: malformed JSON, unknown part
    types and missing clocks are counted, not thrown."""
    path = pathlib.Path(path)
    diag: dict = {
        "container": None,
        "lines": 0,
        "records": 0,
        "malformed_lines": 0,
        "partial_trailing_line": False,
    }
    kind = detect(path)
    db_path = None
    if kind == "sqlite":
        db_path, sid = split_db_path(path)
        session, project, messages, extras = _read_sqlite(db_path, sid, diag)
    elif kind == "json_dir":
        session, project, messages, extras = _read_json_dir(path, diag)
    elif kind == "export_json":
        session, project, messages, extras = _read_export(path, diag)
    else:
        session, project, messages, extras = None, None, [], {"selected_by": "unrecognised"}
    diag["container"] = kind or "unknown"
    session = session or {}

    # the session row / info: SQLite columns and the JSON object use different key styles
    def _s(*keys, default=None):
        for k in keys:
            if k in session and session[k] is not None:
                return session[k]
        return default

    stime = session.get("time") if isinstance(session.get("time"), dict) else {}
    created = _ms(_s("time_created") if "time_created" in session else stime.get("created"))
    updated = _ms(_s("time_updated") if "time_updated" in session else stime.get("updated"))
    archived = _ms(_s("time_archived") if "time_archived" in session else stime.get("archived"))
    model_ref = _json(_s("model")) if _s("model") is not None else None
    tokens_row = None
    if "tokens_input" in session:
        tokens_row = {
            "input": _num(session.get("tokens_input")) or 0,
            "output": _num(session.get("tokens_output")) or 0,
            "reasoning": _num(session.get("tokens_reasoning")) or 0,
            "cache_read": _num(session.get("tokens_cache_read")) or 0,
            "cache_write": _num(session.get("tokens_cache_write")) or 0,
            "total": 0,
        }
    elif isinstance(session.get("tokens"), dict):
        tokens_row = _tokens_of(session["tokens"])
    revert = _json(_s("revert")) if _s("revert") is not None else None

    # message-level shape counts and the three token sums
    roles: Counter = Counter()
    part_types: Counter = Counter()
    unknown_parts: Counter = Counter()
    tools: Counter = Counter()
    unknown_tools: Counter = Counter()
    statuses: Counter = Counter()
    errors: Counter = Counter()
    models: Counter = Counter()
    providers: Counter = Counter()
    msg_sum, step_sum = _zero_tokens(), _zero_tokens()
    msg_cost = step_cost = 0.0
    assistants = with_tokens = summaries = steps = multi_step = 0
    no_ts = 0
    lo = hi = None
    for info, parts in messages:
        roles[str(info.get("role"))] += 1
        ts = (
            _ms((info.get("time") or {}).get("created"))
            if isinstance(info.get("time"), dict)
            else None
        )
        if ts is None:
            no_ts += 1
        else:
            lo = ts if lo is None or ts < lo else lo
            hi = ts if hi is None or ts > hi else hi
        n_steps = 0
        for p in parts:
            pt = str(p.get("type"))
            part_types[pt] += 1
            if pt not in KNOWN_PART_TYPES:
                unknown_parts[pt] += 1
            if pt == "tool":
                name = str(p.get("tool"))
                tools[name] += 1
                if name not in KNOWN_TOOLS:
                    unknown_tools[name] += 1
                st = p.get("state") if isinstance(p.get("state"), dict) else {}
                statuses[str(st.get("status"))] += 1
            elif pt == "step-finish":
                n_steps += 1
                _add(step_sum, _tokens_of(p.get("tokens")))
                step_cost += _num(p.get("cost")) or 0
        if info.get("role") == "assistant":
            assistants += 1
            if info.get("summary") is True:
                summaries += 1
            t = _tokens_of(info.get("tokens"))
            if t and any(t[k] for k in TOKEN_KEYS):
                with_tokens += 1
            _add(msg_sum, t)
            msg_cost += _num(info.get("cost")) or 0
            if isinstance(info.get("modelID"), str):
                models[info["modelID"]] += 1
            if isinstance(info.get("providerID"), str):
                providers[info["providerID"]] += 1
            if isinstance(info.get("error"), dict):
                errors[str(info["error"].get("name"))] += 1
        steps += n_steps
        multi_step += n_steps > 1

    parent = _s("parent_id", "parentID")
    meta = {
        "harness": HARNESS,
        "path": str(path),
        "container": kind,
        "db_path": str(db_path) if db_path else None,
        "session_id": _s("id"),
        "session_selected_by": extras.get("selected_by"),
        "project_id": _s("project_id", "projectID"),
        "parent_id": parent,
        "is_child": parent is not None,
        "child_sessions": [c.get("id") for c in extras.get("children", [])],
        "title": dg.mask(dg._trunc(str(_s("title")), 120)) if _s("title") else None,
        "slug": _s("slug"),
        "directory": _s("directory"),
        "cwd": _s("directory"),
        "worktree": (project or {}).get("worktree"),
        "vcs": (project or {}).get("vcs"),
        "agent": _s("agent"),
        "cli_version": _s("version"),
        "model": models.most_common(1)[0][0]
        if models
        else (model_ref or {}).get("id")
        if isinstance(model_ref, dict)
        else None,
        "provider": providers.most_common(1)[0][0]
        if providers
        else (model_ref or {}).get("providerID")
        if isinstance(model_ref, dict)
        else None,
        "models_seen": dict(models.most_common()) or None,
        "started_at": _iso(created),
        "updated_at": _iso(updated),
        "archived_at": _iso(archived),
        "revert_pending": bool(revert),
    }
    usage = {
        "assistant_messages": assistants,
        "assistant_messages_with_tokens": with_tokens,
        "summary_messages": summaries,
        "step_finish_parts": steps,
        "assistant_messages_multi_step": multi_step,
        # what the session_usage backfill sums: the LAST step of every message
        "sum_message_tokens": msg_sum,
        # one entry per API call: the additive record
        "sum_step_finish_tokens": step_sum,
        "session_row_tokens": tokens_row,
        "message_sum_equals_step_sum": {k: msg_sum[k] for k in TOKEN_KEYS if k != "total"}
        == {k: step_sum[k] for k in TOKEN_KEYS if k != "total"},
        "session_row_equals_message_sum": (
            tokens_row is not None
            and all(tokens_row[k] == msg_sum[k] for k in TOKEN_KEYS if k != "total")
        ),
        "sum_message_cost": round(msg_cost, 6),
        "sum_step_finish_cost": round(step_cost, 6),
        "session_row_cost": _num(_s("cost")),
        "session_message_projection_rows": extras.get("projection_rows"),
    }
    diag.update(
        {
            "records": len(messages) + sum(len(p) for _, p in messages),
            "lines": len(messages),
            "messages": len(messages),
            "parts": sum(len(p) for _, p in messages),
            "orphan_parts": extras.get("orphan_parts", 0),
            "legacy_v0_message": extras.get("legacy_v0_message", 0),
            "roles": dict(roles.most_common()),
            "part_types": dict(part_types.most_common()),
            "tool_names": dict(tools.most_common()),
            "tool_statuses": dict(statuses.most_common()),
            "assistant_error_names": dict(errors.most_common()),
            "unknown_error_names": {k: v for k, v in errors.items() if k not in KNOWN_ERROR_NAMES},
            "no_timestamp": no_ts,
            "bad_timestamp": 0,
            "first_ts": _iso(lo),
            "last_ts": _iso(hi),
            "types": {f"{r}": n for r, n in roles.most_common()},
            "unknown_types": dict(unknown_parts.most_common()),
            "unknown_tools": dict(unknown_tools.most_common()),
        }
    )
    return Scan(path, kind or "unknown", session, project, messages, meta, usage, diag)


def meta(path: pathlib.Path) -> dict:
    """session id / project / parent / model / version / directory for one session."""
    return scan(path).meta


def usage(path: pathlib.Path) -> dict:
    """Token figures THREE ways: the sum of `message.tokens` (each message's LAST step),
    the sum of `step-finish` parts (one per API call) and the session row. When they
    disagree the disagreement is the finding — see the docstring."""
    return scan(path).usage


def diagnostics(path: pathlib.Path) -> dict:
    """Container, tables, part types, tool names/statuses, error names, plus the
    event-derivation counters (`derivation`) from a full load."""
    s = scan(path)
    d = dict(s.diagnostics)
    d["derivation"] = _derive(s)[1]
    return d


# ----------------------------------------------------------------------------- helpers


def _line_count(content: str) -> int:
    return content.count("\n") + (1 if content and not content.endswith("\n") else 0)


def _line_delta(old: str, new: str) -> tuple[int, int]:
    a, b = old.splitlines(), new.splitlines()
    added = removed = 0
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        if tag in ("replace", "insert"):
            added += j2 - j1
        if tag in ("replace", "delete"):
            removed += i2 - i1
    return added, removed


def _patch_text_effect(patch: str) -> tuple[str | None, int, int]:
    m = _PATCH_PATH.search(patch)
    path = m.group("path").strip() if m else None
    added = removed = 0
    for ln in patch.splitlines():
        if ln.startswith(("***", "@@")):
            continue
        if ln.startswith("+"):
            added += 1
        elif ln.startswith("-"):
            removed += 1
    return path, added, removed


def _tool_event(ts: float, part: dict, model: str | None, counters: Counter) -> dg.Ev:
    name = part.get("tool") if isinstance(part.get("tool"), str) else "tool"
    st = part.get("state") if isinstance(part.get("state"), dict) else {}
    inp = st.get("input") if isinstance(st.get("input"), dict) else {}
    md = st.get("metadata") if isinstance(st.get("metadata"), dict) else {}
    # a call in state `error` changed nothing: it keeps its path (the file it aimed at)
    # and gets no line credit, or a failed edit scores the lines it failed to write
    applied = st.get("status") != "error"
    ev = dg.Ev(0, ts, "tool", "", tool=name, tool_id=part.get("callID"), model=model)
    if name == SHELL_TOOL:
        cmd = str(inp.get("command", ""))
        path, approx = dg._bash_file_effect(cmd)
        ev.path = path
        if approx is not None and applied:
            ev.added, ev.removed = approx, 0
        ev.text = dg.mask(dg._trunc(cmd.replace("\n", " ⏎ "), dg.COMMAND_MAX))
    elif name in (EDIT_TOOL, WRITE_TOOL, READ_TOOL):
        p = inp.get("filePath")
        ev.path = p if isinstance(p, str) and p else None
        ev.text = dg.mask(ev.path or "")
        if not applied:
            counters["edit_or_write_failed_no_credit"] += 1
        elif name == WRITE_TOOL and isinstance(inp.get("content"), str):
            ev.added, ev.removed = _line_count(inp["content"]), 0
            if md.get("exists") is True:
                counters["write_overwrote_existing_file"] += 1
        elif name == EDIT_TOOL:
            fd = md.get("filediff") if isinstance(md.get("filediff"), dict) else None
            if fd and _num(fd.get("additions")) is not None:
                ev.added, ev.removed = int(fd["additions"]), int(fd.get("deletions") or 0)
                counters["edit_credit_from_filediff"] += 1
            elif isinstance(inp.get("oldString"), str) and isinstance(inp.get("newString"), str):
                ev.added, ev.removed = _line_delta(inp["oldString"], inp["newString"])
                counters["edit_credit_from_strings"] += 1
    elif name == PATCH_TOOL:
        files = md.get("files") if isinstance(md.get("files"), list) else []
        files = [f for f in files if isinstance(f, dict)]
        if not applied:
            counters["edit_or_write_failed_no_credit"] += 1
            ev.path, _, _ = _patch_text_effect(str(inp.get("patchText") or ""))
        elif files and all(_num(f.get("additions")) is not None for f in files):
            counters["patch_credit_from_metadata"] += 1
            ev.path = next((f.get("filePath") for f in files if f.get("filePath")), None)
            ev.added = sum(int(f["additions"]) for f in files)
            ev.removed = sum(int(f.get("deletions") or 0) for f in files)
        elif isinstance(inp.get("patchText"), str):
            counters["patch_credit_from_text"] += 1
            ev.path, ev.added, ev.removed = _patch_text_effect(inp["patchText"])
        ev.text = dg.mask(ev.path or "")
    elif name in ("glob", "grep"):
        ev.text = dg.mask(dg._trunc(str(inp.get("pattern", "")), 80))
    elif name in ("webfetch", "websearch"):
        ev.text = dg.mask(dg._trunc(str(inp.get("url") or inp.get("query") or ""), 100))
    elif name == TASK_TOOL:
        ev.text = dg.mask(dg._trunc(str(inp.get("description") or inp.get("prompt") or ""), 100))
        if isinstance(md.get("sessionId"), str):
            counters["task_child_sessions"] += 1
    else:
        ev.text = (
            dg.mask(dg._trunc(json.dumps(inp, separators=(",", ":"))[:200], 100)) if inp else ""
        )
    return ev


def _shell_exit(st: dict) -> int | None:
    md = st.get("metadata") if isinstance(st.get("metadata"), dict) else {}
    code = _num(md.get("exit"))
    return int(code) if code is not None else None


# ----------------------------------------------------------------------------- derive


def _derive(s: Scan, start: float | None = None, end: float | None = None):
    """Turn (message, parts) pairs into `Ev`s. Returns (events, derivation counters).

    Events are ordered by (timestamp, emission index). A message without a clock is
    placed at the session's start and counted, never interpolated."""
    counters: Counter = Counter()
    out: list[dg.Ev] = []
    order: list[tuple[float, int]] = []
    session_start = dg._ts(s.meta.get("started_at")) if s.meta.get("started_at") else None
    is_child = bool(s.meta.get("is_child"))
    revert = _json(s.session.get("revert")) if s.session.get("revert") is not None else None
    revert_msg = revert.get("messageID") if isinstance(revert, dict) else None
    revert_part = revert.get("partID") if isinstance(revert, dict) else None
    reverting = False

    def _emit(ev: dg.Ev) -> None:
        order.append((ev.ts, len(out)))
        out.append(ev)

    def _in_window(ts: float) -> bool:
        return (start is None or ts >= start) and (end is None or ts <= end)

    user_texts: dict[str, list[str]] = {}  # message id -> human text parts (for /shell detection)

    for info, parts in s.messages:
        mid = info.get("id")
        if mid == revert_msg and not revert_part:
            reverting = True
        if reverting:
            counters["reverted_pending_cleanup"] += 1
            continue
        if mid == revert_msg and revert_part:
            idx = next((i for i, p in enumerate(parts) if p.get("id") == revert_part), None)
            if idx is not None:
                counters["reverted_pending_cleanup_parts"] += len(parts) - idx
                parts = parts[:idx]
            reverting = True  # every later message is reverted too
        time = info.get("time") if isinstance(info.get("time"), dict) else {}
        ts = _ms(time.get("created"))
        if ts is None:
            ts = _ms(info.get("_row_time_created")) or session_start
            counters["message_no_timestamp"] += 1
            if ts is None:
                counters["message_dropped_no_timestamp"] += 1
                continue
        role = info.get("role")

        if role == "user":
            human, synthetic, ignored = [], 0, 0
            subtask = None
            for p in parts:
                pt = p.get("type")
                if pt == "text":
                    if p.get("synthetic") is True:
                        synthetic += 1
                        if str(p.get("text", "")).startswith(USER_SHELL_TEXT):
                            counters["user_shell_command"] += 1
                            user_texts[str(mid)] = ["__shell__"]
                    elif p.get("ignored") is True:
                        ignored += 1
                    elif isinstance(p.get("text"), str) and p["text"].strip():
                        human.append(p["text"])
                elif pt == "compaction":
                    counters[f"compaction_{'auto' if p.get('auto') else 'manual'}"] += 1
                    if _in_window(ts):
                        _emit(dg.Ev(0, ts, "compaction", ""))
                elif pt == "subtask":
                    subtask = p
                    counters["subtask_part"] += 1
                elif pt == "file":
                    counters["user_file_part"] += 1
                elif pt == "agent":
                    counters["user_agent_mention"] += 1
                else:
                    counters[f"user_part_{pt}"] += 1
            if synthetic:
                counters["user_synthetic_text_parts"] += synthetic
            if ignored:
                counters["user_ignored_text_parts"] += ignored
            text = "\n".join(human).strip()
            source = "text"
            if not text and subtask is not None:
                text = str(subtask.get("description") or subtask.get("prompt") or "").strip()
                source = "subtask"
            if not text:
                if human or subtask is None:
                    counters["user_message_without_prompt"] += 1
                continue
            if is_child:
                counters["prompt_in_child_session"] += 1  # written by the parent agent
                continue
            if mid is not None:
                user_texts[str(mid)] = human
            if _in_window(ts):
                counters[f"prompt_from_{source}"] += 1
                _emit(dg.Ev(0, ts, "prompt", dg.mask(dg._trunc(text, dg.PROMPT_MAX))))

        elif role == "assistant":
            model = info.get("modelID") if isinstance(info.get("modelID"), str) else None
            tokens = _tokens_of(info.get("tokens")) or _zero_tokens()
            summary = info.get("summary") is True
            by_user = user_texts.get(str(info.get("parentID"))) == ["__shell__"]
            if summary:
                counters["summary_message"] += 1
            first_text = True
            n_steps = 0
            for p in parts:
                pt = p.get("type")
                ptime = p.get("time") if isinstance(p.get("time"), dict) else {}
                if pt == "text":
                    if not isinstance(p.get("text"), str) or not p["text"].strip():
                        continue
                    if summary:
                        counters["summary_text_skipped"] += 1
                        continue
                    tts = _ms(ptime.get("start")) or ts
                    if _in_window(tts):
                        counters["assistant"] += 1
                        _emit(
                            dg.Ev(
                                0,
                                tts,
                                "assistant",
                                dg.mask(dg._trunc(p["text"], dg.ASSISTANT_MAX)),
                                model=model,
                                tok_out=tokens["output"] if first_text else None,
                            )
                        )
                    first_text = False
                elif pt == "tool":
                    st = p.get("state") if isinstance(p.get("state"), dict) else {}
                    status = st.get("status")
                    sttime = st.get("time") if isinstance(st.get("time"), dict) else {}
                    tts = _ms(sttime.get("start")) or ts
                    if status not in KNOWN_TOOL_STATUSES:
                        counters[f"tool_status_unknown_{status}"] += 1
                    elif status in ("pending", "running"):
                        counters[f"tool_status_{status}"] += 1
                    if by_user:
                        counters["tool_run_by_user"] += 1
                    if not _in_window(tts):
                        continue
                    ev = _tool_event(tts, p, model, counters)
                    counters["tool"] += 1
                    _emit(ev)
                    md = st.get("metadata") if isinstance(st.get("metadata"), dict) else {}
                    if status == "error":
                        if md.get("interrupted") is True or st.get("error") == TOOL_ABORTED_TEXT:
                            counters["tool_aborted_interrupted"] += 1
                            continue
                        counters["tool_error"] += 1
                        _emit(
                            dg.Ev(
                                0,
                                _ms(sttime.get("end")) or tts,
                                "result_error",
                                dg.mask(dg._trunc(str(st.get("error") or "(error)"), dg.ERROR_MAX)),
                                tool=ev.tool,
                                path=ev.path,
                                ok=False,
                                tool_id=ev.tool_id,
                            )
                        )
                    elif status == "completed" and ev.tool == SHELL_TOOL:
                        code = _shell_exit(st)
                        if code is not None and code != 0:
                            counters["shell_nonzero_exit"] += 1
                            output = str(st.get("output") or md.get("output") or "")
                            _emit(
                                dg.Ev(
                                    0,
                                    _ms(sttime.get("end")) or tts,
                                    "result_error",
                                    dg.mask(dg._trunc(output or f"exit {code}", dg.ERROR_MAX)),
                                    tool=ev.tool,
                                    path=ev.path,
                                    ok=False,
                                    tool_id=ev.tool_id,
                                )
                            )
                elif pt == "step-finish":
                    n_steps += 1
                elif pt == "retry":
                    counters["retry_part"] += 1
                    err = p.get("error") if isinstance(p.get("error"), dict) else {}
                    data = err.get("data") if isinstance(err.get("data"), dict) else {}
                    rts = _ms(ptime.get("created")) or ts
                    if _in_window(rts):
                        _emit(
                            dg.Ev(
                                0,
                                rts,
                                "result_error",
                                dg.mask(
                                    dg._trunc(
                                        str(data.get("message") or err.get("name") or "(retry)"),
                                        dg.ERROR_MAX,
                                    )
                                ),
                                tool="api_request",
                                ok=False,
                            )
                        )
                elif pt in ("step-start", "snapshot", "patch", "reasoning", "file"):
                    counters[f"part_{pt}"] += 1
                else:
                    counters[f"assistant_part_{pt}"] += 1
            if n_steps == 0 and not by_user:
                counters["assistant_without_step_finish"] += 1
            err = info.get("error") if isinstance(info.get("error"), dict) else None
            if err:
                name = str(err.get("name"))
                data = err.get("data") if isinstance(err.get("data"), dict) else {}
                ets = _ms(time.get("completed")) or ts
                if name == ABORTED_ERROR:
                    counters["interrupt_aborted"] += 1
                    if _in_window(ets):
                        _emit(dg.Ev(0, ets, "interrupt", ""))
                else:
                    counters[f"assistant_error_{name}"] += 1
                    if _in_window(ets):
                        _emit(
                            dg.Ev(
                                0,
                                ets,
                                "result_error",
                                dg.mask(dg._trunc(str(data.get("message") or name), dg.ERROR_MAX)),
                                tool="api_request",
                                ok=False,
                            )
                        )
        else:
            counters[f"message_role_unknown_{role}"] += 1

    out = [out[i] for _, i in sorted(order)]
    for i, e in enumerate(out):
        e.n = i
    return out, dict(sorted(counters.items()))


def load_events(
    path: pathlib.Path, start: float | None = None, end: float | None = None
) -> list[dg.Ev]:
    """Read one session (`<db>/<session id>`, a bare database, a `storage/session/…json`
    file or an `opencode export` file) into digest events, in time order, within
    [start, end]."""
    return _derive(scan(path), start, end)[0]
