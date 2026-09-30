"""The only path by which a scenario's base personnel cost is read (F-07, SC-5-01; ADR-0013).

**Since SC-5-04, the dispatcher has three bases** (ADR-0013, addendum 2026-09-29 SC-5-04, points 3-6
and 12): `dispatch_cost_inputs` below splits the resolved (position, month) grid **at the formula
input** — worked-time months to `base_personnel_cost`, `assigned_fte` months and lines to
`app.domain.assigned_fte_cost.assigned_fte_cost`, and no allocation row of a `fixed_amount` or
`assigned_fte` position to the worked-time formula. The split is **not** in `month_has_cost_rate`
or `costed_month_windows`: those keep resolving the rate of every position whose rate is read
(worked time and FTE), so the approval's rate freeze
(`app.data.scenario_approval._copy_catalog_default_rates`) still covers FTE positions — a rate
dropped from the freeze would leave an approved FTE cost with `no_cost_rate` for ever. The
paid-absence component keeps receiving the rate of every allocation row (point 11, unchanged).
This module is the only one allowed to import all three formulas.

**Since SC-5-03, also the one dispatcher between the two cost bases** (ADR-0013, addendum 2026-09-25
SC-5-03, point 5 of the "Decision" section it adds: "Dispatch by `cost_basis` lives in one, shared
calling function (new or existing, in `app.data.personnel_cost`/`app.domain.personnel_
cost`)"). This module is explicitly named as an allowed home for it, and it is the only module
allowed to import *both* formulas — `app.domain.personnel_cost.base_personnel_cost` (worked time)
and `app.domain.fixed_amount_cost.fixed_amount_cost` (fixed amount) — because dispatching between
two independent formulas is a different thing from being either of them (K-01: "the two formulas
themselves are two independent functions"). Neither formula module imports the other, and neither
imports this one; the structural test proving that lives beside each formula's own module
(`tests/test_personnel_cost.py`'s C-5 mirror, `tests/test_fixed_amount_cost.py`'s).

Three mechanisms, none of them new — each is an existing mechanism of this repository applied to
the first calculation that reads `default_cost_rate`:

1. **Scope** — `app.data.staffing.scenario_view_in_scope`, i.e. `project_for_caller` plus membership
   of `Project.scenarios`, returning the scenario **together with the caller's project view**. The
   view carries `project_access.can_view_personnel_costs` for the project **this scenario belongs
   to**, resolved in the same statement that decided the caller may see it (ADR-0005, addendum
   2026-09-19, point 6; addendum 2026-09-23 SC-5-01, point 2). No scope function of its own, no
   `select(Scenario)` and no second read of `project_access` here: "no such project", "not yours"
   and "that scenario belongs to another project" are one `None` (criterion K-05).
2. **Cost-rate resolution by the whole month, in SQL, with one predicate for the live catalogue,
   the approval freeze and the snapshot reader** (ADR-0013, points 1 and 6; ADR-0004, addendum
   2026-09-23 SC-5-01, point 4). `month_has_cost_rate` is that predicate — "the internal windows
   overlapping the month cover every day of it and share one (`default_cost_rate`, `currency`,
   `cost_rate_unit`, surcharge pair)" —
   and `costed_month_windows` is the one statement shape that applies it. The approval's copier
   (`app.data.scenario_approval._copy_catalog_default_rates`) and the reader below both use it.
3. **The source is chosen by the scenario's status, and only by it**: a draft reads the live
   catalogue, an approved scenario reads its own snapshot and never the catalogue (AC-04, criterion
   K-07).

**Independent of the revenue path** (F-06; rule 10 of the Invariant Guardian; ADR-0013, point 1;
ADR-0004, addendum 2026-09-23 SC-5-01, point 3). This module never imports
`app.data.commercial_terms` or `app.domain.revenue*`, and neither of them imports it. What the two
share is geometry only — `app.data.rate_windows`: the month as a range, the day count, "the windows
cover the month", the two overlap joins. Whether the windows *resolve* the month is asked here of
the cost column and there of the selling column; a mid-month change of the selling rate therefore
does not uncost a month, and a mid-month change of the cost rate does not unprice one.

**What this module never reads: `default_selling_rate`**, from the catalogue or from the snapshot.

**What this module does not decide: who may see the figure.** The view carries the flag and the
caller's id; the conjunction `PERSONNEL_COSTS_READ` ∧ `can_view_personnel_costs` is applied by
`app.api.response_shaping` (ADR-0005, addendum 2026-09-23 SC-5-01, point 2), never here.
"""

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.core.identity import CallerIdentity
from app.data.paid_absence_cost import paid_absence_months
from app.data.rate_windows import (
    days_covered_in_month,
    frozen_windows_overlapping,
    internal_catalog_windows_overlapping,
    shifted_calendar_month,
)
from app.data.staffing import scenario_view_in_scope
from app.data.working_calendar import basis_by_location, frozen_basis_by_location
from app.domain.assigned_fte_cost import (
    AssignedFteCostAnswer,
    AssignedFteLine,
    AssignedFteMonth,
    assigned_fte_cost,
)
from app.domain.capacity import CalendarBasis
from app.domain.fixed_amount_cost import (
    FixedAmountCostAnswer,
    FixedAmountLine,
    fixed_amount_cost,
)
from app.domain.paid_absence_cost import (
    FullyLoadedPaidAbsenceCostAnswer,
    PaidAbsenceCostAnswer,
    fully_loaded_paid_absence_cost,
    paid_absence_cost,
)
from app.domain.personnel_cost import (
    APPROVED_SNAPSHOT,
    COST_RATE_UNIT_HOUR,
    LIVE_CATALOG,
    CostRateWindow,
    FullyLoadedPersonnelCostAnswer,
    MonthCostRate,
    PersonnelCostAnswer,
    WorkedMonth,
    base_personnel_cost,
    fully_loaded_personnel_cost,
)
from app.models.approved_snapshot import ApprovedSnapshotCatalogDefaultRate
from app.models.catalog import CatalogDefaultRate
from app.models.scenario import Scenario, ScenarioStatus
from app.models.staffing import (
    COST_BASIS_ASSIGNED_FTE,
    COST_BASIS_FIXED_AMOUNT,
    COST_BASIS_WORKED_TIME,
    StaffingPosition,
    StaffingPositionAllocation,
)

# --- the cost-rate predicate (ADR-0013, point 1) --------------------------------------------------
#
#     a (position, month) has a cost rate  <=>  the internal windows of its tuple overlapping the
#         month (a) together cover every day of it, and
#                (b) all carry the same (`default_cost_rate`, `currency`, `surcharge_percent`,
#                    `includes_surcharge`, `cost_rate_unit`)
#
# and it is then costed at that one cost rate. A cost-rate change inside the month breaks (b), a
# currency change inside the month breaks (b), a gap breaks (a), and — since SC-5-02, closing the
# gap Guardian/Reviewer named at gate 2 review — a surcharge-only or flag-only change inside the
# month breaks (b) exactly the same way: all four are `no_cost_rate`. A window boundary that moves
# only the surcharge is not a smaller problem than one that moves the cost rate — both leave the
# month with more than one candidate answer, and SC-5-01 already drew the line that such a month
# gets the named state, never a silently-picked "first window" answer. Since SC-5-08 the unit of the
# cost rate is a fifth value: a boundary that moves only `cost_rate_unit` (same amount, same
# currency) is `no_cost_rate` too — "5 000 per hour" and "5 000 per month" are two answers, never
# one picked by position (ADR-0013, addendum 2026-09-29, point 2). Where the *selling*
# boundaries fall still does not matter — the mirror of ADR-0003's addendum R-01.
#
# **One predicate, three callers** (ADR-0013, point 6; ADR-0004, addendum 2026-09-23 SC-5-01, point
# 4): the live read below, the approval's freeze and the snapshot reader below all build their
# statement with `costed_month_windows`, so what is frozen for the cost, what a draft reads and what
# an approved scenario reads cannot differ by a clause.


def month_has_cost_rate(
    allocation_id: sa.ColumnElement[uuid.UUID],
    period_month: sa.ColumnElement[date],
    valid_period: sa.ColumnElement[object],
    cost_rate: sa.ColumnElement[object],
    currency: sa.ColumnElement[str],
    surcharge_percent: sa.ColumnElement[object],
    includes_surcharge: sa.ColumnElement[bool],
    cost_rate_unit: sa.ColumnElement[str],
) -> sa.ColumnElement[bool]:
    """The cost predicate itself, as window functions over the windows overlapping one month.

    Evaluated per row of "(allocation, window overlapping its month)", partitioned by the
    allocation, so every row of one month carries the same answer:

    - **covered** — `app.data.rate_windows.days_covered_in_month` (never `NULL`: a month with no
      window is "0 days covered", so `false`, and `false AND NULL` is `false`);
    - **one cost rate** — `min = max` of the cost rate, of the currency and (SC-5-02) of the
      surcharge percentage and (SC-5-08) of the cost-rate unit, plus `bool_and = bool_or` of the
      "already includes it" flag — the boolean equivalent of `min = max` (PostgreSQL has no
      `min`/`max` aggregate for `boolean`) — across the month's windows. Five values, one
      predicate: a month answers with exactly one cost rate, one currency, one surcharge
      percentage, one flag and one unit, or it does not answer at all.

    Not built from `app.data.commercial_terms.month_is_priced` and never reading the selling rate:
    a separate function in a separate module (ADR-0013, point 1).
    """
    covered = days_covered_in_month(allocation_id, period_month, valid_period)
    one_cost_rate = sa.and_(
        sa.func.min(cost_rate).over(partition_by=allocation_id)
        == sa.func.max(cost_rate).over(partition_by=allocation_id),
        sa.func.min(currency).over(partition_by=allocation_id)
        == sa.func.max(currency).over(partition_by=allocation_id),
        sa.func.min(surcharge_percent).over(partition_by=allocation_id)
        == sa.func.max(surcharge_percent).over(partition_by=allocation_id),
        sa.func.bool_and(includes_surcharge).over(partition_by=allocation_id)
        == sa.func.bool_or(includes_surcharge).over(partition_by=allocation_id),
        sa.func.min(cost_rate_unit).over(partition_by=allocation_id)
        == sa.func.max(cost_rate_unit).over(partition_by=allocation_id),
    )
    return sa.and_(covered, one_cost_rate)


def costed_month_windows(
    *, from_snapshot: bool, scenario_id: uuid.UUID, period_shift_months: int = 0
) -> sa.Select:
    """Every (allocation, overlapping window) row of one scenario, each carrying
    `month_has_cost_rate`.

    Columns: `scenario_id`, `position_id`, `position_location_id`, `allocation_id`, `period_month`,
    `planned_allocation_hours`, `window_id` (the catalogue row's id — on the snapshot, the frozen
    `source_rate_id`), `effective_from`, `effective_to`, `cost_rate`, `currency`,
    `surcharge_percent`, `includes_surcharge`, `cost_rate_unit`, `month_has_cost_rate`.

    **`cost_rate_unit` is read symmetrically from both branches (SC-5-08)** — the live column for a
    draft, the frozen column of `approved_snapshot_catalog_default_rate` for an approved scenario,
    never the live catalogue's for the latter (ADR-0004, addendum 2026-09-29, point 4; the unit
    rides the same predicate as the rate it qualifies).

    A `LEFT JOIN`, so a month with no overlapping window still yields one row (window columns
    `NULL`, `month_has_cost_rate` false) — which is what makes `no_cost_rate` a value the formula
    sees rather than a row that silently went missing from an inner join (criterion K-03).

    **The cost rate only — never `default_selling_rate`**, from the catalogue and from the snapshot
    alike. **`planned_allocation_hours` only** — not `billable_hours`, not the availability, not the
    headcount (ADR-0013, points 3 and 4).

    **`surcharge_percent`/`includes_surcharge` are read symmetrically from both branches (SC-5-02),
    exactly like `cost_rate`/`currency` above them.** `ApprovedSnapshotCatalogDefaultRate` carries
    both columns since SC-5-02 froze them on the same row as `default_cost_rate`, in the same
    transaction (`app.data.scenario_approval._copy_catalog_default_rates`) — this is the *existing*
    reader of that table for the base cost, not a new one, and completing the freeze means reading
    it back the same way `default_cost_rate` already is. Naming a literal `0`/`false` here instead
    (an earlier version of this function did) left an approved scenario's fully loaded cost frozen
    at "no surcharge" for ever, regardless of what was actually configured at approval time — a
    silent regression on approval with no catalogue edit involved, caught by QA
    (`tests/test_personnel_cost_surcharge.py::test_qa_finding_…`).

    Callers filter on `month_has_cost_rate` **outside** this select (as a subquery), never inside
    it: a `WHERE` here would run before the window functions and change the partitions they see.
    """
    period_month = shifted_calendar_month(
        StaffingPositionAllocation.period_month, period_shift_months
    )
    if from_snapshot:
        window = ApprovedSnapshotCatalogDefaultRate
        window_id = window.source_rate_id
        # The scenario's **own** frozen rows, re-asked the same predicate per month rather than
        # trusted as "whatever was frozen" (ADR-0004, addendum 2026-09-23 SC-5-01, point 4).
        condition = frozen_windows_overlapping(period_month)
    else:
        window = CatalogDefaultRate
        window_id = window.id
        condition = internal_catalog_windows_overlapping(period_month)
    return (
        sa.select(
            StaffingPosition.scenario_id.label("scenario_id"),
            StaffingPosition.id.label("position_id"),
            StaffingPosition.location_id.label("position_location_id"),
            StaffingPositionAllocation.id.label("allocation_id"),
            period_month.label("period_month"),
            StaffingPositionAllocation.planned_allocation_hours.label("planned_allocation_hours"),
            window_id.label("window_id"),
            window.effective_from.label("effective_from"),
            window.effective_to.label("effective_to"),
            window.default_cost_rate.label("cost_rate"),
            window.currency.label("currency"),
            window.surcharge_percent.label("surcharge_percent"),
            window.includes_surcharge.label("includes_surcharge"),
            window.cost_rate_unit.label("cost_rate_unit"),
            month_has_cost_rate(
                StaffingPositionAllocation.id,
                period_month,
                window.valid_period,
                window.default_cost_rate,
                window.currency,
                window.surcharge_percent,
                window.includes_surcharge,
                window.cost_rate_unit,
            ).label("month_has_cost_rate"),
        )
        .select_from(StaffingPosition)
        .join(
            StaffingPositionAllocation,
            StaffingPositionAllocation.position_id == StaffingPosition.id,
        )
        .outerjoin(window, condition)
        .where(StaffingPosition.scenario_id == scenario_id)
    )


# --- reading the worked months of a scenario, costed ----------------------------------------------


@dataclass(frozen=True)
class NonWorkedTimePosition:
    """A position whose `cost_basis` is not `worked_time` — the input of the dispatch.

    `assigned_fte` is set exactly for `cost_basis = 'assigned_fte'` (the database guarantees it,
    `assigned_fte_required_for_its_basis`); it is `None` for a `fixed_amount` position, whose amount
    is read by `_fixed_amount_lines`."""

    position_id: uuid.UUID
    cost_basis: str
    assigned_fte: Decimal | None


def _non_worked_time_positions(session: Session, scenario: Scenario) -> list[NonWorkedTimePosition]:
    """Every position of the scenario priced by a formula other than the worked-time one, by id.

    Read **before** the rate grid in `_worked_months`, because it decides which months need a
    calendar; `dispatch_cost_inputs` then applies the same list to the grid it returned. The two
    statements run under `READ COMMITTED`, so a position switched between them is priced by the
    basis of one read and the rate of the other for that one request — transient, draft-only (the
    write guard freezes an approved scenario), and the same class as the named limitation of
    `app.data.paid_absence_cost` (R-02): the next read is consistent.
    """
    statement = (
        sa.select(StaffingPosition.id, StaffingPosition.cost_basis, StaffingPosition.assigned_fte)
        .where(
            StaffingPosition.scenario_id == scenario.id,
            StaffingPosition.cost_basis != COST_BASIS_WORKED_TIME,
        )
        .order_by(StaffingPosition.id)
    )
    return [
        NonWorkedTimePosition(
            position_id=row.id, cost_basis=row.cost_basis, assigned_fte=row.assigned_fte
        )
        for row in session.execute(statement)
    ]


def _worked_months(
    session: Session, scenario: Scenario, *, period_shift_months: int = 0
) -> tuple[str, list[WorkedMonth], list[NonWorkedTimePosition]]:
    """Every allocation row of the scenario with its cost rate — live catalogue or snapshot — and
    the positions the dispatch must take out of the worked-time formula.

    **The months are the whole grid, whatever the position's basis**: this is the shared
    per-(position, month) rate structure that the paid-absence component and the what-if raise
    (ADR-0015) consume. It is *not* the input of any one formula — `dispatch_cost_inputs` is
    (ADR-0013, addendum 2026-09-29 SC-5-04, point 6).

    Python only groups the rows of one month together and reads the database's answer
    (`month_has_cost_rate`); it compares no date and no rate. Every row of a costed month carries
    the same cost rate, currency, surcharge percentage and "already includes it" flag — all four
    equalities are part of the predicate (SC-5-02, closing the gap Guardian/Reviewer named at gate
    2 review: a boundary that moves only the surcharge is exactly as disqualifying as one that moves
    the cost rate, never a silently-picked "first window" answer). Reading
    `first.surcharge_percent`/`first.includes_surcharge` below is therefore exactly as safe as
    reading `first.cost_rate`/`first.currency` already was — the predicate guarantees every window
    of a resolved month agrees. Since SC-5-08 the same holds for `first.cost_rate_unit`.

    **The calendar is read only when some resolved month is not hourly** (ADR-0013, addendum
    2026-09-29, points 4 and 5): a scenario of `hour` positions never touches a calendar table, and
    the source follows the rate's — `basis_by_location` for a draft, `frozen_basis_by_location` for
    an approved scenario, never a live lookup to fill a key the snapshot lacks (absence of a key is
    the `no_calendar` state). **An `assigned_fte` position needs a calendar whatever the unit of its
    rate** (SC-5-04, point 5): its hours come from the calendar, so any month of such a position
    triggers the read, `hour` included. A `fixed_amount` position never does — no formula reads its
    months.
    """
    approved = scenario.status == ScenarioStatus.APPROVED
    positions = _non_worked_time_positions(session, scenario)
    fte_position_ids = {
        position.position_id
        for position in positions
        if position.cost_basis == COST_BASIS_ASSIGNED_FTE
    }
    not_worked_time_ids = {position.position_id for position in positions}
    source = APPROVED_SNAPSHOT if approved else LIVE_CATALOG
    rows = costed_month_windows(
        from_snapshot=approved,
        scenario_id=scenario.id,
        period_shift_months=period_shift_months,
    ).subquery(
        "costed_month_windows"
    )
    statement = sa.select(rows).order_by(
        rows.c.period_month, rows.c.position_id, rows.c.effective_from, rows.c.window_id
    )
    grouped: dict[uuid.UUID, list[sa.Row]] = {}
    for row in session.execute(statement).all():
        grouped.setdefault(row.allocation_id, []).append(row)
    needs_calendar = any(
        month_rows[0].position_id in fte_position_ids
        or (
            month_rows[0].position_id not in not_worked_time_ids
            and month_rows[0].month_has_cost_rate
            and month_rows[0].cost_rate_unit != COST_RATE_UNIT_HOUR
        )
        for month_rows in grouped.values()
    )
    bases: Mapping[uuid.UUID, CalendarBasis] = {}
    if needs_calendar:
        if approved:
            bases = frozen_basis_by_location(session, scenario.id)
        else:
            bases = basis_by_location(
                session, sorted({rows[0].position_location_id for rows in grouped.values()})
            )
    months = []
    for month_rows in grouped.values():
        first = month_rows[0]
        rate = None
        if first.month_has_cost_rate:
            rate = MonthCostRate(
                cost_rate=first.cost_rate,
                currency=first.currency,
                surcharge_percent=first.surcharge_percent,
                includes_surcharge=first.includes_surcharge,
                cost_rate_unit=first.cost_rate_unit,
                windows=tuple(
                    CostRateWindow(
                        source_rate_id=row.window_id,
                        effective_from=row.effective_from,
                        effective_to=row.effective_to,
                        cost_rate=row.cost_rate,
                        currency=row.currency,
                        surcharge_percent=row.surcharge_percent,
                        includes_surcharge=row.includes_surcharge,
                        cost_rate_unit=row.cost_rate_unit,
                    )
                    for row in month_rows
                ),
            )
        months.append(
            WorkedMonth(
                position_id=first.position_id,
                period_month=first.period_month,
                planned_allocation_hours=first.planned_allocation_hours,
                rate=rate,
                basis=bases.get(first.position_location_id),
            )
        )
    return source, months, positions


@dataclass(frozen=True)
class CostInputs:
    """What each formula is handed — the result of the dispatch, and nothing else is."""

    worked_months: list[WorkedMonth]
    """The months of `worked_time` positions only: the input of `base_personnel_cost` and
    `fully_loaded_personnel_cost`."""
    assigned_fte_lines: list[AssignedFteLine]
    """Every `assigned_fte` position, with or without allocation rows (a position with none is
    `no_planned_months`)."""
    assigned_fte_months: list[AssignedFteMonth]
    """The allocation rows of `assigned_fte` positions, with their resolved rate and calendar."""


def dispatch_cost_inputs(
    months: list[WorkedMonth], positions: list[NonWorkedTimePosition]
) -> CostInputs:
    """Exclusive dispatch by `cost_basis` at the formula input (ADR-0013 addendum 2026-09-29
    SC-5-04, point 6): a position is priced by **exactly one** formula.

    - a `worked_time` position's months go to the worked-time formula;
    - an `assigned_fte` position's months go to the FTE formula, and its FTE to a line;
    - a `fixed_amount` position's months go to no formula at all — its cost is its amount
      (`_fixed_amount_lines`). Before SC-5-04 they went to the worked-time formula too, which priced
      such a position twice whenever it carried an allocation row (the "verified existing defect" of
      the gate-1 package, fixed here by human decision H-1).

    `months` is the shared resolved grid; this function only chooses what each formula receives —
    it applies no predicate and reads no rate. It is applied identically by the live read
    (`scenario_cost_for_caller`) and by the what-if, which passes the already-raised grid.
    """
    fte_by_position = {
        position.position_id: position.assigned_fte
        for position in positions
        if position.cost_basis == COST_BASIS_ASSIGNED_FTE and position.assigned_fte is not None
    }
    not_worked_time_ids = {position.position_id for position in positions}
    return CostInputs(
        worked_months=[month for month in months if month.position_id not in not_worked_time_ids],
        assigned_fte_lines=[
            AssignedFteLine(position_id=position_id, assigned_fte=fte)
            for position_id, fte in fte_by_position.items()
        ],
        assigned_fte_months=[
            AssignedFteMonth(
                position_id=month.position_id,
                period_month=month.period_month,
                rate=month.rate,
                basis=month.basis,
            )
            for month in months
            if month.position_id in fte_by_position
        ],
    )


# --- the fixed-amount basis (SC-5-03; ADR-0013, addendum 2026-09-25) ------------------------------


def _fixed_amount_lines(session: Session, scenario: Scenario) -> list[FixedAmountLine]:
    """Every `cost_basis = 'fixed_amount'` position of this scenario, as the formula reads them.

    **No catalogue join, no allocation join, no snapshot branch.** Unlike `_worked_months`, this
    query has nothing to resolve *for a month*: `fixed_amount`/`fixed_amount_currency` are a
    position's own columns, read live before and after approval alike (ADR-0004, addendum 2026-09-25
    SC-5-03, point 2 — "brak trzeciego miejsca migawkowego": the write guard protects an approved
    scenario's row, not a copy of it). Rows are ordered by `id` for a deterministic
    `assumptions_used.lines`, the same reason `_distinct_windows` sorts the worked-time windows.
    """
    statement = (
        sa.select(
            StaffingPosition.id,
            StaffingPosition.fixed_amount,
            StaffingPosition.fixed_amount_currency,
        )
        .where(
            StaffingPosition.scenario_id == scenario.id,
            StaffingPosition.cost_basis == COST_BASIS_FIXED_AMOUNT,
        )
        .order_by(StaffingPosition.id)
    )
    return [
        FixedAmountLine(
            position_id=row.id, amount=row.fixed_amount, currency=row.fixed_amount_currency
        )
        for row in session.execute(statement)
    ]


@dataclass(frozen=True)
class ScenarioCostView:
    """One scenario's base personnel cost **as one caller may see it**, resolved in one read.

    A value object for the reason `CallerProjectView` is one: the shaping layer never receives a
    `Session` and must never grow a query of its own, so the per-(caller, project) flag has to
    arrive from here, from the same scope read that returned the scenario.

    - `user_id` names the caller the flag was resolved for, so the shaping layer can refuse to shape
      this view for anybody else (the `_without_personnel_costs` assertion, applied again).
    - `can_view_personnel_costs` is the caller's `project_access` flag **for the project this
      scenario belongs to** — never "any of the caller's assignments" (criterion K-04).
    - `cost` is the full answer, amount and rates included. It is **not** safe to serialise as it
      stands: the conjunction is applied by `app.api.response_shaping`.
    """

    user_id: str
    scenario: Scenario
    can_view_personnel_costs: bool
    cost: PersonnelCostAnswer
    paid_absence: PaidAbsenceCostAnswer
    """The paid-absence component (SC-5-06; ADR-0013, addendum 2026-09-23 SC-5-06) — beside `cost`,
    never added to it. Carried on the same view so the same conjunction gates it (point 6)."""
    fixed_amount: FixedAmountCostAnswer
    """The fixed-amount basis's own component (SC-5-03; ADR-0013, addendum 2026-09-25 SC-5-03) — a
    **third**, independent figure beside `cost` (worked time) and `paid_absence`, never summed
    with either (Out of scope of SC-5-03: "a sum of the scenario's cost combining the bases").
    Carried on the same view for the same reason `paid_absence` is: one conjunction, one place it is
    decided. **Never carries a surcharge** (SC-5-02): `fixed_amount_cost` takes no rate at all to
    apply one to (K-01 of both tasks — the fixed-amount formula does not import the worked-time
    formula, the surcharge formula, or anything the surcharge is computed from); a fixed amount is
    the stated cost as entered, not a base the fully-loaded figure marks up.
    """
    assigned_fte: AssignedFteCostAnswer
    """The assigned-FTE basis's own component (SC-5-04; ADR-0013, addendum 2026-09-29 SC-5-04) — a
    **fourth** independent figure beside `cost`, `paid_absence` and `fixed_amount`, never summed
    with any of them (the total belongs to the F-10 block: `included_cost` does not contain it, so
    profit, margin and markup are overstated for a scenario with FTE positions until that block
    sums the components — named, not repaired). Carried on the same view, gated by the same
    conjunction. **Never carries a surcharge** (point 7). Under a what-if raise it is recomputed on
    the raised rates, through the same shared per-(position, month) structure the base cost
    uses."""
    status_at_read: ScenarioStatus
    """The scenario's status **as this read saw it** — the status that chose live catalogue versus
    snapshot for `cost` — copied into an immutable value right after this read's own
    `session.refresh` (SC-7-03, Issue #118; ADR-0015, addendum SC-7-03, point 3). Never
    `scenario.status` read later: `scenario` is an identity-mapped object another read in the same
    session may refresh again. Compared against `app.data.commercial_terms.ScenarioCommercialView.
    status_at_read` by the race guard in `app.data.scenario_results`/`app.data.scenario_what_if`.
    Not a personnel-cost figure and never serialised: `app.api.response_shaping` does not read
    it."""
    fully_loaded_cost: FullyLoadedPersonnelCostAnswer
    """The fully loaded base cost (SC-5-02) — a second, named field beside `cost`, never a
    replacement of it (criterion K-01). Gated by the identical conjunction, through the identical
    `SCENARIO_COST_FIELDS` set (K-03)."""
    fully_loaded_paid_absence: FullyLoadedPaidAbsenceCostAnswer
    """The paid-absence component's fully loaded cost (SC-5-02, criterion K-05) — beside
    `paid_absence`, never added to it, for the same reason `paid_absence` sits beside `cost`."""


def scenario_cost_for_caller(
    session: Session,
    caller: CallerIdentity,
    project_id: uuid.UUID,
    scenario_id: uuid.UUID,
    *,
    period_shift_months: int = 0,
) -> ScenarioCostView | None:
    """The base personnel cost of one scenario — or `None`, with no way to tell why (K-05).

    `None` is "no such scenario *for this caller*"; a scenario whose cost cannot be stated is a view
    whose cost is a named state, never `None` and never `0`.
    """
    in_scope = scenario_view_in_scope(session, caller, project_id, scenario_id)
    if in_scope is None:
        return None
    project_view, scenario = in_scope
    # Refreshed, not trusted from the identity map: the status decides live-versus-snapshot, and an
    # object loaded earlier in the same session may predate an approval committed since.
    session.refresh(scenario)
    status_at_read = scenario.status
    source, months, positions = _worked_months(
        session, scenario, period_shift_months=period_shift_months
    )
    inputs = dispatch_cost_inputs(months, positions)
    # The paid-absence component is costed at **these** rates — the resolution of every
    # (position, month) of the shared grid, live or frozen by the same status — and asks no
    # predicate of its own
    # (ADR-0013, addendum 2026-09-23 SC-5-06, point 3). Its hours are read from a different source,
    # and its amount is never added to the base amount (point 4).
    rates = {(month.position_id, month.period_month): month.rate for month in months}
    absence_months = paid_absence_months(
        session, scenario, rates, period_shift_months=period_shift_months
    )
    return ScenarioCostView(
        user_id=project_view.user_id,
        scenario=scenario,
        can_view_personnel_costs=project_view.can_view_personnel_costs,
        cost=base_personnel_cost(
            inputs.worked_months, rate_source=source, scenario_currency=scenario.currency
        ),
        paid_absence=paid_absence_cost(absence_months, scenario_currency=scenario.currency),
        fully_loaded_cost=fully_loaded_personnel_cost(
            inputs.worked_months, rate_source=source, scenario_currency=scenario.currency
        ),
        assigned_fte=assigned_fte_cost(
            inputs.assigned_fte_lines,
            inputs.assigned_fte_months,
            rate_source=source,
            scenario_currency=scenario.currency,
        ),
        fixed_amount=fixed_amount_cost(
            _fixed_amount_lines(session, scenario), scenario_currency=scenario.currency
        ),
        fully_loaded_paid_absence=fully_loaded_paid_absence_cost(
            absence_months, scenario_currency=scenario.currency
        ),
        status_at_read=status_at_read,
    )
