"""harness enum: add 'opencode' and 'aider'.

Revision ID: 0015_harness_opencode_aider

`python -m capture sync` now discovers and uploads sessions from every transcript store on
the machine, not only `~/.claude/projects` (capture/harnesses.py), so two harness values
the Postgres type has never seen arrive on the wire.

FOUND BY RUNNING IT. Adding the two names to `privacy/upload-contract.json` regenerates the
Pydantic model, the Swift enum and the TypeScript union, and every one of those accepted an
`aider` payload happily. The database did not: `invalid input value for enum harness:
"aider"` on the INSERT, a 500 from `/v1/sync` with the payload already validated and the
client told nothing useful. The contract generator cannot see the enum TYPE, so a contract
change that adds an enum value is ALWAYS also a migration.

`ALTER TYPE ... ADD VALUE` is the 0010 dance again: PG12+ runs it inside a transaction but
refuses to USE the new value until that transaction commits, so it goes out in its own
autocommit window. `IF NOT EXISTS` makes a re-run a no-op rather than a 42710.

The downgrade is a no-op, for the reason 0010 gives: Postgres cannot DROP VALUE, and the
type swap that would remove one has to delete every row labelled with it first. Destroying
a person's Aider sessions to satisfy a schema rollback is not a trade.
"""

from alembic import op

revision = "0015_harness_opencode_aider"
down_revision = "0014_transcript_chunks"
branch_labels = None
depends_on = None

NEW_VALUES = ("opencode", "aider")


def upgrade() -> None:
    with op.get_context().autocommit_block():
        for value in NEW_VALUES:
            op.execute(f"ALTER TYPE harness ADD VALUE IF NOT EXISTS '{value}'")


def downgrade() -> None:
    # See the docstring and 0010: enum values cannot be dropped in place, and the swap that
    # would remove them requires deleting every session labelled opencode or aider first.
    pass
