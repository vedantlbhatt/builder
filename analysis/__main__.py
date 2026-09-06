from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import sys

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


def _narrative(a) -> int:
    """profile + findings -> `claude -p` -> the page, printed or written.

    Everything here reads the machine's own transcripts. `--findings-only` is the honest
    dry run: it prints exactly what the model would be shown about the person's habits,
    costs nothing, and is the fastest way to see WHY a page says what it says.
    """
    import datetime as _dt

    from . import narrative as nar
    from . import patterns as pat
    from . import profile as pf_mod

    facts, kept = _corpus_facts(pathlib.Path(a.path).expanduser())
    tz = _dt.datetime.now().astimezone().tzinfo
    sessions = []
    for s in kept:
        offset = _dt.datetime.fromtimestamp(s.started_at, tz).utcoffset() or _dt.timedelta(0)
        sessions.append(
            pat.SessionEvents(
                session_id=s.client_session_id,
                started_at=s.started_at,
                ended_at=s.ended_at,
                active_seconds=s.attended + s.autonomous,
                attended_seconds=s.attended,
                tz_offset_minutes=int(offset.total_seconds() // 60),
                events=s.events,
            )
        )
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
    kw = {"model": a.model} if a.model else {}
    doc = nar.write(profile=prof, findings=found, **kw)
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
    import datetime as _dt

    from capture import discover
    from capture import sessions as cap
    from capture.tuning import COUNTED_MIN_ACTIVE_SEC, COUNTED_MIN_MEANINGFUL_EVENTS

    from . import profile as pf_mod

    tz = _dt.datetime.now().astimezone().tzinfo
    sources = [cap.load_source(t) for t in discover.iter_root_transcripts(root)]
    cut = [s for s in cap.sessionize_sources(sources, tz) if s.state == "final"]

    facts, kept = [], []
    for s in cut:
        meaningful = sum(1 for e in s.events if e.kind in ("prompt", "tool", "human_edit"))
        active = s.attended + s.autonomous
        if active < COUNTED_MIN_ACTIVE_SEC and meaningful < COUNTED_MIN_MEANINGFUL_EVENTS:
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

    facts = pf_mod.attribute_commits(
        facts,
        [s.repo.common_root if s.repo else None for s in kept],
        cap_repo.commits_in,
    )
    return facts, kept


if __name__ == "__main__":
    sys.exit(main())
