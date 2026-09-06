"""Every other tool's transcripts, discovered and turned into the same records.

`capture/sessions.py` cuts sittings out of a list of thin records: `{ts, kind, presence,
cwd, sid, ...}`. That list is the only thing the v3 boundary rules read, so a harness is
supported the moment somebody can produce it. `analysis/` already reads all six stores
into `digest.Ev` events; this module is the adapter between the two, plus the discovery
that says where each store lives.

WHAT IS NOT REBUILT HERE

Nothing about how a session is cut. The threshold fit, lineage folding, the two clocks,
the 04:00 boundary and the three structural ends are the same code for every harness, so
a Codex sitting and a Claude Code sitting cannot disagree about what a session is.

THE RULE EVERY HARNESS NEEDS

A subagent or child session is NOT a root. Claude Code's version of this cost a ~3x token
overcount (CLAUDE.md, "Globbing"). It generalises: a Gemini subagent lives one level
deeper under `chats/<parent>/`, and an opencode child carries `parent_id`. Both are
excluded here, on the same allowlist-on-shape principle, and for the same reason: the
parent's own tool result already reports that work.

TOKENS ARE ABSENT, NOT ZERO

Only Claude Code's records carry the per-message usage objects `token_ledger` dedupes on.
The other loaders report tokens their own way and at their own granularity, and mapping a
per-turn or per-step figure into a per-message ledger would produce a number that looks
authoritative and is not comparable with the one beside it. So sessions from these
harnesses upload with `tokens_reported: false`, which is the same thing the Cursor rule
already says: absent, not zero. Hours, prompts, tool calls, lines and commits are all
real; only the token buckets are withheld.
"""

from __future__ import annotations

import dataclasses
import pathlib

from analysis import digest

from . import identity
from .discover import Transcript

#: Where each tool keeps its transcripts, as `docs/integrations.md` records. Every path is
#: a DEFAULT: `discover` takes overrides, and a root that does not exist is skipped in
#: silence, because "you do not use Codex" is not an error.
DEFAULT_ROOTS: dict[str, tuple[str, ...]] = {
    "codex": ("~/.codex/sessions",),
    "gemini_cli": ("~/.gemini/tmp",),
    "cline": (
        "~/Library/Application Support/Code/User/globalStorage/saoudrizwan.claude-dev/tasks",
        "~/.config/Code/User/globalStorage/saoudrizwan.claude-dev/tasks",
        "~/.cline/data",
    ),
    "opencode": ("~/.local/share/opencode",),
    "aider": ("~/src", "~/code", "~/projects", "~/work"),
}

#: `analysis` names two harnesses differently from the upload contract. The contract's
#: spelling is the wire, so it is what this module speaks; the map is here and nowhere
#: else so the translation cannot happen twice with different answers.
_CONTRACT_NAME = {"gemini": "gemini_cli"}
_ANALYSIS_NAME = {v: k for k, v in _CONTRACT_NAME.items()}


@dataclasses.dataclass(frozen=True)
class Store:
    """One session's worth of one harness's store, ready to load."""

    harness: str  # the CONTRACT spelling
    pool_dir: str  # the lineage key: what makes two sittings the same person's thread
    path: pathlib.Path  # what `digest.load_events` reads; may be virtual (`<db>/<id>`)
    cwd: str | None  # for repo attribution, when the store records one
    #: The harness's OWN id for this session. Two containers can hold the same session,
    #: and this is what says so.
    session_id: str | None = None


def contract_name(analysis_harness: str) -> str:
    return _CONTRACT_NAME.get(analysis_harness, analysis_harness)


def analysis_name(contract_harness: str) -> str:
    return _ANALYSIS_NAME.get(contract_harness, contract_harness)


# ------------------------------------------------------------------------- discovery

#: When the same session id turns up in two containers, the one to READ. opencode keeps a
#: SQLite database AND, on any machine upgraded from an older release, the pre-SQLite
#: `storage/session/**.json` tree it was migrated from; an `opencode export` file may be
#: sitting in the same directory. A machine with all three would upload one sitting THREE
#: TIMES, as three sessions, tripling that person's hours. Highest number wins.
_CONTAINER_RANK = {"sqlite": 3, "json_dir": 2, "export_json": 1}

#: The same rule for Gemini: a session saved by an older release is a whole-conversation
#: `.json` beside the `.jsonl` the current one appends to.
_SUFFIX_RANK = {".jsonl": 2, ".json": 1}


def discover(roots: dict[str, list[str]] | None = None) -> list[Store]:
    """Every non-Claude-Code session on this machine, with its lineage and its cwd.

    Discovery itself is `analysis.probe._walk`, which already knows every store's shape,
    including the two that are one file holding many sessions (an Aider chat history, an
    opencode database) and report a virtual `<file>/<session id>` path each. Using it here
    means `python -m analysis probe` and `python -m capture sync` can never disagree about
    what exists.

    Deduplicated by the harness's own session id, because more than one container can hold
    the same sitting (see `_CONTAINER_RANK`).
    """
    from analysis import probe

    best: dict[tuple[str, str], tuple[int, Store]] = {}
    loose: list[Store] = []
    for harness, defaults in DEFAULT_ROOTS.items():
        wanted = roots.get(harness) if roots else None
        for raw in wanted if wanted is not None else defaults:
            root = pathlib.Path(raw).expanduser()
            if not root.exists():
                continue
            for path in probe._walk(root):
                found = _store_for(path, harness)
                if found is None:
                    continue
                store, rank = found
                if store.session_id is None:
                    loose.append(store)
                    continue
                key = (store.harness, store.session_id)
                if key not in best or rank > best[key][0]:
                    best[key] = (rank, store)
    out = [s for _, s in best.values()] + loose
    out.sort(key=lambda s: (s.harness, s.pool_dir, str(s.path)))
    return out


def _store_for(path: pathlib.Path, expected: str) -> tuple[Store, int] | None:
    """One walked path as a `(Store, container rank)`, or None when it is not this
    harness's, is a child session, or cannot be read at all."""
    try:
        found = contract_name(digest.detect_harness(path))
    except OSError:
        return None
    if found != expected:
        return None
    try:
        return _describe(found, path)
    except Exception:
        # A half-written database, a truncated rollout, a task directory being deleted
        # under us. One unreadable session must never stop the other forty from syncing.
        return None


def _describe(harness: str, path: pathlib.Path) -> tuple[Store, int] | None:
    if harness == "codex":
        # Resumes append to the same rollout file, exactly as Claude Code does, so the
        # FILE is the lineage and the sessionizer decides where the sittings inside it end.
        from analysis import codex

        m = codex.meta(path)
        cwd = m.get("cwd")
        return (
            Store(harness, path.stem, path, _text(cwd), _text(m.get("session_id")) or path.stem),
            1,
        )

    if harness == "gemini_cli":
        from analysis import gemini

        m = gemini.meta(path)
        # A subagent recording is NOT a root: its work is already inside the parent's tool
        # result, and counting it is the same overcount the `**/*.jsonl` glob produced for
        # Claude Code. Decided by the recording's own `kind`, not by its path, so it holds
        # for an exported file as well as for `chats/<parent>/`.
        if m.get("kind") == "subagent":
            return None
        # The project is a HASH of the path, so there is no cwd to attribute a repo from,
        # and none is invented. It is still the lineage: two sittings on one project are
        # one thread.
        pool = _text(m.get("project_hash")) or path.parent.name
        return Store(harness, pool, path, None, _text(m.get("session_id"))), _SUFFIX_RANK.get(
            path.suffix, 0
        )

    if harness == "cline":
        from analysis import cline

        m = cline.meta(path)
        return Store(harness, path.name, path, _text(m.get("cwd")), _text(m.get("task_id"))), 1

    if harness == "opencode":
        from analysis import opencode

        m = opencode.meta(path)
        if m.get("is_child") or m.get("parent_id"):
            return None  # a subagent child; the parent already reports its work
        directory = _text(m.get("directory")) or _text(m.get("cwd"))
        # One container holds every session, so the lineage is the DIRECTORY the session
        # ran in, not the file: two sittings in one repo are one thread, two repos are two.
        pool = directory or str(path.parent)
        rank = _CONTAINER_RANK.get(str(m.get("container")), 0)
        return Store(harness, pool, path, directory, _text(m.get("session_id"))), rank

    if harness == "aider":
        # `<repo>/.aider.chat.history.md/<session id>`: one file, many sessions, and the
        # repo directory is both the lineage and the cwd.
        repo_dir = path.parent.parent
        return Store(harness, str(repo_dir), path, str(repo_dir), f"{repo_dir}:{path.name}"), 1

    return None


def _text(v: object) -> str | None:
    return v if isinstance(v, str) and v else None


# ------------------------------------------------------------------------- adapting

#: `digest.Ev.kind` → the record kind `measure_boundaries.classify` would have emitted,
#: and whether it is a PRESENCE signal. Presence is the only field the boundary rules
#: read; the kind decides the strip's colour and the compaction mark.
_KIND: dict[str, tuple[str, bool]] = {
    "prompt": ("prompt", True),
    "interrupt": ("interrupt", True),
    "human_edit": ("human_edit", True),
    "assistant": ("assistant", False),
    "tool": ("tool_use", False),
    "result_error": ("tool_result", False),
    "compaction": ("system", False),
}


def load(store: Store) -> "object":
    """One `Store` as a `sessions.Source`, with records the boundary rules can cut.

    Imported lazily to keep `sessions` free of a dependency on this module: the Claude
    Code path must not grow a reason to import five loaders it never uses.
    """
    from . import sessions as ss

    events = digest.load_events(store.path)
    transcript = Transcript(store.pool_dir, store.path, harness=store.harness)
    src_id = identity.source_id(transcript.descriptor)
    records = records_for(events, store, src_id)
    return ss.Source(transcript, src_id, records, events)


def records_for(events: list[digest.Ev], store: Store, source_id: str) -> list[dict]:
    """Digest events as thin records.

    `sid` is the whole store's own session id, identical on every record, which is exactly
    what lineage folding wants: these harnesses do not interleave two conversations in one
    file the way Claude Code's cwd stamping does.

    `usage` and `msg_id` are None on purpose, so `token_ledger` reports absent rather than
    a partial ledger dressed up as a full one (module docstring).
    """
    sid = store.path.name
    out: list[dict] = []
    for i, e in enumerate(events):
        kind, presence = _KIND.get(e.kind, ("unknown", False))
        out.append(
            {
                "ts": e.ts,
                "kind": kind,
                "presence": presence,
                "cwd": store.cwd,
                "sid": sid,
                "line": i,
                "uuid": f"{source_id}#{i}",
                "session_id": sid,
                "msg_id": None,
                "usage": None,
                "model": e.model,
                # What `strip.build` reads to place a compaction mark. The record kind is
                # `system` for the same reason it is in a Claude Code transcript.
                "subtype": "compact_boundary" if e.kind == "compaction" else None,
                "sidechain": False,
                "source_id": source_id,
                "path": str(store.path),
            }
        )
    out.sort(key=lambda r: r["ts"])
    return out


__all__ = ["DEFAULT_ROOTS", "Store", "analysis_name", "contract_name", "discover", "load",
           "records_for"]
