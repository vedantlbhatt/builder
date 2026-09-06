from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import pathlib
import sys
import time

from . import digest as dg


def _ts(s: str | None) -> float | None:
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp() if s else None


def main() -> int:
    ap = argparse.ArgumentParser(prog="python -m analysis")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("digest", "stats", "run"):
        p = sub.add_parser(name)
        p.add_argument("transcript")
        p.add_argument("--start")
        p.add_argument("--end")
        p.add_argument("--repo")
        p.add_argument("--budget", type=int, default=dg.DEFAULT_BUDGET)
        if name == "run":
            p.add_argument("--out")
            p.add_argument("--model", default=None)
    pf = sub.add_parser(
        "profile",
        help="corpus metrics over every root transcript in a directory (no model, read-only)",
    )
    pf.add_argument("path", nargs="?", default="~/.claude/projects")
    pf.add_argument("--facts-only", action="store_true")
    nr = sub.add_parser(
        "narrative",
        help="the 'how you work' page: profile + comparative findings through `claude -p`",
    )
    nr.add_argument("path", nargs="?", default="~/.claude/projects")
    nr.add_argument("--out")
    nr.add_argument("--model", default=None)
    nr.add_argument(
        "--findings-only",
        action="store_true",
        help="print the comparative findings and stop, without calling a model",
    )
    rp = sub.add_parser(
        "report",
        help="the whole builder report as one JSON document, exactly as it is uploaded",
    )
    rp.add_argument("path", nargs="?", default="~/.claude/projects")
    rp.add_argument(
        "--days",
        type=int,
        default=None,
        help=f"the window, and the length of each trend window (default {rp_default()})",
    )
    rp.add_argument("--out")
    ql = sub.add_parser(
        "quality", help="how long it takes you to get back to green, and what passed first try"
    )
    ql.add_argument("path", nargs="?", default="~/.claude/projects")
    ql.add_argument("--days", type=int, default=30, help="how far back to look (default 30)")
    co = sub.add_parser(
        "contributions",
        help="your commits by day, split by whether an agent was in the room",
    )
    co.add_argument("path", nargs="?", default="~/.claude/projects")
    co.add_argument("--days", type=int, default=60, help="how far back to look (default 60)")
    tr = sub.add_parser(
        "trends", help="how you build now against how you built before, you against you"
    )
    tr.add_argument("path", nargs="?", default="~/.claude/projects")
    tr.add_argument("--window", type=int, default=30, help="days per window (default 30)")
    ag = sub.add_parser(
        "agents",
        help="several agents at once: who ran, overlapping or one after another, and what landed",
    )
    ag.add_argument("path", nargs="?", default="~/.claude/projects")
    ag.add_argument("--days", type=int, default=30, help="how far back to look (default 30)")
    pb = sub.add_parser(
        "playbook",
        help="your own prompts that landed, against the ones that cost a round trip",
    )
    pb.add_argument("path", nargs="?", default="~/.claude/projects")
    pb.add_argument("--days", type=int, default=30, help="how far back to look (default 30)")
    pb.add_argument("--top", type=int, default=5, help="how many of each to show")
    ru = sub.add_parser(
        "rules",
        help="the same mistake, made again: recurring failures turned into rules-file lines",
    )
    ru.add_argument("path", nargs="?", default="~/.claude/projects")
    ru.add_argument("--repo", help="only this repository (matched on the directory name)")
    ru.add_argument("--days", type=int, default=30, help="how far back to look (default 30)")
    ru.add_argument("--model", default=None)
    ru.add_argument(
        "--list",
        action="store_true",
        help="list the recurring failures and stop, without calling a model",
    )
    ru.add_argument("--out", help="append the accepted rules to this file")
    sh = sub.add_parser(
        "shipped",
        help="draft a build post: what you made in a window, and what was hard about it",
    )
    sh.add_argument("path", nargs="?", default="~/.claude/projects")
    sh.add_argument("--repo", help="only this repository (matched on the directory name)")
    sh.add_argument("--days", type=int, default=7, help="how far back to look (default 7)")
    sh.add_argument("--out")
    sh.add_argument("--model", default=None)
    sh.add_argument(
        "--dry-run",
        action="store_true",
        help="print what the model would be shown and stop, without calling it",
    )
    bg = sub.add_parser(
        "cards", help="the postable cards: what this corpus gives you to put in a feed"
    )
    bg.add_argument("path", nargs="?", default="~/.claude/projects")
    bg.add_argument("--all", action="store_true", help="show the ones that scored too low too")
    pr = sub.add_parser("probe", help="read-only shape report over a file or directory")
    pr.add_argument(
        "path",
        help=(
            "a transcript file or a directory: Codex ~/.codex/sessions, Gemini ~/.gemini/tmp, "
            "Cline ~/.cline/data, opencode ~/.local/share/opencode (or one session as "
            "opencode.db/<session id>), Aider a repo directory holding .aider.chat.history.md "
            "(or one session as .aider.chat.history.md/<YYYYMMDD-HHMMSS>)"
        ),
    )
    pr.add_argument("--json", action="store_true")
    a = ap.parse_args()

    if a.cmd == "profile":
        from . import profile as pf_mod

        facts, _ = _corpus_facts(pathlib.Path(a.path).expanduser())
        prof = pf_mod.corpus_profile(facts)
        if a.facts_only:
            for f in prof["facts"]:
                print(f"[{f['unusualness']:5.2f}] {f['text']}")
            return 0
        print(json.dumps(prof, indent=1, default=str))
        return 0

    if a.cmd == "report":
        return _report(a)

    if a.cmd == "quality":
        return _quality(a)

    if a.cmd == "contributions":
        return _contributions(a)

    if a.cmd == "trends":
        return _trends(a)

    if a.cmd == "agents":
        return _agents(a)

    if a.cmd == "playbook":
        return _playbook(a)

    if a.cmd == "rules":
        return _rules(a)

    if a.cmd == "shipped":
        return _shipped(a)

    if a.cmd == "cards":
        return _cards(a)

    if a.cmd == "narrative":
        return _narrative(a)

    if a.cmd == "probe":
        from . import probe as pb

        pb.run(pathlib.Path(a.path).expanduser(), as_json=a.json)
        return 0

    path = pathlib.Path(a.transcript).expanduser()
    meta = {"repo": a.repo} if a.repo else {}

    if a.cmd == "digest":
        d = dg.build(path, _ts(a.start), _ts(a.end), meta, a.budget)
        sys.stdout.write(d["text"])
        sys.stderr.write(
            f"\n[events {d['events']}  coverage {d['coverage']}  chars {len(d['text'])}  hash {d['hash'][:12]}]\n"
        )
        return 0
    if a.cmd == "stats":
        d = dg.build(path, _ts(a.start), _ts(a.end), meta, a.budget)
        print(json.dumps(d["stats"], indent=1))
        return 0
    from . import run as rn

    kw = {"model": a.model} if a.model else {}
    res = rn.analyze(path, _ts(a.start), _ts(a.end), meta, budget=a.budget, **kw)
    text = json.dumps(res, indent=1, ensure_ascii=False)
    if a.out:
        pathlib.Path(a.out).write_text(text)
        sys.stderr.write(f"wrote {a.out}  cost ${res['cost_usd']}  {res['duration_ms']} ms\n")
    else:
        print(text)
    return 0


def _quality(a) -> int:
    """Two of DORA's four questions, for one person. The other two are refused: see
    `analysis/quality.py` for the measurement that forced it."""
    from . import quality as q_mod

    facts, sessions = _narrative_inputs(pathlib.Path(a.path).expanduser())
    cutoff = time.time() - a.days * 86400
    picked = [s for f, s in zip(facts, sessions, strict=True) if f.started_at >= cutoff]
    stats = q_mod.summary(picked)
    if stats["first_try_rate"] is None:
        sys.stderr.write(f"{stats['reason']}.\n")
        return 0

    print(
        f"{stats['runs']} test runs in the last {a.days} days: {stats['passed']} passed, "
        f"{stats['failed']} failed. {round(stats['first_try_rate'] * 100)}% were already green."
    )
    ttg = stats["time_to_green"]
    if ttg is None:
        print(f"\nNothing failed and then passed in one sitting ({stats['reason']}).")
        return 0
    print(
        f"\nBack to green in {_hm(ttg['median_seconds'])} at the median, "
        f"{_hm(ttg['worst_seconds'])} at the worst, over {ttg['n']} recover"
        f"{'y' if ttg['n'] == 1 else 'ies'}."
    )
    print(f"It takes {ttg['median_attempts']} test runs to get there, typically.\n")
    for g in sorted(q_mod.recoveries(picked), key=lambda g: -g.seconds)[:5]:
        print(f"  {_hm(g.seconds):>10}  {g.attempts} runs   {g.command[:64]}")
    return 0


def _hm(seconds: float) -> str:
    m = round(seconds / 60)
    if m < 1:
        return "under a minute"
    return f"{m} min" if m < 60 else f"{m // 60}h {m % 60:02d}m"


def _contributions(a) -> int:
    """The graph, and the number underneath it that GitHub cannot answer."""
    import datetime as _dt

    from capture import repo as cap_repo

    from . import contributions as co_mod

    facts, _ = _narrative_inputs(pathlib.Path(a.path).expanduser())
    cutoff = time.time() - a.days * 86400
    facts = [f for f in facts if f.ended_at >= cutoff]
    if not facts:
        sys.stderr.write(f"no sittings in the last {a.days} days.\n")
        return 0

    # Every commit in every repository this machine's own transcripts resolved to. Not a
    # guess at where somebody keeps code, and not an API round trip that would know less.
    roots = sorted({f.repo for f in facts if f.repo})
    commits: list[float] = []
    for root in roots:
        commits += [ts for _sha, ts in cap_repo.commits_in(root, cutoff, time.time())]
    if not commits:
        sys.stderr.write(
            f"no commits in the last {a.days} days across {len(roots)} repositor(y/ies).\n"
        )
        return 0

    offset = facts[-1].tz_offset_minutes
    graph = co_mod.split(commits, [(f.started_at, f.ended_at) for f in facts], offset)
    share = graph.assisted_share

    print(
        f"{graph.total} commits over {graph.active_days} days in "
        f"{len(roots)} repositor{'y' if len(roots) == 1 else 'ies'}."
    )
    if share is not None:
        print(
            f"{graph.assisted} landed while an agent was working ({round(share * 100)}%), "
            f"{graph.alone} you committed on your own."
        )
    else:
        print(
            f"{graph.assisted} with an agent, {graph.alone} on your own. "
            f"Too few to call a share yet ({co_mod.MIN_COMMITS} needed)."
        )
    print(
        f"Longest run of days you shipped: {graph.longest_streak}. "
        f"Currently on {graph.current_streak}.\n"
    )
    for d in graph.days[-21:]:
        bar = "#" * min(d.assisted, 40) + "." * min(d.alone, 40)
        print(f"  {d.day}  {bar:<42} {d.total:>3}")
    print("\n  # landed with an agent, . you committed on your own")
    return 0


def _trends(a) -> int:
    """Two equal windows, back to back. Never a window against all of history: that
    reports the trend of the corpus growing."""
    from . import profile as pf_mod
    from . import trends as tr_mod

    facts, _ = _narrative_inputs(pathlib.Path(a.path).expanduser())
    now = time.time()
    edge = now - a.window * 86400
    floor = now - 2 * a.window * 86400
    recent = [f for f in facts if f.started_at >= edge]
    earlier = [f for f in facts if floor <= f.started_at < edge]

    found = tr_mod.compare(pf_mod.corpus_profile(earlier), pf_mod.corpus_profile(recent))
    if not found:
        sys.stderr.write(
            f"not enough on both sides yet: {len(earlier)} sitting(s) in the "
            f"{a.window} days before last, {len(recent)} since. "
            f"{tr_mod.MIN_SESSIONS} of each are needed.\n"
        )
        return 0

    line = tr_mod.headline(found, a.window)
    if line:
        print(f"{line}\n")
    for t in found:
        if t.steady:
            mark, tail = "  steady", ""
        else:
            mark = "      up" if t.direction == "up" else "    down"
            tail = (
                ""
                if t.good is None
                else ("   the way you want it" if t.good else "   worth a look")
            )
        print(f"{mark} {abs(t.move):>5.0%}  {t.label:<38} {t.before:>8g} -> {t.now:<8g}{tail}")
    print(
        f"\n{found[0].sessions_now} sittings in the last {a.window} days, "
        f"{found[0].sessions_before} in the {a.window} before that."
    )
    return 0


def _agents(a) -> int:
    """The delegations: how many agents, running at once or in a chain, and what landed.

    NEVER a token, a line or a commit: the parent's `Agent` tool result already reports a
    subagent's work in aggregate, and counting the sidecars again is the globbing revert
    in CLAUDE.md happening a second time. This reads them to describe the DELEGATION.
    """
    import datetime as _dt

    from capture import discover
    from capture import sessions as cap

    from . import agents as ag_mod

    root = pathlib.Path(a.path).expanduser()
    cutoff = time.time() - a.days * 86400
    total_agents = total_seconds = 0
    peak = 0
    kinds: collections.Counter[str] = collections.Counter()
    produced = 0
    rows = []

    for t in discover.iter_root_transcripts(root):
        spans = ag_mod.spans(t.path)
        if not spans:
            continue
        if max(s.ended_at for s in spans) < cutoff:
            continue
        wall = max(s.ended_at for s in spans) - min(s.started_at for s in spans)
        fo = ag_mod.fanout(spans, wall)
        total_agents += fo.agents
        total_seconds += fo.agent_seconds
        peak = max(peak, fo.max_concurrent)
        produced += fo.produced
        kinds.update(fo.by_type)
        rows.append((t.path.stem, fo))

    if not rows:
        sys.stderr.write(
            f"no subagents in the last {a.days} days. Every sitting was you and one agent.\n"
        )
        return 0

    hours = total_seconds / 3600
    print(
        f"{total_agents} agents across {len(rows)} sittings, {hours:.1f} hours of agent work.\n"
        f"{produced} of them produced something. The most running at once was {peak}.\n"
    )
    for name, fo in sorted(rows, key=lambda r: -r[1].agents)[:6]:
        print(
            f"  {name[:8]}  {fo.agents} agents, up to {fo.max_concurrent} at once, "
            f"{fo.agent_seconds / 60:.0f} agent-minutes over {fo.busy_seconds / 60:.0f} busy "
            f"minutes ({fo.parallelism}x) in a {fo.wall_seconds / 60:.0f} minute stretch"
        )
        for sp in sorted(fo.spans, key=lambda s: -s.landed)[:3]:
            print(
                f"      {(sp.agent_type or 'unknown'):<17} {sp.seconds / 60:5.1f}m  "
                f"{sp.landed:2} landed  {(sp.asked or 'no brief recorded')[:56]}"
            )
        print()
    if kinds:
        print("  by kind: " + ", ".join(f"{k} {v}" for k, v in kinds.most_common()))
    return 0


def _playbook(a) -> int:
    """Your own two piles, side by side. No model call: this is all measurement."""
    from . import playbook as pb_mod

    facts, sessions = _narrative_inputs(pathlib.Path(a.path).expanduser())
    cutoff = time.time() - a.days * 86400
    picked = [s for f, s in zip(facts, sessions, strict=True) if f.started_at >= cutoff]
    tried = pb_mod.attempts(picked)
    stats = pb_mod.summary(tried)
    if stats["value"] is None:
        sys.stderr.write(
            f"not enough to compare yet: {stats['reason']}, over {stats['n']} prompt(s).\n"
        )
        return 0

    worked, cost = pb_mod.split(tried)
    print(
        f"{stats['worked']} of {stats['n']} prompts landed something without you having to "
        f"take it back. That is {round(stats['value'] * 100)}%.\n"
    )
    print("THESE LANDED, AND YOU NEVER TOUCHED THE WHEEL")
    for at in worked[: a.top]:
        print(f"\n  {at.landed} things landed over {at.tool_calls} tool calls")
        print(f"  {_wrap(at.text)}")
    print("\n\nTHESE COST YOU A ROUND TRIP")
    for at in cost[: a.top]:
        why = "you corrected it" if at.corrected else (
            "it ran a long way with nothing landing" if at.stalled else "nothing landed"
        )
        print(f"\n  {why}, after {at.tool_calls} tool calls")
        print(f"  {_wrap(at.text)}")
    print()
    return 0


def _wrap(text: str, width: int = 92) -> str:
    import textwrap

    return textwrap.fill(text[:400], width=width, subsequent_indent="  ")


def _rules(a) -> int:
    """Failures that happened in more than one sitting, and the lines that stop them."""
    from . import rules as ru

    facts, sessions = _narrative_inputs(pathlib.Path(a.path).expanduser())
    cutoff = time.time() - a.days * 86400
    picked = [(f, s) for f, s in zip(facts, sessions, strict=True) if f.started_at >= cutoff]
    if a.repo:
        picked = [(f, s) for f, s in picked if f.repo and a.repo.lower() in f.repo.lower()]
    root = collections.Counter(f.repo for f, _ in picked if f.repo).most_common(1)
    project = pathlib.Path(root[0][0]).name if root else "this project"

    found = ru.recurring([s for _, s in picked])
    if not found:
        sys.stderr.write(
            f"nothing has failed in more than one of your last {len(picked)} sittings. "
            f"Every error was a one off, which is the good outcome.\n"
        )
        return 0

    if a.list:
        for i, r in enumerate(found, 1):
            hours = r.span_seconds / 3600
            print(
                f"[{i}] {r.sessions} sittings, {r.occurrences}x"
                + (f", spread over {hours:.0f}h" if hours >= 1 else "")
            )
            print(f"     ran  {r.command[:110]}")
            print(f"     got  {r.error[:110]}")
            print()
        return 0

    kw = {"model": a.model} if a.model else {}
    doc = ru.write(project=project, recurrences=found, **kw)
    lines = []
    for r in doc["rules"]:
        evidence = found[r["candidate"] - 1]
        mark = {"certain": "", "likely": "  (likely)", "guess": "  (a guess, check it)"}
        lines.append(f"- {r['rule']}{mark.get(r.get('confidence', ''), '')}")
        lines.append(f"  {r['because']}")
        lines.append(
            f"  Seen in {evidence.sessions} sittings, {evidence.occurrences} times: "
            f"{evidence.error[:120]}"
        )
        lines.append("")
    text = "\n".join(lines)

    if a.out:
        path = pathlib.Path(a.out)
        with path.open("a") as fh:
            fh.write(f"\n## Rules from failures that kept coming back\n\n{text}")
        sys.stderr.write(f"appended {len(doc['rules'])} rule(s) to {a.out}\n")
    else:
        print(text)
        sys.stderr.write(
            f"{len(found)} recurring failure(s), {len(doc['rules'])} rule(s) proposed. "
            f"Read them, then `--out CLAUDE.md` to append the ones you keep.\n"
        )
    return 0


def _shipped(a) -> int:
    """One repository, one window, one draft post about what got built in it."""
    import datetime as _dt

    from capture import repo as cap_repo

    from . import shipped as sh

    facts, sessions = _narrative_inputs(pathlib.Path(a.path).expanduser())
    cutoff = time.time() - a.days * 86400
    picked = [(f, s) for f, s in zip(facts, sessions, strict=True) if f.started_at >= cutoff]
    if a.repo:
        picked = [(f, s) for f, s in picked if f.repo and a.repo.lower() in f.repo.lower()]
    if not picked:
        sys.stderr.write(
            f"no sessions in the last {a.days} days"
            + (f" in a repository matching {a.repo!r}" % () if a.repo else "")
            + ". Nothing to write a post about.\n"
        )
        return 0

    # ONE repository per post. A post that spans two projects is two posts, and a reader
    # cannot tell which half of it they are looking at. The busiest one wins when the
    # window covers several and `--repo` did not narrow it.
    by_repo = collections.Counter(f.repo for f, _ in picked if f.repo)
    root = by_repo.most_common(1)[0][0] if by_repo else None
    picked = [(f, s) for f, s in picked if f.repo == root]
    project = pathlib.Path(root).name if root else "this project"

    commits = _commit_messages(root, cutoff) if root else []
    files = sorted({e.path.rsplit("/", 1)[-1] for _, s in picked for e in s.events if e.path})[:40]
    struggle_list = sh.struggles([s for _, s in picked], commits)
    dependencies = sh.stack_evidence(root)
    summaries = _stored_summaries([f.session_id for f, _ in picked])

    since = _dt.date.fromtimestamp(cutoff)
    source = sh.build_input(
        project=project,
        since=since,
        until=_dt.date.today(),
        session_summaries=summaries,
        commits=commits,
        files=files,
        struggle_list=struggle_list,
        dependencies=dependencies,
    )
    if a.dry_run:
        print(source)
        sys.stderr.write(
            f"\n{len(picked)} session(s) in {project}, {len(commits)} commit(s), "
            f"{len(struggle_list)} measured struggle(s), {len(summaries)} analysed. "
            f"Nothing was sent.\n"
        )
        return 0

    kw = {"model": a.model} if a.model else {}
    doc = sh.write(
        project=project,
        since=since,
        until=_dt.date.today(),
        session_summaries=summaries,
        commits=commits,
        files=files,
        struggle_list=struggle_list,
        dependencies=dependencies,
        **kw,
    )
    text = json.dumps(doc, indent=1, ensure_ascii=False)
    if a.out:
        pathlib.Path(a.out).write_text(text)
        sys.stderr.write(f"wrote {a.out}\n")
    else:
        print(text)
    return 0


def _commit_messages(common_root: str | None, since: float) -> list[str]:
    """Commit SUBJECTS in the window, from capture's own git runner.

    Subjects only: a body can run to forty lines in a repository with a commit-message
    convention, and the post needs to know what landed, not to read the reasoning again.
    """
    from capture import repo as cap_repo
    from capture.tuning import GIT_EXCLUDE_PATHSPECS

    if not common_root:
        return []
    out = cap_repo._git(
        [
            "log",
            f"--since=@{since:.0f}",
            "--pretty=format:%s",
            "--no-merges",
            "--",
            *GIT_EXCLUDE_PATHSPECS,
        ],
        common_root,
    )
    return [line for line in (out or "").splitlines() if line.strip()][:40]


def _stored_summaries(session_ids: list[str]) -> list[dict]:
    """Any analyses this machine has already written for these sessions.

    Read from the capture state file rather than recomputed: an analysis costs a model
    call, and drafting a post is not a reason to spend one per session in the window.
    """
    from capture import client as cl

    state = cl.read_json(cl.state_path()) or {}
    cache = state.get("analysis") or {}
    out = []
    for sid in session_ids:
        body = cache.get(sid)
        if not isinstance(body, dict):
            continue
        out.append(
            {
                "headline": body.get("headline"),
                "summary": body.get("summary"),
                "highlights": body.get("highlights") or [],
            }
        )
    return out


def _cards(a) -> int:
    """Every postable card this corpus can honestly produce, most postable first."""
    from . import brag
    from . import patterns as pat
    from . import profile as pf_mod

    facts, sessions = _narrative_inputs(pathlib.Path(a.path).expanduser())
    prof = pf_mod.corpus_profile(facts)
    found = pat.findings(sessions)
    made = brag.cards(prof, found)
    if not made:
        sys.stderr.write(
            f"nothing postable yet from {len(facts)} session(s). A card needs a finding "
            f"that cleared its bars, or a priced model comparison.\n"
        )
        return 0
    for c in made:
        print(f"[{c.postability:.2f} {c.kind:>10}]  {c.headline}")
        if c.detail:
            print(f"{'':>19}{c.detail}")
        print()
    return 0


def _recent_trends(facts, window_days: int = 30):
    """The last `window_days` against the `window_days` before. Empty when there is not
    enough history, which is the normal state for a first month and not an error.

    The two windows are always the SAME LENGTH. A window against all of history reports
    the trend of the corpus growing, and a 7 day window labelled "on last month" is a
    wrong sentence attached to a right number.
    """
    from . import profile as pf_mod
    from . import trends as tr_mod

    now = time.time()
    edge, floor = now - window_days * 86400, now - 2 * window_days * 86400
    recent = [f for f in facts if f.started_at >= edge]
    earlier = [f for f in facts if floor <= f.started_at < edge]
    if not recent or not earlier:
        return []
    return tr_mod.compare(pf_mod.corpus_profile(earlier), pf_mod.corpus_profile(recent))


def _corpus_fanout(root: pathlib.Path):
    """Every subagent across the corpus, as one `Fanout`. Never a token: see agents.py."""
    from capture import discover

    from . import agents as ag_mod

    spans = [s for t in discover.iter_root_transcripts(root) for s in ag_mod.spans(t.path)]
    if not spans:
        return None
    wall = max(s.ended_at for s in spans) - min(s.started_at for s in spans)
    return ag_mod.fanout(spans, wall)


def _corpus_contributions(facts):
    """Commits by day, split by whether a sitting was running."""
    from capture import repo as cap_repo

    from . import contributions as co_mod

    roots = sorted({f.repo for f in facts if f.repo})
    if not roots or not facts:
        return None
    since = min(f.started_at for f in facts) - co_mod.LOOKBACK_SEC
    commits = [ts for r in roots for _sha, ts in cap_repo.commits_in(r, since, time.time())]
    if not commits:
        return None
    return co_mod.split(
        commits, [(f.started_at, f.ended_at) for f in facts], facts[-1].tz_offset_minutes
    )


def _narrative_inputs(root: pathlib.Path):
    """(facts, session events) for the whole corpus: the one place both commands cut it."""
    import datetime as _dt

    from capture import sessions as cap

    from . import patterns as pat

    facts, kept = _corpus_facts(root)
    tz = _dt.datetime.now().astimezone().tzinfo
    from . import pricing as pr_mod
    from . import profile as pf_mod

    events = []
    for f, s in zip(facts, kept, strict=True):
        ledger = cap.token_ledger(s.records)
        cost, dominant = pr_mod.priced_session(ledger, pf_mod.DOMINANT_SHARE)
        offset = _dt.datetime.fromtimestamp(s.started_at, tz).utcoffset() or _dt.timedelta(0)
        events.append(
            pat.SessionEvents(
                session_id=s.client_session_id,
                started_at=s.started_at,
                ended_at=s.ended_at,
                active_seconds=s.attended + s.autonomous,
                attended_seconds=s.attended,
                tz_offset_minutes=int(offset.total_seconds() // 60),
                events=s.events,
                output_tokens=(
                    ledger.buckets["output"] if ledger.reported and ledger.buckets else None
                ),
                cost_usd=cost,
                dominant_model=dominant,
            )
        )
    return facts, events


def rp_default() -> int:
    """The report's default window, read from the module that owns it rather than typed
    into the help text a second time."""
    from . import report as rp_mod

    return rp_mod.DEFAULT_WINDOW_DAYS


def _report(a) -> int:
    """The builder report: every measured block, assembled and printed.

    This is the document `python -m capture report` uploads, byte for byte — the same
    function builds it — so printing it here is the honest way to see what would leave the
    machine before any of it does.
    """
    from . import report as rp_mod

    root = pathlib.Path(a.path).expanduser()
    facts, sessions = _narrative_inputs(root)
    days = a.days or rp_mod.DEFAULT_WINDOW_DAYS
    doc = rp_mod.build(
        trends=_recent_trends(facts, days),
        fanout=_corpus_fanout(root),
        contributions=_corpus_contributions(facts),
        sessions=sessions,
        window_days=days,
    )
    text = json.dumps(doc, indent=1, ensure_ascii=False)
    if a.out:
        pathlib.Path(a.out).write_text(text)
        sys.stderr.write(f"wrote {a.out}\n")
    else:
        print(text)
    return 0


def _narrative(a) -> int:
    """profile + findings -> `claude -p` -> the page, printed or written.

    Everything here reads the machine's own transcripts. `--findings-only` is the honest
    dry run: it prints exactly what the model would be shown about the person's habits,
    costs nothing, and is the fastest way to see WHY a page says what it says.
    """
    from . import narrative as nar
    from . import patterns as pat
    from . import profile as pf_mod

    facts, sessions = _narrative_inputs(pathlib.Path(a.path).expanduser())
    found = pat.findings(sessions)

    if a.findings_only:
        if not found:
            sys.stderr.write(
                f"no finding cleared the bars over {len(sessions)} sessions "
                f"(both sides need {pat.MIN_GROUP}, the gap needs {pat.MIN_LIFT}x)\n"
            )
            return 0
        for f in found:
            print(f"[{f.lift:>6}x] {f.text}")
            print(f"          left  {f.left}")
            print(f"          right {f.right}\n")
        return 0

    prof = pf_mod.corpus_profile(facts)
    # Everything else this machine can measure, so the page reflects the whole picture
    # rather than the one module the prose happened to start from.
    kw = {"model": a.model} if a.model else {}
    doc = nar.write(
        profile=prof,
        findings=found,
        trends=_recent_trends(facts),
        fanout=_corpus_fanout(pathlib.Path(a.path).expanduser()),
        contributions=_corpus_contributions(facts),
        **kw,
    )
    text = json.dumps(doc, indent=1, ensure_ascii=False)
    if a.out:
        pathlib.Path(a.out).write_text(text)
        sys.stderr.write(f"wrote {a.out}\n")
    else:
        print(text)
    return 0


def _corpus_facts(root: pathlib.Path) -> tuple[list, list]:
    """Sessionize a whole `~/.claude/projects` tree: (facts, the sessions they came from).

    The sessions travel back beside the facts because the comparative findings
    (analysis/patterns.py) need the EVENTS, prompt wording included, and a `SessionFact`
    is deliberately a summary. Nothing that reads the sessions may upload them.

    The sessionizer is `capture`, which is the reference cut (v3 lineage pooling, fitted
    tau) rather than a second implementation of the boundary rules. Live sessions are
    excluded: their numbers move every minute, so a profile that included them would
    disagree with itself between two runs.
    """
    import dataclasses
    import datetime as _dt

    from capture import discover
    from capture import sessions as cap

    from . import profile as pf_mod

    tz = _dt.datetime.now().astimezone().tzinfo
    sources = [cap.load_source(t) for t in discover.iter_root_transcripts(root)]
    cut = [s for s in cap.sessionize_sources(sources, tz) if s.state == "final"]

    facts, kept = [], []
    for s in cut:
        # `capture.sessions.is_counted`, not a second copy of the rule: it is what decides
        # `visible` on the wire, and a private reimplementation here is a definition of
        # "a session" that can drift from the one the phone uses.
        if not cap.is_counted(s):
            continue
        # Output tokens per model come from the reference LEDGER (deduped on
        # `(source_id, message.id)`, sidechain and `<synthetic>` records excluded), never
        # from summing `.message.usage`: that inflates by 1.878x (CLAUDE.md). Share times
        # the ledger total is also exactly what the server has to work with, so the two
        # paths cannot disagree about the model mix.
        ledger = cap.token_ledger(s.records)
        by_model: dict[str, int] = {}
        if ledger.reported and ledger.buckets:
            out_tokens = ledger.buckets["output"]
            for entry in ledger.models:
                by_model[entry["model_id"]] = round(entry["output_token_share"] * out_tokens)
        offset = _dt.datetime.fromtimestamp(s.started_at, tz).utcoffset() or _dt.timedelta(0)
        facts.append(
            pf_mod.session_fact_from_events(
                session_id=s.client_session_id,
                events=s.events,
                started_at=s.started_at,
                ended_at=s.ended_at,
                attended_seconds=s.attended,
                autonomous_seconds=s.autonomous,
                tz_offset_minutes=int(offset.total_seconds() // 60),
                output_tokens_by_model=by_model,
                # The five buckets, straight from the ledger. `cache_read` is billed at a
                # tenth of the input rate and `cache_w5m` at 1.25x it, so they travel
                # separately: adding them up and multiplying by one price overcharges
                # cache-heavy work by a factor that grows with how well the cache worked.
                tokens=(
                    pf_mod.pricing.Tokens(**ledger.buckets)
                    if ledger.reported and ledger.buckets
                    else None
                ),
                unattended=s.presence == 0,
            )
        )
        kept.append(s)

    # Commits: `git log` over the session window, the same definition the uploader stores
    # and the server aggregates. Counting `git commit` shell calls off the digest text is
    # 3.5x low, because the command is truncated at 160 characters (MEASURED: 19 of 68
    # calls survive that cut on this corpus).
    #
    # A commit is assigned to the FIRST session whose window contains it. Windows overlap
    # (three sessions ran inside one 17:15-18:31 stretch, and every window reaches
    # `tauCommitAttributionSec` back before its start), so summing per-session counts
    # reported 92 commits where the repository had 68.
    from capture import repo as cap_repo

    roots = [s.repo.common_root if s.repo else None for s in kept]
    # WHICH repository, on the fact itself. Without it every session looks like it ran in
    # the same place, which makes `_corpus_commits` treat one machine's whole corpus as a
    # single repo and refuse the total for overlaps that are not overlaps, and leaves a
    # build post with no commits to describe. FOUND BY RUNNING `shipped --dry-run`, which
    # reported "0 commits" for a window with nine of them in it.
    facts = [dataclasses.replace(f, repo=r) for f, r in zip(facts, roots, strict=True)]
    facts = pf_mod.attribute_commits(facts, roots, cap_repo.commits_in)
    return facts, kept


if __name__ == "__main__":
    sys.exit(main())
