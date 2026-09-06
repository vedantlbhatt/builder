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
NARRATIVE_VERSION = 1

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
    schema.pop("$schema", None)
    return schema


def build_input(
    *,
    profile: Mapping,
    findings: Sequence,
    session_headlines: Sequence[str] = (),
) -> str:
    """The user message: every measurement, laid out flat, nothing else.

    Refused metrics are included WITH their reason rather than dropped, so the model can
    see that the question was asked and answered "we cannot know" rather than guessing
    that it was never asked.
    """
    arch = profile.get("archetype") or {}
    lines: list[str] = []
    add = lines.append

    add("ARCHETYPE")
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

    totals = profile.get("totals") or {}
    add("")
    add("TOTALS")
    for k, v in totals.items():
        add(f"  {k}: {v}")

    add("")
    add("METRICS")
    for name, m in (profile.get("metrics") or {}).items():
        if m.get("value") is None:
            add(f"  {name}: REFUSED, {m.get('reason')}")
        else:
            extra = {
                k: v
                for k, v in m.items()
                if k not in ("value", "unit", "n", "basis", "reason") and v is not None
            }
            tail = f"  ({', '.join(f'{k}={v}' for k, v in extra.items())})" if extra else ""
            add(f"  {name}: {m['value']} {m['unit']}, over n={m['n']}{tail}")

    tools = profile.get("top_tools") or []
    if tools:
        add("")
        add("TOOLS YOU REACH FOR")
        for t in tools:
            add(f"  {t['tool']}: {t['calls']} calls, {round(t['share'] * 100)}%")

    models = profile.get("model_mix") or []
    if models:
        add("")
        add("MODELS")
        for m in models:
            add(f"  {m['model']}: {round(m['share'] * 100)}% of output tokens")

    if findings:
        add("")
        add("COMPARATIVE FINDINGS, each already checked for sample size and effect")
        for f in findings:
            add(f"  {f.text}")
            add(f"    left  {f.left}")
            add(f"    right {f.right}")

    if session_headlines:
        add("")
        add("RECENT SESSIONS, as previously summarised")
        for h in session_headlines:
            add(f"  {h}")

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


def verify(narrative: dict, source: str) -> tuple[dict, list[str]]:
    """Drop any sentence carrying a number the input did not contain.

    The prompt already forbids inventing figures. This is the check that makes the ban
    real, and it exists for the reason every other check in this repo does: a fabricated
    number in a sentence about somebody's own habits is the most believable wrong thing
    the app could print. Returns (narrative, the dropped strings).
    """
    known = numbers_in(source)
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
    model: str = rn.DEFAULT_MODEL,
) -> dict:
    """Generate the narrative through the user's own `claude`, verified. Raises
    `run.AnalysisError` when the CLI is missing or the call fails."""
    source = build_input(profile=profile, findings=findings, session_headlines=session_headlines)
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
    doc["model"] = envelope.get("model") or model
    doc["dashes_rewritten"] = dashes
    doc["invented_numbers_dropped"] = len(dropped)
    return doc


__all__ = ["NARRATIVE_VERSION", "build_input", "load_schema", "numbers_in", "verify", "write"]
