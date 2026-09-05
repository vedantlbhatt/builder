"""harness enum: add 'gemini_cli' and 'cline'.

Revision ID: 0010_harness_values

The contract (privacy/upload-contract.json, v2 unchanged) now lists six harness values; the
Postgres `harness` type from 0001 knows four. A client whose upload carries `gemini_cli`
against the old type is a hard 22P02 on the INSERT — the same failure 0001's comment on
`cursor_ide` describes — so the enum grows here, one value per statement.

`ALTER TYPE ... ADD VALUE` is the 0006 dance again: PG12+ runs it inside a transaction but
refuses to USE the new value until that transaction commits, so it goes out in its own
autocommit window. Nothing in this migration uses the values, but the next migration that
does must not have to know that. `IF NOT EXISTS` makes a re-run a no-op rather than a
42710.

The downgrade is deliberately a no-op. Postgres cannot DROP VALUE from an enum; the only
way to remove one is 0006's swap (rename the type, create the old one, retype every column
that uses it — `sessions.harness` — after dropping any policy that mentions the column, then
put the policy back). That rewrite would have to DELETE or relabel every gemini_cli / cline
session first, i.e. destroy real rows to satisfy a schema rollback. An unused extra enum
value costs nothing and breaks nothing that 0009 could run against, so downgrading leaves
the two labels in place and says so.
"""

from alembic import op

revision = "0010_harness_values"
down_revision = "0009_handle_changed_at"
branch_labels = None
depends_on = None

NEW_VALUES = ("gemini_cli", "cline")


def upgrade() -> None:
    # See the docstring: each ADD VALUE commits on its own, or a later statement in the
    # same transaction that touches the value fails with "unsafe use of new value".
    with op.get_context().autocommit_block():
        for value in NEW_VALUES:
            op.execute(f"ALTER TYPE harness ADD VALUE IF NOT EXISTS '{value}'")


def downgrade() -> None:
    # Enum values cannot be dropped in place (no ALTER TYPE ... DROP VALUE in Postgres).
    # Removing them means retyping sessions.harness through a fresh enum, which requires
    # first deleting every row labelled gemini_cli or cline. Leaving the values is safe:
    # nothing at 0009 selects on them, and an extra label in the type is inert.
    pass
