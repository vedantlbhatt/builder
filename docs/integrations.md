# Where the transcripts are, and how a phone gets to them

Builder is mobile-first, but the raw material is on a computer. Every AI coding tool
writes its own conversation log to the machine it runs on, in its own shape, with its own
retention. This document is the map: for each tool, where the log is, what it looks like,
what it does and does not contain, and how far Builder is from reading it.

The shape of the product follows from one fact: **nothing on this list is reachable from
a phone.** The phone is where you *see* sessions; a small agent on the computer is what
*captures* them. "Log in with Claude Code" therefore means: install the Builder agent on
the machine where Claude Code runs, pair it with the phone once (a code, or the QR the
agent shows), and from then on sessions arrive on the phone on their own.

## Status

| tool | where | format | Builder status |
|---|---|---|---|
| Claude Code (local) | `~/.claude/projects/<slug>/<uuid>.jsonl` | JSONL, one record per content block | **shipping** — reference parser, 89 root sessions measured |
| Claude Code (web / phone / remote) | inside the cloud container's `~/.claude/projects` | same JSONL, prompts stamped `promptSource: "sdk"` + `origin.kind: "human"` | parser correct; **capture path missing** — the file never reaches the Mac (see below) |
| Cursor (IDE) | `~/Library/Application Support/Cursor/User/globalStorage/state.vscdb` | SQLite, `composerHeaders` + `cursorDiskKV` bubble rows | **shipping** — header-only fallback for GC'd bodies; no token counts, structurally |
| Codex CLI | `~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<uuid>.jsonl` | JSONL `{timestamp, type, payload}`; types `session_meta`, `response_item`, `event_msg`, `turn_context`, `compacted`, … | **shipping** — `analysis/codex.py` + `CodexParser.swift`, both held to `spec/fixtures/codex`; probe available; no real corpus measured yet |
| Gemini CLI | `~/.gemini/tmp/<project_hash>/chats/*.json` (and `checkpoints/`) | JSON, whole conversation per file; role `user`/`model`, `parts[]` with `functionCall`/`functionResponse` | **analysis loader shipping** (`analysis/gemini.py`, probe accepts `~/.gemini/tmp`); the current writer is JSONL `session-*.jsonl` — metadata line, one `MessageRecord` (`type` `user`/`gemini`, `content`, `toolCalls[]`, `tokens`) per line, `$set` / `$rewindTo` records — with whole-file `.json` as the legacy fallback; the same message id is re-appended on update, so tokens are summed per id, never per line; engine parser next |
| opencode | `~/.local/share/opencode/opencode.db` (SQLite, current); earlier `storage/session/<project>/<id>.json` + `storage/message/<session>/msg_*.json` | SQLite / JSON | researched, not implemented |
| Cline / Roo Code (VS Code) | `~/Library/Application Support/Code/User/globalStorage/saoudrizwan.claude-dev/tasks/<ts>/{api_conversation_history.json, ui_messages.json, task_metadata.json}` | JSON per task | researched, not implemented |
| Aider | `.aider.chat.history.md` and `.aider.input.history` **in the repo directory** | Markdown | researched, not implemented; per-repo, so discovery is by walking known repos |
| GitHub Copilot (VS Code / CLI) | VS Code `workspaceStorage/<hash>/chatSessions/*.json` (IDE); CLI logs under `~/.copilot/` | JSON | researched, low confidence on shapes |
| Windsurf / Antigravity | Codeium's local cascade store under the app's `User/globalStorage` | SQLite / JSON | not researched in depth |
| Claude.ai (chat, not code) | account data export ZIP → `conversations.json` | JSON | **import path**, not capture: the phone can open the export |
| ChatGPT | account data export ZIP → `conversations.json` (mapping tree) | JSON | import path, same as above |

Every "researched" row was established from documentation and third-party write-ups,
not from a corpus on disk. The house rule applies with full force: **a parser written from
a description ships with a diagnostics-first probe**, so the first real corpus tells us
what the description got wrong before any number reaches a card.

## What each source can and cannot say

**Claude Code** is the richest: per-block token usage (with the 1.878x content-block
duplication and the subagent double-count, both handled), tool inputs including
`structuredPatch` line deltas, `promptSource`, `edited_text_file` attachments,
`turn_duration`, titles. Retention is ~30 days (`~/.claude/.last-cleanup` advances live),
which is why Builder's Tier-A store is append-only: once ingested, we are the only copy.

**Cursor** never writes token counts (all 14,565 message rows are `{0, 0}`) and
garbage-collects message bodies at roughly two months; 433 of 482 conversations on the
reference machine are headers only. Sessions from headers get `timeline_fidelity:
header_only` and no strip.

**Codex** rollouts carry `token_count` events with `input_tokens`, `cached_input_tokens`,
`cache_write_input_tokens`, `output_tokens`, `reasoning_output_tokens`, `total_tokens`,
per turn — cumulative in `info.total_token_usage`, per-turn in `info.last_token_usage`.
Tool calls are `response_item` / `function_call` (`name`, `arguments` as a JSON string,
`call_id`), plus `local_shell_call` and `custom_tool_call` (`apply_patch` arrives as a
custom tool with a patch body in `input`). Human prompts are `event_msg` /
`user_message` (`message`), and ALSO appear as `response_item` messages with
`role: "user"` — but so do injected envelopes (`<user_instructions>`,
`<environment_context>`, `<turn_context>`), so the `event_msg` form is the one to count.
Line 1 is `session_meta` (`id`, `timestamp`, `cwd`, `originator`, `cli_version`, `git`).
Resumes append to the same file, exactly like Claude Code, so a file is not a session.

**Gemini CLI** saves whole conversations as JSON (role `user` / `model`, `parts[]` with
`text`, `functionCall {name, args}`, `functionResponse`), keyed by a hash of the project
path. Token counts are per-response `usageMetadata` when present.

**Cline** keeps two parallel files per task: `api_conversation_history.json` (the raw
Anthropic-style messages, with tool_use / tool_result blocks and usage) and
`ui_messages.json` (`say` / `ask` records with `ts` in ms — the timeline). Tasks are
directories named by creation timestamp; `state/taskHistory.json` is the index.

## Remote and cloud sessions

Sessions started from the Claude Code web or mobile UI run in a cloud container. Their
transcript is written there, in the same JSONL format, and is not on any machine the
Builder agent can see. Three ways to close that gap, in order of preference:

1. **The harness syncs the transcript down.** Claude Code has a session-sync mechanism
   (`CLAUDE_CODE_SYNC_SESSION_REFS` is set in remote sessions). If a resumed remote session
   lands the file under the local `~/.claude/projects`, the existing parser handles it
   with the `sdk`/`human` rule. Unverified; needs a corpus with a synced remote session.
2. **A provider API.** If a sessions/transcripts API exists for the account, the *server*
   can pull with the user's grant. This moves capture off the user's machine, which is a
   privacy-contract change, not a parser: only counts and shape would be derived
   server-side, and the raw transcript would be discarded after digesting. Not started.
3. **Export and import.** Claude.ai and ChatGPT both offer account data exports; the
   phone can open the ZIP with the system file picker and Builder can ingest
   `conversations.json` directly on the phone (JavaScript, no agent). This is the only
   capture path that needs no computer at all, and it is the right shape for "analyse my
   chats", as opposed to "analyse my build sessions".

## The probe rule

Before any new parser ships it gets a `probe` command that runs read-only over the real
store and prints: record types seen and their counts, records with no timestamp, unknown
shapes, the first and last timestamp, and — for Codex and Cline, which carry usage — the
naive token sum next to the deduplicated one. That output is what the parser's measured
constants come from. A parser with no probe output behind it is a description, not an
implementation.
