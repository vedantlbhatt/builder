#!/usr/bin/env python3
"""Measure what the session-boundary rules would do to a Claude Code corpus.

This is ALSO the reference implementation of those rules, in the same spirit as the strip
conformance fixtures: the Swift sessionizer must produce identical cuts on the fixture
transcripts under spec/fixtures/boundaries/. Read docs/session-boundaries.md first.

    scripts/measure_boundaries.py ~/.claude/projects              # whole corpus, fallback tau
    scripts/measure_boundaries.py ~/.claude/projects --tau auto   # fit tau to YOUR gaps
    scripts/measure_boundaries.py path/to/one.jsonl --json        # one file, machine-readable

Rules (each carries the Tuning constant it reads), in evaluation order per gap:
  0. cleared         the previous record was a typed `/clear`. The human ended the
                     conversation on purpose; the work stopped there. v3, announced.
  1. idle_gap        gap between consecutive records > tau. tau is the FALLBACK 900 s unless
                     `--tau auto` fits it to the pool (v3, see fit_tau below). The boundary
                     gap is credited (capped) and ended_at extended by it.
  2. switched_repo   a human opened a NEW native session in a DIFFERENT pool at least
                     SWITCH_MIN_GAP (120 s) after this pool's last record and before its
                     next. The sitting moved; this pool's session ends at the switch. v3,
                     announced.
  3. human_returned  a presence signal arrives after >= TAU_RETURN_SPLIT (7200s) of
                     autonomous time. The human came back to a still-running agent: that
                     is a new sitting, so the run is finalized and a new session begins.
  4. day_boundary    the 04:00 local day boundary falls inside a gap while the run is
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
  /clear            user record whose text carries `<command-name>/clear</command-name>`.
                    A human typed it (the harness never clears on its own), so it is
                    presence — but not a prompt. UNTESTED ON REAL DATA: the container
                    corpus this was written against holds zero; the fixture pins the shape.
Only records with a timestamp participate; none are imputed (see Tuning.swift).
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import math
import pathlib
import sys

# ---------------------------------------------------------------------------- constants
#
# Every one mirrors a `Tuning` constant in Packages/BuilderKit/Sources/BuilderModel/Tuning.swift,
# which carries the measurement. Change one here and there together, or the two clients
# will describe the same transcript differently.

#: Tuning.tauSessionSec — the FALLBACK idle gap, used while a pool has fewer than
#: TAU_FIT_MIN_GAPS gaps or its gap distribution is not bimodal. 900 s was chosen from the
#: reference corpus's gap percentiles (p99.83) and is what the ground-truth table in
#: CLAUDE.md is stated at.
TAU_SESSION = 900.0
TAU_SESSION_FALLBACK = TAU_SESSION
#: Tuning.tauSessionMinSec / tauSessionMaxSec — the clamp on a FITTED tau. Below 300 s a
#: fitted valley is inside the agent's own tool cadence tail (p99 of the reference
#: corpus is 171 s; 300 s shattered one afternoon into 217 sessions); above 3600 s the
#: session count on the reference corpus is 30 with a mean of 4.2 h, which is a day, not a
#: sitting.
TAU_MIN = 300.0
TAU_MAX = 3600.0
#: Tuning.tauFitMinGaps — fewer gaps than this and the fit is not attempted. A two-Gaussian
#: EM on 1-D data has five free parameters; 200 points gives the minor component (>= 5% by
#: weight, i.e. >= 10 gaps) something to stand on. UNMEASURED JUDGEMENT CALL below that.
TAU_FIT_MIN_GAPS = 200
#: Tuning.tauFitMinSeparationDecades — the two means must be at least this far apart on
#: log10 seconds. 0.8 decades is a factor of 6.3 — the smallest separation at which two
#: unit-variance log-normal modes still show a dip between them in a text histogram at
#: 0.1-decade bins (Halfaker et al. 2015 report separations of 2–3 decades on every system
#: they studied).
TAU_FIT_MIN_SEPARATION = 0.8
#: Tuning.tauFitMinComponentWeight — the minor component must hold at least this share of
#: the gaps, or "the second mode" is a handful of outliers the EM wrapped a Gaussian around.
TAU_FIT_MIN_WEIGHT = 0.05
#: The valley must land within half a decade of the clamp range — [95 s, 11,400 s] — or the
#: fit found a valley between two MACHINE modes, not between activity and absence, and the
#: clamp would turn nonsense into a confident number. MEASURED on the container corpus: a
#: fit on raw record gaps found modes at 7 ms (records of one turn flushed together) and
#: 3.4 s (the tool cadence), a valley at 0.1 s, and would have clamped it to 300 s.
TAU_FIT_VALLEY_MIN_LOG = math.log10(300.0) - 0.5
TAU_FIT_VALLEY_MAX_LOG = math.log10(3600.0) + 0.5
#: Floor on a component's variance in decades², so a pool of identical gaps (a robot on a
#: fixed cadence) cannot drive a sigma to 0 and the responsibilities to NaN.
TAU_FIT_VAR_FLOOR = 1e-4
TAU_FIT_MAX_ITERS = 500
TAU_FIT_TOL = 1e-10

ACTIVE_GAP_CAP = 120.0
#: Tuning.switchedRepoMinGapSec — equal to ACTIVE_GAP_CAP on purpose: a gap under the cap is
#: credited in full as continuous work, so a human who hops between two repos inside two
#: minutes is one sitting on two repos, not a switch.
SWITCH_MIN_GAP = ACTIVE_GAP_CAP
TAU_AUTONOMOUS = 1800.0  # after this long without presence the agent is on its own
TAU_RETURN_SPLIT = 7200.0  # presence after this much autonomy starts a NEW session
DAY_BOUNDARY_HOUR = 4
INTERRUPT_PREFIX = "[Request interrupted by user"
CLEAR_MARKER = "<command-name>/clear</command-name>"

END_REASONS = (
    "idle_gap",
    "human_returned",
    "day_boundary",
    "cleared",
    "switched_repo",
    "still_running",
)


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
        # A slash command is written as `<command-name>/x</command-name>` in the text. Only
        # `/clear` is a boundary; `/model`, `/effort`, `/compact` remain non-presence.
        if text is not None and CLEAR_MARKER in text:
            return "clear", True
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
            sid = r.get("sessionId")
            rec = {
                "ts": ts,
                "kind": kind,
                "presence": presence,
                "cwd": r.get("cwd"),
                "sid": sid if isinstance(sid, str) else None,
            }
            if extra is not None:
                rec.update(extra(r, i))
            out.append(rec)
    return out


# ------------------------------------------------------------------------ the tau fit
#
# Halfaker et al. (WWW 2015) — inter-activity times are bimodal on a log scale, and the
# session threshold belongs at the valley between the two modes, fitted per dataset, not
# at a convention. This is that fit: a two-component Gaussian mixture on log10(gap), by
# EM, standard library only, written so the Swift `ThresholdFitter` can reproduce every
# floating-point operation in the same order (spec/fixtures/boundaries/threshold_fit.json
# holds a gap list and the numbers this produces for it; the Swift test must match to 1e-6).
#
# THE UNIT IS THE HUMAN'S ACT, NOT THE MACHINE'S RECORD. Halfaker's events were edits and
# queries — each one a person doing something. A Claude Code transcript writes thousands
# of records per sitting, so the gaps between sittings are under 2% of record gaps and the
# mixture cannot see them; what it sees instead is the harness's write cadence (MEASURED
# above: modes at 7 ms and 3.4 s, valley 0.1 s). The v3 fit therefore runs on
# `presence_gaps` — the intervals between consecutive presence signals in a pool, WITHOUT
# resetting at idle gaps, so the between-sitting intervals are in the sample — and the
# resulting tau is applied to record gaps by rule 1. That is coherent: a record gap is
# never longer than the human gap that contains it, so a record gap past the human
# within-sitting valley is at least as strong a "the sitting ended" signal.
#
# No `sum()`: since Python 3.12 it is compensated (Neumaier) for floats, and Swift's
# `reduce(0, +)` is not. Every accumulation below is a plain left-to-right loop.


@dataclasses.dataclass
class TauFit:
    n: int  # gaps used (strictly positive only)
    m1: float  # lower mode, log10 seconds
    m2: float  # upper mode
    s1: float
    s2: float
    w1: float
    w2: float
    valley: float | None  # log10 seconds where the two weighted densities cross
    bimodal: bool
    reason: str  # why it is or is not bimodal, one phrase
    tau: float  # what the sessionizer should use
    source: str  # "fitted" | "fallback"

    def as_dict(self) -> dict:
        return dataclasses.asdict(self)


def _pdf(x: float, m: float, s: float) -> float:
    z = (x - m) / s
    return math.exp(-0.5 * z * z) / (s * math.sqrt(2.0 * math.pi))


def pool_gaps(records: list[dict]) -> list[float]:
    """Strictly positive gaps between consecutive timestamped records of ONE pool.

    Zero gaps are excluded: Claude Code writes several records at one instant (a turn's
    content blocks share a timestamp), and log10(0) is not a number. They are counted
    by the caller so the report can say how many were dropped.
    """
    ts = sorted(r["ts"] for r in records)
    out = []
    for i in range(1, len(ts)):
        g = ts[i] - ts[i - 1]
        if g > 0:
            out.append(g)
    return out


def presence_gaps(records: list[dict]) -> list[float]:
    """Strictly positive intervals between consecutive PRESENCE records of one pool — every
    one of them, across idle gaps too. This is the sample the v3 tau fit runs on."""
    ts = sorted(r["ts"] for r in records if r["presence"])
    out = []
    for i in range(1, len(ts)):
        g = ts[i] - ts[i - 1]
        if g > 0:
            out.append(g)
    return out


def fit_two_gaussians(xs: list[float]) -> tuple[float, float, float, float, float, float]:
    """EM for a 1-D two-Gaussian mixture. Returns (m1, m2, s1, s2, w1, w2) with m1 <= m2.

    Initialisation is deterministic — the means of the lower and upper halves of the
    sorted data, one shared variance, equal weights — so two implementations that follow
    this function operation for operation agree to rounding.
    """
    xs = sorted(xs)
    n = len(xs)
    half = n // 2
    acc = 0.0
    for x in xs[:half]:
        acc += x
    m1 = acc / half
    acc = 0.0
    for x in xs[half:]:
        acc += x
    m2 = acc / (n - half)
    acc = 0.0
    for x in xs:
        acc += x
    mean = acc / n
    acc = 0.0
    for x in xs:
        d = x - mean
        acc += d * d
    var = acc / n
    if var < TAU_FIT_VAR_FLOOR:
        var = TAU_FIT_VAR_FLOOR
    s1 = s2 = math.sqrt(var)
    w1 = w2 = 0.5

    for _ in range(TAU_FIT_MAX_ITERS):
        n1 = 0.0
        sx1 = 0.0
        sx2 = 0.0
        gammas = []
        for x in xs:
            p1 = w1 * _pdf(x, m1, s1)
            p2 = w2 * _pdf(x, m2, s2)
            tot = p1 + p2
            g = 0.5 if tot <= 0.0 else p1 / tot
            gammas.append(g)
            n1 += g
            sx1 += g * x
            sx2 += (1.0 - g) * x
        n2 = n - n1
        if n1 <= 1e-12 or n2 <= 1e-12:
            break  # a component vanished; the caller's weight floor rejects this fit
        nm1 = sx1 / n1
        nm2 = sx2 / n2
        sv1 = 0.0
        sv2 = 0.0
        for x, g in zip(xs, gammas):
            d1 = x - nm1
            d2 = x - nm2
            sv1 += g * d1 * d1
            sv2 += (1.0 - g) * d2 * d2
        v1 = sv1 / n1
        v2 = sv2 / n2
        if v1 < TAU_FIT_VAR_FLOOR:
            v1 = TAU_FIT_VAR_FLOOR
        if v2 < TAU_FIT_VAR_FLOOR:
            v2 = TAU_FIT_VAR_FLOOR
        ns1 = math.sqrt(v1)
        ns2 = math.sqrt(v2)
        nw1 = n1 / n
        nw2 = n2 / n
        delta = max(
            abs(nm1 - m1), abs(nm2 - m2), abs(ns1 - s1), abs(ns2 - s2), abs(nw1 - w1)
        )
        m1, m2, s1, s2, w1, w2 = nm1, nm2, ns1, ns2, nw1, nw2
        if delta < TAU_FIT_TOL:
            break

    if m1 > m2:
        m1, m2, s1, s2, w1, w2 = m2, m1, s2, s1, w2, w1
    return m1, m2, s1, s2, w1, w2


def find_valley(m1, m2, s1, s2, w1, w2) -> float | None:
    """The log-gap between the two means where the weighted densities cross, by bisection.

    None when the weighted lower component is not dominant at its own mean or the upper
    one is not dominant at its — there is then no crossing between them to call a valley.
    """

    def f(x: float) -> float:
        return w1 * _pdf(x, m1, s1) - w2 * _pdf(x, m2, s2)

    lo, hi = m1, m2
    flo, fhi = f(lo), f(hi)
    if not (flo > 0.0 and fhi < 0.0):
        return None
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if f(mid) > 0.0:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def fit_tau(gaps: list[float]) -> TauFit:
    """The v3 threshold: clamp(10**valley, TAU_MIN, TAU_MAX) when the sample is bimodal,
    else the fallback. `gaps` are seconds — the presence-to-presence intervals of every pool
    of every harness the user has (`presence_gaps`), pooled into one sample."""
    xs = [math.log10(g) for g in gaps if g > 0.0]
    n = len(xs)
    if n < TAU_FIT_MIN_GAPS:
        return TauFit(
            n, math.nan, math.nan, math.nan, math.nan, math.nan, math.nan, None, False,
            f"{n} gaps < {TAU_FIT_MIN_GAPS}", TAU_SESSION_FALLBACK, "fallback",
        )
    m1, m2, s1, s2, w1, w2 = fit_two_gaussians(xs)
    valley = find_valley(m1, m2, s1, s2, w1, w2)
    reason = "bimodal"
    bimodal = True
    if m2 - m1 < TAU_FIT_MIN_SEPARATION:
        bimodal, reason = False, f"modes {m2 - m1:.2f} decades apart < {TAU_FIT_MIN_SEPARATION}"
    elif min(w1, w2) < TAU_FIT_MIN_WEIGHT:
        bimodal, reason = False, f"minor component weight {min(w1, w2):.3f} < {TAU_FIT_MIN_WEIGHT}"
    elif valley is None:
        bimodal, reason = False, "no crossing between the modes"
    elif not (TAU_FIT_VALLEY_MIN_LOG <= valley <= TAU_FIT_VALLEY_MAX_LOG):
        bimodal, reason = (
            False,
            f"valley 10^{valley:.2f} = {10 ** valley:.1f} s outside the plausible range "
            f"[{10 ** TAU_FIT_VALLEY_MIN_LOG:.0f}, {10 ** TAU_FIT_VALLEY_MAX_LOG:.0f}] s",
        )
    else:
        # A crossing is only a valley if the mixture is lower there than at both modes.
        def mix(x: float) -> float:
            return w1 * _pdf(x, m1, s1) + w2 * _pdf(x, m2, s2)

        if not (mix(valley) < mix(m1) and mix(valley) < mix(m2)):
            bimodal, reason = False, "mixture has no dip between the modes"
    if bimodal and valley is not None:
        tau = 10.0**valley
        clamped = min(max(tau, TAU_MIN), TAU_MAX)
        if clamped != tau:
            reason = f"bimodal; 10^valley = {tau:.0f} s clamped to [{TAU_MIN:.0f}, {TAU_MAX:.0f}]"
        return TauFit(n, m1, m2, s1, s2, w1, w2, valley, True, reason, clamped, "fitted")
    return TauFit(n, m1, m2, s1, s2, w1, w2, valley, False, reason, TAU_SESSION_FALLBACK, "fallback")


def resolve_tau(spec: str | float | None, pools: dict[str, list[dict]]) -> tuple[float, TauFit | None]:
    """`--tau auto` fits over every pool together; a number is used as given; None is the
    fallback. Returns (tau, fit-or-None)."""
    if spec is None:
        return TAU_SESSION_FALLBACK, None
    if isinstance(spec, str) and spec != "auto":
        return float(spec), None
    if isinstance(spec, (int, float)):
        return float(spec), None
    gaps: list[float] = []
    for recs in pools.values():
        gaps.extend(presence_gaps(recs))
    fit = fit_tau(gaps)
    return fit.tau, fit


# ----------------------------------------------------------------------- pooling


def fold_by_session_lineage(keyed: list[tuple[str, dict]]) -> dict[str, list[dict]]:
    """Pools from (key, record) pairs, with every record of one native session id folded
    into that id's DOMINANT key.

    A session is one human's sitting, and the repository is an attribute of it, not a
    partition key. Claude Code stamps the shell's CURRENT cwd on every record, so a
    conversation whose shell `cd`s between the home directory and a repo scatters its
    records across two keys — MEASURED on the container corpus: one 2,231-record session
    held 332 runs of alternating cwd, all 15 human prompts under `/home/user` and 833 of
    the assistant's records under the repository, with a median gap of 0.4 s at each
    change. Pooled per record that sitting became two overlapping sessions, one with the
    prompts and zero commits and one with the commits and "0 prompts typed" — the
    plausible wrong number. (The reference machine shows the same shape in miniature:
    5 distinct cwds in one 30-minute transcript.)

    Dominance is by record count; ties go to the key of the id's earliest record, then to
    the smaller key string, so three implementations agree. Records without a session id
    keep their own key. Two conversations back to back in the same key still share a
    pool — that rule is unchanged from v1.
    """
    counts: dict[str, dict[str, int]] = {}
    earliest: dict[str, tuple[float, str]] = {}
    for key, r in keyed:
        sid = r.get("sid")
        if not sid:
            continue
        counts.setdefault(sid, {})
        counts[sid][key] = counts[sid].get(key, 0) + 1
        e = earliest.get(sid)
        if e is None or r["ts"] < e[0] or (r["ts"] == e[0] and key < e[1]):
            earliest[sid] = (r["ts"], key)
    home: dict[str, str] = {}
    for sid, by_key in counts.items():
        best = max(by_key.values())
        cands = sorted(k for k, n in by_key.items() if n == best)
        first_key = earliest[sid][1]
        home[sid] = first_key if first_key in cands else cands[0]
    pools: dict[str, list[dict]] = {}
    for key, r in keyed:
        sid = r.get("sid")
        pools.setdefault(home.get(sid, key) if sid else key, []).append(r)
    return pools


# ------------------------------------------------------------------- structural signals


def human_session_starts(pools: dict[str, list[dict]]) -> dict[str, list[float]]:
    """When a HUMAN opened a new native session in each pool.

    A native session id is new at its earliest timestamped record anywhere in the corpus
    (one id can appear in several pools: the working directory changes within a
    conversation, MEASURED 4 cwds in one session in the container corpus). The start
    counts only if that first record is a presence signal — a `claude -p` run stamps its
    prompt `sdk` with no human origin (MEASURED: 7 of 7 headless runs in the container
    corpus), and a robot starting in another repo says nothing about where the person is.
    """
    first: dict[str, tuple[float, str, bool]] = {}
    for key, recs in pools.items():
        for r in recs:
            sid = r.get("sid")
            if not sid:
                continue
            cur = first.get(sid)
            if cur is None or r["ts"] < cur[0]:
                first[sid] = (r["ts"], key, bool(r["presence"]))
    out: dict[str, list[float]] = {k: [] for k in pools}
    for ts, key, presence in first.values():
        if presence:
            out[key].append(ts)
    for k in out:
        out[k].sort()
    return out


def foreign_starts_for(pool: str, starts: dict[str, list[float]]) -> list[float]:
    """Human session starts in every pool EXCEPT `pool`, sorted."""
    out: list[float] = []
    for k, v in starts.items():
        if k != pool:
            out.extend(v)
    out.sort()
    return out


def _switch_in(gap_lo: float, gap_hi: float, foreign: list[float], min_gap: float) -> float | None:
    """The first foreign human start f with gap_lo + min_gap <= f < gap_hi, else None."""
    import bisect

    i = bisect.bisect_left(foreign, gap_lo + min_gap)
    if i < len(foreign) and foreign[i] < gap_hi:
        return foreign[i]
    return None


# ------------------------------------------------------------------------- sessionize


def sessionize(
    records: list[dict],
    tz: dt.tzinfo,
    tau: float = TAU_SESSION,
    cap: float = ACTIVE_GAP_CAP,
    tau_autonomous: float = TAU_AUTONOMOUS,
    tau_return: float = TAU_RETURN_SPLIT,
    foreign_starts: list[float] | None = None,
    switch_min_gap: float = SWITCH_MIN_GAP,
) -> list[dict]:
    """Cut one pool of timestamped records into sessions.

    `foreign_starts` are the instants a human opened a new native session in ANOTHER pool
    (`human_session_starts` / `foreign_starts_for`); None disables rule 2 (switched_repo),
    which is what a single-pool caller gets.
    """
    recs = sorted(records, key=lambda r: r["ts"])
    if not recs:
        return []
    foreign = sorted(foreign_starts) if foreign_starts else []

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

    def count(s: dict, r: dict) -> None:
        s["records"] += 1
        s["prompts"] += r["kind"] == "prompt"
        s["presence"] += r["presence"]

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

            # Rule 0 (v3): the previous record was a `/clear`. The human ended the
            # conversation on purpose, so the session ends there whatever the gap — even
            # an idle gap after it is silence AFTER the stop, not the stop itself. Credit
            # and ended_at are exactly as for idle_gap, so cleared and idle_gap differ in
            # name only, never in arithmetic.
            # Rule 1: idle gap. The boundary gap is credited like any other gap — capped,
            # to whichever clock was running when it began — and ended_at is extended by
            # the same amount so active can never exceed elapsed. Credit is a property of
            # the POOL, not of where the cut lands: without this, merging two sessions
            # would recover up to `cap` seconds and total active time would depend on tau
            # (MEASURED in the Swift engine before that fix: 4.5 h of drift across the
            # threshold range).
            # Rule 2 (v3): a human opened a new session in another pool at least
            # `switch_min_gap` after our last record and before our next. The sitting
            # moved; this session ends at the switch, credited as an idle end would be,
            # because from this pool's point of view that is what it is.
            switch = _switch_in(prev["ts"], r["ts"], foreign, switch_min_gap) if foreign else None
            if prev["kind"] == "clear" or gap > tau or switch is not None:
                if prev["kind"] == "clear":
                    reason = "cleared"
                elif gap > tau:
                    reason = "idle_gap"
                else:
                    reason = "switched_repo"
                    credit = min(switch - prev["ts"], cap)
                cur["active"] += credit
                if autonomous:
                    cur["autonomous"] += credit
                else:
                    cur["attended"] += credit
                close(cur, reason, credit)
                cur = open_session(r)
                last_presence = r["ts"] if r["presence"] else None
                run_start = None if r["presence"] else r["ts"]
                count(cur, r)
                continue

            # Rule 4: the day boundary falls in this gap while autonomous.
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
                count(cur, r)
                if r["presence"]:
                    last_presence = r["ts"]
                    run_start = None
                continue

            # Rule 3: the human returned after a long autonomous run. A run that never had
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
                count(cur, r)
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

        count(cur, r)

    if cur is not None:
        # A pool whose LAST record is a `/clear` ended there, final, with no trailing
        # credit — there is no next record to measure a gap against, as for still_running.
        close(cur, "cleared" if recs[-1]["kind"] == "clear" else "still_running", 0.0)
    return sessions


def sessionize_pools(
    pools: dict[str, list[dict]],
    tz: dt.tzinfo,
    tau: float = TAU_SESSION,
    **kw,
) -> dict[str, list[dict]]:
    """Every pool, with rule 2 wired: each pool sees the human session starts of the others."""
    starts = human_session_starts(pools)
    return {
        key: sessionize(recs, tz, tau=tau, foreign_starts=foreign_starts_for(key, starts), **kw)
        for key, recs in pools.items()
    }


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


def describe_fit(fit: TauFit, unit: str = "presence intervals") -> str:
    if math.isnan(fit.m1):
        return f"tau fit: {fit.n} {unit}: {fit.reason} -> fallback {fit.tau:.0f} s"
    v = "none" if fit.valley is None else f"{fit.valley:.3f} ({10 ** fit.valley:.0f} s)"
    return (
        f"tau fit over {fit.n} {unit}: modes 10^{fit.m1:.2f}={10 ** fit.m1:.1f}s (w {fit.w1:.3f}, "
        f"sd {fit.s1:.2f}) and 10^{fit.m2:.2f}={10 ** fit.m2:.0f}s (w {fit.w2:.3f}, sd {fit.s2:.2f}); "
        f"valley log10 {v}; {fit.reason} -> tau {fit.tau:.0f} s ({fit.source})"
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("root", help="~/.claude/projects, a project dir, or one .jsonl")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--tz", default=None, help="IANA zone; default: system local")
    ap.add_argument(
        "--tau",
        default=None,
        help="'auto' fits the idle-gap threshold to this corpus (v3); a number uses it as "
        "given; omitted uses the 900 s fallback",
    )
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
    # ... and then folded by session lineage: one native session id can be written to
    # several project directories as its cwd changes (the container corpus has one id in
    # three), and a sitting is not split by where the harness put the file.
    keyed: list[tuple[str, dict]] = []
    for f in files:
        key = f.parent.name if root.is_dir() else f.stem
        keyed.extend((key, r) for r in load_records(f))
    pools = fold_by_session_lineage(keyed)

    tau, fit = resolve_tau(args.tau, pools)

    all_sessions: list[dict] = []
    all_intervals: list[float] = []
    for key, cut in sessionize_pools(pools, tz, tau=tau).items():
        for s in cut:
            s["pool"] = key
            all_sessions.append(s)
        all_intervals.extend(presence_intervals(pools[key], tau=tau))
    all_sessions.sort(key=lambda s: s["started_at"])

    # Sensitivity: how many extra cuts do rules 3 and 4 make at candidate thresholds?
    grid = {}
    for ta in (600, 1200, 1800, 3600):
        for tr in (3600, 7200, 14400):
            n = sum(
                len(cut)
                for cut in sessionize_pools(
                    pools, tz, tau=tau, tau_autonomous=ta, tau_return=tr
                ).values()
            )
            grid[f"tau_autonomous={ta},tau_return={tr}"] = n
    baseline = sum(
        len(sessionize(recs, tz, tau=tau, tau_autonomous=1e12, tau_return=1e12))
        for recs in pools.values()
    )

    report = {
        "files": len(files),
        "pools": len(pools),
        "records": sum(len(r) for r in pools.values()),
        "tau": tau,
        "tau_fit": fit.as_dict() if fit else None,
        "sessions_rule1_only": baseline,
        "sessions_v3_default": len(all_sessions),
        "sessions_by_end_reason": {
            k: sum(1 for s in all_sessions if s["end_reason"] == k) for k in END_REASONS
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
    if fit is not None:
        print(describe_fit(fit))
    else:
        print(f"tau {tau:.0f} s ({'fallback' if args.tau is None else 'given'})")
    print(
        f"sessions: rule 1 only {baseline}  ·  v3 default {len(all_sessions)}  {report['sessions_by_end_reason']}"
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
