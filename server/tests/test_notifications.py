"""Completion pushes, decided on upload, AS builder_app through the real sync route.

docs/session-boundaries.md fixes when a finished session is news; CLAUDE.md adds that a
backfill must be silent. Both rules are the kind that fail quietly — a doubled banner, or
seventy-one banners on first launch — so every case here runs a payload through
`/v1/sync/sessions:batch` with `send_session_finished` replaced by a recorder and asserts
exactly what was sent AND what `session_notifications` remembers.

Same harness as test_sync.py, whose fixtures are reused directly.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from test_contract import SAMPLE_ANALYSIS
from test_sync import (  # noqa: F401 - fixtures are picked up by name
    TEST_DB,
    _live,
    _payload,
    _upload,
    app_env,
    client,
    created_users,
    owner_engine,
    paired,
)

pytestmark = pytest.mark.skipif(not TEST_DB, reason="set BUILDER_TEST_DB to run")

# pytest finds the imported fixtures through this module's namespace. Referencing them
# once here is what tells the linter the test parameters below do not shadow unused names.
_SHARED_FIXTURES = (app_env, client, created_users, paired)


@pytest.fixture
def sent(monkeypatch):
    """Replaces the APNs send with a recorder. sync.py calls it through the module
    attribute, which is what makes this substitution reach it."""
    import builder.routes.push as push

    calls: list[dict] = []

    def fake(user_id, title, body, session_id, *, unattended=False):
        calls.append(
            {
                "user_id": user_id,
                "title": title,
                "body": body,
                "session_id": session_id,
                "unattended": unattended,
            }
        )
        return 1

    monkeypatch.setattr(push, "send_session_finished", fake)
    return calls


def _fresh_final(ended_seconds_ago: int = 1000, **overrides) -> dict:
    """A final, notable, attended session that ENDED `ended_seconds_ago`.

    1h 42m attended (6120 s). The default end is 1000 s ago — what an on-time final looks
    like (notify.EXPECTED_FINAL_LAG_SEC: 900 s gap + 30 s tick + 60 s sync pass) and well
    inside the 1800 s horizon.

    `agent_observed_at` is always NOW, whatever the end was, because that is what the
    shipped client sends: SyncCommand.swift stamps it with `Date()` at payload-build
    time. A fixture that back-dated it would let a horizon anchored on the wrong field
    pass its own test."""
    now = datetime.now(UTC).replace(microsecond=0)
    ended = now - timedelta(seconds=ended_seconds_ago)
    base = {
        "started_at": ended - timedelta(seconds=6120),
        "ended_at": ended,
        "active_seconds": 6120,
        "attended_seconds": 6120,
        "autonomous_seconds": 0,
        "agent_observed_at": now,
    }
    base.update(overrides)
    return _payload(**base)


def _recorded(user_id: str) -> dict[str, str]:
    """client_session_id -> kind, read as the owner."""
    with owner_engine().connect() as c:
        rows = c.execute(
            text(
                """
                SELECT s.client_session_id, n.kind
                FROM session_notifications n JOIN sessions s ON s.id = n.session_id
                WHERE s.user_id = :u
                """
            ),
            {"u": user_id},
        ).all()
    return {r.client_session_id: r.kind for r in rows}


def test_a_fresh_final_notable_session_sends_once_and_a_reupload_sends_nothing_more(
    client, paired, sent
):
    uid, headers = paired
    p = _fresh_final(repo_name="gt-transit", analysis=SAMPLE_ANALYSIS)
    assert _upload(client, headers, p)["accepted"] == 1

    assert len(sent) == 1
    call = sent[0]
    assert call["user_id"] == uid
    assert call["title"] == "Session finished: 1h 42m in gt-transit"
    assert call["body"] == "+200 lines · 10 prompts · analysis ready"
    assert call["unattended"] is False
    with owner_engine().connect() as c:
        sid = c.execute(
            text("SELECT id FROM sessions WHERE client_session_id = :c"),
            {"c": p["client_session_id"]},
        ).scalar()
    assert call["session_id"] == str(sid)
    assert _recorded(uid) == {p["client_session_id"]: "session_finished"}

    # A new content hash (a revised analysis, more lines) is a refresh, not news.
    again = {**p, "content_hash": uuid.uuid4().hex * 2, "lines_added_agent": 400}
    assert _upload(client, headers, again)["accepted"] == 1
    assert len(sent) == 1
    assert _recorded(uid) == {p["client_session_id"]: "session_finished"}


def test_private_repo_and_no_analysis_read_differently(client, paired, sent):
    uid, headers = paired
    anonymous = _fresh_final()  # repo_hash set, repo_name absent: the client's allowlist
    _upload(client, headers, anonymous)
    assert sent[-1]["title"] == "Session finished: 1h 42m in a private repo"
    assert sent[-1]["body"] == "+200 lines · 10 prompts"

    no_repo = _fresh_final(repo_hash=None, repo_pepper_version=1)
    _upload(client, headers, no_repo)
    assert sent[-1]["title"] == "Session finished: 1h 42m"


def test_a_live_upload_sends_nothing_and_its_final_sends_once(client, paired, sent):
    uid, headers = paired
    csid = uuid.uuid4().hex * 2
    started = datetime.now(UTC).replace(microsecond=0) - timedelta(hours=2)
    live = _live(started, 20, client_session_id=csid, agent_observed_at=datetime.now(UTC))
    _upload(client, headers, live)
    assert sent == []
    assert _recorded(uid) == {}

    final = _fresh_final(client_session_id=csid)
    _upload(client, headers, final)
    assert len(sent) == 1 and sent[0]["title"].startswith("Session finished")
    assert _recorded(uid) == {csid: "session_finished"}


def test_a_backfilled_final_that_ended_three_hours_ago_is_suppressed_not_sent(client, paired, sent):
    """Backfill must be silent — and the silence must be remembered, or a later
    re-upload of the same session would find no record and announce it after all.

    This is the first-pairing shape exactly: the session ended hours ago, the server has
    never seen it (existing=None), and `agent_observed_at` is seconds old because the Mac
    stamped it when it built the payload. A horizon measured from the observation time
    sees a fresh session here and announces the whole history; the earlier version of
    this test only passed because it hand-set the observation 180 minutes back, which no
    shipped client does."""
    uid, headers = paired
    stale = _fresh_final(ended_seconds_ago=3 * 3600, analysis=SAMPLE_ANALYSIS)
    assert stale["agent_observed_at"] > stale["ended_at"]  # observed now, ended long ago
    assert _upload(client, headers, stale)["accepted"] == 1
    assert sent == []
    assert _recorded(uid) == {stale["client_session_id"]: "suppressed_stale"}

    # A refresh with yet another fresh observation time changes nothing.
    fresh_again = {
        **stale,
        "content_hash": uuid.uuid4().hex * 2,
        "agent_observed_at": datetime.now(UTC).isoformat(),
    }
    _upload(client, headers, fresh_again)
    assert sent == []
    assert _recorded(uid) == {stale["client_session_id"]: "suppressed_stale"}


def test_seventy_one_historical_finals_on_first_pairing_send_nothing(client, paired, sent):
    """The number from CLAUDE.md, run through the route as one backfill batch. Every one
    is notable, every one is `idle_gap`, every one is observed just now, and every one
    ended at least an hour ago. None may be delivered; all must be remembered."""
    uid, headers = paired
    history = [
        _fresh_final(ended_seconds_ago=3600 + i * 7200, analysis=SAMPLE_ANALYSIS) for i in range(71)
    ]
    assert _upload(client, headers, *history)["accepted"] == 71
    assert sent == []
    assert set(_recorded(uid).values()) == {"suppressed_stale"}
    assert len(_recorded(uid)) == 71


def test_the_horizon_is_measured_from_ended_at_not_agent_observed_at(client, paired, sent):
    """The positive case that pins the anchor. An on-time final — ended 1000 s ago, which
    is one gap, one tick and one sync pass — is delivered. The same payload with the
    observation time pushed hours back is STILL delivered, because the observation time
    is not part of the decision; and a payload observed just now whose end is past the
    horizon is not."""
    uid, headers = paired

    on_time = _fresh_final(ended_seconds_ago=1000)
    _upload(client, headers, on_time)
    assert [c["session_id"] for c in sent] and sent[-1]["title"].startswith("Session finished")
    assert _recorded(uid)[on_time["client_session_id"]] == "session_finished"

    observed_long_ago = _fresh_final(
        ended_seconds_ago=1000,
        agent_observed_at=datetime.now(UTC) - timedelta(hours=6),
    )
    _upload(client, headers, observed_long_ago)
    assert _recorded(uid)[observed_long_ago["client_session_id"]] == "session_finished"
    assert len(sent) == 2

    # Just past the horizon: 1800 s + a tick. Observed now, like everything else.
    just_stale = _fresh_final(ended_seconds_ago=1800 + 30)
    _upload(client, headers, just_stale)
    assert _recorded(uid)[just_stale["client_session_id"]] == "suppressed_stale"
    assert len(sent) == 2


def test_a_naive_timestamp_is_a_422_not_a_500(client, paired, sent):
    """`agent_observed_at`, `started_at` and `ended_at` are `AwareDatetime` on the wire.
    A timezone-less string used to parse as a naive datetime, and the first
    `now(UTC) - naive` in notify.plan raised TypeError out of the transaction: the whole
    batch rolled back and the client saw a 500 with no session named. Now the contract
    rejects it at the door, before anything touches the database."""
    uid, headers = paired
    good = _fresh_final()
    for field in ("agent_observed_at", "started_at", "ended_at"):
        bad = {**_fresh_final(), field: good[field].replace("Z", "").split("+")[0]}
        assert "Z" not in bad[field] and "+" not in bad[field], bad[field]
        r = client.post("/v1/sync/sessions:batch", json={"sessions": [good, bad]}, headers=headers)
        assert r.status_code == 422, r.text
        locs = [tuple(e["loc"]) for e in r.json()["detail"]]
        assert ("body", "sessions", 1, field) in locs, locs
    assert sent == []
    assert _recorded(uid) == {}


def test_an_unattended_run_gets_the_run_title(client, paired, sent):
    uid, headers = paired
    run = _fresh_final(
        active_seconds=11100,  # 3h 05m
        attended_seconds=0,
        autonomous_seconds=11100,
        started_at=datetime.now(UTC) - timedelta(seconds=11100 + 20 * 60),
        presence_count=0,
        unattended=True,
        notable=False,
        human_prompt_count=0,
    )
    _upload(client, headers, run)
    assert len(sent) == 1
    assert sent[0]["title"] == "Agent run finished"
    assert sent[0]["body"] == "ran 3h 05m unattended"
    assert sent[0]["unattended"] is True
    assert _recorded(uid) == {run["client_session_id"]: "agent_run_finished"}


def test_cuts_and_quiet_sessions_send_nothing(client, paired, sent):
    """human_returned: you are already here. day_boundary: the work continues. A
    non-notable attended session: below the floor nothing downstream reads."""
    uid, headers = paired
    returned = _fresh_final(end_reason="human_returned")
    boundary = _fresh_final(end_reason="day_boundary")
    quiet = _fresh_final(notable=False)
    assert _upload(client, headers, returned, boundary, quiet)["accepted"] == 3
    assert sent == []
    assert _recorded(uid) == {}


def test_a_push_failure_never_rolls_back_the_upload(client, paired, monkeypatch):
    import builder.routes.push as push

    def boom(*a, **k):
        raise RuntimeError("APNs is down")

    monkeypatch.setattr(push, "send_session_finished", boom)
    uid, headers = paired
    p = _fresh_final()
    assert _upload(client, headers, p)["accepted"] == 1
    assert _recorded(uid) == {p["client_session_id"]: "session_finished"}
    assert (
        client.get("/v1/sessions", headers=headers).json()["sessions"][0]["client_session_id"]
        == p["client_session_id"]
    )
