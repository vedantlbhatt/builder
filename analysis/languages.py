"""What you actually build in, from the files the agent wrote.

Every code-stats product in existence has this and this one did not. It is also the single
easiest place to print a confident wrong number, and three of the four traps below were
found by running it on this repository rather than by thinking about it.

THE UNIT IS LINES THE AGENT ADDED, not files and not time. Files overweight a one-line
config change against a 400 line module; time cannot be attributed to a file at all,
because a sitting that touches six languages has one clock. Lines are already the unit
every other number here uses, and they come from the same place: Edit's `structuredPatch`,
Write's created content, and a shell heredoc's body.

WHAT IS REFUSED, AND WHY EACH REFUSAL EXISTS:

  * GENERATED FILES. `bun.lock` is 3,000 lines nobody wrote and `uv.lock` another 2,000.
    MEASURED on this repository: counting them makes the top language a lockfile format on
    any day somebody ran an install. They are excluded by NAME, not by extension, because
    the extension is shared with files people do write.
  * FILES THIS REPOSITORY GENERATES. `make gen` writes Swift, TypeScript and Python from
    three specs. Those lines are real and they are not language choices, so a file whose
    path is under a `Generated/` directory or whose name ends `_spec.py` is excluded the
    same way.
  * AN EXTENSION NOBODY MAPPED. Bucketed to `other` and counted, never guessed at. A
    guess here reads exactly like a measurement.
  * A CORPUS WITH ALMOST NO ATTRIBUTABLE LINES. Below `MIN_LINES` there is no split worth
    printing, and the refusal says how many lines it did see.

The language NAME is all that travels. A name is not a path and not a file name: `Swift`
is the same string whatever anybody's directories are called.
"""

from __future__ import annotations

import posixpath
from collections.abc import Sequence

#: Below this there is no split worth printing. A pie chart over 40 lines is a picture of
#: one commit. UNMEASURED JUDGEMENT CALL, deliberately blunt.
MIN_LINES = 200

#: How many languages a person reads on a card before it stops being a fact and starts
#: being a table. The rest sum into `other`.
TOP_N = 8

#: Extension to language. Not exhaustive and not trying to be: an extension nobody mapped
#: is counted as `other` rather than guessed at, and adding one is a line here.
EXTENSIONS: dict[str, str] = {
    "py": "Python",
    "swift": "Swift",
    "ts": "TypeScript",
    "tsx": "TypeScript",
    "js": "JavaScript",
    "jsx": "JavaScript",
    "mjs": "JavaScript",
    "cjs": "JavaScript",
    "rb": "Ruby",
    "go": "Go",
    "rs": "Rust",
    "java": "Java",
    "kt": "Kotlin",
    "kts": "Kotlin",
    "m": "Objective-C",
    "mm": "Objective-C",
    "h": "C header",
    "c": "C",
    "cc": "C++",
    "cpp": "C++",
    "hpp": "C++",
    "cs": "C#",
    "php": "PHP",
    "scala": "Scala",
    "ex": "Elixir",
    "exs": "Elixir",
    "erl": "Erlang",
    "hs": "Haskell",
    "lua": "Lua",
    "dart": "Dart",
    "r": "R",
    "jl": "Julia",
    "zig": "Zig",
    "sh": "Shell",
    "bash": "Shell",
    "zsh": "Shell",
    "fish": "Shell",
    "ps1": "PowerShell",
    "sql": "SQL",
    "html": "HTML",
    "css": "CSS",
    "scss": "CSS",
    "sass": "CSS",
    "less": "CSS",
    "vue": "Vue",
    "svelte": "Svelte",
    "json": "JSON",
    "yaml": "YAML",
    "yml": "YAML",
    "toml": "TOML",
    "ini": "INI",
    "xml": "XML",
    "md": "Markdown",
    "mdx": "Markdown",
    "rst": "reStructuredText",
    "txt": "Text",
    "proto": "Protobuf",
    "graphql": "GraphQL",
    "gql": "GraphQL",
    "tf": "Terraform",
    "dockerfile": "Dockerfile",
    "gradle": "Gradle",
    "ipynb": "Notebook",
}

#: Files with no extension whose NAME is the language. Checked before the extension rule.
BY_NAME: dict[str, str] = {
    "Dockerfile": "Dockerfile",
    "Makefile": "Make",
    "Rakefile": "Ruby",
    "Gemfile": "Ruby",
    "Procfile": "Text",
    "CMakeLists.txt": "CMake",
}

#: NOBODY WROTE THESE LINES. Excluded by exact file name, because the extensions they use
#: (`.lock`, `.json`, `.sum`) are shared with files people do write by hand. MEASURED on
#: this repository: `bun.lock` alone is over 3,000 lines, enough to make a lockfile format
#: the top language on any day somebody ran an install.
GENERATED_NAMES = frozenset(
    {
        "bun.lock",
        "bun.lockb",
        "package-lock.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "uv.lock",
        "poetry.lock",
        "Pipfile.lock",
        "Cargo.lock",
        "composer.lock",
        "Gemfile.lock",
        "go.sum",
        "Package.resolved",
    }
)

#: Path fragments that mean a machine wrote the file. `Generated/` is this repository's own
#: convention (`make gen` emits Swift, TypeScript and Python from three specs) and the rest
#: are directories nobody edits by hand.
GENERATED_DIRS = ("/Generated/", "/generated/", "/node_modules/", "/.venv/", "/vendor/")

#: And this repository's generated Python, which lives beside hand-written Python.
GENERATED_SUFFIXES = ("_spec.py", "_pb2.py", ".g.dart", ".freezed.dart")


def language_of(path: str) -> str | None:
    """The language a path is written in, or None when the file is generated.

    None means EXCLUDED, not unknown: an extension nobody mapped comes back as `other`,
    which is counted. The difference matters because one of them should change the
    denominator and the other should not.
    """
    if not path:
        return None
    norm = path.replace("\\", "/")
    name = posixpath.basename(norm)
    if name in GENERATED_NAMES:
        return None
    if any(d in f"/{norm.strip('/')}/" for d in GENERATED_DIRS):
        return None
    if any(name.endswith(s) for s in GENERATED_SUFFIXES):
        return None
    if name in BY_NAME:
        return BY_NAME[name]
    ext = name.rsplit(".", 1)[-1].lower() if "." in name[1:] else ""
    return EXTENSIONS.get(ext, "other")


def split(sessions: Sequence) -> dict:
    """Lines the agent added per language over `sessions`, or a refusal with its count.

    `sessions` are `patterns.SessionEvents`. Only events that carry BOTH a path and a line
    count are read, which is the same set `lines_added_agent` sums, so this can never
    disagree with the totals beside it about how much was written.
    """
    from . import patterns as pat

    by_lang: dict[str, int] = {}
    files: dict[str, set[str]] = {}
    excluded = 0
    for s in sessions:
        for e in s.events:
            if not pat._wrote(e) or not e.path or not e.added:
                continue
            lang = language_of(e.path)
            if lang is None:
                excluded += e.added
                continue
            by_lang[lang] = by_lang.get(lang, 0) + e.added
            files.setdefault(lang, set()).add(e.path)

    total = sum(by_lang.values())
    if total < MIN_LINES:
        return {
            "lines": total,
            "generated_lines_excluded": excluded,
            "languages": None,
            "reason": f"{total} attributable line(s), {MIN_LINES} needed",
        }

    ranked = sorted(by_lang.items(), key=lambda kv: (-kv[1], kv[0]))
    head, tail = ranked[:TOP_N], ranked[TOP_N:]
    out = [
        {"name": n, "lines": v, "files": len(files[n]), "share": round(v / total, 3)}
        for n, v in head
    ]
    if tail:
        rest = sum(v for _, v in tail)
        out.append(
            {
                "name": "other",
                "lines": rest,
                "files": sum(len(files[n]) for n, _ in tail),
                "share": round(rest / total, 3),
            }
        )
    return {
        "lines": total,
        # Reported rather than hidden. A person who ran an install and sees their line
        # total drop by three thousand deserves to know where it went, and a silent
        # exclusion is indistinguishable from a parser that missed the file.
        "generated_lines_excluded": excluded,
        "languages": out,
        "reason": None,
    }


__all__ = [
    "BY_NAME",
    "EXTENSIONS",
    "GENERATED_DIRS",
    "GENERATED_NAMES",
    "GENERATED_SUFFIXES",
    "MIN_LINES",
    "TOP_N",
    "language_of",
    "split",
]
