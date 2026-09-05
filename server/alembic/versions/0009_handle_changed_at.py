"""users.handle_changed_at: when the handle was last CHANGED, for the once-per-30-days rule.

Revision ID: 0009_handle_changed_at

Nullable, no default, and NOT set by this migration. Null means "never changed", which is
true of every existing row: handles so far were assigned by hand, never renamed through
the API. The route writes it only when a non-null handle becomes a different one — the
first claim of a handle is not a change, so a typo in the first pick can still be fixed.

`users` has no RLS (0003 deliberately left it readable so profiles resolve by handle), so
no policy or helper is needed. `/v1/factions/mine` needs no helper either: `members_visible`
and `factions_visible` in 0007 both go through the SECURITY DEFINER `is_faction_member`,
so a member's own membership rows and the faction rows behind them are visible to
builder_app under the viewer's own id. Verified by test_users.py, which checks a
non-member's factions are NOT in the list, reading past the route with the viewer set.
"""

from alembic import op

revision = "0009_handle_changed_at"
down_revision = "0008_session_notifications"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE users ADD COLUMN handle_changed_at timestamptz;")


def downgrade() -> None:
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS handle_changed_at;")
