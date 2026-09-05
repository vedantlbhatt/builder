"""The identities capture derives, and exactly how they relate to the engine's.

All of these mirror `Hashing.swift`. The chain is:

    source_id        = sha256("builder-source-v1|claude_code|<projectdir>/<file>.jsonl")
    event_uid        = sha256("claude_code|<source_id>|<record uuid>")
    client_session_id = sha256("builder-session-v1|claude_code|<machine slot>|<first event_uid>")

The first two are reproducible from the transcript alone and match the engine byte for
byte: the engine's source descriptor is the path RELATIVE to `~/.claude/projects`
(`"\\(dir)/\\(rel)"` in `ClaudeCodeParser.discover`), not the absolute path, so moving the
tree does not orphan anything.

The third is where the two clients part. The engine fills the machine slot with the
Mac's hashed hardware UUID — deliberately, so "a future cross-machine merge has
distinguishable inputs to union rather than colliding ids". A Python client in a cloud
container cannot know that UUID, so a session synced from the container and the same
session synced later from a Mac CANNOT share an id under the engine's own rule; that is a
property of the rule, not a gap in this port. Capture therefore fills the slot with the
literal ``capture``: the id is a pure function of the transcript (source + first record),
so a resumed remote session that lands in a fresh container under the same project
directory and file name keeps its identity, and every re-run of a hook upserts rather than
duplicates. A per-container machine id here would give the same sitting a new identity
each time the container was rebuilt, which is the failure this choice exists to avoid.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import pathlib
import uuid

HARNESS = "claude_code"
CAPTURE_MACHINE_SLOT = "capture"


def sha256_hex(s: str | bytes) -> str:
    if isinstance(s, str):
        s = s.encode("utf-8")
    return hashlib.sha256(s).hexdigest()


def source_id(descriptor: str) -> str:
    """`Hashing.sourceID` — descriptor is `<projectdir>/<file>.jsonl`, relative to the root."""
    return sha256_hex(f"builder-source-v1|{HARNESS}|{descriptor}")


def event_uid(src_id: str, native_event_id: str) -> str:
    """`Hashing.eventUID` — no ordinal, on purpose (a parser bump must not shift uids)."""
    return sha256_hex(f"{HARNESS}|{src_id}|{native_event_id}")


def client_session_id(first_event_uid: str) -> str:
    """`Hashing.clientSessionID` with the machine slot fixed to ``capture`` (see module doc)."""
    return sha256_hex(f"builder-session-v1|{HARNESS}|{CAPTURE_MACHINE_SLOT}|{first_event_uid}")


def machine_id(raw: str) -> str:
    """`Hashing.machineID` — the raw identifier is hashed, never sent."""
    return sha256_hex(f"builder-machine-v1|{raw}")


def raw_machine_identifier() -> str:
    """What stands in for the Mac's platform UUID.

    In order: `BUILDER_MACHINE_ID` (set it in a cloud environment so every container is
    the same device on the server — `devices` is unique on `(user, machine_id)` and
    re-pairing an existing machine id un-revokes the row instead of adding one),
    `/etc/machine-id` where it exists, else a random UUID that the credentials file then
    pins for as long as that file lives.
    """
    env = os.environ.get("BUILDER_MACHINE_ID", "").strip()
    if env:
        return env
    try:
        mid = pathlib.Path("/etc/machine-id").read_text().strip()
        if mid:
            return mid
    except OSError:
        pass
    return uuid.uuid4().hex


def repo_hash(identity: str, pepper: bytes, prefix: str) -> str:
    """`RepoHasher.hash`: HMAC-SHA256 under the GLOBAL, non-secret pepper. Full 64 hex."""
    return hmac.new(pepper, (prefix + identity).encode("utf-8"), hashlib.sha256).hexdigest()
