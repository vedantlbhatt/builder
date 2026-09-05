"""Deciding whether a finished session is news, and what the banner says.

docs/session-boundaries.md fixes the policy; this module is its server-side reading,
evaluated at upload time because the server learns that a session ended only when the
Mac's final payload arrives. Four rules, in the order they are checked:

1. Only a transition to `final` is news. A re-upload of a session that was already final
   (the content hash moved because a title arrived, an analysis was revised, a boundary
   was retuned) changes numbers, not the fact that the work stopped.
2. `human_returned` and `day_boundary` never notify — you are already here, or the work
   continues. Only `idle_gap` means the work stopped.
3. Unattended runs fire "Agent run finished"; attended sessions fire "Session finished"
   only when notable. Unattended is checked first: the Mac's `notable` folded in
   `!unattended` for a long time, and the whole point of the second title is that an
   overnight run is worth looking at even though it can never be a record.
4. Backfill must be silent. First launch finalizes the whole history at once, and an
   alert is only meaningful if it is news, so any session that ENDED more than
   `NOTIFY_HORIZON_SEC` ago is RECORDED as notified (`suppressed_stale`) and not sent.
   The horizon is measured from the session's own `ended_at`, never from
   `agent_observed_at`: the Mac stamps that field with `Date()` when it BUILDS the payload
   (SyncCommand.swift), so on first pairing — or after any server reset — every historical
   final arrives with `existing=None` and an observation age of seconds. Anchored there,
   the horizon is unreachable by real traffic and the whole history is announced. That is
   the exact failure this rule exists to prevent, and `SessionLifecycle.staleNotification
   Seconds` on the Mac anchors on `endedAt` for the same reason.

The decision and its record happen inside the upload transaction; the send happens after
it commits (routes/sync.py). A push failure can therefore never roll back an upload, and
a crash between commit and send loses a banner rather than doubling one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import text

from .contract import SessionUpload

#: Mirrors `Tuning.tauSessionSec` (900 s), the idle gap that ends 99.8% of sessions.
TAU_SESSION_SEC = 900

#: Mirrors `Daemon.Config.tickSeconds` (30 s): the lifecycle tick that notices a gap has
#: crossed `tauSessionSec` and finalizes the session.
DAEMON_TICK_SEC = 30

#: Mirrors `Tuning.liveUploadMinIntervalSec` (60 s), "one tick of the daemon, which is the
#: finest cadence anything upstream changes at" — the most a sync pass trails a finalize.
SYNC_PASS_SEC = 60

#: How long after a session ENDS its final payload arrives, when everything is on time:
#: the gap has to reach `tauSessionSec` before the lifecycle can call it over, the next
#: daemon tick finalizes it, and the next sync pass uploads it.
#:
#:     900 + 30 + 60 = 990 s  ->  "about 1000 s after ended_at"
EXPECTED_FINAL_LAG_SEC = TAU_SESSION_SEC + DAEMON_TICK_SEC + SYNC_PASS_SEC

#: "Anything older than twice the session threshold is recorded as notified without
#: being delivered." Mirrors `SessionLifecycle.staleNotificationSeconds`
#: (`Tuning.tauSessionSec * 2`) and, like it, is measured from the session's END: a
#: genuine transition lands ~990 s after `ended_at`, so 1800 s leaves ~13 minutes for a
#: late tick or a slow sync, while a first-run backfill is hours to years behind it.
NOTIFY_HORIZON_SEC = 2 * TAU_SESSION_SEC
assert NOTIFY_HORIZON_SEC > EXPECTED_FINAL_LAG_SEC, "an on-time final must clear the horizon"

#: The ends that mean the work stopped. Everything else is a cut, not a stop.
NOTIFYING_END_REASONS = frozenset({"idle_gap"})

#: The URL scheme the phone registers (`mobile/app.config.ts` → `scheme`). The recap
#: link is the same string whether it rides in a push, is typed on the Mac, or comes out
#: of a share sheet, so `mobile/app/+native-intent.ts` has exactly one shape to route.
APP_SCHEME = "builder"

#: The two kinds a push can carry, keyed by what `PendingPush.unattended` says. The phone's
#: `routeForNotification` opens both on the recap; the names only decide the headline.
KIND_SESSION_FINISHED = "session_finished"
KIND_AGENT_RUN_FINISHED = "agent_run_finished"


def recap_url(session_id: str) -> str:
    """`builder://session/<id>?recap=1` — the recap sheet over the session detail."""
    return f"{APP_SCHEME}://session/{session_id}?recap=1"


def push_data(session_id: str, *, unattended: bool) -> dict[str, str]:
    """The silent half of a push: what the phone opens when the banner is tapped.

    Lives beside the alert text rather than in it because APNs shows `aps.alert` and
    hands everything else to the app untouched. `kind` names the banner class so the
    phone can tell a run from a session without re-deriving it; `session_id` is what the
    router needs; `url` is the same destination as a deep link, for a client that would
    rather open a URL than build a route. All three are strings — APNs custom keys survive
    the round trip as JSON, and a bare string is the one shape every client decodes alike.
    """
    kind = KIND_AGENT_RUN_FINISHED if unattended else KIND_SESSION_FINISHED
    return {"kind": kind, "session_id": session_id, "url": recap_url(session_id)}


@dataclass(frozen=True)
class PendingPush:
    session_id: str
    kind: str
    title: str
    body: str
    unattended: bool

    @property
    def data(self) -> dict[str, str]:
        """`push_data` for this push. `kind` here and in the data agree by construction:
        both are read off `unattended`, which `plan` set from the kind it chose."""
        return push_data(self.session_id, unattended=self.unattended)


def plan(db, session_id, p: SessionUpload, existing, now: datetime | None = None):
    """Decide, and record the decision, for one upserted session.

    Returns the push to send after commit, or None. `existing` is the row the upsert
    replaced (`state` is what matters) or None for a first sighting. The record is written
    here, before anything is sent, so that whatever happens to the request afterwards the
    session is never announced twice.
    """
    if p.state != "final":
        return None
    if existing is not None and existing.state == "final":
        return None
    if p.end_reason not in NOTIFYING_END_REASONS:
        return None

    if p.unattended:
        kind = KIND_AGENT_RUN_FINISHED
    elif p.notable:
        kind = KIND_SESSION_FINISHED
    else:
        return None

    # Belt to the transition check's braces: a row here means this session was decided
    # once already, whatever the sessions row says about itself.
    already = db.execute(
        text("SELECT kind FROM session_notifications WHERE session_id = :s"),
        {"s": str(session_id)},
    ).first()
    if already is not None:
        return None

    # The session's own clock, not the payload's. `agent_observed_at` is always "now" on
    # a shipped client (see the module docstring), so it cannot tell a backfill from a
    # transition; `ended_at` can, and it is the same clock the Mac's lifecycle uses.
    age = ((now or datetime.now(UTC)) - p.ended_at).total_seconds()
    if age > NOTIFY_HORIZON_SEC:
        _record(db, session_id, "suppressed_stale")
        return None

    title, body = compose(p, kind)
    _record(db, session_id, kind)
    return PendingPush(
        session_id=str(session_id),
        kind=kind,
        title=title,
        body=body,
        unattended=kind == KIND_AGENT_RUN_FINISHED,
    )


def compose(p: SessionUpload, kind: str) -> tuple[str, str]:
    """The two banners from docs/session-boundaries.md.

    An attended session is described by its ATTENDED time — the same clock that decides
    records — so a kickoff prompt plus eight autonomous hours reads as the minutes the
    person actually sat there. An unattended run has no attended time and is described by
    its active time. The repo is named only when the payload named it, which the client
    does only for public repos; an anonymous repo is "a private repo".
    """
    if kind == KIND_AGENT_RUN_FINISHED:
        return "Agent run finished", f"ran {_hm(p.active_seconds)} unattended"

    where = ""
    if p.repo_hash is not None:
        where = f" in {p.repo_name}" if p.repo_name else " in a private repo"
    title = f"Session finished: {_hm(p.attended_seconds)}{where}"
    body = f"+{_plural(p.lines_added_agent, 'line')} · {_plural(p.human_prompt_count, 'prompt')}"
    if p.analysis is not None:
        body += " · analysis ready"
    return title, body


def _record(db, session_id, kind: str) -> None:
    db.execute(
        text(
            """
            INSERT INTO session_notifications (session_id, kind) VALUES (:s, :k)
            ON CONFLICT (session_id) DO NOTHING
            """
        ),
        {"s": str(session_id), "k": kind},
    )


def _hm(seconds: int) -> str:
    """1h 42m, 3h 05m, 42m. Minutes are zero-padded only after an hour figure."""
    h, m = divmod(max(seconds, 0) // 60, 60)
    return f"{h}h {m:02d}m" if h else f"{m}m"


def _plural(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"
