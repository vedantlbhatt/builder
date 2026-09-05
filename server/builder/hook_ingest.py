"""Cut a hook-delivered transcript into sessions with the uploader's own pipeline.

The hook channel (docs/hooks-capture.md) is the zero-install path: Claude Code POSTs the
transcript itself. Everything after that is `python -m capture` running on the server
instead of on the machine — the same record loader, the same v3 sessionizer, the same
payload builder — so a session that arrives by hook and the same session synced later by
capture or by the Mac produce ONE row: `client_session_id` is derived from the first
event with the machine slot fixed to ``capture`` (capture/identity.py), whatever machine
did the cutting.

`capture/`, `analysis/` and `scripts/measure_boundaries.py` live at the repository root.
The repo-root Dockerfile copies them next to `server/`; in a checkout the root is two
levels up. Imported lazily so the server still boots — and `/health` still answers —
without them; only this channel is then unavailable, and it says so.
"""

from __future__ import annotations

import datetime as dt
import pathlib
import sys
import tempfile
import time

_ROOT = pathlib.Path(__file__).resolve().parents[2]

#: `client_version` on hook-fed payloads. The engine's stamps are semver; this names the
#: channel so a row's origin is visible in the database.
HOOK_CLIENT_VERSION = "hook/1"


class CaptureUnavailable(RuntimeError):
    """The sessionizer is not importable on this server (server-only image)."""


def _capture():
    try:
        import capture.sessions  # noqa: F401
    except ImportError:
        root = str(_ROOT)
        if root not in sys.path:
            sys.path.insert(0, root)
    try:
        from capture import identity, sessions
        from capture.discover import Transcript
    except ImportError as e:  # pragma: no cover - deployment shape, not logic
        raise CaptureUnavailable(str(e)) from e
    return identity, sessions, Transcript


def payloads_for(
    raw: bytes,
    *,
    native_session_id: str,
    project_dir: str,
    tz_offset_minutes: int,
    finalize: bool,
    device_id: str,
    now: float | None = None,
) -> list[dict]:
    """Contract payloads for every visible session in one transcript's bytes.

    `finalize` is true for `SessionEnd`: Claude Code is exiting, so an open session is
    closed now. For every other hook the idle rule decides — a `Stop` is the end of a
    turn, not of a sitting, and the person may type again. A transcript whose last record
    is already older than the idle threshold comes back final either way, which is how a
    session whose process was killed without a `SessionEnd` still finishes: the next hook
    from any session re-cuts the stale ones (routes/ingest.py).
    """
    identity, sessions, Transcript = _capture()
    now = time.time() if now is None else now
    tz = dt.timezone(dt.timedelta(minutes=tz_offset_minutes))
    with tempfile.TemporaryDirectory(prefix="builder-hook-") as tmp:
        pdir = pathlib.Path(tmp) / project_dir
        pdir.mkdir()
        path = pdir / f"{native_session_id}.jsonl"
        path.write_bytes(raw)
        src = sessions.load_source(Transcript(project_dir=project_dir, path=path))
        cut = sessions.sessionize_sources([src], tz, now=now, finalize_open=finalize)
        # The device is the capture key's own row; the payload's machine id only has to
        # be a stable sha256 per device (contract), never the server's identity.
        machine = identity.machine_id(f"builder-hook-device|{device_id}")
        out: list[dict] = []
        for s in cut:
            p = sessions.build_payload(s, tz, machine, HOOK_CLIENT_VERSION, now)
            if p["visible"]:
                out.append(p)
        return out
