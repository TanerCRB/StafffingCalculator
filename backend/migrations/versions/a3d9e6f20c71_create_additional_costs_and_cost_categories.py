"""create additional_cost and catalog_cost_categories

SC-5-05 (F-08, Issue #10; ADR-0014, ADR-0004/0005/0007/0008 addenda 2026-09-23 SC-5-05). Expand only
(ADR-0001, expand → deploy → contract): two new tables and one new constraint on an existing table.
No column is added to, dropped from or altered on an existing table, no existing constraint is
changed and no row is written — so the code deployed before this migration keeps working against the
new schema, and there is no contract phase to pair with it.

**The one statement touching an existing table** is `ALTER TABLE staffing_position ADD CONSTRAINT
uq_staffing_position_id_scenario_id UNIQUE (id, scenario_id)`. Purely additive, and it cannot fail
on existing data: `id` is the primary key, so every `(id, scenario_id)` pair is already unique. It
exists because PostgreSQL accepts a foreign key only against a unique constraint on exactly the
referenced columns — the parent half of the composite key below (the construction of
`uq_commercial_terms_id_model_type`, SC-4-01). It builds an index, so it takes a lock on
`staffing_position` for the duration of that build; `lock_timeout` below bounds the wait for it.

**What the database enforces here, and why in the schema** (a fixture, a seed script or a future
import never passes through a Pydantic model):

1. `additional_cost.scenario_id` `NOT NULL`, no `ON DELETE` action (ADR-0014, point 1; ADR-0003,
   point 1).
2. **A position of the same scenario, as a composite foreign key** — `(position_id, scenario_id) →
   staffing_position (id, scenario_id)`. `MATCH SIMPLE` skips it for `position_id IS NULL`, the
   scenario-level cost. A cost pointing at another scenario's position is unwritable (criterion
   K-03, control D-7).
3. `amount > 0` (G-1), `NUMERIC(14,4)` (ADR-0008, point 6).
4. The shape of the period agreeing with the type: a recurring cost has an end (ADR-0008, aneks
   SC-5-05, point 2), a one-off cost has none, the end is not before the start, and both ends are
   first days of a month.
5. `cost_type` and `funding_source` closed by CHECKs; the currency by the two rules
   `catalog_default_rates` carries.
6. `category_id` → `catalog_cost_categories`, no `ON DELETE` action: a category any cost points at
   cannot be deleted (ADR-0014, point 2).
7. `catalog_cost_categories` — a dictionary of the same shape as the other catalogue dictionaries
   (`name_not_blank` CHECK, unique index on the normalised name, `updated_at` marker). **Not
   seeded** (ADR-0014, point 2; the precedent of ADR-0012, point 3).

**What is deliberately not here:** an `EXCLUDE` constraint (ADR-0008, aneks SC-5-05, point 1 — two
costs of one category in one month are both counted), a snapshot table (ADR-0004, aneks SC-5-05:
group 2, protected by the write guard), a free-text column, and any `ON DELETE` action.

Revision ID: a3d9e6f20c71
Revises: e7b41c9d2a58
Create Date: 2026-09-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a3d9e6f20c71"
down_revision: str | None = "e7b41c9d2a58"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LOCK_TIMEOUT = "3s"
"""The same bound as every migration since `d5e94a1c6b73` (R-06): the `ALTER TABLE` on
`staffing_position` and the foreign keys to `scenarios`, `staffing_position` and the new dictionary
all wait for locks on tables other sessions read."""

# Spelled here as well as in the models rather than imported from them: a migration must keep
# describing the schema it produced even after the model moves on (the rule of `f3a1d0c58b27`).
# `tests/test_additional_cost_schema.py` keeps the copies honest.
_CATEGORY_TABLE = "catalog_cost_categories"
_COST_TABLE = "additional_cost"

# `regexp_replace` before `btrim` (see `7b3d5c81e40a`): one-argument `btrim` strips spaces only.
_DIMENSION_NAME_KEY_EXPRESSION = r"lower(btrim(regexp_replace(name, '\s+', ' ', 'g')))"

_POSITION_UNIQUE = "uq_staffing_position_id_scenario_id"
_POSITION_SAME_SCENARIO_FOREIGN_KEY = "fk_additional_cost_position_same_scenario"

_COST_TYPE_KNOWN_EXPRESSION = "cost_type IN ('one_off', 'recurring')"
_FUNDING_SOURCE_KNOWN_EXPRESSION = "funding_source IN ('internal', 'rebilled_to_client')"
_RECURRING_PERIOD_CLOSED_EXPRESSION = "cost_type <> 'recurring' OR end_month IS NOT NULL"
_ONE_OFF_SINGLE_MONTH_EXPRESSION = "cost_type <> 'one_off' OR end_month IS NULL"
_PERIOD_ORDERED_EXPRESSION = "end_month IS NULL OR end_month >= start_month"
_START_MONTH_IS_FIRST_EXPRESSION = "start_month = date_trunc('month', start_month)"
_END_MONTH_IS_FIRST_EXPRESSION = "end_month IS NULL OR end_month = date_trunc('month', end_month)"


def upgrade() -> None:
    op.execute(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT}'")

    # --- the dictionary (ADR-0014, point 2; ADR-0005, aneks SC-5-05, point 3) -------------------
    op.create_table(
        _CATEGORY_TABLE,
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "name ~ '[^[:space:]]'", name=op.f(f"ck_{_CATEGORY_TABLE}_name_not_blank")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{_CATEGORY_TABLE}")),
    )
    op.execute(
        f"CREATE UNIQUE INDEX uq_{_CATEGORY_TABLE}_name_normalized"
        f" ON {_CATEGORY_TABLE} ({_DIMENSION_NAME_KEY_EXPRESSION})"
    )

    # --- the parent half of the composite foreign key (additive; cannot fail on existing rows) ---
    op.create_unique_constraint(_POSITION_UNIQUE, "staffing_position", ["id", "scenario_id"])

    # --- the cost row (ADR-0014, points 1, 3, 5, 6, 9) -------------------------------------------
    op.create_table(
        _COST_TABLE,
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("scenario_id", sa.UUID(), nullable=False),
        sa.Column("position_id", sa.UUID(), nullable=True),
        sa.Column("category_id", sa.UUID(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=14, scale=4), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("cost_type", sa.String(length=20), nullable=False),
        sa.Column("start_month", sa.Date(), nullable=False),
        sa.Column("end_month", sa.Date(), nullable=True),
        sa.Column("funding_source", sa.String(length=30), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("amount > 0", name=op.f(f"ck_{_COST_TABLE}_amount_positive")),
        sa.CheckConstraint(
            "char_length(currency) = 3", name=op.f(f"ck_{_COST_TABLE}_currency_iso4217")
        ),
        sa.CheckConstraint(
            "currency = upper(currency)", name=op.f(f"ck_{_COST_TABLE}_currency_is_upper")
        ),
        sa.CheckConstraint(
            _COST_TYPE_KNOWN_EXPRESSION, name=op.f(f"ck_{_COST_TABLE}_cost_type_known")
        ),
        sa.CheckConstraint(
            _FUNDING_SOURCE_KNOWN_EXPRESSION,
            name=op.f(f"ck_{_COST_TABLE}_funding_source_known"),
        ),
        sa.CheckConstraint(
            _START_MONTH_IS_FIRST_EXPRESSION,
            name=op.f(f"ck_{_COST_TABLE}_start_month_is_first_of_month"),
        ),
        sa.CheckConstraint(
            _END_MONTH_IS_FIRST_EXPRESSION,
            name=op.f(f"ck_{_COST_TABLE}_end_month_is_first_of_month"),
        ),
        sa.CheckConstraint(
            _RECURRING_PERIOD_CLOSED_EXPRESSION,
            name=op.f(f"ck_{_COST_TABLE}_recurring_period_closed"),
        ),
        sa.CheckConstraint(
            _ONE_OFF_SINGLE_MONTH_EXPRESSION,
            name=op.f(f"ck_{_COST_TABLE}_one_off_single_month"),
        ),
        sa.CheckConstraint(
            _PERIOD_ORDERED_EXPRESSION, name=op.f(f"ck_{_COST_TABLE}_period_ordered")
        ),
        # No `ondelete` on any of the three: a cascade would be a second, unguarded way for the
        # rows of an `approved` scenario to disappear (ADR-0003, point 1), and a category in use
        # must not be deletable (ADR-0014, point 2).
        sa.ForeignKeyConstraint(
            ["scenario_id"], ["scenarios.id"], name=op.f(f"fk_{_COST_TABLE}_scenario_id")
        ),
        sa.ForeignKeyConstraint(
            ["position_id", "scenario_id"],
            ["staffing_position.id", "staffing_position.scenario_id"],
            name=op.f(_POSITION_SAME_SCENARIO_FOREIGN_KEY),
        ),
        sa.ForeignKeyConstraint(
            ["category_id"],
            [f"{_CATEGORY_TABLE}.id"],
            name=op.f(f"fk_{_COST_TABLE}_category_id"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{_COST_TABLE}")),
    )
    op.create_index(op.f(f"ix_{_COST_TABLE}_scenario_id"), _COST_TABLE, ["scenario_id"])
    op.create_index(op.f(f"ix_{_COST_TABLE}_position_id"), _COST_TABLE, ["position_id"])
    op.create_index(op.f(f"ix_{_COST_TABLE}_category_id"), _COST_TABLE, ["category_id"])

    # `SET LOCAL` lasts to the end of the transaction; reset so the bound does not silently apply to
    # every migration after this one in the same batch (the convention of `a7c2e5f81b94`).
    op.execute("SET LOCAL lock_timeout = DEFAULT")


def downgrade() -> None:
    """Reverse of `upgrade`, children before parents.

    Data loss by definition, like every downgrade of an expand migration: it exists so the migration
    can be tested both ways, not as a deployment path. Dropping these tables reinterprets no
    surviving row — no other table points at either of them.
    """
    op.execute(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT}'")
    op.drop_index(op.f(f"ix_{_COST_TABLE}_category_id"), table_name=_COST_TABLE)
    op.drop_index(op.f(f"ix_{_COST_TABLE}_position_id"), table_name=_COST_TABLE)
    op.drop_index(op.f(f"ix_{_COST_TABLE}_scenario_id"), table_name=_COST_TABLE)
    op.drop_table(_COST_TABLE)
    op.drop_constraint(_POSITION_UNIQUE, "staffing_position", type_="unique")
    op.execute(f"DROP INDEX uq_{_CATEGORY_TABLE}_name_normalized")
    op.drop_table(_CATEGORY_TABLE)
    op.execute("SET LOCAL lock_timeout = DEFAULT")
