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
| Claude Code (web / phone / remote) | inside the cloud container's `~/.claude/projects` | same JSONL, prompts stamped `promptSource: "sdk"` + `origin.kind: "human"` | **shipping via capture** — `python -m capture` (stdlib only, `docs/cloud-capture.md`) runs in the container from `Stop` / `SessionEnd` hooks, sessionizes with `scripts/measure_boundaries.py` and uploads contract v2 (anonymous mode, `repo_hash` only); held to `spec/fixtures/boundaries` by `make capture-test`; pairs per container until the server has a non-rotating headless credential |
| Cursor (IDE) | `~/Library/Application Support/Cursor/User/globalStorage/state.vscdb` | SQLite, `composerHeaders` + `cursorDiskKV` bubble rows | **shipping** — header-only fallback for GC'd bodies; no token counts, structurally |
| Codex CLI | `~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<uuid>.jsonl` | JSONL `{timestamp, type, payload}`; types `session_meta`, `response_item`, `event_msg`, `turn_context`, `compacted`, … | **shipping** — `analysis/codex.py` + `CodexParser.swift`, both held to `spec/fixtures/codex`; probe available; no real corpus measured yet |
| Gemini CLI | `~/.gemini/tmp/<project>/chats/session-<ts>-<id8>.jsonl` (subagents under `chats/<parent>/`; whole-file `.json` is the legacy form) | JSONL: a metadata line, then one `MessageRecord` (`type` `user`/`gemini`/`info`/`error`/`warning`, `content` parts, `toolCalls[]`, `tokens`, `model`) per line, plus `$set` merges and `$rewindTo` records; the same message id is re-appended on every update | **shipping** — `analysis/gemini.py` + `GeminiParser.swift`, both held to `spec/fixtures/gemini`; no real corpus measured yet |
| opencode | `~/.local/share/opencode/opencode.db` (SQLite, WAL; `opencode-<channel>.db` off the release channels; `$OPENCODE_DB`); the pre-SQLite `storage/session/<project>/<id>.json` + `storage/message/<id>/msg_*.json` + `storage/part/<msg>/prt_*.json` tree survives upgrades; `opencode export` writes `{info, messages: [{info, parts}]}` | one database for every session: `session` rows (`parent_id` on subagent children), `message` rows `{id, session_id, time_created, data}` and `part` rows `{id, message_id, session_id, data}` where `data` is the v1 `Info` / `Part` JSON (parts: `text`, `tool {tool, callID, state}`, `step-start`, `step-finish {tokens, cost}`, `compaction`, `subtask`, `retry`, `patch`, …); every time is epoch ms | **analysis loader shipping** (`analysis/opencode.py`; a session is `opencode.db/<session id>`, probe accepts `~/.local/share/opencode`), held to `spec/fixtures/opencode` in all three containers; tool ids `bash` / `edit` / `write` / `apply_patch`; an assistant message's `tokens` is its LAST step only while each `step-finish` part carries one API call, so the message sum (and the session row backfilled from it) UNDERcounts multi-step turns — the probe prints both; cache tokens are disjoint from `input`; a child session's first user text is agent-authored and never a prompt; `MessageAbortedError` is the interrupt; the store records no human file edits; verified against sst/opencode `dev` @ e289456 (v1.18.29, 2026-09-05); no real corpus measured yet; engine parser next |
| Cline / Roo Code (VS Code) | `~/Library/Application Support/Code/User/globalStorage/saoudrizwan.claude-dev/tasks/<ts>/{api_conversation_history.json, ui_messages.json, task_metadata.json}` | JSON per task | **analysis loader shipping** (`analysis/cline.py`, probe accepts `~/.cline/data` or a `tasks/` root; a session is the task DIRECTORY), held to `spec/fixtures/cline`; `ui_messages.json` is the timeline (`say`/`ask` rows, `ts` in ms — a real clock only on legacy tasks, a monotonic counter on the SDK path and detected as such), `api_conversation_history.json` supplies tool inputs and `is_error` results, `task_metadata.json` supplies human edits; tokens are the sum over `api_req_started` rows reported NEXT TO `state/taskHistory.json`'s totals; verified against cline/cline `main` @ dac3b35 (2026-09-04); no real corpus measured yet; engine parser next |
| Aider | `.aider.chat.history.md` and `.aider.input.history` **in the repo directory** — one pair per repo, every session appended to the same files | Markdown transcript: `# aider chat started at <naive local time>` opens each session (ONE FILE IS MANY SESSIONS), `#### ` lines are the typed input, assistant text is written verbatim, and every line Aider says about itself is a `> ` blockquote (`Tokens: 8.2k sent, 340 received. Cost: $0.03 message, $0.05 session.`, `Applied edit to <path>`, `Commit <sha> <message>`, `Running <cmd>`, the `tool_error` strings); the input history is prompt_toolkit's `FileHistory` — `# <stamp>` + `+<line>` per accepted input, the only per-prompt clock | **analysis loader shipping** (`analysis/aider.py`; a session is `.aider.chat.history.md/<YYYYMMDD-HHMMSS>`, probe accepts the repo directory or a directory of repos; daemon-side discovery is "walk the known repo roots" — engine parser next), held to `spec/fixtures/aider` (three synthetic sessions in one file) and `spec/fixtures/aider/real` (what aider 0.86.2 wrote); every line Aider writes itself ends in a two-space hard break, which is what separates a prompt or tool line from assistant markdown that starts with `#### ` or `> `; only prompts (typed or `--message`) and Aider's own `/run` entries are stamped (nearest input-history entry by text), assistant lines never are, so `wall_seconds` is a LOWER bound; tokens are Aider's rounded `k` strings reported as approximate, cost as printed, message sum NEXT TO the running session total; edit credit is a line diff of the SEARCH/REPLACE (or udiff / patch) block named by `Applied edit to`, `whole` gets path only; a turn with one failed block writes the others but announces none — the `Commit` line is the only trace (`commit_without_applied_edit`); tool names `run` / `apply_edit` / `commit` (the last via `digest.COMMIT_TOOLS`); verified against Aider-AI/aider `main` @ 5dc9490b (v0.86.3.dev, 2026-05-22) and against the files the REAL aider 0.86.2 wrote (two `--message` runs with an invalid key, one session each), which found four things the source reading missed: `add_to_input_history` writes every `--message` / `/run` entry TWICE (46–82 µs apart; folded to one stamp, counted `double_written_entries`); the session's first `> ` line is the COMMAND LINE (an announcement — `--model` is the model of last resort, flag names kept as `launch_flags`, values never); the raw `litellm.<Class>: …` line precedes Aider's friendly description and is the error, once per failure with the description folded, so a `BadRequestError` (no description in aider/exceptions.py) no longer scores `errors: 0`; and a failed request writes no `Tokens:` line at all, so usage is reported absent, never zero |
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
