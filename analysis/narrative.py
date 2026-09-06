"""The "how you work" page: measurements in, a few paragraphs about one person out.

`profile.py` measures the corpus and `patterns.py` compares parts of it against each
other. Both produce numbers, and a wall of numbers is what a person looks at once. This
module is the layer that says what they MEAN for the person who made them, and it is the
only place in the profile where a model writes anything.

It is deliberately the last step and the thinnest one. Everything the model is allowed to
say is already in the input as a measurement, the prompt forbids inventing anything, and
`verify` drops any claim carrying a number the input did not contain. The model's job is
sentences, not arithmetic.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import pathlib
import re
from collections.abc import Mapping, Sequence

from . import run as rn

LOG = logging.getLogger(__name__)

HERE = pathlib.Path(__file__).resolve().parent
SCHEMA_PATH = HERE / "narrative_schema.json"
PROMPT_PATH = HERE / "narrative_prompt.txt"

#: Bumped when the shape or the rules change, so a stored narrative says which it is.
#: Read from the generated schema rather than declared here: spec/narrative.v1.json is
#: the one place the version lives, and a second copy would eventually disagree with it.
NARRATIVE_VERSION = json.loads((HERE / "narrative_schema.json").read_text())["x-version"]

#: Numbers this small are ordinary English ("one experiment", "3 of 14") and are not worth
#: checking against the input. Above it, a number in the prose has to have come from
#: somewhere.
_TRIVIAL_MAX = 12

_NUMBER = re.compile(r"\d+(?:\.\d+)?")

#: A comma sitting between a digit and exactly three more digits is a thousands
#: separator, not punctuation. MEASURED, and the reason this exists: the first narrative
#: generated from a real corpus said "Bash accounts for 72% of your 1,211 tool calls",
#: which is exactly what the input said, and the check deleted the whole sentence because
#: it read "1" and "211" and could not find 211 anywhere. A verification step that throws
#: away correct output is worse than no verification step (CLAUDE.md), so the separator is
#: removed from BOTH sides before either is read. The lookahead for a fourth digit keeps
#: "1,2345" alone, and requiring three digits keeps a list ("5, 10") alone.
_THOUSANDS = re.compile(r"(?<=\d),(?=\d{3}(?!\d))")


def load_schema() -> dict:
    schema = json.loads(SCHEMA_PATH.read_text())
    # The CLI resolves `$schema` as a reference and has no draft-2020-12 meta-schema
    # registered, so it rejects the whole document (analysis/run.py). `x-version` and
    # `$comment` are ours, not the decoder's.
    for k in ("$schema", "$comment", "x-version"):
        schema.pop(k, None)
    return schema


def build_input(
    *,
    profile: Mapping,
    findings: Sequence,
    session_headlines: Sequence[str] = (),
    trends: Sequence = (),
    fanout: object = None,
    contributions: object = None,
) -> str:
    """The user message: every measurement, laid out flat, nothing else.

    Refused metrics are included WITH their reason rather than dropped, so the model can
    see that the question was asked and answered "we cannot know" rather than guessing
    that it was never asked.
    """
    arch = profile.get("archetype") or {}
    lines: list[str] = []
    add = lines.append

    # THE FINDINGS COME FIRST, and the metrics are demoted to a reference block at the
    # bottom. FOUND BY READING THE OUTPUT: with the metrics at the top, three of four
    # paragraphs were built out of them and read "your prompts skew planning to execution
    # 2.33 to 1" and "36.5% of your time is spent with it running on its own", which are
    # true, jargon free, and still say nothing a person can act on. The findings are the
    # only material in here guaranteed to carry a cost or a move, so they are what the
    # page should be made of, and the ORDER of the input turns out to be a stronger
    # instruction than a rule in the prompt.
    add("WHAT THE COMPARISONS FOUND. Build the page out of THESE.")
    if findings:
        add("Each one already cleared a sample size bar and an effect bar, and each one")
        add("already names a cost or a move. Say them in your own words, keep the numbers.")
        for f in findings:
            add("")
            add(f"  {f.text}")
            add(f"    left  {f.left}")
            add(f"    right {f.right}")
    else:
        add("  Nothing cleared the bars: not enough sessions yet to compare two groups of")
        add("  them honestly. Do not invent a comparison. Write a shorter page from the")
        add("  archetype and the background numbers below, and say less.")

    if trends:
        add("")
        add("HOW IT HAS MOVED since the window before this one. Second best material.")
        add("A trend is something they can act on; a level is something they already knew.")
        for t in trends:
            if t.steady:
                add(f"  {t.label}: steady, {t.before:g} then {t.now:g}")
            else:
                verdict = "" if t.good is None else (
                    "  (the way they want it)" if t.good else "  (worth a look)"
                )
                add(
                    f"  {t.label}: {t.direction} {abs(t.move) * 100:.0f}%, "
                    f"{t.before:g} -> {t.now:g}{verdict}"
                )

    if fanout is not None and getattr(fanout, "agents", 0):
        add("")
        add("HOW MANY AGENTS THEY RUN AT ONCE")
        add(
            f"  {fanout.agents} agents, up to {fanout.max_concurrent} at the same moment, "
            f"{fanout.agent_seconds / 3600:.1f} hours of agent work"
        )
        add(f"  {fanout.produced} of them produced something")
        add(f"  by kind: {', '.join(f'{k} {v}' for k, v in fanout.by_type.items())}")

    if contributions is not None and getattr(contributions, "total", 0):
        share = contributions.assisted_share
        add("")
        add("THEIR COMMITS, split by whether an agent was in the room")
        add(
            f"  {contributions.total} commits over {contributions.active_days} days"
            + (f", {round(share * 100)}% with an agent" if share is not None else "")
        )
        add(
            f"  longest run of days they shipped: {contributions.longest_streak}, "
            f"currently on {contributions.current_streak}"
        )

    add("")
    add("THE ARCHETYPE THE RULES CHOSE")
    if arch.get("name"):
        add(f"  {arch['name']}, confidence {arch.get('confidence')}")
        add(f"  chosen by: {arch.get('rule')}")
        add(f"  their value {arch.get('value')} against a threshold of {arch.get('threshold')}")
        runners = ", ".join(
            f"{r['name']} {r['score']}" for r in (arch.get("runners_up") or []) if r.get("score")
        )
        if runners:
            add(f"  runners up: {runners}")
    else:
        add(f"  none: {arch.get('reason')}")

    if session_headlines:
        add("")
        add("RECENT SESSIONS, as previously summarised")
        for h in session_headlines:
            add(f"  {h}")

    add("")
    add("BACKGROUND NUMBERS. Support, never subject.")
    add("A sentence whose only content is one of these is a sentence to delete: they say")
    add("what is true, not what it cost or what to do about it.")

    totals = profile.get("totals") or {}
    add("")
    add("  totals")
    for k, v in totals.items():
        add(f"    {k}: {v}")

    add("")
    add("  metrics")
    for name, m in (profile.get("metrics") or {}).items():
        if m.get("value") is None:
            add(f"    {name}: REFUSED, {m.get('reason')}")
        else:
            extra = {
                k: v
                for k, v in m.items()
                if k not in ("value", "unit", "n", "basis", "reason") and v is not None
            }
            tail = f"  ({', '.join(f'{k}={v}' for k, v in extra.items())})" if extra else ""
            add(f"    {name}: {m['value']} {m['unit']}, over n={m['n']}{tail}")

    tools = profile.get("top_tools") or []
    if tools:
        add("")
        add("  tools reached for")
        for t in tools:
            add(f"    {t['tool']}: {t['calls']} calls, {round(t['share'] * 100)}%")

    models = profile.get("model_mix") or []
    if models:
        add("")
        add("  models")
        for m in models:
            add(f"    {m['model']}: {round(m['share'] * 100)}% of output tokens")

    return "\n".join(lines)


def numbers_in(text: str) -> set[str]:
    """Every number in a string, normalised, ignoring the trivially small ones.

    Normalisation is exact, not float-formatted: `1211` and `1,211` are the same number
    and `1234567` and `1234568` are not. `%g` would round both of the latter to
    `1.23457e+06` and quietly agree they matched, which on a token count is a plausible
    wrong answer from the very check that exists to catch plausible wrong answers.
    """
    out = set()
    for tok in _NUMBER.findall(_THOUSANDS.sub("", text)):
        whole, _, frac = tok.partition(".")
        frac = frac.rstrip("0")
        whole = whole.lstrip("0") or "0"
        if not frac and int(whole) <= _TRIVIAL_MAX:
            continue
        out.add(f"{whole}.{frac}" if frac else whole)
    return out


def known_numbers(source: str) -> set[str]:
    """Every number the model is allowed to write, given what it was shown.

    Two things count as known, and the second is here because of a MEASURED false
    positive. The input states shares as fractions (`night_commit_share: 0.44`) and the
    natural English for a share is a percentage, so a narrative that said "44% of your
    commits" had a correct, sourced sentence deleted for saying 0.44 out loud. Every known
    value in [0, 1] therefore also licenses its percentage, exact and rounded.

    The cost is stated plainly: this widens the allowed set by at most one integer under
    101 per fraction in the input, so a fabricated small percentage can now slip through.
    That is the right trade. The claims worth catching are the ones with a magnitude
    nobody could check by eye (1,211 tool calls, 4,089 lines, a 484-call run), and a check
    that deletes correct sentences is worse than no check at all (CLAUDE.md).
    """
    known = numbers_in(source)
    for tok in list(known):
        value = float(tok)
        if 0.0 <= value <= 1.0:
            known.add(f"{value * 100:.10g}")
            known.add(str(round(value * 100)))
    return known


def verify(narrative: dict, source: str) -> tuple[dict, list[str]]:
    """Drop any sentence carrying a number the input did not contain.

    The prompt already forbids inventing figures. This is the check that makes the ban
    real, and it exists for the reason every other check in this repo does: a fabricated
    number in a sentence about somebody's own habits is the most believable wrong thing
    the app could print. Returns (narrative, the dropped strings).
    """
    known = known_numbers(source)
    dropped: list[str] = []

    def ok(text: str) -> bool:
        unknown = numbers_in(text) - known
        if unknown:
            dropped.append(f"{text}  [not in the input: {', '.join(sorted(unknown))}]")
            return False
        return True

    out = dict(narrative)
    if not ok(out.get("archetype_line", "")):
        out["archetype_line"] = ""
    out["how_you_work"] = [p for p in out.get("how_you_work") or [] if ok(p)]
    for key in ("strengths", "watch_outs"):
        out[key] = [
            item
            for item in out.get(key) or []
            if ok(item.get("text", "")) and ok(item.get("evidence", ""))
        ]
    if not ok(out.get("one_experiment", "")):
        out["one_experiment"] = ""
    return out, dropped


def write(
    *,
    profile: Mapping,
    findings: Sequence = (),
    session_headlines: Sequence[str] = (),
    trends: Sequence = (),
    fanout: object = None,
    contributions: object = None,
    model: str = rn.DEFAULT_MODEL,
) -> dict:
    """Generate the narrative through the user's own `claude`, verified. Raises
    `run.AnalysisError` when the CLI is missing or the call fails."""
    source = build_input(
        profile=profile,
        findings=findings,
        session_headlines=session_headlines,
        trends=trends,
        fanout=fanout,
        contributions=contributions,
    )
    system = PROMPT_PATH.read_text()
    doc, envelope = rn.call_claude(system, source, load_schema(), model)
    doc, dropped = verify(doc, source)
    # A drop is not a detail to swallow. Every one is a sentence the model wrote about
    # this person carrying a figure that came from nowhere, and the only way to know
    # whether the ban is working is to be able to read what it caught.
    for claim in dropped:
        LOG.warning("narrative: dropped an invented number: %s", claim)
    # The dash ban is the same one the session analysis obeys, and it is applied the same
    # way: rewritten, never rejected. Throwing away a paid call over punctuation is not a
    # trade (analysis/run.py).
    doc, dashes = rn.dedash(doc)
    doc["narrative_version"] = NARRATIVE_VERSION
    # The envelope lists every model the CLI touched, including a small bookkeeping one;
    # the analyst is the one that wrote the output tokens (analysis/run.py).
    usage = envelope.get("modelUsage") or {}
    doc["model"] = (
        max(usage, key=lambda k: usage[k].get("outputTokens", 0))
        if usage
        else (envelope.get("model") or model)
    )
    doc["generated_at"] = dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    doc["dashes_rewritten"] = dashes
    doc["invented_numbers_dropped"] = len(dropped)
    return doc


__all__ = ["NARRATIVE_VERSION", "build_input", "load_schema", "numbers_in", "verify", "write"]
