"""`python -m capture pair` and `python -m capture sync`."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import shutil
import socket
import sys
import time
import zoneinfo

from . import CLIENT_VERSION, ROOT, harnesses, repo, sessions
from . import client as cl
from .discover import iter_root_transcripts
from .tuning import LIVE_UPLOAD_MIN_INTERVAL_SEC, PAIR_TIMEOUT_SEC

DEFAULT_ROOT = "~/.claude/projects"


def _server(arg: str | None) -> str:
    return (arg or os.environ.get("BUILDER_API_URL") or cl.DEFAULT_SERVER).rstrip("/")


def _tz(arg: str | None) -> dt.tzinfo:
    """The zone the 04:00 rule runs in. A cloud container is UTC; set `BUILDER_TZ` (or
    `--tz`) to your home zone or a robot's overnight hours land on the wrong day."""
    name = arg or os.environ.get("BUILDER_TZ") or os.environ.get("TZ")
    if name:
        try:
            return zoneinfo.ZoneInfo(name)
        except (zoneinfo.ZoneInfoNotFoundError, ValueError):
            print(f"warning: unknown time zone {name!r}; using the system zone", file=sys.stderr)
    return dt.datetime.now().astimezone().tzinfo or dt.UTC


def _box(code: str) -> list[str]:
    inner = f"    {code}    "
    bar = "─" * len(inner)
    blank = " " * len(inner)
    return [f"┌{bar}┐", f"│{blank}│", f"│{inner}│", f"│{blank}│", f"└{bar}┘"]


# ----------------------------------------------------------------------------- pair


def cmd_pair(a: argparse.Namespace) -> int:
    server = _server(a.server)
    c = cl.Client(server)

    if a.resume:
        return _resume_pending(c, quiet=False)

    if cl.capture_key() is not None:
        print("\n  BUILDER_CAPTURE_KEY is set: `sync` uses it and needs no pairing. Pairing anyway.")
    existing = cl.read_json(cl.credentials_path())
    if existing and existing.get("refresh_token"):
        print("\n  Already paired. Pairing again replaces the stored tokens.")
    machine_id = cl.machine_identity(existing)
    label = (
        a.label or os.environ.get("BUILDER_DEVICE_LABEL") or f"Claude Code ({socket.gethostname()})"
    )

    start = c.device_start(machine_id, label)
    pending = {
        "server": server,
        "machine_id": machine_id,
        "label": label,
        "device_code": start["device_code"],
        "user_code": start["user_code"],
        "interval": int(start.get("interval", 5)),
        "expires_at": time.time() + int(start.get("expires_in", PAIR_TIMEOUT_SEC)),
    }
    cl.write_private_json(cl.pending_path(), pending)

    deep_link = f"builder://pair?code={start['user_code']}"
    print()
    print("  Open Builder on your phone → Settings → Scan code, and enter:")
    print()
    for line in _box(start["user_code"]):
        print(f"      {line}")
    print()
    print(f"  or open {start.get('verification_uri', server + '/pair')} where you are signed in.")
    print()
    print(f"  Deep link:  {deep_link}")
    print()
    if a.no_wait:
        print("  Not waiting. `python -m capture sync` (or `pair --resume`) completes the")
        print("  pairing once you approve; the code expires in 15 minutes.")
        return 0
    print("  Waiting for approval (code expires in 15 minutes)…")
    try:
        c.await_pairing(pending, pending["interval"])
    except cl.PairingTimedOut as e:
        print(f"\n  {e}")
        return 1
    print(f"\n  Paired — this machine is linked as “{label}”. Run `python -m capture sync`.")
    return 0


def _resume_pending(c: cl.Client, quiet: bool) -> int:
    """Finish a `pair --no-wait` with a single poll. Returns 0 when paired."""
    pending = cl.read_json(cl.pending_path())
    if not pending or not pending.get("device_code"):
        if not quiet:
            print("  No pending pairing. Run `python -m capture pair`.")
        return 1
    if time.time() > float(pending.get("expires_at", 0)):
        cl.pending_path().unlink(missing_ok=True)
        if not quiet:
            print("  The pending pairing code expired. Run `python -m capture pair` again.")
        return 1
    try:
        r = c.device_poll_once(pending["device_code"])
    except cl.HTTPFailure as e:
        if not quiet:
            print(f"  Pairing poll failed: {e}")
        return 1
    if r.get("status") == "ok" and r.get("access_token"):
        c.complete_pairing(pending, r)
        if not quiet:
            print(f"  Paired as “{pending.get('label')}”.")
        return 0
    if not quiet:
        print(f"  Still waiting for approval of code {pending.get('user_code')}.")
    return 2


# ----------------------------------------------------------------------------- sync


def _load_state() -> dict:
    return cl.read_json(cl.state_path()) or {"live": {}, "analysis": {}}


def _analysis_for(s: sessions.Session, state: dict, quiet: bool) -> dict | None:
    """Opt-in: `analysis.run.analyze` through the user's own `claude`, once per final
    session, cached in the state file so a re-run of a hook never pays twice."""
    cache = state.setdefault("analysis", {})
    cached = cache.get(s.client_session_id)
    if isinstance(cached, dict):
        return cached
    if not shutil.which("claude"):
        return None
    from analysis import run as rn

    meta = {
        "end_reason": s.end_reason,
        "attended_seconds": round(s.attended),
        "autonomous_seconds": round(s.autonomous),
    }
    if s.repo is not None and s.repo.display_name:
        meta["repo"] = s.repo.display_name
    try:
        res = rn.analyze(s.transcript_path, s.records[0]["ts"], s.last_record_ts, meta)
    except rn.AnalysisError as e:
        if not quiet:
            print(f"  analysis skipped for {s.client_session_id[:8]}: {e}", file=sys.stderr)
        return None
    cache[s.client_session_id] = res["analysis"]
    return res["analysis"]


def discover_sources(a: argparse.Namespace) -> tuple[list, list, list]:
    """Every transcript this machine holds, loaded. Returns (sources, root transcripts, other stores).

    Shared by `sync` and `narrative` so the two cannot cut different corpora. A page that
    described a set of sessions the phone never received would be the same class of bug as
    a wrong number, and harder to see.
    """
    transcripts = iter_root_transcripts(pathlib.Path(a.root).expanduser())
    sources = [sessions.load_source(t) for t in transcripts]
    # Every other tool on this machine, through the same records and the same cut
    # (`capture/harnesses.py`). Opt-out rather than opt-in: a person who installed Builder
    # to see their sessions means all of them, and a store that is not there costs a
    # `Path.exists()`. One unreadable session is skipped, never fatal.
    # Aider writes into the REPOSITORY you ran it in, so the repositories this machine's
    # own transcripts resolved to are where to look. A guess at where somebody keeps code
    # (`~/src`, `~/work`) walks directories nobody asked us to read.
    repo_roots = sorted(
        {
            ident.common_root
            for src in sources
            for r in src.records
            if (ident := repo.identity_for(r.get("cwd"))) is not None and ident.common_root
        }
    )
    other = (
        []
        if getattr(a, "no_other_harnesses", False)
        else harnesses.discover(repo_roots=repo_roots)
    )
    for store in other:
        try:
            sources.append(harnesses.load(store))
        except Exception as e:  # noqa: BLE001 - one bad store must not stop the sync
            if not getattr(a, "quiet", False):
                print(f"  skipped {store.harness} {store.path.name}: {e}", file=sys.stderr)
    return sources, transcripts, other


def build_payloads(a: argparse.Namespace, now: float | None = None) -> tuple[list[dict], dict]:
    """Discover, sessionize, and build every uploadable payload. Returns (payloads, stats)."""
    root = pathlib.Path(a.root).expanduser()
    tz = _tz(a.tz)
    now = time.time() if now is None else now
    sources, transcripts, other = discover_sources(a)
    fit_report: dict = {}
    cut = sessions.sessionize_sources(
        sources, tz, now=now, finalize_open=a.finalize, tau=getattr(a, "tau", "auto"),
        report=fit_report,
    )

    creds = cl.read_json(cl.credentials_path())
    machine_id = cl.machine_identity(creds)
    excluded = repo.excluded_origins()
    state = _load_state()

    payloads: list[dict] = []
    stats = {
        "transcripts": len(transcripts),
        "other_harness_sessions": len(other),
        "harnesses": sorted({s.transcript.harness for s in sources}),
        "records": sum(len(s.records) for s in sources),
        "sessions": len(cut),
        "open_skipped": 0,
        "not_visible": 0,
        "excluded": 0,
        "live": 0,
        "final": 0,
        "with_analysis": 0,
        "tau": fit_report.get("tau"),
        "tau_fit": (
            sessions.mb.describe_fit(fit_report["fit"]) if fit_report.get("fit") else None
        ),
    }
    for s in cut:
        if s.repo is not None and s.repo.identity in excluded:
            stats["excluded"] += 1
            continue
        if s.state == "live" and not a.live:
            stats["open_skipped"] += 1
            continue
        analysis = None
        if a.analyze and s.state == "final":
            analysis = _analysis_for(s, state, a.quiet)
        p = sessions.build_payload(s, tz, machine_id, CLIENT_VERSION, now, analysis)
        if not p["visible"]:
            stats["not_visible"] += 1
            continue
        stats[s.state] += 1
        if analysis is not None:
            stats["with_analysis"] += 1
        payloads.append(p)
    return payloads, {"state": state, **stats}


def cmd_narrative(a: argparse.Namespace) -> int:
    """Write the "how you work" page from this machine's transcripts and upload it.

    It is its own command rather than a flag on `sync` because it costs a model call and
    describes the whole corpus: running it on every sync would pay for a document that has
    not changed. `--dry-run` prints it and sends nothing, which is also the only way to
    read it without a server.

    The work itself lives in `analysis`, not here: this command sessionizes with the same
    reference cut `sync` uses, hands the sessions to `analysis.patterns` and
    `analysis.profile`, and posts what `analysis.narrative` returns.
    """
    key = cl.capture_key(a.key)
    tz = _tz(a.tz)
    now = time.time()

    from analysis import narrative as nar
    from analysis import patterns as pat
    from analysis import profile as pf

    sources, _, _ = discover_sources(a)
    cut = [
        s
        for s in sessions.sessionize_sources(sources, tz, now=now, tau=getattr(a, "tau", "auto"))
        if s.state == "final"
    ]
    facts, events, roots = [], [], []
    for s in cut:
        offset = dt.datetime.fromtimestamp(s.started_at, tz).utcoffset() or dt.timedelta(0)
        minutes = int(offset.total_seconds() // 60)
        roots.append(s.repo.common_root if s.repo else None)
        facts.append(
            pf.session_fact_from_events(
                session_id=s.client_session_id,
                events=s.events,
                started_at=s.started_at,
                ended_at=s.ended_at,
                attended_seconds=s.attended,
                autonomous_seconds=s.autonomous,
                tz_offset_minutes=minutes,
                output_tokens_by_model={},
                unattended=s.presence == 0,
            )
        )
        events.append(
            pat.SessionEvents(
                session_id=s.client_session_id,
                started_at=s.started_at,
                ended_at=s.ended_at,
                active_seconds=s.attended + s.autonomous,
                attended_seconds=s.attended,
                tz_offset_minutes=minutes,
                events=s.events,
            )
        )

    # Commits from `git log` over each session's own window, first-claim across the
    # overlaps, exactly as `python -m analysis narrative` does it. Without this the two
    # commands describe the same corpus with different commit numbers, which is the same
    # bug as one wrong number.
    facts = pf.attribute_commits(facts, roots, repo.commits_in)

    found = pat.findings(events)
    if not a.quiet:
        print(
            f"{len(cut)} final session(s), {len(found)} comparative finding(s) cleared "
            f"both bars (both sides need {pat.MIN_GROUP}, the gap needs {pat.MIN_LIFT}x).",
            file=sys.stderr,
        )

    kw = {"model": a.model} if a.model else {}
    doc = nar.write(profile=pf.corpus_profile(facts), findings=found, **kw)

    if a.dry_run:
        print(json.dumps(doc, indent=1, ensure_ascii=False))
        print(
            "\ndry run: nothing was sent. This document is prose about YOU, written on "
            "this machine from your own transcripts; read it before you upload it.",
            file=sys.stderr,
        )
        return 0

    c = cl.Client(_server(a.server), key=key)
    if key is None and cl.load_credentials() is None:
        if not a.quiet:
            print("Not paired. Run `python -m capture pair --server URL` first.")
        return 3
    c.put_narrative(doc)
    if not a.quiet:
        dropped = doc["invented_numbers_dropped"]
        print(
            "Uploaded your builder narrative."
            + (f" {dropped} claim(s) were dropped for citing a number nobody measured." if dropped else "")
        )
    return 0


def cmd_sync(a: argparse.Namespace) -> int:
    # Resolved first so a malformed key fails before any transcript is read.
    key = cl.capture_key(a.key)
    now = time.time()
    payloads, info = build_payloads(a, now)
    state = info.pop("state")

    summary = (
        f"{info['transcripts']} root transcript(s), {info['records']:,} timestamped records, "
        f"{info['sessions']} session(s): {info['final']} final, {info['live']} live"
        + (f", {info['open_skipped']} open (skipped; pass --live)" if info["open_skipped"] else "")
        + (f", {info['not_visible']} below the visibility floor" if info["not_visible"] else "")
        + (f", {info['excluded']} excluded" if info["excluded"] else "")
        + (f", {info['with_analysis']} with an analysis" if info["with_analysis"] else "")
        + (f"; auth: capture key {cl.key_prefix(key)}… (no pairing needed)" if key else "")
        + (f"; {info['tau_fit']}" if info.get("tau_fit") else "")
    )

    if a.dry_run:
        if not a.quiet:
            print(json.dumps({"sessions": payloads}, indent=1, sort_keys=True))
        print(f"\ndry run: {summary}.", file=sys.stderr)
        print(
            f"{len(payloads)} session(s) would be sent. Nothing was.\n"
            "Every key above is declared in privacy/upload-contract.json; "
            "capture/tests/test_contract.py walks the nested fields to prove it.",
            file=sys.stderr,
        )
        return 0

    c = cl.Client(_server(a.server), key=key)
    if key is None and cl.load_credentials() is None:
        rc = _resume_pending(c, quiet=a.quiet)
        if rc != 0:
            if not a.quiet:
                print("Not paired. Run `python -m capture pair --server URL` first.")
            return 3

    if not payloads:
        if not a.quiet:
            print(f"Nothing to sync: {summary}.")
        return 0

    try:
        known = c.known_hashes()
    except cl.HTTPFailure as e:
        if not a.quiet:
            print(f"warning: /v1/sync/known failed ({e}); sending everything", file=sys.stderr)
        known = {}

    live_state: dict = state.setdefault("live", {})
    to_send = []
    for p in payloads:
        if known.get(p["client_session_id"]) == p["content_hash"]:
            continue
        if p["state"] == "live":
            last = live_state.get(p["client_session_id"])
            if last:
                if now - float(last.get("at", 0)) < LIVE_UPLOAD_MIN_INTERVAL_SEC:
                    continue
                if last.get("hash") == p["content_hash"]:
                    continue
        to_send.append(p)

    if not to_send:
        cl.write_private_json(cl.state_path(), state)
        if not a.quiet:
            print(f"Already up to date: {summary}.")
        return 0

    result = c.upload(to_send)
    rejected_ids = {r.get("client_session_id") for r in result["rejected"]}
    for p in to_send:
        if p["client_session_id"] in rejected_ids:
            continue
        if p["state"] == "live":
            live_state[p["client_session_id"]] = {"at": now, "hash": p["content_hash"]}
        else:
            live_state.pop(p["client_session_id"], None)
    cl.write_private_json(cl.state_path(), state)

    if not a.quiet or result["rejected"]:
        print(f"  {summary}")
        print(f"  accepted   {result['accepted']}")
        print(f"  unchanged  {result['unchanged']}")
        print(f"  live       {sum(1 for p in to_send if p['state'] == 'live')}")
        if result["rejected"]:
            print(f"  rejected   {len(result['rejected'])}")
            for r in result["rejected"][:10]:
                print(f"    {str(r.get('client_session_id', ''))[:8]}  {r.get('reason')}")
    return 1 if result["rejected"] else 0


# ----------------------------------------------------------------------------- main


def make_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="python -m capture",
        description=f"Builder capture {CLIENT_VERSION} — upload Claude Code sessions from anywhere Python runs.",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser(
        "pair", help="RFC 8628 device flow; stores tokens in ~/.builder/credentials.json"
    )
    p.add_argument("--server", help="API base URL (or BUILDER_API_URL)")
    p.add_argument("--label", help="device label shown in the app")
    p.add_argument(
        "--no-wait", action="store_true", help="print the code and exit; sync completes it"
    )
    p.add_argument("--resume", action="store_true", help="poll a pending --no-wait pairing once")
    p.set_defaults(fn=cmd_pair)

    s = sub.add_parser("sync", help="sessionize the transcript store and upload")
    s.add_argument("--root", default=DEFAULT_ROOT, help=f"transcript root (default {DEFAULT_ROOT})")
    s.add_argument("--server", help="API base URL (or BUILDER_API_URL)")
    s.add_argument(
        "--key",
        help="capture key from Builder → Settings → Cloud capture (or BUILDER_CAPTURE_KEY); "
        "replaces pairing",
    )
    s.add_argument("--tz", help="IANA zone for the 04:00 day rule (or BUILDER_TZ / TZ)")
    s.add_argument(
        "--tau",
        default="auto",
        help="idle-gap threshold: 'auto' fits it to your presence intervals (v3; 900 s "
        "until there are 200 of them and the fit is bimodal), or a number of seconds",
    )
    s.add_argument("--dry-run", action="store_true", help="print the payloads; send nothing")
    s.add_argument("--live", action="store_true", help="also upload the open session as state=live")
    s.add_argument(
        "--finalize",
        action="store_true",
        help="treat the open session as ended now (SessionEnd hook: the container is going away)",
    )
    s.add_argument(
        "--analyze", action="store_true", help="attach an analysis via `claude -p` (opt-in)"
    )
    s.add_argument(
        "--no-other-harnesses",
        action="store_true",
        help="Claude Code only: skip Codex, Gemini CLI, Cline, opencode and Aider",
    )
    s.add_argument("--quiet", action="store_true", help="only print rejections and errors")
    s.set_defaults(fn=cmd_sync)

    n = sub.add_parser(
        "narrative",
        help="write the 'how you work' page from this machine's transcripts and upload it",
    )
    n.add_argument("--root", default=DEFAULT_ROOT, help=f"transcript root (default {DEFAULT_ROOT})")
    n.add_argument("--server", help="API base URL (or BUILDER_API_URL)")
    n.add_argument("--key", help="capture key (or BUILDER_CAPTURE_KEY); replaces pairing")
    n.add_argument("--tz", help="IANA zone for the 04:00 day rule (or BUILDER_TZ / TZ)")
    n.add_argument("--tau", default="auto", help="idle-gap threshold; see `sync --tau`")
    n.add_argument("--model", default=None, help="model for `claude -p` (default: sonnet)")
    n.add_argument("--dry-run", action="store_true", help="print the page; send nothing")
    n.add_argument(
        "--no-other-harnesses",
        action="store_true",
        help="Claude Code only: skip Codex, Gemini CLI, Cline, opencode and Aider",
    )
    n.add_argument("--quiet", action="store_true", help="only print errors")
    n.set_defaults(fn=cmd_narrative)
    return ap


def main(argv: list[str] | None = None) -> int:
    a = make_parser().parse_args(argv)
    try:
        return a.fn(a)
    except cl.NotPaired as e:
        print(str(e), file=sys.stderr)
        return 3
    except cl.HTTPFailure as e:
        print(str(e), file=sys.stderr)
        return 4
    except (cl.CaptureKeyRejected, cl.MalformedCaptureKey) as e:
        # One line, no retry: a hook that loops on a revoked key would fill the log with
        # the same sentence every turn and never get anywhere.
        print(str(e), file=sys.stderr)
        return 5
    except KeyboardInterrupt:
        return 130


__all__ = ["ROOT", "build_payloads", "main", "make_parser"]
