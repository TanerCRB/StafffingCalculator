"""Reading what the paid-absence cost of one scenario needs (F-07, F-05; SC-5-06; ADR-0013, addendum
2026-09-23 SC-5-06; ADR-0004, addendum 2026-09-23 SC-5-06).

Called by `app.data.personnel_cost.scenario_cost_for_caller` **after** that function has decided the
scope and resolved the cost rates, so there is no scope decision here and no second cost-rate
predicate: the rate of every (position, month) arrives as the base cost's own `MonthCostRate`.
Nothing here decides who may see the figure either — the component travels on `ScenarioCostView`
and is gated by `app.api.response_shaping` with the rest of the payload.

**The source is chosen by the scenario's status, and only by it** — the rule the base cost follows:

- a **draft** reads the live catalogue: `basis_by_location`, `budgets_for_months`,
  `statutory_leave_type`, and the `generates_cost` flag of each booked type from `absence_type`;
- an **approved** scenario reads **only** its approval snapshot — `frozen_basis_by_location`
  (`approved_snapshot_working_calendar(_day)`), `frozen_budgets_for_months`
  (`approved_snapshot_absence_budget`, per month, the live predicate's expression),
  `frozen_statutory_leave_type` and the frozen flags (`approved_snapshot_absence_type`). Never a
  live table (addendum SC-5-06, point 1; control M-1). The scenario's own absence instances are read
  from `staffing_position_absence` in both cases: they are group 2, frozen by the write guard, and
  the snapshot deliberately does not copy them (ADR-0004, addendum 2026-09-22 SC-3-02, point 1).

**The budget share is the capacity's, not a copy of it**: each month's `BudgetShare` comes from
`app.data.staffing.position_view`, the function the staffing grid uses, fed with whichever inputs
the status selected. The top-up costed is therefore the top-up the capacity subtracts — for the same
inputs, to the cent.

**Independent of the revenue path** (rule 10 of the Invariant Guardian; control N-4): no import of
`app.data.commercial_terms` or `app.domain.revenue*`, and neither of them imports this module.
`generates_revenue` is never read here.

**A known, accepted limitation — a draft read under concurrent edits** (R-02, reviewer, SC-5-06,
Medium; accepted rather than repaired). The rates arrive from `_worked_months` (one statement) and
the positions, their allocations and absences are read here (a statement plus two `selectinload`
statements), all under `READ COMMITTED`, so each statement sees its own snapshot. An allocation row
committed to a **draft** between the two reads is in the positions and not in `rates`, and its
month answers `rates.get(...) is None` — the whole component then reports `no_cost_rate` although
the catalogue does cost that month. It is transient (the next `GET` reads one consistent grid) and
it cannot reach an approved scenario, whose grid the write guard freezes. Not repaired here because
both repairs are out of proportion to a transient read: a single statement for the whole grid would
have to replace `selectinload` and the shared `position_view` input, and `REPEATABLE READ` for the
`GET` is a new transaction-isolation mechanism on the read path that nothing else in this
repository uses. Skipping a month missing from `rates` was rejected too: a wrong key would then turn
a loud `no_cost_rate` into a silent `0.00`.
"""

import uuid
from collections.abc import Mapping, Sequence
from datetime import date

import sqlalchemy as sa
from sqlalchemy.orm import Session, selectinload

from app.data.absence_budget import (
    budgets_for_months,
    frozen_budgets_for_months,
    frozen_statutory_leave_type,
    statutory_leave_type,
)
from app.data.rate_windows import shift_calendar_month_date
from app.data.staffing import budget_keys_and_months, position_view
from app.data.working_calendar import basis_by_location, frozen_basis_by_location
from app.domain.paid_absence_cost import PaidAbsenceMonth, PaidAbsenceSpan
from app.domain.personnel_cost import MonthCostRate
from app.models.approved_snapshot import ApprovedSnapshotAbsenceType
from app.models.catalog import AbsenceType
from app.models.scenario import Scenario, ScenarioStatus
from app.models.staffing import StaffingPosition


class FrozenAbsenceTypeMissing(RuntimeError):
    """An approved scenario books an absence whose type its snapshot does not hold.

    Not a named state of the calculation: the approval copies every booked type in the same
    statement that freezes the scenario, and the write guard keeps the bookings still afterwards, so
    this can only mean a broken snapshot. Answering "does not generate cost" instead would be a
    silently understated cost, and reading the live flag would be the AC-04 breach this module
    exists to avoid — so it is an error, and the message names no value (NF-11).
    """


def _live_cost_flags(session: Session, type_ids: set[uuid.UUID]) -> Mapping[uuid.UUID, bool]:
    """`generates_cost` of each booked type, from the live dictionary — the flag, nothing else."""
    if not type_ids:
        return {}
    return dict(
        session.execute(
            sa.select(AbsenceType.id, AbsenceType.generates_cost).where(
                AbsenceType.id.in_(type_ids)
            )
        )
        .tuples()
        .all()
    )


def _frozen_cost_flags(session: Session, scenario_id: uuid.UUID) -> Mapping[uuid.UUID, bool]:
    """`generates_cost` of each type this scenario froze — its own rows only (control M-2)."""
    return dict(
        session.execute(
            sa.select(
                ApprovedSnapshotAbsenceType.source_absence_type_id,
                ApprovedSnapshotAbsenceType.generates_cost,
            ).where(ApprovedSnapshotAbsenceType.scenario_id == scenario_id)
        )
        .tuples()
        .all()
    )


def paid_absence_months(
    session: Session,
    scenario: Scenario,
    rates: Mapping[tuple[uuid.UUID, date], MonthCostRate | None],
    *,
    period_shift_months: int = 0,
) -> Sequence[PaidAbsenceMonth]:
    """One `PaidAbsenceMonth` per allocation row of the scenario, live or frozen by its status.

    `rates` is keyed by (position, month) and holds the base cost's resolution of every allocation
    row — the component uses **that** rate and asks no predicate of its own (addendum SC-5-06,
    point 3).
    """
    positions = list(
        session.execute(
            sa.select(StaffingPosition)
            .where(StaffingPosition.scenario_id == scenario.id)
            .options(
                selectinload(StaffingPosition.allocations),
                selectinload(StaffingPosition.absences),
            )
            .order_by(StaffingPosition.start_date, StaffingPosition.id)
            # Collections loaded earlier in the same session are re-read, not trusted: an absence
            # booked since would otherwise be missing from the cost.
            .execution_options(populate_existing=True)
        )
        .scalars()
        .all()
    )
    if not positions:
        return []

    location_ids = [position.location_id for position in positions]
    booked_types = {absence.absence_type_id for p in positions for absence in p.absences}
    if scenario.status == ScenarioStatus.APPROVED:
        bases = frozen_basis_by_location(session, scenario.id)
        statutory = frozen_statutory_leave_type(session, scenario.id)
        allocation_months = {
            (position.id, allocation.period_month): shift_calendar_month_date(
                allocation.period_month, period_shift_months
            )
            for position in positions
            for allocation in position.allocations
        }
        keys, months = budget_keys_and_months(positions, bases, allocation_months)
        budgets = frozen_budgets_for_months(session, scenario.id, keys, months)
        cost_flags = _frozen_cost_flags(session, scenario.id)
    else:
        bases = basis_by_location(session, location_ids)
        statutory = statutory_leave_type(session)
        allocation_months = {
            (position.id, allocation.period_month): shift_calendar_month_date(
                allocation.period_month, period_shift_months
            )
            for position in positions
            for allocation in position.allocations
        }
        keys, months = budget_keys_and_months(positions, bases, allocation_months)
        budgets = budgets_for_months(session, keys, months)
        cost_flags = _live_cost_flags(session, booked_types)

    missing = booked_types - set(cost_flags)
    if missing:
        raise FrozenAbsenceTypeMissing(
            "A booked absence type has no generates_cost flag in the source this scenario is "
            "costed from; the paid-absence cost cannot be stated."
        )

    result: list[PaidAbsenceMonth] = []
    for position in positions:
        basis = bases.get(position.location_id)
        position_months = {
            allocation.period_month: allocation_months[(position.id, allocation.period_month)]
            for allocation in position.allocations
        }
        view = position_view(position, basis, budgets, statutory, allocation_months=position_months)
        spans = tuple(
            PaidAbsenceSpan(
                start_date=absence.start_date,
                end_date=absence.end_date,
                generates_cost=cost_flags[absence.absence_type_id],
            )
            for absence in position.absences
        )
        for allocation in position.allocations:
            period_month = allocation_months[(position.id, allocation.period_month)]
            capacity = view.capacity[period_month]
            # Never `None` on a view built by `position_view`: a month with no calendar carries the
            # calendar's own named state, every other month a share or a named budget state
            # (`app.domain.capacity.month_capacity`). Raised rather than defaulted, because a
            # default here would be a budget state nobody decided.
            if capacity.budget is None:
                raise RuntimeError("a capacity month without a budget share reached the cost")
            result.append(
                PaidAbsenceMonth(
                    position_id=position.id,
                    period_month=period_month,
                    basis=basis,
                    absences=spans,
                    budget=capacity.budget,
                    statutory_generates_cost=(
                        None if statutory is None else statutory.generates_cost
                    ),
                    rate=rates.get((position.id, period_month)),
                )
            )
    return result
