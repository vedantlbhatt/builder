"""sessions.end_reason: admit 'cleared' and 'switched_repo' (boundaries v3).

Revision ID: 0012_end_reasons_v3

`end_reason` is text with a CHECK, by 0006's design ("adding a reason later must not need
another autocommit dance, and the legal set is the contract's to define"). This is that
later. The contract (privacy/upload-contract.json) now lists six reasons; the two new ones
are structural ends from docs/session-boundaries.md v3:

* `cleared`        — the human typed `/clear`. The conversation was ended on purpose.
* `switched_repo`  — the human opened a new session in a different repository at least
                     120 s after this one's last record. The sitting moved.

Both mean the work in that session STOPPED, so `notify.NOTIFYING_END_REASONS` admits them
alongside `idle_gap`; a client on the old contract keeps sending the four it knows.

The downgrade restores 0006's four-value CHECK. Rows carrying a v3 reason would violate
it, so they are relabelled `idle_gap` first — the nearest older truth: both v3 ends are
announced and finalized exactly as an idle end is, and differ from it in name only. A
schema rollback should not delete real work.
"""

from alembic import op

revision = "0012_end_reasons_v3"
down_revision = "0011_capture_keys"
branch_labels = None
depends_on = None

V2 = ("idle_gap", "human_returned", "day_boundary", "still_running")
V3 = V2 + ("cleared", "switched_repo")


def _check(values: tuple[str, ...]) -> str:
    return "CHECK (end_reason IN (" + ", ".join(f"'{v}'" for v in values) + "))"


def upgrade() -> None:
    # `sessions_end_reason_check` is the name Postgres gave 0006's inline column CHECK.
    op.execute("ALTER TABLE sessions DROP CONSTRAINT IF EXISTS sessions_end_reason_check")
    op.execute(f"ALTER TABLE sessions ADD CONSTRAINT sessions_end_reason_check {_check(V3)}")


def downgrade() -> None:
    op.execute(
        "UPDATE sessions SET end_reason = 'idle_gap' "
        "WHERE end_reason IN ('cleared', 'switched_repo')"
    )
    op.execute("ALTER TABLE sessions DROP CONSTRAINT IF EXISTS sessions_end_reason_check")
    op.execute(f"ALTER TABLE sessions ADD CONSTRAINT sessions_end_reason_check {_check(V2)}")
