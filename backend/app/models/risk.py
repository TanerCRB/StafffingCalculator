"""A declared risk of a scenario, and its reserve representation (F-09 pt 4-5, SC-6-08; ADR-0021).

Two tables, and every property below is a decision of ADR-0021 (accepted at gate 1 of SC-6-08,
Q-1..Q-8) or of its dated addenda, not a choice made here:

1. **`scenario_risk` - a declared identity** (ADR-0021, point 1, Q-2 = A). A risk is a name the user
   declared, per scenario. "The same risk" is never inferred from a category, a month or an amount:
   a risk is *double-represented* exactly when at least one cost event (an ADR-0014 additional-cost
   row) and at least one reserve point at the same risk row. `UNIQUE (scenario_id, name)` is both a
   sanity rule and the remap key of the copy (point 8, Q-7 = A).
2. **`risk_reserve` - its own table** (point 2, Q-1 = A). A reserve is *not* a row of
   `additional_cost` with a discriminator: every existing reader of that table (`/results`,
   `/compare`, what-if, the copiers) is unchanged, and the total of a reserve is never inside
   `additional_cost` (point 3, Q-3 = A).
3. **The link is a composite foreign key** (point 7). `(risk_id, scenario_id) -> scenario_risk
   (id, scenario_id)` on the reserve, and the same on `additional_cost` (see
   `app.models.additional_cost`): a link to a risk of another scenario is a row the database
   refuses - the ADR-0014 point 1 construction. `MATCH SIMPLE` skips the check when `risk_id` is
   `NULL`: a representation with no declared risk is legal (and can never be signalled - a named
   limitation). **No `ON DELETE` action anywhere** (Q-8 = A): deleting a risk that a cost event or a
   reserve still points at is refused by the database; the user unlinks first.
4. **A reserve is a fixed amount in one currency** (point 4, Q-4 = A) - `NUMERIC(14,4)`, `> 0`,
   month-granular one-off/recurring with a closed range, exactly the shape rules of ADR-0014 pt 3-6.
   No probability, no percentage, no `funding_source`, no category (none was decided).
5. **Its own concurrency marker per row** (point 10; ADR-0007 addendum SC-6-08): `updated_at`.
6. **Group 2 of ADR-0004** (point 6): own data of the scenario, a write refused under `approved`
   in the statement that writes (`app.data.risk`, `app.data.risk_reserve`), no snapshot.

**What is deliberately absent: a free-text column** (a description, a note, a person) - none was
decided, and personal data enters this schema only through a decision that governs it (ADR-0005,
addendum SC-5-05, point 2), and **no `position_id`**: a risk and a reserve are scenario-level, so
the `headcount = 1` exposure named in ADR-0014 point 11 is not widened (point 9).
"""

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.additional_cost import (
    AMOUNT_PRECISION,
    AMOUNT_SCALE,
)
from app.models.additional_cost import (
    RISK_SAME_SCENARIO_FOREIGN_KEY as COST_RISK_SAME_SCENARIO_FOREIGN_KEY,
)

RESERVE_TYPE_ONE_OFF = "one_off"
RESERVE_TYPE_RECURRING = "recurring"
RESERVE_TYPES: tuple[str, ...] = (RESERVE_TYPE_ONE_OFF, RESERVE_TYPE_RECURRING)

NAME_NOT_BLANK_EXPRESSION = "name ~ '[^[:space:]]'"
RESERVE_TYPE_KNOWN_EXPRESSION = "reserve_type IN ('one_off', 'recurring')"
RECURRING_PERIOD_CLOSED_EXPRESSION = "reserve_type <> 'recurring' OR end_month IS NOT NULL"
ONE_OFF_SINGLE_MONTH_EXPRESSION = "reserve_type <> 'one_off' OR end_month IS NULL"
PERIOD_ORDERED_EXPRESSION = "end_month IS NULL OR end_month >= start_month"
START_MONTH_IS_FIRST_EXPRESSION = "start_month = date_trunc('month', start_month)"
END_MONTH_IS_FIRST_EXPRESSION = "end_month IS NULL OR end_month = date_trunc('month', end_month)"
"""The CHECK expressions as SQL, each spelled once here and once in the migration, and asserted
identical to that copy by `tests/test_risk_schema.py` (the drift guard). One constraint per rule, so
a refusal names which rule was broken and dropping any one of them is a mutation a test can kill."""

RISK_ID_SCENARIO_UNIQUE = "uq_scenario_risk_id_scenario_id"
"""The parent half of both composite foreign keys (ADR-0021, point 7). Redundant as a uniqueness
claim (`id` is the primary key) and required anyway: PostgreSQL accepts a composite foreign key only
against a unique constraint on exactly the referenced columns."""

RISK_SCENARIO_NAME_UNIQUE = "uq_scenario_risk_scenario_id_name"
RESERVE_RISK_SAME_SCENARIO_FOREIGN_KEY = "fk_risk_reserve_risk_same_scenario"
"""Named explicitly and spelled once (the cost-side name lives in `app.models.additional_cost` and
is re-exported here as `COST_RISK_SAME_SCENARIO_FOREIGN_KEY`), so a test proves a refusal came from
*this* mechanism."""

__all__ = [
    "COST_RISK_SAME_SCENARIO_FOREIGN_KEY",
    "RESERVE_RISK_SAME_SCENARIO_FOREIGN_KEY",
    "RISK_ID_SCENARIO_UNIQUE",
    "RiskReserve",
    "ScenarioRisk",
]


class ScenarioRisk(Base):
    """One declared risk of one scenario - a name, and nothing else (ADR-0021, point 1)."""

    __tablename__ = "scenario_risk"

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    scenario_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("scenarios.id", name="fk_scenario_risk_scenario_id"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    """ADR-0007's marker for **this risk row** - always the database's clock."""

    __table_args__ = (
        CheckConstraint(NAME_NOT_BLANK_EXPRESSION, name="name_not_blank"),
        UniqueConstraint("id", "scenario_id", name=RISK_ID_SCENARIO_UNIQUE),
        UniqueConstraint("scenario_id", "name", name=RISK_SCENARIO_NAME_UNIQUE),
    )


class RiskReserve(Base):
    """One reserve of one scenario: a fixed amount, a currency, a period, an optional risk."""

    __tablename__ = "risk_reserve"

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    scenario_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("scenarios.id", name="fk_risk_reserve_scenario_id"),
        nullable=False,
        index=True,
    )
    risk_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), nullable=True, index=True
    )
    """`NULL` - a reserve with no declared risk (legal, never signalled). Otherwise a risk of **the
    same** scenario, which the composite foreign key enforces. Indexed: the risk read counts the
    reserves of each risk, and the check on deleting a risk uses it."""

    amount: Mapped[Decimal] = mapped_column(Numeric(AMOUNT_PRECISION, AMOUNT_SCALE), nullable=False)
    """For a one-off reserve the amount of its one month; for a recurring one the amount of **each**
    month of its range - never a total to divide (ADR-0021, point 4; ADR-0014, point 3)."""

    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    reserve_type: Mapped[str] = mapped_column(String(20), nullable=False)
    start_month: Mapped[date] = mapped_column(Date, nullable=False)
    end_month: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["risk_id", "scenario_id"],
            ["scenario_risk.id", "scenario_risk.scenario_id"],
            name=RESERVE_RISK_SAME_SCENARIO_FOREIGN_KEY,
        ),
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint("char_length(currency) = 3", name="currency_iso4217"),
        CheckConstraint("currency = upper(currency)", name="currency_is_upper"),
        CheckConstraint(RESERVE_TYPE_KNOWN_EXPRESSION, name="reserve_type_known"),
        CheckConstraint(START_MONTH_IS_FIRST_EXPRESSION, name="start_month_is_first_of_month"),
        CheckConstraint(END_MONTH_IS_FIRST_EXPRESSION, name="end_month_is_first_of_month"),
        CheckConstraint(RECURRING_PERIOD_CLOSED_EXPRESSION, name="recurring_period_closed"),
        CheckConstraint(ONE_OFF_SINGLE_MONTH_EXPRESSION, name="one_off_single_month"),
        CheckConstraint(PERIOD_ORDERED_EXPRESSION, name="period_ordered"),
    )
