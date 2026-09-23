"""create the absence budget, the statutory-leave flag and the fourth snapshot table

Expand only (ADR-0001, expand → deploy → contract): two new tables, **two new non-nullable columns
with a default** on existing tables, and one new index. Nothing is dropped, nothing changes meaning,
so the code version deployed before this migration keeps working against the new schema and there is
no contract phase to pair with it.

**The two columns added to existing tables are `absence_type.is_statutory_leave` and
`approved_snapshot_absence_type.is_statutory_leave`**, both `NOT NULL` with `DEFAULT false`.
Backward compatible in both directions: existing rows get `false` without a backfill (PostgreSQL 11+
stores the default in the catalogue rather than rewriting the table), and the previous code version
neither reads nor writes either of them. `false` is also the honest value in both places — "this
type is not the one the budget settles against", and for an approval frozen before this migration,
"no type carried the flag then, because there was no flag". The snapshot column keeps the default
only for the length of this migration; see the comment at the `ALTER` for why it is stripped
afterwards.

**What the database enforces here, and why each rule is in the schema rather than only in a request
schema** (a fixture, a seed script, a future import or a second endpoint never sees a Pydantic
model):

1. `EXCLUDE USING gist ((calendar_id, engagement_type_id) =, valid_period &&)` on `absence_budget` —
   one pair has at most one budget at a time, including against a competing connection (criterion
   K-01). The fourth consumer of ADR-0008's pattern and the second table in this repository that
   actually builds it, so it is the same shape as `catalog_default_rates` and not a variant.
2. `CHECK source ~ '[^[:space:]]'` plus `NOT NULL` — a number with no named source is refused by the
   database, not only by the request schema (criterion K-02).
3. `CHECK effective_to IS NOT NULL` — the **one** named narrowing of the pattern this table gets
   (ADR-0008, addendum 2026-09-22 SC-3-03, point 10b): the monthly proration divides by the number
   of months of the window, and an open-ended window has no denominator.
4. `CHECK` that the window begins on the first of a month and ends on the last day of one — what
   makes "the months of the window" a count rather than an interpretation, and the proration's sum
   invariant exact by construction (point 10a).
5. A **partial unique index** on `absence_type ((true)) WHERE is_statutory_leave` — at most one
   absence type may be the one budgets settle against, refused in the statement that inserts the
   second one (point 8a). The application-side equivalent is rejected there by name: check-then-act
   has survived delivered tests three times in this repository.
6. Every foreign key is `NO ACTION` (no `ondelete` clause anywhere here). Towards `scenarios` that
   matters twice over: `ON DELETE CASCADE` would be a way for the snapshot of an `approved` scenario
   to disappear past the write guard, which covers `INSERT`/`DELETE` and not a cascade.

**What is deliberately not here:**

- **No `location_id` in the budget key** (ADR-0008, addendum SC-3-03, point 3b). Locations sharing a
  calendar share its budget; the first request for two budgets under one calendar is a rebuild of
  the `EXCLUDE` constraint and needs its own dated entry.
- **No `absence_type_id` on the budget row.** The type a budget settles against is named by the flag
  above, so the key stays two columns (point 8).
- **No `updated_at` on `approved_snapshot_absence_budget`** — written once inside the approval
  transaction, so there are no two editors for a marker to arbitrate between (ADR-0007, addendum
  2026-09-22, point 5).
- **No column carrying a prorated or otherwise computed figure in the snapshot** (ADR-0004, addendum
  SC-3-03, point 7): the proration is the reader's formula, and criterion K-07 asserts the absence
  over the whole set of columns.
- **No `CREATE EXTENSION btree_gist`** — migration `7b3d5c81e40a` created it for the rate table and
  this is the second table depending on it (ADR-0008, addendum SC-3-03, point 11). The dependency is
  therefore wider than it was, and still unproven on any target environment (open decision #5).

Revision ID: a7c2e5f81b94
Revises: f3a1d0c58b27
Create Date: 2026-09-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a7c2e5f81b94"
down_revision: str | None = "f3a1d0c58b27"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Spelled here as well as in `app.models.catalog` rather than imported from it: a migration must
# keep describing the schema it produced even after the model moves on, and an import would silently
# rewrite history the next time somebody edits the model. What keeps the copies honest is
# `test_the_model_and_the_migration_agree_on_every_sql_expression_of_this_task` in
# `tests/test_absence_budget_schema_constraints.py` — the drift guard R-02.
_VALID_PERIOD_EXPRESSION = "daterange(effective_from, (effective_to + 1), '[)')"
"""The same expression `catalog_default_rates` generates its window with (migration
`7b3d5c81e40a`). Identical on purpose: the `+ 1 day` conversion of the inclusive `effective_to`
exists in exactly one place per table and must be the same place in all of them (ADR-0008, point
3)."""

_BUDGET_WINDOW_MONTH_ALIGNED_EXPRESSION = (
    "effective_from = date_trunc('month', effective_from)"
    " AND (effective_to + 1) = date_trunc('month', (effective_to + 1))"
)

_STATUTORY_LEAVE_INDEX_EXPRESSION = "(true)"
_STATUTORY_LEAVE_INDEX_PREDICATE = "is_statutory_leave"

_LOCK_TIMEOUT = "3s"
"""How long the one `ALTER TABLE` here waits for its lock before giving up (R-06, `d5e94a1c6b73`).

The same figure as the migrations that established this convention, and deliberately the same rather
than re-argued: a second number would make "how long does a migration wait" a question with two
answers. See the comment at the `ALTER` below for why only that block is bounded."""


def upgrade() -> None:
    # --- the eighth catalogue table (ADR-0005, addendum 2026-09-22 SC-3-03) ----------------------
    op.create_table(
        "absence_budget",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("calendar_id", sa.UUID(), nullable=False),
        sa.Column("engagement_type_id", sa.UUID(), nullable=False),
        # NUMERIC → Decimal, never float: this figure multiplies the calendar's standard day and
        # then a rate (NF-01, ADR-0002). Nothing rounds it at write time — rounding is the
        # consumer's rule (`app.core.money.round_money`).
        sa.Column("budget_days", sa.Numeric(precision=6, scale=2), nullable=False),
        sa.Column(
            "unit", sa.String(length=20), server_default=sa.text("'day'"), nullable=False
        ),
        sa.Column("source", sa.String(length=500), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        # Nullable in the column, refused by the CHECK below: the shared shape of the pattern is
        # kept and the narrowing is named separately (ADR-0008, addendum SC-3-03, point 10b).
        sa.Column("effective_to", sa.Date(), nullable=True),
        # The one representation of the window, generated by the database and read by both the
        # `EXCLUDE` below and every lookup (`valid_period @> :day`) — never rebuilt in a `WHERE`.
        sa.Column(
            "valid_period",
            postgresql.DATERANGE(),
            sa.Computed(_VALID_PERIOD_EXPRESSION, persisted=True),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("unit = 'day'", name=op.f("ck_absence_budget_unit_is_day")),
        # Zero days is legal and meaningful (an engagement type with no entitlement); negative is a
        # sign error with no meaning in any calculation.
        sa.CheckConstraint(
            "budget_days >= 0", name=op.f("ck_absence_budget_budget_days_not_negative")
        ),
        # `NOT NULL` alone still admits `''` and `'   '` (criterion K-02).
        sa.CheckConstraint(
            "source ~ '[^[:space:]]'", name=op.f("ck_absence_budget_source_not_blank")
        ),
        # Load-bearing for the EXCLUDE below, not merely tidy: with `effective_to` one day *before*
        # `effective_from`, `daterange` yields an *empty* range and `&&` against an empty range is
        # false for everything — the overlap constraint would silently stop applying to that row.
        # The `IS NULL` branch is dead on this table and kept anyway, so the shared shape has one
        # divergence and not two.
        sa.CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name=op.f("ck_absence_budget_effective_period_ordered"),
        ),
        sa.CheckConstraint(
            "effective_to IS NOT NULL", name=op.f("ck_absence_budget_effective_to_is_closed")
        ),
        sa.CheckConstraint(
            _BUDGET_WINDOW_MONTH_ALIGNED_EXPRESSION,
            name=op.f("ck_absence_budget_window_aligned_to_whole_months"),
        ),
        sa.ForeignKeyConstraint(
            ["calendar_id"], ["working_calendar.id"], name=op.f("fk_absence_budget_calendar_id")
        ),
        sa.ForeignKeyConstraint(
            ["engagement_type_id"],
            ["catalog_engagement_types.id"],
            name=op.f("fk_absence_budget_engagement_type_id"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_absence_budget")),
    )
    # The overlap guarantee, in the database (ADR-0001, invariant-guardian rule 13). Written as raw
    # SQL for the reason migration `c1a4f7b92e05` gives: an `EXCLUDE` is spelled by hand once, so
    # the constraint a reader of this file sees is the constraint PostgreSQL gets. `btree_gist`
    # (created by `7b3d5c81e40a`) is what allows `=` on two `uuid` columns inside a gist index; `&&`
    # on a `daterange` is native.
    op.execute(
        "ALTER TABLE absence_budget ADD CONSTRAINT ex_absence_budget_no_overlapping_periods"
        " EXCLUDE USING gist ("
        "calendar_id WITH =, engagement_type_id WITH =, valid_period WITH &&)"
    )

    # --- which absence type the budget settles against (ADR-0008, addendum SC-3-03, point 8) -----
    # `NOT NULL DEFAULT false`, added to a populated table: PostgreSQL 11+ records the default in
    # the catalogue instead of rewriting every row, so this is a brief ACCESS EXCLUSIVE lock and no
    # table rewrite. The `lock_timeout` follows the convention `d5e94a1c6b73` established (R-06) and
    # applies for the same reason: the statement is microseconds of work *once it holds the lock*,
    # and the entire risk is the waiting — a lock request queues ahead of everything that arrives
    # after it, so an `ALTER` waiting on one long-running reader puts every later reader behind
    # itself. With the bound it is a migration that fails with `55P03`, rolls the whole batch back
    # (`migrations/env.py` runs one transaction) and leaves the database at `f3a1d0c58b27`.
    op.execute(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT}'")
    op.add_column(
        "absence_type",
        sa.Column(
            "is_statutory_leave",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    # `SET LOCAL` lasts to the end of the *transaction*, so without this line the bound would
    # silently apply to every migration running after this one in the same batch. `DEFAULT` rather
    # than a saved value: reading the old one needs `SHOW`, i.e. a live connection, which would make
    # this file unrenderable by `alembic upgrade --sql` — the documented review practice.
    op.execute("SET LOCAL lock_timeout = DEFAULT")
    # At most one flagged row, refused inside the statement that inserts the second one. A unique
    # index on a constant expression, restricted to the flagged rows: both flagged rows index the
    # same value. The other half — "at least one" — is not expressible as an index and is a named
    # state instead (point 8b), never a guess.
    op.execute(
        "CREATE UNIQUE INDEX uq_absence_type_statutory_leave"
        f" ON absence_type ({_STATUTORY_LEAVE_INDEX_EXPRESSION})"
        f" WHERE {_STATUTORY_LEAVE_INDEX_PREDICATE}"
    )

    # --- the frozen absence type grows the third flag (ADR-0004, addendum SC-3-03, point 8) ------
    # Added **with** a `false` default and then stripped of it, which is the only shape that is both
    # expand-safe and faithful to the table's rule that a snapshot column has no default (a column
    # that can be written without a value read from the source is a column that can be silently
    # wrong). The default exists for the length of this migration so that rows frozen *before* the
    # flag existed get `false` — the honest value for them: no absence type carried the flag at the
    # moment those approvals happened, because there was no flag to carry.
    op.execute(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT}'")
    op.add_column(
        "approved_snapshot_absence_type",
        sa.Column(
            "is_statutory_leave",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.alter_column("approved_snapshot_absence_type", "is_statutory_leave", server_default=None)
    op.execute("SET LOCAL lock_timeout = DEFAULT")

    # --- the fourth snapshot table (ADR-0004, addendum 2026-09-22 SC-3-03) -----------------------
    # Values, never references: the only foreign key points at `scenarios`. The raw budget and
    # nothing else — days, unit, window, source. No column carries a prorated or otherwise computed
    # figure (criterion K-07 asserts that over the set of columns rather than over behaviour), and
    # none carries a date the clock produced: the approval freezes every window the scenario's
    # months touch, so each row is attributed by the window it holds (reviewer R-03/R-06).
    op.create_table(
        "approved_snapshot_absence_budget",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("scenario_id", sa.UUID(), nullable=False),
        sa.Column("source_budget_id", sa.UUID(), nullable=False),
        sa.Column("source_calendar_id", sa.UUID(), nullable=False),
        sa.Column("source_engagement_type_id", sa.UUID(), nullable=False),
        sa.Column("budget_days", sa.Numeric(precision=6, scale=2), nullable=False),
        sa.Column("unit", sa.String(length=20), nullable=False),
        sa.Column("source", sa.String(length=500), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        # `NOT NULL` with no CHECK: the source table refuses an open-ended window, and a snapshot
        # records what was approved rather than re-judging it.
        sa.Column("effective_to", sa.Date(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["scenario_id"],
            ["scenarios.id"],
            name=op.f("fk_approved_snapshot_absence_budget_scenario_id"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_approved_snapshot_absence_budget")),
    )
    op.create_index(
        op.f("ix_approved_snapshot_absence_budget_scenario_id"),
        "approved_snapshot_absence_budget",
        ["scenario_id"],
    )


def downgrade() -> None:
    """Reverse of `upgrade`, children before parents.

    A downgrade of an expand-only migration is data loss by definition (every budget row and every
    frozen budget goes with the tables), which is why the deployment order is expand → deploy →
    contract and never "deploy and downgrade if it goes wrong". This exists so the migration can be
    *tested* both ways, as `tests/test_catalog_migration_reversibility.py` does for SC-2-03.

    No guard of the shape `c1a4f7b92e05` grew for `vendor_id` (R-01): that one existed because
    dropping a column **reinterprets** the rows that survive — a vendor-priced rate silently became
    an internal one. Nothing here survives to be reinterpreted: both tables are dropped whole, and
    `is_statutory_leave` going away leaves no row claiming something it is not, because the only
    thing that read it is dropped in the same step.
    """
    op.drop_table("approved_snapshot_absence_budget")
    op.execute("DROP INDEX IF EXISTS uq_absence_type_statutory_leave")

    op.execute(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT}'")
    op.drop_column("approved_snapshot_absence_type", "is_statutory_leave")
    op.drop_column("absence_type", "is_statutory_leave")
    op.execute("SET LOCAL lock_timeout = DEFAULT")

    op.drop_table("absence_budget")
