"""Turn a Claude Code transcript into a digest small enough to analyse and honest enough
to trust.

The digest is the ONLY thing the analysis model sees. Three rules shape it:

1. Every human prompt is included, verbatim (bounded per prompt). Prompts are the
   steering signal and there are few of them — MEASURED 1,456 typed prompts against
   23,838 tool calls in the reference corpus — so they are never sampled out.
2. Every error is included. Friction is what a person most wants explained.
3. Everything else degrades gracefully under a character budget: assistant text is
   truncated, then runs of tool calls collapse into one line, then the middle of the
   session is thinned. `coverage` reports how much survived, and the model is told.

Nothing here reads thinking blocks. They are the model's scratch space, they are the
bulk of the bytes, and a person reading their own recap does not want them quoted back.

Secrets are masked before anything is written: the digest is local, but the analysis it
produces can be uploaded, and a model will happily copy a token into a "friction" note.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import itertools
import json
import pathlib
import re
from collections import Counter

INTERRUPT_PREFIX = "[Request interrupted by user"

PROMPT_MAX = 1_400
ASSISTANT_MAX = 320
ASSISTANT_MAX_TIGHT = 120
COMMAND_MAX = 160
ERROR_MAX = 240
DEFAULT_BUDGET = 60_000  # characters; ~15k tokens

# Tool names that mean "ran a shell command" / "edited a file" per harness. Claude Code's
# names come first; the Codex names are documented in analysis/codex.py, the Gemini names
# in analysis/gemini.py, the Cline names (`execute_command` / `write_to_file` /
# `replace_in_file`, plus the SDK-era `run_commands` / `editor`) in analysis/cline.py, the
# opencode names (`bash` — the shell tool keeps that id for compatibility — `edit`, `write`,
# `apply_patch`) in analysis/opencode.py, the Aider names (`run` for `/run` / `/test` /
# `!` / an LLM-suggested command Aider ran, `apply_edit` for an `Applied edit to` line —
# Aider has no tool protocol, so these are the loader's names) in analysis/aider.py.
# Membership here is what `stats` keys commits, test runs and files-edited on, so a harness
# whose shell tool is missing from this set reports zero commits on a session that ran
# twenty.
SHELL_TOOLS = frozenset(
    {
        "Bash",
        "shell",
        "exec_command",
        "local_shell",
        "shell_command",
        "run_shell_command",
        "execute_command",
        "run_commands",
        "bash",
        "run",
    }
)
EDIT_TOOLS = frozenset(
    {
        "Edit",
        "Write",
        "MultiEdit",
        "NotebookEdit",
        "apply_patch",
        "write_file",
        "replace",
        "write_to_file",
        "replace_in_file",
        "editor",
        "edit",
        "write",
        "apply_edit",
    }
)

# Tools that ARE a git commit, not a shell line that may contain one. Aider commits through
# GitPython and writes `Commit <sha> <message>` (analysis/aider.py `commit`); the `git commit`
# regex over shell text cannot see it, and auto-commits are the point of that harness.
COMMIT_TOOLS = frozenset({"commit"})

# Tools that mean "read a file". FOUND BY A TEST: `files_read` keyed on Claude Code's `Read`
# alone, so every Gemini, Cline and opencode session reported zero files read — the same
# silent zero the shell/edit sets exist to prevent. Codex has no read tool (it reads through
# the shell), so a Codex session's files_read is 0 by construction, not by omission.
READ_TOOLS = frozenset({"Read", "read_file", "read_many_files", "read"})

_SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9\-]{10,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)(password|passwd|secret|token|api[_-]?key)\s*[:=]\s*['\"]?[^\s'\"]{6,}"),
    re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"),  # JWT
]
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def mask(text: str) -> str:
    for pat in _SECRET_PATTERNS:
        text = pat.sub("[redacted]", text)
    return _EMAIL.sub("[email]", text)


def _trunc(s: str, n: int) -> str:
    s = s.strip()
    if len(s) <= n:
        return s
    return s[: n - 12].rstrip() + f"…[+{len(s) - n + 12}]"


def _ts(s: str | None) -> float | None:
    if not s:
        return None
    try:
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"
        )
    return ""


def _looks_like_error(s: str) -> bool:
    """Only shapes that mean the COMMAND failed, not output that mentions failure.

    MEASURED on a 45-minute transcript: a loose keyword match ("no such file", "failed")
    flagged 20 results as errors; 17 were successful commands whose output quoted an
    error string (a `ls` of an absent optional file, a test name containing "failed").
    The harness's own `is_error` flag is authoritative when present; this is the fallback.
    """
    head = s[:400]
    return bool(
        re.search(
            r"(?m)^(Traceback \(most recent call last\)|Error:|error:|fatal:|FAILED|"
            r"npm ERR!|Exit code [1-9]\d*|Command failed|Killed|Segmentation fault)",
            head,
        )
        or re.search(r"(?m)^\w+(Error|Exception): ", head)
    )


_HEREDOC = re.compile(
    r"(?:cat|tee)\s*(?:>>?|-a\s+)?\s*(?P<path>[\w./~\-]+)\s*<<\s*-?['\"]?(?P<delim>\w+)['\"]?"
)
#: ANY heredoc opener, not only the ones that write a file. Used to SKIP bodies: see
#: `_bash_file_effect`. `<<<` is a here-STRING and takes no body, so it is excluded.
_ANY_HEREDOC = re.compile(r"(?<!<)<<(?!<)\s*-?\s*['\"]?(?P<delim>\w+)['\"]?")
_SED_I = re.compile(r"\bsed\s+-i\S*\s+.*?\s(?P<path>[\w./~\-]+\.\w+)(?:\s|$)")


def _command_lines(command: str):
    """Every line of `command` that is a COMMAND, with the heredoc bodies skipped.

    Yields `(index, line)`. A heredoc body is data: it can contain anything, including
    text that looks exactly like another shell command, and scanning it is how this parser
    read a piece of DOCUMENTATION as a file write.

    FOUND BY RUNNING IT on this repository's own corpus. CLAUDE.md contains the sentence
    "`analysis/digest.py` has read `cat > path <<'EOF'` writes since it was written", and
    that file is edited through `python3 - <<'PY' … PY`. The outer opener is not a `cat`
    or `tee` so the old scan skipped past it, found the `cat > path <<'EOF'` INSIDE the
    prose, and attributed 134 lines to a file literally named `path` — a file that has
    never existed, on a corpus of 10,487 attributable lines.
    """
    lines = command.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        yield i, line
        opener = _ANY_HEREDOC.search(line)
        if opener is None:
            i += 1
            continue
        # Skip the body: everything up to and including the terminator, or the rest of
        # the command when there is none (a truncated call).
        delim = opener.group("delim")
        j = i + 1
        while j < len(lines) and lines[j].strip() != delim:
            j += 1
        i = j + 1


def _bash_file_effect(command: str) -> tuple[str | None, int | None]:
    """(path, approx lines written) for shell-driven file writes.

    Agents in permission modes that prefer the shell write files with heredocs instead of
    the Write tool. MEASURED on such a session: 100 Bash calls, 0 Edit/Write calls, and
    every source file in the commit was created by `cat > path <<'EOF'`. Ignoring that
    reports "agent lines +0" on a session that added a thousand. The count is the
    heredoc body length — approximate, labelled so, and better than zero.

    ONLY COMMAND LINES ARE READ, never heredoc bodies (`_command_lines`). A body is data,
    and this parser once read a shell command quoted inside a documentation file as a
    write to a file called `path`.

    The body is the lines strictly between the opener and the terminator. FOUND ON A REAL
    SESSION (Claude Code 2.1.261, `claude -p`, 2026-09-05): the Bash input
    `mkdir -p tests && cat > tests/test_fail.py <<'EOF'\ndef test_fail():\n    assert 1 ==
    2\nEOF\ngit add -A && git commit -m 'add failing test'` scored +3 under the older
    `newlines - 1` rule while git's own result said `2 insertions(+)` — the terminator
    line and the commands after it were being counted as file content.
    """
    lines = command.split("\n")
    for i, line in _command_lines(command):
        m = _HEREDOC.search(line)
        if m is None:
            continue
        delim = m.group("delim")
        body = lines[i + 1 :]
        n = 0
        for ln in body:
            if ln.strip() == delim:
                break
            n += 1
        else:
            # No terminator (truncated command): don't count a trailing empty line.
            if body and body[-1] == "":
                n -= 1
        return m.group("path"), max(0, n)
    for _i, line in _command_lines(command):
        m = _SED_I.search(line)
        if m:
            return m.group("path"), None
    return None, None


@dataclasses.dataclass
class Ev:
    n: int  # ordinal in the digest
    ts: float
    kind: str  # prompt | interrupt | assistant | tool | result_error | human_edit | compaction
    text: str = ""
    tool: str | None = None
    path: str | None = None
    added: int | None = None
    removed: int | None = None
    ok: bool = True
    tool_id: str | None = None
    model: str | None = None
    tok_out: int | None = None


def _tool_line(b: dict) -> tuple[str, str | None, str]:
    """(tool name, file path, one-line description of the input)."""
    name = b.get("name") or "tool"
    inp = b.get("input") or {}
    if not isinstance(inp, dict):
        return name, None, ""
    path = inp.get("file_path") or inp.get("path") or inp.get("notebook_path")
    if name == "Bash":
        cmd = str(inp.get("command", ""))
        path, _ = _bash_file_effect(cmd)
        return name, path, _trunc(cmd.replace("\n", " ⏎ "), COMMAND_MAX)
    if name in ("Read", "Write", "Edit", "MultiEdit", "NotebookEdit"):
        return name, path, str(path or "")
    if name in ("Glob", "Grep"):
        return name, None, _trunc(str(inp.get("pattern", "")), 80)
    if name in ("Agent", "Task"):
        return name, None, _trunc(str(inp.get("description") or inp.get("prompt", "")), 100)
    if name in ("WebSearch", "WebFetch"):
        return name, None, _trunc(str(inp.get("query") or inp.get("url", "")), 100)
    if name == "TodoWrite" or name.startswith("Task"):
        return name, None, ""
    # MCP and everything else: name only. Inputs may be anything.
    return name, None, _trunc(json.dumps(inp, separators=(",", ":"))[:200], 100) if inp else ""


def _is_gemini_record(r: dict) -> bool:
    # Gemini's metadata line carries `sessionId` + `projectHash` and never `uuid` /
    # `parentUuid`; a Claude Code record carries `sessionId` too, so order matters.
    return (
        isinstance(r.get("sessionId"), str)
        and isinstance(r.get("projectHash"), str)
        and "uuid" not in r
        and "parentUuid" not in r
    )


_CLINE_FILES = frozenset({"ui_messages.json", "api_conversation_history.json"})


def _is_opencode(path: pathlib.Path) -> bool:
    # Decided on the path shape first (a virtual `<db>/<session id>` does not exist on
    # disk; a session file sits under `storage/session/`), then on the SQLite header and
    # table names — Cursor's `state.vscdb` and Codex's `state_N.sqlite` are SQLite too.
    # Cheap rejections first so every `.jsonl` in a Claude Code tree is not opened twice.
    if path.suffix in (".jsonl", ".md", ".txt", ".sqlite", ".vscdb"):
        return False
    from . import opencode

    return opencode.detect(path) is not None


def _is_aider(path: pathlib.Path) -> bool:
    # Aider keeps `.aider.chat.history.md` (+ `.aider.input.history`) IN THE REPO; a session
    # is `<chat file>/<YYYYMMDD-HHMMSS>` (virtual last component). Decided on the names
    # first; a `.md` under another name is opened only for its first line, which must be
    # `# aider chat started at …` (analysis/aider.py).
    if path.suffix in (".jsonl", ".json", ".sqlite", ".vscdb", ".db"):
        return False
    from . import aider

    return aider.detect(path) is not None


def _is_cline_task(path: pathlib.Path) -> bool:
    # A Cline task is a DIRECTORY holding `ui_messages.json` (and usually
    # `api_conversation_history.json`); either file names the task too. Decided on the
    # path shape, not the content: the ui file is a bare JSON array of `{ts, type, say}`
    # rows with nothing that names the harness (analysis/cline.py).
    if path.is_dir():
        return any((path / n).is_file() for n in _CLINE_FILES)
    return path.name in _CLINE_FILES


def detect_harness(path: pathlib.Path) -> str:
    """ "claude_code", "codex", "gemini", "cline", "opencode" or "aider", from the first
    complete non-empty line (or the path shape).

    A Codex rollout's first record is `{"type": "session_meta", "payload": …}` (the
    recorder writes it before anything else); a Gemini CLI recording's first line is
    `{"sessionId", "projectHash", "startTime", …}` (or, for a legacy `.json` file, the
    whole conversation as one object); a Claude Code transcript's records carry
    `sessionId` / `parentUuid` / `uuid`; a Cline task is a directory (or a
    `ui_messages.json` / `api_conversation_history.json` inside one); an opencode session
    is `<opencode.db>/<session id>` (a SQLite file with `session`/`message`/`part`
    tables, plus a virtual last component), a `storage/session/<project>/<id>.json`
    file, or an `opencode export` file (analysis/opencode.py); an Aider session is a repo
    directory holding `.aider.chat.history.md`, that file, its `.aider.input.history`, or
    `<chat file>/<session id>` (analysis/aider.py). Anything unrecognised is treated as
    Claude Code, which is the loader with the measured ground truth behind it.
    """
    path = pathlib.Path(path)
    if _is_cline_task(path):
        return "cline"
    if _is_aider(path):
        return "aider"
    if _is_opencode(path):
        return "opencode"
    try:
        if path.suffix == ".json":
            try:
                whole = json.loads(path.read_bytes())
            except json.JSONDecodeError:
                whole = None
            if isinstance(whole, dict) and _is_gemini_record(whole):
                return "gemini"
        with path.open("rb") as f:
            for line in f:
                if not line.endswith(b"\n"):
                    break
                if not line.strip():
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    return "claude_code"
                if not isinstance(r, dict):
                    return "claude_code"
                if _is_gemini_record(r):
                    return "gemini"
                if "sessionId" in r or "parentUuid" in r or "uuid" in r:
                    return "claude_code"
                if r.get("type") == "session_meta" or (
                    "payload" in r and "timestamp" in r and isinstance(r.get("type"), str)
                ):
                    return "codex"
                return "claude_code"
    except OSError:
        pass
    return "claude_code"


def load_events(
    path: pathlib.Path, start: float | None = None, end: float | None = None
) -> list[Ev]:
    """Read one transcript into digest events, dispatching on the harness that wrote it."""
    harness = detect_harness(path)
    if harness == "codex":
        from . import codex

        return codex.load_events(path, start, end)
    if harness == "gemini":
        from . import gemini

        return gemini.load_events(path, start, end)
    if harness == "cline":
        from . import cline

        return cline.load_events(path, start, end)
    if harness == "opencode":
        from . import opencode

        return opencode.load_events(path, start, end)
    if harness == "aider":
        from . import aider

        return aider.load_events(path, start, end)
    return load_claude_code_events(path, start, end)


def load_claude_code_events(
    path: pathlib.Path, start: float | None = None, end: float | None = None
) -> list[Ev]:
    """Read one Claude Code transcript into digest events, in time order, within [start, end]."""
    raw: list[tuple[float, int, dict]] = []
    with path.open("rb") as f:
        for i, line in enumerate(f):
            if not line.endswith(b"\n"):
                break  # partial trailing line is never consumed
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = _ts(r.get("timestamp"))
            if ts is None:
                continue
            if start is not None and ts < start:
                continue
            if end is not None and ts > end:
                continue
            raw.append((ts, i, r))
    raw.sort(key=lambda t: (t[0], t[1]))

    tool_names: dict[str, tuple[str, str | None]] = {}  # tool_use_id -> (name, path)
    out: list[Ev] = []
    seen_msg: set[str] = set()

    for ts, _, r in raw:
        t = r.get("type")
        if t == "user":
            msg = r.get("message") or {}
            content = msg.get("content")
            ps = r.get("promptSource")
            origin = (r.get("origin") or {}).get("kind")
            is_meta = bool(r.get("isMeta"))
            text = _text_of(content)
            if (
                not is_meta
                and (ps == "typed" or (ps == "sdk" and origin == "human"))
                and text.strip()
            ):
                out.append(Ev(0, ts, "prompt", mask(_trunc(text, PROMPT_MAX))))
            elif text.startswith(INTERRUPT_PREFIX):
                out.append(Ev(0, ts, "interrupt", ""))
            if isinstance(content, list):
                tur = r.get("toolUseResult")
                for b in content:
                    if not isinstance(b, dict) or b.get("type") != "tool_result":
                        continue
                    tid = b.get("tool_use_id")
                    name, path = tool_names.get(tid, ("tool", None))
                    body = (
                        _text_of(b.get("content"))
                        if not isinstance(b.get("content"), str)
                        else b["content"]
                    )
                    is_err = bool(b.get("is_error")) or _looks_like_error(body or "")
                    added = removed = None
                    if isinstance(tur, dict):
                        patch = tur.get("structuredPatch")
                        if isinstance(patch, list) and patch:
                            added = sum(
                                1
                                for h in patch
                                for ln in h.get("lines", [])
                                if str(ln).startswith("+")
                            )
                            removed = sum(
                                1
                                for h in patch
                                for ln in h.get("lines", [])
                                if str(ln).startswith("-")
                            )
                        elif tur.get("type") == "create" and isinstance(tur.get("content"), str):
                            # Lines = newlines, plus one only for an unterminated last
                            # line. FOUND ON A REAL SESSION (Claude Code 2.1.261, `claude
                            # -p`, 2026-09-05): `toolUseResult: {"type": "create",
                            # "filePath": ".../hello.py", "content": "#!/usr/bin/env
                            # python3\n\ndef main():\n    print('hi')\n\nif __name__ ==
                            # '__main__': main()\n"}` is a 6-line file (`wc -l` 6, git
                            # `6 insertions(+)`); `newlines + 1` reported 7.
                            c = tur["content"]
                            added = c.count("\n") + (1 if c and not c.endswith("\n") else 0)
                            removed = 0
                        path = (
                            path or tur.get("filePath") or (tur.get("file") or {}).get("filePath")
                            if isinstance(tur.get("file"), dict)
                            else path or tur.get("filePath")
                        )
                    if is_err:
                        out.append(
                            Ev(
                                0,
                                ts,
                                "result_error",
                                mask(_trunc(body or "(error)", ERROR_MAX)),
                                tool=name,
                                path=path,
                                ok=False,
                                tool_id=tid,
                            )
                        )
                    elif added is not None:
                        # attach the line delta to the originating tool event
                        for e in reversed(out):
                            if e.kind == "tool" and e.tool_id == tid:
                                e.added, e.removed, e.path = added, removed, path or e.path
                                break
        elif t == "assistant":
            msg = r.get("message") or {}
            mid = msg.get("id") or r.get("requestId")
            model = msg.get("model")
            usage = msg.get("usage") or {}
            first = mid not in seen_msg
            if mid:
                seen_msg.add(mid)
            for b in msg.get("content") or []:
                if not isinstance(b, dict):
                    continue
                bt = b.get("type")
                if bt == "text" and b.get("text", "").strip():
                    out.append(
                        Ev(
                            0,
                            ts,
                            "assistant",
                            mask(_trunc(b["text"], ASSISTANT_MAX)),
                            model=model,
                            tok_out=usage.get("output_tokens") if first else None,
                        )
                    )
                    first = False
                elif bt == "tool_use":
                    name, path, desc = _tool_line(b)
                    tool_names[b.get("id")] = (name, path)
                    ev = Ev(
                        0,
                        ts,
                        "tool",
                        mask(desc),
                        tool=name,
                        path=path,
                        tool_id=b.get("id"),
                        model=model,
                    )
                    if name == "Bash":
                        _, approx = _bash_file_effect(
                            str((b.get("input") or {}).get("command", ""))
                        )
                        if approx is not None:
                            ev.added, ev.removed = approx, 0
                    out.append(ev)
        elif t == "attachment" and (r.get("attachment") or {}).get("type") == "edited_text_file":
            out.append(
                Ev(0, ts, "human_edit", "", path=(r.get("attachment") or {}).get("filename"))
            )
        elif t == "system" and r.get("subtype") == "compact_boundary":
            out.append(Ev(0, ts, "compaction", ""))

    for i, e in enumerate(out):
        e.n = i
    return out


# ----------------------------------------------------------------------------- stats


def stats(events: list[Ev]) -> dict:
    """Deterministic numbers. These go on the card; the model never invents them."""
    if not events:
        return {"events": 0}
    t0, t1 = events[0].ts, events[-1].ts
    prompts = [e for e in events if e.kind == "prompt"]
    tools = [e for e in events if e.kind == "tool"]
    errors = [e for e in events if e.kind == "result_error"]
    words = [len(p.text.split()) for p in prompts]
    added = sum(e.added or 0 for e in tools)
    removed = sum(e.removed or 0 for e in tools)
    files = {
        e.path
        for e in tools
        if e.path and (e.tool in EDIT_TOOLS or (e.tool in SHELL_TOOLS and e.added is not None))
    }
    shell_written = sum(1 for e in tools if e.tool in SHELL_TOOLS and e.added is not None)
    reads = {e.path for e in tools if e.path and e.tool in READ_TOOLS}
    commits = sum(
        1
        for e in tools
        if e.tool in COMMIT_TOOLS
        or (e.tool in SHELL_TOOLS and re.search(r"\bgit commit\b", e.text))
    )
    tests = sum(
        1
        for e in tools
        if e.tool in SHELL_TOOLS
        and re.search(
            r"\b(pytest|bun test|npm test|swift test|jest|cargo test|go test|make test)\b", e.text
        )
    )
    models = Counter(e.model for e in events if e.kind == "assistant" and e.model)
    # prompts received = assistant turns that produced text for the human
    replies = sum(1 for e in events if e.kind == "assistant")
    return {
        "events": len(events),
        "wall_seconds": round(t1 - t0),
        "prompts_sent": len(prompts),
        "replies_received": replies,
        "interrupts": sum(1 for e in events if e.kind == "interrupt"),
        "human_edits": sum(1 for e in events if e.kind == "human_edit"),
        "compactions": sum(1 for e in events if e.kind == "compaction"),
        "prompt_words_avg": round(sum(words) / len(words), 1) if words else 0,
        "prompt_words_median": sorted(words)[len(words) // 2] if words else 0,
        "prompt_words_max": max(words) if words else 0,
        "tool_calls": len(tools),
        "tool_mix": dict(Counter(e.tool for e in tools).most_common()),
        "errors": len(errors),
        "lines_added_agent": added,
        "lines_removed_agent": removed,
        "files_edited": len(files),
        "files_written_via_shell": shell_written,
        "files_read": len(reads),
        "git_commits_run": commits,
        "test_runs": tests,
        "models": dict(models.most_common()),
        "longest_silence_seconds": round(
            max((b.ts - a.ts for a, b in itertools.pairwise(events)), default=0)
        ),
    }


# ----------------------------------------------------------------------------- render


def _fmt_t(t0: float, ts: float) -> str:
    m = (ts - t0) / 60
    return f"+{m:5.1f}m"


def _render_event(e: Ev, t0: float, tight: bool) -> str:
    t = _fmt_t(t0, e.ts)
    if e.kind == "prompt":
        return f"[{e.n}] {t} PROMPT: {json.dumps(e.text, ensure_ascii=False)}"
    if e.kind == "interrupt":
        return f"[{e.n}] {t} INTERRUPT (human stopped the agent)"
    if e.kind == "human_edit":
        return f"[{e.n}] {t} HUMAN EDITED FILE {e.path or ''}".rstrip()
    if e.kind == "compaction":
        return f"[{e.n}] {t} CONTEXT COMPACTED"
    if e.kind == "assistant":
        txt = _trunc(e.text, ASSISTANT_MAX_TIGHT if tight else ASSISTANT_MAX)
        return f"[{e.n}] {t} ASSISTANT: {json.dumps(txt, ensure_ascii=False)}"
    if e.kind == "tool":
        delta = f" +{e.added}/-{e.removed}" if e.added is not None else ""
        body = e.text if e.tool in SHELL_TOOLS else (e.path or e.text)
        return f"[{e.n}] {t} {e.tool}{delta}: {body}".rstrip(": ")
    if e.kind == "result_error":
        return f"[{e.n}] {t} ERROR from {e.tool}: {json.dumps(e.text, ensure_ascii=False)}"
    return f"[{e.n}] {t} {e.kind}"


def _collapse_tool_runs(events: list[Ev], t0: float, min_run: int = 4) -> list[str]:
    """Consecutive tool calls (no prompt/assistant/error between) become one summary line."""
    lines: list[str] = []
    i = 0
    while i < len(events):
        e = events[i]
        if e.kind != "tool":
            lines.append(_render_event(e, t0, tight=True))
            i += 1
            continue
        j = i
        while j < len(events) and events[j].kind == "tool":
            j += 1
        run = events[i:j]
        if len(run) < min_run:
            lines.extend(_render_event(x, t0, tight=True) for x in run)
        else:
            mix = Counter(x.tool for x in run)
            edited = [
                f"{x.path.rsplit('/', 1)[-1]} +{x.added}/-{x.removed}"
                for x in run
                if x.added is not None and x.path
            ][:6]
            cmds = [x.text.split(" ⏎ ")[0][:40] for x in run if x.tool in SHELL_TOOLS][:4]
            span = (run[-1].ts - run[0].ts) / 60
            parts = [f"{k}×{v}" for k, v in mix.most_common()]
            detail = ""
            if edited:
                detail += " edits: " + ", ".join(edited)
            if cmds:
                detail += " bash: " + " | ".join(cmds)
            lines.append(
                f"[{run[0].n}-{run[-1].n}] {_fmt_t(t0, run[0].ts)} TOOLS ×{len(run)} over {span:.1f}m: {', '.join(parts)}.{detail}"
            )
        i = j
    return lines


def render(events: list[Ev], meta: dict, budget: int = DEFAULT_BUDGET) -> tuple[str, float]:
    """Render the digest under a character budget. Returns (text, coverage)."""
    if not events:
        return "# SESSION DIGEST\n(no events)\n", 1.0
    t0 = events[0].ts
    st = stats(events)

    head = ["# SESSION DIGEST"]
    for k in (
        "repo",
        "harness",
        "started_at_local",
        "end_reason",
        "attended_seconds",
        "autonomous_seconds",
    ):
        if meta.get(k) is not None:
            head.append(f"{k}: {meta[k]}")
    head.append(
        f"wall: {st['wall_seconds'] // 60}m  prompts sent: {st['prompts_sent']}  replies: {st['replies_received']}  "
        f"tool calls: {st['tool_calls']}  errors: {st['errors']}  interrupts: {st['interrupts']}  "
        f"agent lines +{st['lines_added_agent']}/-{st['lines_removed_agent']}  files edited: {st['files_edited']}  "
        f"git commits run: {st['git_commits_run']}  test runs: {st['test_runs']}"
    )
    head.append("tool mix: " + ", ".join(f"{k} {v}" for k, v in st["tool_mix"].items()))
    if st["models"]:
        head.append("models: " + ", ".join(f"{k} ({v} turns)" for k, v in st["models"].items()))
    head.append("")
    head.append("# TIMELINE  ([n] = event ordinal, +m = minutes from start)")
    header = "\n".join(head) + "\n"

    # Level 0: everything, generous truncation.
    body = "\n".join(_render_event(e, t0, tight=False) for e in events)
    if len(header) + len(body) <= budget:
        return header + body + "\n", 1.0

    # Level 1: tight assistant text, collapse tool runs.
    lines = _collapse_tool_runs(events, t0)
    body = "\n".join(lines)
    if len(header) + len(body) <= budget:
        return header + body + "\n", 1.0

    # Level 2: keep every prompt/error/interrupt/human-edit line and the first/last 12
    # lines; thin the rest evenly until it fits. Coverage reports the loss.
    must = [
        ln
        for ln in lines
        if any(
            k in ln
            for k in (
                " PROMPT: ",
                " ERROR from ",
                " INTERRUPT",
                " HUMAN EDITED",
                " CONTEXT COMPACTED",
            )
        )
    ]
    rest = [ln for ln in lines if ln not in set(must)]
    keep_edges = rest[:12] + rest[-12:] if len(rest) > 24 else rest
    middle = rest[12:-12] if len(rest) > 24 else []
    budget_left = (
        budget - len(header) - sum(len(x) + 1 for x in must) - sum(len(x) + 1 for x in keep_edges)
    )
    kept_middle: list[str] = []
    if middle and budget_left > 0:
        avg = max(1, sum(len(x) + 1 for x in middle) // len(middle))
        n_keep = max(0, min(len(middle), budget_left // avg))
        if n_keep:
            step = len(middle) / n_keep
            kept_middle = [middle[int(i * step)] for i in range(n_keep)]
    chosen = set(must) | set(keep_edges) | set(kept_middle)
    body_lines = [ln for ln in lines if ln in chosen]
    dropped = len(lines) - len(body_lines)
    coverage = len(body_lines) / max(1, len(lines))
    note = f"\n(… {dropped} of {len(lines)} timeline lines omitted to fit; every prompt and error is present. coverage={coverage:.2f})\n"
    return header + "\n".join(body_lines) + note, round(coverage, 3)


def digest_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def build(
    path: pathlib.Path,
    start: float | None = None,
    end: float | None = None,
    meta: dict | None = None,
    budget: int = DEFAULT_BUDGET,
) -> dict:
    harness = detect_harness(path)
    meta = dict(meta or {})
    meta.setdefault("harness", harness)
    if harness == "codex":
        from . import codex

        events = codex.load_events(path, start, end)
    elif harness == "gemini":
        from . import gemini

        events = gemini.load_events(path, start, end)
    elif harness == "cline":
        from . import cline

        events = cline.load_events(path, start, end)
    elif harness == "opencode":
        from . import opencode

        events = opencode.load_events(path, start, end)
    elif harness == "aider":
        from . import aider

        events = aider.load_events(path, start, end)
    else:
        events = load_claude_code_events(path, start, end)
    text, coverage = render(events, meta, budget)
    return {
        "text": text,
        "coverage": coverage,
        "hash": digest_hash(text),
        "stats": stats(events),
        "events": len(events),
    }
