"""Reading and writing the leave budget, and finding the type it settles against (F-05, SC-3-03).

**No guard function here, and that absence is decided rather than convenient** (ADR-0001, addendum
2026-09-22, point 1, extended by ADR-0005, addendum 2026-09-22 SC-3-03, point 1).
`app.data.project_reads.project_for_caller` exists so that a *scope predicate* cannot be forgotten;
a budget row belongs to no project, no user and no tenant, so there is no predicate to forget. Its
two key columns point at other organisational rows — the same reading `vendor_id` and
`catalog_locations.calendar_id` already have — and no function here narrows anything by the
caller's identity. The expiry condition is unchanged and structural: the first *per-caller*
predicate on any of these tables ends the exemption for all of them.

**No overlap pre-check, in Python or anywhere else.** The refusal is the `EXCLUDE` constraint,
evaluated by the database inside the `INSERT` (ADR-0008, addendum 2026-09-22 SC-3-03, point 1). A
`SELECT` asking "does an overlapping window exist?" before the write is check-then-act: a competing
connection committing between the two statements produces two overlapping budgets for one pair, and
no single-connection test can see it. That mutation has survived delivered tests three times in this
repository, which is why criterion K-01 requires the two-connection race.

**Which window covers which month is resolved by the database**, with `valid_period @> :month`
against the generated column — the same expression the `EXCLUDE` reads (ADR-0008, point 3). There
is no Python comparison of `effective_from`/`effective_to` anywhere on this path, because a second
spelling of "covers this day" is how a constraint and a lookup end up disagreeing by one day, and
because "the most recent window that has started" is the mutation criterion K-06 exists to kill.

**What this module produces for the calculation is a value object, not an ORM row**
(`app.domain.absence_budget.AbsenceBudget`), for the reason `app.data.working_calendar` gives: the
same shape has to come out of the live table and out of the approval snapshot — the latter since
SC-5-06 (`frozen_budgets_for_months`, `frozen_statutory_leave_type`), the first reader of those
frozen rows.
"""

import uuid
from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.data.write_errors import WriteFailed, WriteRefused, failure_for
from app.domain.absence_budget import AbsenceBudget as BudgetValue
from app.domain.absence_budget import StatutoryLeaveType
from app.models.approved_snapshot import (
    ApprovedSnapshotAbsenceBudget,
    ApprovedSnapshotAbsenceType,
)
from app.models.catalog import VALID_PERIOD_EXPRESSION, AbsenceBudget, AbsenceType

BudgetKey = tuple[uuid.UUID, uuid.UUID]
"""`(calendar_id, engagement_type_id)` — the key of a budget, as Python.

The same two columns as `app.models.catalog.ABSENCE_BUDGET_KEY_COLUMNS` and in the same order, so
the lookup and the constraint cannot disagree about what "the same budget" is."""


class AbsenceBudgetWriteFailed(WriteFailed):
    """A budget write failed for a reason nothing here established — a `500`.

    The same division as on every other write path in this repository (R-01): a statement timeout,
    a dropped connection or a numeric overflow must not reach a caller as a conflict nobody
    observed.
    """


class AbsenceBudgetWriteRefused(AbsenceBudgetWriteFailed, WriteRefused):
    """The database refused the write for a reason its SQLSTATE names — a `409`.

    A subclass of both, so `except AbsenceBudgetWriteFailed` still catches everything while
    `except AbsenceBudgetWriteRefused` catches only what a caller can act on.
    """


def _failure(error: SQLAlchemyError) -> WriteFailed:
    """Classify one failed budget write by SQLSTATE, into this module's pair of exceptions.

    Spelled once so every statement of this path classifies identically — a per-call-site `except`
    is how one of them ends up answering `409` for a defect while another does not.
    """
    return failure_for(
        error,
        subject="absence budget",
        refused=AbsenceBudgetWriteRefused,
        failed=AbsenceBudgetWriteFailed,
    )


def statutory_leave_type(session: Session) -> StatutoryLeaveType | None:
    """The one absence type budgets settle against, or `None` when none is flagged.

    `one_or_none()` is safe here **because of the partial unique index**, not because of an
    assumption: `uq_absence_type_statutory_leave` makes a second flagged row impossible, so "two
    rows came back" is not a case this function has to choose between (ADR-0008, addendum
    2026-09-22 SC-3-03, point 8a). Without that index this call would raise on data the catalogue
    would happily hold.

    `None` is the named state of point 8b — "no statutory leave type has been named" — and the
    caller answers it with `app.domain.absence_budget.NO_STATUTORY_LEAVE_TYPE` plus `"n/a"`. What
    this function must never do is guess: no `ORDER BY name LIMIT 1`, no `name ILIKE '%leave%'`.
    Either of those is a second resolution mechanism next to the flag, and it is the mutation
    criterion K-04 names (the fixtures deliberately flag a type that is *not* first alphabetically).
    """
    row = (
        session.execute(sa.select(AbsenceType).where(AbsenceType.is_statutory_leave))
        .scalars()
        .one_or_none()
    )
    if row is None:
        return None
    return StatutoryLeaveType(
        absence_type_id=row.id,
        name=row.name,
        generates_cost=row.generates_cost,
        generates_revenue=row.generates_revenue,
    )


def list_budgets(session: Session) -> Sequence[AbsenceBudget]:
    """Every budget row, ordered — the same rows for every caller (ADR-0005, addendum SC-3-03).

    Ordered by the key and then by the window's start, so a list read twice comes back the same way
    and the windows of one pair read in chronological order.

    No pagination, unlike `app.data.catalog.list_rates`: the number of budget rows is bounded by
    (calendars × engagement types × windows), i.e. by how the organisation is *structured* rather
    than by how much it prices. That reasoning is the whole justification and it expires the day a
    budget is entered per location or per person.
    """
    statement = sa.select(AbsenceBudget).order_by(
        AbsenceBudget.calendar_id,
        AbsenceBudget.engagement_type_id,
        AbsenceBudget.effective_from,
    )
    return list(session.execute(statement).scalars().all())


def budgets_for_months(
    session: Session, keys: Sequence[BudgetKey], months: Sequence[date]
) -> Mapping[tuple[BudgetKey, date], BudgetValue]:
    """The budget in force for each (key, month) pair the caller asks about — **resolved by SQL**.

    One statement for the whole read: the months travel as a `VALUES` list and are joined to the
    table on `valid_period @> month`, so the database answers "which window covers this month" with
    the generated column both the `EXCLUDE` constraint and every other lookup read (ADR-0008,
    point 3). A month no window covers is simply absent from the result, which is what makes the
    named "no budget" state a *missing key* the caller has to handle rather than a zero it could
    accidentally add up — the shape `app.data.working_calendar.basis_by_location` already uses.

    The `EXCLUDE` constraint is what makes a mapping the right return type at all: at most one
    window per key can cover a day, so there is never a pair of candidate rows to choose between.

    The alternative shapes, and why not: one statement per position (N queries for a grid NF-03
    sizes at 200 positions), or "every row of this key, compared in Python" — which would put a
    second spelling of "covers this month" next to the constraint's.
    """
    if not keys or not months:
        return {}
    month_values = sa.values(
        sa.column("period_month", sa.Date), name="requested_months"
    ).data([(month,) for month in sorted(set(months))])
    statement = (
        sa.select(month_values.c.period_month, AbsenceBudget)
        .select_from(month_values)
        .join(
            AbsenceBudget,
            AbsenceBudget.valid_period.bool_op("@>")(month_values.c.period_month),
        )
        .where(
            sa.tuple_(AbsenceBudget.calendar_id, AbsenceBudget.engagement_type_id).in_(
                sorted(set(keys))
            )
        )
    )
    return {
        ((row.calendar_id, row.engagement_type_id), period_month): value_of(row)
        for period_month, row in session.execute(statement).all()
    }


def frozen_statutory_leave_type(
    session: Session, scenario_id: uuid.UUID
) -> StatutoryLeaveType | None:
    """The statutory type **as one approved scenario froze it** — the snapshot's counterpart of
    `statutory_leave_type` (ADR-0004, addendum 2026-09-23 SC-5-06, point 1).

    The snapshot's own contract (`app.models.approved_snapshot.ApprovedSnapshotAbsenceType`, S-02 of
    SC-3-03 as amended by ADR-0004, addendum 2026-09-23 SC-5-06, point 5): a frozen row with
    `is_statutory_leave = true` is present **iff** a type was named when the scenario was approved
    and the scenario has an allocation row in a location with a calendar — budget or no budget.
    Every month this answer is consulted for with a calendar therefore sees the named type if there
    was one, so `None` is the frozen `no_statutory_leave_type` and never "the type was named but
    not frozen" (control M-3). It stays that answer whatever the catalogue flags later — moving the
    flag to another type after the approval moves nothing here (control M-1). The flags returned
    (`generates_cost` included) are the frozen values, never the live ones. The one exception is
    historical and named in the addendum: a scenario approved before it, with a named non-costing
    type and no frozen budget, reads `None` here for ever.

    `one_or_none()` holds because the approval copies the type from one snapshot of a catalogue in
    which a partial unique index allows one flagged row, and deduplicates the copy.
    """
    row = session.execute(
        sa.select(ApprovedSnapshotAbsenceType).where(
            ApprovedSnapshotAbsenceType.scenario_id == scenario_id,
            ApprovedSnapshotAbsenceType.is_statutory_leave,
        )
    ).scalars().one_or_none()
    if row is None:
        return None
    return StatutoryLeaveType(
        absence_type_id=row.source_absence_type_id,
        name=row.name,
        generates_cost=row.generates_cost,
        generates_revenue=row.generates_revenue,
    )


def frozen_budgets_for_months(
    session: Session, scenario_id: uuid.UUID, keys: Sequence[BudgetKey], months: Sequence[date]
) -> Mapping[tuple[BudgetKey, date], BudgetValue]:
    """The frozen budget in force for each (key, month) — the snapshot's counterpart of
    `budgets_for_months`, **resolved per month by the same predicate** (ADR-0004,
    addendum 2026-09-22 SC-3-03, point 7c; addendum 2026-09-23 SC-5-06, point 2).

    The live read asks `valid_period @> month` of the generated column; the frozen table has no
    generated column (its column set is fixed by SC-3-03 K-07), so the window's range is built here
    from **the same expression the generated column is defined by** —
    `app.models.catalog.VALID_PERIOD_EXPRESSION`, applied to the frozen `effective_from` /
    `effective_to` — and asked the same `@>`. One spelling of "which window covers this month", not
    a Python comparison and not a second SQL one.

    Only this scenario's rows (control M-2), and only rows of the keys asked about. A month no
    frozen window covers is absent from the result — the frozen `no_budget`, including the case the
    addendum names (point 7e): a month planned beyond the last window the catalogue had at approval
    stays without a budget, never an extrapolation of the last known one.
    """
    if not keys or not months:
        return {}
    month_values = sa.values(
        sa.column("period_month", sa.Date), name="requested_frozen_months"
    ).data([(month,) for month in sorted(set(months))])
    frozen = (
        sa.select(
            ApprovedSnapshotAbsenceBudget.source_budget_id,
            ApprovedSnapshotAbsenceBudget.source_calendar_id,
            ApprovedSnapshotAbsenceBudget.source_engagement_type_id,
            ApprovedSnapshotAbsenceBudget.budget_days,
            ApprovedSnapshotAbsenceBudget.unit,
            ApprovedSnapshotAbsenceBudget.source,
            ApprovedSnapshotAbsenceBudget.effective_from,
            ApprovedSnapshotAbsenceBudget.effective_to,
        )
        .where(
            ApprovedSnapshotAbsenceBudget.scenario_id == scenario_id,
            sa.tuple_(
                ApprovedSnapshotAbsenceBudget.source_calendar_id,
                ApprovedSnapshotAbsenceBudget.source_engagement_type_id,
            ).in_(sorted(set(keys))),
        )
        .subquery("frozen_budget")
    )
    # The generated column's own expression, over the frozen dates. Its column names are unqualified
    # in the constant, and the only relation in this `FROM` is `frozen_budget`, so they resolve to
    # the frozen row's dates and to nothing else.
    with_period = (
        sa.select(
            *frozen.c,
            sa.literal_column(VALID_PERIOD_EXPRESSION).label("valid_period"),
        )
        .select_from(frozen)
        .subquery("frozen_budget_period")
    )
    statement = (
        sa.select(month_values.c.period_month, with_period)
        .select_from(month_values)
        .join(with_period, with_period.c.valid_period.op("@>")(month_values.c.period_month))
    )
    return {
        ((row.source_calendar_id, row.source_engagement_type_id), row.period_month): BudgetValue(
            budget_id=row.source_budget_id,
            calendar_id=row.source_calendar_id,
            engagement_type_id=row.source_engagement_type_id,
            budget_days=row.budget_days,
            unit=row.unit,
            source=row.source,
            effective_from=row.effective_from,
            effective_to=row.effective_to,
        )
        for row in session.execute(statement).all()
    }


def value_of(row: AbsenceBudget) -> BudgetValue:
    """One ORM budget row as the value object the calculation consumes.

    `effective_to` is typed as a plain `date` on the value object, not `date | None`: the database
    refuses an open-ended window on this table (ADR-0008, addendum SC-3-03, point 10b), so the case
    does not exist in the data and carrying it in the type would invite handling it in code.
    """
    assert row.effective_to is not None, (
        "a budget row with no end date reached the calculation — "
        "ck_absence_budget_effective_to_is_closed is missing from the database"
    )
    return BudgetValue(
        budget_id=row.id,
        calendar_id=row.calendar_id,
        engagement_type_id=row.engagement_type_id,
        budget_days=row.budget_days,
        unit=row.unit,
        source=row.source,
        effective_from=row.effective_from,
        effective_to=row.effective_to,
    )


def create_budget(
    session: Session,
    *,
    calendar_id: uuid.UUID,
    engagement_type_id: uuid.UUID,
    budget_days: Decimal,
    unit: str,
    source: str,
    effective_from: date,
    effective_to: date,
) -> AbsenceBudget:
    """Insert one budget window and commit it — or raise, without quoting what was written.

    **No pre-check of anything the database checks**: the overlap (`EXCLUDE`), the non-blank source,
    the closed and month-aligned window, the unit and both foreign keys are all refused inside the
    `INSERT`. The API boundary states the same rules to produce a `422` naming the field, and the
    constraint is what actually holds — a fixture, a seed script or an import never passes through
    Pydantic (criterion K-02's first mutation).

    `valid_period` is absent from the arguments and from the insert: it is a generated column, and
    assigning it would be the second place the window's boundary is decided.

    `unit` and `source` are explicit arguments with no defaults. For `unit` that is the argument
    `create_rate` makes (a second call site must not inherit whatever this function preferred); for
    `source` it is the point of the criterion — a default would make "the source of this number" a
    field that can be omitted, which is exactly what K-02 refuses, and an author's identity is not
    an acceptable substitute (ADR-0008, addendum SC-3-03, point 6).

    Nothing here rounds `budget_days`: the stored value is input, and rounding is the consumer's
    rule (`app.core.money.round_money`).

    A failed write is classified by SQLSTATE and by nothing else (`_failure`), so an overlap, a
    broken check or a missing reference is a refusal the caller can act on, while an overflow, a
    timeout or a lost connection stays a defect and must not be answered with a `409`.
    """
    budget = AbsenceBudget(
        id=uuid.uuid4(),
        calendar_id=calendar_id,
        engagement_type_id=engagement_type_id,
        budget_days=budget_days,
        unit=unit,
        source=source,
        effective_from=effective_from,
        effective_to=effective_to,
    )
    try:
        session.add(budget)
        session.commit()
    except SQLAlchemyError as error:
        session.rollback()
        # `from None`, as on every write path here: a chained exception is printed with its cause,
        # so re-raising *with* the original would put PostgreSQL's `DETAIL: Failing row contains
        # (…)` back into the traceback one line further down (NF-11).
        raise _failure(error) from None
    # Re-read after the commit so the row returned is the row the database holds: `NUMERIC(6,2)`
    # turns `Decimal("26")` into `Decimal("26.00")`, and the response of a write must not disagree
    # with the next `GET` about which of the two it is. Sessions here run with
    # `expire_on_commit=False` (`app.db.session`), which is why this cannot be left to the ORM.
    session.refresh(budget)
    return budget
