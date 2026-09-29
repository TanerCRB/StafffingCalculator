"""add surcharge_percent and includes_surcharge to catalog_default_rates and its snapshot

SC-5-02 (Issue #77, F-07): "Separate personnel surcharges from the base rate, calculate the fully
loaded cost" — the first schema change of the fully loaded personnel cost. Two new columns, on the
same row as `default_cost_rate`, on both the live rate table and its approval snapshot (ADR-0013,
addendum 2026-09-25 SC-5-02, Q4/Q5; ADR-0005, same date; ADR-0004, same date).

**Expand only.** Both columns are added `NOT NULL` with a default that makes every existing row a
legal, meaningful row rather than an unanswered question: `0` surcharge and "does not already
include one" is exactly what a tuple priced before this task existed actually was. No previously
deployed code reads either column, so nothing written before this migration needs to change, and
nothing this migration adds requires a second, contracting migration to follow it.

**`catalog_default_rates` keeps its default; the snapshot table's is dropped after the backfill.**
The live table's default stays because `POST /catalog/rates` may not always send an explicit value
(the request schema's own default), exactly like `unit`'s `server_default`
(`d5e94a1c6b73`/`app.models.catalog`). The snapshot table's rule is stricter, and older: "no default
on any of them: a snapshot column with a default is a column that can be written without a value
having been read from the source" (`app.models.approved_snapshot`, module docstring) — the same
shape `a7c2e5f81b94` used for `approved_snapshot_absence_type.is_statutory_leave`. The default is
present only long enough to give scenarios approved before this migration the honest value for
them (no surcharge concept existed at the moment of their approval), then dropped so every future
`INSERT … SELECT` (`app.data.scenario_approval._copy_catalog_default_rates`) must name the column
explicitly.

**Why a CHECK on the live table and not on the snapshot.** `surcharge_percent >= 0` is a rule about
*input* — a negative surcharge is a sign error, not a discount (the same reasoning `RateAmount`/
`BudgetDays` give their own non-negative rules). The snapshot never re-judges what was approved
(module docstring, "No CHECK constraints are repeated here"), so it does not gain one.

**Read back by the existing reader, symmetrically with `default_cost_rate`.** An earlier version of
this migration's own docstring said SC-5-02 "does not use the snapshot column in any query" —
corrected during gate 2 review (Guardian S-01/Reviewer R-01, 2026-09-25): `app.data.personnel_cost.
costed_month_windows`'s approval-snapshot branch **does** select both columns, off
`ApprovedSnapshotCatalogDefaultRate`, exactly as it already selects `default_cost_rate`/`currency`
off the same table. That is the *existing* reader of this table for the base cost (SC-5-01), not a
new one — completing the freeze this migration performs, not building new scope.

**Consequence for `downgrade()`, named because of the above.** Because a live query now references
`surcharge_percent`/`includes_surcharge` on both tables, downgrading this migration while the
application is deployed does not quietly revert to pre-SC-5-02 behaviour — it breaks every
personnel-cost read for every scenario (draft and approved alike) with a `column … does not exist`
error, because `costed_month_windows` unconditionally selects the two columns it no longer has. A
downgrade of this revision must be paired with a deployment of the pre-SC-5-02 application code, not
run against the current one.

Revision ID: 9b3f6a1d0c47
Revises: b9e3c7a1f264
Create Date: 2026-09-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9b3f6a1d0c47"
down_revision: str | None = "b9e3c7a1f264"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Spelled here as well as in `app.models.catalog`/`app.models.organization_defaults` rather than
# imported from them — the same reason as every other migration touching this table
# (`d5e94a1c6b73`, `a7c2e5f81b94`): a migration keeps describing the schema it produced even after
# the model moves on.
_PERCENT_PRECISION = 6
_PERCENT_SCALE = 3

_LOCK_TIMEOUT = "3s"
"""The bound `d5e94a1c6b73` established for an `ALTER TABLE` on the catalogue (R-06). Both `ALTER`s
below are metadata-only with a constant default, so each finishes in microseconds once it holds the
lock — the risk this bounds is the wait, not the work."""


def upgrade() -> None:
    op.execute(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT}'")
    op.add_column(
        "catalog_default_rates",
        sa.Column(
            "surcharge_percent",
            sa.Numeric(precision=_PERCENT_PRECISION, scale=_PERCENT_SCALE),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )
    op.add_column(
        "catalog_default_rates",
        sa.Column(
            "includes_surcharge",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        op.f("ck_catalog_default_rates_surcharge_percent_not_negative"),
        "catalog_default_rates",
        "surcharge_percent >= 0",
    )
    op.execute("SET LOCAL lock_timeout = DEFAULT")

    # Matching columns on the approval snapshot (ADR-0004, addendum 2026-09-25 SC-5-02, point 2):
    # `NOT NULL DEFAULT` for the length of this migration only, so a scenario approved before this
    # revision gets the honest backfilled value (`0`/`false`), then the default is dropped so the
    # copier must always name the column explicitly (module docstring of
    # `app.models.approved_snapshot`, "no default on any of them").
    op.execute(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT}'")
    op.add_column(
        "approved_snapshot_catalog_default_rate",
        sa.Column(
            "surcharge_percent",
            sa.Numeric(precision=_PERCENT_PRECISION, scale=_PERCENT_SCALE),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )
    op.add_column(
        "approved_snapshot_catalog_default_rate",
        sa.Column(
            "includes_surcharge",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.alter_column(
        "approved_snapshot_catalog_default_rate", "surcharge_percent", server_default=None
    )
    op.alter_column(
        "approved_snapshot_catalog_default_rate", "includes_surcharge", server_default=None
    )
    op.execute("SET LOCAL lock_timeout = DEFAULT")


def downgrade() -> None:
    # No guard here of the kind `c1a4f7b92e05`'s downgrade needs — dropping either column loses no
    # data anybody entered through a path that could misinterpret it (there is no vendor-style
    # reinterpretation risk here, only an absence of the figure).
    #
    # It is **not**, however, a quiet revert to pre-SC-5-02 behaviour while the application keeps
    # running: `app.data.personnel_cost.costed_month_windows` unconditionally selects both columns
    # off both tables (the module docstring above explains why, corrected at gate 2 review). Running
    # this downgrade against a database a current deployment still queries turns every
    # personnel-cost read, draft or approved, into a `column … does not exist` error — this
    # downgrade is only safe paired with a deployment of the code version that predates this
    # migration.
    op.execute(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT}'")
    op.drop_column("approved_snapshot_catalog_default_rate", "includes_surcharge")
    op.drop_column("approved_snapshot_catalog_default_rate", "surcharge_percent")
    op.execute("SET LOCAL lock_timeout = DEFAULT")

    op.execute(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT}'")
    # No explicit `op.drop_constraint` for the CHECK: PostgreSQL drops a CHECK constraint
    # automatically when the column it references is dropped (verified against a live database —
    # no `CASCADE` needed, unlike a dependent view). A separate `ALTER TABLE catalog_default_rates
    # DROP CONSTRAINT …` statement here would collide, by exact text prefix, with
    # `tests/test_catalog_migration_reversibility.py`'s interleaving hook for a *different*
    # migration's own constraint drop (`c1a4f7b92e05`, its `EXCLUDE`-restore step) — that hook fires
    # once, on the first statement matching `"alter table catalog_default_rates drop constraint"`,
    # wherever it occurs in the downgrade chain, not only in the migration it was written for.
    op.drop_column("catalog_default_rates", "includes_surcharge")
    op.drop_column("catalog_default_rates", "surcharge_percent")
    op.execute("SET LOCAL lock_timeout = DEFAULT")
