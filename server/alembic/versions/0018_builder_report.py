"""builder_report: the measured half of the profile, one row per person.

Revision ID: 0018_builder_report

0016 added the profile's prose. This adds the profile's NUMBERS — the ones the server
cannot compute, which is most of the interesting ones. `corpus_metrics` already reads the
stored sessions and gets totals, prompt shape and the archetype out of them. What it
cannot get, at all, from what the contract allows on the wire:

  trends         needs the metrics recomputed over two windows, which it could do, and is
                 kept here beside the rest so one document describes one moment.
  agents         needs SUBAGENT SIDECAR TRANSCRIPTS. They are files on the machine; not
                 one byte of them is uploadable, and no other tool on that machine reads
                 them either.
  quality        needs SHELL COMMAND TEXT, to know a `pytest` from a `git status`. The
                 server has a count of Bash calls and no commands.
  prompting      needs PROMPT TEXT. Never leaves the machine, so only the two counts do.
  contributions  needs commit TIMES against session windows. The server has a commit
                 count per session and no timestamps.

So the document is computed where the transcripts are, by `python -m analysis report`,
uploaded by `python -m capture report`, validated against report_spec.py — generated from
spec/report.v1.json, `extra='forbid'`, every string bounded — and stored.

WHY NOT COLUMNS. The same reason 0006 and 0016 give: the phone renders it whole and the
spec version, not the table, defines its shape. `report_version` is lifted out because a
reader that has to migrate old documents needs to find them with a WHERE clause.

ONE ROW PER USER, upserted, no history. A report describes a corpus as it stands.

RLS in the shape of 0016: ENABLE + FORCE, owner-only, no public policy. These are numbers
about a person, not about a session, and sharing a session shares a session.
"""

from alembic import op

revision = "0018_builder_report"
down_revision = "0017_build_posts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE builder_report (
          user_id        uuid PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
          -- == spec version. A retuned threshold is a recompute, not a migration.
          report_version integer NOT NULL,
          generated_at   timestamptz NOT NULL,
          -- The window every block looked back over, so the phone can say "30 days"
          -- rather than implying the numbers are all of history.
          window_days    integer NOT NULL,
          body           jsonb NOT NULL,
          created_at     timestamptz NOT NULL DEFAULT now(),
          updated_at     timestamptz NOT NULL DEFAULT now()
        );

        GRANT SELECT, INSERT, UPDATE, DELETE ON builder_report TO builder_app, builder_worker;

        ALTER TABLE builder_report ENABLE ROW LEVEL SECURITY;
        ALTER TABLE builder_report FORCE  ROW LEVEL SECURITY;

        CREATE POLICY builder_report_owner ON builder_report
          USING (user_id = NULLIF(current_setting('app.viewer_id', true), '')::uuid)
          WITH CHECK (user_id = NULLIF(current_setting('app.viewer_id', true), '')::uuid);
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP POLICY IF EXISTS builder_report_owner ON builder_report;
        DROP TABLE IF EXISTS builder_report;
        """
    )
