#!/usr/bin/env python3
"""Synthesize transcripts that exercise every session-boundary rule, and record what the
reference implementation (scripts/measure_boundaries.py) says about them.

Outputs spec/fixtures/boundaries/<case>.jsonl and <case>.expected.json. The Swift
sessionizer test loads both and must agree on: session count, each session's end_reason,
started_at/ended_at (±1 s), active/attended/autonomous (±1 s), prompts, presence.

Timestamps are fixed (deterministic), timezone is America/New_York so the 04:00 rule is
exercised against DST-real offsets. Records carry only the fields the parser reads.

v3 adds three things, each in its own place so the v2 files stay byte-identical:

* cases whose expected.json carries `"tau": {"mode": "auto", ...}` — the reference fitted
  the idle-gap threshold to that transcript's presence intervals, and a conformant
  sessionizer must fit the same number (to 1e-6) before cutting;
* spec/fixtures/boundaries/cross_pool/ — cases with TWO native session ids that must be
  pooled separately (`switched_repo` is a cross-pool rule). The expected sessions are the
  union of both pools in start order, each tagged with its pool;
* spec/fixtures/boundaries/threshold_fit.json — a bare gap list and the fit the reference
  produces for it, so the EM itself is held to 1e-6 independently of any transcript.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import pathlib
import sys
import zoneinfo

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import measure_boundaries as mb

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "spec" / "fixtures" / "boundaries"
OUT_CROSS = OUT / "cross_pool"
TZ = zoneinfo.ZoneInfo("America/New_York")
SESSION = "00000000-0000-4000-8000-000000000001"
SESSION_B = "00000000-0000-4000-8000-000000000002"


def iso(t: dt.datetime) -> str:
    return t.astimezone(dt.UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


class LCG:
    """A tiny deterministic generator, so the fixtures do not depend on the `random`
    module's algorithm staying stable across Python releases."""

    def __init__(self, seed: int):
        self.s = seed & 0xFFFFFFFF

    def next(self) -> float:
        self.s = (1664525 * self.s + 1013904223) & 0xFFFFFFFF
        return self.s / 4294967296.0

    def between(self, lo: float, hi: float) -> float:
        return lo + (hi - lo) * self.next()


class Tape:
    def __init__(self, start: dt.datetime, session: str = SESSION, cwd: str = "/Users/dev/proj"):
        self.t = start
        self.recs: list[dict] = []
        self.n = 0
        self.session = session
        self.cwd = cwd

    def _base(self, typ: str) -> dict:
        self.n += 1
        return {
            "type": typ,
            # The v2 fixtures were written with this exact suffix; a second session id in
            # the cross-pool cases gets its own so uuids never collide across pools.
            "uuid": f"{self.n:08d}-0000-4000-8000-{'000000000000' if self.session == SESSION else '000000000002'}",
            "parentUuid": None,
            "sessionId": self.session,
            "timestamp": iso(self.t),
            "cwd": self.cwd,
            "version": "2.1.0",
        }

    def advance(self, seconds: float) -> None:
        self.t += dt.timedelta(seconds=seconds)

    def prompt(self, text: str = "do the thing", remote: bool = False) -> None:
        r = self._base("user")
        r["message"] = {"role": "user", "content": text}
        if remote:
            r["promptSource"] = "sdk"
            r["origin"] = {"kind": "human"}
        else:
            r["promptSource"] = "typed"
        self.recs.append(r)

    def meta_prompt(self) -> None:
        r = self._base("user")
        r["message"] = {"role": "user", "content": "<command-message>x</command-message>"}
        r["isMeta"] = True
        self.recs.append(r)

    def slash(self, name: str) -> None:
        """A slash command as the harness writes it: no promptSource, the command in the
        text. `/clear` is the only one the rules read."""
        r = self._base("user")
        r["message"] = {
            "role": "user",
            "content": f"<command-name>{name}</command-name>\n<command-message>{name.lstrip('/')}</command-message>\n<command-args></command-args>",
        }
        self.recs.append(r)

    def interrupt(self) -> None:
        r = self._base("user")
        r["message"] = {"role": "user", "content": "[Request interrupted by user]"}
        self.recs.append(r)

    def human_edit(self) -> None:
        r = self._base("attachment")
        r["attachment"] = {"type": "edited_text_file", "filename": "a.py"}
        self.recs.append(r)

    def agent_burst(self, seconds: float, every: float = 8.0, command: str | None = None) -> None:
        """Tool-call chatter: one assistant record every `every` seconds for `seconds`."""
        end = self.t + dt.timedelta(seconds=seconds)
        while self.t < end:
            r = self._base("assistant")
            r["message"] = {
                "role": "assistant",
                "model": "claude-sonnet-5",
                "id": f"msg_{self.n}",
                "content": [
                    {
                        "type": "tool_use",
                        "id": f"tu_{self.n}",
                        "name": "Bash",
                        "input": {"command": command} if command else {},
                    }
                ],
                "usage": {"input_tokens": 1, "output_tokens": 1},
            }
            self.recs.append(r)
            self.advance(every)


def cases() -> dict[str, Tape]:
    out: dict[str, Tape] = {}

    # 1. A plain attended afternoon: prompts every few minutes, ends by idle gap.
    t = Tape(dt.datetime(2026, 3, 10, 14, 0, tzinfo=TZ))
    for _ in range(6):
        t.prompt()
        t.advance(5)
        t.agent_burst(240)
        t.advance(120)
    t.advance(2000)  # > tau: session ends
    t.prompt("new sitting")
    t.advance(3)
    t.agent_burst(60)
    out["attended_afternoon_then_gap"] = t

    # 2. Kickoff at 23:00, agent runs continuously until 06:30, human returns 09:00.
    #    Expect: rule 3 splits at 04:00 (autonomous), rule 1 ends the second piece at
    #    06:30+cap, and the 09:00 prompt is a fresh session (already past an idle gap).
    t = Tape(dt.datetime(2026, 3, 10, 23, 0, tzinfo=TZ))
    t.prompt("run the whole migration overnight")
    t.advance(5)
    t.agent_burst(7.5 * 3600, every=60)  # until ~06:30
    t.advance(2.5 * 3600)  # silence until 09:00
    t.prompt("morning, what happened?")
    t.advance(3)
    t.agent_burst(120)
    out["overnight_run_day_boundary"] = t

    # 3. Kickoff 22:00, agent NEVER stops (a loop), human returns at 08:00 and types.
    #    Expect: split at 04:00 (day_boundary), then human_returned at 08:00.
    t = Tape(dt.datetime(2026, 3, 10, 22, 0, tzinfo=TZ))
    t.prompt("loop until the suite is green")
    t.advance(5)
    t.agent_burst(10 * 3600, every=60)  # 22:00 -> 08:00 continuous
    t.prompt("ok stop, show me")
    t.advance(3)
    t.agent_burst(300)
    out["endless_loop_human_returns"] = t

    # 4. Attended late night across 04:00: prompts every 20 minutes 01:00 -> 05:00.
    #    Expect: ONE session. The day rule never splits an attended sitting.
    t = Tape(dt.datetime(2026, 3, 10, 1, 0, tzinfo=TZ))
    for _ in range(12):
        t.prompt()
        t.advance(5)
        t.agent_burst(900, every=30)
        t.advance(295)
    out["attended_across_4am"] = t

    # 5. Lunch: prompt, 50 minutes of autonomous work, human back after 55 min total.
    #    Expect: ONE session (55 min < tauReturnSplit), with autonomous time > 0.
    t = Tape(dt.datetime(2026, 3, 10, 12, 0, tzinfo=TZ))
    t.prompt("refactor the module while I eat")
    t.advance(5)
    t.agent_burst(50 * 60)
    t.advance(300)  # still < tau idle
    t.prompt("looks good")
    t.advance(3)
    t.agent_burst(120)
    out["lunch_autonomy_no_split"] = t

    # 6. Pure robot: no presence at all, 30 hours continuous, crossing 04:00 twice.
    #    Expect: 3 sessions (day_boundary, day_boundary, still_running), all zero presence.
    t = Tape(dt.datetime(2026, 3, 10, 20, 0, tzinfo=TZ))
    t.agent_burst(30 * 3600, every=60)
    out["robot_thirty_hours"] = t

    # 7. Remote session: prompts stamped sdk/human, plus a meta injection and an interrupt.
    #    Expect: prompts == 3 (the meta record is not a prompt), presence == 4.
    t = Tape(dt.datetime(2026, 3, 10, 9, 0, tzinfo=TZ))
    t.prompt("hello", remote=True)
    t.advance(2)
    t.meta_prompt()
    t.advance(1)
    t.agent_burst(60)
    t.prompt("do it", remote=True)
    t.advance(2)
    t.agent_burst(300)
    t.interrupt()
    t.advance(2)
    t.prompt("no, the other one", remote=True)
    t.advance(2)
    t.agent_burst(60)
    out["remote_sdk_prompts"] = t

    # 8. DST spring-forward night (2026-03-08 in America/New_York): robot across it.
    t = Tape(dt.datetime(2026, 3, 7, 23, 30, tzinfo=TZ))
    t.prompt("go")
    t.advance(5)
    t.agent_burst(9 * 3600, every=60)
    out["dst_spring_forward_robot"] = t

    # 9. A robot from the first record (scheduled agent), then a human sits down after 3 h.
    #    Expect: human_returned at the first prompt even though the run never had presence.
    t = Tape(dt.datetime(2026, 3, 10, 9, 0, tzinfo=TZ))
    t.agent_burst(3 * 3600, every=60)
    t.prompt("what did you do?")
    t.advance(3)
    t.agent_burst(120)
    out["robot_then_human_arrives"] = t

    # ---- v3 -------------------------------------------------------------------------

    # 10. `/clear` twice. The first ends a sitting whose next record is only 3 s away —
    #     no gap could have ended it — and the second is the LAST record of the pool, so
    #     the final session is `cleared` and final rather than `still_running` and live.
    #     `/model` in the middle is a slash command that is NOT a boundary and NOT presence.
    #     UNTESTED ON REAL DATA: the container corpus holds zero `/clear` records; this
    #     fixture pins the record shape the rule reads.
    t = Tape(dt.datetime(2026, 3, 10, 15, 0, tzinfo=TZ))
    t.prompt("fix the flaky test")
    t.advance(5)
    t.agent_burst(240)
    t.slash("/model")
    t.advance(2)
    t.agent_burst(60)
    t.slash("/clear")
    t.advance(3)
    t.prompt("now the other module")
    t.advance(5)
    t.agent_burst(300)
    t.slash("/clear")
    out["cleared_twice"] = t

    # 11. The coordinator's real-input shape: the human's prompts are stamped with the
    #     shell's HOME cwd and the agent's tool calls (including the commits) with the
    #     repository, interleaved within seconds, because Claude Code stamps the current
    #     cwd on every record. MEASURED on the container corpus: 332 cwd runs in one
    #     2,231-record session, all 15 prompts under /home/user and 833 assistant records
    #     under the repo. Pooled per record this became two overlapping sessions — one
    #     with the prompts and 0 commits, one with the commits and "0 prompts typed".
    #     Expect: ONE session holding the prompts AND the commits.
    t = Tape(dt.datetime(2026, 3, 10, 16, 0, tzinfo=TZ), cwd="/Users/dev")
    for i in range(5):
        t.cwd = "/Users/dev"
        t.prompt(f"step {i}")
        t.advance(4)
        t.cwd = "/Users/dev/proj"
        t.agent_burst(200, command="git commit -m step" if i % 2 else None)
        t.cwd = "/Users/dev"
        t.agent_burst(16)  # a tool call back home, as the shell returns
        t.advance(30)
    out["cwd_interleaved_one_sitting"] = t

    # 12. Auto tau on a pool that IS bimodal. Twenty-four sittings of ten prompts each,
    #     one to three minutes apart with agent chatter between, separated by 40–80 min
    #     of nothing: 216 within-sitting presence intervals and 23 between-sitting ones
    #     (9.6% — above the 5% floor), 1.5 decades apart. Inside one sitting the agent
    #     falls silent for 700 s; the fitted tau (below 900) cuts there and the fallback
    #     does not, which is what makes this fixture a test of the fit and not just of
    #     the walk. Every constant the fit reads is in expected.json under `tau.fit`.
    t = Tape(dt.datetime(2026, 3, 9, 8, 0, tzinfo=TZ))
    g = LCG(20260309)
    for sitting in range(24):
        for k in range(10):
            t.prompt(f"s{sitting} p{k}")
            t.advance(3)
            work = g.between(40, 150)
            t.agent_burst(work)
            if sitting == 11 and k == 4:
                t.advance(700)  # the agent went quiet mid-sitting: between 300 and 900 s
            else:
                t.advance(g.between(10, 40))
        t.advance(g.between(2400, 4800))
    out["auto_tau_bimodal"] = t

    return out


def cross_pool_cases() -> dict[str, list[Tape]]:
    """Cases with two native session ids in two working directories. The Swift test pools
    them by native session (as the fixture suite always has), which here is two pools."""
    out: dict[str, list[Tape]] = {}

    # 13. Repo A: prompt at 10:00, agent works to 10:05. The human opens a NEW session in
    #     repo B at 10:08:20 (500 s in, 200 s after A's last record: past the 120 s
    #     floor). The agent in A emits more records from 10:11:40 (700 s in) — a late
    #     result, a background hook — without an idle gap having passed in A (404 s).
    #     Expect: A's first session ends `switched_repo` at last record + 120 s credit;
    #     B is one still_running session; A's late records open a fresh session in A
    #     with no presence (the human is in B).
    t0 = dt.datetime(2026, 3, 10, 10, 0, tzinfo=TZ)
    a = Tape(t0, session=SESSION, cwd="/Users/dev/a")
    a.prompt("start A")
    a.advance(5)
    a.agent_burst(292)  # last record at ~296 s
    a.t = t0 + dt.timedelta(seconds=700)
    a.agent_burst(100)
    b = Tape(t0 + dt.timedelta(seconds=500), session=SESSION_B, cwd="/Users/dev/b")
    b.prompt("start B")
    b.advance(4)
    b.agent_burst(300)
    out["switched_repo_two_pools"] = [a, b]

    # 14. The same two pools but B's first record is a `claude -p` prompt (`sdk`, no human
    #     origin) — a robot starting elsewhere says nothing about the person, so A is NOT
    #     cut. MEASURED: 7 of 7 headless runs in the container corpus have this shape.
    a = Tape(t0, session=SESSION, cwd="/Users/dev/a")
    a.prompt("start A")
    a.advance(5)
    a.agent_burst(292)
    a.t = t0 + dt.timedelta(seconds=700)
    a.agent_burst(100)
    b = Tape(t0 + dt.timedelta(seconds=500), session=SESSION_B, cwd="/Users/dev/b")
    r = b._base("user")
    r["message"] = {"role": "user", "content": "headless digest"}
    r["promptSource"] = "sdk"
    b.recs.append(r)
    b.advance(4)
    b.agent_burst(300)
    out["headless_start_elsewhere_no_switch"] = [a, b]

    return out


def _write_jsonl(path: pathlib.Path, recs: list[dict]) -> None:
    path.write_text("".join(json.dumps(r, separators=(",", ":")) + "\n" for r in recs))


def _session_dict(s: dict, pool: str | None = None) -> dict:
    d = {
        "started_at": round(s["started_at"], 3),
        "ended_at": round(s["ended_at"], 3),
        "active_seconds": round(s["active"], 3),
        "attended_seconds": round(s["attended"], 3),
        "autonomous_seconds": round(s["autonomous"], 3),
        "prompts": s["prompts"],
        "presence": s["presence"],
        "end_reason": s["end_reason"],
    }
    if pool is not None:
        d["pool"] = pool
    return d


def _constants(tau: float) -> dict:
    return {
        "tau_session": tau,
        "active_gap_cap": mb.ACTIVE_GAP_CAP,
        "tau_autonomous": mb.TAU_AUTONOMOUS,
        "tau_return_split": mb.TAU_RETURN_SPLIT,
        "day_boundary_hour": mb.DAY_BOUNDARY_HOUR,
    }


def _fit_dict(fit: mb.TauFit) -> dict:
    d = fit.as_dict()
    d["constants"] = {
        "min_gaps": mb.TAU_FIT_MIN_GAPS,
        "min_separation_decades": mb.TAU_FIT_MIN_SEPARATION,
        "min_component_weight": mb.TAU_FIT_MIN_WEIGHT,
        "valley_min_log10": mb.TAU_FIT_VALLEY_MIN_LOG,
        "valley_max_log10": mb.TAU_FIT_VALLEY_MAX_LOG,
        "var_floor": mb.TAU_FIT_VAR_FLOOR,
        "tau_min": mb.TAU_MIN,
        "tau_max": mb.TAU_MAX,
        "fallback": mb.TAU_SESSION_FALLBACK,
    }
    return d


#: Cases whose threshold the reference FITS rather than takes from the fallback.
AUTO_TAU_CASES = frozenset({"auto_tau_bimodal"})


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    OUT_CROSS.mkdir(parents=True, exist_ok=True)
    index = {}
    for name, tape in cases().items():
        path = OUT / f"{name}.jsonl"
        _write_jsonl(path, tape.recs)
        recs = mb.load_records(path)
        if name in AUTO_TAU_CASES:
            fit = mb.fit_tau(mb.presence_gaps(recs))
            assert fit.source == "fitted", f"{name}: the fit fell back ({fit.reason})"
            tau = fit.tau
        else:
            fit, tau = None, mb.TAU_SESSION
        sessions = mb.sessionize(recs, TZ, tau=tau)
        expected: dict = {
            "tz": "America/New_York",
            "constants": _constants(tau),
            "records": len(recs),
            "sessions": [_session_dict(s) for s in sessions],
        }
        if fit is not None:
            expected["tau"] = {
                "mode": "auto",
                "value": tau,
                "fit": _fit_dict(fit),
                "sessions_at_fallback": len(mb.sessionize(recs, TZ, tau=mb.TAU_SESSION_FALLBACK)),
            }
        (OUT / f"{name}.expected.json").write_text(json.dumps(expected, indent=1) + "\n")
        index[name] = [(s["end_reason"], round(s["active"] / 60, 1)) for s in sessions]
        extra = f"  tau {tau:.1f} (fitted; {expected['tau']['sessions_at_fallback']} at 900)" if fit else ""
        print(f"{name:34s} records {len(recs):5d}  sessions {len(sessions)}  {index[name]}{extra}")

    for name, tapes in cross_pool_cases().items():
        path = OUT_CROSS / f"{name}.jsonl"
        recs_all = [r for tape in tapes for r in tape.recs]
        recs_all.sort(key=lambda r: r["timestamp"])
        _write_jsonl(path, recs_all)
        recs = mb.load_records(path)
        pools = mb.fold_by_session_lineage([(r["sid"], r) for r in recs])
        cut = mb.sessionize_pools(pools, TZ)
        sessions = sorted(
            ((s, pool) for pool, ss in cut.items() for s in ss), key=lambda x: x[0]["started_at"]
        )
        expected = {
            "tz": "America/New_York",
            "pooling": "native_session",
            "constants": _constants(mb.TAU_SESSION),
            "switch_min_gap": mb.SWITCH_MIN_GAP,
            "records": len(recs),
            "sessions": [_session_dict(s, pool) for s, pool in sessions],
        }
        (OUT_CROSS / f"{name}.expected.json").write_text(json.dumps(expected, indent=1) + "\n")
        summary = [(p[-1], s["end_reason"], round(s["active"] / 60, 1)) for s, p in sessions]
        print(f"cross_pool/{name:23s} records {len(recs):5d}  sessions {len(sessions)}  {summary}")

    # The EM itself, on a bare gap list. Two log-normal-ish clusters, 320 + 40 samples,
    # so the minor component is 11% and the modes sit ~1.8 decades apart.
    g = LCG(1729)
    gaps: list[float] = []
    for _ in range(320):
        gaps.append(10 ** (2.0 + 0.35 * (g.next() + g.next() + g.next() - 1.5)))
    for _ in range(40):
        gaps.append(10 ** (3.8 + 0.30 * (g.next() + g.next() + g.next() - 1.5)))
    gaps = [round(x, 3) for x in gaps]
    fit = mb.fit_tau(gaps)
    assert fit.bimodal, fit.reason
    (OUT / "threshold_fit.json").write_text(
        json.dumps(
            {
                "note": "Input to and output of measure_boundaries.fit_tau. A conformant "
                "ThresholdFitter must reproduce every number in `fit` to 1e-6.",
                "gaps": gaps,
                "fit": _fit_dict(fit),
                "log10_gaps_are_the_sample": True,
            },
            indent=1,
        )
        + "\n"
    )
    print(
        f"threshold_fit.json                 gaps {len(gaps)}  modes {fit.m1:.3f}/{fit.m2:.3f}  "
        f"valley {fit.valley:.4f}  tau {fit.tau:.2f}"
    )
    assert not math.isnan(fit.m1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
