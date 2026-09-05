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
    if root.is_file():
        return [root]
    # Codex keeps `sessions/YYYY/MM/DD/rollout-*.jsonl`; Claude Code keeps
    # `projects/<slug>/<uuid>.jsonl` plus subagent sidecars. A recursive glob covers both;
    # the probe reports per file, so sidecars are visible rather than silently merged.
    return sorted(p for p in root.rglob("*.jsonl") if p.is_file())


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
    d["unknown_types"] = {}
    return d


def probe_file(path: pathlib.Path) -> dict:
    """Everything the probe knows about one file, as plain data."""
    path = pathlib.Path(path)
    harness = dg.detect_harness(path)
    out: dict = {"path": str(path), "harness": harness, "bytes": path.stat().st_size}
    if harness == "codex":
        from . import codex

        s = codex.scan(path)
        events, derivation = codex._derive(s)
        out["diagnostics"] = dict(s.diagnostics, derivation=derivation)
        out["meta"] = s.meta
        out["usage"] = s.usage
    else:
        out["diagnostics"] = _claude_code_summary(path)
        events = dg.load_claude_code_events(path)
    out["stats"] = dg.stats(events)
    return out


def _fmt_usage(u: dict) -> str:
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
            for k in ("cli_version", "history_mode", "model", "cwd", "git_branch", "source")
            if meta.get(k)
        ]
        lines.append("  meta: " + "  ".join(bits))
    lines.append(
        f"  lines: {g['lines']}  records: {g['records']}  malformed: {g['malformed_lines']}  "
        f"partial trailing line: {'yes' if g['partial_trailing_line'] else 'no'}"
    )
    lines.append(
        f"  no timestamp: {g['no_timestamp']}  bad timestamp: {g.get('bad_timestamp', 0)}  "
        f"first: {g['first_ts']}  last: {g['last_ts']}"
    )
    lines.append("  types: " + ", ".join(f"{k} {v}" for k, v in g["types"].items()))
    if g.get("payload_types"):
        lines.append(
            "  payload types: " + ", ".join(f"{k} {v}" for k, v in g["payload_types"].items())
        )
    unk = dict(g.get("unknown_types") or {})
    unk.update(g.get("unknown_payload_types") or {})
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
        print(f"no *.jsonl under {root}")
        return results
    for r in results:
        print(r.get("error") and f"{r['path']}\n  ERROR {r['error']}" or format_probe(r))
        print()
    by = Counter(r.get("harness", "error") for r in results)
    print(f"{len(results)} files: " + ", ".join(f"{k} {v}" for k, v in by.most_common()))
    return results
