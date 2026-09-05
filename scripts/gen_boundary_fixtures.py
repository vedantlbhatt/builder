#!/usr/bin/env python3
"""Synthesize transcripts that exercise every session-boundary rule, and record what the
reference implementation (scripts/measure_boundaries.py) says about them.

Outputs spec/fixtures/boundaries/<case>.jsonl and <case>.expected.json. The Swift
sessionizer test loads both and must agree on: session count, each session's end_reason,
started_at/ended_at (±1 s), active/attended/autonomous (±1 s), prompts, presence.

Timestamps are fixed (deterministic), timezone is America/New_York so the 04:00 rule is
exercised against DST-real offsets. Records carry only the fields the parser reads.
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import sys
import zoneinfo

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import measure_boundaries as mb

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "spec" / "fixtures" / "boundaries"
TZ = zoneinfo.ZoneInfo("America/New_York")
SESSION = "00000000-0000-4000-8000-000000000001"


def iso(t: dt.datetime) -> str:
    return t.astimezone(dt.UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


class Tape:
    def __init__(self, start: dt.datetime):
        self.t = start
        self.recs: list[dict] = []
        self.n = 0

    def _base(self, typ: str) -> dict:
        self.n += 1
        return {
            "type": typ,
            "uuid": f"{self.n:08d}-0000-4000-8000-000000000000",
            "parentUuid": None,
            "sessionId": SESSION,
            "timestamp": iso(self.t),
            "cwd": "/Users/dev/proj",
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

    def interrupt(self) -> None:
        r = self._base("user")
        r["message"] = {"role": "user", "content": "[Request interrupted by user]"}
        self.recs.append(r)

    def human_edit(self) -> None:
        r = self._base("attachment")
        r["attachment"] = {"type": "edited_text_file", "filename": "a.py"}
        self.recs.append(r)

    def agent_burst(self, seconds: float, every: float = 8.0) -> None:
        """Tool-call chatter: one assistant record every `every` seconds for `seconds`."""
        end = self.t + dt.timedelta(seconds=seconds)
        while self.t < end:
            r = self._base("assistant")
            r["message"] = {
                "role": "assistant",
                "model": "claude-sonnet-5",
                "id": f"msg_{self.n}",
                "content": [
                    {"type": "tool_use", "id": f"tu_{self.n}", "name": "Bash", "input": {}}
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

    return out


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    index = {}
    for name, tape in cases().items():
        path = OUT / f"{name}.jsonl"
        path.write_text("".join(json.dumps(r, separators=(",", ":")) + "\n" for r in tape.recs))
        recs = mb.load_records(path)
        sessions = mb.sessionize(recs, TZ)
        expected = {
            "tz": "America/New_York",
            "constants": {
                "tau_session": mb.TAU_SESSION,
                "active_gap_cap": mb.ACTIVE_GAP_CAP,
                "tau_autonomous": mb.TAU_AUTONOMOUS,
                "tau_return_split": mb.TAU_RETURN_SPLIT,
                "day_boundary_hour": mb.DAY_BOUNDARY_HOUR,
            },
            "records": len(recs),
            "sessions": [
                {
                    "started_at": round(s["started_at"], 3),
                    "ended_at": round(s["ended_at"], 3),
                    "active_seconds": round(s["active"], 3),
                    "attended_seconds": round(s["attended"], 3),
                    "autonomous_seconds": round(s["autonomous"], 3),
                    "prompts": s["prompts"],
                    "presence": s["presence"],
                    "end_reason": s["end_reason"],
                }
                for s in sessions
            ],
        }
        (OUT / f"{name}.expected.json").write_text(json.dumps(expected, indent=1) + "\n")
        index[name] = [(s["end_reason"], round(s["active"] / 60, 1)) for s in sessions]
        print(f"{name:34s} records {len(recs):5d}  sessions {len(sessions)}  {index[name]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
