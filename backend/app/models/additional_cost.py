"""The additional costs of a scenario — hardware, licences, cloud, travel (F-08, SC-5-05; ADR-0014).

One table, and every property below is a decision of ADR-0014 (accepted at gate 1 of SC-5-05) or of
its dated addenda in ADR-0004/0005/0007/0008, not a choice made here:

1. **The scenario owns the cost** (point 1). `scenario_id` is `NOT NULL`, with no `ON DELETE` action
   (ADR-0003, point 1: a cascade would be a second, unguarded way for the rows of an `approved`
   scenario to disappear). A "project" cost of F-08 is a cost of the scenario with no position
   (Q-2 = A) — there is no project-level table, and every scenario carries its own copy.
2. **An optional staffing position of the same scenario, enforced by the database** (point 1). The
   composite foreign key `(position_id, scenario_id) → staffing_position (id, scenario_id)` makes "a
   position of another scenario" a row the database refuses — the construction SC-4-01 used for type
   agreement (`fk_tm_terms_commercial_terms_model_type`). `MATCH SIMPLE` (PostgreSQL's default)
   skips the check when `position_id` is `NULL`, which is exactly the scenario-level cost.
3. **A fixed amount, strictly positive** (points 5 and 6; Q-1 = A, G-1 = A). `NUMERIC(14,4)`: input
   precision above the currency's minor unit, never rounded on the way in (ADR-0008, point 6).
   `CHECK amount > 0` — a zero row has no reason to exist and a credit is a different, undesigned
   mechanism.
4. **Month granularity, closed periods, type and shape agreeing in the database** (point 3;
   ADR-0008, addendum SC-5-05, point 2). A one-off cost is one month (`end_month IS NULL`); a
   recurring cost is a closed `[start_month, end_month]` range, never open-ended. Both ends are
   first days of a month. The recurring amount is the amount **of every month** of the range
   (Q-3 = A) — nothing here or downstream divides it.
5. **Not a consumer of ADR-0008** (point 4; ADR-0008, addendum SC-5-05, point 1): two costs of one
   category in one month are both true and both counted, so there is no `EXCLUDE`. The absence is
   the decision.
6. **`funding_source` — who carries the cost** (point 9): `internal` or `rebilled_to_client`. The
   word *vendor* is deliberately absent: in this repository it means a subcontractor
   (`catalog_vendors`). A stored attribute only (Q-5 = A) — both values enter the sum of additional
   costs, and neither touches the revenue.
7. **Its own concurrency marker** (ADR-0007, addendum 2026-09-23 SC-5-05): `updated_at`, per cost
   row. A cost may exist without a position, so it cannot borrow the position's token the way an
   absence does; two costs of one position are edited independently.
8. **Group 2 of ADR-0004** (addendum SC-5-05, point 1): own data of the scenario, protected after an
   approval by the refusal of a write (`app.data.additional_cost`, through
   `app.data.scenario_guard`), not by a snapshot. The category is group 1 — a label, read live.

**What is deliberately absent: a free-text column** (a description, a note, a person). None was
decided, and a cost attached to a `headcount = 1` position is already indirectly about one person
(ADR-0005, addendum SC-5-05, point 2) — a text field would make that direct without the decision
that governs personal data.
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
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

AMOUNT_PRECISION = 14
AMOUNT_SCALE = 4
"""`NUMERIC(14,4)` — the scale of every rate in this schema, larger than any currency's minor unit
(ADR-0008, point 6; ADR-0014, point 6). An amount is *input*: `12.3456` is stored as `12.3456`, and
the one rounding happens at the end of the sum (`app.core.money.round_money`)."""

COST_TYPE_ONE_OFF = "one_off"
COST_TYPE_RECURRING = "recurring"
COST_TYPES: tuple[str, ...] = (COST_TYPE_ONE_OFF, COST_TYPE_RECURRING)

FUNDING_INTERNAL = "internal"
FUNDING_REBILLED_TO_CLIENT = "rebilled_to_client"
FUNDING_SOURCES: tuple[str, ...] = (FUNDING_INTERNAL, FUNDING_REBILLED_TO_CLIENT)

COST_TYPE_KNOWN_EXPRESSION = "cost_type IN ('one_off', 'recurring')"
FUNDING_SOURCE_KNOWN_EXPRESSION = "funding_source IN ('internal', 'rebilled_to_client')"
RECURRING_PERIOD_CLOSED_EXPRESSION = "cost_type <> 'recurring' OR end_month IS NOT NULL"
ONE_OFF_SINGLE_MONTH_EXPRESSION = "cost_type <> 'one_off' OR end_month IS NULL"
PERIOD_ORDERED_EXPRESSION = "end_month IS NULL OR end_month >= start_month"
START_MONTH_IS_FIRST_EXPRESSION = "start_month = date_trunc('month', start_month)"
END_MONTH_IS_FIRST_EXPRESSION = "end_month IS NULL OR end_month = date_trunc('month', end_month)"
"""The CHECK expressions as SQL, each spelled once here and once in migration `a3d9e6f20c71`, and
asserted identical to that copy by `tests/test_additional_cost_schema.py` (the drift guard R-02
introduced for the catalogue). One constraint per rule rather than one conjunction, so a refusal
names *which* rule was broken (`describe_without_values` keeps the constraint name) and dropping any
one of them is a mutation a test can kill (criterion K-03)."""

POSITION_SAME_SCENARIO_FOREIGN_KEY = "fk_additional_cost_position_same_scenario"
"""The composite foreign key that makes "a position of this cost's scenario" a property of the
database (ADR-0014, point 1; criterion K-03). Named explicitly and spelled once, so the tests prove
a refusal came from *this* mechanism."""

CATEGORY_FOREIGN_KEY = "fk_additional_cost_category_id"

RISK_SAME_SCENARIO_FOREIGN_KEY = "fk_additional_cost_risk_same_scenario"
"""The composite foreign key `(risk_id, scenario_id) -> scenario_risk (id, scenario_id)` (SC-6-08;
ADR-0021, point 7): the mirror of the position key above, for the declared-risk link."""


class AdditionalCost(Base):
    """One additional cost of one scenario: a category, a fixed amount, a currency, a period."""

    __tablename__ = "additional_cost"

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    scenario_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("scenarios.id", name="fk_additional_cost_scenario_id"),
        nullable=False,
        index=True,
    )
    """The owner (ADR-0014, point 1). Indexed: "the costs of this scenario" is the lookup of every
    read and of the scenario-level copier."""

    position_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), nullable=True, index=True
    )
    """`NULL` — a cost of the scenario as a whole (the "project" cost of F-08, Q-2 = A). Otherwise a
    position of **the same** scenario, which the composite foreign key below enforces. Indexed: the
    staffing copier reads "the costs of these positions"."""

    risk_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), nullable=True, index=True
    )
    """The declared risk this cost event represents, or `NULL` (SC-6-08; ADR-0021, points 1 and 7).
    A risk **of the same scenario**, by the composite foreign key `fk_additional_cost_risk_same_
    scenario`; no `ON DELETE` action, so a risk with a cost event pointing at it cannot be deleted
    (Q-8 = A). The link decides nothing in the sum - a linked and an unlinked cost are added up
    identically (ADR-0021, point 3)."""

    category_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("catalog_cost_categories.id", name=CATEGORY_FOREIGN_KEY),
        nullable=False,
        index=True,
    )
    """From the dictionary only. No `ON DELETE` action, so the database refuses to delete a category
    while any cost points at it (ADR-0014, point 2); the index serves that check."""

    amount: Mapped[Decimal] = mapped_column(
        Numeric(AMOUNT_PRECISION, AMOUNT_SCALE), nullable=False
    )
    """For a one-off cost, the amount of its one month; for a recurring cost, the amount of **each**
    month of its range (ADR-0014, point 3, Q-3 = A) — never a total to be divided."""

    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    """ISO 4217, on every cost (F-08). No conversion anywhere (ADR-0006): costs in two currencies
    are the named `currency_mismatch` state, never a sum."""

    cost_type: Mapped[str] = mapped_column(String(20), nullable=False)
    """`one_off` / `recurring` — a string closed by a CHECK, not a PostgreSQL enum, for the reason
    `commercial_terms.model_type` gives (a widened CHECK is one statement inside the migration's
    transaction; `ALTER TYPE … ADD VALUE` is not)."""

    # Calendar months as dates, first day of the month (invariant-guardian rule 15): a cost month
    # has no timezone and no clock.
    start_month: Mapped[date] = mapped_column(Date, nullable=False)
    """The month of a one-off cost, or the first month of a recurring one."""

    end_month: Mapped[date | None] = mapped_column(Date, nullable=True)
    """The last month of a recurring cost, **inclusive** — never `NULL` for one (the database
    refuses an open-ended recurring cost; ADR-0008, addendum SC-5-05, point 2). Always `NULL` for a
    one-off cost, so "one month" has one spelling."""

    funding_source: Mapped[str] = mapped_column(String(30), nullable=False)
    """`internal` / `rebilled_to_client` (ADR-0014, point 9)."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    """ADR-0007's concurrency marker for **this cost row** (addendum 2026-09-23 SC-5-05). Always the
    database's clock (`now()` in the statement that writes), never this process's."""

    __table_args__ = (
        ForeignKeyConstraint(
            ["position_id", "scenario_id"],
            ["staffing_position.id", "staffing_position.scenario_id"],
            name=POSITION_SAME_SCENARIO_FOREIGN_KEY,
        ),
        ForeignKeyConstraint(
            ["risk_id", "scenario_id"],
            ["scenario_risk.id", "scenario_risk.scenario_id"],
            name=RISK_SAME_SCENARIO_FOREIGN_KEY,
        ),
        CheckConstraint("amount > 0", name="amount_positive"),
        # The same two rules `catalog_default_rates` carries for its currency.
        CheckConstraint("char_length(currency) = 3", name="currency_iso4217"),
        CheckConstraint("currency = upper(currency)", name="currency_is_upper"),
        CheckConstraint(COST_TYPE_KNOWN_EXPRESSION, name="cost_type_known"),
        CheckConstraint(FUNDING_SOURCE_KNOWN_EXPRESSION, name="funding_source_known"),
        CheckConstraint(START_MONTH_IS_FIRST_EXPRESSION, name="start_month_is_first_of_month"),
        CheckConstraint(END_MONTH_IS_FIRST_EXPRESSION, name="end_month_is_first_of_month"),
        CheckConstraint(RECURRING_PERIOD_CLOSED_EXPRESSION, name="recurring_period_closed"),
        CheckConstraint(ONE_OFF_SINGLE_MONTH_EXPRESSION, name="one_off_single_month"),
        CheckConstraint(PERIOD_ORDERED_EXPRESSION, name="period_ordered"),
    )
