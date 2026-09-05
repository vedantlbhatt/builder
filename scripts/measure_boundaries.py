#!/usr/bin/env python3
"""Measure what the v2 session-boundary rules would do to a Claude Code corpus.

This is ALSO the reference implementation of those rules, in the same spirit as the strip
conformance fixtures: the Swift sessionizer must produce identical cuts on the fixture
transcripts under spec/fixtures/boundaries/. Read docs/session-boundaries.md first.

    scripts/measure_boundaries.py ~/.claude/projects          # whole corpus
    scripts/measure_boundaries.py path/to/one.jsonl --json    # one file, machine-readable

Rules (each carries the Tuning constant it reads):
  1. idle_gap        gap between consecutive records > TAU_SESSION (900s).  Unchanged.
                     The boundary gap is credited (capped) and ended_at extended by it.
  2. human_returned  a presence signal arrives after >= TAU_RETURN_SPLIT (7200s) of
                     autonomous time. The human came back to a still-running agent: that
                     is a new sitting, so the run is finalized and a new session begins.
  3. day_boundary    the 04:00 local day boundary falls inside a gap while the run is
                     autonomous (no presence for > TAU_AUTONOMOUS). Attended late nights
                     are never split — that rule stands — but a robot running through the
                     night credits hours to each day it ran, and cannot extend a streak.

Presence signals — evidence a human is at the keyboard:
  typed prompt      user record, promptSource == "typed"  (local)  or
                                 promptSource == "sdk" && origin.kind == "human"  (remote
                                 sessions started from the web/phone; MEASURED on a remote
                                 transcript: all 9 human prompts carried this shape and
                                 zero carried "typed")
  interrupt         user record whose text is exactly the harness sentinel
                    "[Request interrupted by user" (Escape / stop)
  human file edit   attachment.type == "edited_text_file"
Only records with a timestamp participate; none are imputed (see Tuning.swift).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import sys

TAU_SESSION = 900.0
ACTIVE_GAP_CAP = 120.0
TAU_AUTONOMOUS = 1800.0  # after this long without presence the agent is on its own
TAU_RETURN_SPLIT = 7200.0  # presence after this much autonomy starts a NEW session
DAY_BOUNDARY_HOUR = 4
INTERRUPT_PREFIX = "[Request interrupted by user"


def parse_ts(s: str | None) -> float | None:
    if not s:
        return None
    try:
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def local_day(ts: float, tz: dt.tzinfo) -> str:
    d = dt.datetime.fromtimestamp(ts, tz) - dt.timedelta(hours=DAY_BOUNDARY_HOUR)
    return d.strftime("%Y-%m-%d")


def next_day_boundary_after(ts: float, tz: dt.tzinfo) -> float:
    """The first 04:00 local strictly after ts, as a unix timestamp."""
    d = dt.datetime.fromtimestamp(ts, tz)
    b = d.replace(hour=DAY_BOUNDARY_HOUR, minute=0, second=0, microsecond=0)
    if b <= d:
        b += dt.timedelta(days=1)
    return b.timestamp()


def classify(rec: dict) -> tuple[str, bool]:
    """(kind, is_presence). kind is informational; presence is what the rules read."""
    t = rec.get("type")
    if t == "user":
        msg = rec.get("message") or {}
        content = msg.get("content")
        ps = rec.get("promptSource")
        origin = (rec.get("origin") or {}).get("kind")
        is_meta = bool(rec.get("isMeta"))
        text = None
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            texts = [
                b.get("text", "")
                for b in content
                if isinstance(b, dict) and b.get("type") == "text"
            ]
            text = "\n".join(texts) if texts else None
        if not is_meta and (ps == "typed" or (ps == "sdk" and origin == "human")):
            return "prompt", True
        if text is not None and text.startswith(INTERRUPT_PREFIX):
            return "interrupt", True
        return "user_other", False
    if t == "attachment":
        if (rec.get("attachment") or {}).get("type") == "edited_text_file":
            return "human_edit", True
        return "attachment", False
    if t == "assistant":
        return "assistant", False
    return t or "unknown", False


def load_records(path: pathlib.Path, extra=None) -> list[dict]:
    """Timestamped records, thinned to what the rules read.

    `extra`, when given, is called with each raw record and its 0-based line index and
    returns a dict merged into the thin record. It exists so `capture/` can carry ids and
    token usage through this one parse instead of reading the file a second time with a
    second partial-line rule; the sessionizer never reads anything it adds.
    """
    out = []
    with path.open("rb") as f:
        for i, line in enumerate(f):
            if not line.endswith(b"\n"):
                break  # partial trailing line: NEVER consumed (see state_schema.sql)
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = parse_ts(r.get("timestamp"))
            if ts is None:
                continue
            kind, presence = classify(r)
            rec = {"ts": ts, "kind": kind, "presence": presence, "cwd": r.get("cwd")}
            if extra is not None:
                rec.update(extra(r, i))
            out.append(rec)
    return out


def sessionize(
    records: list[dict],
    tz: dt.tzinfo,
    tau: float = TAU_SESSION,
    cap: float = ACTIVE_GAP_CAP,
    tau_autonomous: float = TAU_AUTONOMOUS,
    tau_return: float = TAU_RETURN_SPLIT,
) -> list[dict]:
    """Cut one pool of timestamped records into v2 sessions."""
    recs = sorted(records, key=lambda r: r["ts"])
    if not recs:
        return []

    sessions: list[dict] = []
    cur: dict | None = None
    last_presence: float | None = None  # persists across a human_returned cut? No — see below.

    def open_session(r: dict) -> dict:
        return {
            "started_at": r["ts"],
            "ended_at": r["ts"],
            "active": 0.0,
            "attended": 0.0,
            "autonomous": 0.0,
            "prompts": 0,
            "presence": 0,
            "records": 0,
            "end_reason": "still_running",
            "longest_autonomous_run": 0.0,
        }

    def close(s: dict, reason: str, trailing: float) -> None:
        s["ended_at"] += trailing
        s["end_reason"] = reason
        sessions.append(s)

    run_start: float | None = None  # when the current autonomous stretch began

    for i, r in enumerate(recs):
        if cur is None:
            cur = open_session(r)
            last_presence = r["ts"] if r["presence"] else None
            run_start = None if r["presence"] else r["ts"]
        else:
            prev = recs[i - 1]
            gap = r["ts"] - prev["ts"]
            credit = min(gap, cap)

            # Is the human absent at the START of this gap?
            since_presence = None if last_presence is None else prev["ts"] - last_presence
            autonomous = since_presence is None or since_presence > tau_autonomous

            # Rule 1: idle gap. The boundary gap is credited like any other gap — capped,
            # to whichever clock was running when it began — and ended_at is extended by
            # the same amount so active can never exceed elapsed. Credit is a property of
            # the POOL, not of where the cut lands: without this, merging two sessions
            # would recover up to `cap` seconds and total active time would depend on tau
            # (MEASURED in the Swift engine before that fix: 4.5 h of drift across the
            # threshold range).
            if gap > tau:
                cur["active"] += credit
                if autonomous:
                    cur["autonomous"] += credit
                else:
                    cur["attended"] += credit
                close(cur, "idle_gap", credit)
                cur = open_session(r)
                last_presence = r["ts"] if r["presence"] else None
                run_start = None if r["presence"] else r["ts"]
                cur["records"] += 1
                cur["prompts"] += r["kind"] == "prompt"
                cur["presence"] += r["presence"]
                continue

            # Rule 3: the day boundary falls in this gap while autonomous.
            boundary = next_day_boundary_after(prev["ts"], tz)
            # `<=`: a record stamped exactly 04:00:00 begins the new day. MEASURED on the
            # robot fixture: 20-second cadence from a round hour lands a record on the
            # boundary itself, and a strict comparison never split a 30-hour run.
            if autonomous and boundary <= r["ts"]:
                # credit up to the boundary to the old session, remainder to the new one
                before = min(boundary - prev["ts"], cap)
                cur["active"] += before
                cur["autonomous"] += before
                # The old session ends AT the boundary, not at its last record: it was
                # credited up to the boundary, and active must not exceed elapsed.
                cur["ended_at"] = boundary
                close(cur, "day_boundary", 0.0)
                cur = open_session(r)
                cur["started_at"] = boundary
                # last_presence carries over: the human is still absent
                run_start = run_start or prev["ts"]
                after = max(0.0, min(r["ts"] - boundary, cap - before)) if gap <= cap else 0.0
                cur["active"] += after
                cur["autonomous"] += after
                cur["ended_at"] = r["ts"]
                cur["records"] += 1
                cur["prompts"] += r["kind"] == "prompt"
                cur["presence"] += r["presence"]
                if r["presence"]:
                    last_presence = r["ts"]
                    run_start = None
                continue

            # Rule 2: the human returned after a long autonomous run. A run that never had
            # a presence signal (a scheduled agent, a robot from the first record) measures
            # its autonomy from its own start, so the first human to sit down at it opens a
            # new session rather than being absorbed into the robot's.
            autonomy_len = (
                since_presence
                if since_presence is not None
                else (prev["ts"] - run_start if run_start is not None else None)
            )
            if r["presence"] and autonomous and autonomy_len is not None and autonomy_len >= tau_return:
                cur["active"] += credit
                cur["autonomous"] += credit
                # ended_at advances by the credit, as for idle_gap: the run was credited
                # for the gap, so it must be seen to have lasted that long.
                close(cur, "human_returned", credit)
                cur = open_session(r)
                last_presence = r["ts"]
                run_start = None
                cur["records"] += 1
                cur["prompts"] += r["kind"] == "prompt"
                cur["presence"] += r["presence"]
                continue

            # Ordinary continuation: credit the gap to whichever clock is running.
            cur["active"] += credit
            if autonomous:
                cur["autonomous"] += credit
                if run_start is None:
                    run_start = prev["ts"]
                cur["longest_autonomous_run"] = max(
                    cur["longest_autonomous_run"], r["ts"] - run_start
                )
            else:
                cur["attended"] += credit
            cur["ended_at"] = r["ts"]
            if r["presence"]:
                last_presence = r["ts"]
                run_start = None

        cur["records"] += 1
        cur["prompts"] += r["kind"] == "prompt"
        cur["presence"] += r["presence"]

    if cur is not None:
        close(cur, "still_running", 0.0)
    return sessions


def presence_intervals(records: list[dict], tau: float = TAU_SESSION) -> list[float]:
    """Presence-to-presence intervals during CONTINUOUS activity (no idle gap between)."""
    recs = sorted(records, key=lambda r: r["ts"])
    out, last_p = [], None
    for i, r in enumerate(recs):
        if i and r["ts"] - recs[i - 1]["ts"] > tau:
            last_p = None  # a session boundary resets the clock
        if r["presence"]:
            if last_p is not None:
                out.append(r["ts"] - last_p)
            last_p = r["ts"]
    return out


def pct(xs: list[float], q: float) -> float:
    if not xs:
        return float("nan")
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))]


def fmt(sec: float) -> str:
    sec = int(sec)
    if sec >= 3600:
        return f"{sec // 3600}h {(sec % 3600) // 60:02d}m"
    return f"{sec // 60}m {sec % 60:02d}s"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("root", help="~/.claude/projects, a project dir, or one .jsonl")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--tz", default=None, help="IANA zone; default: system local")
    args = ap.parse_args()

    tz = dt.datetime.now().astimezone().tzinfo
    if args.tz:
        import zoneinfo

        tz = zoneinfo.ZoneInfo(args.tz)

    root = pathlib.Path(args.root).expanduser()
    files = (
        [root]
        if root.is_file()
        else sorted(
            p
            for p in root.rglob("*.jsonl")
            if "/" not in str(p.relative_to(root).parent.as_posix()).strip(".")
        )
    )
    # Root transcripts only: <projectdir>/<uuid>.jsonl. Sidecars inherit the parent's session.
    if root.is_dir():
        files = [p for p in root.rglob("*.jsonl") if len(p.relative_to(root).parts) == 2] or files

    # Pool per project directory. The engine pools by RESOLVED repository (git common
    # dir, worktrees folded), which this script cannot do without git; the project dir is
    # the closest stand-in. Never by raw cwd: it varies within one conversation as the
    # agent `cd`s around (MEASURED: 5 distinct cwds in a single 30-minute transcript).
    pools: dict[str, list[dict]] = {}
    for f in files:
        recs = load_records(f)
        key = f.parent.name if root.is_dir() else f.stem
        pools.setdefault(key, []).extend(recs)

    all_sessions: list[dict] = []
    all_intervals: list[float] = []
    for key, recs in pools.items():
        for s in sessionize(recs, tz):
            s["pool"] = key
            all_sessions.append(s)
        all_intervals.extend(presence_intervals(recs))

    # Sensitivity: how many extra cuts do rules 2 and 3 make at candidate thresholds?
    grid = {}
    for ta in (600, 1200, 1800, 3600):
        for tr in (3600, 7200, 14400):
            n = sum(
                len(sessionize(recs, tz, tau_autonomous=ta, tau_return=tr))
                for recs in pools.values()
            )
            grid[f"tau_autonomous={ta},tau_return={tr}"] = n
    baseline = sum(
        len(sessionize(recs, tz, tau_autonomous=1e12, tau_return=1e12)) for recs in pools.values()
    )

    report = {
        "files": len(files),
        "pools": len(pools),
        "records": sum(len(r) for r in pools.values()),
        "sessions_rule1_only": baseline,
        "sessions_v2_default": len(all_sessions),
        "sessions_by_end_reason": {
            k: sum(1 for s in all_sessions if s["end_reason"] == k)
            for k in ("idle_gap", "human_returned", "day_boundary", "still_running")
        },
        "presence_interval_seconds": {
            "n": len(all_intervals),
            "p50": pct(all_intervals, 0.5),
            "p90": pct(all_intervals, 0.9),
            "p98": pct(all_intervals, 0.98),
            "p99": pct(all_intervals, 0.99),
            "max": max(all_intervals) if all_intervals else None,
            "over_1800": sum(1 for x in all_intervals if x > 1800),
            "over_7200": sum(1 for x in all_intervals if x > 7200),
        },
        "autonomous_seconds_total": sum(s["autonomous"] for s in all_sessions),
        "attended_seconds_total": sum(s["attended"] for s in all_sessions),
        "longest_autonomous_run_seconds": max(
            (s["longest_autonomous_run"] for s in all_sessions), default=0
        ),
        "sessions_with_zero_presence": sum(1 for s in all_sessions if s["presence"] == 0),
        "sensitivity_session_counts": grid,
        "sessions": all_sessions if len(all_sessions) <= 50 else all_sessions[:50],
    }

    if args.json:
        json.dump(report, sys.stdout, indent=1, default=str)
        return 0

    p = report["presence_interval_seconds"]
    print(
        f"files {report['files']}  pools {report['pools']}  timestamped records {report['records']:,}"
    )
    print(
        f"sessions: rule 1 only {baseline}  ·  v2 default {len(all_sessions)}  {report['sessions_by_end_reason']}"
    )
    print(
        f"presence→presence during activity (n={p['n']}): p50 {fmt(p['p50'])}  p90 {fmt(p['p90'])}  "
        f"p98 {fmt(p['p98'])}  p99 {fmt(p['p99'])}  max {fmt(p['max'] or 0)}  >30m {p['over_1800']}  >2h {p['over_7200']}"
    )
    print(
        f"attended {fmt(report['attended_seconds_total'])}  autonomous {fmt(report['autonomous_seconds_total'])}  "
        f"longest autonomous run {fmt(report['longest_autonomous_run_seconds'])}  zero-presence sessions {report['sessions_with_zero_presence']}"
    )
    print("sensitivity (session count):")
    for k, v in grid.items():
        print(f"  {k:36s} {v}")
    print("\nsessions:")
    for s in all_sessions[:50]:
        st = dt.datetime.fromtimestamp(s["started_at"], tz).strftime("%a %d %b %H:%M")
        print(
            f"  {st}  {fmt(s['ended_at'] - s['started_at']):>9}  active {fmt(s['active']):>8}  "
            f"attended {fmt(s['attended']):>8}  auto {fmt(s['autonomous']):>8}  prompts {s['prompts']:3d}  "
            f"presence {s['presence']:3d}  {s['end_reason']}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
