"""The build post: what somebody made, drafted from the work that made it.

`profile.py` and `patterns.py` describe the PERSON. `brag.py` turns that into stats
somebody might post. This module is the other thing entirely, and it is the one a feed of
builders is actually made of: **what you built**, written so another builder would want to
look at it.

WHERE THE HARD PART COMES FROM, which is the point of this module. "I built a transit app"
is a sentence. "The map kept dropping overlays because the native layer applies inserts
before removes" is a post, and it is the only thing here another builder cannot get
anywhere else. So the difficulty is not asked for as an opinion: the measured struggle in
the transcripts is handed over as candidates, and the model picks among them and says what
it turned out to be.

  * runs of consecutive failures (`patterns._stuck_in_a_loop`)
  * files rewritten over and over in one sitting
  * long stretches where nothing was written, tested or committed
  * commits that revert something

That is how the process measurements earn a place in a build post: not as the subject,
as the evidence for the story.

WHAT IS DELIBERATELY ABSENT: hours, tokens, dollars, archetype, scores. A build post that
leads with how long it took is a post about the poster. Those live on the profile.

Everything here needs commit messages, file names and prompt text, none of which leave the
machine (privacy/upload-contract.json). The post is drafted where the work is and the
author publishes what they choose to publish.
"""

from __future__ import annotations

import collections
import dataclasses
import datetime as dt
import json
import logging
import pathlib
import re
from collections.abc import Mapping, Sequence

from . import run as rn

LOG = logging.getLogger(__name__)

HERE = pathlib.Path(__file__).resolve().parent
SCHEMA_PATH = HERE / "shipped_schema.json"
PROMPT_PATH = HERE / "shipped_prompt.txt"

SHIPPED_VERSION = json.loads(SCHEMA_PATH.read_text())["x-version"]

#: How many of the loudest struggles to hand over as candidates for the hard part. More
#: than a handful and the model picks the one that reads best rather than the one that
#: cost the most.
TOP_STRUGGLES = 5

#: A file rewritten this many times in one sitting was being fought with, not edited.
#: Same cut as `patterns.REWORK_WRITES`, imported rather than copied.
_REVERT = re.compile(r"\b(revert|roll ?back|undo)\b", re.I)


@dataclasses.dataclass(frozen=True)
class Struggle:
    """One measured piece of difficulty, with what it cost."""

    kind: str
    text: str
    seconds: float = 0.0


def load_schema() -> dict:
    schema = json.loads(SCHEMA_PATH.read_text())
    for k in ("$schema", "$comment", "x-version"):
        schema.pop(k, None)
    return schema


# ------------------------------------------------------------------ what was hard


def struggles(sessions: Sequence, commits: Sequence[str] = ()) -> list[Struggle]:
    """Every measured piece of difficulty in this span, loudest first.

    `sessions` are `patterns.SessionEvents`. Nothing here is a judgement about whether the
    work was HARD in some absolute sense; it is a list of places the transcripts show a
    person or an agent getting stuck, which is what a reader recognises.
    """
    from . import patterns as pat

    out: list[Struggle] = []

    for s in sessions:
        # A failure is the error event AFTER the call, not a flag on the call
        # (patterns.py, found by running it on a corpus with 128 errors and 0 loops).
        seq = [e for e in s.events if e.kind in ("tool", "result_error")]
        run, start, worst, worst_cmd, worst_secs = 0, None, 0, "", 0.0
        i = 0
        while i < len(seq):
            e = seq[i]
            if e.kind != "tool":
                i += 1
                continue
            failed = i + 1 < len(seq) and seq[i + 1].kind == "result_error"
            if failed:
                run += 1
                if start is None:
                    start = e.ts
                if run > worst:
                    worst, worst_cmd, worst_secs = run, _what_failed(e), e.ts - start
                i += 2
                continue
            run, start = 0, None
            i += 1
        if worst >= pat.STUCK_FAILURES:
            out.append(
                Struggle(
                    "failure_loop",
                    f"{worst} failures in a row on `{_short(worst_cmd)}` before anything changed",
                    worst_secs,
                )
            )

        touched: collections.Counter[str] = collections.Counter()
        first_last: dict[str, list[float]] = {}
        for e in s.events:
            if pat._wrote(e) and e.path:
                touched[e.path] += 1
                first_last.setdefault(e.path, []).append(e.ts)
        for path, n in touched.most_common(2):
            if n >= pat.REWORK_WRITES:
                span = max(first_last[path]) - min(first_last[path])
                out.append(
                    Struggle(
                        "rewritten",
                        f"{path.rsplit('/', 1)[-1]} rewritten {n} times in one sitting",
                        span,
                    )
                )

        for calls, secs in pat._runs_with_nothing_to_show(s):
            if calls >= pat.SPIN_TOOL_CALLS:
                out.append(
                    Struggle(
                        "went_nowhere",
                        f"{calls} tool calls with nothing written, tested or committed",
                        secs,
                    )
                )

    for message in commits:
        if _REVERT.search(message):
            out.append(Struggle("reverted", f"reverted: {_short(message, 120)}"))

    # ROUND-ROBIN BY KIND, not straight by cost. Sorting on seconds alone handed the
    # model five identical "N tool calls with nothing to show" lines, because a long spin
    # always outweighs a failure loop that resolved in ninety seconds, and a list with one
    # flavour in it gives the post one thing to say. The reader wants the loop AND the
    # rewrite AND the spin; the loudest of each is what makes a story.
    by_kind: dict[str, list[Struggle]] = {}
    for st in sorted(out, key=lambda s: -s.seconds):
        by_kind.setdefault(st.kind, []).append(st)
    ordered: list[Struggle] = []
    while any(by_kind.values()):
        for kind in sorted(by_kind, key=lambda k: -(by_kind[k][0].seconds if by_kind[k] else 0)):
            if by_kind[kind]:
                ordered.append(by_kind[kind].pop(0))
    return ordered


def _what_failed(e) -> str:
    """What to call the thing that kept failing: a command, or a tool name.

    `Ev.text` is the command line for a shell call and the serialised INPUT for everything
    else, so using it blindly put `{"analysis_version":1,"model":"claude-sonnet-5",...` in
    front of the model as the thing that was hard. A person says "the schema call kept
    failing", not the first sixty characters of its payload.
    """
    from . import digest as dg

    if e.tool in dg.SHELL_TOOLS and e.text:
        return e.text
    return e.tool or "the same call"


def _short(text: str, cap: int = 60) -> str:
    one = " ".join((text or "").split())
    return one if len(one) <= cap else one[: cap - 1] + "…"


# ---------------------------------------------------------------------- the input


#: Where a repository states its own dependencies. Read-only, top level and one directory
#: down, because a monorepo keeps the phone's manifest in `mobile/` and the server's in
#: `server/`.
MANIFESTS = (
    "package.json",
    "requirements.txt",
    "pyproject.toml",
    "Package.swift",
    "go.mod",
    "Cargo.toml",
    "Gemfile",
    "composer.json",
)

#: Enough to describe a stack, few enough that the model is not reading a lockfile.
MAX_DEPENDENCIES = 120


def stack_evidence(root: str | None) -> list[str]:
    """What the repository says it depends on, from its own manifests.

    FOUND BY RUNNING IT. The first version of this module handed the model file basenames
    and commit subjects and nothing else, so a post about an Expo app with a FastAPI
    backend had `Expo`, `FastAPI` and `Postgres` deleted by the stack check as
    unsupported, and kept `Swift`, `Playwright` and `EAS`, which happened to appear in a
    filename. Every one of those deletions was of a TRUE claim.

    The check was not wrong; the evidence was too thin. A dependency manifest is the
    repository stating its own stack, which is ground truth rather than a guess, and it is
    the only place that answer exists.
    """
    if not root:
        return []
    base = pathlib.Path(root)
    names: list[str] = []
    for path in _manifest_paths(base):
        try:
            text = path.read_text(errors="replace")[:200_000]
        except OSError:
            continue
        names.extend(_names_in(path.name, text))
    seen, out = set(), []
    for n in names:
        key = n.lower()
        if key not in seen:
            seen.add(key)
            out.append(n)
    return out[:MAX_DEPENDENCIES]


def _manifest_paths(base: pathlib.Path) -> list[pathlib.Path]:
    found = []
    for name in MANIFESTS:
        top = base / name
        if top.is_file():
            found.append(top)
    try:
        children = sorted(d for d in base.iterdir() if d.is_dir() and not d.name.startswith("."))
    except OSError:
        return found
    for child in children[:20]:
        for name in MANIFESTS:
            nested = child / name
            if nested.is_file():
                found.append(nested)
    return found


def _names_in(filename: str, text: str) -> list[str]:
    """Dependency names only. Versions are noise and a lockfile is not a stack."""
    if filename in ("package.json", "composer.json"):
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return []
        out = []
        for key in ("dependencies", "devDependencies", "peerDependencies", "require"):
            block = data.get(key)
            if isinstance(block, dict):
                out.extend(str(k) for k in block)
        return out
    if filename == "requirements.txt":
        return [
            re.split(r"[<>=!~\[; ]", line.strip(), 1)[0]
            for line in text.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
    if filename == "pyproject.toml":
        # Names only, from any `dependencies = [...]` block. A TOML parser would be
        # better and is not worth a dependency in a package that deliberately has none.
        return re.findall(r'^\s*"([A-Za-z0-9_.\-]+)', text, re.M)
    if filename == "Package.swift":
        return re.findall(r'\.package\([^)]*url:\s*"[^"]*/([A-Za-z0-9_.\-]+?)(?:\.git)?"', text)
    if filename == "go.mod":
        return re.findall(r"^\s+([\w.\-]+/[\w./\-]+)\s+v", text, re.M)
    if filename == "Cargo.toml":
        return re.findall(r"^\s*([A-Za-z0-9_\-]+)\s*=", text, re.M)
    if filename == "Gemfile":
        return re.findall(r"^\s*gem\s+['\"]([^'\"]+)", text, re.M)
    return []


def build_input(
    *,
    project: str,
    since: dt.date | None,
    until: dt.date | None,
    session_summaries: Sequence[Mapping] = (),
    commits: Sequence[str] = (),
    files: Sequence[str] = (),
    struggle_list: Sequence[Struggle] = (),
    dependencies: Sequence[str] = (),
) -> str:
    """Everything the model is allowed to describe, and nothing else.

    The order is the instruction: what got done first, what was hard second, the raw
    material last. The narrative page learned this the hard way (docs/narrative.md) and
    the lesson transfers, because a model handed a list of file names at the top writes a
    post made of file names.
    """
    lines: list[str] = []
    add = lines.append

    add(f"PROJECT: {project}")
    if since and until:
        add(f"WINDOW: {since} to {until}")

    add("")
    add("WHAT THE SESSIONS SAY GOT DONE. This is the subject of the post.")
    if session_summaries:
        for s in session_summaries:
            add("")
            add(f"  {s.get('headline') or '(no headline)'}")
            if s.get("summary"):
                add(f"    {s['summary']}")
            for h in s.get("highlights") or []:
                add(f"    - {h}")
    else:
        add("  Nothing has been analysed in this window. Work only from the commits below,")
        add("  and if they do not say what was built, say less rather than guessing.")

    add("")
    add("WHAT WAS HARD. Pick from THESE. Do not invent a difficulty.")
    if struggle_list:
        add("Each one is measured from the transcripts: somewhere the work actually stuck.")
        for st in struggle_list[:TOP_STRUGGLES]:
            cost = f"  ({_mins(st.seconds)})" if st.seconds >= 60 else ""
            add(f"  [{st.kind}] {st.text}{cost}")
    else:
        add("  Nothing in this window shows the work getting stuck. `hard_part` is null.")

    if commits:
        add("")
        add("COMMITS in the window, as written")
        for c in commits:
            add(f"  {_short(c, 140)}")

    if files:
        add("")
        add("FILES touched, by name only. Never list these in the post.")
        add("  " + ", ".join(files))

    if dependencies:
        add("")
        add("WHAT THE PROJECT DEPENDS ON, from its own manifests. `stack` comes from here.")
        add("  " + ", ".join(dependencies))

    return "\n".join(lines)


def _mins(seconds: float) -> str:
    m = round(seconds / 60)
    return f"{m} min" if m < 60 else f"{m // 60}h {m % 60:02d}m"


# ------------------------------------------------------------------ the checking


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9.+#]{2,}", (text or "").lower())}


def verify(post: dict, source: str) -> tuple[dict, list[str]]:
    """Drop any part of the stack the work never showed.

    The prose is checked by a person before it is published, and prose is the part a
    model is actually good at. `stack` is different: it is a list of claims a reader will
    take as fact, it is the easiest thing in the world to guess from the shape of a
    project ("it's a React Native app, so: Redux"), and a wrong entry is a public claim
    the author never made. So every entry has to appear in the input somewhere.

    Returns (post, the dropped strings).
    """
    known = _words(source)
    dropped: list[str] = []
    kept = []
    for item in post.get("stack") or []:
        # A multi-word entry ("React Native") is kept when every word of it appears.
        if _words(item) <= known:
            kept.append(item)
        else:
            dropped.append(f"stack: {item}  [not in the work]")
    post["stack"] = kept
    return post, dropped


def write(
    *,
    project: str,
    since: dt.date | None = None,
    until: dt.date | None = None,
    session_summaries: Sequence[Mapping] = (),
    commits: Sequence[str] = (),
    files: Sequence[str] = (),
    struggle_list: Sequence[Struggle] = (),
    dependencies: Sequence[str] = (),
    model: str = rn.DEFAULT_MODEL,
) -> dict:
    """Draft the build post through the user's own `claude`, checked. Raises
    `run.AnalysisError` when the CLI is missing or the call fails."""
    source = build_input(
        project=project,
        since=since,
        until=until,
        session_summaries=session_summaries,
        commits=commits,
        files=files,
        struggle_list=struggle_list,
        dependencies=dependencies,
    )
    doc, envelope = rn.call_claude(PROMPT_PATH.read_text(), source, load_schema(), model)
    doc, dropped = verify(doc, source)
    for claim in dropped:
        LOG.warning("shipped: dropped an unsupported claim: %s", claim)
    doc, dashes = rn.dedash(doc)
    doc["shipped_version"] = SHIPPED_VERSION
    usage = envelope.get("modelUsage") or {}
    doc["model"] = (
        max(usage, key=lambda k: usage[k].get("outputTokens", 0))
        if usage
        else (envelope.get("model") or model)
    )
    doc["generated_at"] = dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    doc["dashes_rewritten"] = dashes
    doc["unsupported_claims_dropped"] = len(dropped)
    return doc


__all__ = [
    "SHIPPED_VERSION",
    "Struggle",
    "build_input",
    "load_schema",
    "stack_evidence",
    "struggles",
    "verify",
    "write",
]
