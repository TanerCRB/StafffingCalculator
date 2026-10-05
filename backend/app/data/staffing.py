"""The only path by which staffing positions, their monthly hours and their absences are read or
written (SC-3-01, SC-3-02).

Three mechanisms live here, and none of them is new — each is an existing mechanism of this
repository applied to the first table that inherits its scope from a parent:

1. **Scope.** There is no `scenario_for_caller` and there must not be one (ADR-0001, addendum
   2026-09-19). The address carries both identifiers, the scope comes from
   `app.data.project_reads.project_for_caller` — the very same entry point the project endpoints
   use — and the scenario's membership of that project is checked against the `Project.scenarios`
   collection that function already eager-loads. One function, not two composing on one query; and
   "not yours", "no such project" and "that scenario belongs to another project" arrive at the API
   as the same absence, so none of them can be told apart from the others (ADR-0005, addendum
   2026-09-19, point 4 — for the write path as much as for the read one).

2. **The refusal of a write to an `approved` scenario** (ADR-0004). Four write paths, four
   statements, each carrying its own guard *inside* the statement that writes:

   - `INSERT ... SELECT ... FROM scenarios WHERE id = :scenario_id AND status <> 'approved'` —
     an `INSERT` has no `WHERE` to hang a predicate on, so the predicate lives in the `SELECT` the
     row comes from (ADR-0004, addendum 2026-09-19, point 2). Refusal is zero rows affected; the
     reason is diagnosed only *after* the refusal, exactly as `project_writes._diagnose_refusal`
     does it. The same construction inserts an absence, one level down (`create_absence`).
   - the allocation edit and the absence delete, each a single statement in which the position's
     concurrency token, the scenario's status and the existence of the target row are all part of
     the `WHERE` — see `update_allocation` and `delete_absence`.

3. **Describing a failed write without quoting what was written** (NF-11): `app.data.write_errors`,
   the same classification by SQLSTATE the catalogue write path uses. Not re-implemented here.

**SC-3-02 changes one of the three and adds nothing to the list.** The `approved` guard above was
delivered with a named, dated gap: under `READ COMMITTED` an approval committing *concurrently with*
a child write was not excluded, and the note said the first real approval path had to close it for
every child table at once. That path now exists, and the closure is
`app.data.scenario_guard` — the parent row is selected `FOR UPDATE` **inside** each guarded
statement, so the two transactions serialise on it. Every write path in this module goes through it:
the position `INSERT`, the allocation `UPDATE`, the absence `INSERT` and the absence `DELETE`. It is
one mechanism, not one per table, because the ADR requires the window closed for all of them at once
(ADR-0004, addendum 2026-09-22, point 5; criterion K-20).

**Absences (F-05) are the third table of this aggregate and reuse every one of the three
mechanisms**: the same scope resolution (`scenario_in_scope`), the same `approved` refusal in the
statement that writes, the same `staffing_position.updated_at` token (ADR-0007, addendum
2026-09-22 — no token of their own), the same SQLSTATE classification.

**SC-5-03 adds a fifth write path and no new mechanism**: `update_position_cost_basis` edits
`cost_basis`/`fixed_amount`/`fixed_amount_currency` — columns of the position row itself — in one
`UPDATE staffing_position SET updated_at = now(), <changes> WHERE …` that carries the same
concurrency token and the same `unapproved_scenario` guard as every write above (criterion K-03).
**Gate 1 fix (Guardian, 2026-09-25):** that same `WHERE` also carries `cost_basis =
'fixed_amount'` whenever `changes` would otherwise let an amount land on a row without also
switching its basis — `CostBasisMismatch`, a third state-refusal beside `ApprovedScenarioFrozen`
and `ConcurrentStaffingEditConflict`, not a fourth mechanism. **Gate 1 fix, R-02 (Reviewer,
2026-09-26):** `_diagnose_position_refusal` checks the token *before* `CostBasisMismatch`, not
after — `cost_basis`, unlike `approved`, toggles both ways, so a stale token must win the diagnosis
every time, or a race lets one caller's stale retry silently overwrite another's fresh, deliberate
switch with no warning to either side.

**SC-2-06 adds a sixth write path and no new mechanism**: `assign_person` writes `person_id` — the
optional named person of the position (F-03; ADR-0019) — in the same single-`UPDATE` shape as the
cost-basis edit and under the same `unapproved_scenario` guard, plus "the person exists" in the same
`WHERE`, but against its **own** marker, `person_assignment_updated_at`, leaving the position's
`updated_at` untouched (D-4 = B; ADR-0007 addendum 2026-09-28): two markers on one row, disjoint
columns, exactly one marker per write path. It is the **only** function that writes that column:
every other write here names its columns explicitly and none of them names `person_id`, so omitting
the person from any other request means "unchanged", never "removed" (ADR-0005, addendum 2026-09-27,
point 6). The copy cascade carries `person_id` by reflection — the copy points at the same person
(ADR-0004, addendum 2026-09-27, point 4). Nothing in this module reads a person's *name*: the name
lives in the register (`app.data.people`), and a position response carries at most the id, gated in
response shaping.

**Reading a position now also derives its capacity** (`app.domain.capacity`), which is why the read
functions return a `StaffingPositionView` rather than a bare row. The derived figure travels
*beside* the typed `availability_hours` and never replaces it: nothing in this module writes a
computed number into `staffing_position_allocation`, and criterion K-08 asserts that at the level of
the statements issued.

What this module deliberately does **not** do: resolve a rate or read a cost (ADR-0005, addendum
2026-09-19, point 5). A position carries the four dimension ids and nothing priced, which is why
there is no cost gate on this path and why `StaffingPositionView` carries no per-caller flag — a
view object exists to carry a per-(caller, row) flag, and there is still none here to carry. It
reads the catalogue for three things, all of them organisational data with no scope of its own
(ADR-0001, addendum 2026-09-22; ADR-0005, addendum 2026-09-22 SC-3-03, point 1): the working
calendar of the position's location, the **leave budget** of its (calendar, engagement type) pair,
and which absence type carries `is_statutory_leave`. None of the three is gated on
`PERSONNEL_COSTS_READ` and none of them is an amount — a budget is a number of days (ADR-0005,
addendum SC-3-03, point 3). The day a *cost* is derived from one, that figure is a cost field and
goes through the SC-1-08 conjunction (point 4).
"""

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, selectinload

from app.core.identity import CallerIdentity
from app.data.absence_budget import BudgetKey, budgets_for_months, statutory_leave_type
from app.data.column_copy import values_to_copy
from app.data.project_reads import CallerProjectView, project_for_caller
from app.data.rate_windows import shift_calendar_month_date
from app.data.risk_copy import copied_risk_ids, remapped
from app.data.scenario_guard import unapproved_scenario
from app.data.working_calendar import basis_by_location
from app.data.write_errors import WriteFailed, WriteRefused, failure_for
from app.domain.absence_budget import AbsenceBudget, StatutoryLeaveType, month_budget_share
from app.domain.capacity import (
    AbsenceSpan,
    CalendarBasis,
    MonthCapacity,
    absence_day_equivalents_between,
    month_capacity,
)
from app.models.additional_cost import AdditionalCost
from app.models.person import Person
from app.models.scenario import Scenario, ScenarioStatus
from app.models.staffing import (
    COST_BASIS_ASSIGNED_FTE,
    COST_BASIS_FIXED_AMOUNT,
    COST_BASIS_WORKED_TIME,
    HOURS_COLUMNS,
    StaffingPosition,
    StaffingPositionAbsence,
    StaffingPositionAllocation,
)

_POSITION_TABLE = StaffingPosition.__table__
_ALLOCATION_TABLE = StaffingPositionAllocation.__table__
_ABSENCE_TABLE = StaffingPositionAbsence.__table__

EDITABLE_ALLOCATION_FIELDS: frozenset[str] = frozenset(HOURS_COLUMNS)
"""Everything the allocation edit path will ever write: the three hour figures, and nothing else.

An allow-list rather than "whatever the request carried". `period_month` is absent on purpose —
moving a month is not an edit of a value but a different row, and an edit that could rewrite it
would be an undocumented way around `UNIQUE (position_id, period_month)`. `position_id` and `id` are
not user input at all, and there is no `updated_at` on this table to write (ADR-0007, addendum
2026-09-19: the token is the position's)."""

EDITABLE_POSITION_FIELDS: frozenset[str] = frozenset(
    {"cost_basis", "fixed_amount", "fixed_amount_currency", "assigned_fte"}
)
"""Everything `update_position_cost_basis` will ever write (SC-5-03, SC-5-04; ADR-0013, addenda
2026-09-25 SC-5-03 and 2026-09-29 SC-5-04): the personnel-cost basis of the position itself and the
figure each basis stores. An allow-list, for the same reason
`EDITABLE_ALLOCATION_FIELDS` is one — the four dimension ids, `headcount` and the two dates have no
edit path in this cost-basis operation, and this set must not silently grow to include them. SC-3-09
gives those non-cost fields their own `EDITABLE_POSITION_DETAILS_FIELDS` allow-list."""

EDITABLE_POSITION_DETAILS_FIELDS: frozenset[str] = frozenset(
    {
        "role_id",
        "seniority_id",
        "location_id",
        "engagement_type_id",
        "headcount",
        "start_date",
        "end_date",
    }
)
"""The non-cost staffing fields editable through the position-details path (SC-3-09).

Cost-basis values remain on their existing endpoint and allow-list so ordinary staffing editors
cannot write a field whose visibility is governed by the personnel-cost ADRs."""


class StaffingWriteFailed(WriteFailed):
    """A staffing write failed for a reason nothing here established — a `500`.

    Same division as in the catalogue path (R-01): a numeric overflow, a statement timeout or a
    dropped connection must not reach a caller as a `409` describing a conflict nobody observed. The
    message is built from identifiers only (SQLSTATE, constraint name), because PostgreSQL's own
    `DETAIL: Failing row contains (…)` line carries the whole row (NF-11).
    """


class StaffingWriteRefused(StaffingWriteFailed, WriteRefused):
    """The database refused the write for a reason its SQLSTATE names — a `409`.

    A subclass of both, so `except StaffingWriteFailed` still catches every failure of this path
    while `except StaffingWriteRefused` catches only what a caller can act on.
    """


class StaffingWriteRejected(RuntimeError):
    """The write was understood, reached this layer, and was refused *by state* — a `409`.

    Distinct from `StaffingWriteFailed`, which means the statement broke. Two subclasses, two
    independent reasons, exactly as `ProjectEditRefused` splits them (ADR-0007: "one place, two
    independent reasons for refusal"). No subclass ever quotes a field value — NF-11 applies to a
    refusal as much as to a failure.
    """


class ApprovedScenarioFrozen(StaffingWriteRejected):
    """The scenario is `approved`, so its staffing is frozen (ADR-0004).

    The permanent reason: retrying with a fresh token will never help. Further changes need a new
    version, i.e. a copy of the scenario — which is a `draft` and accepts the same write
    (`app.data.project_writes.copy_scenario`).
    """


class ConcurrentStaffingEditConflict(StaffingWriteRejected):
    """The position changed since the caller read it (ADR-0007, NF-05).

    Carries nothing about the competing change: this is a refusal, not a merge. The token is the
    *position's* `updated_at`, so an edit of another month of the same position invalidates it — a
    false collision accepted by name in ADR-0007's addendum of 2026-09-19, because the position is
    the unit of editing and the month is not.
    """


class CostBasisMismatch(StaffingWriteRejected):
    """`fixed_amount`/`fixed_amount_currency` were named without also naming `cost_basis`, and the
    position's *current, stored* basis is not `fixed_amount` (gate 1 SC-5-03, Guardian finding).

    The model's invariant is "`fixed_amount`/`fixed_amount_currency` are `NULL` for the default
    basis" (`app.models.staffing`) — a `PATCH` that writes an amount onto a `worked_time` row would
    leave that invariant holding in the database's eyes (the CHECK does not forbid a stray amount on
    `worked_time`, `test_worked_time_basis_with_a_stray_amount_or_currency_is_not_refused_by_this_
    check` names that permissiveness on purpose) while breaking it in the sense every reader of this
    module relies on: a `worked_time` row with a leftover amount nobody asked for. Refused *by
    state*, exactly like `ApprovedScenarioFrozen`/`ConcurrentStaffingEditConflict` — the same
    guarded `UPDATE` decides it, in the same `WHERE`, not a second check bolted on afterwards.

    A caller switching `cost_basis` to `fixed_amount` in the same request is unaffected: `changes`
    then contains `cost_basis`, and the requirement below never applies. A caller editing the amount
    of a position that is *already* `fixed_amount` is unaffected either — the requirement is about
    what the row **is**, not about what the request also happens to name.

    **Reported only when the caller's own token is still fresh** (Reviewer R-02, correcting this
    exception's first diagnosis order): `cost_basis` toggles both ways, unlike `approved`, so a
    `cost_basis` read *after* a concurrent write has already changed it is not a fact about the
    caller's own request — see `_diagnose_position_refusal`. A stale token is always
    `ConcurrentStaffingEditConflict`, never this.
    """


_AMOUNT_FIELDS: frozenset[str] = frozenset({"fixed_amount", "fixed_amount_currency"})


def _requires_an_existing_assigned_fte_basis(changes: Mapping[str, Any]) -> bool:
    """Whether `changes` needs the row's *current* `cost_basis` to already be `assigned_fte`.

    True exactly when `assigned_fte` is named and `cost_basis` is not — the FTE correction of a
    position that is already on the FTE basis (SC-5-04). On any other basis the CHECK
    `assigned_fte_only_on_its_basis` would refuse the stray value anyway; this gives the caller the
    same named refusal (`CostBasisMismatch`) the fixed amount gets, decided in the same `WHERE`."""
    return "cost_basis" not in changes and "assigned_fte" in changes


def _values_for_a_basis_switch(changes: Mapping[str, Any]) -> dict[str, Any]:
    """The columns the `UPDATE` writes: `changes` plus the clearing a basis switch implies.

    The three bases store different figures, and the database keeps each on its own basis only
    (`assigned_fte_only_on_its_basis`, `assigned_fte_not_with_fixed_amount`). A switch is therefore
    one atomic statement that also empties what the new basis must not carry:

    - to any basis but `assigned_fte`: the stored FTE is cleared (`assigned_fte = NULL`);
    - to `assigned_fte`: a stated `fixed_amount`/`fixed_amount_currency` is cleared.

    `setdefault`, never an override: a value the caller named explicitly is written as named and, if
    it contradicts the basis, refused by the CHECK rather than silently replaced. A switch to
    `worked_time` still leaves a stray fixed amount as before (SC-5-03, unchanged)."""
    values = dict(changes)
    new_basis = values.get("cost_basis")
    if new_basis is not None and new_basis != COST_BASIS_ASSIGNED_FTE:
        values.setdefault("assigned_fte", None)
    if new_basis == COST_BASIS_ASSIGNED_FTE:
        values.setdefault("fixed_amount", None)
        values.setdefault("fixed_amount_currency", None)
    return values


def _requires_an_existing_fixed_amount_basis(changes: Mapping[str, Any]) -> bool:
    """Whether `changes` needs the row's *current* `cost_basis` to already be `fixed_amount`.

    True exactly when `fixed_amount`/`fixed_amount_currency` are named and `cost_basis` is not —
    the one combination the schema validator cannot refuse on its own, because it has no view of
    the row (`StaffingPositionCostBasisEditRequest._at_least_one_field_and_consistent_with_its_
    basis`'s own residual, closed here instead, at the one place that does have the row: the
    statement that writes it)."""
    return "cost_basis" not in changes and bool(_AMOUNT_FIELDS & set(changes))


class AllocationFieldNotEditable(RuntimeError):
    """A caller asked to write a column outside `EDITABLE_ALLOCATION_FIELDS`, or asked for nothing.

    A programming error, not a client error — the counterpart of
    `app.data.project_writes.ProjectFieldNotEditable`: the request schema cannot express either
    case, so this guards the *next* call site (an import, a script, a future endpoint) and not the
    one that exists today. Deliberately not a `StaffingWriteRejected`, so it cannot be answered with
    a `409` that would describe a state of the data as the reason.
    """


def _failure(error: SQLAlchemyError) -> WriteFailed:
    """Classify one failed staffing write by SQLSTATE, into this module's pair of exceptions.

    Spelled once so both write paths classify identically — a per-call-site `except` block is how
    one of them ends up answering `409` for a defect while the other does not.
    """
    return failure_for(
        error,
        subject="staffing position",
        refused=StaffingWriteRefused,
        failed=StaffingWriteFailed,
    )


def scenario_in_scope(
    session: Session, caller: CallerIdentity, project_id: uuid.UUID, scenario_id: uuid.UUID
) -> Scenario | None:
    """The scenario named by the address — or `None`, with no way to tell why.

    Not a second scope function (ADR-0001, addendum 2026-09-19). It calls `project_for_caller`,
    which is where the `project_access` predicate lives, and then looks for the scenario in the
    `Project.scenarios` collection that call already loaded (`selectinload`). No `select(Scenario)`
    is issued here and none may be added: a query of its own would be a second place deciding what a
    caller may see, and it would answer for scenarios of projects the caller cannot see.

    Three different situations collapse into the same `None` — no such project, the project is
    outside the caller's scope, the scenario belongs to a different project — and that is the point:
    a caller of this function cannot tell them apart even if it wanted to, so the API has nothing
    from which to build an "exists, but not yours" answer.
    """
    in_scope = scenario_view_in_scope(session, caller, project_id, scenario_id)
    return None if in_scope is None else in_scope[1]


def scenario_view_in_scope(
    session: Session, caller: CallerIdentity, project_id: uuid.UUID, scenario_id: uuid.UUID
) -> tuple[CallerProjectView, Scenario] | None:
    """`scenario_in_scope`, keeping the caller's project view it was decided from (SC-5-01).

    **The same scope decision, not a second one** — `scenario_in_scope` is this function with the
    view dropped. It exists because the first path gated on personnel costs inside a scenario
    (`app.data.personnel_cost`) needs `project_access.can_view_personnel_costs` for **the project
    the scenario belongs to**, and the one place that flag may come from is the statement that
    decided the caller may see that project (ADR-0005, addendum 2026-09-19, point 6; addendum
    2026-09-23 SC-5-01, point 2). Reading it again afterwards would be a second query deciding a
    per-caller fact, and reading it from "any assignment of the caller" would be the per-subject
    answer to a
    per-assignment question (criterion K-04).
    """
    view = project_for_caller(session, caller, project_id)
    if view is None:
        return None
    for scenario in view.project.scenarios:
        if scenario.id == scenario_id:
            return view, scenario
    return None


def ensure_allocation_months_fit_shift(
    session: Session, scenario_id: uuid.UUID, months: int
) -> None:
    """Refuse a staffing-month shift that cannot be represented by the application's date type."""
    period_months = session.scalars(
        sa.select(StaffingPositionAllocation.period_month)
        .join(StaffingPosition, StaffingPosition.id == StaffingPositionAllocation.position_id)
        .where(StaffingPosition.scenario_id == scenario_id)
    ).all()
    for period_month in period_months:
        shift_calendar_month_date(period_month, months)


@dataclass(frozen=True)
class StaffingPositionView:
    """One position, its month rows, its absences — and the capacity derived for each month.

    A value object rather than a bare row, for the reason `CallerProjectView` is one: the layer that
    shapes the response never receives a `Session` and must never grow a query of its own, so
    everything the payload needs arrives from here, resolved in the same read.

    **It carries no per-caller flag, and that absence is the statement.** A `CallerProjectView`
    exists to pair a row with `project_access.can_view_personnel_costs`; nothing on a staffing
    position is gated on a permission (ADR-0005, addendum 2026-09-19, point 5), so a `user_id` field
    here would advertise a gate that does not exist. The day a resolved rate appears on a position
    (F-07) this type grows the flag *and* the shaping layer grows the SC-1-08 conjunction.

    `capacity` is keyed by `period_month` and holds one entry per allocation row of the position —
    the derived figure that travels *beside* `availability_hours` (K-08). A position whose location
    names no calendar still gets an entry for every month: the named `no_calendar` state, never a
    missing key and never a zero (K-23).
    """

    position: StaffingPosition
    capacity: Mapping[date, MonthCapacity]


def _views_of(
    session: Session, positions: Sequence[StaffingPosition]
) -> Sequence[StaffingPositionView]:
    """Pair each position with the capacity derived for each of its months.

    Three lookups for the whole sequence, none of them per position: the calendars keyed by
    location (`basis_by_location`), the budgets keyed by (calendar, engagement type) and month
    (`budgets_for_months`), and the one absence type budgets settle against
    (`statutory_leave_type`). 200 positions in three locations are three statements, not 600. The
    absences are read from the rows already loaded, so nothing here queries per position either.

    A location with no calendar is simply absent from the calendar mapping, so `basis` is `None`,
    `month_capacity` produces the named state and the budget answers with the *same* state — a
    location with no calendar has no budget either, and that is one named state to show rather than
    two (ADR-0008, addendum 2026-09-22 SC-3-03, point 3a). The branch is one `dict.get`, and there
    is no fallback value anywhere on this path to fall into instead (K-23, K-01, K-08).

    A month no budget window covers is likewise absent from the budget mapping: the named "no
    budget" state is the *absence* of a key, never a zero the caller could add up (K-06, K-08).
    """
    bases = basis_by_location(session, [position.location_id for position in positions])
    statutory = statutory_leave_type(session)
    keys, months = budget_keys_and_months(positions, bases)
    budgets = budgets_for_months(session, keys, months)
    return [
        position_view(position, bases.get(position.location_id), budgets, statutory)
        for position in positions
    ]


def budget_keys_and_months(
    positions: Sequence[StaffingPosition],
    bases: Mapping[uuid.UUID, CalendarBasis],
    allocation_months: Mapping[tuple[uuid.UUID, date], date] | None = None,
) -> tuple[list[BudgetKey], list[date]]:
    """The (calendar, engagement type) pairs and the months whose budgets these positions read.

    A position whose location names no calendar contributes nothing: it has no budget either
    (ADR-0008, addendum 2026-09-22 SC-3-03, point 3a). Public since SC-5-06, because the
    paid-absence cost (`app.data.paid_absence_cost`) asks the same question — of the live
    catalogue for a draft, of the approval snapshot for an approved scenario — and a second
    spelling of "which budgets does this grid read" is how the grid and the cost would come to
    disagree about one month.
    """
    keys: list[BudgetKey] = []
    months: list[date] = []
    for position in positions:
        basis = bases.get(position.location_id)
        if basis is None:
            continue
        keys.append((basis.calendar_id, position.engagement_type_id))
        months.extend(
            allocation.period_month
            if allocation_months is None
            else allocation_months.get(
                (position.id, allocation.period_month), allocation.period_month
            )
            for allocation in position.allocations
        )
    return keys, months


def position_view(
    position: StaffingPosition,
    basis: CalendarBasis | None,
    budgets: Mapping[tuple[BudgetKey, date], AbsenceBudget],
    statutory: StatutoryLeaveType | None,
    allocation_months: Mapping[date, date] | None = None,
) -> StaffingPositionView:
    """One position's view: its months, its absences and the capacity of each month.

    **A pure function of what it is handed** — no `Session`, no query — and public since SC-5-06:
    the paid-absence cost reads each month's `MonthCapacity.budget` from here, so the budget top-up
    it costs is by construction the one the capacity subtracts (ADR-0013, addendum 2026-09-23
    SC-5-06, point 2b). For an approved scenario the inputs come from the approval snapshot
    (`app.data.paid_absence_cost`); the rule applied to them is this one, not a copy.

    **Which absences count against the budget is decided here, once, from the flagged type** — the
    span carries a `bool` and the capacity formula never sees an absence type id (ADR-0008, addendum
    2026-09-22 SC-3-03, point 8). **With no flagged type at all the budget is not applied and the
    answer says so** (`statutory_leave_named=False` → `NO_STATUTORY_LEAVE_TYPE`): the `max` of point
    9 has no second operand, because nothing says which booked absences the entitlement already
    covers, and applying it anyway deducts the same leave twice — once as itself and once as an
    entitlement that absorbed nothing (reviewer R-02). That is the state a freshly migrated database
    is in, so the silent version of this answer would have been the default one.

    The `max` of point 9 is resolved over the budget's **window**, so the statutory days are counted
    over `effective_from`..`effective_to` and not over the month — `absence_day_equivalents_between`
    is the same intersection rule the monthly deduction uses, over a different range. Reversing the
    order (a `max` per month) is the mutation that turns 24 budgeted days plus 20 booked in July
    into 42 days deducted over a year.

    **That count is memoised per budget window** (reviewer R-04). Its value depends on the position
    and the window and on nothing else, while the loop below runs per *month*: recomputing it there
    walked every day of every window once per month of the grid — measured at 2.2 s of pure Python
    for an NF-03 grid with no absences at all, and 21 s for a five-year window, before a single row
    was read. The memo is local to one position and one call, so it can hold nothing stale.
    """
    spans = [
        AbsenceSpan(
            start_date=absence.start_date,
            end_date=absence.end_date,
            is_statutory_leave=(
                statutory is not None and absence.absence_type_id == statutory.absence_type_id
            ),
        )
        for absence in position.absences
    ]
    statutory_spans = [span for span in spans if span.is_statutory_leave]
    key: BudgetKey | None = (
        None if basis is None else (basis.calendar_id, position.engagement_type_id)
    )
    absorbed: dict[uuid.UUID, int] = {}
    capacity: dict[date, MonthCapacity] = {}
    for allocation in position.allocations:
        period_month = (
            allocation.period_month
            if allocation_months is None
            else allocation_months.get(allocation.period_month, allocation.period_month)
        )
        budget = None if key is None else budgets.get((key, period_month))
        share = None
        if budget is not None and basis is not None:
            if budget.budget_id not in absorbed:
                absorbed[budget.budget_id] = absence_day_equivalents_between(
                    basis, statutory_spans, budget.effective_from, budget.effective_to
                )
            share = month_budget_share(
                budget,
                period_month=period_month,
                headcount=position.headcount,
                standard_hours_per_day=basis.standard_hours_per_day,
                statutory_leave_named=statutory is not None,
                manual_statutory_days_in_window=absorbed[budget.budget_id],
            )
        capacity[period_month] = month_capacity(
            basis,
            headcount=position.headcount,
            period_month=period_month,
            absences=spans,
            budget_share=share,
        )
    return StaffingPositionView(position=position, capacity=capacity)


DEFAULT_STAFFING_POSITION_LIST_LIMIT = 200
"""The page size assumed when a caller names `offset` but not `limit` (SC-3-05, ADR-0017 point 6).

Not the default for "no parameters at all" — that case stays "the whole grid" (K-01), unchanged
from the shape SC-3-01 shipped. This constant only fills in the other half of a partial request, and
its value is NF-03's own reference scale (200 positions), so a caller who names only `offset` still
gets a realistic page rather than an arbitrarily small or unbounded one."""

MAX_STAFFING_POSITION_LIST_LIMIT = 1000
"""The ceiling `limit` may name — refused (`422`) above it, never silently clamped (K-06), for the
reason `app.data.catalog.MAX_RATE_LIST_LIMIT` gives: a clamp would answer fewer rows than asked for
without saying so. Five times NF-03's reference scale (200 positions), the same headroom reasoning
`MAX_ALLOCATION_MONTHS` applies to a single position's grid."""

MAX_STAFFING_POSITION_LIST_OFFSET = 1_000_000
"""The ceiling `offset` may name — refused (`422`) above it, exactly as `limit` is, and for the
same two reasons `app.data.catalog.MAX_RATE_LIST_OFFSET` documents: an unbounded value reaches the
driver as a `bigint` a client can type by accident, and `OFFSET n` still walks and discards `n`
rows before the first row of the page, so the server side is bounded by `limit + offset`, not by
`limit` alone."""


def _staffing_position_page_statement(
    scenario_id: uuid.UUID, *, limit: int, offset: int
) -> sa.Select[tuple[uuid.UUID, int]]:
    """The **one** statement that decides which position ids are on this page, and the total.

    Public shape mirrored from `app.data.catalog.rate_page_statement` (ADR-0017 point 5): `total` is
    a scalar subquery in the same `SELECT` as the page, evaluated in the same snapshot, so no
    concurrent write between the two halves can make the response describe a scenario that never
    existed at any instant. The page itself is a bounded top-N (`ORDER BY … LIMIT … OFFSET`) over
    `(start_date, id)` — the existing total order of `list_positions` (K-03) — filtered by one
    `scenario_id`.

    **The planner can answer it with an index scan that stops after `limit + offset` rows, and not
    merely as an aspiration** (gate-2 review R-02, Reviewer finding — this docstring used to claim
    that sentence with no supporting index, the identical gap `RATE_PAGE_INDEX`'s own module
    docstring records having existed for `GET /catalog/rates`): the composite btree
    `app.models.staffing.STAFFING_POSITION_PAGE_INDEX`, leading on `scenario_id` then `start_date`
    then `id`, is exactly this statement's `WHERE` column followed by its `ORDER BY` columns, so a
    scan of it can walk straight to this scenario's rows in the order the page needs and stop after
    `limit + offset` of them — never sorting the rest of the scenario's positions, let alone the
    table's. Proven against the real plan, not asserted from the shape of the SQL alone:
    `test_r_02_a_page_is_read_as_a_bounded_top_n_not_a_sort_of_the_scenario`
    (`tests/test_staffing_pagination.py`), the same construction
    `test_r_01_a_page_is_read_as_a_bounded_top_n_not_a_sort_of_the_whole_catalogue` uses for its
    catalogue counterpart.

    Returns `(id, total)` rather than the mapped entity: the page's own month rows and absences
    still need `selectinload`, which does not compose with a subquery built from bare columns, so
    the positions themselves are re-read by id once the page is known (`list_positions`).
    """
    page = (
        sa.select(StaffingPosition.id, StaffingPosition.start_date)
        .where(StaffingPosition.scenario_id == scenario_id)
        .order_by(StaffingPosition.start_date, StaffingPosition.id)
        .limit(limit)
        .offset(offset)
        .subquery()
    )
    total = (
        sa.select(sa.func.count())
        .select_from(StaffingPosition)
        .where(StaffingPosition.scenario_id == scenario_id)
        .scalar_subquery()
    )
    return sa.select(page.c.id, total.label("total")).order_by(page.c.start_date, page.c.id)


def _staffing_position_total_statement(scenario_id: uuid.UUID) -> sa.Select[tuple[int]]:
    """`count(*)` over one scenario's positions — the fallback for the one case the page cannot
    carry: an empty page (`offset` past the end, or a scenario with no positions at all), which
    has no row to attach a `total` to (the same shape `app.data.catalog.rate_total_statement`
    answers)."""
    return (
        sa.select(sa.func.count())
        .select_from(StaffingPosition)
        .where(StaffingPosition.scenario_id == scenario_id)
    )


def list_positions(
    session: Session,
    caller: CallerIdentity,
    project_id: uuid.UUID,
    scenario_id: uuid.UUID,
    *,
    limit: int | None = None,
    offset: int = 0,
) -> tuple[Sequence[StaffingPositionView], int] | None:
    """Every staffing position of one scenario, each with its month rows, and the total count — or
    `None` for "no such" (SC-3-01; paginated since SC-3-05, ADR-0017).

    `None`, not an empty list: a scenario the caller may not see and a scenario with no positions
    must not be the same answer, or the absence of staffing would be indistinguishable from the
    absence of access. The `None` itself carries no reason (see `scenario_in_scope`).

    **`limit=None` means "the whole grid", not "page one of a default size"** (K-01, gate-1 decision
    Q-4/A): the exact query SC-3-01 always ran, unchanged, because there is no frontend consumer of
    this endpoint yet to leave holding a silently truncated page. `total` in that branch is `len` of
    what was just read — every position of the scenario, so it always equals the number returned.

    **A named `limit` runs a bounded top-N instead** (K-02, K-04):
    `_staffing_position_page_statement` decides the page's ids and the total in one snapshot, and
    this function then re-reads exactly those ids with the same `selectinload` two extra statements
    the unbounded branch uses (NF-03 — 200 positions in three statements, not 400, whichever branch
    runs). An empty page — `offset` past the end of an in-scope scenario — is `([], total)`, a
    `200` with an empty list (K-05): the scope decision already happened above, through
    `scenario_in_scope`, and applying `limit`/`offset` after it is what keeps pagination from
    becoming a second channel that could answer for scope (`out of range` must read exactly like
    "this page happens to be empty", never like "no such scenario").

    Ordered by period, then by id, so a grid read twice comes back in the same order (K-03) — the
    same total order on both branches, the page's own `ORDER BY` and the unbounded branch's.
    `selectinload` rather than a lazy load per row: the months and absences of one page of positions
    are two extra statements, not one per position.
    """
    if scenario_in_scope(session, caller, project_id, scenario_id) is None:
        return None

    if limit is None:
        statement = (
            sa.select(StaffingPosition)
            .where(StaffingPosition.scenario_id == scenario_id)
            .options(
                selectinload(StaffingPosition.allocations),
                selectinload(StaffingPosition.absences),
            )
            .order_by(StaffingPosition.start_date, StaffingPosition.id)
        )
        positions = list(session.execute(statement).scalars().all())
        return _views_of(session, positions), len(positions)

    page_rows = session.execute(
        _staffing_position_page_statement(scenario_id, limit=limit, offset=offset)
    ).all()
    if not page_rows:
        total = session.execute(_staffing_position_total_statement(scenario_id)).scalar_one()
        return [], total
    page_ids = [row.id for row in page_rows]
    total = page_rows[0].total
    statement = (
        sa.select(StaffingPosition)
        .where(StaffingPosition.id.in_(page_ids))
        .options(
            selectinload(StaffingPosition.allocations),
            selectinload(StaffingPosition.absences),
        )
    )
    by_id = {position.id: position for position in session.execute(statement).scalars().all()}
    ordered = [by_id[position_id] for position_id in page_ids]
    return _views_of(session, ordered), total


def list_absences(
    session: Session,
    caller: CallerIdentity,
    project_id: uuid.UUID,
    scenario_id: uuid.UUID,
    position_id: uuid.UUID,
) -> Sequence[StaffingPositionAbsence] | None:
    """The absences of one position — or `None`, which is the answer for every kind of absence.

    "No such project", "the project is outside your scope", "that scenario belongs to another
    project" and "that position belongs to another scenario" are four facts and one `None`
    (ADR-0005, addendum 2026-09-22, point 4 — criterion K-10). The scope is resolved through
    `scenario_in_scope`, i.e. through `project_for_caller`, and the position is then narrowed to
    that scenario in the query, so no branch here can tell the four apart even if it wanted to.

    The `WHERE` narrows by `scenario_id` through a join rather than trusting `position_id` alone: a
    position id belonging to another scenario — possibly of a project the caller cannot see — must
    match nothing, not return its rows.
    """
    if scenario_in_scope(session, caller, project_id, scenario_id) is None:
        return None
    statement = (
        sa.select(StaffingPositionAbsence)
        .join(StaffingPosition, StaffingPosition.id == StaffingPositionAbsence.position_id)
        .where(
            StaffingPositionAbsence.position_id == position_id,
            StaffingPosition.scenario_id == scenario_id,
        )
        .order_by(StaffingPositionAbsence.start_date, StaffingPositionAbsence.id)
    )
    rows = list(session.execute(statement).scalars().all())
    if not rows and not _position_exists(session, scenario_id, position_id):
        return None
    return rows


def _position_exists(
    session: Session, scenario_id: uuid.UUID, position_id: uuid.UUID
) -> bool:
    """Is there such a position *inside this scenario*?

    Reached only after `scenario_in_scope` has already answered for the scenario, so this narrows
    an in-scope scenario to one of its own rows and can never answer about a project the caller
    cannot see. It exists so that "this position has no absences" (an empty list, `200`) and "there
    is no such position" (`None`, `404`) stay two different answers — without it, a caller could not
    tell an empty plan from a mistyped id.
    """
    return session.execute(
        sa.select(
            sa.exists().where(
                StaffingPosition.id == position_id,
                StaffingPosition.scenario_id == scenario_id,
            )
        )
    ).scalar_one()


def create_position(
    session: Session,
    caller: CallerIdentity,
    project_id: uuid.UUID,
    scenario_id: uuid.UUID,
    *,
    role_id: uuid.UUID,
    seniority_id: uuid.UUID,
    location_id: uuid.UUID,
    engagement_type_id: uuid.UUID,
    headcount: int,
    start_date: date,
    end_date: date | None,
    allocations: Sequence[Mapping[str, Any]] = (),
    cost_basis: str = COST_BASIS_WORKED_TIME,
    fixed_amount: Decimal | None = None,
    fixed_amount_currency: str | None = None,
    assigned_fte: Decimal | None = None,
) -> StaffingPositionView | None:
    """Insert one position, with the month rows given for it — or refuse, or answer `None`.

    `assigned_fte` (SC-5-04; ADR-0013 addendum 2026-09-29 SC-5-04, point 2) travels through the same
    single `INSERT ... SELECT` as the other cost-basis columns; its four CHECKs are what refuse a
    missing, non-positive, stray or `fixed_amount`-accompanied value, never a check in this
    function.

    `cost_basis`/`fixed_amount`/`fixed_amount_currency` (SC-5-03; ADR-0013, addendum 2026-09-25
    SC-5-03) default to the backward-compatible worked-time basis with no stated amount — the same
    guarantee the column's own `server_default` gives a row written outside this function. They
    travel through the **same** `INSERT ... SELECT ... WHERE status <> 'approved'` as every other
    column of the row (criterion K-03: one statement, one guard, no new mechanism for the two new
    columns); the database's own `fixed_amount_required_for_its_basis` CHECK is what refuses a
    `fixed_amount` basis with no amount or no currency (criterion K-06), never a check in this
    function.

    `None` means "no such scenario *for this caller*" and is the same answer for every reason
    (`scenario_in_scope`), which is also what gives the 404-before-409 precedence ADR-0007
    requires: a scenario outside the caller's scope never reaches the `approved` guard, so a refusal
    saying "approved" cannot become a side channel confirming that the scenario exists.

    **The guard against writing to an `approved` scenario is the `INSERT ... SELECT` below**, not a
    status read followed by an insert. The row's `scenario_id` *comes from* a `SELECT` over
    `scenarios` narrowed by `status <> 'approved'`: no matching parent, no row to insert, zero rows
    affected — the refusal is counted by the database in the same statement as the write (ADR-0004,
    addendum 2026-09-19, point 2). A Python pre-check has survived delivered tests three times in
    this repository (SC-1-02 twice, SC-2-01), which is why the predicate is not in Python.

    **That `SELECT` now also locks the scenario row** (`app.data.scenario_guard`). Until SC-3-02
    the predicate excluded an approval that had *already* committed but not one committing
    concurrently — a gap this module named and dated when it was introduced, because nothing could
    approve a scenario yet. With `FOR UPDATE` the two transactions serialise on the parent row: an
    approval that commits first makes this statement's locking scan re-check the new row version and
    find no parent, and an approval that arrives second waits until this insert has committed
    (criterion K-20).

    The month rows are inserted afterwards, in the same transaction, and need no guard of their own:
    their `position_id` is the id of a position this statement just created, so a refused position
    leaves nothing for them to point at and the transaction has no position to attach them to. This
    is the whole reason the monthly grid is created *with* its position rather than by a second,
    separately guarded endpoint — a third write path would need a third run of the `approved` guard,
    and it would not have one.

    `created_at`/`updated_at` are absent from the inserted columns on purpose: they come from the
    column defaults, i.e. from the database's clock.
    """
    if scenario_in_scope(session, caller, project_id, scenario_id) is None:
        return None

    position_id = uuid.uuid4()
    # `FROM (SELECT id FROM scenarios WHERE id = :id AND status <> 'approved' FOR UPDATE)` — the
    # guard and the lock in one place, shared with every other child write (`scenario_guard`). A
    # sub-select carrying a locking clause is never flattened away by the planner, so the lock is
    # taken by this statement and released only when this transaction ends.
    open_scenario = unapproved_scenario(scenario_id).subquery("open_scenario")
    # The literals are typed explicitly: an untyped `sa.literal(uuid)` inside a `SELECT` reaches
    # PostgreSQL as an unknown-typed parameter, and the column list of an `INSERT ... SELECT` is
    # where that becomes a cast error rather than a value.
    source = sa.select(
        sa.literal(position_id, type_=_POSITION_TABLE.c.id.type).label("id"),
        open_scenario.c.id.label("scenario_id"),
        sa.literal(role_id, type_=_POSITION_TABLE.c.role_id.type).label("role_id"),
        sa.literal(seniority_id, type_=_POSITION_TABLE.c.seniority_id.type).label("seniority_id"),
        sa.literal(location_id, type_=_POSITION_TABLE.c.location_id.type).label("location_id"),
        sa.literal(
            engagement_type_id, type_=_POSITION_TABLE.c.engagement_type_id.type
        ).label("engagement_type_id"),
        sa.literal(headcount, type_=_POSITION_TABLE.c.headcount.type).label("headcount"),
        sa.literal(start_date, type_=_POSITION_TABLE.c.start_date.type).label("start_date"),
        sa.literal(end_date, type_=_POSITION_TABLE.c.end_date.type).label("end_date"),
        sa.literal(cost_basis, type_=_POSITION_TABLE.c.cost_basis.type).label("cost_basis"),
        sa.literal(fixed_amount, type_=_POSITION_TABLE.c.fixed_amount.type).label(
            "fixed_amount"
        ),
        sa.literal(
            fixed_amount_currency, type_=_POSITION_TABLE.c.fixed_amount_currency.type
        ).label("fixed_amount_currency"),
        sa.literal(assigned_fte, type_=_POSITION_TABLE.c.assigned_fte.type).label("assigned_fte"),
    ).select_from(open_scenario)
    statement = (
        sa.insert(_POSITION_TABLE)
        .from_select(
            [
                "id",
                "scenario_id",
                "role_id",
                "seniority_id",
                "location_id",
                "engagement_type_id",
                "headcount",
                "start_date",
                "end_date",
                "cost_basis",
                "fixed_amount",
                "fixed_amount_currency",
                "assigned_fte",
            ],
            source,
        )
        .returning(_POSITION_TABLE.c.id)
    )

    try:
        inserted = session.execute(statement).one_or_none()
        if inserted is None:
            # No `session.rollback()`: the statement matched no parent row, so there is nothing
            # written to undo, and a rollback would additionally discard unrelated work the caller's
            # transaction may hold. The exception below is a `StaffingWriteRejected`, not a
            # `SQLAlchemyError`, so it passes through the `except` untouched.
            raise _diagnose_insert_refusal(session, scenario_id)
        if allocations:
            session.execute(
                sa.insert(_ALLOCATION_TABLE),
                [
                    # `id`/`position_id` last: they must win over anything of the same name in
                    # `allocation`, not be overridden by it. A caller-supplied `position_id` here
                    # would attach a month row to a position outside this call's own scope/guard
                    # checks (security-auditor B-01, 2026-09-19) — closed by ordering, not just by
                    # today's schema forbidding the field on the HTTP path.
                    {**dict(allocation), "id": uuid.uuid4(), "position_id": position_id}
                    for allocation in allocations
                ],
            )
        session.commit()
    except SQLAlchemyError as error:
        session.rollback()
        # `from None`, as everywhere on a write path here: a chained exception is printed together
        # with its cause, so re-raising *with* the original would put PostgreSQL's `DETAIL: Failing
        # row contains (…)` back into the traceback one line further down (NF-11).
        raise _failure(error) from None
    return _position_by_id(session, position_id)


def _diagnose_insert_refusal(session: Session, scenario_id: uuid.UUID) -> StaffingWriteRejected:
    """Name the reason the `INSERT ... SELECT` found no parent row.

    Run only *after* the refusal, never as the guard itself — the guard is the `WHERE` of the
    `SELECT`. The scenario was inside the caller's scope a moment ago (`scenario_in_scope`), so
    "approved" is the expected answer and the other branch covers a scenario that disappeared
    between the two statements: reported as a conflict, because re-reading is what resolves it.
    """
    approved = session.execute(
        sa.select(
            sa.exists().where(
                Scenario.id == scenario_id, Scenario.status == ScenarioStatus.APPROVED
            )
        )
    ).scalar_one()
    if approved:
        return ApprovedScenarioFrozen(
            "This scenario is approved, so its staffing is part of an approved calculation and "
            "cannot be changed. Copy the scenario to open a new version and change the copy."
        )
    return ConcurrentStaffingEditConflict(
        "The scenario changed since it was read. Re-read it and apply the change again."
    )


def _position_by_id(session: Session, position_id: uuid.UUID) -> StaffingPositionView | None:
    """Re-read one position with its month rows and absences after a write, capacity included.

    A read-back rather than an object assembled in Python: `NUMERIC(10,2)` turns `Decimal("8")` into
    `Decimal("8.00")` and the timestamps come from the database's clock, so the payload returned by
    a write must be the row the database holds — otherwise the `POST` response and the next `GET`
    disagree about the same row. Sessions here run with `expire_on_commit=False` (`app.db.session`),
    which is exactly why this cannot be left to the ORM's expiry.

    The derived capacity goes through the same `position_view` as the list path, so a write
    response and
    a read response cannot disagree about a figure neither of them stores (K-08).
    """
    statement = (
        sa.select(StaffingPosition)
        .where(StaffingPosition.id == position_id)
        .options(
            selectinload(StaffingPosition.allocations),
            selectinload(StaffingPosition.absences),
        )
    )
    position = session.execute(statement).scalars().one_or_none()
    if position is None:
        return None
    session.refresh(position)
    return _views_of(session, [position])[0]


def update_allocation(
    session: Session,
    caller: CallerIdentity,
    project_id: uuid.UUID,
    scenario_id: uuid.UUID,
    position_id: uuid.UUID,
    period_month: date,
    *,
    expected_updated_at: datetime,
    changes: Mapping[str, Any],
) -> StaffingPositionView | None:
    """Edit one month of one position's allocation, or refuse — `None` when there is nothing there.

    Everything that decides whether this write may happen is in **one statement**, and that is the
    whole claim of criterion K-07:

    ```
    WITH guarded_position AS (
        UPDATE staffing_position SET updated_at = now()
         WHERE id = :position_id AND scenario_id = :scenario_id
           AND updated_at = :expected                      -- ADR-0007, token on the position
           AND scenario_id IN (SELECT id FROM scenarios
                                WHERE id = :scenario_id AND status <> 'approved'
                                  FOR UPDATE)              -- ADR-0004, and the lock (K-20)
           AND EXISTS (… the month row …)                   -- so a missing month rotates no token
        RETURNING id
    )
    UPDATE staffing_position_allocation SET … FROM guarded_position WHERE …
    ```

    Five properties of that shape, each one load-bearing:

    - **The token is compared by the database, inside the `UPDATE`.** A Python comparison against
      the value just read leaves the window between the read and the write — the interval the guard
      exists to cover (NF-05, and the mutation that survived SC-1-02's delivered tests).
    - **The token is the position's**, and the statement that checks it is also the statement that
      rotates it, so two edits of the same position serialise on that row (ADR-0007, addendum
      2026-09-19). `now()` is the database's clock, never this process's.
    - **`scenario_id` is part of the `WHERE`.** A position id belonging to another scenario — of
      another project, possibly one the caller cannot see — matches nothing, and the answer is the
      same absence as for a position that does not exist.
    - **The month row's existence is a condition of the token rotation.** Without it, a `PATCH` on a
      month that does not exist would still bump `updated_at` and invalidate every other editor's
      token for a write that changed nothing — the defect ADR-0004's addendum of 2026-09-19 (point
      1) recorded for archiving, which is not a reason to repeat it.
    - **The scenario is read `FOR UPDATE`, so this edit and an approval serialise** (SC-3-02,
      `app.data.scenario_guard`). The `NOT EXISTS (… approved)` this clause replaces excluded an
      approval that had already committed and not one committing alongside; both are excluded now,
      and the shape is the same one the position `INSERT` and both absence paths use (K-20).

    Returns the whole position, freshly read: the caller needs the *new* token, and the response of
    an edit and of a read must be the same shape (the frontend that displays a grid has no second
    schema for "the grid after a write").
    """
    forbidden = sorted(set(changes) - EDITABLE_ALLOCATION_FIELDS)
    if forbidden:
        raise AllocationFieldNotEditable(
            "These allocation fields cannot be edited through this function: "
            + ", ".join(forbidden)
            + f". Editable: {', '.join(sorted(EDITABLE_ALLOCATION_FIELDS))}."
        )
    if not changes:
        raise AllocationFieldNotEditable(
            "An allocation edit must name at least one field to change."
        )

    if scenario_in_scope(session, caller, project_id, scenario_id) is None:
        return None

    month_row_exists = sa.exists().where(
        _ALLOCATION_TABLE.c.position_id == _POSITION_TABLE.c.id,
        _ALLOCATION_TABLE.c.period_month == period_month,
    )
    guarded_position = (
        sa.update(_POSITION_TABLE)
        .where(
            _POSITION_TABLE.c.id == position_id,
            _POSITION_TABLE.c.scenario_id == scenario_id,
            _POSITION_TABLE.c.updated_at == expected_updated_at,
            _POSITION_TABLE.c.scenario_id.in_(unapproved_scenario(scenario_id)),
            month_row_exists,
        )
        # Explicit rather than left to the column's `onupdate`: this statement is the only one that
        # writes the position row on this path, and an `UPDATE` with no `SET` is not a statement.
        .values(updated_at=sa.func.now())
        .returning(_POSITION_TABLE.c.id)
        .cte("guarded_position")
    )
    statement = (
        sa.update(_ALLOCATION_TABLE)
        .where(
            _ALLOCATION_TABLE.c.position_id == guarded_position.c.id,
            _ALLOCATION_TABLE.c.period_month == period_month,
        )
        .values(**dict(changes))
        .returning(_ALLOCATION_TABLE.c.id)
    )

    try:
        applied = session.execute(statement).one_or_none()
        if applied is None:
            raise _diagnose_allocation_refusal(
                session,
                scenario_id,
                position_id,
                period_month,
                expected_updated_at=expected_updated_at,
            )
        session.commit()
    except SQLAlchemyError as error:
        session.rollback()
        raise _failure(error) from None
    return _position_by_id(session, position_id)


class PositionNotFound(RuntimeError):
    """There is no such staffing position inside that scenario (SC-5-03).

    Raised by `update_position_cost_basis`, the counterpart of `AllocationMonthNotFound` one level
    up the aggregate: this edit never leaves the position row, so "the position itself is missing"
    is a fact this module needs its own name for, distinct from "the month row is missing" (which
    presupposes the position exists) and from "the absence is missing" one table over.
    """


class AllocationMonthNotFound(RuntimeError):
    """There is no such month row under that position, inside that scenario.

    Answered as the same `404` as "no such scenario": the month, the position and the scenario are
    one aggregate as far as a caller's ability to tell them apart goes (ADR-0005, addendum
    2026-09-19, point 4). Raised rather than returned as `None` so that "outside your scope" and
    "no such month" stay two distinct facts *inside* this layer, while the API answers both with one
    body.
    """


def _diagnose_allocation_refusal(
    session: Session,
    scenario_id: uuid.UUID,
    position_id: uuid.UUID,
    period_month: date,
    *,
    expected_updated_at: datetime,
) -> Exception:
    """Name the reason the conditional edit matched no row — after the refusal, never as the guard.

    **"Does the target exist at all?" comes before "may it be written?"**: first the position, then
    the month row, and only then the state of the scenario and the token. That is the
    404-before-409 precedence of ADR-0007 (addendum 2026-09-19) applied one level below the
    scenario, and it is a correction (R-01, reviewer 2026-09-19): checking `approved` first answered
    a `PATCH` on a month with no row with `409 "this scenario is approved — copy it and change it"`,
    advice that cannot work, because the same request against the copy is a `404` for a month that
    never existed. A refusal must not name a reason it has not established, and "frozen" is a
    statement about a row — so it needs the row.

    Among the reasons that do apply to an existing row the permanent one comes first, exactly as
    `project_writes._diagnose_refusal` orders them: an `approved` scenario can never be satisfied by
    a retry with a fresh token, while a concurrency conflict is resolved by re-reading. A caller
    told only "conflict" would keep retrying a write that cannot succeed.

    Reordering leaks nothing across the scope boundary: every branch below is reached only for a
    scenario `scenario_in_scope` has already returned, and the position lookup is narrowed to that
    scenario — so "no such month" is never an answer about a row in a project the caller cannot see,
    and it reaches the caller as the same `404` body as every other absence on this path
    (`app.api.staffing.STAFFING_NOT_FOUND_DETAIL`).
    """
    position = session.execute(
        sa.select(StaffingPosition).where(
            StaffingPosition.id == position_id, StaffingPosition.scenario_id == scenario_id
        )
    ).scalars().one_or_none()
    if position is None:
        return AllocationMonthNotFound("No such staffing position in this scenario.")
    month_exists = session.execute(
        sa.select(
            sa.exists().where(
                StaffingPositionAllocation.position_id == position_id,
                StaffingPositionAllocation.period_month == period_month,
            )
        )
    ).scalar_one()
    if not month_exists:
        return AllocationMonthNotFound("No allocation row for that month on this position.")
    approved = session.execute(
        sa.select(
            sa.exists().where(
                Scenario.id == scenario_id, Scenario.status == ScenarioStatus.APPROVED
            )
        )
    ).scalar_one()
    if approved:
        return ApprovedScenarioFrozen(
            "This scenario is approved, so its staffing is part of an approved calculation and "
            "cannot be changed. Copy the scenario to open a new version and change the copy."
        )
    return ConcurrentStaffingEditConflict(
        "The staffing position changed since it was read. Re-read it and apply the edit again."
    )


# --- the personnel-cost basis (F-07, SC-5-03; ADR-0013, addendum 2026-09-25) ----------------------
#
# One more write path, and it introduces no mechanism either: the same shape `update_allocation`
# already has, except the guard and the write land on the **same** table (`staffing_position`
# itself, not a child of it), so the CTE that shape needs to bridge two tables collapses into one
# `UPDATE` — the guard, the token rotation and the write to `cost_basis`/`fixed_amount`/
# `fixed_amount_currency` are the same statement (criterion K-03, "the SAME write statement as
# the rest of staffing_position's columns").


def update_position_cost_basis(
    session: Session,
    caller: CallerIdentity,
    project_id: uuid.UUID,
    scenario_id: uuid.UUID,
    position_id: uuid.UUID,
    *,
    expected_updated_at: datetime,
    changes: Mapping[str, Any],
) -> StaffingPositionView | None:
    """Edit a position's personnel-cost basis, or refuse — `None` when there is nothing there.

    ```
    UPDATE staffing_position
       SET updated_at = now(), <changes>
     WHERE id = :position_id AND scenario_id = :scenario_id
       AND updated_at = :expected                      -- ADR-0007, the position's own token
       AND scenario_id IN (SELECT id FROM scenarios
                            WHERE id = :scenario_id AND status <> 'approved'
                              FOR UPDATE)                -- ADR-0004, and the lock (K-20)
     RETURNING id
    ```

    One statement, not two: unlike the allocation edit (a grandchild row reached through a CTE on
    the position), `cost_basis`/`fixed_amount`/`fixed_amount_currency` are columns of the position
    row itself, so the guard, the token rotation and the write are one `UPDATE` — the construction
    ADR-0004's addendum of this date names as the property this task must prove, not assume ("no
    new shape of the guard, no new concurrency token").

    The database's own `fixed_amount_required_for_its_basis`/`fixed_amount_positive`/currency CHECKs
    are what refuse an inconsistent combination (e.g. `cost_basis = 'fixed_amount'` with no amount)
    — this function performs no such check itself and passes `changes` straight into the `UPDATE`,
    exactly as `update_allocation` does for the hours fields.

    **One guard those CHECKs do not cover, added to the same `WHERE`** (gate 1 SC-5-03, Guardian
    finding): a request naming `fixed_amount`/`fixed_amount_currency` without also naming
    `cost_basis` (the one combination `StaffingPositionCostBasisEditRequest`'s own validator cannot
    refuse, having no view of the row) must not silently land on a `worked_time` row — the database
    CHECKs allow it (a `worked_time` row may carry a stray amount, by design, see
    `test_staffing_cost_basis_schema.py`), but the invariant this module and every reader of it
    relies on is that it never does. `_requires_an_existing_fixed_amount_basis` adds `cost_basis =
    'fixed_amount'` to the `WHERE` exactly in that one case, so the guard, the token rotation and
    the write stay the *same* statement (`CostBasisMismatch`, diagnosed the same way as
    `ApprovedScenarioFrozen`/`ConcurrentStaffingEditConflict` are).
    """
    forbidden = sorted(set(changes) - EDITABLE_POSITION_FIELDS)
    if forbidden:
        raise AllocationFieldNotEditable(
            "These position fields cannot be edited through this function: "
            + ", ".join(forbidden)
            + f". Editable: {', '.join(sorted(EDITABLE_POSITION_FIELDS))}."
        )
    if not changes:
        raise AllocationFieldNotEditable(
            "A position edit must name at least one field to change."
        )

    if scenario_in_scope(session, caller, project_id, scenario_id) is None:
        return None

    conditions = [
        _POSITION_TABLE.c.id == position_id,
        _POSITION_TABLE.c.scenario_id == scenario_id,
        _POSITION_TABLE.c.updated_at == expected_updated_at,
        _POSITION_TABLE.c.scenario_id.in_(unapproved_scenario(scenario_id)),
    ]
    if _requires_an_existing_fixed_amount_basis(changes):
        conditions.append(_POSITION_TABLE.c.cost_basis == COST_BASIS_FIXED_AMOUNT)
    if _requires_an_existing_assigned_fte_basis(changes):
        conditions.append(_POSITION_TABLE.c.cost_basis == COST_BASIS_ASSIGNED_FTE)

    statement = (
        sa.update(_POSITION_TABLE)
        .where(*conditions)
        .values(updated_at=sa.func.now(), **_values_for_a_basis_switch(changes))
        .returning(_POSITION_TABLE.c.id)
    )

    try:
        applied = session.execute(statement).one_or_none()
        if applied is None:
            raise _diagnose_position_refusal(
                session,
                scenario_id,
                position_id,
                expected_updated_at=expected_updated_at,
                changes=changes,
            )
        session.commit()
    except SQLAlchemyError as error:
        session.rollback()
        raise _failure(error) from None
    return _position_by_id(session, position_id)


def update_position_details(
    session: Session,
    caller: CallerIdentity,
    project_id: uuid.UUID,
    scenario_id: uuid.UUID,
    position_id: uuid.UUID,
    *,
    expected_updated_at: datetime,
    changes: Mapping[str, Any],
) -> StaffingPositionView | None:
    """Edit the position's dimensions, headcount and period, or refuse.

    The request names the full editable details tuple. This single `UPDATE` keeps the existing
    position marker and approved-scenario guard in the statement that writes the fields; the
    returned view is then read from the database for the write response.
    """
    forbidden = sorted(set(changes) - EDITABLE_POSITION_DETAILS_FIELDS)
    if forbidden:
        raise AllocationFieldNotEditable(
            "These position details cannot be edited through this function: "
            + ", ".join(forbidden)
            + f". Editable: {', '.join(sorted(EDITABLE_POSITION_DETAILS_FIELDS))}."
        )
    if set(changes) != EDITABLE_POSITION_DETAILS_FIELDS:
        missing = sorted(EDITABLE_POSITION_DETAILS_FIELDS - set(changes))
        raise AllocationFieldNotEditable(
            "A position details edit must name every editable field. Missing: "
            + ", ".join(missing)
            + "."
        )

    if scenario_in_scope(session, caller, project_id, scenario_id) is None:
        return None

    statement = (
        sa.update(_POSITION_TABLE)
        .where(
            _POSITION_TABLE.c.id == position_id,
            _POSITION_TABLE.c.scenario_id == scenario_id,
            _POSITION_TABLE.c.updated_at == expected_updated_at,
            _POSITION_TABLE.c.scenario_id.in_(unapproved_scenario(scenario_id)),
        )
        .values(updated_at=sa.func.now(), **dict(changes))
        .returning(_POSITION_TABLE.c.id)
    )

    try:
        applied = session.execute(statement).one_or_none()
        if applied is None:
            raise _diagnose_position_refusal(
                session,
                scenario_id,
                position_id,
                expected_updated_at=expected_updated_at,
                changes=changes,
            )
        session.commit()
    except SQLAlchemyError as error:
        session.rollback()
        raise _failure(error) from None
    return _position_by_id(session, position_id)


def _diagnose_position_refusal(
    session: Session,
    scenario_id: uuid.UUID,
    position_id: uuid.UUID,
    *,
    expected_updated_at: datetime,
    changes: Mapping[str, Any],
) -> Exception:
    """Name the reason the position edit matched no row — after the refusal, never as the guard.

    The same order as `_diagnose_allocation_refusal`/`_diagnose_absence_refusal`: the position's own
    existence first, then the permanent reason (`approved`), then the token — and only *after* the
    token is confirmed still fresh does `CostBasisMismatch` get to name anything (Reviewer R-02,
    correcting this function's first version).

    **`CostBasisMismatch` is not a second permanent reason beside `approved` — it must not be
    checked before the token.** Unlike `approved` (one-directional: a scenario never returns from
    `approved` to `draft`), `cost_basis` toggles in both directions — `test_the_cost_basis_edit_
    endpoint_changes_the_basis_and_is_reflected_in_the_cost` switches a position from `worked_time`
    to `fixed_amount` and back on the same row. So a *stale read* of the current `cost_basis` here
    (this `SELECT` runs after the failed `UPDATE`, in the same transaction, and therefore observes
    whatever a concurrent committer has already changed under `READ COMMITTED`) can name a mismatch
    that is an artefact of a race, not of the caller's request: a competing edit that switched the
    position to `worked_time` *after* this caller's token was issued makes today's `cost_basis` look
    like the reason, when the real reason is that the caller's whole view of the row is stale — and
    `CostBasisMismatch`'s own advice ("name `cost_basis='fixed_amount'` and retry") would then walk
    the caller straight into silently overwriting the very edit that raced them, with no re-read and
    no warning to either side. The token is compared explicitly here (not "second-guessing the
    database", as `_diagnose_absence_refusal`'s own note might suggest a comparison never belongs in
    Python) because this function — unlike its two siblings — has *two* independent dynamic
    conditions in its guarded `WHERE`, not one: elimination genuinely cannot tell them apart once
    `approved` is ruled out, so at least one of the two must be checked directly. The token is the
    one chosen, because it is the one whose staleness makes every other fact about the row
    unreliable to report.
    """
    position = session.execute(
        sa.select(StaffingPosition).where(
            StaffingPosition.id == position_id, StaffingPosition.scenario_id == scenario_id
        )
    ).scalars().one_or_none()
    if position is None:
        return PositionNotFound("No such staffing position in this scenario.")
    approved = session.execute(
        sa.select(
            sa.exists().where(
                Scenario.id == scenario_id, Scenario.status == ScenarioStatus.APPROVED
            )
        )
    ).scalar_one()
    if approved:
        return ApprovedScenarioFrozen(
            "This scenario is approved, so its staffing is part of an approved calculation and "
            "cannot be changed. Copy the scenario to open a new version and change the copy."
        )
    if position.updated_at != expected_updated_at:
        return ConcurrentStaffingEditConflict(
            "The staffing position changed since it was read. Re-read it and apply the edit again."
        )
    if (
        _requires_an_existing_fixed_amount_basis(changes)
        and position.cost_basis != COST_BASIS_FIXED_AMOUNT
    ):
        return CostBasisMismatch(
            "fixed_amount/fixed_amount_currency can only be edited without also naming "
            "cost_basis when the position's current basis is already 'fixed_amount'. Name "
            "cost_basis='fixed_amount' in the same request to switch it."
        )
    if (
        _requires_an_existing_assigned_fte_basis(changes)
        and position.cost_basis != COST_BASIS_ASSIGNED_FTE
    ):
        return CostBasisMismatch(
            "assigned_fte can only be edited without also naming cost_basis when the position's "
            "current basis is already 'assigned_fte'. Name cost_basis='assigned_fte' in the same "
            "request to switch it."
        )
    # Defensive only — unreachable in practice: if the token still matches, the scenario is not
    # approved and the basis condition (if any) already holds, the guarded `UPDATE` would have
    # matched the row. Named as a concurrency conflict rather than raised as an assertion, so an
    # unforeseen fourth condition added later fails the same way every other unmodelled case here
    # does, not with a stack trace.
    return ConcurrentStaffingEditConflict(
        "The staffing position changed since it was read. Re-read it and apply the edit again."
    )


def create_allocation(
    session: Session,
    caller: CallerIdentity,
    project_id: uuid.UUID,
    scenario_id: uuid.UUID,
    position_id: uuid.UUID,
    *,
    expected_updated_at: datetime,
    period_month: date,
    availability_hours: Decimal,
    planned_allocation_hours: Decimal,
    billable_hours: Decimal,
) -> StaffingPositionView | None:
    """Add one month to an existing position under its shared token and scenario guard.

    The position-token rotation and allocation insert are one data-modifying CTE statement. A
    duplicate month or a database constraint refusal rolls back the token rotation with the insert.
    """
    if scenario_in_scope(session, caller, project_id, scenario_id) is None:
        return None

    guarded_position = (
        sa.update(_POSITION_TABLE)
        .where(
            _POSITION_TABLE.c.id == position_id,
            _POSITION_TABLE.c.scenario_id == scenario_id,
            _POSITION_TABLE.c.updated_at == expected_updated_at,
            _POSITION_TABLE.c.scenario_id.in_(unapproved_scenario(scenario_id)),
        )
        .values(updated_at=sa.func.now())
        .returning(_POSITION_TABLE.c.id)
        .cte("guarded_position")
    )
    source = sa.select(
        sa.literal(uuid.uuid4(), type_=_ALLOCATION_TABLE.c.id.type).label("id"),
        guarded_position.c.id.label("position_id"),
        sa.literal(period_month, type_=_ALLOCATION_TABLE.c.period_month.type).label(
            "period_month"
        ),
        sa.literal(
            availability_hours, type_=_ALLOCATION_TABLE.c.availability_hours.type
        ).label("availability_hours"),
        sa.literal(
            planned_allocation_hours, type_=_ALLOCATION_TABLE.c.planned_allocation_hours.type
        ).label("planned_allocation_hours"),
        sa.literal(billable_hours, type_=_ALLOCATION_TABLE.c.billable_hours.type).label(
            "billable_hours"
        ),
    ).select_from(guarded_position)
    statement = (
        sa.insert(_ALLOCATION_TABLE)
        .from_select(
            [
                "id",
                "position_id",
                "period_month",
                "availability_hours",
                "planned_allocation_hours",
                "billable_hours",
            ],
            source,
        )
        .returning(_ALLOCATION_TABLE.c.id)
    )

    try:
        inserted = session.execute(statement).one_or_none()
        if inserted is None:
            raise _diagnose_position_refusal(
                session,
                scenario_id,
                position_id,
                expected_updated_at=expected_updated_at,
                changes={},
            )
        session.commit()
    except SQLAlchemyError as error:
        session.rollback()
        raise _failure(error) from None
    return _position_by_id(session, position_id)


# --- the named person of a position (F-03, SC-2-06; ADR-0019; ADR-0004/0005 addendumy 2026-09-27) -
#
# One more write path, and the only one that writes `person_id` (ADR-0005, addendum 2026-09-27,
# point 3 of gate 1: "one write path for the assignment"). The same single-`UPDATE` shape as
# `update_position_cost_basis`: `person_id` is a column of the position row itself, so the guard,
# the token rotation and the write are one statement — no new guard shape, no new token.


class AssignedPersonNotFound(StaffingWriteRejected):
    """The `person_id` named by an assignment is not in the register (SC-2-06, criterion K-05b).

    Refused *by state*, like `ApprovedScenarioFrozen` — decided by the same guarded `UPDATE`, whose
    `WHERE` requires the person to exist, and backed by `fk_staffing_position_person_id` for any
    writer that does not come through here. The message names the condition and never a value: not
    the id the caller sent back to it, and certainly never a name (ADR-0019, point 6).

    Reached only by a caller holding `STAFFING_WRITE` ∧ `PEOPLE_READ` (the endpoint's dependency),
    i.e. one who may read the whole register anyway — so this is not an existence oracle
    (ADR-0005, addendum 2026-09-27, point 5b)."""


def assign_person(
    session: Session,
    caller: CallerIdentity,
    project_id: uuid.UUID,
    scenario_id: uuid.UUID,
    position_id: uuid.UUID,
    *,
    expected_person_assignment_updated_at: datetime,
    person_id: uuid.UUID | None,
) -> StaffingPositionView | None:
    """Assign a person to a position (`person_id`), or remove the assignment (`None`) — or refuse,
    or answer `None` when there is no such position for this caller.

    ```
    UPDATE staffing_position
       SET person_id = :person_id,
           person_assignment_updated_at = now(),
           updated_at = updated_at                         -- unchanged, and said so (see below)
     WHERE id = :position_id AND scenario_id = :scenario_id
       AND person_assignment_updated_at = :expected        -- ADR-0007 addendum 2026-09-28
       AND scenario_id IN (SELECT id FROM scenarios
                            WHERE id = :scenario_id AND status <> 'approved'
                              FOR UPDATE)                    -- ADR-0004, and the lock (K-20)
       AND EXISTS (SELECT 1 FROM person WHERE id = :person_id)   -- only when assigning
     RETURNING id
    ```

    One statement: the `approved` guard is **in** the write (ADR-0004, addendum 2026-09-27 SC-2-06,
    point 2 — criterion K-06), never a status read followed by an `UPDATE`. The database's own
    `ck_staffing_position_person_requires_single_headcount` refuses a person on a position whose
    `headcount` is not 1 (Q-7 = a, criterion K-05c) and `fk_staffing_position_person_id` a person
    that does not exist — this function performs neither check in Python.

    **The order of refusals** (gate 1, decision 3): scope (`None` → `404`) → a position missing from
    this scenario (`404`) → a person missing from the register (`AssignedPersonNotFound`) →
    `approved` (`ApprovedScenarioFrozen`) → a stale assignment marker. The person's existence is
    part of the `WHERE` rather than left to the foreign key precisely so that this order holds when
    the scenario is also approved: a foreign key is checked only on a row that is actually written,
    so it would let "approved" answer first. The diagnosis runs *after* the refusal and is never the
    guard.

    `None` for `person_id` removes the assignment through the same statement and the same guard: an
    approved scenario refuses the removal exactly as it refuses the assignment (A4-31-2). Every
    *other* write of this row leaves `person_id` untouched — none of them names the column
    (ADR-0005, addendum 2026-09-27, point 6).

    **Does not move the position's `updated_at`** (D-4 = B; ADR-0007 addendum 2026-09-28 — this
    replaces gate 1's decision 8). The assignment has its own marker,
    `person_assignment_updated_at`, compared and rotated here and nowhere else; `updated_at` is
    visible without `PEOPLE_READ`, so moving it would tell such a caller that the position was
    assigned. `updated_at = updated_at` is spelled out in the `SET` on purpose: the model's
    `onupdate=func.now()` fires on every `UPDATE` of this table that does not name the column, and
    would move it silently (criterion A7-31-1 kills exactly that). Consequence: a grid edit holding
    a token read before somebody's assignment still succeeds, and both writes land (A7-31-3) — they
    write disjoint columns.
    """
    if scenario_in_scope(session, caller, project_id, scenario_id) is None:
        return None

    conditions: list[sa.ColumnElement[bool]] = [
        _POSITION_TABLE.c.id == position_id,
        _POSITION_TABLE.c.scenario_id == scenario_id,
        _POSITION_TABLE.c.person_assignment_updated_at == expected_person_assignment_updated_at,
        _POSITION_TABLE.c.scenario_id.in_(unapproved_scenario(scenario_id)),
    ]
    if person_id is not None:
        conditions.append(_person_exists(person_id))

    statement = (
        sa.update(_POSITION_TABLE)
        .where(*conditions)
        .values(
            person_id=person_id,
            person_assignment_updated_at=sa.func.now(),
            # Explicit, and load-bearing: without it the column's `onupdate` moves the position's
            # own token on every assignment (D-4 = B would be silently unmet).
            updated_at=_POSITION_TABLE.c.updated_at,
        )
        .returning(_POSITION_TABLE.c.id)
    )

    try:
        applied = session.execute(statement).one_or_none()
        if applied is None:
            raise _diagnose_assignment_refusal(
                session, scenario_id, position_id, person_id=person_id
            )
        session.commit()
    except SQLAlchemyError as error:
        session.rollback()
        raise _failure(error) from None
    return _position_by_id(session, position_id)


def _person_exists(person_id: uuid.UUID) -> sa.ColumnElement[bool]:
    """`EXISTS (SELECT 1 FROM person WHERE id = :person_id)` — spelled once for the guarded `WHERE`
    and for the diagnosis, so the two cannot come to disagree about what "exists" means."""
    return sa.exists().where(Person.id == person_id)


def _diagnose_assignment_refusal(
    session: Session,
    scenario_id: uuid.UUID,
    position_id: uuid.UUID,
    *,
    person_id: uuid.UUID | None,
) -> Exception:
    """Name the reason the assignment matched no row — after the refusal, never as the guard.

    Gate 1's order (decision 3): the position first (`PositionNotFound` → `404`), then the person
    (`AssignedPersonNotFound`), then the permanent reason (`approved`), and only then the
    assignment's own marker (`person_assignment_updated_at`, ADR-0007 addendum 2026-09-28) — which,
    once every other condition of the `WHERE` is ruled out, is the one left, so it is named by
    elimination, as `_diagnose_absence_refusal` names it. Its message is distinct from the grid's
    stale-`updated_at` message (point 4 of that addendum: a different marker to re-read) and
    carries no value. Every branch is reached only for a
    scenario `scenario_in_scope` has already returned, and the position lookup is narrowed to that
    scenario, so nothing here answers about a row the caller cannot see. Persons are never deleted
    by this system (ADR-0019, point 7), so "the person was missing" cannot become untrue between the
    statement and this read.
    """
    position_exists = session.execute(
        sa.select(
            sa.exists().where(
                StaffingPosition.id == position_id, StaffingPosition.scenario_id == scenario_id
            )
        )
    ).scalar_one()
    if not position_exists:
        return PositionNotFound("No such staffing position in this scenario.")
    if person_id is not None and not session.execute(
        sa.select(_person_exists(person_id))
    ).scalar_one():
        return AssignedPersonNotFound(
            "No such person in the register. Assign a person that exists, or remove the "
            "assignment."
        )
    approved = session.execute(
        sa.select(
            sa.exists().where(
                Scenario.id == scenario_id, Scenario.status == ScenarioStatus.APPROVED
            )
        )
    ).scalar_one()
    if approved:
        return ApprovedScenarioFrozen(
            "This scenario is approved, so its staffing is part of an approved calculation and "
            "cannot be changed. Copy the scenario to open a new version and change the copy."
        )
    return ConcurrentStaffingEditConflict(
        "The person assignment of this staffing position changed since it was read. Re-read the "
        "assignment and apply the change again."
    )


# --- absences (F-05, SC-3-02) -------------------------------------------------------------------
#
# Two write paths, and neither introduces a mechanism. Both have the shape `update_allocation`
# already has: **one statement** in which the position's ADR-0007 token, the scenario's status, the
# lock that serialises against an approval and (for the delete) the existence of the target row are
# all part of the same `WHERE`. The absence row itself has no token and no status of its own — it
# has no `updated_at` column at all (ADR-0007, addendum 2026-09-22, point 1), so there is nothing
# here that could compare one.


class AbsenceNotFound(RuntimeError):
    """There is no such absence under that position, inside that scenario.

    Answered as the same `404` as "no such scenario": the absence, the position and the scenario are
    one aggregate as far as a caller's ability to tell them apart goes (ADR-0005, addendum
    2026-09-22, point 4). Raised rather than returned as `None` so that "outside your scope" and
    "no such absence" stay two distinct facts *inside* this layer while the API answers both with
    one body — the division `AllocationMonthNotFound` already makes one table over.
    """


MAX_ABSENCES_PER_POSITION = 60
"""The most absence rows one position may ever hold (SC-3-05, gate-1 decision Q-B).

**Mirrors `app.api.schemas.staffing.MAX_ALLOCATION_MONTHS`** — the same headroom reasoning (five
years, one row per month at the most granular a booked absence gets in practice) applied to a table
that grows one row at a time through `POST` instead of arriving in a single request: `allocations`
is bounded because a create request names all its months at once and Pydantic can refuse a request
too large to hold; `absences` has no such natural bound, because nothing stops a caller from calling
this endpoint sixty-one times. Left unbounded, a position's absences were the one axis of growth
`list_positions`'s own pagination (this same task) does not address — pagination limits how many
*positions* one response carries, not how large one position's own nested lists become (architect's
impact map, Q-B).

**Not a CHECK constraint.** Counting the rows of a *different* table from inside a `CHECK` on
`staffing_position` is not expressible without a trigger, and this repository's existing pattern for
a bound on "how many child rows may a parent accumulate" is the request-schema bound
(`MAX_ALLOCATION_MONTHS`) precisely because the accumulation happens in one request there — here it
does not, so the bound instead lives in the one write statement that would cross it
(`create_absence`), evaluated by a correlated `count(*)` in the same guarded `UPDATE ... WHERE` that
rotates the token, never a separate `SELECT` before the `INSERT` (a check-then-act window two
concurrent callers at the limit could both pass — the shape ADR-0008 and this module's own
`INSERT ... SELECT` guards exist to avoid)."""


class AbsenceLimitReached(StaffingWriteRejected):
    """This position already holds `MAX_ABSENCES_PER_POSITION` absence rows (SC-3-05, K-07).

    Refused *by state*, exactly like `ApprovedScenarioFrozen`/`ConcurrentStaffingEditConflict` — the
    same guarded `UPDATE` decides it, in the same `WHERE`, not a second check bolted on afterwards
    (criterion K-07's mutation: removing the correlated `count(*)` condition from that `WHERE` must
    make this exception unreachable and let the insert through).

    **Reported only when the caller's own token is still fresh** (the same caution
    `CostBasisMismatch` documents, applied here): the count, like `cost_basis`, moves in both
    directions — an absence added, then removed, then added again — so a count re-read *after* a
    concurrent write has already changed it is not a fact about the caller's own request. A stale
    token is always `ConcurrentStaffingEditConflict`, never this.

    Not permanent the way `ApprovedScenarioFrozen` is: deleting an absence of this position frees a
    slot, and the identical request then succeeds — the message says so, so the caller has a way
    forward rather than only a wall.
    """


def _guarded_position(
    *,
    scenario_id: uuid.UUID,
    position_id: uuid.UUID,
    expected_updated_at: datetime,
    extra: Sequence[sa.ColumnElement[bool]] = (),
) -> sa.CTE:
    """The common head of every absence write: rotate the token, or match nothing.

    ```
    UPDATE staffing_position SET updated_at = now()
     WHERE id = :position_id
       AND scenario_id = :scenario_id
       AND updated_at = :expected                           -- ADR-0007, the aggregate's token
       AND scenario_id IN (SELECT id FROM scenarios
                            WHERE id = :scenario_id AND status <> 'approved' FOR UPDATE)
       AND <extra>                                          -- e.g. "that absence row exists"
    RETURNING id
    ```

    Spelled once for the insert and the delete rather than twice, because two copies are how one of
    them ends up missing a clause — and the clause that would go missing is invisible until the day
    it matters. The CTE keeps the name `guarded_position`, the name `update_allocation` uses too:
    three write paths, one recognisable statement shape.

    `extra` is where the delete hangs "and that absence exists", so a delete of an id that is not
    there rotates nobody's token — the same reason the allocation edit requires its month row to
    exist (and the same defect ADR-0004's addendum of 2026-09-19 recorded for archiving).
    """
    return (
        sa.update(_POSITION_TABLE)
        .where(
            _POSITION_TABLE.c.id == position_id,
            _POSITION_TABLE.c.scenario_id == scenario_id,
            _POSITION_TABLE.c.updated_at == expected_updated_at,
            _POSITION_TABLE.c.scenario_id.in_(unapproved_scenario(scenario_id)),
            *extra,
        )
        # Explicit rather than left to the column's `onupdate`, for the reason `update_allocation`
        # gives: this is the only statement that writes the position row on this path, and an
        # `UPDATE` with no `SET` is not a statement. `now()` is the database's clock.
        .values(updated_at=sa.func.now())
        .returning(_POSITION_TABLE.c.id)
        .cte("guarded_position")
    )


def create_absence(
    session: Session,
    caller: CallerIdentity,
    project_id: uuid.UUID,
    scenario_id: uuid.UUID,
    position_id: uuid.UUID,
    *,
    expected_updated_at: datetime,
    absence_type_id: uuid.UUID,
    start_date: date,
    end_date: date,
) -> StaffingPositionView | None:
    """Add one absence to one position, or refuse — `None` when there is no such position for this
    caller.

    The `INSERT` takes its `position_id` from the guarded `UPDATE` above, so it inherits every one
    of that statement's conditions structurally rather than by repeating them: no rotated token, no
    row to insert. That is the same construction the month rows of `create_position` use, one level
    down.

    **What a caller cannot express here.** There is no `person`, no `note` and no `justification`
    parameter, and there is no column for one either (ADR-0005, addendum 2026-09-22, point 6). The
    absence is anonymous because the *position* is anonymous, and the kind of absence comes only
    from the dictionary — `absence_type_id` is a foreign key, so a type nobody created is refused by
    the database with SQLSTATE `23503` and never becomes free text.

    Overlapping absences are accepted on purpose: two people of a `headcount = 3` position may be
    away over the same days, and the capacity formula counts them both (criterion K-06). There is no
    `EXCLUDE` constraint on this table and its absence is the decision.

    **Capped at `MAX_ABSENCES_PER_POSITION` (SC-3-05, K-07).** The bound is a fourth condition of
    the same guarded `UPDATE` — a correlated `count(*)` against `staffing_position_absence` for
    this position, compared inside the statement's own `WHERE` — never a `SELECT` run first in
    Python: two concurrent callers at the limit would both read "59, not yet at 60" and both
    insert, exactly the check-then-act window `create_position`'s own `INSERT ... SELECT` guard
    exists to avoid one table over. The row lock the guarded `UPDATE` already takes on
    `staffing_position` (to rotate the token) is what serialises two concurrent inserts on the
    *same* position: the second one blocks until the first commits, and PostgreSQL re-evaluates the
    whole `WHERE` — the correlated count included — against the now-current data before deciding
    whether it still matches.
    """
    if scenario_in_scope(session, caller, project_id, scenario_id) is None:
        return None

    absences_below_cap = (
        sa.select(sa.func.count())
        .select_from(_ABSENCE_TABLE)
        .where(_ABSENCE_TABLE.c.position_id == _POSITION_TABLE.c.id)
        .scalar_subquery()
    ) < MAX_ABSENCES_PER_POSITION
    guarded = _guarded_position(
        scenario_id=scenario_id,
        position_id=position_id,
        expected_updated_at=expected_updated_at,
        extra=(absences_below_cap,),
    )
    absence_id = uuid.uuid4()
    # Typed literals, for the reason `create_position` gives: an untyped `sa.literal(uuid)` inside
    # the `SELECT` of an `INSERT ... SELECT` reaches PostgreSQL as an unknown-typed parameter.
    source = sa.select(
        sa.literal(absence_id, type_=_ABSENCE_TABLE.c.id.type).label("id"),
        guarded.c.id.label("position_id"),
        sa.literal(
            absence_type_id, type_=_ABSENCE_TABLE.c.absence_type_id.type
        ).label("absence_type_id"),
        sa.literal(start_date, type_=_ABSENCE_TABLE.c.start_date.type).label("start_date"),
        sa.literal(end_date, type_=_ABSENCE_TABLE.c.end_date.type).label("end_date"),
    )
    statement = (
        sa.insert(_ABSENCE_TABLE)
        .from_select(["id", "position_id", "absence_type_id", "start_date", "end_date"], source)
        .returning(_ABSENCE_TABLE.c.id)
    )

    try:
        inserted = session.execute(statement).one_or_none()
        if inserted is None:
            # No `session.rollback()`: the statement matched no position, so there is nothing
            # written to undo, and a rollback would discard unrelated work the caller's transaction
            # may hold (the argument `create_position` and `update_project` both make). The
            # exception raised here is not a `SQLAlchemyError`, so it passes the `except` untouched.
            raise _diagnose_absence_refusal(
                session,
                scenario_id,
                position_id,
                expected_updated_at=expected_updated_at,
            )
        session.commit()
    except SQLAlchemyError as error:
        session.rollback()
        raise _failure(error) from None
    return _position_by_id(session, position_id)


def delete_absence(
    session: Session,
    caller: CallerIdentity,
    project_id: uuid.UUID,
    scenario_id: uuid.UUID,
    position_id: uuid.UUID,
    absence_id: uuid.UUID,
    *,
    expected_updated_at: datetime,
) -> StaffingPositionView | None:
    """Remove one absence from one position, or refuse — `None` when there is no such position here.

    **The only delete path in this repository, and it is guarded exactly like a write** — because it
    is one. ADR-0004 does not distinguish "changing" an approved calculation from "removing" a row
    of it, so the `approved` predicate, the ADR-0007 token and the lock against a concurrent
    approval are all in the `WHERE` of the statement that deletes (criteria K-13, K-12, K-20).

    The existence of the absence row is part of the guarded `UPDATE`'s own `WHERE`, so a delete of
    an id that is not there matches nothing and rotates nobody's token. It also gives the
    404-before-409 precedence without a rule anyone has to remember: `_diagnose_absence_refusal`
    asks "is the row there?" before "may it be written?", so a delete of a non-existent absence
    under an `approved` scenario is a `404` and not a `409` advising a copy that would answer `404`
    as well (the R-01 correction, one table over).
    """
    if scenario_in_scope(session, caller, project_id, scenario_id) is None:
        return None

    absence_row_exists = sa.exists().where(
        _ABSENCE_TABLE.c.id == absence_id,
        _ABSENCE_TABLE.c.position_id == _POSITION_TABLE.c.id,
    )
    guarded = _guarded_position(
        scenario_id=scenario_id,
        position_id=position_id,
        expected_updated_at=expected_updated_at,
        extra=(absence_row_exists,),
    )
    statement = (
        sa.delete(_ABSENCE_TABLE)
        .where(
            _ABSENCE_TABLE.c.id == absence_id,
            _ABSENCE_TABLE.c.position_id == guarded.c.id,
        )
        .returning(_ABSENCE_TABLE.c.id)
    )

    try:
        deleted = session.execute(statement).one_or_none()
        if deleted is None:
            raise _diagnose_absence_refusal(
                session,
                scenario_id,
                position_id,
                absence_id=absence_id,
                expected_updated_at=expected_updated_at,
            )
        session.commit()
    except SQLAlchemyError as error:
        session.rollback()
        raise _failure(error) from None
    return _position_by_id(session, position_id)


def _diagnose_absence_refusal(
    session: Session,
    scenario_id: uuid.UUID,
    position_id: uuid.UUID,
    *,
    absence_id: uuid.UUID | None = None,
    expected_updated_at: datetime,
) -> Exception:
    """Name the reason an absence write matched no row — after the refusal, never as the guard.

    The same order as `_diagnose_allocation_refusal`, and for the same reason (R-01): **"does the
    target exist at all?" comes before "may it be written?"**. The position first, then — on a
    delete — the absence row, and only then the state of the scenario and the token. A refusal must
    not name a reason it has not established, and "this calculation is frozen" is a statement about
    a row, so it needs the row.

    Among the reasons that *do* apply to an existing row, the permanent one comes first: an
    `approved` scenario can never be satisfied by a retry with a fresh token, while a concurrency
    conflict is resolved by re-reading. A caller told only "conflict" would keep retrying a write
    that cannot succeed.

    Nothing here leaks across the scope boundary: every branch is reached only for a scenario
    `scenario_in_scope` has already returned, and both lookups are narrowed to that scenario, so
    "no such absence" is never an answer about a row in a project the caller cannot see. It reaches
    the caller as the same `404` body as every other absence on this path.

    `expected_updated_at` is accepted and, on every branch but one, deliberately not compared here:
    the comparison happened inside the `UPDATE`, and repeating it in Python would be a second
    answer to a question the database has already answered — the shape this module exists to
    avoid. It is in the signature so that the diagnosis cannot be called from a path that has no
    token at all.

    **SC-3-05 adds one exception, on the insert path only** (`absence_id is None`): once `approved`
    is ruled out, the guarded `UPDATE`'s `WHERE` still has *two* independent dynamic conditions left
    — the token and the cap (`MAX_ABSENCES_PER_POSITION`) — and elimination alone cannot tell them
    apart, exactly the reason `_diagnose_position_refusal` compares `cost_basis` explicitly rather
    than falling through to it by default. The token is checked first and the cap only when it is
    still fresh, for the identical reason `CostBasisMismatch` is: the absence count moves in both
    directions (added, then removed), so a count re-read after a concurrent write already changed
    it would name a limit that is an artefact of the caller's whole view of the row being stale,
    not of the caller's own request. A stale token is always `ConcurrentStaffingEditConflict`,
    checked or not — the delete path has no cap to protect against, so it keeps its original,
    purely eliminative shape.
    """
    position = session.execute(
        sa.select(StaffingPosition).where(
            StaffingPosition.id == position_id, StaffingPosition.scenario_id == scenario_id
        )
    ).scalars().one_or_none()
    if position is None:
        return AbsenceNotFound("No such staffing position in this scenario.")
    if absence_id is not None:
        absence_exists = session.execute(
            sa.select(
                sa.exists().where(
                    StaffingPositionAbsence.id == absence_id,
                    StaffingPositionAbsence.position_id == position_id,
                )
            )
        ).scalar_one()
        if not absence_exists:
            return AbsenceNotFound("No such absence on this staffing position.")
    approved = session.execute(
        sa.select(
            sa.exists().where(
                Scenario.id == scenario_id, Scenario.status == ScenarioStatus.APPROVED
            )
        )
    ).scalar_one()
    if approved:
        return ApprovedScenarioFrozen(
            "This scenario is approved, so its staffing is part of an approved calculation and "
            "cannot be changed. Copy the scenario to open a new version and change the copy."
        )
    if absence_id is None and position.updated_at == expected_updated_at:
        absence_count = session.execute(
            sa.select(sa.func.count())
            .select_from(StaffingPositionAbsence)
            .where(StaffingPositionAbsence.position_id == position_id)
        ).scalar_one()
        if absence_count >= MAX_ABSENCES_PER_POSITION:
            return AbsenceLimitReached(
                f"This position already holds the maximum of {MAX_ABSENCES_PER_POSITION} planned "
                "absences. Remove one before adding another."
            )
    return ConcurrentStaffingEditConflict(
        "The staffing position changed since it was read. Re-read it and apply the change again."
    )


# --- the copying cascade (ADR-0004, addendum 2026-09-19) ----------------------------------------

POSITION_COLUMNS_NOT_COPIED: frozenset[str] = frozenset(
    {"id", "scenario_id", "created_at", "updated_at", "person_assignment_updated_at"}
)
"""Position attributes a copy does **not** inherit, and why each one is here:

- `id` — a copy is a new row, not a second name for the source one (AC-02).
- `scenario_id` — set from the scenario being copied into; that is what makes the copier usable for
  all three entry points of the one copy mechanism (project copy, scenario duplication, new
  version).
- `created_at` / `updated_at` — the copy is created now, and its ADR-0007 token is its own.
  Inheriting the source's token would hand a caller a token issued for a different row.
- `person_assignment_updated_at` (SC-2-06; ADR-0007 addendum 2026-09-28, point 5) — the same reason,
  for the assignment's own marker: the copy gets the database's `now()`. `person_id` itself *is*
  copied (the same person, never a copy of one).

Everything else is copied by reflection (`app.data.column_copy.values_to_copy`), so a column added
later is copied by default rather than silently dropped; the accompanying drift-guard test asserts
every mapped attribute is either copied or named here."""

ALLOCATION_COLUMNS_NOT_COPIED: frozenset[str] = frozenset({"id", "position_id", "created_at"})
"""The same for a month row. `position_id` is the one value that cannot be reflected: it has to be
the id of the *copied* position, which is why this table needs no separate registry entry — see
`copy_staffing_positions`."""

ABSENCE_COLUMNS_NOT_COPIED: frozenset[str] = frozenset({"id", "position_id", "created_at"})
"""The same for an absence row (SC-3-02), and the same three reasons.

`absence_type_id` is **not** here, i.e. it *is* copied: the type is an organisational dictionary
entry shared by both scenarios, and re-pointing the copy at a different type would change what the
copy says. `start_date`/`end_date` are copied for the same reason — a copy of a plan is that plan.

The accompanying drift guard asserts that every mapped attribute of the absence row is either copied
by reflection or named here (criterion K-14), so a column added later forces the decision instead of
being silently dropped from every copy."""

ADDITIONAL_COST_COLUMNS_NOT_COPIED: frozenset[str] = frozenset(
    {"id", "scenario_id", "position_id", "risk_id", "created_at", "updated_at"}
)
"""Additional-cost attributes a copy does **not** inherit (SC-5-05), for both halves of the copy —
the position-attached costs in `copy_staffing_positions` and the scenario-level ones in
`app.data.additional_cost.copy_scenario_additional_costs`, which imports this set from here (it
already depends on this module for its scope; the reverse import would be a cycle).

- `id` — a copy is a new row (AC-02).
- `scenario_id` — the copy's own scenario.
- `position_id` — the *copied* position's id (from `new_position_ids`), or `NULL` for a
  scenario-level cost; never the source's value.
- `risk_id` — the copy's **own** risk, found by the source risk's name
  (`app.data.risk_copy.copied_risk_ids`), or `NULL` for an unlinked cost (SC-6-08; ADR-0021,
  point 8); never the source's value, which would point at another scenario's risk.
- `created_at` / `updated_at` — the copy is created now, and its ADR-0007 marker is its own.

Everything else — category, amount, currency, type, period, funding — is copied by reflection, and a
drift guard asserts every mapped attribute is on one side or the other
(`tests/test_additional_cost_copy.py`)."""


def copy_staffing_positions(session: Session, source: Scenario, copy: Scenario) -> None:
    """Copy one scenario's staffing positions, their month rows **and their absences** (AC-02).

    The single entry in `app.data.project_writes.SCENARIO_CHILD_COPIERS` for this aggregate, and
    deliberately one entry for **three** tables since SC-3-02 (ADR-0004, addendum 2026-09-19,
    point 1, extended by the addendum of 2026-09-22, point 1). The registry's contract (`Session`,
    source scenario, copy) carries no room for a mapping from old position ids to new ones, and a
    separately registered copier for the month rows or the absences would have nowhere to get one:
    both point at a `position_id` whose value is new on the copy. So the mapping is held here,
    locally, between the passes of this function — the price being that "one registry entry per
    table" stops being literally true and becomes "one entry per aggregate whose root is a child of
    the scenario". A completeness test over that registry, if one is ever written, has to know the
    difference, or the next grandchild table will look registered while it is not.

    **Since SC-5-05 a fourth pass copies the additional costs attached to a position** (ADR-0014,
    point 10, Q-6 = A): they need `new_position_ids` exactly as the months and absences do. The
    costs with no position are not this function's — they have their own registry entry.

    **What is deliberately not copied: the approval snapshot.** `approved_snapshot_*` rows are the
    third group of scenario children (ADR-0004, addendum 2026-09-22, point 2) — written once at
    approval, never copied — so their absence from this cascade is *required*, not permitted, and
    criterion K-17's canary asserts that a copy of an approved scenario holds zero of them.

    `flush()` between the passes for the same reason `copy_scenario` flushes before calling the
    copiers: the child needs a parent row to point at. The enclosing transaction stays the caller's,
    so a failure in a later pass undoes the earlier ones.

    Nothing here is guarded against writing to an `approved` scenario, and nothing here needs to be:
    every copy is a `draft` (`copy_scenario` takes no status parameter), so this function only ever
    writes into a scenario that accepts writes. The *source* is read and never written, which is why
    copying an approved scenario leaves its approval intact — and why a copy is the legal way to
    continue changing an approved calculation (ADR-0004).
    """
    positions = list(
        session.execute(
            sa.select(StaffingPosition)
            .where(StaffingPosition.scenario_id == source.id)
            .order_by(StaffingPosition.start_date, StaffingPosition.id)
        )
        .scalars()
        .all()
    )
    if not positions:
        return

    new_position_ids: dict[uuid.UUID, uuid.UUID] = {}
    for position in positions:
        new_position_id = uuid.uuid4()
        session.add(
            StaffingPosition(
                id=new_position_id,
                scenario_id=copy.id,
                **values_to_copy(position, excluded=POSITION_COLUMNS_NOT_COPIED),
            )
        )
        new_position_ids[position.id] = new_position_id
    session.flush()

    allocations = list(
        session.execute(
            sa.select(StaffingPositionAllocation).where(
                StaffingPositionAllocation.position_id.in_(new_position_ids)
            )
        )
        .scalars()
        .all()
    )
    for allocation in allocations:
        session.add(
            StaffingPositionAllocation(
                id=uuid.uuid4(),
                position_id=new_position_ids[allocation.position_id],
                **values_to_copy(allocation, excluded=ALLOCATION_COLUMNS_NOT_COPIED),
            )
        )
    session.flush()

    # Third pass: the absences (F-05, SC-3-02). The same mapping and the same reflection as the
    # month rows — and `position_id` taken from `new_position_ids`, never from the source row, or
    # the copy's absences would hang off the *source's* positions and deleting one on the copy would
    # change the original (criterion K-14's mutation b).
    absences = list(
        session.execute(
            sa.select(StaffingPositionAbsence).where(
                StaffingPositionAbsence.position_id.in_(new_position_ids)
            )
        )
        .scalars()
        .all()
    )
    for absence in absences:
        session.add(
            StaffingPositionAbsence(
                id=uuid.uuid4(),
                position_id=new_position_ids[absence.position_id],
                **values_to_copy(absence, excluded=ABSENCE_COLUMNS_NOT_COPIED),
            )
        )
    session.flush()

    # Fourth pass: the additional costs attached to a position (F-08, SC-5-05). Here and not in a
    # registry entry of their own, because this is the one place holding `new_position_ids`
    # (ADR-0014, point 10, Q-6 = A; ADR-0004, addendum SC-5-05, point 4). `position_id` comes from
    # the mapping, never from the source row — a copied cost pointing at the *source's* position is
    # refused by `fk_additional_cost_position_same_scenario` anyway, and pointing it at another
    # position of the copy would silently move the cost. The costs **without** a position are the
    # other half, copied by `app.data.additional_cost.copy_scenario_additional_costs` through its
    # own entry in `SCENARIO_CHILD_COPIERS`; neither pass touches the other's rows.
    position_costs = list(
        session.execute(
            sa.select(AdditionalCost)
            .where(AdditionalCost.position_id.in_(new_position_ids))
            .order_by(AdditionalCost.start_month, AdditionalCost.id)
        )
        .scalars()
        .all()
    )
    # SC-6-08: a link to a declared risk is remapped to the copy's own risk (ADR-0021, point 8).
    # The risk entry of `SCENARIO_CHILD_COPIERS` runs **before** this function, so the copy's
    # risks exist already; the mapping is by the source risk's name.
    risk_mapping = copied_risk_ids(session, source.id, copy.id)
    for cost in position_costs:
        session.add(
            AdditionalCost(
                id=uuid.uuid4(),
                scenario_id=copy.id,
                position_id=new_position_ids[cost.position_id],
                risk_id=remapped(risk_mapping, cost.risk_id),
                **values_to_copy(cost, excluded=ADDITIONAL_COST_COLUMNS_NOT_COPIED),
            )
        )
    session.flush()
