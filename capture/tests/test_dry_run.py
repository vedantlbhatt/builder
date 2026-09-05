"""python3 -m unittest capture.tests.test_dry_run

`--dry-run` prints every byte that would be sent and sends nothing — the command the
privacy page tells people to run. Proven against a listening server that records every
request: after a dry run its log is empty. The live-snapshot limiter and the
`/v1/sync/known` skip are exercised against the same server.

Two transcripts: a boundary fixture (two finished sittings, dated 2026-03) and a
synthetic one whose last record is seconds old, which is the only way to have a session
the clock still calls live.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import io
import json
import os
import pathlib
import shutil
import tempfile
import time
import unittest

from capture import cli
from capture import client as cl
from capture.tests._fake_server import FakeBuilder

ROOT = pathlib.Path(__file__).resolve().parents[2]
FIX = ROOT / "spec" / "fixtures" / "boundaries"
UUID = "00000000-0000-4000-8000-000000000001"
UUID_LIVE = "00000000-0000-4000-8000-000000000002"


def _iso(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts, dt.UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def write_live_transcript(path: pathlib.Path, now: float, seconds: int = 400) -> None:
    """A sitting that started `seconds` ago and is still going: one remote prompt, then
    agent chatter every 8 s (with usage and unique message ids) up to a moment ago."""
    recs = []
    t = now - seconds
    recs.append(
        {
            "type": "user",
            "uuid": "u-0",
            "parentUuid": None,
            "sessionId": UUID_LIVE,
            "timestamp": _iso(t),
            "cwd": "/nonexistent/proj",
            "promptSource": "sdk",
            "origin": {"kind": "human"},
            "message": {"role": "user", "content": "go"},
        }
    )
    n = 0
    t += 5
    while t < now - 2:
        n += 1
        recs.append(
            {
                "type": "assistant",
                "uuid": f"a-{n}",
                "parentUuid": "u-0" if n == 1 else f"a-{n - 1}",
                "sessionId": UUID_LIVE,
                "timestamp": _iso(t),
                "cwd": "/nonexistent/proj",
                "message": {
                    "role": "assistant",
                    "model": "claude-sonnet-5",
                    "id": f"msg_{n}",
                    "content": [{"type": "tool_use", "id": f"tu_{n}", "name": "Bash", "input": {}}],
                    "usage": {"input_tokens": 10, "output_tokens": 3},
                },
            }
        )
        t += 8
    path.write_text("".join(json.dumps(r, separators=(",", ":")) + "\n" for r in recs))


class DryRun(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = pathlib.Path(self.tmp.name)
        self.root = base / "projects"
        proj = self.root / "-Users-dev-proj"
        proj.mkdir(parents=True)
        shutil.copy(FIX / "attended_afternoon_then_gap.jsonl", proj / f"{UUID}.jsonl")
        live = self.root / "-Users-dev-live"
        live.mkdir()
        write_live_transcript(live / f"{UUID_LIVE}.jsonl", time.time())
        self.creds = base / "builder" / "credentials.json"
        os.environ["BUILDER_CREDENTIALS"] = str(self.creds)
        os.environ["BUILDER_TZ"] = "America/New_York"
        self.server = FakeBuilder()
        self.url = self.server.start()
        access, refresh = self.server.seed()
        cl.write_private_json(
            self.creds,
            {
                "server": self.url,
                "machine_id": "m" * 64,
                "access_token": access,
                "refresh_token": refresh,
            },
        )

    def tearDown(self):
        self.server.stop()
        self.tmp.cleanup()
        os.environ.pop("BUILDER_CREDENTIALS", None)
        os.environ.pop("BUILDER_TZ", None)

    def _run(self, *args) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = cli.main(["sync", "--root", str(self.root), "--server", self.url, *args])
        return rc, out.getvalue(), err.getvalue()

    def test_dry_run_prints_the_payloads_and_sends_nothing(self):
        rc, out, err = self._run("--dry-run", "--live")
        self.assertEqual(rc, 0)
        doc = json.loads(out)
        states = sorted(p["state"] for p in doc["sessions"])
        self.assertEqual(states, ["final", "final", "live"])
        self.assertIn("Nothing was", err)
        self.assertEqual(self.server.requests, [], "a dry run must not touch the network")

    def test_open_session_is_skipped_without_live(self):
        _, out, err = self._run("--dry-run")
        doc = json.loads(out)
        self.assertEqual([p["state"] for p in doc["sessions"]], ["final", "final"])
        self.assertIn("1 open (skipped; pass --live)", err)

    def test_finalize_turns_the_open_session_final(self):
        _, out, _ = self._run("--dry-run", "--finalize")
        doc = json.loads(out)
        self.assertEqual([p["state"] for p in doc["sessions"]], ["final"] * 3)
        self.assertTrue(all(p["end_reason"] == "idle_gap" for p in doc["sessions"]))

    def test_real_sync_uploads_then_is_up_to_date(self):
        rc, out, err = self._run("--live")
        self.assertEqual(rc, 0, err)
        self.assertEqual(len(self.server.uploads), 1)
        sent = self.server.uploads[0]
        self.assertEqual(sorted(p["state"] for p in sent), ["final", "final", "live"])
        rc, out, err = self._run("--live")
        self.assertEqual(rc, 0)
        self.assertEqual(len(self.server.uploads), 1, "known hashes skip the re-send")
        self.assertIn("Already up to date", out)

    def test_live_snapshot_is_rate_limited_per_session(self):
        """Inside liveUploadMinIntervalSec the live snapshot is not re-sent even when the
        server has forgotten it (a lost server row must not turn into a burst)."""
        self._run("--live")
        self.server.known.clear()
        self._run("--live")
        self.assertEqual(len(self.server.uploads), 2)
        self.assertEqual([p["state"] for p in self.server.uploads[1]], ["final", "final"])
        state = json.loads(cl.state_path().read_text())
        self.assertEqual(len(state["live"]), 1)


if __name__ == "__main__":
    unittest.main()
