"""The silent half of a completion push: what a tap opens.

`aps.alert` is what the banner shows; `data` is what the phone routes on. The two are
built from the same decision, and this file pins the shape the phone's
`routeForNotification` (mobile/src/push/push.ts) reads: `kind`, `session_id`, `url`.
No database — the payload is a pure function of the decision, and the decision itself is
covered through the sync route in test_notifications.py.
"""

import json

from builder import notify
from builder.routes.push import apns_payload

SID = "0b6d7a1e-2f44-4a4a-9d2e-5d2a5d7c0a11"


def test_push_data_names_the_kind_the_session_and_the_recap_link():
    assert notify.push_data(SID, unattended=False) == {
        "kind": "session_finished",
        "session_id": SID,
        "url": f"builder://session/{SID}?recap=1",
    }
    assert notify.push_data(SID, unattended=True)["kind"] == "agent_run_finished"
    # Same destination for both: the recap decides its own headline from the session.
    assert notify.push_data(SID, unattended=True)["url"] == notify.recap_url(SID)


def test_pending_push_data_agrees_with_its_kind():
    for kind, unattended in (("session_finished", False), ("agent_run_finished", True)):
        p = notify.PendingPush(
            session_id=SID, kind=kind, title="t", body="b", unattended=unattended
        )
        assert p.data["kind"] == kind
        assert p.data["session_id"] == SID
        assert p.data["url"] == notify.recap_url(SID)


def test_recap_url_matches_the_app_scheme_and_route():
    # `mobile/app.config.ts` registers `builder`; the phone routes `/session/[id]` and
    # opens the recap sheet on `?recap=1`. A change to either side must change this.
    assert notify.recap_url(SID) == f"builder://session/{SID}?recap=1"


def test_apns_payload_adds_data_without_changing_what_is_displayed():
    body = apns_payload(
        "Session finished: 1h 42m", "+200 lines · 10 prompts", SID, unattended=False
    )
    assert body["aps"] == {
        "alert": {"title": "Session finished: 1h 42m", "body": "+200 lines · 10 prompts"},
        "sound": "default",
        "thread-id": "session",
        "interruption-level": "active",
    }
    assert body["data"] == notify.push_data(SID, unattended=False)
    # The older keys stay for a phone build that reads them.
    assert body["session"] == SID and body["unattended"] is False
    # Every custom value is a string: APNs delivers JSON and the phone decodes it as such.
    assert all(isinstance(v, str) for v in body["data"].values())
    # Well under the 4 KB APNs ceiling, with room for the longest title compose() writes.
    assert len(json.dumps(body)) < 1024


def test_an_unattended_payload_carries_the_run_kind():
    body = apns_payload("Agent run finished", "ran 3h 05m unattended", SID, unattended=True)
    assert body["data"]["kind"] == "agent_run_finished"
    assert body["unattended"] is True
