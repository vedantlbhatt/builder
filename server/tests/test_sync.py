"""The sync path end to end, AS builder_app, through the real routes.

Contract v2 changed what a row means — a live snapshot replaced in place, two clocks, an
opt-in analysis — and every one of those is a thing the server can get quietly wrong: a
second row instead of a replacement, an analysis wiped by the next snapshot, a local date
one day off between midnight and four. These tests upload real payloads through
`/v1/sync/sessions:batch` and read back through the session routes and, where the
guarantee is about isolation, through a restricted connection with a different viewer.

Same harness as test_auth.py: the API engine is pointed at `builder_app`, so RLS is real,
and devices are minted through the pairing flow rather than inserted by hand.

Run with a local Postgres:
    BUILDER_TEST_DB=postgresql+psycopg://localhost/builder_test pytest tests/test_sync.py
"""

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from test_contract import SAMPLE_ANALYSIS, valid_payload

TEST_DB = os.environ.get("BUILDER_TEST_DB")
pytestmark = pytest.mark.skipif(not TEST_DB, reason="set BUILDER_TEST_DB to run")


def app_url() -> str:
    """builder_app's connection string, derived structurally — see test_rls.app_url."""
    u = make_url(TEST_DB)
    return str(
        u.set(
            username="builder_app",
            password=os.environ.get("BUILDER_TEST_APP_PASSWORD", u.password),
        ).render_as_string(hide_password=False)
    )


def owner_engine():
    return create_engine(TEST_DB, future=True)


def app_engine():
    return create_engine(app_url(), future=True)


def _ed25519_pem() -> str:
    key = ed25519.Ed25519PrivateKey.generate()
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


# ------------------------------------------------------------------------- fixtures


@pytest.fixture
def app_env(monkeypatch):
    import builder.auth as auth
    import builder.db as db_module
    from builder.settings import settings

    monkeypatch.setenv("APP_DATABASE_URL", app_url())
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("JWT_PRIVATE_KEY", _ed25519_pem())
    settings.cache_clear()
    db_module._engine = None
    auth._jwks_cache.clear()
    yield
    settings.cache_clear()
    db_module._engine = None
    auth._jwks_cache.clear()


@pytest.fixture
def client(app_env):
    from fastapi.testclient import TestClient

    from builder.main import app

    return TestClient(app)


@pytest.fixture
def created_users():
    """Deleted as the owner on the way out; sessions, strips, stats and analyses cascade."""
    ids: list[str] = []
    yield ids
    if ids:
        with owner_engine().begin() as c:
            c.execute(text("DELETE FROM users WHERE id = ANY(CAST(:ids AS uuid[]))"), {"ids": ids})


def _pair(client, created_users) -> tuple[str, dict]:
    """A fresh user with a paired Mac. Returns (user_id, bearer headers)."""
    uid = str(uuid.uuid4())
    with owner_engine().begin() as c:
        c.execute(text("INSERT INTO users (id) VALUES (:i)"), {"i": uid})
    created_users.append(uid)

    machine = uuid.uuid4().hex * 2
    r = client.post(
        "/v1/auth/device/start",
        json={"machine_id": machine, "label": "test-mac", "agent_version": "0.1"},
    )
    assert r.status_code == 200, r.text
    started = r.json()
    with owner_engine().begin() as c:
        c.execute(
            text(
                "UPDATE device_grants SET user_id = :u, approved_at = now() WHERE user_code = :uc"
            ),
            {"u": uid, "uc": started["user_code"]},
        )
    r = client.post("/v1/auth/device/poll", json={"device_code": started["device_code"]})
    assert r.status_code == 200 and r.json()["status"] == "ok", r.text
    return uid, {"authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture
def paired(client, created_users):
    return _pair(client, created_users)


def _payload(**overrides) -> dict:
    """A JSON-ready payload with a unique session id and content hash unless overridden."""
    base = {
        "client_session_id": uuid.uuid4().hex * 2,
        "content_hash": uuid.uuid4().hex * 2,
    }
    base.update(overrides)
    return valid_payload(**base).model_dump(mode="json")


def _live(started: datetime, minutes: int, **overrides) -> dict:
    """A live snapshot `minutes` in: attended throughout, still running."""
    active = minutes * 60
    return _payload(
        state="live",
        end_reason="still_running",
        started_at=started,
        ended_at=started + timedelta(minutes=minutes),
        active_seconds=active,
        attended_seconds=active,
        autonomous_seconds=0,
        notable=False,
        **overrides,
    )


def _upload(client, headers, *payloads) -> dict:
    r = client.post("/v1/sync/sessions:batch", json={"sessions": list(payloads)}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def _owner_rows(user_id: str) -> list:
    with owner_engine().connect() as c:
        return c.execute(
            text(
                """
                SELECT id, client_session_id, state, end_reason, active_seconds,
                       attended_seconds, autonomous_seconds, presence_count, unattended,
                       local_date, local_hour, local_dow, started_at
                FROM sessions WHERE user_id = :u ORDER BY started_at
                """
            ),
            {"u": user_id},
        ).all()


# ---------------------------------------------------------------------------- tests


def test_live_snapshot_is_replaced_in_place_by_the_final(client, paired):
    """Same client_session_id, state flips live -> final, ONE row. A second row would
    double the hours the moment a session finished."""
    uid, headers = paired
    started = datetime(2026, 8, 15, 16, 0, tzinfo=UTC)
    csid = uuid.uuid4().hex * 2

    live = _live(started, 20, client_session_id=csid)
    assert _upload(client, headers, live)["accepted"] == 1

    rows = _owner_rows(uid)
    assert len(rows) == 1 and rows[0].state == "live"
    assert rows[0].end_reason == "still_running"

    r = client.get("/v1/sessions/live", headers=headers)
    assert [s["client_session_id"] for s in r.json()["sessions"]] == [csid]

    final = _payload(
        client_session_id=csid,
        started_at=started,
        ended_at=started + timedelta(hours=1),
        active_seconds=3600,
        attended_seconds=3000,
        autonomous_seconds=600,
        presence_count=12,
    )
    assert _upload(client, headers, final)["accepted"] == 1

    rows = _owner_rows(uid)
    assert len(rows) == 1, "the final must replace the live row, not sit beside it"
    row = rows[0]
    assert row.state == "final" and row.end_reason == "idle_gap"
    assert (row.active_seconds, row.attended_seconds, row.autonomous_seconds) == (3600, 3000, 600)
    assert row.presence_count == 12 and row.unattended is False

    assert client.get("/v1/sessions/live", headers=headers).json()["sessions"] == []
    listed = client.get("/v1/sessions", headers=headers).json()["sessions"]
    assert [s["client_session_id"] for s in listed] == [csid]
    assert listed[0]["state"] == "final"
    assert listed[0]["attended_seconds"] == 3000

    # Re-sending the identical final is free.
    assert _upload(client, headers, final)["unchanged"] == 1


def test_analysis_upserts_reads_back_and_is_not_retracted_by_a_payload_without_one(client, paired):
    uid, headers = paired
    csid = uuid.uuid4().hex * 2

    with_analysis = _payload(client_session_id=csid, analysis=SAMPLE_ANALYSIS)
    assert _upload(client, headers, with_analysis)["accepted"] == 1
    sid = _owner_rows(uid)[0].id

    r = client.get(f"/v1/sessions/{sid}", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["analysis"] == SAMPLE_ANALYSIS

    # An updated analysis for the same session replaces the stored one.
    revised = {**SAMPLE_ANALYSIS, "headline": "Revised on the checkpoint", "digest_hash": "f" * 64}
    assert (
        _upload(client, headers, _payload(client_session_id=csid, analysis=revised))["accepted"]
        == 1
    )
    body = client.get(f"/v1/sessions/{sid}", headers=headers).json()["analysis"]
    assert body["headline"] == "Revised on the checkpoint"
    with owner_engine().connect() as c:
        stored = c.execute(
            text(
                "SELECT digest_hash, analysis_version FROM session_analysis WHERE session_id = :s"
            ),
            {"s": sid},
        ).one()
    assert stored.digest_hash == "f" * 64 and stored.analysis_version == 1

    # A later payload with no analysis (the user turned upload off, or a snapshot that did
    # not run one) leaves the stored document alone.
    assert _upload(client, headers, _payload(client_session_id=csid))["accepted"] == 1
    assert client.get(f"/v1/sessions/{sid}", headers=headers).json()["analysis"] == body

    # And a session that never had one reads back null, not a missing key.
    other = _payload()
    _upload(client, headers, other)
    other_id = next(
        r.id for r in _owner_rows(uid) if r.client_session_id == other["client_session_id"]
    )
    detail = client.get(f"/v1/sessions/{other_id}", headers=headers).json()
    assert "analysis" in detail and detail["analysis"] is None


def test_sanity_gate_rejections_arrive_as_rejected_not_as_rows(client, paired):
    uid, headers = paired
    split_wrong = _payload(attended_seconds=1000, autonomous_seconds=1000)
    robot = _payload(presence_count=0, unattended=False)
    fine = _payload()

    out = _upload(client, headers, split_wrong, robot, fine)
    assert out["accepted"] == 1
    reasons = {r["client_session_id"]: r["reason"] for r in out["rejected"]}
    assert "active_seconds is 3600" in reasons[split_wrong["client_session_id"]]
    assert "presence_count is 0" in reasons[robot["client_session_id"]]

    stored = {r.client_session_id for r in _owner_rows(uid)}
    assert stored == {fine["client_session_id"]}


def test_local_date_honours_the_four_am_boundary(client, paired):
    """01:30 local on the 15th is the evening of the 14th, on the server as on the Mac.

    tz -420 (Pacific daylight time): 08:30Z is 01:30 local. Before the fix the server
    filed it under the 15th while the menu bar said the 14th, so a late night broke the
    streak on one screen and not the other. local_dow follows the date (the 14th is a
    Friday: isodow 5, stored 4); local_hour stays the clock reading, 1."""
    uid, headers = paired
    late = datetime(2026, 8, 15, 8, 30, tzinfo=UTC)
    morning = datetime(2026, 8, 15, 16, 30, tzinfo=UTC)  # 09:30 local, same calendar day
    a = _payload(started_at=late, ended_at=late + timedelta(hours=1), tz_offset_minutes=-420)
    b = _payload(started_at=morning, ended_at=morning + timedelta(hours=1), tz_offset_minutes=-420)
    _upload(client, headers, a, b)

    rows = {r.client_session_id: r for r in _owner_rows(uid)}
    night = rows[a["client_session_id"]]
    day = rows[b["client_session_id"]]
    assert night.local_date.isoformat() == "2026-08-14"
    assert night.local_dow == 4
    assert night.local_hour == 1
    assert day.local_date.isoformat() == "2026-08-15"
    assert day.local_dow == 5
    assert day.local_hour == 9

    # A day_boundary cut legitimately moves started_at; the upsert must follow it.
    moved = _payload(
        client_session_id=a["client_session_id"],
        started_at=late + timedelta(hours=3),  # 04:30 local
        ended_at=late + timedelta(hours=4),
        tz_offset_minutes=-420,
        end_reason="day_boundary",
    )
    _upload(client, headers, moved)
    night = {r.client_session_id: r for r in _owner_rows(uid)}[a["client_session_id"]]
    assert night.local_date.isoformat() == "2026-08-15"
    assert night.end_reason == "day_boundary"


def test_live_endpoint_and_profile_keep_live_out_of_the_aggregates(client, paired):
    uid, headers = paired
    t0 = datetime(2026, 8, 15, 15, 0, tzinfo=UTC)
    final = _payload(
        started_at=t0 - timedelta(hours=3),
        ended_at=t0 - timedelta(hours=2),
        attended_seconds=2400,
        autonomous_seconds=1200,
    )
    first_live = _live(t0, 30)
    second_live = _live(t0 + timedelta(minutes=5), 10)
    # Separate requests so updated_at differs; `now()` is fixed within one transaction.
    _upload(client, headers, final)
    _upload(client, headers, first_live)
    _upload(client, headers, second_live)

    live = client.get("/v1/sessions/live", headers=headers).json()["sessions"]
    assert [s["client_session_id"] for s in live] == [
        second_live["client_session_id"],
        first_live["client_session_id"],
    ], "newest update first"
    for s in live:
        assert s["state"] == "live" and s["end_reason"] == "still_running"
        assert {"attended_seconds", "autonomous_seconds", "presence_count", "updated_at"} <= set(s)

    p = client.get("/v1/profile", headers=headers).json()
    assert [s["client_session_id"] for s in p["live"]] == [s["client_session_id"] for s in live]
    assert p["totals"] == {"sessions": 1, "active_seconds": 3600}
    assert sum(g["active_seconds"] for g in p["graph"]) == 3600
    assert p["longest_session"]["attended_seconds"] == 2400
    assert p["longest_session"]["active_seconds"] == 3600
    assert p["attribution"]["attended_seconds"] == 2400
    assert p["attribution"]["autonomous_seconds"] == 1200

    # The list is final-only unless asked; `include_live` folds the snapshots in.
    only_final = client.get("/v1/sessions?notable_only=false", headers=headers).json()
    assert {s["state"] for s in only_final["sessions"]} == {"final"}
    both = client.get("/v1/sessions?notable_only=false&include_live=true", headers=headers).json()
    assert {s["state"] for s in both["sessions"]} == {"final", "live"}
    assert len(both["sessions"]) == 3
    assert client.get("/v1/sessions?state=open", headers=headers).status_code == 422


def test_analysis_is_invisible_to_another_viewer(client, created_users):
    """RLS on session_analysis, exercised as builder_app with the other user's viewer —
    through the route and through a bare connection, because a 404 alone could be the
    sessions policy doing the work while the analysis table sits unprotected."""
    uid_a, headers_a = _pair(client, created_users)
    uid_b, headers_b = _pair(client, created_users)

    _upload(client, headers_a, _payload(analysis=SAMPLE_ANALYSIS))
    sid = _owner_rows(uid_a)[0].id

    assert client.get(f"/v1/sessions/{sid}", headers=headers_a).json()["analysis"] is not None
    assert client.get(f"/v1/sessions/{sid}", headers=headers_b).status_code == 404

    with app_engine().connect() as c:
        c.execute(text("SELECT set_config('app.viewer_id', :v, false)"), {"v": uid_b})
        as_b = c.execute(text("SELECT count(*) FROM session_analysis")).scalar()
        c.execute(text("SELECT set_config('app.viewer_id', :v, false)"), {"v": uid_a})
        as_a = c.execute(
            text("SELECT count(*) FROM session_analysis WHERE session_id = :s"), {"s": sid}
        ).scalar()
        c.execute(text("SELECT set_config('app.viewer_id', '', false)"))
        as_nobody = c.execute(text("SELECT count(*) FROM session_analysis")).scalar()
    assert as_a == 1, "the owner must see their own analysis, or the table is over-locked"
    assert as_b == 0
    assert as_nobody == 0, "an unshared analysis must not be public"
