"""python3 -m unittest capture.tests.test_client

Refresh-on-401 against a real HTTP server in a thread, including the rotation: every
refresh returns a NEW refresh token, the old one is spent, and presenting a spent token
again revokes the device. The client must therefore store the rotated pair BEFORE it
retries, and must never present a token twice. The pairing flow and chunked upload are
exercised the same way.
"""

from __future__ import annotations

import json
import os
import pathlib
import stat
import tempfile
import unittest

from capture import client as cl
from capture.tests._fake_server import FakeBuilder


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.creds_path = pathlib.Path(self.tmp.name) / "builder" / "credentials.json"
        os.environ["BUILDER_CREDENTIALS"] = str(self.creds_path)
        os.environ.pop("BUILDER_CREDENTIALS_JSON", None)
        self.server = FakeBuilder()
        self.url = self.server.start()
        self.client = cl.Client(self.url, sleep=lambda s: None)

    def tearDown(self):
        self.server.stop()
        self.tmp.cleanup()
        os.environ.pop("BUILDER_CREDENTIALS", None)

    def _write_creds(self, access: str, refresh: str):
        cl.write_private_json(
            self.creds_path,
            {
                "server": self.url,
                "machine_id": "m" * 64,
                "access_token": access,
                "refresh_token": refresh,
            },
        )

    def _creds(self) -> dict:
        return json.loads(self.creds_path.read_text())

    def _refresh_calls(self):
        return [
            b["refresh_token"] for m, p, b, t in self.server.requests if p == "/v1/auth/refresh"
        ]


class RefreshOn401(_Base):
    def test_401_refreshes_once_stores_the_rotated_pair_then_retries(self):
        access, refresh = self.server.seed()
        self._write_creds(access, refresh)
        self.server.expire_access()  # the stored access token is now dead

        self.assertEqual(self.client.known_hashes(), {})

        paths = [p for _, p, _, _ in self.server.requests]
        self.assertEqual(paths, ["/v1/sync/known", "/v1/auth/refresh", "/v1/sync/known"])
        stored = self._creds()
        self.assertNotEqual(stored["refresh_token"], refresh, "rotated token must be stored")
        self.assertIn(stored["access_token"], self.server.valid_access)
        self.assertEqual(stat.S_IMODE(self.creds_path.stat().st_mode), 0o600)
        self.assertEqual(self.server.reuse_detected, 0)

    def test_second_expiry_uses_the_new_refresh_token_never_the_spent_one(self):
        access, r0 = self.server.seed()
        self._write_creds(access, r0)
        self.server.expire_access()
        self.client.known_hashes()
        r1 = self._creds()["refresh_token"]

        self.server.expire_access()
        self.client.known_hashes()
        r2 = self._creds()["refresh_token"]

        self.assertEqual(self._refresh_calls(), [r0, r1])
        self.assertNotIn(r2, (r0, r1))
        self.assertEqual(self.server.reuse_detected, 0)

    def test_a_401_after_refresh_is_not_retried_again(self):
        """Exactly one refresh and one retry per call, whatever the server says next."""
        access, refresh = self.server.seed()
        self._write_creds(access, refresh)
        self.server.expire_access()
        # Make the freshly minted access token invalid too, so the retry 401s as well.
        original = self.server.handle

        def handle(method, path, body, token):
            status, out = original(method, path, body, token)
            if path == "/v1/auth/refresh" and status == 200:
                self.server.valid_access.discard(out["access_token"])
            return status, out

        self.server.handle = handle
        with self.assertRaises(cl.HTTPFailure) as ctx:
            self.client.known_hashes()
        self.assertEqual(ctx.exception.status, 401)
        self.assertEqual(len(self._refresh_calls()), 1)

    def test_spent_token_presented_again_is_reuse_and_clears_the_tokens(self):
        """Simulates a second container that was handed a static copy of the credentials:
        its refresh token is already spent, the server revokes the chain, and the client
        drops its tokens rather than looping on 401."""
        access, r0 = self.server.seed()
        self._write_creds(access, r0)
        self.server.expire_access()
        self.client.known_hashes()  # rotates r0 -> r1

        self._write_creds("stale", r0)  # the stale copy
        with self.assertRaises(cl.HTTPFailure):
            self.client.known_hashes()
        self.assertEqual(self.server.reuse_detected, 1)
        stored = self._creds()
        self.assertNotIn("refresh_token", stored)
        self.assertEqual(stored["machine_id"], "m" * 64, "machine id survives for the re-pair")
        with self.assertRaises(cl.NotPaired):
            self.client.known_hashes()

    def test_not_paired_makes_no_request(self):
        with self.assertRaises(cl.NotPaired):
            self.client.upload([{"client_session_id": "x", "content_hash": "y"}])
        self.assertEqual(self.server.requests, [])

    def test_inline_credentials_env_is_materialised_to_disk(self):
        access, refresh = self.server.seed()
        os.environ["BUILDER_CREDENTIALS_JSON"] = json.dumps(
            {
                "server": self.url,
                "machine_id": "m" * 64,
                "access_token": access,
                "refresh_token": refresh,
            }
        )
        try:
            self.assertEqual(self.client.known_hashes(), {})
            self.assertTrue(self.creds_path.exists())
            self.assertEqual(stat.S_IMODE(self.creds_path.stat().st_mode), 0o600)
        finally:
            os.environ.pop("BUILDER_CREDENTIALS_JSON", None)


class Pairing(_Base):
    def test_device_flow_stores_tokens_with_mode_0600(self):
        self.server.pending_polls = 2
        start = self.client.device_start("m" * 64, "test box")
        self.assertEqual(start["user_code"], "BCDF-GHJK")
        pending = {"device_code": start["device_code"], "machine_id": "m" * 64, "label": "test box"}
        cl.write_private_json(cl.pending_path(), pending)
        creds = self.client.await_pairing(pending, interval=start["interval"], timeout=60)
        self.assertIn(creds["access_token"], self.server.valid_access)
        self.assertEqual(stat.S_IMODE(self.creds_path.stat().st_mode), 0o600)
        self.assertFalse(cl.pending_path().exists())
        polls = [p for _, p, _, _ in self.server.requests if p == "/v1/auth/device/poll"]
        self.assertEqual(len(polls), 3, "two pending answers, then ok")
        start_body = next(b for _, p, b, _ in self.server.requests if p == "/v1/auth/device/start")
        self.assertEqual(
            set(start_body),
            {"machine_id", "label", "platform", "agent_version"},
            "the same body the Mac sends",
        )
        # Paired credentials work without a refresh.
        self.assertEqual(self.client.known_hashes(), {})
        self.assertEqual(self._refresh_calls(), [])

    def test_pairing_times_out(self):
        self.server.pending_polls = 10**6
        clock = [0.0]

        def tick(seconds):
            clock[0] += seconds

        client = cl.Client(self.url, sleep=tick, clock=lambda: clock[0])
        pending = {"device_code": "DEVCODE", "machine_id": "m" * 64, "label": "x"}
        with self.assertRaises(cl.PairingTimedOut):
            client.await_pairing(pending, interval=5, timeout=30)


class Upload(_Base):
    def test_batches_of_200_and_known_hash_skip(self):
        access, refresh = self.server.seed()
        self._write_creds(access, refresh)
        sessions = [{"client_session_id": f"s{i}", "content_hash": f"h{i}"} for i in range(250)]
        r = self.client.upload(sessions)
        self.assertEqual((r["accepted"], r["unchanged"]), (250, 0))
        self.assertEqual([len(u) for u in self.server.uploads], [200, 50])
        r = self.client.upload(sessions[:3])
        self.assertEqual((r["accepted"], r["unchanged"]), (0, 3))
        self.assertEqual(len(self.client.known_hashes()), 250)


if __name__ == "__main__":
    unittest.main()
