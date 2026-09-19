"""create staffing_position and staffing_position_allocation

Expand only (ADR-0001): two new tables, nothing dropped, nothing altered, no column of an existing
table changed in meaning — so the code version deployed before this migration keeps working against
the new schema and there is no contract phase to pair with it.

**What the database enforces here, and why each rule is in the schema rather than only in the
request schema** (`app.api.schemas.staffing` states the same rules to get a `422` instead of a
`500`; a fixture, a seed script, a future import or a second endpoint never sees that schema):

1. `UNIQUE (position_id, period_month)` — one allocation row per position per calendar month.
2. `CHECK period_month = date_trunc('month', period_month)` — load-bearing for the rule above, not
   cosmetic: without it `2026-03-01` and `2026-03-15` are two different `DATE` values and the unique
   constraint accepts both, so "one allocation per month" silently becomes "per day somebody typed".
3. Three separate `CHECK … >= 0`, one per hour column, so a refusal names which figure was negative
   and so dropping one of the three is visible.
4. `CHECK headcount > 0` and `CHECK end_date IS NULL OR end_date >= start_date`.
5. Five foreign keys, all `NO ACTION` (no `ondelete` clause): to the scenario and to the four
   catalogue dictionaries. Nothing in this plan block deletes any of those, and refusing to orphan a
   row pre-empts no later decision about referential history. Towards the scenario it matters twice
   over — `ON DELETE CASCADE` would be a way for the rows of an `approved` scenario to disappear
   past the write guard, which covers `INSERT`/`UPDATE` and not a cascade.

**What is deliberately not here: `EXCLUDE USING gist`.** The effective-range pattern of ADR-0008
(`catalog_default_rates`, migration `7b3d5c81e40a`) is *not* applied to a position's period —
overlapping positions for the same role over the same months are legal (ADR-0004, addendum
2026-09-19). No `btree_gist` object is needed and none is created. The absence is the decision.

Revision ID: b6d2f74c3e18
Revises: 7b3d5c81e40a
Create Date: 2026-09-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b6d2f74c3e18"
down_revision: str | None = "7b3d5c81e40a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Spelled here as well as in `app.models.staffing` rather than imported from it: a migration must
# keep describing the schema it produced even after the model moves on, and an import would silently
# rewrite history the next time somebody edits the model. What keeps the two copies honest is
# `test_the_model_and_the_migration_agree_on_the_month_check` in
# `tests/test_staffing_schema_constraints.py` — the same drift guard R-02 added for the catalogue's
# generated column.
_FIRST_DAY_OF_MONTH_EXPRESSION = "period_month = date_trunc('month', period_month)"

_HOURS_COLUMNS = ("availability_hours", "planned_allocation_hours", "billable_hours")


def upgrade() -> None:
    op.create_table(
        "staffing_position",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("scenario_id", sa.UUID(), nullable=False),
        # The full catalogue tuple, all four NOT NULL — the same key shape as
        # `catalog_default_rates`, because a nullable dimension would mean "any" and that is a
        # rate-resolution rule nobody decided.
        sa.Column("role_id", sa.UUID(), nullable=False),
        sa.Column("seniority_id", sa.UUID(), nullable=False),
        sa.Column("location_id", sa.UUID(), nullable=False),
        sa.Column("engagement_type_id", sa.UUID(), nullable=False),
        sa.Column("headcount", sa.Integer(), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        # NULL = open-ended, not a 9999-12-31 sentinel (the reasoning of ADR-0008 point 2, reused).
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        # ADR-0007's concurrency token for the position and its whole month grid (addendum
        # 2026-09-19). `now()` is the database's clock: two application instances must not be able
        # to disagree about which write came last.
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("headcount > 0", name=op.f("ck_staffing_position_headcount_positive")),
        sa.CheckConstraint(
            "end_date IS NULL OR end_date >= start_date",
            name=op.f("ck_staffing_position_position_period_ordered"),
        ),
        sa.ForeignKeyConstraint(
            ["scenario_id"], ["scenarios.id"], name=op.f("fk_staffing_position_scenario_id")
        ),
        sa.ForeignKeyConstraint(
            ["role_id"], ["catalog_roles.id"], name=op.f("fk_staffing_position_role_id")
        ),
        sa.ForeignKeyConstraint(
            ["seniority_id"],
            ["catalog_seniorities.id"],
            name=op.f("fk_staffing_position_seniority_id"),
        ),
        sa.ForeignKeyConstraint(
            ["location_id"],
            ["catalog_locations.id"],
            name=op.f("fk_staffing_position_location_id"),
        ),
        sa.ForeignKeyConstraint(
            ["engagement_type_id"],
            ["catalog_engagement_types.id"],
            name=op.f("fk_staffing_position_engagement_type_id"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_staffing_position")),
    )
    op.create_index(
        op.f("ix_staffing_position_scenario_id"), "staffing_position", ["scenario_id"]
    )

    op.create_table(
        "staffing_position_allocation",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("position_id", sa.UUID(), nullable=False),
        sa.Column("period_month", sa.Date(), nullable=False),
        # NUMERIC(10,2) → Decimal, never float: hours are one multiplication away from money
        # (NF-01, ADR-0002). Three independent inputs, none derived from another (criterion K-05).
        *(
            sa.Column(column, sa.Numeric(precision=10, scale=2), nullable=False)
            for column in _HOURS_COLUMNS
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        # No `updated_at`: the concurrency token is the position's (ADR-0007, addendum 2026-09-19).
        sa.CheckConstraint(
            _FIRST_DAY_OF_MONTH_EXPRESSION,
            name=op.f("ck_staffing_position_allocation_period_month_is_first_of_month"),
        ),
        # One constraint per column, not one conjunction: the refusal then names which figure was
        # negative, and removing one of the three is a mutation a test can kill. Zero is accepted —
        # a month without allocation is data, not an error.
        # The names drop the `_hours` suffix on purpose: `ck_staffing_position_allocation_` already
        # spends 32 of PostgreSQL's 63 identifier characters, and the full column name would push
        # `planned_allocation_hours` past the limit and be silently truncated.
        *(
            sa.CheckConstraint(
                f"{column} >= 0",
                name=op.f(
                    "ck_staffing_position_allocation_"
                    f"{column.removesuffix('_hours')}_non_negative"
                ),
            )
            for column in _HOURS_COLUMNS
        ),
        sa.ForeignKeyConstraint(
            ["position_id"],
            ["staffing_position.id"],
            name=op.f("fk_staffing_position_allocation_position_id"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_staffing_position_allocation")),
        sa.UniqueConstraint(
            "position_id",
            "period_month",
            name=op.f("uq_staffing_position_allocation_position_id_period_month"),
        ),
    )
    # **No separate index on `position_id`** (R-05, reviewer 2026-09-19). The unique constraint
    # above creates a btree on `(position_id, period_month)`, whose leading column is this one, so
    # `WHERE position_id = ?` — the only lookup this table has — is already served by it. A second
    # index would cost a write on every insert and update of a month row and answer no query the
    # first one does not. The index on `staffing_position.scenario_id` stays: that column has no
    # unique constraint to ride on.


def downgrade() -> None:
    # The grandchild first: its foreign key to `staffing_position` is `NO ACTION`, so the parent
    # table cannot be dropped while allocation rows still point at it.
    op.drop_table("staffing_position_allocation")
    op.drop_index(op.f("ix_staffing_position_scenario_id"), table_name="staffing_position")
    op.drop_table("staffing_position")
