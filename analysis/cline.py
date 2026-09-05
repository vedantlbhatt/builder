"""Read one Cline (VS Code extension) task into the same `Ev` list the Claude Code loader
produces, so `digest.stats` / `digest.render` / `run.analyze` work unchanged.

A Cline session is a task DIRECTORY, not a file:

    <dataDir>/tasks/<taskId>/api_conversation_history.json   Anthropic MessageParam[]
    <dataDir>/tasks/<taskId>/ui_messages.json                ClineMessage[] — the timeline
    <dataDir>/tasks/<taskId>/task_metadata.json              TaskMetadata
    <dataDir>/state/taskHistory.json                         HistoryItem[] — the index

`<dataDir>` is `CLINE_DATA_DIR`, else `$CLINE_DIR/data`, else `~/.cline/data`
(apps/vscode/src/sdk/legacy-state-reader.ts `resolveDataDir`); the VS Code build also
uses the host's globalStorage — `~/Library/Application Support/Code/User/globalStorage/
saoudrizwan.claude-dev/tasks/<taskId>/` (apps/vscode/src/core/storage/disk.ts
`getGlobalStorageDir("tasks", taskId)`, `GlobalFileNames`). `load_events` accepts the
directory or either JSON file inside it and reads every file present.

Every shape below is marked VERIFIED (read from cline/cline `main` at commit dac3b35,
2026-09-04, fetched 2026-09-05 via raw.githubusercontent.com/cline/cline/main/<path>) or
ASSUMED. The house rule applies: a parser written from a description ships with a
diagnostics-first probe (`python -m analysis probe ~/.cline/data/tasks`), and the first
real corpus decides what this file got wrong before any number reaches a card.

VERIFIED shapes and where they come from
----------------------------------------
* `ClineMessage {ts: number, type: "ask"|"say", ask?: ClineAsk, say?: ClineSay, text?,
  reasoning?, images?, files?, partial?, seq?, epoch?, commandCompleted?,
  lastCheckpointHash?, conversationHistoryIndex?, conversationHistoryDeletedRange?,
  modelInfo?}` — apps/vscode/src/shared/ExtensionMessage.ts. `ClineSay` includes `task`,
  `error`, `api_req_started`, `api_req_finished`, `text`, `reasoning`, `completion_result`,
  `plan_completion_result`, `user_feedback`, `user_feedback_diff`, `command`,
  `command_output`, `tool`, `deleted_api_reqs`, `diff_error`, `checkpoint_created`,
  `compaction`, `subagent_usage`, …; `ClineAsk` includes `followup`, `plan_mode_respond`,
  `command`, `command_output`, `completion_result`, `tool`, `api_req_failed`,
  `resume_task`, `resume_completed_task`, `mistake_limit_reached`, `use_mcp_server`,
  `condense`, …. `ui_messages.json` is that array, whole-file JSON
  (legacy-state-reader.ts `readUiMessages`; disk.ts `GlobalFileNames.uiMessages`).
* The human's first message is `say: "task"`; every later human message is
  `say: "user_feedback"` (apps/vscode/src/sdk/message-translator.ts:2481 —
  `say: clineMessages.length === 0 ? "task" : "user_feedback"`; SdkController.ts:1655;
  sdk-checkpoints.ts:4 treats exactly those two as user messages).
* `ClineSayTool {tool: "editedExistingFile"|"newFileCreated"|"fileDeleted"|"readFile"|
  "listFilesTopLevel"|"listFilesRecursive"|"listCodeDefinitionNames"|"searchFiles"|
  "webFetch"|"webSearch"|"summarizeTask"|"useSkill", path?, diff?, content?, regex?,
  filePattern?, …}` is the JSON in `text` of a `say`/`ask: "tool"` — ExtensionMessage.ts.
  `write_to_file {path, content}` → `newFileCreated {path, content}`; `replace_in_file` /
  `editor {old_text, new_text}` / `apply_patch` → `editedExistingFile {path, content:
  "------- SEARCH\n…\n=======\n…\n+++++++ REPLACE", diff}` (message-translator.ts:642-735).
* `say: "command"` text is `${command}\nOutput:` followed by the output; the marker is
  `COMMAND_OUTPUT_STRING = "Output:"` (apps/vscode/src/shared/combineCommandSequences.ts:73;
  message-translator.ts:1364, `commandCompleted: true` at :1652).
* `say: "error"` text is the error message (message-translator.ts:1761).
* `ClineApiReqInfo {request?, tokensIn?, tokensOut?, cacheWrites?, cacheReads?, cost?,
  cancelReason?: "streaming_failed"|"user_cancelled"|"retries_exhausted",
  streamingFailedMessage?}` is the JSON in `text` of `say: "api_req_started"`
  (ExtensionMessage.ts). Totals are the SUM over `api_req_started` rows — plus
  `deleted_api_reqs` and `subagent_usage` rows — exactly `getApiMetrics`
  (apps/vscode/src/shared/getApiMetrics.ts); an `api_req_finished` row, when present, is
  merged INTO its `api_req_started` at render time (combineApiRequests.ts), so `usage()`
  reports the per-row sum and the finished rows separately, never both added together.
* When a task is cleared mid-request, the LAST `api_req_started` without `cost` gets
  `cancelReason: "user_cancelled"` (apps/vscode/src/sdk/sdk-message-coordinator.ts
  `finalizeMessagesForSave`); resuming writes `ask: "resume_task"` /
  `"resume_completed_task"` (sdk-task-control-coordinator.ts:100,273,297).
* THE `ts` IS NOT ALWAYS A CLOCK. Legacy tasks stamp `Date.now()` and the task directory
  is `${Date.now()}` (apps/vscode/src/dev/commands/tasks.ts:83-86; HistoryItem `ts`). On
  the SDK path `ts` is minted by `MessageIdMinter.nextId()` — "pure monotonic counter;
  never reads the clock", seeded at 0 (apps/vscode/src/sdk/message-id-minter.ts;
  message-translator.ts:196), and the task id is the SDK session id
  (sdk-task-start-coordinator.ts:139). A `ts` outside the epoch-millisecond range is
  therefore an ORDINAL: such rows keep their order, are placed at the task's start time,
  and are counted in `diagnostics["ts_counter_not_clock"]` — never scaled into seconds.
* `api_conversation_history.json` is `Anthropic.MessageParam[]` (disk.ts
  `getSavedApiConversationHistory`; legacy-state-reader.ts `readApiConversationHistory`):
  `{role: "user"|"assistant", content: string | [{type: "text"|"tool_use"|"tool_result"|
  "image"|"thinking", …}]}`; `tool_use {id, name, input}`, `tool_result {tool_use_id,
  content, is_error?}` (apps/vscode/src/sdk/legacy-task-handling.ts:17-34). It carries NO
  timestamps and NO usage.
* Legacy tool names — `ClineDefaultTool` (apps/vscode/src/shared/tools.ts): `execute_command`,
  `replace_in_file`, `read_file`, `write_to_file`, `search_files`, `list_files`,
  `list_code_definition_names`, `browser_action`, `use_mcp_tool`, `access_mcp_resource`,
  `ask_followup_question`, `attempt_completion`, `new_task`, `plan_mode_respond`,
  `web_fetch`, `web_search`, `apply_patch`, `use_skill`, `use_subagents`, …. SDK-era
  names: `run_commands`, `editor`, `apply_patch` (sdk/packages/core/src/extensions/tools/
  constants.ts; runtime-builder.ts:103 `bash: "run_commands"`).
* Envelopes inside user text: the first message is `<task>\n…\n</task>`
  (dev/commands/tasks.ts:101,185); guidance is `<feedback>\n…\n</feedback>`
  (core/prompts/responses.ts:48; sdk/tool-approval-denial.ts:24); a resume is
  `[TASK RESUMPTION] …` with the human's new text in `<user_message>` (responses.ts:241-253);
  a failed tool's result text is `The tool execution failed with the following error:\n
  <error>\n…\n</error>` (responses.ts:27).
* `TaskMetadata {files_in_context: [{path, record_state, record_source: "read_tool"|
  "user_edited"|"cline_edited"|"file_mentioned", cline_read_date, cline_edit_date,
  user_edit_date?}], model_usage: [{ts, model_id, model_provider_id, mode}],
  environment_history: [{ts, os_name, …, cline_version}]}` — apps/vscode/src/core/context/
  context-tracking/ContextTrackerTypes.ts. `user_edited` entries with a `user_edit_date` are
  the human-edit presence signal.
* `HistoryItem {id, ulid?, ts, task, tokensIn, tokensOut, cacheWrites?, cacheReads?,
  totalCost, size?, cwdOnTaskInitialization?, modelId?, apiProvider?, isLegacy?}` —
  apps/vscode/src/shared/HistoryItem.ts; the index lives at `<dataDir>/state/
  taskHistory.json` (legacy-state-reader.ts `taskHistoryPath`), i.e. two levels up from
  the task directory. Its totals are reported NEXT TO the ui sum, never chosen.

ASSUMED (not in the current source; handled leniently and counted in diagnostics)
-------------------------------------------------------------------------------
* `<environment_details>…</environment_details>` appended to user messages: ZERO hits in
  `main` at the fetch date; older releases wrote it. It is stripped wherever it appears and
  counted (`envelope_environment_details`), and a message that is nothing but such an
  envelope is not a prompt.
* Legacy (pre-SDK) writers recorded a tool pending approval as `ask: "tool"` /
  `ask: "command"` with the same JSON payload, and an auto-approved one as `say`. Both are
  read as tool events; an identical `say` directly after its `ask` is deduplicated and
  counted (`tool_ask_then_say_deduped`).
* `cancelReason: "user_cancelled"` is emitted as an `interrupt` — it is written when the
  person clears or cancels the task; `streaming_failed` / `retries_exhausted` are counted.
* `say: "error"` (API / runtime errors) is emitted as a `result_error` from `api_request`;
  `ask: "api_req_failed"` the same. `diff_error` is a `result_error` from `replace_in_file`.
* Line credit: `newFileCreated` scores the line count of `content`; `editedExistingFile`
  scores a real line diff of each SEARCH → REPLACE block (difflib), falling back to
  `+`/`-` line counts when `diff` is a unified patch. Neither has been compared against a
  corpus.
* `modelInfo.modelId` on a `ClineMessage` is read when it is a string; the field's shape
  (`ClineMessageModelInfo`, shared/messages) was not fetched.
* `api_conversation_history.json` tool_use blocks are paired with ui tool events BY ORDER
  WITHIN TOOL CLASS (shell / write / edit / read / …), which is how the two files are
  produced; unmatched blocks on either side are counted, not guessed at.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import difflib
import json
import pathlib
import re
from collections import Counter, deque

from . import digest as dg

HARNESS = "cline"

UI_FILE = "ui_messages.json"
API_FILE = "api_conversation_history.json"
META_FILE = "task_metadata.json"
INDEX_REL = ("..", "..", "state", "taskHistory.json")  # VERIFIED layout, see docstring

# VERIFIED: ClineDefaultTool (shared/tools.ts) + SDK-era names (extensions/tools/constants.ts).
SHELL_TOOL = "execute_command"
WRITE_FILE_TOOL = "write_to_file"
EDIT_TOOL = "replace_in_file"
READ_FILE_TOOL = "read_file"
SDK_SHELL_TOOLS = frozenset({"run_commands"})
SDK_EDIT_TOOLS = frozenset({"editor", "apply_patch"})

# VERIFIED: ClineSayTool["tool"] → the legacy tool name it renders (message-translator.ts).
TOOL_BY_UI_KIND = {
    "newFileCreated": WRITE_FILE_TOOL,
    "editedExistingFile": EDIT_TOOL,
    "fileDeleted": "delete_file",
    "readFile": READ_FILE_TOOL,
    "listFilesTopLevel": "list_files",
    "listFilesRecursive": "list_files",
    "listCodeDefinitionNames": "list_code_definition_names",
    "searchFiles": "search_files",
    "webFetch": "web_fetch",
    "webSearch": "web_search",
    "summarizeTask": "summarize_task",
    "useSkill": "use_skill",
}
# tool class used to pair api tool_use blocks with ui tool events, by name
_CLASS_BY_TOOL = {
    SHELL_TOOL: "shell",
    "run_commands": "shell",
    WRITE_FILE_TOOL: "write",
    EDIT_TOOL: "edit",
    "editor": "edit",
    "apply_patch": "edit",
    READ_FILE_TOOL: "read",
    "search_files": "search",
    "list_files": "list",
    "list_code_definition_names": "defs",
    "web_fetch": "web",
    "web_search": "web",
    "use_skill": "skill",
    "delete_file": "delete",
    # known tools that never produce a ui `tool`/`command` row; classified so they are not
    # reported as unknown, and never paired
    "attempt_completion": "completion",
    "ask_followup_question": "followup",
    "plan_mode_respond": "plan",
    "act_mode_respond": "plan",
    "new_task": "new_task",
    "condense": "condense",
    "summarize_task": "condense",
    "use_mcp_tool": "mcp",
    "access_mcp_resource": "mcp",
    "load_mcp_documentation": "mcp",
    "browser_action": "browser",
    "focus_chain": "todo",
    "use_subagents": "subagents",
    "report_bug": "bug",
    "new_rule": "rule",
}

# VERIFIED: ExtensionMessage.ts ClineSay / ClineAsk.
KNOWN_SAY = frozenset(
    {
        "task",
        "error",
        "api_req_started",
        "api_req_finished",
        "text",
        "reasoning",
        "completion_result",
        "plan_completion_result",
        "user_feedback",
        "user_feedback_diff",
        "command",
        "command_output",
        "tool",
        "shell_integration_warning",
        "shell_integration_warning_with_suggestion",
        "browser_action_launch",
        "browser_action",
        "browser_action_result",
        "mcp_server_request_started",
        "mcp_server_response",
        "mcp_notification",
        "use_mcp_server",
        "diff_error",
        "deleted_api_reqs",
        "clineignore_error",
        "command_permission_denied",
        "checkpoint_created",
        "load_mcp_documentation",
        "info",
        "task_progress",
        "hook_status",
        "hook_output_stream",
        "subagent",
        "use_subagents",
        "subagent_usage",
        "conditional_rules_applied",
        "compaction",
        # dropped on read by legacy-state-reader.ts REMOVED_LEGACY_SAY_TYPES
        "error_retry",
        "api_req_retried",
    }
)
KNOWN_ASK = frozenset(
    {
        "followup",
        "plan_mode_respond",
        "act_mode_respond",
        "command",
        "command_output",
        "completion_result",
        "tool",
        "api_req_failed",
        "resume_task",
        "resume_completed_task",
        "mistake_limit_reached",
        "browser_action_launch",
        "use_mcp_server",
        "new_task",
        "condense",
        "summarize_task",
        "report_bug",
        "use_subagents",
    }
)
KNOWN_BLOCK_TYPES = frozenset({"text", "tool_use", "tool_result", "image", "thinking", "document"})

COMMAND_OUTPUT_MARKER = "\nOutput:"  # VERIFIED: combineCommandSequences.ts:73
TOOL_ERROR_PREFIX = "The tool execution failed with the following error:"  # responses.ts:27
TASK_RESUMPTION_PREFIX = "[TASK RESUMPTION]"  # responses.ts:241
USAGE_KEYS = ("tokensIn", "tokensOut", "cacheWrites", "cacheReads", "cost")

_EPOCH_MS_MIN = 1_000_000_000_000  # 2001-09-09
_EPOCH_MS_MAX = 10_000_000_000_000  # 2286-11-20
_ENV_DETAILS = re.compile(r"<environment_details>.*?</environment_details>", re.DOTALL)
_WRAPPED = re.compile(r"^\s*<(task|feedback|user_message|answer)>\s*(.*?)\s*</\1>\s*$", re.DOTALL)
_USER_MESSAGE = re.compile(r"<user_message>\s*(.*?)\s*</user_message>", re.DOTALL)
_SEARCH_REPLACE = re.compile(
    r"^-{3,} SEARCH\n(.*?)\n?^={3,}\n(.*?)\n?^\+{3,} REPLACE", re.DOTALL | re.MULTILINE
)


# ----------------------------------------------------------------------------- scan


@dataclasses.dataclass
class Scan:
    """Everything one pass over a task directory yields. `load_events`, `meta`, `usage` and
    `diagnostics` are views on this; the probe prints all of it."""

    path: pathlib.Path
    task_dir: pathlib.Path
    ui: list[dict]
    api: list[dict]
    task_meta: dict
    index_item: dict | None
    base_ts: float  # seconds; the task's start on the wall clock (see `_base_ts`)
    meta: dict
    usage: dict
    diagnostics: dict


def resolve(path: pathlib.Path) -> tuple[pathlib.Path, dict[str, pathlib.Path | None]]:
    """(task directory, {ui, api, meta, index} → existing path or None) for a directory or
    either JSON file inside it."""
    path = pathlib.Path(path)
    task_dir = path if path.is_dir() else path.parent
    files: dict[str, pathlib.Path | None] = {}
    for key, name in (("ui", UI_FILE), ("api", API_FILE), ("meta", META_FILE)):
        p = task_dir / name
        files[key] = p if p.is_file() else None
    idx = task_dir.joinpath(*INDEX_REL)
    files["index"] = idx if idx.is_file() else None
    return task_dir, files


def _read_json(p: pathlib.Path | None, diag: dict, key: str):
    if p is None:
        return None
    try:
        return json.loads(p.read_bytes())
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        diag["malformed_lines"] += 1
        diag["malformed_files"].append(p.name)
        return None


def _ms(v) -> float | None:
    """Epoch milliseconds → seconds; anything outside the epoch-ms range is NOT a clock."""
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


def _json_text(text) -> dict:
    if not isinstance(text, str) or not text:
        return {}
    try:
        v = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return v if isinstance(v, dict) else {}


def _base_ts(task_dir: pathlib.Path, ui: list[dict], index_item: dict | None, tm: dict):
    """(seconds, source) — the wall-clock start of the task, from the most trustworthy
    place that has one: the legacy `${Date.now()}` directory name, the index's `ts`, the
    first clock-shaped ui `ts`, task_metadata's first `model_usage.ts`. Else 0."""
    if task_dir.name.isdigit():
        b = _ms(int(task_dir.name))
        if b is not None:
            return b, "task_dir_name"
    if index_item and _ms(index_item.get("ts")) is not None:
        return _ms(index_item["ts"]), "task_history_index"
    for m in ui:
        if isinstance(m, dict) and _ms(m.get("ts")) is not None:
            return _ms(m["ts"]), "first_ui_ts"
    for m in tm.get("model_usage") or []:
        if isinstance(m, dict) and _ms(m.get("ts")) is not None:
            return _ms(m["ts"]), "task_metadata_model_usage"
    return 0.0, "none"


def scan(path: pathlib.Path) -> Scan:
    """One read of the task's files. Never raises on content: an unparseable or absent
    file, a row without a clock, an unknown say/ask kind are counted, not thrown."""
    task_dir, files = resolve(path)
    diag: dict = {
        "container": "task_dir",
        "files_present": sorted(k for k, v in files.items() if v is not None),
        "lines": 0,
        "records": 0,
        "malformed_lines": 0,
        "malformed_files": [],
        "partial_trailing_line": False,
    }
    ui_raw = _read_json(files["ui"], diag, "ui")
    api_raw = _read_json(files["api"], diag, "api")
    tm_raw = _read_json(files["meta"], diag, "meta")
    idx_raw = _read_json(files["index"], diag, "index")

    ui = [m for m in ui_raw if isinstance(m, dict)] if isinstance(ui_raw, list) else []
    api = [m for m in api_raw if isinstance(m, dict)] if isinstance(api_raw, list) else []
    tm = tm_raw if isinstance(tm_raw, dict) else {}
    index_item = None
    if isinstance(idx_raw, list):
        for it in idx_raw:
            if isinstance(it, dict) and str(it.get("id")) == task_dir.name:
                index_item = it
                break
        diag["index_items"] = len(idx_raw)
        diag["index_has_this_task"] = index_item is not None
    if isinstance(ui_raw, list):
        diag["ui_rows_not_objects"] = len(ui_raw) - len(ui)
    if isinstance(api_raw, list):
        diag["api_rows_not_objects"] = len(api_raw) - len(api)

    base, base_src = _base_ts(task_dir, ui, index_item, tm)

    # timeline shape
    types: Counter = Counter()
    unknown: Counter = Counter()
    clock = counter = no_ts = partial = 0
    lo = hi = None
    for m in ui:
        t = m.get("type")
        kind = m.get("say") if t == "say" else m.get("ask") if t == "ask" else None
        label = f"{t}:{kind}"
        types[label] += 1
        known = KNOWN_SAY if t == "say" else KNOWN_ASK if t == "ask" else frozenset()
        if kind not in known:
            unknown[label] += 1
        if m.get("partial"):
            partial += 1
        ts = _ms(m.get("ts"))
        if ts is not None:
            clock += 1
            lo = ts if lo is None or ts < lo else lo
            hi = ts if hi is None or ts > hi else hi
        elif _num(m.get("ts")) is not None:
            counter += 1
        else:
            no_ts += 1

    # api history shape
    roles: Counter = Counter()
    blocks: Counter = Counter()
    unknown_blocks: Counter = Counter()
    string_content = 0
    for m in api:
        roles[str(m.get("role"))] += 1
        c = m.get("content")
        if isinstance(c, str):
            string_content += 1
        elif isinstance(c, list):
            for b in c:
                bt = b.get("type") if isinstance(b, dict) else None
                blocks[str(bt)] += 1
                if bt not in KNOWN_BLOCK_TYPES:
                    unknown_blocks[str(bt)] += 1

    # usage: sum over api_req_started rows (getApiMetrics), finished rows apart, index apart
    started = {k: 0 for k in USAGE_KEYS}
    started_n = started_with_tokens = 0
    finished = {k: 0 for k in USAGE_KEYS}
    finished_n = 0
    extra = {k: 0 for k in USAGE_KEYS}  # deleted_api_reqs + subagent_usage
    extra_n = 0
    cancel: Counter = Counter()
    models: Counter = Counter()
    for m in ui:
        if m.get("type") != "say":
            continue
        say = m.get("say")
        if say not in ("api_req_started", "api_req_finished", "deleted_api_reqs", "subagent_usage"):
            continue
        info = _json_text(m.get("text"))
        acc = (
            started
            if say == "api_req_started"
            else finished
            if say == "api_req_finished"
            else extra
        )
        had = False
        for k in USAGE_KEYS:
            v = _num(info.get(k))
            if v is not None:
                acc[k] += v
                had = had or k != "cost"
        if say == "api_req_started":
            started_n += 1
            started_with_tokens += had
            if info.get("cancelReason") is not None:
                cancel[str(info["cancelReason"])] += 1
        elif say == "api_req_finished":
            finished_n += 1
        else:
            extra_n += 1
        mi = m.get("modelInfo")
        if isinstance(mi, dict) and isinstance(mi.get("modelId"), str):
            models[mi["modelId"]] += 1
    for k in USAGE_KEYS:
        started[k] = round(started[k], 6) if isinstance(started[k], float) else started[k]
    metrics = {k: started[k] + extra[k] for k in USAGE_KEYS}
    metrics["cost"] = round(metrics["cost"], 6)
    index_totals = None
    ui_eq_index = None
    if index_item is not None:
        index_totals = {
            "tokensIn": _num(index_item.get("tokensIn")),
            "tokensOut": _num(index_item.get("tokensOut")),
            "cacheWrites": _num(index_item.get("cacheWrites")),
            "cacheReads": _num(index_item.get("cacheReads")),
            "cost": _num(index_item.get("totalCost")),
        }
        ui_eq_index = all((index_totals[k] or 0) == metrics[k] for k in ("tokensIn", "tokensOut"))
    usage = {
        "api_requests": started_n,
        "api_requests_with_tokens": started_with_tokens,
        "api_requests_cancelled": dict(cancel.most_common()),
        "ui_api_req_started_sum": started,
        "ui_api_req_finished_rows": finished_n,
        "ui_api_req_finished_sum": finished,
        "ui_deleted_and_subagent_rows": extra_n,
        "ui_get_api_metrics": metrics,  # started + deleted_api_reqs + subagent_usage
        "index_totals": index_totals,
        "ui_equals_index": ui_eq_index,
    }

    # meta
    for mu in tm.get("model_usage") or []:
        if isinstance(mu, dict) and isinstance(mu.get("model_id"), str):
            models[mu["model_id"]] += 1
    env = [e for e in (tm.get("environment_history") or []) if isinstance(e, dict)]
    model = None
    if index_item and isinstance(index_item.get("modelId"), str):
        model = index_item["modelId"]
    elif models:
        model = models.most_common(1)[0][0]
    meta = {
        "harness": HARNESS,
        "path": str(task_dir),
        "session_id": task_dir.name,
        "task_id": task_dir.name,
        "started_at": _iso(base) if base else None,
        "started_at_source": base_src,
        "model": model,
        "models_seen": dict(models.most_common()) or None,
        "provider": (index_item or {}).get("apiProvider")
        or next(
            (
                mu.get("model_provider_id")
                for mu in tm.get("model_usage") or []
                if isinstance(mu, dict)
            ),
            None,
        ),
        "cwd": (index_item or {}).get("cwdOnTaskInitialization"),
        "cli_version": env[-1].get("cline_version") if env else None,
        "task": dg.mask(dg._trunc(str(index_item["task"]), 120))
        if index_item and isinstance(index_item.get("task"), str)
        else None,
        "is_legacy": (index_item or {}).get("isLegacy"),
    }

    diag.update(
        {
            "lines": len(ui),
            "records": len(ui) + len(api),
            "ui_messages": len(ui),
            "ui_partial_rows": partial,
            "api_messages": len(api),
            "api_roles": dict(roles.most_common()),
            "api_block_types": dict(blocks.most_common()),
            "api_string_content": string_content,
            "unknown_block_types": dict(unknown_blocks.most_common()),
            "task_metadata_files_in_context": len(tm.get("files_in_context") or []),
            "ts_epoch_ms": clock,
            "ts_counter_not_clock": counter,
            "no_timestamp": no_ts,
            "bad_timestamp": 0,
            "base_ts_source": base_src,
            "first_ts": _iso(lo),
            "last_ts": _iso(hi),
            "types": dict(types.most_common()),
            "unknown_types": dict(unknown.most_common()),
        }
    )
    return Scan(pathlib.Path(path), task_dir, ui, api, tm, index_item, base, meta, usage, diag)


def meta(path: pathlib.Path) -> dict:
    """task id / start / model / provider / cwd for one task."""
    return scan(path).meta


def usage(path: pathlib.Path) -> dict:
    """Token figures every way the store offers them: the sum over `api_req_started` rows,
    `api_req_finished` rows apart, and the `taskHistory.json` index totals apart. When
    they disagree the disagreement is the finding."""
    return scan(path).usage


def diagnostics(path: pathlib.Path) -> dict:
    """Files present, say/ask kinds, clock-vs-counter `ts`, api block types, plus the
    event-derivation counters (`derivation`) from a full load."""
    s = scan(path)
    d = dict(s.diagnostics)
    d["derivation"] = _derive(s)[1]
    return d


# ----------------------------------------------------------------------------- helpers


def _strip_envelopes(text: str, counters: Counter) -> str | None:
    """Human text out of Cline's wrappers. None means "not a human message at all"."""
    if not isinstance(text, str):
        return None
    if _ENV_DETAILS.search(text):
        counters["envelope_environment_details"] += 1
        text = _ENV_DETAILS.sub("", text)
    if text.lstrip().startswith(TASK_RESUMPTION_PREFIX):
        counters["task_resumption_message"] += 1
        m = _USER_MESSAGE.search(text)
        if not m:
            return None
        text = m.group(1)
    m = _WRAPPED.match(text)
    if m:
        counters[f"envelope_{m.group(1)}"] += 1
        text = m.group(2)
    text = text.strip()
    return text or None


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


def _edit_delta(payload: dict, counters: Counter) -> tuple[int, int] | None:
    """(+, -) for an editedExistingFile payload: SEARCH/REPLACE blocks first, unified diff
    second, else None (counted)."""
    for key in ("content", "diff"):
        text = payload.get(key)
        if not isinstance(text, str) or not text:
            continue
        blocks = _SEARCH_REPLACE.findall(text)
        if blocks:
            counters["edit_credit_search_replace"] += 1
            added = removed = 0
            for old, new in blocks:
                a, r = _line_delta(old, new)
                added, removed = added + a, removed + r
            return added, removed
        lines = text.splitlines()
        if any(ln.startswith(("@@", "+++ ")) for ln in lines):
            counters["edit_credit_unified_diff"] += 1
            return (
                sum(1 for ln in lines if ln.startswith("+") and not ln.startswith("+++")),
                sum(1 for ln in lines if ln.startswith("-") and not ln.startswith("---")),
            )
    counters["edit_credit_unknown_shape"] += 1
    return None


def _tool_event_from_ui(ts: float, payload: dict, model, counters: Counter) -> dg.Ev | None:
    kind = payload.get("tool")
    name = TOOL_BY_UI_KIND.get(kind) if isinstance(kind, str) else None
    if name is None:
        counters[f"ui_tool_kind_unknown_{kind}"] += 1
        name = str(kind or "tool")
    path = payload.get("path") if isinstance(payload.get("path"), str) else None
    ev = dg.Ev(0, ts, "tool", dg.mask(path or ""), tool=name, path=path, model=model)
    if name == WRITE_FILE_TOOL and isinstance(payload.get("content"), str):
        ev.added, ev.removed = _line_count(payload["content"]), 0
    elif name == EDIT_TOOL:
        d = _edit_delta(payload, counters)
        if d is not None:
            ev.added, ev.removed = d
    elif name == "search_files":
        ev.text = dg.mask(dg._trunc(str(payload.get("regex", "")), 80))
    elif name in ("web_fetch", "web_search"):
        ev.text = dg.mask(dg._trunc(str(payload.get("path") or payload.get("content") or ""), 100))
    return ev


def _shell_event(ts: float, command: str, model) -> dg.Ev:
    path, approx = dg._bash_file_effect(command)
    ev = dg.Ev(0, ts, "tool", "", tool=SHELL_TOOL, path=path, model=model)
    if approx is not None:
        ev.added, ev.removed = approx, 0
    ev.text = dg.mask(dg._trunc(command.replace("\n", " ⏎ "), dg.COMMAND_MAX))
    return ev


def _tool_event_from_api(ts: float, block: dict, model, counters: Counter) -> dg.Ev:
    """Fallback when the ui timeline has nothing for a tool_use block."""
    name = block.get("name") if isinstance(block.get("name"), str) else "tool"
    inp = block.get("input") if isinstance(block.get("input"), dict) else {}
    if name in SDK_SHELL_TOOLS or name == SHELL_TOOL:
        return _shell_event(ts, str(inp.get("command", "")), model)
    path = inp.get("path") if isinstance(inp.get("path"), str) else None
    ev = dg.Ev(0, ts, "tool", dg.mask(path or ""), tool=name, path=path, model=model)
    if name == WRITE_FILE_TOOL and isinstance(inp.get("content"), str):
        ev.added, ev.removed = _line_count(inp["content"]), 0
    elif name == EDIT_TOOL and isinstance(inp.get("diff"), str):
        d = _edit_delta({"content": inp["diff"]}, counters)
        if d is not None:
            ev.added, ev.removed = d
    elif (
        name == "editor"
        and isinstance(inp.get("old_text"), str)
        and isinstance(inp.get("new_text"), str)
    ):
        ev.added, ev.removed = _line_delta(inp["old_text"], inp["new_text"])
    elif not path:
        ev.text = (
            dg.mask(dg._trunc(json.dumps(inp, separators=(",", ":"))[:200], 100)) if inp else ""
        )
    return ev


def _result_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"
        )
    return ""


# ----------------------------------------------------------------------------- derive


def _derive(s: Scan, start: float | None = None, end: float | None = None):
    """Turn the task's files into `Ev`s. Returns (events, derivation counters).

    `ui_messages.json` is the timeline. `api_conversation_history.json` contributes tool
    inputs, tool_result errors, and — only when there is no timeline at all — prompts,
    replies and tool calls, every one placed at the task's start because that file has no
    clock. `task_metadata.json` contributes human edits."""
    counters: Counter = Counter()
    out: list[dg.Ev] = []
    order: list[tuple[float, int]] = []
    base = s.base_ts
    model = s.meta.get("model")

    def _emit(ev: dg.Ev) -> None:
        order.append((ev.ts, len(out)))
        out.append(ev)

    def _in_window(ts: float) -> bool:
        return (start is None or ts >= start) and (end is None or ts <= end)

    def _stamp(m: dict) -> float:
        ts = _ms(m.get("ts"))
        if ts is None:
            counters["ui_row_placed_at_task_start"] += 1
            return base
        return ts

    # api tool_use blocks, queued per class for pairing with the timeline
    api_calls: dict[str, deque] = {}
    api_by_id: dict[str, dict] = {}
    api_results: list[tuple[str, dict]] = []
    for m in s.api:
        c = m.get("content")
        if not isinstance(c, list):
            continue
        for b in c:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "tool_use":
                name = b.get("name") if isinstance(b.get("name"), str) else "tool"
                cls = _CLASS_BY_TOOL.get(name)
                if cls is None:
                    counters[f"api_tool_unclassified_{name}"] += 1
                    cls = f"other:{name}"
                api_calls.setdefault(cls, deque()).append(b)
                if isinstance(b.get("id"), str):
                    api_by_id[b["id"]] = b
            elif b.get("type") == "tool_result" and isinstance(b.get("tool_use_id"), str):
                api_results.append((b["tool_use_id"], b))

    ev_by_api_id: dict[str, dg.Ev] = {}

    def _pair(ev: dg.Ev, cls: str) -> None:
        q = api_calls.get(cls)
        if not q:
            counters["ui_tool_without_api_block"] += 1
            return
        b = q.popleft()
        counters["ui_tool_paired_with_api_block"] += 1
        ev.tool_id = b.get("id") if isinstance(b.get("id"), str) else None
        if ev.tool_id:
            ev_by_api_id[ev.tool_id] = ev
        inp = b.get("input") if isinstance(b.get("input"), dict) else {}
        if ev.path is None and isinstance(inp.get("path"), str):
            ev.path = inp["path"]
        if ev.added is None:
            probe = _tool_event_from_api(ev.ts, b, model, counters)
            if probe.added is not None:
                ev.added, ev.removed = probe.added, probe.removed
                counters["line_credit_from_api_input"] += 1

    last_tool_key = None  # (type, kind, text) of the previous ui tool row, for ask→say dedupe
    last_shell: dg.Ev | None = None
    if s.ui:
        for m in s.ui:
            t, ts = m.get("type"), _stamp(m)
            kind = m.get("say") if t == "say" else m.get("ask")
            text = m.get("text") if isinstance(m.get("text"), str) else ""
            if t == "say" and kind in ("task", "user_feedback"):
                human = _strip_envelopes(text, counters)
                if human is None:
                    counters["prompt_empty_after_strip"] += 1
                elif _in_window(ts):
                    counters["prompt"] += 1
                    _emit(dg.Ev(0, ts, "prompt", dg.mask(dg._trunc(human, dg.PROMPT_MAX))))
            elif t == "say" and kind in ("text", "completion_result", "plan_completion_result"):
                if text.strip() and _in_window(ts):
                    counters["assistant"] += 1
                    _emit(
                        dg.Ev(
                            0,
                            ts,
                            "assistant",
                            dg.mask(dg._trunc(text, dg.ASSISTANT_MAX)),
                            model=model,
                        )
                    )
            elif t == "ask" and kind in ("followup", "plan_mode_respond", "act_mode_respond"):
                info = _json_text(text)
                body = info.get("question") or info.get("response") or (text if not info else "")
                if isinstance(body, str) and body.strip() and _in_window(ts):
                    counters["assistant_from_ask"] += 1
                    _emit(
                        dg.Ev(
                            0,
                            ts,
                            "assistant",
                            dg.mask(dg._trunc(body, dg.ASSISTANT_MAX)),
                            model=model,
                        )
                    )
            elif kind == "tool" and t in ("say", "ask"):
                key = ("tool", text)
                if t == "say" and last_tool_key == key:
                    counters["tool_ask_then_say_deduped"] += 1
                    continue
                last_tool_key = key
                counters[f"tool_from_{t}"] += 1
                payload = _json_text(text)
                if not payload:
                    counters["tool_payload_not_json"] += 1
                    continue
                if not _in_window(ts):
                    continue
                ev = _tool_event_from_ui(ts, payload, model, counters)
                if ev is None:
                    continue
                _emit(ev)
                _pair(ev, _CLASS_BY_TOOL.get(ev.tool or "", f"other:{ev.tool}"))
                continue
            elif kind == "command" and t in ("say", "ask"):
                key = ("command", text)
                if t == "say" and last_tool_key == key:
                    counters["tool_ask_then_say_deduped"] += 1
                    continue
                last_tool_key = key
                counters[f"command_from_{t}"] += 1
                cmd, _, output = text.partition(COMMAND_OUTPUT_MARKER)
                if not _in_window(ts):
                    continue
                ev = _shell_event(ts, cmd, model)
                _emit(ev)
                _pair(ev, "shell")
                last_shell = ev
                if output.strip() and dg._looks_like_error(output.strip()):
                    counters["command_output_error"] += 1
                    _emit(
                        dg.Ev(
                            0,
                            ts,
                            "result_error",
                            dg.mask(dg._trunc(output, dg.ERROR_MAX)),
                            tool=SHELL_TOOL,
                            path=ev.path,
                            ok=False,
                            tool_id=ev.tool_id,
                        )
                    )
                continue
            elif t == "say" and kind == "command_output":
                if text.strip() and dg._looks_like_error(text.strip()) and _in_window(ts):
                    counters["command_output_error"] += 1
                    _emit(
                        dg.Ev(
                            0,
                            ts,
                            "result_error",
                            dg.mask(dg._trunc(text, dg.ERROR_MAX)),
                            tool=SHELL_TOOL,
                            path=last_shell.path if last_shell else None,
                            ok=False,
                            tool_id=last_shell.tool_id if last_shell else None,
                        )
                    )
            elif t == "say" and kind == "api_req_started":
                info = _json_text(text)
                reason = info.get("cancelReason")
                if reason == "user_cancelled":
                    if _in_window(ts):
                        counters["interrupt_user_cancelled"] += 1
                        _emit(dg.Ev(0, ts, "interrupt", ""))
                elif reason is not None:
                    counters[f"api_req_cancel_{reason}"] += 1
                    if isinstance(info.get("streamingFailedMessage"), str) and _in_window(ts):
                        _emit(
                            dg.Ev(
                                0,
                                ts,
                                "result_error",
                                dg.mask(dg._trunc(info["streamingFailedMessage"], dg.ERROR_MAX)),
                                tool="api_request",
                                ok=False,
                            )
                        )
            elif (t == "say" and kind in ("error", "diff_error")) or (
                t == "ask" and kind == "api_req_failed"
            ):
                if _in_window(ts):
                    counters[f"error_{kind}"] += 1
                    _emit(
                        dg.Ev(
                            0,
                            ts,
                            "result_error",
                            dg.mask(dg._trunc(text or "(error)", dg.ERROR_MAX)),
                            tool=EDIT_TOOL if kind == "diff_error" else "api_request",
                            ok=False,
                        )
                    )
            elif (
                (t == "say" and kind in ("deleted_api_reqs",))
                or (
                    t == "say"
                    and kind == "compaction"
                    and _json_text(text).get("status") == "completed"
                )
                or (t == "ask" and kind == "condense")
            ):
                if _in_window(ts):
                    counters["compaction"] += 1
                    _emit(dg.Ev(0, ts, "compaction", ""))
            else:
                counters[f"skipped_{t}_{kind}"] += 1
            if kind not in ("tool", "command"):
                last_tool_key = None
    else:
        # No timeline: the api history is all there is, and it has no clock.
        counters["timeline_from_api_history_no_clock"] += 1
        for m in s.api:
            c = m.get("content")
            parts = (
                [{"type": "text", "text": c}]
                if isinstance(c, str)
                else c
                if isinstance(c, list)
                else []
            )
            role = m.get("role")
            for b in parts:
                if not isinstance(b, dict):
                    continue
                bt = b.get("type")
                if role == "user" and bt == "text":
                    human = _strip_envelopes(b.get("text"), counters)
                    if human is None:
                        counters["prompt_empty_after_strip"] += 1
                    elif _in_window(base):
                        counters["prompt"] += 1
                        _emit(dg.Ev(0, base, "prompt", dg.mask(dg._trunc(human, dg.PROMPT_MAX))))
                elif role == "assistant" and bt == "text" and str(b.get("text", "")).strip():
                    if _in_window(base):
                        counters["assistant"] += 1
                        _emit(
                            dg.Ev(
                                0,
                                base,
                                "assistant",
                                dg.mask(dg._trunc(b["text"], dg.ASSISTANT_MAX)),
                                model=model,
                            )
                        )
                elif role == "assistant" and bt == "tool_use" and _in_window(base):
                    counters["tool_from_api_block"] += 1
                    ev = _tool_event_from_api(base, b, model, counters)
                    ev.tool_id = b.get("id") if isinstance(b.get("id"), str) else None
                    if ev.tool_id:
                        ev_by_api_id[ev.tool_id] = ev
                    _emit(ev)

    # tool_result errors from the api history, attached to the paired tool event
    for tid, b in api_results:
        body = _result_text(b.get("content"))
        is_err = bool(b.get("is_error")) or body.lstrip().startswith(TOOL_ERROR_PREFIX)
        if not is_err:
            continue
        ev = ev_by_api_id.get(tid)
        if ev is None:
            counters["api_tool_result_error_unpaired"] += 1
            continue
        counters["api_tool_result_error"] += 1
        msg = body.split("<error>", 1)[-1].split("</error>", 1)[0] if "<error>" in body else body
        _emit(
            dg.Ev(
                0,
                ev.ts,
                "result_error",
                dg.mask(dg._trunc(msg or "(error)", dg.ERROR_MAX)),
                tool=ev.tool,
                path=ev.path,
                ok=False,
                tool_id=tid,
            )
        )
    for cls, q in api_calls.items():
        if q:
            counters[f"api_tool_blocks_unpaired_{cls}"] += len(q)

    # human edits from task_metadata
    for f in s.task_meta.get("files_in_context") or []:
        if not isinstance(f, dict) or f.get("record_source") != "user_edited":
            continue
        ts = _ms(f.get("user_edit_date"))
        if ts is None:
            counters["human_edit_without_clock"] += 1
            continue
        if _in_window(ts):
            counters["human_edit"] += 1
            _emit(dg.Ev(0, ts, "human_edit", "", path=f.get("path")))

    out = [out[i] for _, i in sorted(order)]
    for i, e in enumerate(out):
        e.n = i
    return out, dict(sorted(counters.items()))


def load_events(
    path: pathlib.Path, start: float | None = None, end: float | None = None
) -> list[dg.Ev]:
    """Read one task (directory or either JSON file in it) into digest events, in time
    order, within [start, end]."""
    return _derive(scan(path), start, end)[0]
