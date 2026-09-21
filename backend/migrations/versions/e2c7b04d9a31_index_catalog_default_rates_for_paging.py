"""index catalog_default_rates for the paged read of GET /catalog/rates

R-01 (reviewer, gate 2 on Issue #46, 2026-09-21). SC-2-03 gave `GET /catalog/rates` a `LIMIT`, and
the `LIMIT` bounded the wrong thing: the *response* shrank, the work did not. The page is ordered by
`(effective_from DESC, id DESC)` and this table had no index that could produce that order — only
the primary key (on `id` alone) and the gist index behind the `EXCLUDE` constraint, which leads on
the four dimension columns and the vendor expression. PostgreSQL therefore sorted every filtered row
before the `LIMIT` could cut, so a 250k-row catalogue paid for 250k rows to answer with 2000.

This migration adds the only thing that can turn that into a bounded top-N: a btree on
`(effective_from, id)`. Ascending, although the page is read descending — a btree is scannable in
both directions, so one index serves both and two would be two objects maintaining one ordering
(`app.models.catalog.RATE_PAGE_INDEX`).

**Expand only.** An index is added, nothing is dropped and nothing is rewritten: code deployed
before this migration keeps working unchanged (it only runs slower), and code deployed after it
produces identical *results* either way — the index changes the plan, never the answer. This is the
ADR-0001 shape the previous catalogue migration (`c1a4f7b92e05`) had to deviate from, and here there
is nothing to deviate about.

**Built with a plain `CREATE INDEX`, not `CONCURRENTLY`**, and that is a decision with an expiry
date rather than an oversight. A concurrent build cannot run inside a transaction, and
`migrations/env.py` runs every migration in one; a plain build takes a `SHARE` lock, which blocks
writes to `catalog_default_rates` for the duration. The factual basis for accepting that is the same
one `c1a4f7b92e05` records: **no deployed environment exists** (open decision #5) — the only
databases are ephemeral test containers and local development ones, where the table is small and no
concurrent writer exists. The first persistent environment with a populated catalogue needs a
concurrent build in a migration of its own, run outside a transaction.

Revision ID: e2c7b04d9a31
Revises: c1a4f7b92e05
Create Date: 2026-09-21
"""

from collections.abc import Sequence

from alembic import op

revision: str = "e2c7b04d9a31"
down_revision: str | None = "c1a4f7b92e05"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Spelled here as well as in `app.models.catalog`, and compared with that copy by
# `test_the_model_and_the_migration_agree_on_every_sql_expression` — same reason as in the two
# migrations before this one: a migration must keep describing the schema it produced even after the
# model moves on, so it cannot import a constant the model is free to change.
_RATE_TABLE = "catalog_default_rates"
_PAGE_INDEX = "ix_catalog_default_rates_effective_from_id"
_PAGE_INDEX_COLUMNS = ("effective_from", "id")


def upgrade() -> None:
    op.create_index(_PAGE_INDEX, _RATE_TABLE, list(_PAGE_INDEX_COLUMNS), unique=False)


def downgrade() -> None:
    # Dropping the index restores the schema exactly — and restores the unbounded sort with it. No
    # guard and no data check: unlike `c1a4f7b92e05`'s downgrade, nothing here can reinterpret a
    # row, because an index holds no facts the table does not.
    op.drop_index(_PAGE_INDEX, table_name=_RATE_TABLE)
