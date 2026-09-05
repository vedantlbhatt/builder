"""Find root transcripts under `~/.claude/projects`.

A root transcript is EXACTLY `<projectdir>/<uuid>.jsonl` — one path component below a
project directory. This is an ALLOWLIST on shape, deliberately, not a denylist on
`subagents/`: the tree holds `<uuid>/subagents/`, `<uuid>/workflows/` and
`<uuid>/tool-results/` as siblings, so a denylist naming only `subagents` waves the other
two through as roots. Subagent sidecars carry tokens the parent's `Agent` tool result
already reports in aggregate, and their message ids genuinely differ, so counting one as a
root is the ~3x overcount that a `**/*.jsonl` glob produces (CLAUDE.md, "Globbing").

Mirrors `ClaudeCodeParser.isRootTranscript(relativePath:)`, including the test it names:
a file at `<projectdir>/<uuid>/futuredir/x.jsonl` must NOT be a root.
"""

from __future__ import annotations

import dataclasses
import pathlib


@dataclasses.dataclass(frozen=True)
class Transcript:
    #: The project directory NAME (`-home-user-builder`), never decoded back to a path —
    #: the encoding is lossy (`/a/b-c`, `/a/b/c` and `/a/b.c` all become `-a-b-c`).
    project_dir: str
    path: pathlib.Path

    @property
    def descriptor(self) -> str:
        """The engine's canonical source descriptor: `<projectdir>/<file>.jsonl`."""
        return f"{self.project_dir}/{self.path.name}"


def is_root_transcript(relative_path: str) -> bool:
    """`relative_path` is relative to the PROJECT directory. Roots have no `/` in them."""
    return relative_path.endswith(".jsonl") and "/" not in relative_path


def iter_root_transcripts(root: pathlib.Path) -> list[Transcript]:
    """Every root transcript under `root`, sorted, as `(project_dir, path)`.

    A project directory can legitimately contain ZERO transcripts and still exist —
    entering a worktree mid-session writes only sidecar directories under the
    worktree-encoded name (observed on the reference machine) — so an empty directory is
    simply skipped, not reported.
    """
    root = pathlib.Path(root).expanduser()
    out: list[Transcript] = []
    if not root.is_dir():
        return out
    for project in sorted(p for p in root.iterdir() if p.is_dir()):
        for entry in sorted(project.iterdir()):
            if entry.is_file() and is_root_transcript(entry.name):
                out.append(Transcript(project_dir=project.name, path=entry))
    return out
