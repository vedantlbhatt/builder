#!/usr/bin/env python3
"""Mint an authenticated device for end-to-end runs, the way the server's own tests do.

The server has no back door for this and none is added here. The script walks the real
RFC 8628 device flow (`/v1/auth/device/start`, `/v1/auth/device/poll`) and stands in for
the phone tap by approving the grant as the database OWNER — exactly what
`server/tests/test_sync.py::_pair` does with its `UPDATE device_grants` — so the tokens it
prints were issued by the running API under its own signing key and rotate like any other.

    DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost/builder_e2e \
    python3 scripts/e2e_mint_device.py --server http://127.0.0.1:8787 --handle vedant \
        --label "Claude Code (container)" [--credentials ~/.builder/credentials.json]

`--credentials` writes the file `python -m capture` reads (same keys as
`capture.client.Client.complete_pairing`), so the capture CLI is paired without a phone.
Prints a JSON object with the user id, machine id, access and refresh token.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import sys
import urllib.request
import uuid

from sqlalchemy import create_engine, text


def _post(server: str, path: str, body: dict) -> dict:
    req = urllib.request.Request(
        server.rstrip("/") + path,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default=os.environ.get("BUILDER_API_URL", "http://127.0.0.1:8787"))
    ap.add_argument("--handle", default="vedant")
    ap.add_argument("--display-name", default=None)
    ap.add_argument("--tz", default="America/New_York")
    ap.add_argument("--label", default="e2e device")
    ap.add_argument("--platform", default="macos", choices=["macos", "ios", "linux"])
    ap.add_argument("--machine", help="raw machine identifier; hashed like capture does")
    ap.add_argument("--credentials", help="also write capture's credentials.json here")
    a = ap.parse_args()

    owner_url = os.environ.get("DATABASE_URL")
    if not owner_url:
        print("set DATABASE_URL to the OWNER connection (postgres role)", file=sys.stderr)
        return 2
    eng = create_engine(owner_url, future=True)

    # One user per handle; re-running the script re-uses it (a second device, not a
    # second person).
    with eng.begin() as c:
        row = c.execute(
            text("SELECT id FROM users WHERE handle = :h"), {"h": a.handle}
        ).first()
        if row is None:
            uid = str(
                c.execute(
                    text(
                        "INSERT INTO users (handle, display_name, tz, profile_public) "
                        "VALUES (:h, :d, :tz, true) RETURNING id"
                    ),
                    {"h": a.handle, "d": a.display_name or a.handle, "tz": a.tz},
                ).scalar()
            )
        else:
            uid = str(row.id)

    raw = a.machine or f"e2e-{a.label}"
    machine_id = hashlib.sha256(f"builder-machine-v1|{raw}".encode()).hexdigest()
    started = _post(
        a.server,
        "/v1/auth/device/start",
        {"machine_id": machine_id, "label": a.label, "platform": a.platform, "agent_version": "e2e"},
    )
    with eng.begin() as c:
        n = c.execute(
            text(
                "UPDATE device_grants SET user_id = :u, approved_at = now() "
                "WHERE user_code = :uc AND approved_at IS NULL"
            ),
            {"u": uid, "uc": started["user_code"]},
        ).rowcount
    if n != 1:
        print(f"grant {started['user_code']} not found to approve", file=sys.stderr)
        return 1
    polled = _post(a.server, "/v1/auth/device/poll", {"device_code": started["device_code"]})
    if polled.get("status") != "ok":
        print(f"poll did not return tokens: {polled}", file=sys.stderr)
        return 1

    out = {
        "user_id": uid,
        "handle": a.handle,
        "machine_id": machine_id,
        "label": a.label,
        "access_token": polled["access_token"],
        "refresh_token": polled["refresh_token"],
    }
    if a.credentials:
        p = pathlib.Path(a.credentials).expanduser()
        p.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(p.parent, 0o700)
        except OSError:
            pass
        creds = {
            "server": a.server.rstrip("/"),
            "machine_id": machine_id,
            "label": a.label,
            "access_token": polled["access_token"],
            "refresh_token": polled["refresh_token"],
            "paired_at": __import__("time").time(),
        }
        p.write_text(json.dumps(creds, indent=1, sort_keys=True) + "\n")
        os.chmod(p, 0o600)
        out["credentials"] = str(p)
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
