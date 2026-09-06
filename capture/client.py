"""The server, over urllib. The same endpoints, bodies and headers as `SyncClient.swift`.

Deliberately no certificate pinning, for the same reason as the Mac: the privacy page
tells people to run this behind mitmproxy and watch what it sends.

Credentials live in `~/.builder/credentials.json` (mode 0600; `BUILDER_CREDENTIALS`
overrides the path). Refresh tokens ROTATE on every use and the server treats a spent
token presented again as a leak — it revokes the whole device. Two things follow:

* the rotated pair is written atomically (temp file + `os.replace`) BEFORE the retried
  request is sent, so a crash between refresh and retry cannot leave the old, now-dead
  token on disk;
* one process must never refresh twice concurrently. This client is single-threaded and
  refreshes at most once per request (`retry_on_401` is cleared on the retry), which is the
  same guarantee the Mac gets from actor isolation.

`BUILDER_CREDENTIALS_JSON` may carry the file's contents inline (a secret in an environment
that has no persistent disk). It is materialised to the credentials path on first use so
that rotations within one container persist for that container's life — and
`docs/cloud-capture.md` explains why a static copy shared by several containers trips
the reuse detector.

A CAPTURE KEY (`BUILDER_CAPTURE_KEY`, or `--key`) is the credential for a fleet. It is a
`bck_…` string the phone minted, sent as the bearer on every request, never refreshed and
never written to disk by this client. The server accepts it on the two sync routes and
nowhere else, and a 401 with a key means the key is revoked (or never existed): the client
reports which key by its prefix and stops. There is nothing to retry — no refresh can
bring a revoked key back, and the paired-token path is untouched by any of this.
"""

from __future__ import annotations

import json
import os
import pathlib
import tempfile
import time
import urllib.error
import urllib.request

from . import CLIENT_VERSION, identity
from .tuning import PAIR_TIMEOUT_SEC

DEFAULT_SERVER = "http://localhost:8000"


class NotPaired(Exception):
    def __str__(self) -> str:
        return "not paired — run `python -m capture pair --server URL` first"


class HTTPFailure(Exception):
    def __init__(self, status: int, body: str):
        super().__init__(f"server returned {status}: {body[:200]}")
        self.status = status
        self.body = body


class PairingTimedOut(Exception):
    def __str__(self) -> str:
        return "pairing expired before it was approved"


#: The server's prefix for a capture key (`CAPTURE_KEY_PREFIX` in builder/auth.py). Checked
#: before the first request so a pasted refresh token or an empty variable fails here, with
#: a sentence, rather than as a 401 that reads like a revocation.
CAPTURE_KEY_PREFIX = "bck_"
#: How much of a key is ever printed: the same eight characters the phone lists.
CAPTURE_KEY_DISPLAY_CHARS = 8


class CaptureKeyRejected(Exception):
    """A 401 while authenticating with a capture key: revoked, or never valid. Final."""

    def __init__(self, prefix: str):
        super().__init__(prefix)
        self.prefix = prefix

    def __str__(self) -> str:
        return (
            f"capture key {self.prefix}… was rejected by the server (revoked or unknown); "
            "mint a new one in Builder → Settings → Cloud capture"
        )


class MalformedCaptureKey(Exception):
    def __str__(self) -> str:
        return f"BUILDER_CAPTURE_KEY does not look like a capture key (expected {CAPTURE_KEY_PREFIX}…)"


# ----------------------------------------------------------------------------- credentials


def credentials_path() -> pathlib.Path:
    override = os.environ.get("BUILDER_CREDENTIALS")
    if override:
        return pathlib.Path(override).expanduser()
    return pathlib.Path.home() / ".builder" / "credentials.json"


def pending_path() -> pathlib.Path:
    return credentials_path().with_name("pending-pair.json")


def state_path() -> pathlib.Path:
    return credentials_path().with_name("capture-state.json")


def write_private_json(path: pathlib.Path, data: dict) -> None:
    """Atomic replace, mode 0600, parent directory 0700."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=1, sort_keys=True)
            f.write("\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_json(path: pathlib.Path) -> dict | None:
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def load_credentials() -> dict | None:
    path = credentials_path()
    creds = read_json(path)
    if creds is None:
        inline = os.environ.get("BUILDER_CREDENTIALS_JSON", "").strip()
        if inline:
            try:
                creds = json.loads(inline)
            except json.JSONDecodeError:
                creds = None
            if isinstance(creds, dict) and creds.get("refresh_token"):
                write_private_json(path, creds)
            else:
                creds = None
    if creds and creds.get("refresh_token") and creds.get("access_token"):
        return creds
    return None


def capture_key(explicit: str | None = None) -> str | None:
    """The key from `--key`, else `BUILDER_CAPTURE_KEY`; None when neither is set.

    Whitespace is stripped because the value is pasted into an environment-settings form.
    Anything else that is not `bck_…` is refused before a request is made.
    """
    raw = (explicit if explicit is not None else os.environ.get("BUILDER_CAPTURE_KEY", "")).strip()
    if not raw:
        return None
    if not raw.startswith(CAPTURE_KEY_PREFIX):
        raise MalformedCaptureKey()
    return raw


def key_prefix(raw: str) -> str:
    return raw[:CAPTURE_KEY_DISPLAY_CHARS]


def machine_identity(existing: dict | None) -> str:
    """The hashed machine id this client presents. Pinned by the credentials file once
    chosen, so a re-pair keeps the same device row on the server."""
    if existing and existing.get("machine_id"):
        return existing["machine_id"]
    return identity.machine_id(identity.raw_machine_identifier())


# ----------------------------------------------------------------------------- transport


class Client:
    def __init__(
        self,
        server: str,
        opener=None,
        sleep=time.sleep,
        clock=time.time,
        client_version: str = CLIENT_VERSION,
        key: str | None = None,
    ):
        self.server = server.rstrip("/")
        self._open = opener or urllib.request.urlopen
        self._sleep = sleep
        self._clock = clock
        self.client_version = client_version
        #: A capture key. When set, `_authenticated` sends it and never touches the
        #: credentials file or `/v1/auth/refresh`.
        self.key = key

    # -- raw ---------------------------------------------------------------------------

    def _request(
        self, method: str, path: str, body: dict | None, token: str | None
    ) -> tuple[int, dict]:
        data = None
        headers = {"Accept": "application/json", "User-Agent": f"builder-capture/{CLIENT_VERSION}"}
        if body is not None:
            data = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(self.server + path, data=data, method=method, headers=headers)
        try:
            with self._open(req, timeout=60) as resp:
                status = resp.status
                raw = resp.read()
        except urllib.error.HTTPError as e:
            status = e.code
            raw = e.read()
        except (urllib.error.URLError, OSError) as e:
            raise HTTPFailure(0, f"network error: {e}") from e
        try:
            parsed = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            parsed = {"_raw": raw.decode("utf-8", "replace")}
        return status, parsed if isinstance(parsed, dict) else {"_raw": parsed}

    def _unauthenticated(self, method: str, path: str, body: dict | None) -> dict:
        status, parsed = self._request(method, path, body, None)
        if not 200 <= status < 300:
            raise HTTPFailure(status, json.dumps(parsed))
        return parsed

    def _authenticated(self, method: str, path: str, body: dict | None) -> dict:
        """Bearer from the credentials file; on 401 refresh ONCE, store, retry ONCE.

        With a capture key: bearer is the key, a 401 is final (`CaptureKeyRejected`), and
        neither the credentials file nor the refresh route is consulted.
        """
        if self.key is not None:
            status, parsed = self._request(method, path, body, self.key)
            if status == 401:
                raise CaptureKeyRejected(key_prefix(self.key))
            if not 200 <= status < 300:
                raise HTTPFailure(status, json.dumps(parsed))
            return parsed
        creds = load_credentials()
        if creds is None:
            raise NotPaired()
        status, parsed = self._request(method, path, body, creds["access_token"])
        if status == 401:
            creds = self.refresh(creds)
            status, parsed = self._request(method, path, body, creds["access_token"])
        if not 200 <= status < 300:
            raise HTTPFailure(status, json.dumps(parsed))
        return parsed

    # -- auth --------------------------------------------------------------------------

    def refresh(self, creds: dict) -> dict:
        """POST /v1/auth/refresh. The new pair is on disk before this returns.

        A failed refresh means the refresh token is dead — expired, revoked, or spent by
        another copy of these credentials. The tokens are cleared (the machine id and
        server are kept, so the next `pair` reuses the same device) and every later call
        becomes a clear "not paired" instead of an endless 401 loop.
        """
        status, parsed = self._request(
            "POST", "/v1/auth/refresh", {"refresh_token": creds["refresh_token"]}, None
        )
        if not 200 <= status < 300 or not parsed.get("access_token"):
            stripped = {
                k: v for k, v in creds.items() if k not in ("access_token", "refresh_token")
            }
            write_private_json(credentials_path(), stripped)
            raise HTTPFailure(status, json.dumps(parsed))
        creds = dict(creds)
        creds["access_token"] = parsed["access_token"]
        creds["refresh_token"] = parsed["refresh_token"]
        creds["refreshed_at"] = self._clock()
        write_private_json(credentials_path(), creds)
        return creds

    def device_start(self, machine_id: str, label: str) -> dict:
        return self._unauthenticated(
            "POST",
            "/v1/auth/device/start",
            {
                "machine_id": machine_id,
                "label": label,
                "platform": "linux",
                "agent_version": self.client_version,
            },
        )

    def device_poll_once(self, device_code: str) -> dict:
        """One poll. `authorization_pending` is a 200 with a status, not an error."""
        return self._unauthenticated("POST", "/v1/auth/device/poll", {"device_code": device_code})

    def complete_pairing(self, pending: dict, poll_result: dict) -> dict:
        creds = {
            "server": self.server,
            "machine_id": pending["machine_id"],
            "label": pending["label"],
            "access_token": poll_result["access_token"],
            "refresh_token": poll_result["refresh_token"],
            "paired_at": self._clock(),
        }
        write_private_json(credentials_path(), creds)
        try:
            pending_path().unlink()
        except OSError:
            pass
        return creds

    def await_pairing(self, pending: dict, interval: int, timeout: float = PAIR_TIMEOUT_SEC):
        deadline = self._clock() + timeout
        while self._clock() < deadline:
            r = self.device_poll_once(pending["device_code"])
            if r.get("status") == "ok" and r.get("access_token") and r.get("refresh_token"):
                return self.complete_pairing(pending, r)
            self._sleep(max(1, int(interval)))
        raise PairingTimedOut()

    # -- sync --------------------------------------------------------------------------

    def known_hashes(self) -> dict[str, str]:
        r = self._authenticated("GET", "/v1/sync/known", None)
        known = r.get("known")
        return known if isinstance(known, dict) else {}

    def put_narrative(self, doc: dict) -> dict:
        """PUT /v1/profile/narrative: the "how you work" page this machine just wrote.

        One document per person, replaced rather than appended: a narrative describes the
        corpus as it stands, and the server keeps no history of ones that no longer do.
        """
        return self._authenticated("PUT", "/v1/profile/narrative", doc)

    def put_report(self, doc: dict) -> dict:
        """PUT /v1/profile/report: the measured half of the profile.

        Same shape as `put_narrative` and for the same reason — one document per person,
        replaced rather than appended. Unlike the narrative no model was involved, so this
        one is cheap enough to run on a schedule.
        """
        return self._authenticated("PUT", "/v1/profile/report", doc)

    def upload(self, sessions: list[dict], chunk_size: int = 200) -> dict:
        """POST /v1/sync/sessions:batch in chunks of 200, as the Mac does."""
        accepted = unchanged = 0
        rejected: list[dict] = []
        for i in range(0, len(sessions), chunk_size):
            r = self._authenticated(
                "POST", "/v1/sync/sessions:batch", {"sessions": sessions[i : i + chunk_size]}
            )
            accepted += int(r.get("accepted", 0))
            unchanged += int(r.get("unchanged", 0))
            rejected.extend(r.get("rejected") or [])
        return {"accepted": accepted, "unchanged": unchanged, "rejected": rejected}
