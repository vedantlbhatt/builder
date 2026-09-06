"""Repository identity and commit statistics, from git, the way the engine does it.

`--git-common-dir`, never `--show-toplevel`: six of thirteen project directories on the
reference machine are worktrees of one repository, and `--show-toplevel` fragments it into
seven project arcs. The display name comes from the normalized origin, never the folder
(`RideGT` on disk is `gt-transit` on the remote) — and capture never uploads it anyway:
`repo_name` and `title` are public-repo fields, and which repositories are public is a
setting on the Mac that a container cannot see. Everything here therefore goes up in
anonymous mode: `repo_hash` only, so the phone can fold cloud sessions into the right arc.

The same limit applies to `excluded`. An excluded repository produces ZERO uploads on the
Mac; capture cannot read that list, so `BUILDER_CAPTURE_EXCLUDE` (comma-separated
normalized origins, e.g. `github.com/acme/secret`) is the only exclusion it honours, and
`docs/cloud-capture.md` says so.
"""

from __future__ import annotations

import dataclasses
import functools
import os
import pathlib
import subprocess

from . import identity
from .tuning import GIT_EXCLUDE_PATHSPECS, REPO_HASH_PREFIX, REPO_PEPPER


def normalize_origin(raw: str | None) -> str | None:
    """`OriginNormalizer.normalize`: every spelling of one remote to one string.

        https://github.com/VedantLBhatt/gt-transit.git
        git@github.com:vedantlbhatt/gt-transit.git
        ssh://git@github.com/vedantlbhatt/gt-transit
        https://token@github.com:443/vedantlbhatt/gt-transit.git/

    all become `github.com/vedantlbhatt/gt-transit`. Credentials are stripped before
    anything is hashed: a token in a remote URL is not identity.
    """
    if raw is None:
        return None
    s = raw.strip()
    if not s:
        return None
    if "://" not in s and "@" in s and ":" in s and s.index("@") < s.index(":"):
        at, colon = s.index("@"), s.index(":")
        s = f"{s[at + 1 : colon]}/{s[colon + 1 :]}"
    else:
        if "://" in s:
            s = s.split("://", 1)[1]
        if "@" in s:
            s = s.split("@", 1)[1]
    if ":" in s and "/" in s:
        colon, slash = s.index(":"), s.index("/")
        if colon < slash and s[colon + 1 : slash].isdigit():
            s = s[:colon] + s[slash:]
    s = s.rstrip("/")
    s = s.removesuffix(".git")
    s = s.rstrip("/")
    if "/" not in s:
        return s.lower()
    host, path = s.split("/", 1)
    host = host.lower()
    if host in ("github.com", "gitlab.com", "bitbucket.org"):
        path = path.lower()
    return host if not path else f"{host}/{path}"


def display_name(normalized: str | None) -> str | None:
    if not normalized:
        return None
    return normalized.rsplit("/", 1)[-1]


@dataclasses.dataclass(frozen=True)
class RepoIdentity:
    identity: str
    basis: str  # "origin" | "root_commit"
    common_root: str | None

    @property
    def hash(self) -> str:
        return identity.repo_hash(self.identity, REPO_PEPPER, REPO_HASH_PREFIX)

    @property
    def display_name(self) -> str | None:
        if self.basis == "origin":
            return display_name(self.identity)
        return pathlib.Path(self.common_root).name if self.common_root else None


def _git(args: list[str], cwd: str) -> str | None:
    try:
        r = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
            env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    return r.stdout.strip()


@functools.lru_cache(maxsize=256)
def identity_for(cwd: str | None) -> RepoIdentity | None:
    """`RepoResolver.identity(forWorkingDirectory:)`. Cached per cwd: it varies within one
    transcript (MEASURED: 5 distinct cwds in a 30-minute session) but the set is small."""
    if not cwd or not os.path.isdir(cwd):
        return None
    if _git(["rev-parse", "--is-inside-work-tree"], cwd) != "true":
        return None
    common = _git(["rev-parse", "--path-format=absolute", "--git-common-dir"], cwd)
    common_root = str(pathlib.Path(common).parent) if common else None
    origin = _git(["config", "--get", "remote.origin.url"], cwd)
    norm = normalize_origin(origin)
    if norm:
        return RepoIdentity(identity=norm, basis="origin", common_root=common_root)
    root = _git(["rev-list", "--max-parents=0", "HEAD"], cwd)
    if root:
        first = root.splitlines()[0].strip()
        if first:
            return RepoIdentity(
                identity=f"localroot:{first}", basis="root_commit", common_root=common_root
            )
    return None


@dataclasses.dataclass(frozen=True)
class WindowStats:
    commits: int = 0
    insertions: int = 0
    deletions: int = 0
    files_changed: int = 0


def commits_in(common_root: str | None, since: float, until: float) -> list[tuple[str, float]]:
    """(sha, unix time) for every commit in the window, vendored files excluded.

    `window_stats` answers "how much landed here", which is a per-session question and
    the one the payload asks. This answers "WHICH commits", which is the only way to add
    two overlapping sessions up without counting the same commit twice: two agents running
    at once in one repository both see every commit in the overlap, and the sum of two
    correct per-session numbers is then wrong (analysis/profile.py, COMMITS_OVERLAPPING).
    """
    if not common_root or not os.path.isdir(common_root):
        return []
    out = _git(
        [
            "log",
            f"--since=@{since:.0f}",
            f"--until=@{until:.0f}",
            "--pretty=format:%H %ct",
            "--no-merges",
            "--",
            *GIT_EXCLUDE_PATHSPECS,
        ],
        common_root,
    )
    rows = []
    for line in (out or "").splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].isdigit():
            rows.append((parts[0], float(parts[1])))
    return rows


def window_stats(common_root: str | None, since: float, until: float) -> WindowStats:
    """`RepoResolver.stats`: commits and line deltas inside a window, vendored files
    excluded. Binary files report `-\t-`; the file is counted, the lines are not."""
    if not common_root or not os.path.isdir(common_root):
        return WindowStats()
    out = _git(
        [
            "log",
            f"--since=@{since:.0f}",
            f"--until=@{until:.0f}",
            "--pretty=format:%H",
            "--numstat",
            "--no-merges",
            "--",
            *GIT_EXCLUDE_PATHSPECS,
        ],
        common_root,
    )
    if not out:
        return WindowStats()
    commits = insertions = deletions = 0
    files: set[str] = set()
    for line in out.splitlines():
        if not line:
            continue
        if "\t" not in line:
            commits += 1
            continue
        parts = line.split("\t", 2)
        if len(parts) != 3:
            continue
        if parts[0].isdigit():
            insertions += int(parts[0])
        if parts[1].isdigit():
            deletions += int(parts[1])
        files.add(parts[2])
    return WindowStats(commits, insertions, deletions, len(files))


def excluded_origins() -> set[str]:
    raw = os.environ.get("BUILDER_CAPTURE_EXCLUDE", "")
    return {normalize_origin(x) for x in raw.split(",") if x.strip()} - {None}
