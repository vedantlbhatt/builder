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


def _fresh_final(minutes_ago: int = 5, **overrides) -> dict:
    """A final, notable, attended session whose end was observed `minutes_ago`.

    1h 42m attended (6120 s), observed five minutes ago by default — well inside the
    2 x 900 s horizon."""
    observed = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=minutes_ago)
    ended = observed - timedelta(minutes=15)
    base = {
        "started_at": ended - timedelta(seconds=6120),
        "ended_at": ended,
        "active_seconds": 6120,
        "attended_seconds": 6120,
        "autonomous_seconds": 0,
        "agent_observed_at": observed,
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


def test_a_final_observed_three_hours_ago_is_recorded_as_suppressed_not_sent(client, paired, sent):
    """Backfill must be silent — and the silence must be remembered, or a later
    re-upload of the same session would find no record and announce it after all."""
    uid, headers = paired
    stale = _fresh_final(minutes_ago=180, analysis=SAMPLE_ANALYSIS)
    _upload(client, headers, stale)
    assert sent == []
    assert _recorded(uid) == {stale["client_session_id"]: "suppressed_stale"}

    # Even if a refresh arrives with a fresh observation time.
    fresh_again = {
        **stale,
        "content_hash": uuid.uuid4().hex * 2,
        "agent_observed_at": datetime.now(UTC).isoformat(),
    }
    _upload(client, headers, fresh_again)
    assert sent == []
    assert _recorded(uid) == {stale["client_session_id"]: "suppressed_stale"}


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
