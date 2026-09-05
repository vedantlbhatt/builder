"""Run the analysis: digest → `claude -p` → validated JSON.

Why `claude -p` and not the API: the user's own Claude Code subscription pays for it,
nothing leaves the machine except to Anthropic under the user's existing agreement, and
there is no key to manage. `--tools ""` and a replaced system prompt keep the context
to the digest alone (MEASURED: 1.6k tokens of overhead versus 41k with the default
Claude Code system prompt, and the default prompt would also let the model run tools).
"""

from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import shutil
import subprocess
import uuid

from . import digest as dg
from . import prompt as pr

HERE = pathlib.Path(__file__).resolve().parent
SCHEMA_PATH = HERE / "schema.json"
DEFAULT_MODEL = os.environ.get("BUILDER_ANALYSIS_MODEL", "sonnet")
TIMEOUT_S = 480  # MEASURED: 150 s for a 33 KB digest on sonnet, five internal turns


class AnalysisError(RuntimeError):
    pass


def load_schema() -> dict:
    if not SCHEMA_PATH.exists():
        raise AnalysisError(f"{SCHEMA_PATH} missing — run `make gen`")
    schema = json.loads(SCHEMA_PATH.read_text())
    # The CLI's validator resolves `$schema` as a reference and has no draft-2020-12
    # meta-schema registered: it rejects the whole document with "no schema with key or
    # ref". The keywords we use are identical across drafts, so drop the header.
    schema.pop("$schema", None)
    schema.pop("$comment", None)
    return schema


def call_claude(
    system: str, user: str, schema: dict, model: str = DEFAULT_MODEL
) -> tuple[dict, dict]:
    """Returns (structured_output, envelope)."""
    exe = shutil.which("claude")
    if not exe:
        raise AnalysisError("claude CLI not found on PATH")
    env = dict(os.environ)
    # Never attach to the caller's session: inside Claude Code these are set and a nested
    # `claude -p` would append its turn to the CURRENT transcript.
    for k in ("CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID", "CLAUDE_CODE_CHILD_SESSION"):
        env.pop(k, None)
    cmd = [
        exe,
        "-p",
        user,
        "--output-format",
        "json",
        "--json-schema",
        json.dumps(schema),
        "--system-prompt",
        system,
        "--tools",
        "",
        "--model",
        model,
        "--session-id",
        str(uuid.uuid4()),
    ]
    try:
        # stdin must be closed explicitly: with an inherited pipe the CLI waits for piped
        # input, warns after 3 s, and treats the prompt as incomplete.
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_S,
            env=env,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired as e:
        raise AnalysisError(f"claude -p timed out after {TIMEOUT_S}s") from e
    if proc.returncode != 0:
        raise AnalysisError(f"claude -p exit {proc.returncode}: {proc.stderr[-800:]}")
    try:
        env_out = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise AnalysisError(f"claude -p returned non-JSON: {proc.stdout[:300]}") from e
    if env_out.get("is_error"):
        raise AnalysisError(f"claude -p error: {env_out.get('result')}")
    so = env_out.get("structured_output")
    if not isinstance(so, dict):
        # Older CLIs put the JSON in `result` as a string.
        try:
            so = json.loads(env_out.get("result", ""))
        except json.JSONDecodeError as e:
            raise AnalysisError("no structured_output in claude -p response") from e
    return so, env_out


def _verify_excerpts(analysis: dict, digest_text: str) -> int:
    """Drop decision patterns whose excerpt is not a verbatim substring. Returns drops."""
    kept, dropped = [], 0
    norm = " ".join(digest_text.split())
    for p in analysis.get("decision_patterns") or []:
        ex = " ".join(str(p.get("prompt_excerpt", "")).split()).strip("…. ")
        if ex and ex in norm:
            kept.append(p)
        else:
            dropped += 1
    analysis["decision_patterns"] = kept
    return dropped


def analyze(
    transcript: pathlib.Path,
    start: float | None = None,
    end: float | None = None,
    meta: dict | None = None,
    model: str = DEFAULT_MODEL,
    budget: int = dg.DEFAULT_BUDGET,
) -> dict:
    schema = load_schema()
    d = dg.build(transcript, start, end, meta, budget)
    system = pr.SYSTEM
    user = pr.user_message(d["text"], d["coverage"])
    analysis, envelope = call_claude(system, user, schema, model)

    analysis["analysis_version"] = schema.get("x-version", 1) if isinstance(schema, dict) else 1
    # The envelope lists every model the CLI touched, including a small one it uses for
    # bookkeeping (MEASURED: haiku with 15 output tokens beside sonnet with 16,435). The
    # analyst is the one that wrote the output tokens.
    usage = envelope.get("modelUsage") or {}
    analysis["model"] = (
        max(usage, key=lambda k: usage[k].get("outputTokens", 0)) if usage else model
    )
    analysis["generated_at"] = dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    analysis["digest_hash"] = d["hash"]
    analysis["digest_coverage"] = d["coverage"]
    dropped = _verify_excerpts(analysis, d["text"])

    return {
        "analysis": analysis,
        "stats": d["stats"],
        "digest_chars": len(d["text"]),
        "digest_events": d["events"],
        "excerpts_dropped": dropped,
        "cost_usd": envelope.get("total_cost_usd"),
        "duration_ms": envelope.get("duration_ms"),
    }
