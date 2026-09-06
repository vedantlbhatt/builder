"""session_stats.feedback: what one sitting cost, for the card you read once.

Revision ID: 0019_session_feedback

Contract v3 adds one field, `feedback`, and this is where it lands. It sits on
`session_stats` rather than in a table of its own because it is exactly what that table
already holds: per-session numbers the client measured, upserted with the rest of them.

WHAT IT IS. Up to three notes, each `{id, seconds, count}`, from a fixed list of three:
the agent ran a long way with nothing written, tested or committed; the same thing failed
several times in a row; one file was rewritten over and over. The SENTENCE is not stored,
because it is not uploaded — the client writes it from the id, so rewording a note is a
client release and not a re-upload of everybody's history.

WHAT IT IS NOT. It is not a second `analysis`, and it needed no privacy exception. The
local version of these notes names the failing COMMAND and the FILE that was rewritten;
both are on privacy/upload-contract.json's never-list and neither is on the wire. What
arrives here is an enum and two integers.

COALESCE ON UPDATE, like `analysis` and for the same reason. A client that does not
compute feedback — the Mac today, or a container where `analysis/` is not deployed —
sends nothing, and nothing must not mean "delete what another client measured". A final
session's events do not change, so a note that is already stored is still true.
"""

from alembic import op

revision = "0019_session_feedback"
down_revision = "0018_builder_report"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE session_stats
          ADD COLUMN feedback jsonb;

        COMMENT ON COLUMN session_stats.feedback IS
          'contract v3: [{id, seconds, count}], at most three. No prose, no command, no '
          'file name. NULL means the sitting had nothing worth saying OR the client does '
          'not compute it; both render as no notes.';
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE session_stats DROP COLUMN IF EXISTS feedback;")
