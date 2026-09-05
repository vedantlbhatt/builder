"""Read a Gemini CLI chat recording into the same `Ev` list the Claude Code loader
produces, so `digest.stats` / `digest.render` / `run.analyze` work unchanged.

Recordings live at `<global tmp>/<project id>/chats/session-<YYYY-MM-DDTHH-MM>-<id8>.jsonl`
(subagents: `chats/<parentSessionId>/<sessionId>.jsonl`). The global tmp dir is
`~/.gemini/tmp`; the project id is a short slug from `~/.gemini/projects.json`, and older
installs used a sha256 of the project path (config/storage.ts `performMigration`).

THE SHAPE IS NOT `{sessionId, messages: [{role, parts, usageMetadata}]}`. Every shape below
is marked VERIFIED (read from the Gemini CLI source on 2026-09-05, `main` branch, via
raw.githubusercontent.com/google-gemini/gemini-cli/main/packages/core/src/...) or ASSUMED.
The house rule applies: a parser written from a description ships with a diagnostics-first
probe (`python -m analysis probe ~/.gemini/tmp`), and the first real corpus decides what
this file got wrong before any number reaches a card.

VERIFIED shapes and where they come from
----------------------------------------
* Container. `ChatRecordingService.appendRecord` writes ONE JSON OBJECT PER LINE
  (services/chatRecordingService.ts). Line 1 is the metadata record `{sessionId,
  projectHash, startTime, lastUpdated, kind?, directories?}`; then one `MessageRecord`
  per line; `{"$set": {...}}` merges metadata (and `$set.messages` REPLACES the message
  list — written by `updateMessagesFromHistory`); `{"$rewindTo": <id>}` drops that message
  and everything after it (or everything, if the id is unknown) — `rewindTo`.
  `loadConversationRecord` classifies a line by key: `$rewindTo` string → rewind; `id`
  string → message; `$set` object → metadata update; `sessionId`+`projectHash` → metadata.
* Legacy container. Whole-file JSON of a `ConversationRecord`, read by
  `parseLegacyRecordFallback` (any object with `sessionId`), and migrated to `.jsonl` on
  resume. Both are read here; `Scan.diagnostics["container"]` says which.
* The same message id is APPENDED AGAIN whenever it changes: `pushMessage` writes the full
  record on every update, and `recordMessageTokens` / `recordToolCalls` update the last
  gemini message in place. The reader keeps a Map keyed by id in first-insertion order, so
  later copies replace earlier ones. Summing `tokens` over raw lines therefore overcounts;
  `usage()` reports the naive sum NEXT TO the per-id deduplicated one.
* `MessageRecord = {id, timestamp, content: PartListUnion, displayContent?} & ({type:
  'user'|'info'|'error'|'warning'} | {type: 'gemini', toolCalls?: ToolCallRecord[],
  thoughts?, tokens?: TokensSummary|null, model?})` — services/chatRecordingTypes.ts.
  `PartListUnion` is a string, a Part, or Part[] (`@google/genai`); a Part is `{text}`,
  `{functionCall: {id?, name, args}}`, `{functionResponse: {id, name, response}}`,
  `{inlineData}`, `{fileData}`, …, and text parts may carry `thought: true`.
* `TokensSummary {input, output, cached, thoughts?, tool?, total}` is built in
  `recordMessageTokens` from `promptTokenCount`, `candidatesTokenCount`,
  `cachedContentTokenCount`, `thoughtsTokenCount`, `toolUsePromptTokenCount`,
  `totalTokenCount`, each `?? 0`. Absent → `null`/missing, never zero-by-construction.
* `ToolCallRecord {id, name, args: object, result?: PartListUnion|null, status,
  timestamp, agentId?, displayName?, description?, resultDisplay?}` — chatRecordingTypes.ts,
  built in core/geminiChat.ts `recordCompletedToolCalls` (`timestamp` is the COMPLETION
  time, `status` is `CoreToolCallStatus`: validating | scheduled | error | success |
  executing | cancelled | awaiting_approval — scheduler/types.ts).
* A failed tool's result is `[{functionResponse: {id, name, response: {error:
  <message>}}}]` (scheduler/scheduler.ts `createErrorResponse`); a successful string result
  is `{response: {output: <text>}}` (utils/generateContentResponseUtilities.ts
  `createFunctionResponsePart`).
* Tool responses are ALSO recorded as `type: 'user'` messages whose parts are
  `functionResponse` parts (`recordSyntheticMessage('user', …)` in geminiChat.ts
  `sendMessageStream`, the `isOriginalFunctionResponse` branch). A user message is a
  prompt only when it carries no functionResponse part. Real prompts are recorded with
  `content: userContent.parts` and, when the typed text differs (e.g. `@file` expansion),
  `displayContent` holding what the person typed.
* `isIgnoredUserContent` (utils/sessionUtils.ts): empty, or starting with `/`, `?`,
  `<session_context>`, `<hook_context>` — slash commands and injections, not prompts.
* Model text is recorded as `type: 'gemini'` with `content: responseText` (a string) and
  `model`; a tool-call-only turn gets `recordSyntheticMessage('gemini', parts)` with
  `functionCall` parts and `thought` text parts. After `updateMessagesFromHistory` a gemini
  message's `content` can be the full parts array INCLUDING `functionCall` parts for calls
  that `toolCalls[]` already records — so `toolCalls[]` is authoritative and a
  `functionCall` part is only used when no record with the same id (or name+args) exists.
* A tool call that FAILED earns no credit. `_tool_event` reads lines and the file path
  from the call's ARGUMENTS, which describe what the model asked for, not what happened;
  so a `replace` whose `status` is `error` ("could not find the string to replace") must
  not add to lines or files edited. Credit is given only when `status == "success"` and
  the result carries no `response.error`; a cancelled / incomplete / failed call keeps its
  tool event and its error, with `added`/`removed`/`path` cleared and
  `tool_credit_withheld` counted.
* A shell command that exited NON-ZERO is recorded as `status: "success"`. tools/shell.ts
  sets `error` only for a spawn failure (`result.error`, `SHELL_EXECUTE_ERROR`) or a
  sandbox-expansion request; the exit status reaches the model as an `Exit Code: N` line
  inside `llmContent` (`Output: …\nExit Code: N\nProcess Group PGID: …`, wrapped in
  `<untrusted_context>` by `wrapUntrusted`), which `createFunctionResponsePart` delivers
  as `response.output`. scheduler/tool-executor.ts maps `toolResult.error === undefined`
  to `CoreToolCallStatus.Success`, so `status` alone calls every failed test run a
  success. `_result_error` therefore treats `(?m)^Exit Code: [1-9][0-9]*` in a shell
  result as an error and otherwise falls back to `digest._looks_like_error`, the same
  fallback the Claude Code loader applies to `tool_result` blocks.
* A subagent recording (`kind: "subagent"`, written to `chats/<parentSessionId>/<id>.jsonl`
  — chatRecordingService.ts, the `this.kind === 'subagent'` branches) records the PARENT
  MODEL's instruction through the same `recordMessage({type: 'user'})` path in
  geminiChat.ts `sendMessageStream` that records a person typing. Nobody typed it: it is
  emitted as `prompt_agent_authored`, which `digest.stats` does not count as a prompt,
  and counted under the same name.
* Tool names and argument keys — tools/definitions/base-declarations.ts:
  `run_shell_command {command, description?, dir_path?, is_background?}` (tools/shell.ts
  `ShellToolParams`), `write_file {file_path, content}`, `replace {file_path, old_string,
  new_string, allow_multiple?}`, `read_file {file_path, start_line?, end_line?}`, `glob`,
  `grep_search`, `list_directory`, `google_web_search`, `web_fetch`, `read_many_files`,
  `write_todos`, `ask_user`, `invoke_agent`, `activate_skill`, …

VERIFIED ON DISK (@google/gemini-cli 0.58.0 via npm, 2026-09-05; the file is the fixture)
----------------------------------------------------------------------------------------
`gemini -p "say hi"` with an invalid key (`GEMINI_CLI_TRUST_WORKSPACE=true` — headless
mode refuses an untrusted directory) wrote `~/.gemini/tmp/repo/chats/
session-2026-09-05T17-43-f8c061f4.jsonl`, kept verbatim as
spec/fixtures/gemini/real_first_records.jsonl. The project id is the slug `repo` from
`~/.gemini/projects.json`, and the filename stamp is `YYYY-MM-DDTHH-MM-<id8>`, as read from
the source. The recording is written BEFORE the API call: five lines —
  1. metadata `{sessionId, projectHash, startTime, lastUpdated, kind: "main"}`;
  2. `{"$set": {"messages": [<one user message whose text is the `<session_context>`
     injection>], "lastUpdated"}}` — `updateMessagesFromHistory` at startup, giving that
     message a 32-hex id rather than a uuid;
  3. the typed prompt `{"id": <uuid>, "timestamp", "type": "user", "content": [{"text":
     "say hi"}]}` — written by `recordMessage` before the request goes out;
  4. `{"$set": {"lastUpdated"}}`;
  5. after the 400: `{"$set": {"messages": [<session_context only>], "lastUpdated"}}` —
     the failed turn rolled back and the prompt REMOVED from the model's history.
Mirroring the reader's Map (line 5 replaces the list) therefore yields zero prompts for a
sitting where a person typed one. A message that was recorded and later dropped by a
`$set.messages` rebuild is kept (`set_messages_dropped_kept`, `messages_kept_after_rebuild`)
— the rebuild edits the model's context, not the record of what the person did. The
`<session_context>` message is ignored by prefix, as the source said. No `type: 'error'`
message was written for the API failure; the error went to stderr only.

ASSUMED (not in the current source; handled leniently and counted in diagnostics)
-------------------------------------------------------------------------------
* Older releases named the file argument `absolute_path` (read_file) or `path`; both are
  accepted as fallbacks for `file_path`.
* `write_file` line credit is the line count of `content` (newlines, +1 for an
  unterminated last line); `replace` credit is a real line diff of `old_string` →
  `new_string` (difflib), which matches what a `structuredPatch` would report. Neither has
  been compared against a corpus.
* `status: "cancelled"` is counted, not emitted as an interrupt: the source does not say
  whether it is the person pressing Escape or a policy denial.
* `type: 'error'` messages (UI/API errors, not tool failures) are counted, not emitted.

Nothing here reads `thoughts` or `thought: true` parts, for the same reason the Claude Code
loader does not read thinking blocks.
"""

from __future__ import annotations

import dataclasses
import difflib
import json
import pathlib
import re
from collections import Counter

from . import digest as dg

HARNESS = "gemini"

# VERIFIED: tools/definitions/base-declarations.ts.
SHELL_TOOL = "run_shell_command"
WRITE_FILE_TOOL = "write_file"
EDIT_TOOL = "replace"
READ_FILE_TOOL = "read_file"
FILE_TOOLS = frozenset({WRITE_FILE_TOOL, EDIT_TOOL, READ_FILE_TOOL, "read_many_files"})

# VERIFIED: chatRecordingTypes.ts `ConversationRecordExtra["type"]`.
KNOWN_MESSAGE_TYPES = frozenset({"user", "gemini", "info", "error", "warning"})
# VERIFIED: scheduler/types.ts `CoreToolCallStatus`.
KNOWN_STATUSES = frozenset(
    {"validating", "scheduled", "error", "success", "executing", "cancelled", "awaiting_approval"}
)
# VERIFIED: @google/genai `Part` keys the CLI writes; anything else is counted as unknown.
KNOWN_PART_KEYS = frozenset(
    {
        "text",
        "functionCall",
        "functionResponse",
        "inlineData",
        "fileData",
        "executableCode",
        "codeExecutionResult",
        "videoMetadata",
    }
)
_PART_META_KEYS = frozenset({"thought", "thoughtSignature"})
# VERIFIED: utils/sessionUtils.ts `isIgnoredUserContent`.
IGNORED_PROMPT_PREFIXES = ("/", "?", "<session_context>", "<hook_context>")

TOKEN_KEYS = ("input", "output", "cached", "thoughts", "tool", "total")

# VERIFIED: tools/shell.ts pushes `Exit Code: ${result.exitCode}` onto llmContent for a
# non-zero exit and still returns no `error`, so the ToolCallRecord says `success`.
_SHELL_EXIT_CODE = re.compile(r"(?m)^Exit Code: [1-9]\d*")


# ----------------------------------------------------------------------------- scan


@dataclasses.dataclass
class Scan:
    """Everything one pass over a recording yields. `load_events`, `meta`, `usage` and
    `diagnostics` are views on this; the probe prints all of it."""

    path: pathlib.Path
    messages: list[dict]  # final per-id records, first-insertion order (rewinds applied)
    meta: dict
    usage: dict
    diagnostics: dict


def _zero_tokens() -> dict:
    return {k: 0 for k in TOKEN_KEYS}


def _add_tokens(acc: dict, t) -> bool:
    if not isinstance(t, dict):
        return False
    for k in TOKEN_KEYS:
        v = t.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            acc[k] += int(v)
    return True


class _Conversation:
    """The reader's Map-keyed-by-id state machine, mirrored from `loadConversationRecord`."""

    def __init__(self) -> None:
        self.meta: dict = {}
        self.messages: dict[str, dict] = {}
        # Messages a `$set.messages` rebuild removed after they had been recorded. The
        # rebuild rewrites the history the MODEL will see next; it does not unmake what
        # happened. FOUND ON A REAL RECORDING (Gemini CLI 0.58.0, 2026-09-05, kept as
        # spec/fixtures/gemini/real_first_records.jsonl): a typed `say hi` was appended as
        # `{"id": "2824c40b-…", "type": "user", "content": [{"text": "say hi"}]}`; the API
        # call failed (invalid key) and geminiChat.ts rolled the turn back —
        # `agentHistory.rollback(historyLengthBefore)` then `updateMessagesFromHistory` —
        # writing `{"$set": {"messages": [<session_context only>], …}}`. Mirroring the
        # reader alone left the recording with ZERO prompts for a sitting where a person
        # typed one, which is exactly the "typed-only rule files it as unattended" trap.
        self.dropped: dict[str, dict] = {}
        self.naive = _zero_tokens()
        self.naive_records_with_tokens = 0
        self.kinds: Counter = Counter()

    def merged(self) -> list[dict]:
        """Final per-id records plus those a rebuild dropped and never re-listed."""
        out = list(self.messages.values())
        out += [m for mid, m in self.dropped.items() if mid not in self.messages]
        return out

    def _put(self, msg: dict) -> None:
        mid = msg["id"]
        if mid in self.messages:
            self.kinds["message_rewrite"] += 1
        self.messages[mid] = msg  # dict keeps first-insertion position, like the JS Map
        if isinstance(msg.get("tokens"), dict):
            self.naive_records_with_tokens += 1
            _add_tokens(self.naive, msg["tokens"])

    def record(self, r: dict) -> None:
        if isinstance(r.get("$rewindTo"), str):
            self.kinds["rewind"] += 1
            target = r["$rewindTo"]
            if target in self.messages:
                ids = list(self.messages)
                for mid in ids[ids.index(target) :]:
                    del self.messages[mid]
            else:
                self.kinds["rewind_unknown_id_cleared_all"] += 1
                self.messages.clear()
        elif isinstance(r.get("id"), str):
            self.kinds["message"] += 1
            self._put(r)
        elif isinstance(r.get("$set"), dict):
            self.kinds["set"] += 1
            s = r["$set"]
            if isinstance(s.get("messages"), list):
                self.kinds["set_messages_rebuild"] += 1
                before = self.messages
                self.messages = {}
                for m in s["messages"]:
                    if isinstance(m, dict) and isinstance(m.get("id"), str):
                        self._put(m)
                for mid, m in before.items():
                    if mid not in self.messages:
                        self.kinds["set_messages_dropped_kept"] += 1
                        self.dropped[mid] = m
            self.meta.update({k: v for k, v in s.items() if k != "messages"})
        elif isinstance(r.get("sessionId"), str) and isinstance(r.get("projectHash"), str):
            self.kinds["metadata"] += 1
            self.meta.update({k: v for k, v in r.items() if k != "messages"})
            if isinstance(r.get("messages"), list):  # entire legacy record on one line
                self.kinds["legacy_record_inline"] += 1
                for m in r["messages"]:
                    if isinstance(m, dict) and isinstance(m.get("id"), str):
                        self._put(m)
        else:
            self.kinds["unknown"] += 1


def scan(path: pathlib.Path) -> Scan:
    """One read of the file. Never raises on content: malformed lines, unknown record kinds
    and missing timestamps are counted, not thrown."""
    path = pathlib.Path(path)
    conv = _Conversation()
    diag = {
        "container": None,
        "lines": 0,
        "records": 0,
        "malformed_lines": 0,
        "partial_trailing_line": False,
    }

    data = path.read_bytes()
    parsed_whole = None
    if path.suffix == ".json":
        try:
            parsed_whole = json.loads(data)
        except json.JSONDecodeError:
            parsed_whole = None
    if isinstance(parsed_whole, dict):
        diag["container"] = "json"
        diag["records"] = 1
        conv.record(parsed_whole)
    else:
        diag["container"] = "jsonl"
        for line in data.splitlines(keepends=True):
            if not line.endswith(b"\n"):
                diag["partial_trailing_line"] = True
                break  # a partial trailing line is never consumed
            diag["lines"] += 1
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
            conv.record(r)

    messages = conv.merged()
    kept_after_rebuild = sum(1 for mid in conv.dropped if mid not in conv.messages)
    m = conv.meta
    meta = {
        "harness": HARNESS,
        "path": str(path),
        "session_id": m.get("sessionId"),
        "project_hash": m.get("projectHash"),
        "started_at": m.get("startTime"),
        "last_updated": m.get("lastUpdated"),
        "kind": m.get("kind") or "main",
        "summary": m.get("summary"),
        "directories": m.get("directories"),
    }

    # usage: deduplicated by message id (what the reader would load) next to the naive sum
    dedup = _zero_tokens()
    with_tokens = gemini_msgs = 0
    models: Counter = Counter()
    types: Counter = Counter()
    no_ts = bad_ts = 0
    for msg in messages:
        t = msg.get("type")
        types[str(t)] += 1
        raw = msg.get("timestamp")
        if raw is None:
            no_ts += 1
        elif not isinstance(raw, str) or dg._ts(raw) is None:
            bad_ts += 1
        if t == "gemini":
            gemini_msgs += 1
            if isinstance(msg.get("model"), str):
                models[msg["model"]] += 1
            if _add_tokens(dedup, msg.get("tokens")):
                with_tokens += 1
    usage = {
        "gemini_messages": gemini_msgs,
        "gemini_messages_with_tokens": with_tokens,
        "naive_records_with_tokens": conv.naive_records_with_tokens,
        "naive_sum_all_records": conv.naive,
        "deduped_by_message_id": dedup,
        "naive_equals_deduped": conv.naive == dedup,
    }
    if models:
        meta["model"] = models.most_common(1)[0][0]
        meta["models_seen"] = dict(models.most_common())

    diag.update(
        {
            "record_kinds": dict(conv.kinds.most_common()),
            "messages": len(messages),
            "messages_kept_after_rebuild": kept_after_rebuild,
            "no_timestamp": no_ts,
            "bad_timestamp": bad_ts,
            "types": dict(types.most_common()),
            "unknown_types": {k: v for k, v in types.items() if k not in KNOWN_MESSAGE_TYPES},
            "first_ts": m.get("startTime"),
            "last_ts": m.get("lastUpdated"),
        }
    )
    return Scan(path, messages, meta, usage, diag)


def meta(path: pathlib.Path) -> dict:
    """session id / project hash / start / kind / model for one recording."""
    return scan(path).meta


def usage(path: pathlib.Path) -> dict:
    """Token figures BOTH ways: the naive sum over every record line and the sum over the
    final per-id records. When they disagree the disagreement is the finding."""
    return scan(path).usage


def diagnostics(path: pathlib.Path) -> dict:
    """Record kinds, message types, timestamp gaps, unknown parts, plus the event-derivation
    counters (`derivation`) from a full load."""
    s = scan(path)
    d = dict(s.diagnostics)
    d["derivation"] = _derive(s)[1]
    return d


# ----------------------------------------------------------------------------- helpers


def _parts(content) -> list:
    """PartListUnion → list of Part-ish items (a bare string becomes one text part)."""
    if isinstance(content, str):
        return [{"text": content}] if content else []
    if isinstance(content, dict):
        return [content]
    if isinstance(content, list):
        return [p if isinstance(p, dict) else {"text": str(p)} for p in content]
    return []


def _text(parts: list, counters: Counter) -> str:
    """Join non-thought text parts; count unknown part shapes instead of raising."""
    out = []
    for p in parts:
        keys = set(p) - _PART_META_KEYS
        if not keys & KNOWN_PART_KEYS:
            counters[f"unknown_part_{'+'.join(sorted(keys)) or '(empty)'}"] += 1
            continue
        if isinstance(p.get("text"), str) and not p.get("thought"):
            out.append(p["text"])
    return "\n".join(out)


def _has_function_response(parts: list) -> bool:
    return any(isinstance(p.get("functionResponse"), dict) for p in parts)


def _is_ignored_prompt(text: str) -> bool:
    t = text.strip()
    return not t or t.startswith(IGNORED_PROMPT_PREFIXES)


def _file_path(args: dict):
    for k in ("file_path", "absolute_path", "path"):  # first VERIFIED, rest ASSUMED
        v = args.get(k)
        if isinstance(v, str) and v:
            return v
    return None


def _line_delta(old: str, new: str) -> tuple[int, int]:
    """(+ lines, - lines) of a minimal line diff, i.e. what a structuredPatch reports."""
    a, b = old.splitlines(), new.splitlines()
    added = removed = 0
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        if tag in ("replace", "insert"):
            added += j2 - j1
        if tag in ("replace", "delete"):
            removed += i2 - i1
    return added, removed


def _tool_event(ts: float, name: str, call_id, args, model: str | None) -> dg.Ev:
    """The tool event for one call, with `path` / `added` / `removed` read from the call's
    ARGUMENTS — what the model asked for. `_derive` withholds that credit when the call
    did not succeed."""
    args = args if isinstance(args, dict) else {}
    ev = dg.Ev(0, ts, "tool", "", tool=name, tool_id=call_id, model=model)
    if name == SHELL_TOOL:
        cmd = str(args.get("command", ""))
        path, approx = dg._bash_file_effect(cmd)
        ev.path = path
        if approx is not None:
            ev.added, ev.removed = approx, 0
        ev.text = dg.mask(dg._trunc(cmd.replace("\n", " ⏎ "), dg.COMMAND_MAX))
    elif name in FILE_TOOLS:
        ev.path = _file_path(args)
        ev.text = dg.mask(ev.path or "")
        if name == WRITE_FILE_TOOL:
            content = args.get("content")
            if isinstance(content, str):
                # lines written: newlines, plus one for an unterminated last line
                ev.added = content.count("\n") + (
                    1 if content and not content.endswith("\n") else 0
                )
                ev.removed = 0
        elif name == EDIT_TOOL:
            old, new = args.get("old_string"), args.get("new_string")
            if isinstance(old, str) and isinstance(new, str):
                ev.added, ev.removed = _line_delta(old, new)
    elif name in ("glob", "grep_search"):
        ev.text = dg.mask(dg._trunc(str(args.get("pattern", "")), 80))
    elif name in ("google_web_search", "web_fetch"):
        ev.text = dg.mask(dg._trunc(str(args.get("query") or args.get("prompt") or ""), 100))
    else:
        ev.text = (
            dg.mask(dg._trunc(json.dumps(args, separators=(",", ":"))[:200], 100)) if args else ""
        )
    return ev


def _result_error(result, status, name: str | None = None) -> tuple[bool, str]:
    """(is_error, text) from a ToolCallRecord's `status`, `result` parts and tool name.

    `status == "error"` and `response.error` are authoritative. A `success` is then
    re-examined, because the shell tool reports a non-zero exit as `Exit Code: N` in its
    output and no error at all (VERIFIED, tools/shell.ts); anything else that the shared
    `digest._looks_like_error` fallback recognises is an error too — the same rule the
    Claude Code loader applies to every `tool_result` block."""
    text = ""
    err = status == "error"
    for p in _parts(result):
        fr = p.get("functionResponse")
        if isinstance(fr, dict) and isinstance(fr.get("response"), dict):
            resp = fr["response"]
            if resp.get("error") is not None:
                err = True
                text = text or str(resp["error"])
            elif isinstance(resp.get("output"), str):
                text = text or resp["output"]
        elif isinstance(p.get("text"), str):
            text = text or p["text"]
    if not err and status == "success" and text:
        exited_non_zero = name == SHELL_TOOL and bool(_SHELL_EXIT_CODE.search(text))
        err = exited_non_zero or dg._looks_like_error(text)
    return err, text


# ----------------------------------------------------------------------------- derive


def _derive(s: Scan, start: float | None = None, end: float | None = None):
    """Turn final message records into `Ev`s. Returns (events, derivation counters).

    Events are ordered by (timestamp, file position), like the other loaders. A message
    with no timestamp is given the session start and counted, never interpolated — the
    writer stamps every record (`newMessage`), so this is a never-seen shape; if the probe
    ever counts one on a real corpus, that count is the finding."""
    counters: Counter = Counter()
    start_ts = dg._ts(s.meta.get("started_at")) if s.meta.get("started_at") else None
    # VERIFIED: a subagent's first user message is the parent model's instruction, recorded
    # through the same path as a typed prompt. Nobody typed it.
    agent_authored = s.meta.get("kind") == "subagent"
    out: list[dg.Ev] = []
    order: list[tuple[float, int]] = []  # (ts, emission index) — the sort key

    def _emit(ev: dg.Ev) -> None:
        order.append((ev.ts, len(out)))
        out.append(ev)

    def _in_window(ts: float) -> bool:
        return (start is None or ts >= start) and (end is None or ts <= end)

    def _stamp(raw, fallback: float | None, what: str) -> float | None:
        ts = dg._ts(raw) if isinstance(raw, str) else None
        if ts is None:
            counters[f"{what}_no_timestamp"] += 1
            if fallback is None:
                counters[f"{what}_dropped_no_timestamp"] += 1
            return fallback
        return ts

    for msg in s.messages:
        ts = _stamp(msg.get("timestamp"), start_ts, "message")
        if ts is None:
            continue
        t = msg.get("type")
        parts = _parts(msg.get("content"))

        if t == "user":
            if _has_function_response(parts):
                counters["user_tool_response_records"] += 1
                continue
            text = _text(parts, counters)
            disp = _parts(msg.get("displayContent"))
            if disp:
                dtext = _text(disp, counters)
                if dtext.strip():
                    counters["prompt_from_display_content"] += 1
                    text = dtext
            if _is_ignored_prompt(text):
                counters["prompt_ignored"] += 1
                continue
            if _in_window(ts):
                kind = "prompt_agent_authored" if agent_authored else "prompt"
                counters[kind] += 1
                _emit(dg.Ev(0, ts, kind, dg.mask(dg._trunc(text, dg.PROMPT_MAX))))

        elif t == "gemini":
            model = msg.get("model") if isinstance(msg.get("model"), str) else None
            tokens = msg.get("tokens") if isinstance(msg.get("tokens"), dict) else {}
            text = _text(parts, counters)
            if text.strip() and _in_window(ts):
                counters["assistant"] += 1
                _emit(
                    dg.Ev(
                        0,
                        ts,
                        "assistant",
                        dg.mask(dg._trunc(text, dg.ASSISTANT_MAX)),
                        model=model,
                        tok_out=tokens.get("output"),
                    )
                )
            calls = msg.get("toolCalls") if isinstance(msg.get("toolCalls"), list) else []
            seen_ids: set = set()
            seen_sig: set = set()
            for c in calls:
                if not isinstance(c, dict):
                    counters["tool_call_not_object"] += 1
                    continue
                name = c.get("name") if isinstance(c.get("name"), str) else "tool"
                cid = c.get("id")
                seen_ids.add(cid)
                seen_sig.add((name, json.dumps(c.get("args"), sort_keys=True, default=str)))
                cts = _stamp(c.get("timestamp"), ts, "tool_call")
                status = c.get("status")
                if status not in KNOWN_STATUSES:
                    counters[f"tool_status_unknown_{status}"] += 1
                elif status not in ("success", "error"):
                    counters[f"tool_status_{status}"] += 1
                if not _in_window(cts):
                    continue
                counters["tool_from_record"] += 1
                # The verdict comes BEFORE the tool event so that credit follows it: a
                # failed `replace` changed nothing, a cancelled `write_file` wrote nothing.
                err, rtext = _result_error(c.get("result"), status, name)
                ev = _tool_event(cts, name, cid, c.get("args"), model)
                asked_path = ev.path
                if status != "success" or err:
                    if ev.path or ev.added is not None:
                        counters["tool_credit_withheld"] += 1
                    ev.path = None
                    ev.added = ev.removed = None
                _emit(ev)
                if err:
                    counters["result_error"] += 1
                    _emit(
                        dg.Ev(
                            0,
                            cts,
                            "result_error",
                            dg.mask(dg._trunc(rtext or "(error)", dg.ERROR_MAX)),
                            tool=name,
                            path=asked_path,
                            ok=False,
                            tool_id=cid,
                        )
                    )
            # functionCall parts not covered by a ToolCallRecord (legacy / synced content)
            for p in parts:
                fc = p.get("functionCall")
                if not isinstance(fc, dict):
                    continue
                name = fc.get("name") if isinstance(fc.get("name"), str) else "tool"
                sig = (name, json.dumps(fc.get("args"), sort_keys=True, default=str))
                if (fc.get("id") is not None and fc.get("id") in seen_ids) or sig in seen_sig:
                    counters["function_call_part_deduped"] += 1
                    continue
                if _in_window(ts):
                    counters["tool_from_function_call_part"] += 1
                    _emit(_tool_event(ts, name, fc.get("id"), fc.get("args"), model))

        elif t in ("info", "error", "warning"):
            counters[f"message_type_{t}"] += 1
        else:
            counters[f"message_type_unknown_{t}"] += 1

    out = [out[i] for _, i in sorted(order)]
    for i, e in enumerate(out):
        e.n = i
    return out, dict(sorted(counters.items()))


def load_events(
    path: pathlib.Path, start: float | None = None, end: float | None = None
) -> list[dg.Ev]:
    """Read one recording into digest events, in file (= time) order, within [start, end]."""
    return _derive(scan(path), start, end)[0]
