"""`python -m analysis probe <path-or-dir>` — the diagnostics-first pass over a transcript
store, read-only.

docs/integrations.md promises that no parser ships without a probe that prints, for the
real store: the record types seen and their counts, records with no timestamp, unknown
shapes, the first and last timestamp, and — for harnesses that carry usage — the naive
token sum next to the deduplicated one. This is that command. Nothing here writes.
"""

from __future__ import annotations

import json
import pathlib
from collections import Counter

from . import digest as dg


def _walk(root: pathlib.Path) -> list[pathlib.Path]:
    root = pathlib.Path(root).expanduser()
    from . import aider

    if root.is_file():
        # an Aider chat history is MANY sessions; report each as `<chat file>/<id>`
        if aider.detect(root) in ("chat_file", "input_file"):
            chat = aider.resolve(root)[0]
            return [chat / s.id for s in aider.list_sessions(chat)]
        return [root]
    if not root.exists():
        return [root]  # a virtual `<db>/<session id>`; probe_file reports if it is not one
    # Codex keeps `sessions/YYYY/MM/DD/rollout-*.jsonl`; Claude Code keeps
    # `projects/<slug>/<uuid>.jsonl` plus subagent sidecars; Gemini CLI keeps
    # `tmp/<project>/chats/session-*.jsonl` (subagents one level deeper) and, from older
    # releases, whole-conversation `chats/*.json`. A recursive glob covers all three; the
    # probe reports per file, so sidecars are visible rather than silently merged. `.json`
    # is taken ONLY under a `chats/` directory: the same tree holds `checkpoints/*.json`
    # and `logs.json`, which are not conversations.
    files = [p for p in root.rglob("*.jsonl") if p.is_file()]
    files += [p for p in root.rglob("*.json") if p.is_file() and "chats" in p.parts]
    # Cline keeps one DIRECTORY per task: `tasks/<taskId>/ui_messages.json` next to
    # `api_conversation_history.json`. The probe reports per task directory (the loader
    # reads every file in it), so the root may be `~/.cline/data`, its `tasks/`, or one task.
    if root.is_dir() and dg._is_cline_task(root):
        return [root]
    task_dirs = {p.parent for p in root.rglob("ui_messages.json") if p.is_file()}
    task_dirs |= {p.parent for p in root.rglob("api_conversation_history.json") if p.is_file()}
    # opencode keeps EVERY session in one SQLite file (`opencode.db`, or `opencode-<channel>
    # .db`); the probe reports per session as `<db>/<session id>` — child (subagent)
    # sessions included, flagged by `parent=` in the meta line, so they are visible rather
    # than silently summed into their parent. The pre-SQLite JSON store is
    # `storage/session/<project>/<id>.json`, and `opencode export` files directly under the
    # root are taken too (a recursive `.json` scan would open every `storage/part` file).
    from . import opencode

    sessions: set[pathlib.Path] = set()
    for db in root.rglob("*.db"):
        if db.is_file() and opencode.is_database(db):
            sessions |= {db / s["id"] for s in opencode.list_sessions(db)}
    sessions |= {p for p in root.rglob("session/*/*.json") if opencode.is_session_file(p)}
    sessions |= {p for p in root.glob("*.json") if opencode.is_export_file(p)}
    # Aider keeps `.aider.chat.history.md` in each REPO, every session appended to it; the
    # root may be one repo or a directory of repos. One virtual path per session.
    for chat in root.rglob(aider.CHAT_FILE):
        if chat.is_file():
            sessions |= {chat / s.id for s in aider.list_sessions(chat)}
    return sorted(set(files) | task_dirs | sessions)


def _claude_code_summary(path: pathlib.Path) -> dict:
    """Type counts and timestamp gaps for a Claude Code transcript, same read discipline
    (partial trailing line never consumed, malformed lines counted)."""
    types: Counter = Counter()
    d = {
        "lines": 0,
        "records": 0,
        "malformed_lines": 0,
        "partial_trailing_line": False,
        "no_timestamp": 0,
        "bad_timestamp": 0,
        "first_ts": None,
        "last_ts": None,
    }
    lo = hi = None
    with path.open("rb") as f:
        for line in f:
            if not line.endswith(b"\n"):
                d["partial_trailing_line"] = True
                break
            d["lines"] += 1
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                d["malformed_lines"] += 1
                continue
            if not isinstance(r, dict):
                d["malformed_lines"] += 1
                continue
            d["records"] += 1
            types[str(r.get("type"))] += 1
            raw = r.get("timestamp")
            ts = dg._ts(raw) if isinstance(raw, str) else None
            if raw is None:
                d["no_timestamp"] += 1
            elif ts is None:
                d["bad_timestamp"] += 1
            else:
                if lo is None or ts < lo[0]:
                    lo = (ts, raw)
                if hi is None or ts > hi[0]:
                    hi = (ts, raw)
    d["first_ts"] = lo[1] if lo else None
    d["last_ts"] = hi[1] if hi else None
    d["types"] = dict(types.most_common())
    d["unknown_types"] = {t: n for t, n in types.items() if t not in CLAUDE_CODE_KNOWN_TYPES}
    return d


# Record types the loader reads (user, assistant, attachment, system) plus the bookkeeping
# types ClaudeCodeParser.swift names. Before this set existed the Claude Code summary
# printed `UNKNOWN: none` unconditionally — the same false reassurance the other probes
# exist to prevent. MEASURED on this container's 2,553-record root transcript (Claude Code
# 2.1.261, 2026-09-05): assistant 787, attachment 633, user 475, queue-operation 164,
# last-prompt 163, atis-latch 157, mode 138, system 36; the 458 records without a timestamp
# were exactly the atis-latch, last-prompt and mode rows. `claude -p` roots add ai-title.
CLAUDE_CODE_KNOWN_TYPES = frozenset(
    {
        "user",
        "assistant",
        "attachment",
        "system",
        "summary",
        "ai-title",
        "last-prompt",
        "mode",
        "permission-mode",
        "file-history-snapshot",
        "file-history-delta",
        "queue-operation",
        "atis-latch",
        "frame-link",
        "pr-link",
        "started",
        "result",
    }
)


def probe_file(path: pathlib.Path) -> dict:
    """Everything the probe knows about one file, as plain data."""
    path = pathlib.Path(path)
    harness = dg.detect_harness(path)
    size = path.stat().st_size if path.exists() else path.parent.stat().st_size
    out: dict = {"path": str(path), "harness": harness, "bytes": size}
    if harness == "codex":
        from . import codex

        s = codex.scan(path)
        events, derivation = codex._derive(s)
        out["diagnostics"] = dict(s.diagnostics, derivation=derivation)
        out["meta"] = s.meta
        out["usage"] = s.usage
    elif harness == "gemini":
        from . import gemini

        s = gemini.scan(path)
        events, derivation = gemini._derive(s)
        out["diagnostics"] = dict(s.diagnostics, derivation=derivation)
        out["meta"] = s.meta
        out["usage"] = s.usage
    elif harness == "cline":
        from . import cline

        s = cline.scan(path)
        _, files = cline.resolve(path)
        out["bytes"] = sum(p.stat().st_size for k, p in files.items() if p and k != "index")
        events, derivation = cline._derive(s)
        out["diagnostics"] = dict(s.diagnostics, derivation=derivation)
        out["meta"] = s.meta
        out["usage"] = s.usage
    elif harness == "opencode":
        from . import opencode

        s = opencode.scan(path)
        events, derivation = opencode._derive(s)
        out["diagnostics"] = dict(s.diagnostics, derivation=derivation)
        out["meta"] = s.meta
        out["usage"] = s.usage
    elif harness == "aider":
        from . import aider

        s = aider.scan(path)
        out["bytes"] = sum(p.stat().st_size for p in (s.chat_file, s.input_file) if p)
        events, derivation = aider._derive(s)
        out["diagnostics"] = dict(s.diagnostics, derivation=derivation)
        out["meta"] = s.meta
        out["usage"] = s.usage
    else:
        out["diagnostics"] = _claude_code_summary(path)
        events = dg.load_claude_code_events(path)
    out["stats"] = dg.stats(events)
    return out


def _fmt_gemini_usage(u: dict) -> str:
    naive, dedup = u["naive_sum_all_records"], u["deduped_by_message_id"]
    lines = [
        (
            f"  gemini messages: {u['gemini_messages']} (with tokens: "
            f"{u['gemini_messages_with_tokens']}); record lines carrying tokens: "
            f"{u['naive_records_with_tokens']}"
        ),
        (
            f"  naive sum over every record:   total {naive['total']:,}  in {naive['input']:,} "
            f"(cached {naive['cached']:,})  out {naive['output']:,} (thoughts {naive['thoughts']:,})"
        ),
        (
            f"  deduped by message id:         total {dedup['total']:,}  in {dedup['input']:,} "
            f"(cached {dedup['cached']:,})  out {dedup['output']:,} (thoughts {dedup['thoughts']:,})"
        ),
        "  naive == deduped: "
        + ("yes" if u["naive_equals_deduped"] else "NO — do not sum record lines"),
    ]
    return "\n".join(lines)


def _fmt_cline_usage(u: dict) -> str:
    def _row(label: str, t: dict) -> str:
        return (
            f"  {label:<31}in {t['tokensIn'] or 0:,} (cache w {t['cacheWrites'] or 0:,} / "
            f"r {t['cacheReads'] or 0:,})  out {t['tokensOut'] or 0:,}  cost ${t['cost'] or 0}"
        )

    lines = [
        (
            f"  api_req_started rows: {u['api_requests']} (with tokens: "
            f"{u['api_requests_with_tokens']}); api_req_finished rows: "
            f"{u['ui_api_req_finished_rows']}; deleted/subagent rows: "
            f"{u['ui_deleted_and_subagent_rows']}"
            + (
                f"; cancelled: {', '.join(f'{k} {v}' for k, v in u['api_requests_cancelled'].items())}"
                if u["api_requests_cancelled"]
                else ""
            )
        ),
        _row("sum over api_req_started:", u["ui_api_req_started_sum"]),
        _row("getApiMetrics (with deleted):", u["ui_get_api_metrics"]),
    ]
    if u["ui_api_req_finished_rows"]:
        lines.append(_row("api_req_finished rows apart:", u["ui_api_req_finished_sum"]))
    if u["index_totals"]:
        lines.append(_row("taskHistory.json index:", u["index_totals"]))
        lines.append(
            "  ui == index: "
            + ("yes" if u["ui_equals_index"] else "NO — the two stores disagree; report both")
        )
    else:
        lines.append("  taskHistory.json index:        (not found for this task)")
    return "\n".join(lines)


def _fmt_opencode_usage(u: dict) -> str:
    def _row(label: str, t: dict | None) -> str:
        if not t:
            return f"  {label:<31}(not in this store)"
        return (
            f"  {label:<31}in {t['input']:,} (cache r {t['cache_read']:,} / w {t['cache_write']:,})"
            f"  out {t['output']:,} (reasoning {t['reasoning']:,})"
        )

    lines = [
        (
            f"  assistant messages: {u['assistant_messages']} (with tokens: "
            f"{u['assistant_messages_with_tokens']}; summaries: {u['summary_messages']}; "
            f"multi-step: {u['assistant_messages_multi_step']}); step-finish parts: "
            f"{u['step_finish_parts']}"
        ),
        _row("sum of message.tokens (last step):", u["sum_message_tokens"]),
        _row("sum of step-finish parts:", u["sum_step_finish_tokens"]),
        _row("session row tokens_*:", u["session_row_tokens"]),
        "  message sum == step sum: "
        + (
            "yes"
            if u["message_sum_equals_step_sum"]
            else "NO — message.tokens is the LAST step; sum the step-finish parts"
        ),
        (
            f"  cost: messages ${u['sum_message_cost']}  steps ${u['sum_step_finish_cost']}  "
            f"session row ${u['session_row_cost']}"
        ),
    ]
    if u.get("session_message_projection_rows") is not None:
        lines.append(
            f"  session_message projection rows: {u['session_message_projection_rows']} "
            "(the same turns again in the v2 shape — never unioned with `message`)"
        )
    return "\n".join(lines)


def _fmt_aider_usage(u: dict) -> str:
    t = u["tokens_as_printed"]
    lines = [
        (
            f"  Tokens lines: {u['messages']} (with cost: {u['messages_with_cost']}); figures "
            f"are AS PRINTED — Aider rounds to 0.1k / 1k, so these are approximate"
        ),
        (
            f"  sum as printed:                sent {t['sent']:,} (cache write {t['cache_write']:,}"
            f" / hit {t['cache_hit']:,})  received {t['received']:,}"
        ),
        (
            f"  cost: sum of message costs ${u['sum_message_cost']}  last session total "
            f"{'$' + str(u['last_session_cost']) if u['last_session_cost'] is not None else '(none)'}"
        ),
    ]
    if u["session_cost_matches_sum"] is not None:
        lines.append(
            "  session total == sum: "
            + ("yes" if u["session_cost_matches_sum"] else "NO — a turn's cost went unprinted")
        )
    return "\n".join(lines)


def _fmt_usage(u: dict) -> str:
    if "deduped_by_message_id" in u:
        return _fmt_gemini_usage(u)
    if "ui_api_req_started_sum" in u:
        return _fmt_cline_usage(u)
    if "sum_step_finish_tokens" in u:
        return _fmt_opencode_usage(u)
    if "tokens_as_printed" in u:
        return _fmt_aider_usage(u)
    naive = u["naive_sum_last_token_usage"]
    final = u["final_total_token_usage"]
    lines = [
        (
            f"  token_count events: {u['token_count_events']} "
            f"(with info: {u['token_count_events_with_info']})"
        ),
        (
            f"  naive sum of last_token_usage: total {naive['total_tokens']:,}  "
            f"in {naive['input_tokens']:,} (cached {naive['cached_input_tokens']:,})  "
            f"out {naive['output_tokens']:,} (reasoning {naive['reasoning_output_tokens']:,})"
        ),
    ]
    if final:
        lines.append(
            f"  final total_token_usage:       total {final['total_tokens']:,}  "
            f"in {final['input_tokens']:,} (cached {final['cached_input_tokens']:,})  "
            f"out {final['output_tokens']:,} (reasoning {final['reasoning_output_tokens']:,})"
            f"  [max seen {u['max_total_tokens_seen']:,}]"
        )
        lines.append(
            "  naive == final: "
            + ("yes" if u["naive_sum_equals_final_total"] else "NO — do not sum blindly")
        )
    else:
        lines.append("  final total_token_usage:       (none recorded)")
    if u["token_usage_records"]:
        rs = u["token_usage_records_sum_usage"]
        th = u["token_usage_records_final_thread_usage"] or {}
        lines.append(
            f"  token_usage_record: {u['token_usage_records']} records, sum usage total "
            f"{rs['total_tokens']:,}; final thread_token_usage total "
            f"{th.get('total_tokens', 0):,}"
        )
    return "\n".join(lines)


def format_probe(d: dict) -> str:
    g = d["diagnostics"]
    st = d["stats"]
    lines = [f"{d['path']}", f"  harness: {d['harness']}   bytes: {d['bytes']:,}"]
    meta = d.get("meta")
    if meta:
        bits = [
            f"{k}={meta[k]}"
            for k in (
                "cli_version",
                "history_mode",
                "model",
                "edit_format",
                "cwd",
                "git_branch",
                "source",
                "kind",
                "container",
                "session_id",
                "sessions_in_file",
                "parent_id",
                "session_selected_by",
            )
            if meta.get(k) and not (k == "session_selected_by" and meta[k] == "path")
        ]
        if meta.get("child_sessions"):
            bits.append(f"children={len(meta['child_sessions'])}")
        lines.append("  meta: " + "  ".join(bits))
    lines.append(
        f"  lines: {g['lines']}  records: {g['records']}  malformed: {g['malformed_lines']}  "
        f"partial trailing line: {'yes' if g['partial_trailing_line'] else 'no'}"
        + (f"  container: {g['container']}" if g.get("container") else "")
    )
    if g.get("record_kinds"):
        lines.append(
            "  record kinds: " + ", ".join(f"{k} {v}" for k, v in g["record_kinds"].items())
        )
    lines.append(
        f"  no timestamp: {g['no_timestamp']}  bad timestamp: {g.get('bad_timestamp', 0)}  "
        f"first: {g['first_ts']}  last: {g['last_ts']}"
    )
    lines.append("  types: " + ", ".join(f"{k} {v}" for k, v in g["types"].items()))
    if g.get("part_types"):
        lines.append("  part types: " + ", ".join(f"{k} {v}" for k, v in g["part_types"].items()))
    if g.get("tool_statuses"):
        lines.append(
            "  tool statuses: " + ", ".join(f"{k} {v}" for k, v in g["tool_statuses"].items())
        )
    if g.get("assistant_error_names"):
        lines.append(
            "  assistant errors: "
            + ", ".join(f"{k} {v}" for k, v in g["assistant_error_names"].items())
        )
    if g.get("payload_types"):
        lines.append(
            "  payload types: " + ", ".join(f"{k} {v}" for k, v in g["payload_types"].items())
        )
    unk = dict(g.get("unknown_types") or {})
    unk.update(g.get("unknown_payload_types") or {})
    unk.update({f"tool:{k}": v for k, v in (g.get("unknown_tools") or {}).items()})
    unk.update({f"error:{k}": v for k, v in (g.get("unknown_error_names") or {}).items()})
    lines.append("  UNKNOWN: " + (", ".join(f"{k} {v}" for k, v in unk.items()) if unk else "none"))
    if g.get("derivation"):
        lines.append("  derivation: " + ", ".join(f"{k} {v}" for k, v in g["derivation"].items()))
    if st.get("events"):
        lines.append(
            f"  events: {st['events']}  prompts: {st['prompts_sent']}  replies: "
            f"{st['replies_received']}  tool calls: {st['tool_calls']}  errors: {st['errors']}  "
            f"interrupts: {st['interrupts']}  compactions: {st['compactions']}  "
            f"wall: {st['wall_seconds'] // 60}m"
        )
        if st["tool_mix"]:
            lines.append("  tool mix: " + ", ".join(f"{k} {v}" for k, v in st["tool_mix"].items()))
    else:
        lines.append("  events: 0")
    if d.get("usage"):
        lines.append(_fmt_usage(d["usage"]))
    return "\n".join(lines)


def run(root: pathlib.Path, as_json: bool = False) -> list[dict]:
    files = _walk(root)
    results = []
    for f in files:
        try:
            results.append(probe_file(f))
        except OSError as e:  # unreadable file: report, keep going
            results.append({"path": str(f), "error": str(e)})
    if as_json:
        print(json.dumps(results, indent=1, ensure_ascii=False))
        return results
    if not files:
        print(f"no transcripts under {root}")
        return results
    for r in results:
        print(r.get("error") and f"{r['path']}\n  ERROR {r['error']}" or format_probe(r))
        print()
    by = Counter(r.get("harness", "error") for r in results)
    print(f"{len(results)} files: " + ", ".join(f"{k} {v}" for k, v in by.most_common()))
    return results
