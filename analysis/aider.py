"""Read one Aider session out of a repo's `.aider.chat.history.md` into the same `Ev` list
the Claude Code loader produces, so `digest.stats` / `digest.render` / `run.analyze` work
unchanged.

Aider keeps its history IN THE REPO DIRECTORY, one pair of files per repo, every session
appended to the same files:

    <repo>/.aider.chat.history.md    markdown transcript; `# aider chat started at …`
                                     opens each session, so ONE FILE IS MANY SESSIONS
    <repo>/.aider.input.history      prompt_toolkit FileHistory: `# <stamp>` + `+<line>`
                                     per accepted input — the only per-prompt clock

A session is addressed as `<chat file>/<session id>` (a virtual path; the id is the header
stamp as `YYYYMMDD-HHMMSS`, `-2`/`-3` on a duplicate second). The repo directory, the bare
chat file, or the input file all resolve to the LAST session in the file — the one being
appended to — and `meta["session_selected_by"] == "latest"` says so; `list_sessions`
exposes every one, and the probe reports each. Discovery on the daemon side is "walk the
known repo roots for `.aider.chat.history.md`" (there is no central store); the engine is
out of scope here.

Every shape below is marked VERIFIED (read from Aider-AI/aider `main` at commit 5dc9490b,
2026-05-22, __version__ 0.86.3.dev, fetched 2026-09-05 via raw.githubusercontent.com and a
depth-1 clone — file hashes matched; re-fetched 2026-09-05 18:09 UTC, `git ls-remote
refs/heads/main` still 5dc9490bb35f, and diffed against the INSTALLED aider 0.86.2 that
wrote the real fixture: io.py and main.py byte-identical, exceptions.py on main adds only
`PermissionDeniedError`, base_coder.py differs by two `auto_commits` guards) or ASSUMED.
The house rule applies: a parser written from a description ships with a diagnostics-first
probe (`python -m analysis probe <repo>`), and the first real corpus decides what this file
got wrong before any number reaches a card — VERIFIED ON DISK below is what it decided.

VERIFIED shapes and where they come from
----------------------------------------
* The writer is ONE function, `InputOutput.append_chat_history(text, linebreak, blockquote,
  strip)` (aider/io.py:1117): `blockquote` prefixes `"> "`, `linebreak` rstrips and appends
  `"  \\n"` (a markdown hard break). Everything Aider says about itself goes through
  `_tool_message` / `tool_output` / `confirm_ask` / `prompt_ask` with BOTH flags
  (io.py:960-999, 905-923), and every human input goes through `user_input` (io.py:775):
  `"\\n#### " + "  \\n#### ".join(lines)` with `linebreak=True`, an empty input written as
  `#### <blank>`. So every line Aider wrote itself ends in two spaces; the assistant's
  text is written verbatim by `ai_output` (`"\\n" + content.strip() + "\\n\\n"`, io.py:793)
  and carries no such mark. That trailing `"  "` is the discriminator this loader uses: a
  `#### ` or `> ` line WITHOUT it is assistant markdown (a level-4 heading, a blockquote)
  and is counted, never read as a prompt or a tool line.
* `\\n# aider chat started at %Y-%m-%d %H:%M:%S\\n\\n` is written once per `InputOutput`
  construction, i.e. per aider process (io.py:335-336), NAIVE LOCAL TIME, no zone. It is
  the session boundary and the session's only guaranteed clock.
* `.aider.input.history` is prompt_toolkit's `FileHistory` (io.py:355-356 as the
  `PromptSession` history). `store_string` writes `"\\n# " + str(datetime.now()) + "\\n"`
  then `"+" + line + "\\n"` per line of the string (prompt_toolkit 3.0.52 history.py:37-46)
  — microseconds present unless zero, when `str()` omits them. `Buffer.append_to_history`
  (buffer.py:1356-1365) skips EMPTY input and an input IDENTICAL to the previous entry, so
  a prompt typed twice in a row has one stamp. Every `prompt_session.prompt()` call is
  recorded: typed prompts, each physical line of a `{ … }` multiline input, and the
  `y`/`n`/`a`/`d` answers of `confirm_ask` (io.py:876). Without `fancy_input` (dumb
  terminal, `--no-fancy-input`) `input()` is used and NOTHING is stamped (io.py:670).
* THE DOUBLE WRITE. `add_to_input_history` (io.py:736-745) — called for `--message`
  (main.py:1127), for the `/run <command>` Aider records after an LLM-suggested shell
  command (base_coder.py:2474) and by the GUI (gui.py:387) — writes the string through a
  throwaway `FileHistory(self.input_history_file)` AND then through
  `self.prompt_session.history`, a second `FileHistory` on the SAME file, so whenever a
  `PromptSession` exists the entry lands TWICE, back to back. MEASURED on the real
  fixture: 4 of 4 `--message` entries doubled, 46 µs and 82 µs apart
  (`17:42:30.356444` / `.356490`, `17:43:57.904712` / `.904794`). The `Buffer` dedupe
  above never sees these. `_read_inputs` folds consecutive identical entries less than
  `_DOUBLE_WRITE_SEC` apart into the FIRST stamp and counts `double_written_entries`, so
  `entries`, `entries_in_window` and the prompt ↔ stamp match are all one per input.
* THE FIRST `> ` LINE OF A SESSION IS THE COMMAND LINE. main.py:749-751 writes
  `" ".join(sys.argv)` through `tool_output(cmd_line, log_only=True)` — to the history
  only, never the terminal — after `scrub_sensitive_info` (format_settings.py:1-9) has
  replaced the `--openai-api-key` / `--anthropic-api-key` VALUES with `...` + their last
  four characters (a key given any other way, or in the environment, is never on the
  line). It precedes `Aider v…`, repeats the `--message` text and every file path the
  person passed, and is an announcement here (`announcement_command_line`): `--model X`
  (or `--model=X`) is kept as `meta["launch_model"]` and is the model of record when no
  `Model:` line follows (`meta["model_source"]` says which), and the bare flag names are
  kept as `meta["launch_flags"]` — values dropped, so neither the prompt nor the paths
  leave the file. Shortcut flags (`--sonnet`, `--4o`, …) are not resolved. REAL:
  `> /…/bin/aider --model claude-3-5-haiku-20241022 --yes --message say hi --no-git
  --no-check-update --no-show-model-warnings  `.
* THE RAW LITELLM LINE PRECEDES THE FRIENDLY ONE. A failed request reaches
  `check_and_open_urls` (base_coder.py:944-961; the retry path :1479-1486 is the same
  pair): `str(err)` — `litellm.<Class>: …`, litellm 1.81.10 exceptions.py formats every
  class that way, `litellm.Timeout:` included — through `tool_warning` when
  aider/exceptions.py has a description for the class and `tool_error` when it does not,
  THEN the description through `tool_error`, then `offer_url` for every URL in the text
  (`> <url>` subject + `Open URL for more info? … : y`). A multi-line `str(err)` becomes
  one `> ` line per line (`_tool_message`). Here the raw line is the error, exactly once
  per failure (`error_api_request`, class name in `api_error_classes`); its later lines
  are `error_detail_line`; the description that follows is the SAME failure and is folded
  (`api_error_description_folded`), never a second event. `BadRequestError`,
  `NotFoundError`, `APIError`, … have NO description, so before this rule a rejected
  request left `errors: 0`. REAL: `> litellm.BadRequestError: LLM Provider NOT provided.
  …  ` then `> Pass model as E.g. …  `; and `> litellm.AuthenticationError:
  AnthropicException - b'{"type":"error",…"invalid x-api-key"…}'  ` then `> The API
  provider is not able to authenticate you. Check your API key.  `.
* NO `Tokens:` LINE ON FAILURE. `send` (base_coder.py:1811) calls
  `calculate_and_show_tokens_and_cost` only after a completion (or on
  `ContextWindowExceededError`, :1814-1817), and `show_usage_report` (:2102) returns when
  no report was built. A session whose every request failed has NO usage — `usage()`
  reports `tokens_as_printed` and `sum_message_cost` as None, never zero, with `messages`
  0; `wall_seconds` still comes from the stamps that exist (the prompt's), so a one-prompt
  failure has wall 0 and its error events inherit the prompt's clock. `no_timestamp` in
  the diagnostics counts exactly those inherited clocks — Aider stamps nothing it writes
  itself, so every non-prompt event is one.
* Order of a turn (base_coder.py `send` finally-block :1828-1834; `send_message`
  :1548-1620): `#### prompt` → [`> ^C again to exit` if interrupted mid-stream,
  io.py `keyboard_interrupt` :998 called from `send` :1818] → assistant text → `> Tokens: …`
  (`show_usage_report` :2102) → `> Applied edit to <path>` per edited file (:2334) →
  `> Commit <short sha> <message>` (repo.py:313, `auto_commit`) → lint → `> Running <cmd>`
  for each confirmed LLM-suggested command (:2472) → auto-test. Assistant lines are NEVER
  stamped: wall time is bounded by the stamped prompts and `/run` entries, so
  `wall_seconds` is a LOWER bound on the sitting and the last reply's clock is unknown.
* `Tokens: {sent} sent[, {n} cache write][, {n} cache hit], {received} received.` then
  ` Cost: ${m} message, ${s} session.` on the same line, or on its OWN `> ` line when both
  cache figures are present (`sep = "\\n"`, base_coder.py:2023-2067). Numbers come from
  `format_tokens` (utils.py:279): `<1000` exact, `<10000` as `1.2k`, else `12k` — the
  transcript holds ROUNDED counts, so `usage()` is labelled approximate and never summed
  into anything that claims precision. Cost is `format_cost` (:2049): 2 decimals, more for
  sub-cent values. The session figure is a running total that resets with the process.
  Without pricing metadata only the Tokens line is written (:2032).
* Announcements (`get_announcements`, base_coder.py:207-297): `Aider v{version}`,
  `Model: {name} with {edit_format} edit format[, {n} think tokens][, reasoning {x}]
  [, prompt cache][, infinite output]` (`Main model:` when a weak model differs; then
  `Weak model: {name}`; `Editor model: {name} with {fmt} edit format` under architect),
  `Git repo: {dir} with {n:,} files` / `Git repo: none`, `Repo-map: …`, `Added {f} to the
  chat.` / `Added {f} to the chat (read-only).`, `Restored previous conversation history.`,
  `Multiline mode: …`. `/add` writes `Added {f} to the chat` without the period
  (commands.py:902).
* Slash commands are typed input, so they appear as `#### /cmd args` (commands.py `run`
  :311-333; `!` is `/run`, :312). `/ask`, `/code`, `/architect`, `/context` and `/help
  <question>` carry a message to a model and are prompts here; `/run`, `/test` (and `!`)
  execute a shell command (`cmd_run` :1013, `cmd_test` :993) and are shell events;
  `/commit` yields a `Commit …` line (:337-355); everything else (`/add`, `/drop`,
  `/clear`, `/undo`, `/model`, `/tokens`, `/exit`, …) is counted, not an event.
  `/test`'s output is added ONLY on a non-zero exit (`add_on_nonzero_exit`, :1035-1036),
  so `Added {n} lines of output to the chat.` after a `/test` means the tests FAILED — and
  the failure text is then sent to the model as the next message with no `####` line
  (`preproc_user_input` :917 returns the command's result as the message). Shell output
  itself never reaches the history file (`run_cmd` prints live).
* Edit formats (aider/coders/*_coder.py `edit_format`): `diff` / `diff-fenced` /
  `editor-diff` / `editor-diff-fenced` are SEARCH/REPLACE blocks — `HEAD = ^<{5,9}
  SEARCH>?`, `DIVIDER = ^={5,9}`, `UPDATED = ^>{5,9} REPLACE` (editblock_coder.py:386-388)
  with the filename on one of the three lines above (`find_filename` :538); `udiff` /
  `udiff-simple` are ```` ```diff ```` fences with `--- `/`+++ ` headers and hunks
  (udiff_coder.py:346); `patch` is `*** Begin Patch` / `*** Update File: p` / `*** Add
  File: p` / `*** End Patch` (patch_coder.py:115-119); `whole` / `editor-whole` is the
  full file under a fence (wholefile_coder.py:22). The blocks live in the assistant text;
  `Applied edit to <path>` is the only statement that one was written.
* THE PARTIAL-APPLY TRAP. `apply_updates` (base_coder.py:2296-2336) computes `edited`
  BEFORE `apply_edits`; when one SEARCH/REPLACE block fails, `EditBlockCoder.apply_edits`
  has already written the others and raises (`# {n} SEARCH/REPLACE block(s) failed to
  match!` … `## SearchReplaceNoExactMatch: This SEARCH block failed to exactly match lines
  in {path}`, editblock_coder.py:83-97), the `except ValueError` branch prints `The LLM did
  not conform to the edit format.` and RETURNS `edited` without any `Applied edit to`
  line — then `auto_commit` writes `Commit …` for the files that did change. Edit credit
  here comes only from `Applied edit to`, so such files are undercounted and the case is
  counted as `commit_without_applied_edit`; the reflected retry that follows (no `####`)
  usually applies the fixed block and IS credited.
* `tool_error` strings (grep over the package; io.py:988 also counts them): the edit path
  (`The LLM did not conform to the edit format.`, `Exception while updating files:`,
  `Failed to apply edit to {p}`, `Unable to create {p}, skipping edits.`), git (`Unable to
  commit: …`, `Unable to complete {cmd}: …`, `Failed to generate commit message!`, `No git
  repository found.`, `Error running /git command: …`), context (`Model {m} has hit a token
  limit!`, `Your estimated chat context of …`), commands (`Invalid command: …`, `Ambiguous
  command: …`, and `tool_output`'s `Error: Command {c} not found.`), files (`{f}: file not
  found error`, `{f}: is a directory`, `{f}: unable to read: …`, `Unable to read {f}`), and
  the LiteLLM descriptions of aider/exceptions.py printed by `send_message` :1478-1484
  (`The API provider is not able to authenticate you. …`, `… rate limited you. …`, `…
  servers are down or overloaded.`, `… timed out …`, `Permission was denied. …`, `… refused
  the request due to a safety policy …`, `… unable to fetch one or more images.`) with
  `Retrying in {s} seconds...` after a retryable one. In the file an error is
  indistinguishable from any other `> ` line, so ONLY these exact strings become
  `result_error` events; everything else unrecognised is counted under
  `tool_line_unclassified` and its first words surface in the probe.

VERIFIED ON DISK (aider 0.86.2 via pip, 2026-09-05; the files are spec/fixtures/aider/real)
-------------------------------------------------------------------------------------------
Two `--message "say hi"` runs with `ANTHROPIC_API_KEY=invalid-builder-test`, `--yes
--no-git --no-check-update --no-show-model-warnings`, stdin not a TTY, both exit 0, both
appending to the same pair of files — kept verbatim except that argv[0], a container path,
reads `/home/user/aider-venv/bin/aider`; the key reached neither file (it was in the
environment, and `scrub_sensitive_info` only touches `--*-api-key` values anyway):
  * `20260905-174227`, `--model claude-3-5-haiku-20241022` — litellm rejects the bare
    name BEFORE any request: command line, `Aider v0.86.2`, `Model: … with diff edit
    format`, `Git repo: none`, `Repo-map: disabled`, the release-notes URL and its
    confirm, `#### say hi`, `litellm.BadRequestError: LLM Provider NOT provided. …`, its
    second line `Pass model as E.g. …`, the URL `offer_url` pulled out of it and its
    confirm. 16 lines: 1 prompt, 1 error, no Tokens line.
  * `20260905-174355`, `--model anthropic/claude-3-5-haiku-20241022` — the request goes
    out and comes back 401: the same announcements, `#### say hi`,
    `litellm.AuthenticationError: AnthropicException - b'{…"invalid x-api-key"…}'`, `The
    API provider is not able to authenticate you. Check your API key.` 11 lines: 1 prompt,
    1 error, no Tokens line.
  * `.aider.input.history`: FOUR entries for TWO inputs (THE DOUBLE WRITE), read as
    `entries` 2, `double_written_entries` 2, one `prompt_stamped` per session.
Before these rules the probe filed the command line and both raw litellm lines under
`tool_line_unclassified` and reported `errors: 0` for the first run. Not exercised (no
working model): assistant text, `Tokens:` / `Cost:`, `Applied edit to`, `Commit`,
`Running`, `/run`, `^C` — those remain source-verified only.

ASSUMED (not in the current source, or a choice this loader makes; counted in diagnostics)
------------------------------------------------------------------------------------------
* Prompt stamps are matched by TEXT: the nearest input-history entry at or after the
  session header (and after the previous match) whose text equals the prompt; a `{ … }`
  multiline prompt falls back to its first line. An unmatched prompt (consecutive duplicate
  typed at the prompt, no input history, `--no-fancy-input`, an entry from an overlapping
  aider process) sits at the previous stamp and is counted `prompt_without_input_stamp`.
  `--message` prompts ARE stamped (main.py:1127 — twice, see THE DOUBLE WRITE). Header
  and stamps are interpreted in the LOCAL zone of the machine running this code — the
  machine that ran aider, on the daemon side. `_DOUBLE_WRITE_SEC` = 1.0 is a ceiling far
  above the measured 46–82 µs and far below anything a person or a second `Running …` can
  produce; consecutive identical entries cannot come from typing at all (the `Buffer`
  dedupe), so the fold has no legitimate victim.
* Line credit: a SEARCH/REPLACE block scores a real line diff (difflib) of SEARCH → REPLACE,
  a udiff or patch block its `+`/`-` lines, in the assistant text immediately preceding the
  `Applied edit to` line, matched by path (exact, then basename). `whole` blocks are the
  file, not a delta, and get path only; so does any `Applied edit to` with no matching
  block (`edit_credit_path_only`). None of this has been compared against a corpus.
* `^C again to exit` followed by assistant text is a mid-stream interrupt (the partial
  reply is written after it); followed by anything else it is a ^C at the input prompt or
  before the first token, counted `ctrl_c_without_reply`, not an event.
* `Added {n} lines of output to the chat.` right after a `Running …` or `/run` is a
  confirmed paste, not a failure; with no command in the turn it is counted
  `output_added_without_command` (auto-test or auto-lint output) and not judged.
* The `confirm_ask` and `prompt_ask` lines (`… (Y)es/(N)o … : y`) are human presence with
  a stamp, but no `Ev` kind fits; they are counted (`confirm_answer`) and advance nothing.
* Tool names: Aider has no tool protocol, so these are this loader's names — `apply_edit`
  (in `digest.EDIT_TOOLS`), `run` (in `digest.SHELL_TOOLS`; `/run`, `/test`, `!`, `Running`),
  `commit` (in `digest.COMMIT_TOOLS`, added for this harness: Aider commits through
  GitPython, never a shell line, so the `git commit` regex cannot see it), `api_request` and
  `aider` for error sources.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import difflib
import pathlib
import re
from collections import Counter

from . import digest as dg

HARNESS = "aider"

CHAT_FILE = ".aider.chat.history.md"
INPUT_FILE = ".aider.input.history"

EDIT_TOOL = "apply_edit"
SHELL_TOOL = "run"
COMMIT_TOOL = "commit"
API_TOOL = "api_request"
AIDER_TOOL = "aider"

# VERIFIED io.py:336 / prompt_toolkit history.py:305
_HEADER = re.compile(r"^# aider chat started at (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s*$")
_INPUT_STAMP = re.compile(r"^# (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})(?:\.(\d{1,6}))?\s*$")
_PROMPT_PREFIX = "#### "
_BLANK_PROMPT = "<blank>"  # io.py:781

# VERIFIED base_coder.py:207-297 (announcements), :2023-2061 (usage), :2334, :2472,
# repo.py:313, commands.py:902, :1033
_VERSION = re.compile(r"^Aider v(\S+)$")
_MODEL = re.compile(r"^(Main model|Model): (.+?) with (\S+) edit format(?:, (.*))?$")
_WEAK_MODEL = re.compile(r"^Weak model: (.+)$")
_EDITOR_MODEL = re.compile(r"^Editor model: (.+?) with (\S+) edit format$")
_GIT_REPO = re.compile(r"^Git repo: (.+)$")
_ANNOUNCE = ("Repo-map: ", "Restored previous conversation history.", "Multiline mode: ", "See: ")
_FILE_ADDED = re.compile(r"^Added (.+?) to the chat(?: \(read-only\))?\.?$")
_OUTPUT_ADDED = re.compile(r"^Added (\d+) lines? of output to the chat\.$")
_TOKENS = re.compile(
    r"^Tokens: (\S+) sent(?:, (\S+) cache write)?(?:, (\S+) cache hit)?, (\S+) received\."
    r"(?: Cost: \$([0-9.]+) message, \$([0-9.]+) session\.)?$"
)
_COST = re.compile(r"^Cost: \$([0-9.]+) message, \$([0-9.]+) session\.$")
_APPLIED = re.compile(r"^Applied edit to (.+)$")
_DRY_RUN = re.compile(r"^Did not apply edit to (.+) \(--dry-run\)$")
_COMMIT = re.compile(r"^Commit ([0-9a-f]{6,40}) (.*)$")
_RUNNING = re.compile(r"^Running (.+)$")
_CONFIRM = re.compile(r"\(Y\)es/\(N\)o.*\]: ?(\S*)$")
_RETRY = re.compile(r"^Retrying in [0-9.]+ seconds\.\.\.$")
_HELP_LISTING = re.compile(r"^/[a-z][a-z-]*\s{2,}\S")
_CTRL_C_AGAIN = "^C again to exit"  # base_coder.py:998
_CTRL_C_EXIT = "^C KeyboardInterrupt"  # base_coder.py:994
_ERROR_PATH = re.compile(
    r"failed to exactly match lines in (\S+)$|^(\S+) does not contain (?:lines|these)"
)
_SR_FAILED = re.compile(r"^# \d+ SEARCH/REPLACE blocks? failed to match!$")  # editblock:83

# VERIFIED tool_error strings → the tool an error is attributed to. Order matters: the
# specific `Unable to complete commit:` before the generic `Unable to complete `.
_ERROR_PREFIXES: tuple[tuple[str, str], ...] = (
    ("The LLM did not conform to the edit format.", EDIT_TOOL),
    ("Exception while updating files:", EDIT_TOOL),
    ("Failed to apply edit to ", EDIT_TOOL),
    ("Unable to create ", EDIT_TOOL),
    ("Unable to commit:", COMMIT_TOOL),
    ("Unable to complete commit:", COMMIT_TOOL),
    ("Failed to generate commit message!", COMMIT_TOOL),
    ("Error running /git command:", SHELL_TOOL),
    ("Your estimated chat context of ", API_TOOL),
    ("The API provider is not able to authenticate you.", API_TOOL),
    ("The API provider's servers are down or overloaded.", API_TOOL),
    ("The API provider has refused the request due to a safety policy", API_TOOL),
    ("The API provider was unable to fetch one or more images.", API_TOOL),
    ("Permission was denied. Check your API key", API_TOOL),
    ("The API provider has rate limited you.", API_TOOL),
    ("The API provider timed out without returning a response.", API_TOOL),
    # exceptions.py `get_ex_info`: the three descriptions built from the message text
    ("You need to: pip install boto3", API_TOOL),
    ("OpenRouter or the upstream API provider is down", API_TOOL),
    ("Insufficient credits with the API provider.", API_TOOL),
    ("Invalid command: ", AIDER_TOOL),
    ("Ambiguous command: ", AIDER_TOOL),
    ("Error: Command ", AIDER_TOOL),
    ("No git repository found.", AIDER_TOOL),
    ("Unable to complete ", AIDER_TOOL),
    ("Unable to initialize interactive help.", AIDER_TOOL),
    ("Unable to lint ", AIDER_TOOL),
    ("Unable to read ", AIDER_TOOL),
    ("Unable to write file ", AIDER_TOOL),
    ("Unable to add ", AIDER_TOOL),
    ("Unable to diff:", AIDER_TOOL),
    ("This is the first commit in the repository.", AIDER_TOOL),
    ("The last commit was not made by aider in this chat session.", AIDER_TOOL),
    ("Error restoring ", AIDER_TOOL),
    ("No matches found for: ", AIDER_TOOL),
    ("File not found: ", AIDER_TOOL),
    ("Not a file or directory: ", AIDER_TOOL),
    ("Can't initialize prompt toolkit: ", AIDER_TOOL),
    ("Traceback (most recent call last):", AIDER_TOOL),
)
_ERROR_SUFFIXES: tuple[tuple[str, str], ...] = (
    (": file not found error", AIDER_TOOL),
    (": is a directory", AIDER_TOOL),
)
_ERROR_INFIX: tuple[tuple[str, str], ...] = ((": unable to read: ", AIDER_TOOL),)
_TOKEN_LIMIT = re.compile(r"^Model .+ has hit a token limit!$")  # base_coder.py:1653
# VERIFIED main.py:749-751 — `" ".join(sys.argv)`, so argv[0] is the console script
# (`…/bin/aider`, `…\Scripts\aider.exe`) or `…/aider/__main__.py` under `python -m aider`.
# REAL: `/home/user/aider-venv/bin/aider --model claude-3-5-haiku-20241022 --yes --message
# say hi --no-git --no-check-update --no-show-model-warnings`.
_CMD_LINE = re.compile(r"^(?:\S*[\\/])?aider(?:\.exe|[\\/]__main__\.py)?(?:\s+(.*))?$")
_LAUNCH_MODEL = "--model"
# VERIFIED litellm 1.81.10 exceptions.py: every class formats `litellm.<Class>: <message>`
# (`litellm.Timeout:` has no `Error` suffix); base_coder.py:944-961 writes `str(err)` first.
_LITELLM = re.compile(r"^litellm\.([A-Za-z]\w*): ")
# MEASURED spec/fixtures/aider/real: the two writes of one `add_to_input_history` call landed
# 46 µs and 82 µs apart. One second is a ceiling, not a fit (see THE DOUBLE WRITE).
_DOUBLE_WRITE_SEC = 1.0

# VERIFIED commands.py: which slash commands carry a message to a model
_MESSAGE_COMMANDS = frozenset({"/ask", "/code", "/architect", "/context", "/help"})
_SHELL_COMMANDS = frozenset({"/run", "/test", "/git"})

# VERIFIED editblock_coder.py:386-388, udiff_coder.py:346, patch_coder.py:115-119
_SR_HEAD = re.compile(r"^<{5,9} SEARCH>?\s*$")
_SR_DIVIDER = re.compile(r"^={5,9}\s*$")
_SR_UPDATED = re.compile(r"^>{5,9} REPLACE\s*$")
_FENCE = "```"
_PATCH_BEGIN = "*** Begin Patch"
_PATCH_END = "*** End Patch"
_PATCH_FILE = re.compile(r"^\*\*\* (?:Update|Add) File: (.+)$")


# ----------------------------------------------------------------------------- files


@dataclasses.dataclass
class Session:
    id: str
    ordinal: int  # 1-based position in the file
    started_at: str  # as written, naive local
    start_ts: float  # local epoch seconds
    line_start: int  # header line index
    line_end: int  # exclusive


@dataclasses.dataclass
class Scan:
    """Everything one read of a session yields. `load_events`, `meta`, `usage` and
    `diagnostics` are views on this; the probe prints all of it."""

    path: pathlib.Path
    chat_file: pathlib.Path
    input_file: pathlib.Path | None
    session: Session | None
    sessions: list[Session]
    items: list[tuple[str, str]]  # ("prompt" | "tool" | "text", payload) in file order
    inputs: list[tuple[float, str, str]]  # (ts, stamp as written, text) in the session window
    meta: dict
    usage: dict
    diagnostics: dict


def _local_ts(stamp: str, micros: str | None = None) -> float | None:
    """Naive `YYYY-MM-DD HH:MM:SS[.ffffff]` → epoch seconds in THIS machine's zone."""
    try:
        d = dt.datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S")  # noqa: DTZ007 — naive by design
    except ValueError:
        return None
    ts = d.timestamp()
    if micros:
        ts += int(micros.ljust(6, "0")) / 1_000_000
    return ts


def _read_lines(path: pathlib.Path) -> tuple[list[str], bool]:
    """(lines without their line ending, partial trailing line seen). Aider appends while
    a session is live, so a last line without a newline is never consumed."""
    data = path.read_bytes()
    partial = bool(data) and not data.endswith(b"\n")
    raw = data.split(b"\n")[:-1]  # after a trailing newline the last piece is empty
    return [ln.decode("utf-8", errors="replace").rstrip("\r") for ln in raw], partial


def _is_chat_file(path: pathlib.Path) -> bool:
    if not path.is_file():
        return False
    if path.name == CHAT_FILE:
        return True
    if path.suffix != ".md":
        return False
    try:
        with path.open("rb") as f:
            head = f.read(4096)
    except OSError:
        return False
    for ln in head.split(b"\n"):
        if ln.strip():
            return bool(_HEADER.match(ln.decode("utf-8", errors="replace").rstrip("\r")))
    return False


def detect(path: pathlib.Path) -> str | None:
    """ "repo_dir", "chat_file", "input_file", "session" or None — decided on the path
    shape first (a `.md` is opened only to read its first line)."""
    path = pathlib.Path(path)
    if path.is_dir():
        return "repo_dir" if (path / CHAT_FILE).is_file() else None
    if path.name == INPUT_FILE and path.is_file():
        return "input_file" if (path.parent / CHAT_FILE).is_file() else None
    if _is_chat_file(path):
        return "chat_file"
    if not path.exists() and _is_chat_file(path.parent):
        return "session"
    return None


def resolve(path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path | None, str | None]:
    """(chat file, input file or None, session id or None) for any accepted path shape."""
    path = pathlib.Path(path)
    kind = detect(path)
    if kind == "repo_dir":
        chat, sid = path / CHAT_FILE, None
    elif kind == "input_file":
        chat, sid = path.parent / CHAT_FILE, None
    elif kind == "session":
        chat, sid = path.parent, path.name
    else:
        chat, sid = path, None
    inp = chat.parent / INPUT_FILE
    return chat, (inp if inp.is_file() else None), sid


def _sessions_from_lines(lines: list[str]) -> tuple[list[Session], int]:
    """Split on the header. Returns (sessions, lines before the first header)."""
    heads = [(i, m.group(1)) for i, ln in enumerate(lines) if (m := _HEADER.match(ln))]
    sessions: list[Session] = []
    seen: Counter = Counter()
    for k, (i, stamp) in enumerate(heads):
        end = heads[k + 1][0] if k + 1 < len(heads) else len(lines)
        base = stamp.replace("-", "").replace(":", "").replace(" ", "-")
        seen[base] += 1
        sid = base if seen[base] == 1 else f"{base}-{seen[base]}"
        sessions.append(Session(sid, k + 1, stamp, _local_ts(stamp) or 0.0, i, end))
    return sessions, (heads[0][0] if heads else len(lines))


def list_sessions(path: pathlib.Path) -> list[Session]:
    """Every session in the chat file (or the repo / input file next to it), in order."""
    chat, _, _ = resolve(path)
    if not chat.is_file():
        return []
    return _sessions_from_lines(_read_lines(chat)[0])[0]


def _read_inputs(path: pathlib.Path | None) -> tuple[list[tuple[float, str, str]], dict]:
    d = {
        "present": path is not None,
        "entries": 0,  # inputs, after the fold below
        "entries_in_window": 0,
        "double_written_entries": 0,  # second copies folded away (io.py:740-743)
        "malformed_lines": 0,
        "bad_timestamp": 0,
        "partial_trailing_line": False,
    }
    if path is None:
        return [], d
    lines, d["partial_trailing_line"] = _read_lines(path)
    out: list[tuple[float, str, str]] = []
    cur: list[str] | None = None
    cur_ts: float | None = None
    cur_stamp = ""

    def flush() -> None:  # FileHistory.load_history_strings: a stamp with no `+` lines is nothing
        if cur and cur_ts is not None:
            text = "\n".join(cur)
            # THE DOUBLE WRITE: `add_to_input_history` (io.py:736-745) stores the string
            # through two `FileHistory` objects on the same file. REAL:
            #   # 2026-09-05 17:42:30.356444 / +say hi
            #   # 2026-09-05 17:42:30.356490 / +say hi
            # One input; the first stamp is when it was accepted.
            if out and out[-1][2] == text and 0 <= cur_ts - out[-1][0] < _DOUBLE_WRITE_SEC:
                d["double_written_entries"] += 1
                return
            out.append((cur_ts, cur_stamp, text))

    for ln in lines:
        m = _INPUT_STAMP.match(ln)
        if m:
            flush()
            cur_stamp = ln[2:].strip()
            cur_ts = _local_ts(m.group(1), m.group(2))
            cur = [] if cur_ts is not None else None
            if cur_ts is None:
                d["bad_timestamp"] += 1
        elif ln.startswith("+") and cur is not None:
            cur.append(ln[1:])
        else:
            # any other line ends the entry (prompt_toolkit/history.py:287-291); a `+` line
            # with no stamp before it, or a non-blank stray line, is malformed
            flush()
            cur = None
            if ln.strip():
                d["malformed_lines"] += 1
    flush()
    d["entries"] = len(out)
    return out, d


def _launch_flags(args: str) -> tuple[list[str], str | None]:
    """(flag names in order, `--model` value) from the logged command line. Values are
    dropped on purpose: the `--message` text and every file path the person passed are
    on that line, and neither belongs in meta. `--model=X` and `--model X` both count;
    the shortcut flags (`--sonnet`, `--4o`, …) are kept as flags and not resolved."""
    flags: list[str] = []
    model: str | None = None
    toks = args.split()
    for i, tok in enumerate(toks):
        if not tok.startswith("-") or tok == "-" or tok.lstrip("-").replace(".", "").isdigit():
            continue
        name, eq, val = tok.partition("=")
        flags.append(name)
        if name == _LAUNCH_MODEL:
            nxt = toks[i + 1] if i + 1 < len(toks) else None
            model = val if eq else (nxt if nxt and not nxt.startswith("-") else None)
    return flags, model or None


# ----------------------------------------------------------------------------- lines


def _classify(line: str) -> tuple[str, str]:
    """("prompt" | "tool" | "text", payload). Aider's own lines end in two spaces."""
    if line.startswith(_PROMPT_PREFIX) or line == "####":
        if line.endswith("  "):
            return "prompt", line[len(_PROMPT_PREFIX) :].rstrip()
        return "text", line
    if line.startswith("> ") or line == ">":
        if line.endswith("  "):
            return "tool", line[1:].strip()
        return "text", line
    return "text", line


def _is_aider_line(line: str) -> bool:
    """A line Aider wrote whole: header, or a prefixed line carrying the two-space suffix."""
    return bool(_HEADER.match(line)) or (
        line.endswith("  ")
        and (line.startswith(("> ", _PROMPT_PREFIX)) or line in (">  ", "####  "))
    )


def _tool_block_end(lines: list[str], i: int) -> int | None:
    """`tool_output` with an embedded newline writes ONE `> ` prefix on the first line and
    ONE two-space suffix on the last (`append_chat_history`, io.py:1117-1126) — the
    SEARCH/REPLACE failure body, `/git` output, a multi-command confirm subject, the
    `/model` re-announcement. From a `> ` line WITHOUT the suffix at `i`, return the index
    of that closing line: the first later line that ends in two spaces and is NOT itself a
    whole Aider line. Reaching a whole Aider line (or EOF) first means the `> ` was the
    assistant's own blockquote — None."""
    for j in range(i + 1, len(lines)):
        ln = lines[j]
        if _is_aider_line(ln):
            return None
        if ln.endswith("  "):
            return j
    return None


def _items(lines: list[str], diag: dict) -> list[tuple[str, str]]:
    """Lines → items: adjacent prompt lines are one prompt, adjacent text lines one
    assistant block (blank-trimmed, blank blocks dropped), tool lines one each, and a
    multi-line `tool_output` one `tool` item followed by a `toolcont` item per extra line."""
    items: list[tuple[str, str]] = []
    kinds: Counter = Counter()
    buf_kind: str | None = None
    buf: list[str] = []

    def flush() -> None:
        nonlocal buf_kind, buf
        if buf_kind == "prompt":
            items.append(("prompt", "\n".join(buf).strip()))
        elif buf_kind == "text":
            text = "\n".join(buf).strip("\n")
            if text.strip():
                items.append(("text", text))
        buf_kind, buf = None, []

    i = 0
    while i < len(lines):
        ln = lines[i]
        if _HEADER.match(ln):
            flush()
            i += 1
            continue
        kind, payload = _classify(ln)
        if kind == "text" and ln.startswith("> "):
            end = _tool_block_end(lines, i)
            if end is not None:
                flush()
                diag["tool_blocks_multiline"] += 1
                kinds["tool"] += end - i + 1
                items.append(("tool", payload[1:].strip()))
                items.extend(("toolcont", lines[k].strip()) for k in range(i + 1, end + 1))
                i = end + 1
                continue
            diag["looks_like_tool_no_suffix"] += 1
        elif kind == "text" and ln.startswith(_PROMPT_PREFIX):
            diag["looks_like_prompt_no_suffix"] += 1
        kinds[kind] += 1
        i += 1
        if kind == "tool":
            flush()
            items.append(("tool", payload))
            continue
        if buf_kind != kind:
            flush()
            buf_kind = kind
        buf.append(payload)
    flush()
    diag["line_kinds"] = {k: kinds[k] for k in ("prompt", "tool", "text")}
    return items


# ----------------------------------------------------------------------------- scan


def scan(path: pathlib.Path) -> Scan:
    """One read of the chat file and the input history, selecting one session. Never
    raises on content: a missing input history, a file without a header, an unknown
    session id are counted, not thrown."""
    path = pathlib.Path(path)
    chat, inp, sid = resolve(path)
    diag: dict = {
        "container": "aider_chat_history",
        "files_present": [],
        "lines": 0,
        "records": 0,
        "malformed_lines": 0,
        "partial_trailing_line": False,
        "looks_like_prompt_no_suffix": 0,
        "looks_like_tool_no_suffix": 0,
        "tool_blocks_multiline": 0,
    }
    all_lines: list[str] = []
    if chat.is_file():
        diag["files_present"].append("chat")
        try:
            all_lines, diag["partial_trailing_line"] = _read_lines(chat)
        except OSError:
            diag["malformed_lines"] += 1
    if inp is not None:
        diag["files_present"].append("input")
    inputs, in_diag = _read_inputs(inp)
    diag["input_history"] = in_diag
    diag["malformed_lines"] += in_diag["malformed_lines"]
    diag["bad_timestamp"] = in_diag["bad_timestamp"]

    sessions, preamble = _sessions_from_lines(all_lines)
    diag["sessions_in_file"] = len(sessions)
    diag["preamble_lines"] = preamble
    session: Session | None = None
    selected_by = None
    if sid is not None:
        session = next((s for s in sessions if s.id == sid), None)
        selected_by = "path" if session else None
        if session is None:
            diag["session_not_found"] = sid
    elif sessions:
        session, selected_by = sessions[-1], "latest"

    lines = all_lines[session.line_start : session.line_end] if session else []
    items = _items(lines, diag)
    diag["lines"] = len(lines)
    diag["records"] = len(items)
    diag["types"] = dict(Counter(k for k, _ in items))
    diag["first_ts"] = session.started_at if session else None

    # the input window this session may draw stamps from: [header, next header)
    lo = session.start_ts if session else None
    nxt = (
        sessions[session.ordinal].start_ts if session and session.ordinal < len(sessions) else None
    )
    window = [e for e in inputs if lo is not None and e[0] >= lo and (nxt is None or e[0] < nxt)]
    in_diag["entries_in_window"] = len(window)

    meta = {
        "harness": HARNESS,
        "path": str(chat),
        "session_id": session.id if session else None,
        "session_ordinal": session.ordinal if session else None,
        "sessions_in_file": len(sessions),
        "session_selected_by": selected_by,
        "started_at": session.started_at if session else None,
        "started_at_zone": "local, as written (Aider stamps no zone)",
        "input_history_present": inp is not None,
    }
    s = Scan(path, chat, inp, session, sessions, items, window, meta, {}, diag)
    _derive(s)  # fills meta (announcements), usage, and the derived diagnostics
    return s


def meta(path: pathlib.Path) -> dict:
    """session id / start / model / edit format / version for one session."""
    return scan(path).meta


def usage(path: pathlib.Path) -> dict:
    """The Tokens lines summed AS PRINTED (Aider rounds them), message costs summed next
    to the last running session total. When they disagree the disagreement is the finding."""
    return scan(path).usage


def diagnostics(path: pathlib.Path) -> dict:
    """Files present, sessions in the file, line kinds, input-history coverage, plus the
    event-derivation counters (`derivation`)."""
    s = scan(path)
    return dict(s.diagnostics, derivation=_derive(s)[1])


# ----------------------------------------------------------------------------- credit


def _line_delta(old: list[str], new: list[str]) -> tuple[int, int]:
    added = removed = 0
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=old, b=new, autojunk=False).get_opcodes():
        if tag in ("replace", "insert"):
            added += j2 - j1
        if tag in ("replace", "delete"):
            removed += i2 - i1
    return added, removed


def _filename_before(lines: list[str], i: int) -> str | None:
    """`find_filename` (editblock_coder.py:538): one of the three lines above HEAD that is
    not a fence, cleaned of `*`, backticks, `#` and a trailing colon."""
    for j in range(i - 1, max(i - 4, -1), -1):
        cand = lines[j].strip()
        if not cand or cand.startswith(_FENCE):
            continue
        cand = cand.rstrip(":").lstrip("#").strip().strip("`").strip("*").strip()
        if cand and " " not in cand:
            return cand
    return None


def _edit_blocks(text: str, counters: Counter) -> dict[str, list[int]]:
    """{path: [added, removed]} for every edit block in one assistant reply."""
    lines = text.split("\n")
    n = len(lines)
    blocks: dict[str, list[int]] = {}

    def add(path: str | None, a: int, r: int, kind: str) -> None:
        if not path:
            counters["edit_block_without_filename"] += 1
            return
        counters[f"edit_block_{kind}"] += 1
        p = blocks.setdefault(path, [0, 0])
        p[0] += a
        p[1] += r

    i = 0
    while i < n:
        line = lines[i]
        if _SR_HEAD.match(line):
            path = _filename_before(lines, i)
            j = i + 1
            orig: list[str] = []
            while j < n and not _SR_DIVIDER.match(lines[j]):
                orig.append(lines[j])
                j += 1
            j += 1
            upd: list[str] = []
            while j < n and not _SR_UPDATED.match(lines[j]):
                upd.append(lines[j])
                j += 1
            if j >= n:
                counters["edit_block_malformed"] += 1
                break
            a, r = _line_delta(orig, upd)
            add(path, a, r, "search_replace")
            i = j + 1
            continue
        if (
            line.startswith(_FENCE)
            and i + 2 < n
            and lines[i + 1].startswith("--- ")
            and lines[i + 2].startswith("+++ ")
        ):
            j, path, a, r = i + 1, None, 0, 0
            while j < n and not lines[j].startswith(_FENCE):
                ln = lines[j]
                if ln.startswith("+++ "):
                    if path:
                        add(path, a, r, "udiff")
                    path, a, r = ln[4:].strip(), 0, 0
                elif ln.startswith("--- "):
                    pass
                elif ln.startswith("+"):
                    a += 1
                elif ln.startswith("-"):
                    r += 1
                j += 1
            add(path, a, r, "udiff")
            i = j + 1
            continue
        if line.strip() == _PATCH_BEGIN:
            j, path, a, r = i + 1, None, 0, 0
            while j < n and lines[j].strip() != _PATCH_END:
                ln = lines[j]
                m = _PATCH_FILE.match(ln.strip())
                if m:
                    if path:
                        add(path, a, r, "patch")
                    path, a, r = m.group(1).strip(), 0, 0
                elif ln.startswith("***"):
                    pass
                elif ln.startswith("+"):
                    a += 1
                elif ln.startswith("-"):
                    r += 1
                j += 1
            add(path, a, r, "patch")
            i = j + 1
            continue
        i += 1
    return blocks


def _credit_for(path: str, blocks: dict[str, list[int]]) -> tuple[int, int] | None:
    if path in blocks:
        return blocks[path][0], blocks[path][1]
    base = pathlib.PurePosixPath(path).name
    hits = [v for k, v in blocks.items() if pathlib.PurePosixPath(k).name == base]
    if len(hits) == 1:
        return hits[0][0], hits[0][1]
    return None


def _k(s: str) -> int | None:
    """`format_tokens` (utils.py:279) back to an integer — ROUNDED, as printed."""
    try:
        if s.endswith("k"):
            return round(float(s[:-1]) * 1000)
        return int(s)
    except ValueError:
        return None


def _error_tool(text: str) -> str | None:
    for prefix, tool in _ERROR_PREFIXES:
        if text.startswith(prefix):
            return tool
    for suffix, tool in _ERROR_SUFFIXES:
        if text.endswith(suffix):
            return tool
    for infix, tool in _ERROR_INFIX:
        if infix in text:
            return tool
    if _TOKEN_LIMIT.match(text):
        return API_TOOL
    return None


# ----------------------------------------------------------------------------- derive


def _derive(s: Scan, start: float | None = None, end: float | None = None):
    """Turn one session's items into `Ev`s. Returns (events, derivation counters).

    Idempotent: `scan` runs it once to fill meta / usage, `load_events` and the probe run
    it again; usage is rebuilt from scratch each time, never accumulated."""
    counters: Counter = Counter()
    out: list[dg.Ev] = []
    tokens = {"sent": 0, "cache_write": 0, "cache_hit": 0, "received": 0}
    usage = {
        "messages": 0,
        "messages_with_cost": 0,
        "tokens_as_printed": tokens,
        "approximate": True,  # format_tokens rounds to 0.1k / 1k
        "sum_message_cost": 0.0,
        "last_session_cost": None,
        "session_cost_matches_sum": None,
    }
    s.usage.clear()
    s.usage.update(usage)
    s.meta.update(
        {
            "cli_version": None,
            "model": None,
            "model_source": None,  # "announcement" (`Model:` line) | "command_line" | None
            "launch_model": None,  # `--model` on the logged command line
            "launch_flags": None,  # the command line's flag names, values dropped
            "edit_format": None,
            "models_seen": None,
            "weak_model": None,
            "editor_model": None,
            "git_repo": None,
            "files_in_chat": 0,
        }
    )
    s.diagnostics.update(
        {"no_timestamp": 0, "last_ts": None, "unknown_types": {}, "api_error_classes": {}}
    )
    if s.session is None:
        counters["no_session"] += 1
        return out, dict(sorted(counters.items()))

    items = s.items
    inputs = s.inputs
    cursor = 0
    cur_ts = s.session.start_ts
    last_stamp: str | None = None
    model: str | None = None
    launch_model: str | None = None
    models_seen: Counter = Counter()
    api_classes: Counter = Counter()
    files_in_chat: set[str] = set()
    unknown: Counter = Counter()
    cost_sum = 0.0
    last_session_cost = None
    last_assistant: dg.Ev | None = None
    blocks: dict[str, list[int]] = {}
    applied_since_prompt = False
    last_command: str | None = None  # the last typed slash command in this turn
    last_shell: str | None = None  # "running" | "/run" | "/test" | "/git" | None
    error_body = False  # inside the detail lines of an edit-format error
    error_ev: dg.Ev | None = None
    inherited = 0  # emitted events whose clock is the previous stamp, not their own

    def _in_window(ts: float) -> bool:
        return (start is None or ts >= start) and (end is None or ts <= end)

    def _emit(ev: dg.Ev, stamped: bool = False) -> None:
        nonlocal inherited
        if _in_window(ev.ts):
            out.append(ev)
            inherited += 0 if stamped else 1

    def _stamp(text: str, kind: str) -> tuple[float, bool]:
        """Nearest input-history entry at/after the cursor whose text is `text` (or, for
        a `{ … }` multiline input, its first line). Advances the cursor on a match."""
        nonlocal cursor, cur_ts, last_stamp
        want = text.strip()
        first = next((ln for ln in want.split("\n") if ln.strip()), want).strip()
        for probe in (want, first):
            if not probe:
                continue
            for k in range(cursor, len(inputs)):
                if inputs[k][2].strip() == probe:
                    cursor = k + 1
                    cur_ts, last_stamp = inputs[k][0], inputs[k][1]
                    suffix = "_by_first_line" if probe != want else ""
                    counters[f"{kind}_stamped{suffix}"] += 1
                    return cur_ts, True
        counters[f"{kind}_without_input_stamp"] += 1
        return cur_ts, False

    def _shell(ts: float, command: str, source: str) -> dg.Ev:
        nonlocal last_shell
        path, approx = dg._bash_file_effect(command)
        ev = dg.Ev(0, ts, "tool", "", tool=SHELL_TOOL, path=path, model=model)
        if approx is not None:
            ev.added, ev.removed = approx, 0
        ev.text = dg.mask(dg._trunc(command.replace("\n", " ⏎ "), dg.COMMAND_MAX))
        last_shell = source
        return ev

    def _error(text: str, tool: str, path: str | None = None) -> dg.Ev:
        return dg.Ev(
            0,
            cur_ts,
            "result_error",
            dg.mask(dg._trunc(text, dg.ERROR_MAX)),
            tool=tool,
            path=path,
            ok=False,
        )

    def _announce(text: str) -> bool:
        """Announcement lines (`get_announcements`, `/add`): meta only, never an event."""
        nonlocal model
        m = _VERSION.match(text)
        if m:
            s.meta["cli_version"] = s.meta["cli_version"] or m.group(1)
            counters["announcement"] += 1
            return True
        m = _MODEL.match(text)
        if m:
            model = m.group(2).strip()
            models_seen[model] += 1
            s.meta["model"], s.meta["edit_format"] = model, m.group(3)
            s.meta["model_source"] = "announcement"
            counters["announcement_model"] += 1
            return True
        for rx, key in ((_WEAK_MODEL, "weak_model"), (_EDITOR_MODEL, "editor_model")):
            m = rx.match(text)
            if m:
                s.meta[key] = m.group(1).strip()
                counters["announcement"] += 1
                return True
        m = _GIT_REPO.match(text)
        if m:
            s.meta["git_repo"] = m.group(1).strip()
            counters["announcement"] += 1
            return True
        if text.startswith(_ANNOUNCE):
            counters["announcement"] += 1
            return True
        m = _FILE_ADDED.match(text)
        if m:
            files_in_chat.add(m.group(1))
            counters["file_added"] += 1
            return True
        return False

    for idx, (kind, text) in enumerate(items):
        if kind == "prompt":
            error_body, error_ev = False, None
            applied_since_prompt = False
            last_command, last_shell = None, None
            blocks = {}
            if text == _BLANK_PROMPT or not text:
                counters["prompt_blank"] += 1
                continue
            word = text.split()[0]
            rest = text[len(word) :].strip() if not text.startswith("!") else text[1:].strip()
            if text.startswith("!") or word in _SHELL_COMMANDS:
                source = "/run" if text.startswith("!") else word
                command = ("git " + rest) if word == "/git" else (rest or word)
                ts, stamped = _stamp(text, "command")
                counters[f"command_{source.lstrip('/')}"] += 1
                last_command = source
                _emit(_shell(ts, command, source), stamped)
                continue
            if text.startswith("/") and (word not in _MESSAGE_COMMANDS or not rest):
                _stamp(text, "command")
                counters[f"command_{word.lstrip('/')}"] += 1
                last_command = word
                continue
            ts, stamped = _stamp(text, "prompt")
            counters["prompt"] += 1
            _emit(dg.Ev(0, ts, "prompt", dg.mask(dg._trunc(text, dg.PROMPT_MAX))), stamped)
            continue

        if kind == "text":
            error_body, error_ev = False, None
            counters["assistant"] += 1
            ev = dg.Ev(0, cur_ts, "assistant", dg.mask(dg._trunc(text, dg.ASSISTANT_MAX)))
            ev.model = model
            _emit(ev)
            last_assistant = ev
            blocks = _edit_blocks(text, counters)
            continue

        if kind == "toolcont":
            # a later line of one multi-line `tool_output`: detail of an error, an
            # announcement re-shown by `/model`, or output (`/git`, a confirm subject)
            counters["tool_block_continuation_line"] += 1
            if error_body:
                counters["error_detail_line"] += 1
                pm = _ERROR_PATH.search(text)
                if pm and error_ev is not None and error_ev.path is None:
                    error_ev.path = pm.group(1) or pm.group(2)
            elif text and not _announce(text):
                counters["tool_block_continuation_unclassified"] += 1
            continue

        # a `> ` line Aider wrote about itself
        m = _CMD_LINE.match(text)
        if m:
            # VERIFIED main.py:749-751 (`tool_output(cmd_line, log_only=True)`). REAL:
            # `> /home/user/aider-venv/bin/aider --model claude-3-5-haiku-20241022 --yes
            #  --message say hi --no-git --no-check-update --no-show-model-warnings  `
            # An announcement — never an error, never a tool — and the model of last resort.
            counters["announcement_command_line"] += 1
            flags, launch = _launch_flags(m.group(1) or "")
            s.meta["launch_flags"] = flags
            if launch:
                launch_model = launch
                s.meta["launch_model"] = launch
                if model is None:
                    model = launch  # until (unless) a `Model:` line says otherwise
            continue
        m = _TOKENS.match(text)
        if m:
            error_body = False
            usage["messages"] += 1
            for key, grp in (("sent", 1), ("cache_write", 2), ("cache_hit", 3), ("received", 4)):
                v = _k(m.group(grp)) if m.group(grp) else 0
                if v is None:
                    counters["usage_unparsed_figure"] += 1
                    v = 0
                tokens[key] += v
            if last_assistant is not None and last_assistant.tok_out is None:
                last_assistant.tok_out = _k(m.group(4))
            if m.group(5):
                usage["messages_with_cost"] += 1
                cost_sum += float(m.group(5))
                last_session_cost = float(m.group(6))
            counters["usage_line"] += 1
            continue
        m = _COST.match(text)
        if m:
            usage["messages_with_cost"] += 1
            cost_sum += float(m.group(1))
            last_session_cost = float(m.group(2))
            counters["cost_line"] += 1
            continue
        m = _APPLIED.match(text)
        if m:
            error_body = False
            path = m.group(1).strip()
            ev = dg.Ev(0, cur_ts, "tool", dg.mask(path), tool=EDIT_TOOL, path=path, model=model)
            credit = _credit_for(path, blocks)
            if credit is not None:
                ev.added, ev.removed = credit
                counters["edit_credit_from_block"] += 1
            else:
                counters["edit_credit_path_only"] += 1
            applied_since_prompt = True
            _emit(ev)
            continue
        if _DRY_RUN.match(text):
            counters["edit_dry_run"] += 1
            continue
        m = _COMMIT.match(text)
        if m:
            error_body = False
            if not applied_since_prompt and last_command != "/commit":
                counters["commit_without_applied_edit"] += 1
            counters["commit"] += 1
            ev = dg.Ev(0, cur_ts, "tool", dg.mask(dg._trunc(m.group(2), dg.COMMAND_MAX)))
            ev.tool, ev.tool_id, ev.model = COMMIT_TOOL, m.group(1), model
            _emit(ev)
            continue
        m = _RUNNING.match(text)
        if m:
            error_body = False
            command = m.group(1).strip()
            ts, stamped = _stamp("/run " + command, "running")
            counters["running"] += 1
            _emit(_shell(ts, command, "running"), stamped)
            continue
        m = _OUTPUT_ADDED.match(text)
        if m:
            if last_shell == "/test":
                counters["test_output_added_nonzero_exit"] += 1
                msg = (
                    f"test command exited non-zero; {m.group(1)} lines of output added to the chat"
                )
                _emit(_error(msg, SHELL_TOOL))
            elif last_shell in ("running", "/run", "/git"):
                counters["command_output_added"] += 1
            else:
                counters["output_added_without_command"] += 1
            continue
        if text == _CTRL_C_AGAIN:
            if idx + 1 < len(items) and items[idx + 1][0] == "text":
                counters["interrupt"] += 1
                _emit(dg.Ev(0, cur_ts, "interrupt", ""))
            else:
                counters["ctrl_c_without_reply"] += 1
            continue
        if text == _CTRL_C_EXIT:
            counters["ctrl_c_exit"] += 1
            continue
        if _SR_FAILED.match(text):
            # the body of the edit-format error; the `did not conform` line precedes it
            counters["error_detail_line"] += 1
            error_body = True
            continue
        m = _LITELLM.match(text)
        if m:
            # VERIFIED base_coder.py:944-961 / :1479-1486: `str(err)` comes first. REAL:
            #   `> litellm.BadRequestError: LLM Provider NOT provided. Pass in the LLM
            #    provider you are trying to call. You passed model=claude-3-5-haiku-20241022  `
            #   `> litellm.AuthenticationError: AnthropicException - b'{"type":"error",
            #    "error":{"type":"authentication_error","message":"invalid x-api-key"},…}'  `
            # The first has NO aider/exceptions.py description (BadRequestError → None), so
            # nothing after it matched `_ERROR_PREFIXES` and the failed turn scored
            # `errors: 0`. The raw line is the error, once; what follows it is detail.
            counters[f"error_{API_TOOL}"] += 1
            api_classes[m.group(1)] += 1
            ev = _error(text, API_TOOL)
            _emit(ev)
            error_body, error_ev = True, ev
            continue
        tool = _error_tool(text)
        if tool is not None:
            if (
                tool == API_TOOL
                and error_body
                and error_ev is not None
                and error_ev.tool == API_TOOL
            ):
                # REAL: `> The API provider is not able to authenticate you. Check your API
                # key.  ` right after the `litellm.AuthenticationError:` line — the same
                # failure, described; one event, not two.
                counters["api_error_description_folded"] += 1
                continue
            counters[f"error_{tool}"] += 1
            ev = _error(text, tool)
            _emit(ev)
            error_body, error_ev = tool == EDIT_TOOL, ev
            continue
        if _RETRY.match(text):
            counters["api_retry"] += 1
            error_body, error_ev = False, None  # the next attempt's raw line is a new failure
            continue
        if _announce(text):
            continue
        if _CONFIRM.search(text):
            counters["confirm_answer"] += 1
            continue
        if error_body:
            counters["error_detail_line"] += 1
            pm = _ERROR_PATH.search(text)
            if pm and error_ev is not None and error_ev.path is None:
                error_ev.path = pm.group(1) or pm.group(2)
            continue
        if _HELP_LISTING.match(text) or text.startswith("Use `/help <question>`"):
            counters["help_listing_line"] += 1
            continue
        if text.startswith("You can use /undo"):
            counters["undo_hint"] += 1
            continue
        if text.startswith(("Warning: ", "Skipping ", "Dropping ")):
            counters["warning_line"] += 1
            continue
        if text.startswith(("http://", "https://")):
            counters["url_line"] += 1
            continue
        if not text:
            counters["tool_line_empty"] += 1
            continue
        counters["tool_line_unclassified"] += 1
        unknown[" ".join(text.split()[:2])] += 1

    if usage["messages"] == 0:
        # NO `Tokens:` LINE ON FAILURE (base_coder.py:1811, :2102): absent, not zero. REAL:
        # both failed runs wrote none.
        usage["tokens_as_printed"] = None
    usage["sum_message_cost"] = round(cost_sum, 6) if usage["messages_with_cost"] else None
    usage["last_session_cost"] = last_session_cost
    if last_session_cost is not None:
        n = max(1, usage["messages_with_cost"])
        usage["session_cost_matches_sum"] = abs(last_session_cost - cost_sum) <= 0.005 * n + 0.005
    s.usage.clear()
    s.usage.update(usage)
    s.meta["models_seen"] = dict(models_seen.most_common()) or None
    if s.meta["model"] is None and launch_model is not None:
        s.meta["model"], s.meta["model_source"] = launch_model, "command_line"
    s.meta["files_in_chat"] = len(files_in_chat)
    s.diagnostics["api_error_classes"] = dict(api_classes.most_common())
    s.diagnostics["no_timestamp"] = inherited
    s.diagnostics["last_ts"] = last_stamp
    s.diagnostics["unknown_types"] = dict(unknown.most_common(10))

    for i, e in enumerate(out):
        e.n = i
    return out, dict(sorted(counters.items()))


def load_events(
    path: pathlib.Path, start: float | None = None, end: float | None = None
) -> list[dg.Ev]:
    """Read one session (a `<chat file>/<id>` path, or the latest session of a repo
    directory / chat file / input file) into digest events, in file order — which is
    time order as far as the stamps reach — within [start, end]."""
    return _derive(scan(path), start, end)[0]
