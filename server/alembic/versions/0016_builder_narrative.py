"""builder_narrative: the "how you work" page, one row per person.

Revision ID: 0016_builder_narrative

The profile already had two halves and both of them were numbers. `corpus_metrics` says
5.75 test runs an hour and the rules say `quality_guardian`, and a person reading that
learns a label, not what it means about them. This table holds the third half: prose,
written from those same measurements, that says what they add up to.

WHY THE SERVER CANNOT WRITE IT. The narrative rests on comparative findings computed from
PROMPT TEXT and the events around it (analysis/patterns.py), and prompt text never leaves
the machine (privacy/upload-contract.json). So the document is produced where the
transcripts are, by the user's own `claude`, and arrives here already written. The server
validates it against narrative_spec.py, which is generated from spec/narrative.v1.json
alongside the schema the model was constrained by, and stores it.

It is the same deliberate exception the contract's `analysis` field is, and it is narrower:
no file names, no paths, no excerpts, no verbatim prompts. The one thing it carries that
`analysis` does not is a claim about the PERSON, which is why every string in it is bounded
and why `invented_numbers_dropped` is stored beside the body. A narrative that needed the
number check to fire is a narrative worth being able to find later.

ONE ROW PER USER, keyed on user_id, upserted. There is no history: a narrative describes
the corpus as it stands, and a stale one is not evidence of anything.

RLS ENABLE + FORCE with an owner-only policy in the shape of `capture_keys` (0011); there
is deliberately NO public policy, because a shared session is a session and this is a
statement about a person. `builder_app` gets full DML on it: unlike 0007 and 0011 there is
no column here the app is not allowed to replace, since the whole point of an upsert is
that every column is rewritten together.
"""

from alembic import op

revision = "0016_builder_narrative"
down_revision = "0015_harness_opencode_aider"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE builder_narrative (
          user_id           uuid PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
          -- == spec version. A retuned prompt is a recompute, not a migration.
          narrative_version integer NOT NULL,
          model             text,
          generated_at      timestamptz NOT NULL,
          -- How many claims the number check took back off the model before this was
          -- stored (analysis/narrative.py). Zero is the normal case; a row where it is not
          -- zero is one to go and read.
          invented_numbers_dropped integer NOT NULL DEFAULT 0,
          -- The whole document, validated field by field against narrative_spec.py on the
          -- way in (extra='forbid', every string bounded). One jsonb rather than a column
          -- per paragraph, for the reason 0006 gives: the phone renders it whole and the
          -- spec version, not the table, defines its shape.
          body              jsonb NOT NULL,
          created_at        timestamptz NOT NULL DEFAULT now(),
          updated_at        timestamptz NOT NULL DEFAULT now()
        );

        GRANT SELECT, INSERT, UPDATE, DELETE ON builder_narrative TO builder_app, builder_worker;

        ALTER TABLE builder_narrative ENABLE ROW LEVEL SECURITY;
        ALTER TABLE builder_narrative FORCE  ROW LEVEL SECURITY;

        -- Owner only. No public policy on purpose: sharing a session shares a session.
        CREATE POLICY builder_narrative_owner ON builder_narrative
          USING (user_id = NULLIF(current_setting('app.viewer_id', true), '')::uuid)
          WITH CHECK (user_id = NULLIF(current_setting('app.viewer_id', true), '')::uuid);
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP POLICY IF EXISTS builder_narrative_owner ON builder_narrative;
        DROP TABLE IF EXISTS builder_narrative;
        """
    )
