"""index staffing_position for the paged read of GET .../staffing-positions

SC-3-05 (Issue #136), gate-2 review — Reviewer finding R-02, mirrored from `e2c7b04d9a31`'s own
fix for `GET /catalog/rates` (the identical gap on the identical shape of query). The pagination
this task added (`app.data.staffing._staffing_position_page_statement`) orders one scenario's
positions by `(start_date, id)`, and the docstring on that function claimed "an index scan that
stops after limit + offset rows" — a claim nothing in the schema supported: `staffing_position`
carried only the primary key (on `id` alone) and the plain, single-column index on `scenario_id`
(`ForeignKey(..., index=True)`, `app.models.staffing.StaffingPosition.scenario_id`), and neither can
produce `ORDER BY start_date, id` filtered by one `scenario_id` without a full sort of every
position of the scenario first.

This migration adds the only thing that can turn that into a bounded top-N: a btree on
`(scenario_id, start_date, id)`. Leading on `scenario_id` because every read on this path is
filtered by exactly one scenario (`app.data.staffing`), the same reasoning
`app.models.catalog.RATE_PAGE_INDEX` applies with `effective_from` in the lead — there, the table
carries no per-caller scope column to lead on, here it does, and the index reflects the query that
is actually run.

**Expand only.** An index is added, nothing is dropped and nothing is rewritten: code deployed
before this migration keeps working unchanged (it only runs slower, exactly as `GET
.../staffing-positions` already did before SC-3-05 added the sort this index now supports), and
code deployed after it produces identical *results* either way — the index changes the plan, never
the answer.

**Built with a plain `CREATE INDEX`, not `CONCURRENTLY`**, for the same reason `e2c7b04d9a31`
records and with the same expiry: `migrations/env.py` runs every migration inside one transaction,
a concurrent build cannot run inside one, and the factual basis for accepting the `SHARE` lock this
takes is that no deployed environment exists yet (open decision `e2c7b04d9a31` already names) — only
ephemeral test containers and local development databases, where the table is small and no
concurrent writer exists. The first persistent environment with a populated `staffing_position`
needs a concurrent build in a migration of its own, run outside a transaction.

Revision ID: f1a2c4b6d8e0
Revises: a8f18e00172b
Create Date: 2026-09-27
"""

from collections.abc import Sequence

from alembic import op

revision: str = "f1a2c4b6d8e0"
down_revision: str | None = "a8f18e00172b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Spelled here as well as in `app.models.staffing`, and compared with that copy by
# `test_the_model_and_the_migration_agree_on_the_staffing_position_page_index` — the same drift
# guard `e2c7b04d9a31`'s own copy of `RATE_PAGE_INDEX` already has.
_POSITION_TABLE = "staffing_position"
_PAGE_INDEX = "ix_staffing_position_scenario_start_date_id"
_PAGE_INDEX_COLUMNS = ("scenario_id", "start_date", "id")


def upgrade() -> None:
    op.create_index(_PAGE_INDEX, _POSITION_TABLE, list(_PAGE_INDEX_COLUMNS), unique=False)


def downgrade() -> None:
    # Dropping the index restores the schema exactly — and restores the unbounded sort with it. No
    # guard and no data check: unlike a column, an index holds no facts the table does not.
    op.drop_index(_PAGE_INDEX, table_name=_POSITION_TABLE)
