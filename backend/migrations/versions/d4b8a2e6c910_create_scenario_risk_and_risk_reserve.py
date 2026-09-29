"""create scenario_risk and risk_reserve, and link additional_cost to a risk

SC-6-08 (F-09 pt 4-5, Issue #89; ADR-0021, ADR-0004/0005/0007/0014 addenda SC-6-08). Expand only
(ADR-0001, expand -> deploy -> contract): two new tables and one new **nullable** column with its
foreign key on an existing table. No existing column is altered or dropped, no existing constraint
is changed and no row is written, so the code deployed before this migration keeps working against
the new schema (it never names `risk_id`, and the column defaults to `NULL`), and there is no
contract phase to pair with it.

**The statement touching an existing table** is `ALTER TABLE additional_cost ADD COLUMN risk_id
UUID NULL` plus its index and composite foreign key. Adding a nullable column with no default is a
metadata-only change in PostgreSQL 11+; the foreign key validates against an all-`NULL` column
(`MATCH SIMPLE` skips it), and the index build takes a lock on `additional_cost` for its duration -
`lock_timeout` below bounds the wait.

**What the database enforces here, and why in the schema** (a fixture, a seed script or a future
import never passes through a Pydantic model):

1. `scenario_risk.scenario_id` and `risk_reserve.scenario_id` `NOT NULL`, no `ON DELETE` action
   (ADR-0021, point 7; ADR-0003, point 1).
2. `UNIQUE (id, scenario_id)` on `scenario_risk` - the parent half of the composite keys - and
   `UNIQUE (scenario_id, name)`, the copy's remap key (point 8).
3. **A risk of the same scenario, as a composite foreign key**, twice: `(risk_id, scenario_id) ->
   scenario_risk (id, scenario_id)` on `additional_cost` and on `risk_reserve`. `MATCH SIMPLE` skips
   it for `risk_id IS NULL`. A link across scenarios is unwritable, and a referenced risk is
   undeletable (Q-8 = A: no `ON DELETE` action).
4. On `risk_reserve`: `amount > 0`, `NUMERIC(14,4)`, the period's shape agreeing with the type, the
   currency rules - the same rules `additional_cost` carries (ADR-0021, point 4).

**What is deliberately not here:** an `EXCLUDE`, a snapshot table (group 2), a free-text column, a
`position_id`, and any `ON DELETE` action.

Revision ID: d4b8a2e6c910
Revises: d4a7e19c2b60
Create Date: 2026-09-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d4b8a2e6c910"
down_revision: str | None = "d4a7e19c2b60"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LOCK_TIMEOUT = "3s"

# Spelled here as well as in the models rather than imported from them: a migration must keep
# describing the schema it produced even after the model moves on (the rule of `f3a1d0c58b27`).
# `tests/test_risk_schema.py` keeps the copies honest.
_RISK_TABLE = "scenario_risk"
_RESERVE_TABLE = "risk_reserve"
_COST_TABLE = "additional_cost"

_RISK_ID_SCENARIO_UNIQUE = "uq_scenario_risk_id_scenario_id"
_RISK_SCENARIO_NAME_UNIQUE = "uq_scenario_risk_scenario_id_name"
_RESERVE_RISK_FOREIGN_KEY = "fk_risk_reserve_risk_same_scenario"
_COST_RISK_FOREIGN_KEY = "fk_additional_cost_risk_same_scenario"

_NAME_NOT_BLANK_EXPRESSION = "name ~ '[^[:space:]]'"
_RESERVE_TYPE_KNOWN_EXPRESSION = "reserve_type IN ('one_off', 'recurring')"
_RECURRING_PERIOD_CLOSED_EXPRESSION = "reserve_type <> 'recurring' OR end_month IS NOT NULL"
_ONE_OFF_SINGLE_MONTH_EXPRESSION = "reserve_type <> 'one_off' OR end_month IS NULL"
_PERIOD_ORDERED_EXPRESSION = "end_month IS NULL OR end_month >= start_month"
_START_MONTH_IS_FIRST_EXPRESSION = "start_month = date_trunc('month', start_month)"
_END_MONTH_IS_FIRST_EXPRESSION = "end_month IS NULL OR end_month = date_trunc('month', end_month)"


def upgrade() -> None:
    op.execute(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT}'")

    # --- the declared risk (ADR-0021, points 1, 7, 8) ---------------------------------------------
    op.create_table(
        _RISK_TABLE,
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("scenario_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
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
        sa.CheckConstraint(
            _NAME_NOT_BLANK_EXPRESSION, name=op.f(f"ck_{_RISK_TABLE}_name_not_blank")
        ),
        sa.ForeignKeyConstraint(
            ["scenario_id"], ["scenarios.id"], name=op.f(f"fk_{_RISK_TABLE}_scenario_id")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{_RISK_TABLE}")),
        sa.UniqueConstraint("id", "scenario_id", name=_RISK_ID_SCENARIO_UNIQUE),
        sa.UniqueConstraint("scenario_id", "name", name=_RISK_SCENARIO_NAME_UNIQUE),
    )
    op.create_index(op.f(f"ix_{_RISK_TABLE}_scenario_id"), _RISK_TABLE, ["scenario_id"])

    # --- the reserve (ADR-0021, points 2, 4, 7) ---------------------------------------------------
    op.create_table(
        _RESERVE_TABLE,
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("scenario_id", sa.UUID(), nullable=False),
        sa.Column("risk_id", sa.UUID(), nullable=True),
        sa.Column("amount", sa.Numeric(precision=14, scale=4), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("reserve_type", sa.String(length=20), nullable=False),
        sa.Column("start_month", sa.Date(), nullable=False),
        sa.Column("end_month", sa.Date(), nullable=True),
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
        sa.CheckConstraint("amount > 0", name=op.f(f"ck_{_RESERVE_TABLE}_amount_positive")),
        sa.CheckConstraint(
            "char_length(currency) = 3", name=op.f(f"ck_{_RESERVE_TABLE}_currency_iso4217")
        ),
        sa.CheckConstraint(
            "currency = upper(currency)", name=op.f(f"ck_{_RESERVE_TABLE}_currency_is_upper")
        ),
        sa.CheckConstraint(
            _RESERVE_TYPE_KNOWN_EXPRESSION, name=op.f(f"ck_{_RESERVE_TABLE}_reserve_type_known")
        ),
        sa.CheckConstraint(
            _START_MONTH_IS_FIRST_EXPRESSION,
            name=op.f(f"ck_{_RESERVE_TABLE}_start_month_is_first_of_month"),
        ),
        sa.CheckConstraint(
            _END_MONTH_IS_FIRST_EXPRESSION,
            name=op.f(f"ck_{_RESERVE_TABLE}_end_month_is_first_of_month"),
        ),
        sa.CheckConstraint(
            _RECURRING_PERIOD_CLOSED_EXPRESSION,
            name=op.f(f"ck_{_RESERVE_TABLE}_recurring_period_closed"),
        ),
        sa.CheckConstraint(
            _ONE_OFF_SINGLE_MONTH_EXPRESSION,
            name=op.f(f"ck_{_RESERVE_TABLE}_one_off_single_month"),
        ),
        sa.CheckConstraint(
            _PERIOD_ORDERED_EXPRESSION, name=op.f(f"ck_{_RESERVE_TABLE}_period_ordered")
        ),
        # No `ondelete` on either key (ADR-0021, Q-8 = A; ADR-0003, point 1).
        sa.ForeignKeyConstraint(
            ["scenario_id"], ["scenarios.id"], name=op.f(f"fk_{_RESERVE_TABLE}_scenario_id")
        ),
        sa.ForeignKeyConstraint(
            ["risk_id", "scenario_id"],
            [f"{_RISK_TABLE}.id", f"{_RISK_TABLE}.scenario_id"],
            name=op.f(_RESERVE_RISK_FOREIGN_KEY),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{_RESERVE_TABLE}")),
    )
    op.create_index(op.f(f"ix_{_RESERVE_TABLE}_scenario_id"), _RESERVE_TABLE, ["scenario_id"])
    op.create_index(op.f(f"ix_{_RESERVE_TABLE}_risk_id"), _RESERVE_TABLE, ["risk_id"])

    # --- the optional link from a cost event (additive: a nullable column, no default) ------------
    op.add_column(_COST_TABLE, sa.Column("risk_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        op.f(_COST_RISK_FOREIGN_KEY),
        _COST_TABLE,
        _RISK_TABLE,
        ["risk_id", "scenario_id"],
        ["id", "scenario_id"],
    )
    op.create_index(op.f(f"ix_{_COST_TABLE}_risk_id"), _COST_TABLE, ["risk_id"])

    op.execute("SET LOCAL lock_timeout = DEFAULT")


def downgrade() -> None:
    """Reverse of `upgrade`, children before parents.

    Data loss by definition, like every downgrade of an expand migration: it exists so the migration
    can be tested both ways, not as a deployment path. Dropping `additional_cost.risk_id` discards
    the link only; every cost row survives.
    """
    op.execute(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT}'")
    op.drop_index(op.f(f"ix_{_COST_TABLE}_risk_id"), table_name=_COST_TABLE)
    op.drop_constraint(_COST_RISK_FOREIGN_KEY, _COST_TABLE, type_="foreignkey")
    op.drop_column(_COST_TABLE, "risk_id")
    op.drop_index(op.f(f"ix_{_RESERVE_TABLE}_risk_id"), table_name=_RESERVE_TABLE)
    op.drop_index(op.f(f"ix_{_RESERVE_TABLE}_scenario_id"), table_name=_RESERVE_TABLE)
    op.drop_table(_RESERVE_TABLE)
    op.drop_index(op.f(f"ix_{_RISK_TABLE}_scenario_id"), table_name=_RISK_TABLE)
    op.drop_table(_RISK_TABLE)
    op.execute("SET LOCAL lock_timeout = DEFAULT")
