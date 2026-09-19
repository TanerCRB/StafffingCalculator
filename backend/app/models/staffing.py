"""Scenario staffing: one anonymous position per role tuple, with a monthly grid of hours (F-04).

Two tables, one aggregate. `staffing_position` is a child of `scenarios`; its monthly allocation
rows are grandchildren, reachable only through a position. That shape decides four things, each of
them written down in an accepted decision rather than chosen here:

1. **Own data of the scenario, not an inherited value** (ADR-0004, addendum 2026-09-19 "pozycja
   obsady i alokacja nie wchodzą do migawki"). Nothing outside the scenario can change these rows,
   so there is nothing for an approval snapshot to freeze: what protects them after approval is the
   refusal of a write (`app.data.staffing`), not a second copy of the rows.
2. **Scope is inherited through the scenario** (ADR-0001, addendum 2026-09-19; ADR-0005, addendum
   2026-09-19 "pozycje obsady"). `scenario_id → scenarios.project_id` ties every row to a project,
   so the `project_access` filter applies — and this is the first table in the repository for which
   it applies *indirectly*. The catalogue's exemption ("a row belonging to no project") explicitly
   does not stretch here.
3. **No effective-range pattern.** ADR-0008 is deliberately *not* applied (ADR-0004, addendum
   2026-09-19): two positions for the same role over the same months are legal — a PM plans
   different people on one role at different times, and an overlap is a state to see, not a write to
   refuse. The only uniqueness in this module is `UNIQUE (position_id, period_month)` on the
   allocation row. The absence of an `EXCLUDE` constraint here is correctness, not an omission.
4. **The concurrency token lives on the position** (ADR-0007, addendum 2026-09-19): one
   `updated_at` for the whole month grid of one position, because the grid is edited as a unit. The
   allocation row therefore has no `updated_at` of its own — and must not grow one without
   revisiting that decision, because two tokens on one edit path is a question ADR-0007 does not
   answer.

**Singular table names**, unlike `projects`/`scenarios`: `staffing_position` and
`staffing_position_allocation` are the names the binding decisions use (ADR-0005's addendum quotes
`staffing_position.scenario_id`, ADR-0007's quotes `staffing_position.updated_at`), and a table
whose name differs from the decision that created it cannot be found by reading the decision.

**What is deliberately absent: any cost, rate or amount of money.** A position carries a dimension
tuple, a headcount, its own period and hours — nothing a currency could be attached to (ADR-0005,
addendum 2026-09-19, point 5). The conjunction gate of SC-1-08 is therefore not activated by this
table, and the first task that does put a resolved rate on a position (F-07, plan block 5) has to
prove that gate with a criterion of its own.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.scenario import Scenario

HOURS_PRECISION = 10
HOURS_SCALE = 2
"""`NUMERIC(10,2)` for every hour figure — `Decimal`, never `float` (NF-01, ADR-0002).

Hours are not money, but they are one multiplication away from it: a `float` here would put a
binary rounding error into every cost and revenue figure derived from this row, which is the exact
failure ADR-0002 forbids on a money path *or next to one*. Scale 2 because half-hours and quarters
are what a plan is written in; precision 10 leaves room for the sum of a whole position (the hours
of a `headcount` > 1 position are its total, not per head — gate-1 decision 1)."""

FIRST_DAY_OF_MONTH_EXPRESSION = "period_month = date_trunc('month', period_month)"
"""What makes `period_month` a month rather than a day, as SQL — spelled once.

Load-bearing for `UNIQUE (position_id, period_month)` and not merely tidy: without it,
`2026-03-01` and `2026-03-15` are two different `DATE` values, the unique constraint accepts both,
and "one allocation per calendar month" quietly becomes "one allocation per day somebody happened
to type". The truncation is a *refusal*, not a normalisation — a request naming the 15th is a
client bug worth surfacing, for the same reason `Iso4217Code` refuses `"eur"` instead of
upper-casing it.

Copied into the migration that creates the table (a migration must keep describing the schema it
produced), and asserted identical to that copy by
`tests/test_staffing_schema_constraints.py::test_the_model_and_the_migration_agree_on_the_month_check`
— the drift guard R-02 added for the catalogue's generated column."""

HOURS_COLUMNS: tuple[str, ...] = (
    "availability_hours",
    "planned_allocation_hours",
    "billable_hours",
)
"""The three hour figures of one month, as data — because they are three independent inputs and
every mechanism that treats them as a group (the non-negativity constraints, the request schema, the
copier) has to cover all three or be visibly incomplete (criterion K-05).

None of the three is derived from another: availability is how much time the position has,
planned allocation is how much of it the plan uses, billable is how much of that the client pays
for. Deriving any one of them (billable := planned, availability := planned) is the mutation K-05
exists to kill — and the reason they are three columns rather than one column plus two ratios."""

HOURS_NON_NEGATIVE_CONSTRAINTS: dict[str, str] = {
    column: f"{column.removesuffix('_hours')}_non_negative" for column in HOURS_COLUMNS
}
"""Column → the name of the CHECK constraint that keeps it non-negative, spelled once.

The names drop the `_hours` suffix for a reason that is not style: PostgreSQL truncates an
identifier at 63 characters, and the naming convention already spends 32 of them on
`ck_staffing_position_allocation_`. `…_planned_allocation_hours_non_negative` would be cut to a name
nobody can read back to its meaning — and a *test* asserting on the name it expected would fail for
a reason that looks like the constraint being missing (measured while writing K-04c).

Referenced by the tests that prove a refusal came from *this* mechanism rather than from something
else that happened to fail, so a rename is a single edit rather than a search. The migration spells
the same three names out, and a test asks the migrated database whether all three are really there —
which is what would catch a truncation returning by the back door."""


class StaffingPosition(Base):
    """One anonymous staffing position of one scenario: a dimension tuple, a headcount, a period.

    Anonymous on purpose (F-03/F-04): there is no person here and no column for one. A named
    assignment is Issue #31, blocked on the authentication ADR, and adding a name to this table
    would make it a personal-data table without the decision that governs one.
    """

    __tablename__ = "staffing_position"

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    # No `ondelete` — i.e. `NO ACTION` — on any foreign key in this module. Nothing in this task
    # deletes a scenario, a position or a dimension entry (deletion is out of scope, and named as
    # such in the plan entry), so the database refusing to orphan a row is the option that pre-empts
    # no later decision. `ON DELETE CASCADE` towards the scenario would additionally be a second,
    # unguarded way for the rows of an `approved` scenario to disappear: the write guard in
    # `app.data.staffing` is on `INSERT`/`UPDATE`, and a cascade is neither.
    scenario_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("scenarios.id", name="fk_staffing_position_scenario_id"),
        nullable=False,
        index=True,
    )

    # The full catalogue tuple, all four `NOT NULL`, exactly as it keys a rate
    # (`app.models.catalog.RATE_DIMENSION_COLUMNS`): a nullable dimension would mean "any", which is
    # a rate-resolution rule nobody decided (rule 13 of the Invariant Guardian). The foreign keys
    # carry explicit names for the reason `catalog_default_rates` documents — the derived name for
    # `engagement_type_id` exceeds PostgreSQL's 63-character identifier limit and gets truncated
    # with a hash appended.
    role_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("catalog_roles.id", name="fk_staffing_position_role_id"),
        nullable=False,
    )
    seniority_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("catalog_seniorities.id", name="fk_staffing_position_seniority_id"),
        nullable=False,
    )
    location_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("catalog_locations.id", name="fk_staffing_position_location_id"),
        nullable=False,
    )
    engagement_type_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey(
            "catalog_engagement_types.id", name="fk_staffing_position_engagement_type_id"
        ),
        nullable=False,
    )

    headcount: Mapped[int] = mapped_column(Integer, nullable=False)
    """How many people this position plans for. An integer, not a `Decimal`: a fraction of a person
    is an allocation figure (hours), not a headcount, and FTE as a unit is out of scope until a
    working calendar exists to convert it (F-05, Issue #7)."""

    # Calendar dates, not points in time (invariant-guardian rule 15): "this position starts on
    # 1 March" has no timezone and no clock.
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    """`NULL` means open-ended — the position runs for as long as the scenario does. Not a
    `9999-12-31` sentinel, for the same reason ADR-0008 gives for `effective_to`: a sentinel is a
    second spelling of "unbounded" that no query treats as one."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    """ADR-0007's concurrency token for the whole position, its month grid included (addendum
    2026-09-19). The value is always the *database's* clock — `func.now()` and, on the allocation
    edit path, an explicit `now()` in the statement — never this process's, so two application
    instances cannot disagree about which write came last."""

    allocations: Mapped[list["StaffingPositionAllocation"]] = relationship(
        back_populates="position",
        order_by="StaffingPositionAllocation.period_month",
        cascade="all, delete-orphan",
        passive_deletes=False,
    )
    """Ordered by month, because a grid read back in arbitrary order is a grid nobody can compare
    with the one they sent. `delete-orphan` is an ORM-level statement about the aggregate (an
    allocation with no position is not a row that means anything), not a database cascade — the
    foreign key stays `NO ACTION`, so nothing deletes a position that still has months."""

    scenario: Mapped["Scenario"] = relationship()

    __table_args__ = (
        # In the database, not only in the request schema: a headcount of 0 or -1 written by a
        # fixture, a seed script or a future import is the same defect as one written by a client.
        CheckConstraint("headcount > 0", name="headcount_positive"),
        # The same ordering rule as `scenario_period_ordered` and the catalogue's
        # `effective_period_ordered`, for the same reason — an inverted period is not a period.
        CheckConstraint(
            "end_date IS NULL OR end_date >= start_date", name="position_period_ordered"
        ),
    )


class StaffingPositionAllocation(Base):
    """One calendar month of one position: available, planned and billable hours (F-04).

    Three independent figures, no derivation between them (see `HOURS_COLUMNS`), and a month with
    zero hours is data rather than an error: a gap in the middle of a position's period is how a
    plan says "nobody works here that month", so `0` is accepted and only a *negative* figure is
    refused.
    """

    __tablename__ = "staffing_position_allocation"

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    position_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("staffing_position.id", name="fk_staffing_position_allocation_position_id"),
        nullable=False,
    )
    """No `index=True` (R-05, reviewer 2026-09-19): `UNIQUE (position_id, period_month)` below
    already creates a btree whose leading column is this one, so `WHERE position_id = ?` — the only
    lookup this table has — rides on it. A second index would be a write on every insert and edit of
    a month row for no query of its own. `StaffingPosition.scenario_id` does keep its index: that
    column has no unique constraint to ride on."""

    period_month: Mapped[date] = mapped_column(Date, nullable=False)
    """The first day of the month this row is about — a `DATE` truncated to the month by the CHECK
    constraint below, not a free-form day (see `FIRST_DAY_OF_MONTH_EXPRESSION`). Monthly
    granularity is the gate-1 decision: delivery phases as a dimension need an entity that does not
    exist yet (F-02, Issue #4)."""

    availability_hours: Mapped[Decimal] = mapped_column(
        Numeric(HOURS_PRECISION, HOURS_SCALE), nullable=False
    )
    """How many hours this position has available that month — entered by hand. **Named risk:**
    until F-05 (calendars, absences — Issue #7) nothing in the system knows about a single day off,
    so this figure is an assertion by the planner and not a derived capacity."""

    planned_allocation_hours: Mapped[Decimal] = mapped_column(
        Numeric(HOURS_PRECISION, HOURS_SCALE), nullable=False
    )
    """How many hours the plan uses. May exceed `availability_hours`: over-allocation is a legal
    state to be shown, never a write to refuse (gate-1 out of scope, point 5 — the warning needs a
    threshold from F-02 and must not be implemented as a CHECK constraint)."""

    billable_hours: Mapped[Decimal] = mapped_column(
        Numeric(HOURS_PRECISION, HOURS_SCALE), nullable=False
    )
    """How many of the planned hours the client pays for. Not derived from the planned figure and
    not a percentage of it: the cost of non-billable effort is F-07 (Issue #9), and it needs this
    number to be an input rather than a rounding of another one."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # No `updated_at` here: the concurrency token is the position's (ADR-0007, addendum
    # 2026-09-19). A second token on this row would make one request carry N tokens and would need
    # a rule for partial refusal that ADR-0007 does not have.

    position: Mapped["StaffingPosition"] = relationship(back_populates="allocations")

    __table_args__ = (
        # One allocation per (position, calendar month). Together with the CHECK below — never
        # without it, see `FIRST_DAY_OF_MONTH_EXPRESSION`.
        UniqueConstraint("position_id", "period_month"),
        CheckConstraint(FIRST_DAY_OF_MONTH_EXPRESSION, name="period_month_is_first_of_month"),
        # One constraint per column rather than one conjunction over the three: the refusal then
        # names *which* figure was negative (`describe_without_values` keeps the constraint name and
        # drops the values — NF-11), and dropping one of the three is a mutation a test can kill.
        *(
            CheckConstraint(f"{column} >= 0", name=constraint)
            for column, constraint in HOURS_NON_NEGATIVE_CONSTRAINTS.items()
        ),
    )
