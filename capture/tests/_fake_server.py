"""A stand-in for the Builder server, in a thread, for the client tests.

It implements exactly the behaviour the client depends on and nothing else: bearer
checking, refresh-token ROTATION with reuse detection (a spent token presented again
revokes the device), capture keys (`bck_…`, sync routes only, revoked == unknown == 401),
the device-grant start/poll pair, `/v1/sync/known` and the batch upload. Every request is
recorded so a test can assert what was — and was not — sent.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class FakeBuilder:
    def __init__(self):
        self.valid_access: set[str] = set()
        self.live_refresh: dict[str, str] = {}  # refresh token -> chain id
        self.spent_refresh: set[str] = set()
        self.revoked_chains: set[str] = set()
        self.reuse_detected = 0
        self.requests: list[tuple[str, str, dict | None, str | None]] = []
        self.known: dict[str, str] = {}
        self.uploads: list[list[dict]] = []
        self.pending_polls = 0  # authorization_pending answers before "ok"
        self.valid_keys: set[str] = set()  # capture keys the server accepts on sync routes
        self.counter = 0
        self.lock = threading.Lock()
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):  # silence
                pass

            def _send(self, status: int, body: dict):
                raw = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _body(self):
                n = int(self.headers.get("Content-Length") or 0)
                return json.loads(self.rfile.read(n)) if n else None

            def _bearer(self):
                h = self.headers.get("Authorization", "")
                return h[7:] if h.lower().startswith("bearer ") else None

            def do_GET(self):
                self._route("GET")

            def do_POST(self):
                self._route("POST")

            def _route(self, method):
                body = self._body() if method == "POST" else None
                token = self._bearer()
                with server.lock:
                    server.requests.append((method, self.path, body, token))
                    status, out = server.handle(method, self.path, body, token)
                self._send(status, out)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    # -- lifecycle ----------------------------------------------------------------------

    def start(self) -> str:
        self.thread.start()
        return f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    # -- helpers ------------------------------------------------------------------------

    def _mint(self, chain: str) -> tuple[str, str]:
        self.counter += 1
        access, refresh = f"A{self.counter}", f"R{self.counter}"
        self.valid_access.add(access)
        self.live_refresh[refresh] = chain
        return access, refresh

    def seed(self) -> tuple[str, str]:
        """A paired device: returns (access, refresh) for chain 'dev'."""
        return self._mint("dev")

    def expire_access(self):
        self.valid_access.clear()

    # -- routes -------------------------------------------------------------------------

    def handle(self, method, path, body, token):
        if path == "/v1/auth/refresh" and method == "POST":
            raw = (body or {}).get("refresh_token")
            if raw in self.spent_refresh:
                self.reuse_detected += 1
                chain = self.live_refresh.get(raw, "dev")
                self.revoked_chains.add(chain)
                for r, c in list(self.live_refresh.items()):
                    if c == chain:
                        self.spent_refresh.add(r)
                return 401, {"detail": "refresh token reuse detected; all tokens revoked"}
            if raw not in self.live_refresh:
                return 401, {"detail": "unknown refresh token"}
            chain = self.live_refresh[raw]
            if chain in self.revoked_chains:
                return 401, {"detail": "device revoked"}
            self.spent_refresh.add(raw)
            access, refresh = self._mint(chain)
            return 200, {"access_token": access, "refresh_token": refresh, "expires_in": 900}

        if path == "/v1/auth/device/start" and method == "POST":
            return 200, {
                "device_code": "DEVCODE",
                "user_code": "BCDF-GHJK",
                "verification_uri": "http://fake/pair",
                "expires_in": 900,
                "interval": 5,
            }
        if path == "/v1/auth/device/poll" and method == "POST":
            if (body or {}).get("device_code") != "DEVCODE":
                return 400, {"detail": "unknown device_code"}
            if self.pending_polls > 0:
                self.pending_polls -= 1
                return 200, {"status": "authorization_pending"}
            access, refresh = self._mint("paired")
            return 200, {
                "status": "ok",
                "access_token": access,
                "refresh_token": refresh,
                "expires_in": 900,
            }

        # everything below needs a bearer: a device token, or on the sync routes a key
        if token is not None and token.startswith("bck_"):
            if not path.startswith("/v1/sync/"):
                return 401, {"detail": "capture keys are accepted by the sync routes only"}
            if token not in self.valid_keys:
                return 401, {"detail": "invalid capture key"}
        elif token not in self.valid_access:
            return 401, {"detail": "invalid token"}
        if path == "/v1/sync/known" and method == "GET":
            return 200, {"known": dict(self.known)}
        if path == "/v1/sync/sessions:batch" and method == "POST":
            sessions = (body or {}).get("sessions") or []
            if len(sessions) > 250:
                return 413, {"detail": "at most 250 sessions per batch"}
            self.uploads.append(sessions)
            accepted = unchanged = 0
            for s in sessions:
                if self.known.get(s["client_session_id"]) == s["content_hash"]:
                    unchanged += 1
                else:
                    self.known[s["client_session_id"]] = s["content_hash"]
                    accepted += 1
            return 200, {"accepted": accepted, "unchanged": unchanged, "rejected": []}
        return 404, {"detail": "no route"}
