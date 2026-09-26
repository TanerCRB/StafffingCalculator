"""add cost_basis and fixed_amount to staffing_position

SC-5-03 (F-07, Issue #78; ADR-0013 aneks 2026-09-25 SC-5-03, ADR-0004/0005 addenda of the same
date). Expand only (ADR-0001, expand -> deploy -> contract): three new, nullable-or-defaulted
columns on an existing table and five new CHECK constraints. No column is dropped or altered, no
existing constraint is changed, and no row is rewritten by anything other than its own default — so
the code deployed before this migration keeps reading and writing `staffing_position` exactly as it
did, and there is no contract phase to pair with it.

**What the database enforces here, and why in the schema** (a fixture, a seed script or a future
import never passes through a Pydantic model — ADR-0001):

1. `cost_basis` is `NOT NULL` with `server_default 'worked_time'` (K-02): every row written before
   this migration, and every row a future writer that does not know about the new column inserts,
   keeps costing exactly as SC-5-01 already proved. Closed to two values by a CHECK
   (`cost_basis_known`), not a PostgreSQL enum — the same reasoning `commercial_terms.model_type`
   and `additional_cost.cost_type` already use: widening a CHECK is one statement inside this
   migration's own transaction, `ALTER TYPE ... ADD VALUE` is not.
2. `fixed_amount` (`NUMERIC(14,4)`, nullable) and `fixed_amount_currency` (`CHAR(3)`, nullable) are
   the position's own stated cost and its currency when `cost_basis = 'fixed_amount'` — `NULL`
   otherwise.
3. **`fixed_amount_required_for_its_basis`** (ADR-0013 aneks 2026-09-25 SC-5-03, point 2; the same
   construction as `ck_additional_cost_amount_positive`/G-1, ADR-0014 D-10):
   `cost_basis <> 'fixed_amount' OR (fixed_amount IS NOT NULL AND fixed_amount_currency IS NOT
   NULL)`. The aneks leaves open whether the currency joins the amount in this CHECK or stays
   nullable with a fallback; this migration's answer is that it joins it — a `fixed_amount` with no
   currency is exactly the unnamed state ADR-0013's "two shapes, never a third" forbids, so the
   database refuses it from existing at all rather than leaving a formula to invent a third shape
   for it.
4. `fixed_amount > 0` when not `NULL` (mirrors `additional_cost.amount_positive`, G-1) and the same
   two ISO-4217 rules `additional_cost.currency`/`catalog_default_rates.currency` already carry
   (three characters, upper case) on `fixed_amount_currency` when not `NULL`.

**What is deliberately not here:** a snapshot table (ADR-0004, aneks 2026-09-25 SC-5-03, point 2 —
own data of the scenario, protected by the write guard, not by a copy: nothing outside the scenario
ever changes these columns, so there is nothing for an approval to freeze); a new entry in
`SCENARIO_CHILD_COPIERS` (point 3 of the same aneks — the columns travel with the position row the
existing copier already copies by reflection); a new ADR-0007 concurrency token (point 4 — the
position's `updated_at` already covers every column of this row).

5. **`NOT VALID` + `VALIDATE CONSTRAINT` considered and rejected for these five `CHECK`s** (Reviewer
   finding R-01, bramka 1 SC-5-03) — `migrations/env.py` wraps this migration's `upgrade()` in one
   transaction, so the split buys nothing here, exactly as `c8e2a4f61d93` already found for the same
   shape of migration; the plain `ADD CONSTRAINT ... CHECK (...)` below is deliberate, not an
   oversight. R-01 is tracked as a named exception for this whole class of migration
   (`a8f18e00172b`, `d2f6a91c4b58`, `4f0a9c1b7d62`), to be closed by a separate task that gives
   `env.py` more than one transaction per migration.

Revision ID: a8f18e00172b
Revises: 9b3f6a1d0c47
Create Date: 2026-09-25

**Repointed after merging `origin/main`** (SC-5-02 landed in the meantime and forked its own
migration chain from the same parent, `d2f6a91c4b58`): `down_revision` moved from `d2f6a91c4b58` to
`9b3f6a1d0c47`, the new tip of that chain (`d2f6a91c4b58 -> b7e3f19a6c52 -> b9e3c7a1f264 ->
9b3f6a1d0c47`), so Alembic has one head again. Content unchanged, same linearisation pattern as
`dc9c9b4` ("SC-5-02: zlinearyzuj migracje po merge z main (SC-4-05)").
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a8f18e00172b"
down_revision: str | None = "9b3f6a1d0c47"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LOCK_TIMEOUT = "3s"
"""The same bound as every migration since `d5e94a1c6b73` (R-06): `ALTER TABLE ... ADD COLUMN` with
a constant default and `ADD CONSTRAINT ... CHECK` both take `ACCESS EXCLUSIVE` for their statement's
duration on a table other sessions read."""

_TABLE = "staffing_position"

_COST_BASIS_WORKED_TIME = "worked_time"

_COST_BASIS_KNOWN_EXPRESSION = "cost_basis IN ('worked_time', 'fixed_amount')"
_FIXED_AMOUNT_REQUIRES_ITS_OWN_BASIS_EXPRESSION = (
    "cost_basis <> 'fixed_amount' OR "
    "(fixed_amount IS NOT NULL AND fixed_amount_currency IS NOT NULL)"
)
_FIXED_AMOUNT_POSITIVE_EXPRESSION = "fixed_amount IS NULL OR fixed_amount > 0"
_FIXED_AMOUNT_CURRENCY_ISO4217_EXPRESSION = (
    "fixed_amount_currency IS NULL OR char_length(fixed_amount_currency) = 3"
)
_FIXED_AMOUNT_CURRENCY_IS_UPPER_EXPRESSION = (
    "fixed_amount_currency IS NULL OR fixed_amount_currency = upper(fixed_amount_currency)"
)
"""Spelled here as well as in `app.models.staffing` rather than imported from it — the same
convention every migration since `f3a1d0c58b27` keeps, and the accompanying schema-drift test keeps
the two copies honest."""


def upgrade() -> None:
    op.execute(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT}'")

    op.add_column(
        _TABLE,
        sa.Column(
            "cost_basis",
            sa.String(length=20),
            nullable=False,
            server_default=_COST_BASIS_WORKED_TIME,
        ),
    )
    op.add_column(
        _TABLE, sa.Column("fixed_amount", sa.Numeric(precision=14, scale=4), nullable=True)
    )
    op.add_column(
        _TABLE, sa.Column("fixed_amount_currency", sa.String(length=3), nullable=True)
    )

    op.create_check_constraint(
        op.f(f"ck_{_TABLE}_cost_basis_known"), _TABLE, _COST_BASIS_KNOWN_EXPRESSION
    )
    op.create_check_constraint(
        op.f(f"ck_{_TABLE}_fixed_amount_required_for_its_basis"),
        _TABLE,
        _FIXED_AMOUNT_REQUIRES_ITS_OWN_BASIS_EXPRESSION,
    )
    op.create_check_constraint(
        op.f(f"ck_{_TABLE}_fixed_amount_positive"), _TABLE, _FIXED_AMOUNT_POSITIVE_EXPRESSION
    )
    op.create_check_constraint(
        op.f(f"ck_{_TABLE}_fixed_amount_currency_iso4217"),
        _TABLE,
        _FIXED_AMOUNT_CURRENCY_ISO4217_EXPRESSION,
    )
    op.create_check_constraint(
        op.f(f"ck_{_TABLE}_fixed_amount_currency_is_upper"),
        _TABLE,
        _FIXED_AMOUNT_CURRENCY_IS_UPPER_EXPRESSION,
    )

    op.execute("SET LOCAL lock_timeout = DEFAULT")


def downgrade() -> None:
    """Reverse of `upgrade`. Data loss by definition, like every downgrade of an expand migration:
    a position costed by a fixed amount goes back to a schema with nowhere to keep it."""
    op.execute(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT}'")

    op.drop_constraint(
        op.f(f"ck_{_TABLE}_fixed_amount_currency_is_upper"), _TABLE, type_="check"
    )
    op.drop_constraint(
        op.f(f"ck_{_TABLE}_fixed_amount_currency_iso4217"), _TABLE, type_="check"
    )
    op.drop_constraint(op.f(f"ck_{_TABLE}_fixed_amount_positive"), _TABLE, type_="check")
    op.drop_constraint(
        op.f(f"ck_{_TABLE}_fixed_amount_required_for_its_basis"), _TABLE, type_="check"
    )
    op.drop_constraint(op.f(f"ck_{_TABLE}_cost_basis_known"), _TABLE, type_="check")

    op.drop_column(_TABLE, "fixed_amount_currency")
    op.drop_column(_TABLE, "fixed_amount")
    op.drop_column(_TABLE, "cost_basis")

    op.execute("SET LOCAL lock_timeout = DEFAULT")
