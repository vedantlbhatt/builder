"""Read an OpenAI Codex CLI rollout file into the same `Ev` list the Claude Code loader
produces, so `digest.stats` / `digest.render` / `run.analyze` work unchanged.

Rollouts live at `~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<uuid>.jsonl`. Every line is
one `RolloutLine`: `{"timestamp": ..., "ordinal"?: n, "type": <tag>, "payload": {...}}`.

Every shape below is marked VERIFIED (read from the Codex source on 2026-09-05, `main`
branch, via raw.githubusercontent.com/openai/codex/main/codex-rs/...) or ASSUMED (an
older on-disk form the current source no longer writes, or a field we have only seen
described). The house rule applies: a parser written from a description ships with a
diagnostics-first probe (`python -m analysis probe`), and the first real corpus decides
what this file got wrong before any number reaches a card.

VERIFIED shapes and where they come from
----------------------------------------
* `RolloutLine {timestamp: String, ordinal: Option<u64>, #[serde(flatten)] item}` —
  history/src/lib.rs. The flattened item is `RolloutItemWire`, `#[serde(tag = "type",
  rename_all = "snake_case")]` with a `payload` field — history/src/rollout_payload.rs.
  Top-level types: session_meta, response_item (+ optional `metadata`),
  inter_agent_communication, inter_agent_communication_metadata, compacted, turn_context,
  token_usage_record, world_state, retained_context, security_risk_score, event_msg,
  realtime_item.
* Per-line timestamp format: `[year]-[month]-[day]T[hour]:[minute]:[second].[subsecond
  digits:3]Z`, always UTC — rollout/src/recorder.rs `JsonlWriter::write_rollout_item`.
  We parse leniently anyway (with or without fraction, with 'Z' or an offset).
* `SessionMeta` (protocol/src/protocol.rs): `id`, `timestamp`, `cwd`, `originator`,
  `cli_version`, `source`, `model_provider?`, `history_mode` ("legacy" | "paginated"), …
  plus `git?` from the flattening `SessionMetaLine`. NOTE: there is NO model field on
  session_meta; the model is on `TurnContextItem.model`.
* `TurnContextItem` (protocol.rs): `cwd`, `approval_policy`, `sandbox_policy`,
  `model: String`, `effort?`, `summary`, `turn_id?`.
* `EventMsg` (protocol.rs) is `#[serde(tag = "type", rename_all = "snake_case")]`:
  - `user_message` → `UserMessageEvent {message: String, images?, …}`
  - `agent_message` → `AgentMessageEvent {message: String, phase?}`
  - `token_count` → `TokenCountEvent {info: Option<TokenUsageInfo>, rate_limits}` with
    `TokenUsageInfo {total_token_usage, last_token_usage, model_context_window}` and
    `TokenUsage {input_tokens, cached_input_tokens, cache_write_input_tokens,
    output_tokens, reasoning_output_tokens, total_tokens}`
  - `task_started` / `task_complete` (`#[serde(rename = "task_started", alias =
    "turn_started")]` — the v1 wire names; v2 aliases accepted)
  - `turn_aborted` → `TurnAbortedEvent {turn_id?, reason: TurnAbortReason}` with
    `TurnAbortReason` snake_case: interrupted | replaced | review_ended | budget_limited.
    Only `interrupted` is a human presence signal.
  - `item_completed` → `ItemCompletedEvent {thread_id, turn_id, item: TurnItem}` where
    `TurnItem` is `#[serde(tag = "type")]` WITHOUT rename_all, so tags are PascalCase:
    "UserMessage" {content: [UserInput]}, "AgentMessage" {content: [{type: "Text",
    text}]}, … (protocol/src/items.rs). `UserInput` is snake_case-tagged: {type: "text",
    text} (protocol/src/user_input.rs).
* Persistence policy (rollout/src/policy.rs `should_persist_event_msg`): `user_message`
  and `agent_message` events are persisted ONLY when `history_mode == legacy`; paginated
  rollouts persist `item_completed` events carrying TurnItems instead. Both forms are
  read here and deduplicated. `exec_command_end` and `error` are never persisted.
* `ResponseItem` (protocol/src/models.rs) is `#[serde(tag = "type", rename_all =
  "snake_case")]`: message {role, content: [ContentItem]}, reasoning, local_shell_call
  {call_id?, status, action: {type: "exec", command: [String], …}}, function_call {name,
  arguments: String (JSON text), call_id}, function_call_output {call_id?, name?, output},
  custom_tool_call {call_id, name, input: String}, custom_tool_call_output {call_id, output},
  … `ContentItem` tags: input_text | input_image | input_audio | output_text.
* `FunctionCallOutputPayload` serializes as ONLY its body: a plain string or a list of
  `{type: "input_text", text}` items. `success` is internal metadata and is NOT written
  (models.rs `impl Serialize for FunctionCallOutputPayload`).
* exec_command output text (core/src/tools/context.rs `response_header`): lines
  "Chunk ID: …", "Wall time: N seconds", "Process exited with code N" (absent while the
  process is still running: "Process running with session ID N"), "Output:", then the
  output. That header is how a non-zero exit is detected.
* apply_patch: a freeform tool, so it arrives as `custom_tool_call` with `name:
  "apply_patch"` and the patch text in `input` (core/src/tools/handlers/apply_patch.rs
  requires `ToolPayload::Custom`). Success output begins "Success. Updated the following
  files:" (apply-patch/src/lib.rs); failures begin "apply_patch verification failed:".
* The `shell` tool's argument object has `command: [String]` (+ `workdir`, `timeout_ms`);
  `exec_command`'s has `cmd: String` (+ `workdir`, `yield_time_ms`, `max_output_tokens`)
  — core/src/tools/handlers/shell_spec.rs, unified_exec.rs `ExecCommandArgs`.
* Envelope tags: `<user_instructions>` and `<environment_context>` are constants in
  protocol.rs; `## My request for Codex:` (`USER_MESSAGE_BEGIN`) is a prefix that
  `strip_user_message_prefix` removes before a user message is previewed.

VERIFIED ON DISK (codex-cli 0.153.4 via npm, 2026-09-05; the files are the fixtures)
------------------------------------------------------------------------------------
Two rollouts written by the REAL binary, kept verbatim under spec/fixtures/codex/:
`real_first_records.jsonl` — `codex exec "say hi"` with an invalid key, killed while
retrying the API (10 lines, the writer's first records before any model turn); and
`real_tools_mock_model.jsonl` — the same binary talking to a local mock of the Responses
API (`-c model_providers.mock=…`, `include_apply_patch_tool=true`, `-s workspace-write`),
so every MODEL item is scripted but every record, header and output is the writer's own.
What they settle:
* `history_mode` is `"paginated"` by default: NO `user_message` / `agent_message`
  event_msg is persisted; the prompt is an `item_completed` `UserMessage` `{content:
  [{type: "text", text, text_elements: []}]}` plus a `response_item` user message with
  the same text (deduped here), the reply an `item_completed` `AgentMessage`
  `{content: [{type: "Text", text}]}` plus its `response_item` copy (deduped).
* `session_meta.payload` carries BOTH `session_id` and `id` (equal), `thread_source:
  "user"`, `base_instructions: {text, provenance: {type: "model", model}}` (21 KB of
  system prompt), `context_window: {window_id}`, `originator: "codex_exec"`, `source:
  "exec"`, and `git: {commit_hash, branch}`. Every line has `ordinal`.
* `turn_context.payload` has `turn_id`, `root_turn_id`, `workspace_roots`,
  `current_date`, `timezone`, `approvals_reviewer`, `permission_profile`,
  `active_permission_profile`, `personality`, `collaboration_mode`,
  `multi_agent_version`, `realtime_active` and `model` — the model IS there, as the
  source said. `world_state` (full state incl. `model`) is written before it.
* Every `response_item` carries an `id` (`msg_…`, `fc_…`, `fco_…`, `ctc_…`, `ctco_…`) and
  `internal_chat_message_metadata_passthrough {turn_id, create_time?, content_item_kinds?}`.
* The exec tool this build offers is `exec_command {cmd, justification?, login?, …}`
  (plus `write_stdin`); there is NO `shell` tool in the request. Its output header is
  `Chunk ID: <hex>` / `Wall time: N seconds` / `Process exited with code N` / `Original
  token count: N` / `Output:` — one line more than the source read said.
* `apply_patch` run THROUGH `exec_command` as `apply_patch <<'EOF' … EOF` is intercepted:
  the output is `Wall time: 0.0000 seconds\nOutput:\nExit code: 0\nWall time: 0.2
  seconds\nOutput:\nSuccess. Updated the following files:\nA notes2.md` — the bare
  `Exit code: N` form below is therefore CURRENT, not legacy. The `apply_patch` CUSTOM
  tool, even when enabled, answered `unsupported custom tool call: apply_patch`; a patch
  whose output does not begin `Success.` earns no credit (`apply_patch_output_not_success`,
  `tool_credit_withheld`).
* Paginated rollouts also persist `item_completed` `CommandExecution {command, cwd,
  parsed_cmd, status: completed|failed, stdout, stderr, exit_code, duration}` and
  `FileChange {changes: {<abs path>: {type: add|…, content}}, status, stdout}` items —
  counted (`item_completed_ignored_*`), not read: they repeat what the tool call and its
  output already carry.
* Tokens: one `token_usage_record {response_id, usage, turn_token_usage,
  thread_token_usage}` per response is written BEFORE the `token_count` event.
  MEASURED over 6 responses: sum of `usage` == final `thread_token_usage` == final
  `total_token_usage` == naive sum of `last_token_usage` (7,635). No repeated `info`.
* `task_complete {turn_id, last_agent_message, started_at, completed_at, duration_ms,
  time_to_first_token_ms}`; `task_started {turn_id, started_at, model_context_window,
  collaboration_mode_kind}`. `error` events were indeed never persisted: the invalid-key
  run retried silently and wrote nothing after the prompt.

ASSUMED (not in the current source; handled leniently and counted in diagnostics)
-------------------------------------------------------------------------------
* Older releases wrote the `shell` tool's output as a JSON string
  `{"output": "...", "metadata": {"exit_code": N, "duration_seconds": F}}`. The
  `"exit_code": N` regex exists for those files. Unverified against a corpus.
* Older releases may have written `apply_patch` as a `function_call` whose `arguments`
  JSON has an `input` key holding the patch. Handled; unverified.
* Some clients may have written `function_call_output.output` as an object with
  `content` / `output` / `success` keys. Handled; the current writer never does this.
* `token_count` events can repeat an unchanged `info` (rate-limit refreshes), which is
  exactly why `usage()` reports the naive sum of `last_token_usage` NEXT TO the final
  `total_token_usage` rather than picking one. Not seen on 0.153.4 (above); still
  reported both ways.

Nothing here reads `reasoning` items or `agent_reasoning*` events, for the same reason the
Claude Code loader does not read thinking blocks.
"""

from __future__ import annotations

import dataclasses
import json
import pathlib
import re
from collections import Counter

from . import digest as dg

HARNESS = "codex"

# Two texts are "the same message" when they match exactly and land within this window.
# response_item assistant messages and agent_message events are written by the same turn
# a few milliseconds apart; 2 s is generous and still far below any inter-turn gap.
DEDUPE_WINDOW_S = 2.0

# VERIFIED: history/src/rollout_payload.rs `RolloutItemWire` variants, snake_case.
KNOWN_TYPES = frozenset(
    {
        "session_meta",
        "response_item",
        "inter_agent_communication",
        "inter_agent_communication_metadata",
        "compacted",
        "turn_context",
        "token_usage_record",
        "world_state",
        "retained_context",
        "security_risk_score",
        "event_msg",
        "realtime_item",
    }
)

# VERIFIED: protocol/src/protocol.rs `EventMsg` variants, snake_case (84 names, generated
# from the source on 2026-09-05; `task_started`/`task_complete` are the wire names, the
# `turn_*` forms are their serde aliases and are kept so v2 files are not "unknown").
KNOWN_EVENT_TYPES = frozenset(
    (
        "agent_message",
        "agent_message_content_delta",
        "agent_reasoning",
        "agent_reasoning_raw_content",
        "agent_reasoning_section_break",
        "apply_patch_approval_request",
        "auth_recovery_completed",
        "auth_recovery_started",
        "collab_agent_interaction_begin",
        "collab_agent_interaction_end",
        "collab_agent_spawn_begin",
        "collab_agent_spawn_end",
        "collab_close_begin",
        "collab_close_end",
        "collab_resume_begin",
        "collab_resume_end",
        "collab_waiting_begin",
        "collab_waiting_end",
        "context_compacted",
        "deprecation_notice",
        "dynamic_tool_call_request",
        "dynamic_tool_call_response",
        "elicitation_request",
        "entered_review_mode",
        "environment_connected",
        "environment_disconnected",
        "error",
        "exec_approval_request",
        "exec_command_begin",
        "exec_command_end",
        "exec_command_output_delta",
        "exited_review_mode",
        "guardian_assessment",
        "guardian_warning",
        "hook_completed",
        "hook_started",
        "image_generation_begin",
        "image_generation_end",
        "item_completed",
        "item_started",
        "mcp_startup_complete",
        "mcp_startup_update",
        "mcp_tool_call_begin",
        "mcp_tool_call_end",
        "model_reroute",
        "model_verification",
        "patch_apply_begin",
        "patch_apply_end",
        "patch_apply_updated",
        "plan_delta",
        "plan_update",
        "raw_response_completed",
        "raw_response_item",
        "realtime_conversation_closed",
        "realtime_conversation_list_voices_response",
        "realtime_conversation_realtime",
        "realtime_conversation_sdp",
        "realtime_conversation_started",
        "reasoning_content_delta",
        "reasoning_raw_content_delta",
        "request_permissions",
        "request_user_input",
        "safety_buffering",
        "session_configured",
        "stream_error",
        "sub_agent_activity",
        "task_complete",
        "task_started",
        "terminal_interaction",
        "thread_goal_updated",
        "thread_queue_changed",
        "thread_rolled_back",
        "thread_settings_applied",
        "token_count",
        "turn_aborted",
        "turn_complete",
        "turn_diff",
        "turn_moderation_metadata",
        "turn_started",
        "user_message",
        "view_image_tool_call",
        "warning",
        "web_search_begin",
        "web_search_end",
    )
)

# VERIFIED: protocol/src/models.rs `ResponseItem` variants, snake_case.
KNOWN_RESPONSE_TYPES = frozenset(
    (
        "additional_tools",
        "message",
        "agent_message",
        "reasoning",
        "local_shell_call",
        "function_call",
        "tool_search_call",
        "function_call_output",
        "custom_tool_call",
        "custom_tool_call_output",
        "tool_search_output",
        "web_search_call",
        "image_generation_call",
        "compaction",
        "configuration_update",
        "compaction_trigger",
        "context_compaction",
        "other",
    )
)

SHELL_TOOLS = frozenset({"shell", "exec_command", "local_shell", "shell_command", "container.exec"})
APPLY_PATCH = "apply_patch"

# VERIFIED constants from protocol/src/protocol.rs.
ENVELOPE_TAGS = ("<user_instructions>", "<environment_context>", "<turn_context>")
USER_MESSAGE_BEGIN = "## My request for Codex:"

# VERIFIED: core/src/tools/context.rs `response_header` (first pattern), and ON DISK
# (0.153.4) the bare "Exit code: N" line that `apply_patch` through `exec_command` writes
# inside its Output section. ASSUMED: the JSON `metadata` form, from older releases.
_EXIT_PATTERNS = [
    re.compile(r"(?m)^Process exited with code (-?\d+)"),
    re.compile(r'"exit_code"\s*:\s*(-?\d+)'),
    re.compile(r"(?m)^Exit code:?\s+(-?\d+)"),
]
_APPLY_PATCH_FAIL = re.compile(r"(?m)^apply_patch (verification failed|handler received)")

_PATCH_PATH = re.compile(r"^\*\*\* (?:Update|Add) File: (?P<path>.+?)\s*$", re.MULTILINE)
_PATCH_PATH_ANY = re.compile(r"^\*\*\* (?:Update|Add|Delete) File: (?P<path>.+?)\s*$", re.MULTILINE)


# ----------------------------------------------------------------------------- scan


@dataclasses.dataclass
class Scan:
    """Everything one pass over a rollout file yields. `load_events`, `meta`, `usage` and
    `diagnostics` are views on this; the probe prints all of it."""

    path: pathlib.Path
    records: list[tuple[float, int, dict]]  # (ts, line index, record) — unsorted
    meta: dict
    usage: dict
    diagnostics: dict


def _zero_usage() -> dict:
    return {
        k: 0
        for k in (
            "input_tokens",
            "cached_input_tokens",
            "cache_write_input_tokens",
            "output_tokens",
            "reasoning_output_tokens",
            "total_tokens",
        )
    }


def _add_usage(acc: dict, u: dict | None) -> None:
    if not isinstance(u, dict):
        return
    for k in acc:
        v = u.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            acc[k] += int(v)


def _clean_usage(u) -> dict | None:
    if not isinstance(u, dict):
        return None
    out = _zero_usage()
    _add_usage(out, u)
    return out


def scan(path: pathlib.Path) -> Scan:
    """One read of the file. Never raises on content: malformed lines, unknown types and
    missing timestamps are counted, not thrown, because a probe that crashes on the first
    surprising record tells you nothing about the other 40,000."""
    path = pathlib.Path(path)
    records: list[tuple[float, int, dict]] = []
    types: Counter = Counter()
    payload_types: Counter = Counter()
    unknown_types: Counter = Counter()
    unknown_payload_types: Counter = Counter()
    diag = {
        "lines": 0,
        "records": 0,
        "malformed_lines": 0,
        "partial_trailing_line": False,
        "no_timestamp": 0,
        "bad_timestamp": 0,
        "no_type": 0,
        "first_ts": None,
        "last_ts": None,
    }
    meta: dict = {"harness": HARNESS, "path": str(path)}
    usage = {
        "token_count_events": 0,
        "token_count_events_with_info": 0,
        # naive: sum of `info.last_token_usage` over every token_count event.
        "naive_sum_last_token_usage": _zero_usage(),
        # authoritative candidate: the last `info.total_token_usage` seen.
        "final_total_token_usage": None,
        "max_total_tokens_seen": 0,
        "token_usage_records": 0,
        "token_usage_records_sum_usage": _zero_usage(),
        "token_usage_records_final_thread_usage": None,
    }
    models: Counter = Counter()
    first_ts = last_ts = None
    first_iso = last_iso = None

    with path.open("rb") as f:
        for i, line in enumerate(f):
            diag["lines"] += 1
            if not line.endswith(b"\n"):
                diag["partial_trailing_line"] = True
                diag["lines"] -= 1
                break  # a partial trailing line is never consumed
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                diag["malformed_lines"] += 1
                continue
            if not isinstance(r, dict):
                diag["malformed_lines"] += 1
                continue
            diag["records"] += 1
            t = r.get("type")
            if not isinstance(t, str):
                diag["no_type"] += 1
                t = "(none)"
            types[t] += 1
            if t not in KNOWN_TYPES:
                unknown_types[t] += 1
            p = r.get("payload")
            pt = p.get("type") if isinstance(p, dict) else None
            if t in ("event_msg", "response_item"):
                key = f"{t}/{pt or '(none)'}"
                payload_types[key] += 1
                known = KNOWN_EVENT_TYPES if t == "event_msg" else KNOWN_RESPONSE_TYPES
                if pt not in known:
                    unknown_payload_types[key] += 1

            raw_ts = r.get("timestamp")
            ts = dg._ts(raw_ts) if isinstance(raw_ts, str) else None
            if raw_ts is None:
                diag["no_timestamp"] += 1
            elif ts is None:
                diag["bad_timestamp"] += 1
            if ts is not None:
                if first_ts is None or ts < first_ts:
                    first_ts, first_iso = ts, raw_ts
                if last_ts is None or ts > last_ts:
                    last_ts, last_iso = ts, raw_ts
                records.append((ts, i, r))

            # --- side channels: meta and usage, independent of the event list
            if t == "session_meta" and isinstance(p, dict):
                git = p.get("git") if isinstance(p.get("git"), dict) else {}
                meta.update(
                    {
                        "session_id": p.get("id"),
                        "started_at": p.get("timestamp"),
                        "cwd": p.get("cwd"),
                        "originator": p.get("originator"),
                        "cli_version": p.get("cli_version"),
                        "source": p.get("source")
                        if isinstance(p.get("source"), str)
                        else json.dumps(p.get("source"))
                        if p.get("source") is not None
                        else None,
                        "model_provider": p.get("model_provider"),
                        "history_mode": p.get("history_mode") or "legacy",
                        "git_branch": git.get("branch"),
                        "git_commit": git.get("commit_hash"),
                    }
                )
            elif t == "turn_context" and isinstance(p, dict):
                if isinstance(p.get("model"), str):
                    models[p["model"]] += 1
                    meta["model"] = p["model"]
                if meta.get("cwd") is None and p.get("cwd"):
                    meta["cwd"] = p.get("cwd")
            elif t == "event_msg" and pt == "token_count" and isinstance(p, dict):
                usage["token_count_events"] += 1
                info = p.get("info")
                if isinstance(info, dict):
                    usage["token_count_events_with_info"] += 1
                    _add_usage(usage["naive_sum_last_token_usage"], info.get("last_token_usage"))
                    tot = _clean_usage(info.get("total_token_usage"))
                    if tot is not None:
                        usage["final_total_token_usage"] = tot
                        usage["max_total_tokens_seen"] = max(
                            usage["max_total_tokens_seen"], tot["total_tokens"]
                        )
            elif t == "token_usage_record" and isinstance(p, dict):
                usage["token_usage_records"] += 1
                _add_usage(usage["token_usage_records_sum_usage"], p.get("usage"))
                thread = _clean_usage(p.get("thread_token_usage"))
                if thread is not None:
                    usage["token_usage_records_final_thread_usage"] = thread

    if models:
        meta["models_seen"] = dict(models.most_common())
    naive = usage["naive_sum_last_token_usage"]["total_tokens"]
    final = (usage["final_total_token_usage"] or {}).get("total_tokens")
    usage["naive_sum_equals_final_total"] = (final is not None) and naive == final
    diag.update(
        {
            "first_ts": first_iso,
            "last_ts": last_iso,
            "types": dict(types.most_common()),
            "payload_types": dict(sorted(payload_types.items())),
            "unknown_types": dict(unknown_types.most_common()),
            "unknown_payload_types": dict(unknown_payload_types.most_common()),
        }
    )
    return Scan(path, records, meta, usage, diag)


def meta(path: pathlib.Path) -> dict:
    """cwd / model / cli_version / history_mode / git for one rollout file."""
    return scan(path).meta


def usage(path: pathlib.Path) -> dict:
    """Token figures, BOTH ways: the naive sum of per-turn `last_token_usage` and the final
    cumulative `total_token_usage`. The digest header may state either only if it says
    which; when they disagree the disagreement is the finding."""
    return scan(path).usage


def diagnostics(path: pathlib.Path) -> dict:
    """Record-type counts, unknown types, timestamp gaps, malformed lines, plus the
    event-derivation counters (`derivation`) from a full load."""
    s = scan(path)
    d = dict(s.diagnostics)
    d["derivation"] = _derive(s)[1]
    return d


# ----------------------------------------------------------------------------- helpers


def _content_text(content, kinds: tuple[str, ...]) -> str:
    """Join the text of ContentItem / UserInput / AgentMessageContent lists."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = []
    for b in content:
        if isinstance(b, dict) and b.get("type") in kinds and isinstance(b.get("text"), str):
            parts.append(b["text"])
    return "\n".join(parts)


def _output_text(output) -> tuple[str, bool | None]:
    """(text, success) for a function_call_output / custom_tool_call_output payload.

    VERIFIED: a string, or a list of {type: input_text, text}. ASSUMED: an object with
    `content` / `output` and maybe `success` from older clients."""
    if isinstance(output, str):
        return output, None
    if isinstance(output, list):
        return _content_text(output, ("input_text", "output_text", "text")), None
    if isinstance(output, dict):
        success = output.get("success")
        inner = output.get("content", output.get("output"))
        text, _ = _output_text(inner) if inner is not None else ("", None)
        if not text:
            text = json.dumps(output, separators=(",", ":"))
        return text, success if isinstance(success, bool) else None
    return "", None


def _exit_code(text: str) -> int | None:
    head = text[:600]
    for pat in _EXIT_PATTERNS:
        m = pat.search(head)
        if m:
            try:
                return int(m.group(1))
            except ValueError:
                continue
    return None


def _is_error(text: str, success: bool | None) -> bool:
    if success is False:
        return True
    code = _exit_code(text)
    if code is not None:
        return code != 0
    return bool(_APPLY_PATCH_FAIL.search(text[:400])) or dg._looks_like_error(text)


def _strip_prompt_prefix(text: str) -> str:
    # VERIFIED: protocol.rs `strip_user_message_prefix`.
    i = text.find(USER_MESSAGE_BEGIN)
    return text[i + len(USER_MESSAGE_BEGIN) :].strip() if i >= 0 else text.strip()


def _is_envelope(text: str) -> bool:
    return text.lstrip().startswith(ENVELOPE_TAGS)


def _patch_effect(patch: str) -> tuple[str | None, int, int]:
    """(first touched path, '+' lines, '-' lines) for an apply_patch body.

    Header lines (`*** …`, `@@ …`) are excluded; the Codex patch grammar has no `+++`/`---`
    file headers so every remaining +/- line is a content line."""
    m = _PATCH_PATH.search(patch) or _PATCH_PATH_ANY.search(patch)
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


def _parse_args(s) -> dict:
    if isinstance(s, dict):
        return s
    if not isinstance(s, str):
        return {}
    try:
        v = json.loads(s)
    except json.JSONDecodeError:
        return {}
    return v if isinstance(v, dict) else {}


def _command_text(args: dict) -> str:
    cmd = args.get("command")
    if cmd is None:
        cmd = args.get("cmd")
    if isinstance(cmd, list):
        return " ".join(str(c) for c in cmd)
    return str(cmd or "")


def _tool_event(ts: float, name: str, call_id, desc_src: str, model: str | None) -> dg.Ev:
    """Build a tool Ev; shell commands get the heredoc/sed file-effect the Bash path gets."""
    ev = dg.Ev(0, ts, "tool", "", tool=name, tool_id=call_id, model=model)
    if name in SHELL_TOOLS:
        if "*** Begin Patch" in desc_src:
            # `apply_patch <<'EOF' … EOF` run through the shell — VERIFIED that the
            # apply-patch crate accepts this form (`maybe_parse_apply_patch`).
            path, added, removed = _patch_effect(desc_src)
            ev.path, ev.added, ev.removed = path, added, removed
        else:
            path, approx = dg._bash_file_effect(desc_src)
            ev.path = path
            if approx is not None:
                ev.added, ev.removed = approx, 0
        ev.text = dg.mask(dg._trunc(desc_src.replace("\n", " ⏎ "), dg.COMMAND_MAX))
    elif name == APPLY_PATCH:
        path, added, removed = _patch_effect(desc_src)
        ev.path, ev.added, ev.removed = path, added, removed
        ev.text = dg.mask(path or "")
    else:
        ev.text = dg.mask(dg._trunc(desc_src.replace("\n", " "), 100)) if desc_src else ""
    return ev


# ----------------------------------------------------------------------------- derive


def _derive(s: Scan, start: float | None = None, end: float | None = None):
    """Turn scanned records into `Ev`s. Returns (events, derivation counters)."""
    recs = sorted(
        (
            (ts, i, r)
            for ts, i, r in s.records
            if (start is None or ts >= start) and (end is None or ts <= end)
        ),
        key=lambda t: (t[0], t[1]),
    )
    counters: Counter = Counter()

    # Pass 1: the event_msg texts (and paginated item_completed texts) that response_item
    # copies must defer to. Keyed on the exact text; values are timestamps.
    agent_ts: dict[str, list[float]] = {}
    user_ts: dict[str, list[float]] = {}
    for ts, _, r in recs:
        if r.get("type") != "event_msg":
            continue
        p = r.get("payload")
        if not isinstance(p, dict):
            continue
        pt = p.get("type")
        if pt == "agent_message" and isinstance(p.get("message"), str):
            agent_ts.setdefault(p["message"].strip(), []).append(ts)
        elif pt == "user_message" and isinstance(p.get("message"), str):
            user_ts.setdefault(_strip_prompt_prefix(p["message"]), []).append(ts)
        elif pt == "item_completed" and isinstance(p.get("item"), dict):
            item = p["item"]
            it = item.get("type")
            if it in ("AgentMessage", "agent_message"):
                txt = _content_text(item.get("content"), ("Text", "text", "output_text"))
                if txt.strip():
                    agent_ts.setdefault(txt.strip(), []).append(ts)

    def _near(index: dict[str, list[float]], text: str, ts: float) -> bool:
        return any(abs(t - ts) <= DEDUPE_WINDOW_S for t in index.get(text, ()))

    out: list[dg.Ev] = []
    tool_names: dict[str, tuple[str, str | None]] = {}  # call_id -> (tool, path)
    tool_events: dict[str, dg.Ev] = {}  # call_id -> the tool Ev, so a failed output can
    patch_credit: set[str] = set()  # …withhold the line/file credit a patch body earned
    model: str | None = None
    emitted_prompts: dict[str, list[float]] = {}
    emitted_assistant: dict[str, list[float]] = {}

    def _prompt(ts: float, raw: str, source: str) -> None:
        text = _strip_prompt_prefix(raw)
        if not text:
            counters["prompt_empty"] += 1
            return
        if _is_envelope(text):
            counters[f"prompt_envelope_skipped_{source}"] += 1
            return
        if _near(emitted_prompts, text, ts):
            counters["prompt_deduped"] += 1
            return
        emitted_prompts.setdefault(text, []).append(ts)
        counters[f"prompt_from_{source}"] += 1
        out.append(dg.Ev(0, ts, "prompt", dg.mask(dg._trunc(text, dg.PROMPT_MAX))))

    def _remember(cid: str, name: str, src: str, ev: dg.Ev) -> None:
        tool_names[cid] = (name, ev.path)
        tool_events[cid] = ev
        if name == APPLY_PATCH or "*** Begin Patch" in src:
            patch_credit.add(cid)

    def _assistant(ts: float, raw: str, source: str) -> None:
        text = raw.strip()
        if not text:
            return
        if _near(emitted_assistant, text, ts):
            counters["assistant_deduped"] += 1
            return
        emitted_assistant.setdefault(text, []).append(ts)
        counters[f"assistant_from_{source}"] += 1
        out.append(
            dg.Ev(0, ts, "assistant", dg.mask(dg._trunc(text, dg.ASSISTANT_MAX)), model=model)
        )

    for ts, _, r in recs:
        t = r.get("type")
        p = r.get("payload")
        if not isinstance(p, dict):
            if t in ("event_msg", "response_item", "turn_context", "compacted"):
                counters["payload_not_object"] += 1
            continue
        pt = p.get("type")

        if t == "turn_context":
            if isinstance(p.get("model"), str):
                model = p["model"]

        elif t == "compacted":
            out.append(dg.Ev(0, ts, "compaction", ""))

        elif t == "event_msg":
            if pt == "user_message":
                # THE prompt source. response_item user messages are not used: the same
                # channel carries `<environment_context>` / `<user_instructions>` envelopes
                # and per-turn context injections, indistinguishable by role alone. The
                # event_msg is written only for what the person actually sent.
                if isinstance(p.get("message"), str):
                    _prompt(ts, p["message"], "event_msg")
            elif pt == "agent_message":
                if isinstance(p.get("message"), str):
                    _assistant(ts, p["message"], "event_msg")
            elif pt == "turn_aborted":
                if p.get("reason") == "interrupted":
                    out.append(dg.Ev(0, ts, "interrupt", ""))
                else:
                    counters[f"turn_aborted_{p.get('reason')}"] += 1
            elif pt == "item_completed" and isinstance(p.get("item"), dict):
                item = p["item"]
                it = item.get("type")
                if it in ("UserMessage", "user_message"):
                    txt = _content_text(item.get("content"), ("text", "input_text"))
                    _prompt(ts, txt, "item_completed")
                elif it in ("AgentMessage", "agent_message"):
                    txt = _content_text(item.get("content"), ("Text", "text", "output_text"))
                    _assistant(ts, txt, "item_completed")
                else:
                    counters[f"item_completed_ignored_{it}"] += 1
            # token_count, task_*/turn_*, reasoning, deltas, approvals …: not events.

        elif t == "response_item":
            if pt == "function_call":
                name = p.get("name") if isinstance(p.get("name"), str) else "tool"
                args = _parse_args(p.get("arguments"))
                if name == APPLY_PATCH:
                    # ASSUMED older function-call form: patch under `input`.
                    src = str(args.get("input") or args.get("patch") or "")
                elif name in SHELL_TOOLS:
                    src = _command_text(args)
                else:
                    src = json.dumps(args, separators=(",", ":"))[:200] if args else ""
                ev = _tool_event(ts, name, p.get("call_id"), src, model)
                _remember(str(p.get("call_id")), name, src, ev)
                out.append(ev)
            elif pt == "local_shell_call":
                action = p.get("action") if isinstance(p.get("action"), dict) else {}
                src = _command_text(action)
                cid = p.get("call_id") or p.get("id")
                ev = _tool_event(ts, "shell", cid, src, model)
                _remember(str(cid), "shell", src, ev)
                out.append(ev)
            elif pt == "custom_tool_call":
                name = p.get("name") if isinstance(p.get("name"), str) else "tool"
                src = p.get("input") if isinstance(p.get("input"), str) else ""
                ev = _tool_event(ts, name, p.get("call_id"), src, model)
                _remember(str(p.get("call_id")), name, src, ev)
                out.append(ev)
            elif pt in ("function_call_output", "custom_tool_call_output"):
                cid = str(p.get("call_id"))
                name, path = tool_names.get(cid, ("tool", None))
                if cid not in tool_names:
                    counters["output_without_call"] += 1
                text, success = _output_text(p.get("output"))
                is_err = _is_error(text, success)
                if name == APPLY_PATCH and not is_err and not text.lstrip().startswith("Success"):
                    # VERIFIED ON DISK (codex-cli 0.153.4, spec/fixtures/codex/
                    # real_tools_mock_model.jsonl): with `include_apply_patch_tool=true`
                    # the custom tool call was answered `unsupported custom tool call:
                    # apply_patch` — no "verification failed" prefix, no exit code, and
                    # notes.md was never created — yet the digest credited `+3/-0
                    # notes.md`. A patch is applied only when the output says `Success.`
                    # (apply-patch/src/lib.rs); anything else is a failure.
                    is_err = True
                    counters["apply_patch_output_not_success"] += 1
                if is_err and cid in patch_credit:
                    ev = tool_events.get(cid)
                    if ev is not None and (ev.added is not None or ev.path):
                        # The tool event was built from the patch BODY — what the model
                        # asked for. It changed nothing; credit follows the verdict.
                        ev.added = ev.removed = None
                        ev.path = None
                        counters["tool_credit_withheld"] += 1
                if is_err:
                    out.append(
                        dg.Ev(
                            0,
                            ts,
                            "result_error",
                            dg.mask(dg._trunc(text or "(error)", dg.ERROR_MAX)),
                            tool=name,
                            path=path,
                            ok=False,
                            tool_id=cid,
                        )
                    )
            elif pt == "message":
                role = p.get("role")
                if role == "assistant":
                    txt = _content_text(p.get("content"), ("output_text",))
                    _assistant(ts, txt, "response_item")
                elif role == "user":
                    counters["response_item_user_messages"] += 1
                    txt = _content_text(p.get("content"), ("input_text",))
                    if _is_envelope(txt):
                        counters["response_item_user_envelopes"] += 1
                else:
                    counters[f"response_item_message_{role}"] += 1
            # reasoning, web_search_call, compaction items, …: never read.

    for i, e in enumerate(out):
        e.n = i
    return out, dict(sorted(counters.items()))


def load_events(
    path: pathlib.Path, start: float | None = None, end: float | None = None
) -> list[dg.Ev]:
    """Read one rollout into digest events, in time order, within [start, end]."""
    return _derive(scan(path), start, end)[0]
