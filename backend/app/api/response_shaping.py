"""The layer that turns database rows into API payloads, with permission-dependent fields removed
(ADR-0005).

ADR-0005 puts permission-dependent field removal here, in response shaping, so that the API,
the PDF/spreadsheet export (F-11) and any server-side rendering share one implementation
instead of three. Every endpoint returning project data goes through this module.

Personnel costs: `PERSONNEL_COST_FIELDS` is empty today because no personnel-cost column exists
in the schema yet (F-07/F-08, plan block 5). The gate is wired and takes both of its factors, but
it currently removes nothing — this is a seam, not proof that AC-06 is satisfied.

The gate is a conjunction of two mechanisms (ADR-0005, addendum 2026-09-19): the caller's
`PERSONNEL_COSTS_READ` permission *and* `project_access.can_view_personnel_costs` for this
(caller, project) pair. This module never queries for the second one — it arrives on the
`CallerProjectView` the single read path produced, which is why every shaping function here takes
a view rather than a bare `Project`: there is no way to shape a project without having been handed
the flag, so the gate cannot be half-applied by forgetting an argument. The view also names the
caller it was built for, and `_without_personnel_costs` refuses to shape it for anyone else — see
its docstring.

**Two gates, not one** (ADR-0005, addendum 2026-09-19 "pierwszy zbiór danych bez zasięgu projektu",
point 4). `_without_personnel_costs` gates project payloads on the *conjunction* above.
`_without_catalog_personnel_costs` gates catalogue payloads on the permission alone, because a
catalogue row has no project for the second factor to be true or false about. The addendum accepts
that asymmetry explicitly and fixes its direction: the single-factor form applies **only where there
is no project**. The day a catalogue rate travels inside a response describing a project or a
scenario (a resolved staffing-position rate — plan blocks 3-5), the conjunction applies unchanged;
reading a project's cost rate "through the catalogue" must not become a way around the assignment
flag. Both gates remove *fields* and never refuse the row, and both do it here rather than in the
frontend (AC-06, NF-04), so the F-11 export inherits them.

**A third gate since SC-5-01** (ADR-0005, aneks 2026-09-23 SC-5-01): `_without_scenario_personnel_
costs` gates a scenario's base personnel cost on the same conjunction as the project gate, with its
own field set (`SCENARIO_COST_FIELDS`) and its own view type (`ScenarioCostView`) — the first gate
here that removes a real personnel-cost figure inside a project context. `PERSONNEL_COST_FIELDS`
(project payloads) stays empty.
"""

from collections.abc import Sequence
from typing import Any

from app.api.schemas.additional_cost import (
    AdditionalCostAssumptionsRead,
    AdditionalCostPeriodRead,
    AdditionalCostRead,
    AdditionalCostSpreadRead,
    AdditionalCostTotalRead,
    ScenarioAdditionalCosts,
)
from app.api.schemas.catalog import (
    AbsenceBudgetEntry,
    AbsenceBudgetList,
    AbsenceTypeEntry,
    AbsenceTypeList,
    CatalogRate,
    CatalogRateList,
    DimensionEntry,
    DimensionEntryList,
    StatutoryLeaveRegime,
    WorkingCalendarDayEntry,
    WorkingCalendarEntry,
    WorkingCalendarList,
)
from app.api.schemas.commercial_terms import (
    CategoryRevenueRead,
    CommercialTermsRead,
    OutcomeCategoriesRead,
    OutcomeCategoryRead,
    OutcomeTermsRead,
    RateWindowRead,
    RevenueAssumptionsRead,
    RevenueRead,
    ScenarioCommercialTerms,
    UnresolvedMonthRead,
)
from app.api.schemas.personnel_cost import (
    CostAssumptionsRead,
    CostRateWindowRead,
    PaidAbsenceAssumptionsRead,
    PaidAbsenceMonthHoursRead,
    PersonnelCostRead,
    ScenarioPersonnelCost,
    UnresolvedCostMonthRead,
    UnresolvedPaidAbsenceMonthRead,
)
from app.api.schemas.project import (
    DeliveryPeriod,
    ProjectDetail,
    ProjectListItem,
    ProjectListResponse,
    ScenarioListItem,
)
from app.api.schemas.scenario import ResolvedAssumptionRead, ScenarioAssumptions
from app.api.schemas.scenario_results import ScenarioResults
from app.api.schemas.scenario_what_if import ScenarioWhatIfSalaryRaiseResults
from app.api.schemas.staffing import (
    AbsenceBudgetSource,
    DerivedCapacitySource,
    StaffingAbsence,
    StaffingAbsenceList,
    StaffingAllocation,
    StaffingPositionList,
    StaffingPositionRead,
)
from app.core.identity import CallerIdentity, Permission
from app.core.money import NOT_APPLICABLE
from app.data.additional_cost import AdditionalCostRow, ScenarioAdditionalCostView
from app.data.assumptions import ScenarioAssumptionsView
from app.data.catalog import DimensionRow
from app.data.commercial_terms import ScenarioCommercialView
from app.data.organization_defaults import OrganizationLevel
from app.data.personnel_cost import ScenarioCostView
from app.data.project_reads import CallerProjectView
from app.data.scenario_results import ScenarioResultsView
from app.data.scenario_what_if import ScenarioWhatIfView
from app.data.staffing import StaffingPositionView
from app.domain.absence_budget import NO_STATUTORY_LEAVE_TYPE, BudgetShare, StatutoryLeaveType
from app.domain.absence_budget import RESOLVED as BUDGET_RESOLVED
from app.domain.additional_cost import CALCULATED as ADDITIONAL_COST_CALCULATED
from app.domain.additional_cost import AdditionalCostResult, AdditionalCostUnavailable
from app.domain.assumptions import resolve_all
from app.domain.capacity import NO_CALENDAR, MonthCapacity
from app.domain.paid_absence_cost import (
    FullyLoadedPaidAbsenceCostAnswer,
    FullyLoadedPaidAbsenceCostResult,
    PaidAbsenceCostAnswer,
    PaidAbsenceCostResult,
)
from app.domain.personnel_cost import CALCULATED as COST_CALCULATED
from app.domain.personnel_cost import (
    FullyLoadedPersonnelCostAnswer,
    FullyLoadedPersonnelCostResult,
    PersonnelCostResult,
)
from app.domain.revenue import CALCULATED as REVENUE_CALCULATED
from app.domain.revenue import EXPECTED_NOT_APPLICABLE, RevenueResult, RevenueUnavailable
from app.domain.scenario_readiness import assess
from app.domain.scenario_results import scenario_profitability
from app.models.catalog import (
    AbsenceBudget,
    AbsenceType,
    CatalogDefaultRate,
    WorkingCalendar,
)
from app.models.commercial_terms import (
    OUTCOME_CATEGORIES,
    OutcomeTerms,
    probability_column,
    units_column,
)
from app.models.project import Project, ProjectStatus
from app.models.scenario import Scenario, ScenarioStatus
from app.models.staffing import StaffingPositionAbsence

PERSONNEL_COST_FIELDS: frozenset[str] = frozenset()
"""Response fields carrying individual personnel costs. Empty until plan block 5 adds them."""

CATALOG_PERSONNEL_COST_FIELDS: frozenset[str] = frozenset({"default_cost_rate"})
"""Catalogue response fields carrying a personnel cost — one, and it is a real column (SC-2-01).

Unlike `PERSONNEL_COST_FIELDS` above, this set is **not** empty, so the catalogue gate removes
something today and the criterion tests need no stand-in field (the substitution SC-1-08 had to make
was itself the weakest part of that proof). Emptying this set is therefore a mutation the delivered
tests kill: a caller without `PERSONNEL_COSTS_READ` would start receiving the cost rate.

A rate's *currency* and *selling* rate are not in here: F-13/AC-06 protect what a person costs, and
removing the selling rate would make a commercial figure that every planner needs invisible."""

SCENARIO_COST_FIELDS: frozenset[str] = frozenset(
    {
        "amount",
        "assumptions_used",
        # SC-5-06 (ADR-0013, aneks 2026-09-23 SC-5-06, point 6): the paid-absence component's
        # amount, **its budget part** and its assumptions. The budget part is a cost although the
        # budget is not (ADR-0005, aneks SC-3-03, point 4): days × a cost rate is what a person's
        # leave costs, while the same days in the staffing and catalogue payloads stay outside this
        # set, as they were.
        "paid_absence_amount",
        "paid_absence_budget_amount",
        "paid_absence_assumptions_used",
        # SC-5-02 (Issue #77, K-01/K-03/K-05; ADR-0013 aneks 2026-09-25): the fully loaded cost and
        # its surcharge, for the base component and for the paid-absence component — the identical
        # conjunction as `amount`/`paid_absence_amount`, through this same set (K-03), never a
        # second mechanism for a second money figure.
        "fully_loaded_amount",
        "surcharge_amount",
        "paid_absence_fully_loaded_amount",
        "paid_absence_surcharge_amount",
    }
)
"""Fields of a scenario's personnel cost that carry a personnel cost (SC-5-01, SC-5-06, SC-5-02).

A third set, next to `PERSONNEL_COST_FIELDS` (project payloads — still empty, and deliberately left
so: ADR-0005, aneks 2026-09-23 SC-5-01, point 3) and `CATALOG_PERSONNEL_COST_FIELDS` (catalogue
rows). Applied by `_without_scenario_personnel_costs` to `PersonnelCostRead`.

**Two fields, and both are necessary**: `amount` is the cost, and `assumptions_used` names every
cost rate the amount was computed from — removing the amount and leaving the rates would leak the
same figure one multiplication away (criterion K-04). The *sum* is gated exactly like the rates,
although F-13 speaks of "individual" costs: with one position of `headcount = 1` the scenario's sum
**is** one person's cost (point 5 — overpaid protection, accepted on purpose).

Not in here: `state`, `cost_basis` and `currency` — nor, since SC-5-06, `paid_absence_state` and
`paid_absence_currency`. None of them is a figure a person costs, and a refused caller still learns
that a cost exists and why it may not be stateable — the refusal is of the *field*, never of the
scenario (point 1)."""

SCENARIO_PROFITABILITY_FIELDS: frozenset[str] = frozenset(
    {"profit", "margin", "markup", "included_cost"}
)
"""Fields of a scenario's whole-life result that mix a personnel cost into one number (SC-7-01,
ADR-0005 aneks 2026-09-24).

A fourth set, next to `PERSONNEL_COST_FIELDS` (project payloads), `CATALOG_PERSONNEL_COST_FIELDS`
(catalogue rows) and `SCENARIO_COST_FIELDS` (a scenario's base personnel cost). Applied by
`_without_scenario_profitability` to `ScenarioResults`, which is otherwise gated exactly like
`PersonnelCostRead` — the same conjunction, on the same `ScenarioCostView` — so the two never
disagree about whether this caller may see this scenario's personnel costs.

**Not `revenue` and not `additional_cost`.** Neither one is a personnel cost by itself (SC-4-01,
point 3; SC-5-05, point 1), and each keeps answering under `RESULTS_READ` alone. What earns a place
in this set is that `profit`, `margin`, `markup` and `included_cost` cannot be split back into a
personnel and a non-personnel part after they are computed — a caller who may not see the personnel
cost may not be handed a profit either, because a profit and a personnel cost one subtraction apart
is the same leak `SCENARIO_COST_FIELDS` exists to close."""

_PROJECT_STATUS_LABELS = {
    ProjectStatus.ACTIVE: "Active",
    ProjectStatus.ARCHIVED: "Archived",
}
_SCENARIO_STATUS_LABELS = {
    ScenarioStatus.DRAFT: "Draft",
    ScenarioStatus.APPROVED: "Approved",
}


def _shape_scenario(
    scenario: Scenario, project: Project, organization_level: OrganizationLevel
) -> ScenarioListItem:
    # Readiness asks the resolved value, not the column (SC-1-10, gate 1 Q-5): an inherited margin
    # is not a missing one. The organisation level arrived with the view; nothing here queries.
    readiness = assess(
        scenario, resolve_all(scenario, project, organization_level.for_scenario(scenario))
    )
    return ScenarioListItem(
        id=scenario.id,
        name=scenario.name,
        status=_SCENARIO_STATUS_LABELS[scenario.status],
        missing_inputs=list(readiness.missing_inputs),
        ready_for_approval=readiness.ready_for_approval,
        target_margin_percent=scenario.target_margin_percent,
    )


def shape_duplicated_scenario(
    scenario: Scenario, project: Project, organization_level: OrganizationLevel
) -> ScenarioListItem:
    """The response of `POST …/scenarios/{id}/duplicate` (SC-6-01, F-09 pt.1) — the same shape a
    scenario has inside `ProjectDetail.scenarios`, built by the same `_shape_scenario` so the two
    payloads cannot drift into disagreeing about what a scenario row looks like. A thin public name
    for a private helper: every other reader of a scenario row goes through this module and this
    one is no exception, even though the duplicate is a freshly created `draft` with nothing yet
    planned in it.
    """
    return _shape_scenario(scenario, project, organization_level)


def shape_scenario_assumptions(view: ScenarioAssumptionsView) -> ScenarioAssumptions:
    """One scenario's resolved assumptions as the API returns them (SC-1-10).

    **No `caller` argument, and that absence is the statement** — the one `shape_staffing_position`
    makes: a target margin and an overload threshold are commercial parameters, not what a person
    costs (Issue #4, "Dane osobowe: nie dotyczy"), so nothing here is gated on a permission. The day
    a resolved *rate* or cost travels through this payload it grows the SC-1-08 conjunction.

    Nothing is decided here: value, state and source arrive resolved from `app.domain.assumptions`,
    and the frozen-or-live choice was made in `app.data.organization_defaults`.
    """
    resolved = {
        name: ResolvedAssumptionRead(
            value=assumption.value, state=assumption.state, source=assumption.source
        )
        for name, assumption in view.assumptions.items()
    }
    return ScenarioAssumptions(
        id=view.scenario.id,
        status=_SCENARIO_STATUS_LABELS[view.scenario.status],
        **resolved,
    )


def _common_project_fields(view: CallerProjectView) -> dict[str, Any]:
    """The fields every project representation shares. One source, so the list row and the
    detail row cannot drift into disagreeing about the same project."""
    project = view.project
    return {
        "id": project.id,
        "name": project.name,
        "client": project.client,
        "delivery_period": DeliveryPeriod(
            start=project.delivery_period_start, end=project.delivery_period_end
        ),
        "reporting_currency": project.reporting_currency,
        "description": project.description,
        "status": _PROJECT_STATUS_LABELS[project.status],
        "scenarios": [
            _shape_scenario(scenario, project, view.organization_level)
            for scenario in project.scenarios
        ],
    }


def _shape_project(view: CallerProjectView, caller: CallerIdentity) -> ProjectListItem:
    return _without_personnel_costs(
        ProjectListItem(**_common_project_fields(view)), view, caller
    )


def shape_project_detail(view: CallerProjectView, caller: CallerIdentity) -> ProjectDetail:
    """Shape one project the caller has already been granted by the data layer.

    Like `shape_project_list`, this does not decide access and must never be asked to: it is
    reached only with a row `app.data.project_reads` returned, i.e. one inside the caller's
    `project_access` scope. It does apply the personnel-cost gate, which is a different
    question (F-13: seeing a project ≠ seeing individual costs).
    """
    project = view.project
    item = ProjectDetail(
        **_common_project_fields(view),
        owner=project.owner,
        # The concurrency token (ADR-0007). Added to the detail representation only, so the list
        # contract of SC-1-05/06 is unchanged: `_common_project_fields` stays the shared subset.
        updated_at=project.updated_at,
        # The project level's own override columns, as stored (SC-1-10, R-01) — deliberately not
        # `resolve_all`: `null` here is "no override on this level", the meaning `PATCH` gives it.
        # Commercial parameters, not personnel costs, so the gate below does not concern them.
        target_margin_percent=project.target_margin_percent,
        overload_threshold_percent=project.overload_threshold_percent,
    )
    return _without_personnel_costs(item, view, caller)


def _without_personnel_costs[ProjectItemT: ProjectListItem](
    item: ProjectItemT, view: CallerProjectView, caller: CallerIdentity
) -> ProjectItemT:
    """Remove personnel-cost fields unless *both* halves of the gate say yes.

    The conjunction (ADR-0005, addendum 2026-09-19, points 1–2):

    - `caller.has(PERSONNEL_COSTS_READ)` — may this caller see personnel costs *at all*;
    - `view.can_view_personnel_costs` — is this caller's `project_access` row *for this project*
      one of the assignments that narrowing allows.

    Both are per-request: the permission set is rebuilt from the request's auth context
    (`app.api.deps.get_caller_identity`) and the flag comes from the scope-filtered statement that
    produced `view`. Neither is cached, and neither is a client-supplied field.

    Separate from project access on purpose: a caller may legitimately see a project and still not
    be allowed to see individual costs (F-13, AC-06) — so this is a denial of a *field*, never of
    the project, and it never turns into a 403 or a 404.

    The flag is decided per `view`, i.e. per (caller, project), so two projects in one list
    response can answer differently. Resolving it once per caller — from "the caller's
    `project_access` row", singular — would be the same bug as caching an identity: a
    per-assignment question answered per subject.

    `view` and `caller` arrive as two arguments, so the `assert` below is what keeps them one
    subject: it fails rather than shaping user A's view with user B's permission set. Today the
    single read path makes that impossible by construction; the assertion is here for the second
    producer of views (F-11 export, a server-to-server interface), where a mismatch would otherwise
    be a silent widening of the gate and not a crash. It runs before the conjunction on purpose —
    a check placed after it would already have read a flag that may belong to somebody else.
    """
    # Raised explicitly rather than written as `assert`: `python -O` removes an `assert` statement,
    # and a gate-widening mismatch must not be the thing an optimisation flag switches off.
    if view.user_id != caller.user_id:
        raise AssertionError(
            "A project view built for one user is being shaped with another user's identity: "
            "the personnel-cost gate would combine one caller's assignment flag with another "
            "caller's permission set. Build the view through app.data.project_reads for the "
            "caller the response is for."
        )
    if not PERSONNEL_COST_FIELDS:
        return item
    if caller.has(Permission.PERSONNEL_COSTS_READ) and view.can_view_personnel_costs:
        return item
    return item.model_copy(update=dict.fromkeys(PERSONNEL_COST_FIELDS))


def shape_project_list(
    views: Sequence[CallerProjectView], caller: CallerIdentity
) -> ProjectListResponse:
    """Shape an already access-filtered sequence of projects.

    This function does not filter by access and must never be asked to: scope is applied in the
    query (`app.data.project_reads`), so an inaccessible project is never in this sequence in
    the first place.

    Each view carries its own cost-visibility flag, and the gate is applied row by row: a list is
    where a per-caller shortcut would be invisible, because with one accessible project the two
    readings agree.
    """
    return ProjectListResponse(projects=[_shape_project(view, caller) for view in views])


# --- the catalogue (SC-2-01) --------------------------------------------------------------------
# A second shaping function with a different gate input, accepted as such by ADR-0005's addendum of
# 2026-09-19 (point 4): "jedno miejsce" becomes two functions in one module, not two modules. It
# takes a bare row and a caller — there is no `CallerCatalogView` and there must not be one,
# because a view object exists to carry a per-(caller, row) flag and the catalogue has no such flag
# to carry (ADR-0001, addendum 2026-09-19). Inventing one would suggest a scope decision nobody
# makes here.


def _without_catalog_personnel_costs(item: CatalogRate, caller: CallerIdentity) -> CatalogRate:
    """Remove the catalogue's cost-rate field unless the caller holds `PERSONNEL_COSTS_READ`.

    One factor, and this is the exception ADR-0005's addendum of 2026-09-19 ("pierwszy zbiór danych
    bez zasięgu projektu", point 3) creates, named as a weakening: outside a project context the
    second factor of the SC-1-08 conjunction — `project_access.can_view_personnel_costs` — has no
    subject, since there is no project it could be true or false *about*. Applied literally, the
    conjunction would close this gate forever rather than gate it.

    The direction of the exception must not be reversed: it holds **only where there is no
    project**. A resolved rate inside a project or scenario response goes back through the
    conjunction.

    The permission is read from the caller the request produced
    (`app.api.deps.get_caller_identity`, rebuilt per request, never cached and never taken from
    a body field). The denial is the removal of a field: same `200`, same row, dimensions and
    selling rate intact (point 5). A `403` or a `404` here would deny the *rate*, and with it
    the selling rate the caller is entitled to; a `404` would additionally make "you may not see
    this cost" indistinguishable from "this tuple has no rate"."""
    if caller.has(Permission.PERSONNEL_COSTS_READ):
        return item
    return item.model_copy(update=dict.fromkeys(CATALOG_PERSONNEL_COST_FIELDS))


def shape_dimension_entry(entry: DimensionRow) -> DimensionEntry:
    """One dictionary entry as the API returns it — from the list, the create and the edit paths.

    **No `caller` argument, and that absence is the statement**, exactly as on
    `shape_staffing_position`: a dictionary entry is an id, a name and a concurrency marker, and not
    one of the three is gated on a permission (ADR-0005, addendum 2026-09-19, point 1 — a catalogue
    row belongs to no project and no user). A caller parameter here would suggest a gate that is not
    there, which is the dangerous direction to be wrong in.

    One function rather than three inline constructions (SC-2-04): the marker is a field a client
    cannot edit without, so the path that forgot to carry it would be the path from which editing is
    impossible — and with three copies, adding the next field is three edits and one of them is the
    one somebody misses.
    """
    return DimensionEntry(id=entry.id, name=entry.name, updated_at=entry.updated_at)


def shape_dimension_entry_list(entries: Sequence[DimensionRow]) -> DimensionEntryList:
    """A whole dictionary — every row through the function above, no second construction path."""
    return DimensionEntryList(entries=[shape_dimension_entry(entry) for entry in entries])


def shape_working_calendar(calendar: WorkingCalendar) -> WorkingCalendarEntry:
    """One working calendar with its exceptional days (F-05, SC-3-02).

    **No `caller` argument**, for the reason `shape_dimension_entry` gives: a calendar belongs to no
    project and no user, and not one of its fields is gated on a permission (ADR-0005, addendum
    2026-09-22, points 1 and 7). `standard_hours_per_day` in particular is *not* a personnel cost —
    it is how long a working day is, the same figure for everyone in that location — so the SC-1-08
    conjunction is not activated by this payload and none is applied.

    The days are passed through in the order the relationship loaded them (by date), so a calendar
    read twice is comparable with itself.
    """
    return WorkingCalendarEntry(
        id=calendar.id,
        name=calendar.name,
        standard_hours_per_day=calendar.standard_hours_per_day,
        week_pattern=calendar.week_pattern,
        days=[
            WorkingCalendarDayEntry(day=row.day, kind=row.kind.value) for row in calendar.days
        ],
        updated_at=calendar.updated_at,
    )


def shape_working_calendar_list(
    calendars: Sequence[WorkingCalendar],
) -> WorkingCalendarList:
    """Every calendar through the function above — no second construction path."""
    return WorkingCalendarList(
        calendars=[shape_working_calendar(calendar) for calendar in calendars]
    )


def shape_absence_budget(
    budget: AbsenceBudget, statutory: StatutoryLeaveType | None
) -> AbsenceBudgetEntry:
    """One leave budget as the API returns it, with the regime of the flagged absence type.

    **No `caller` argument, and that absence is the statement** — the same one
    `shape_working_calendar` makes. A budget belongs to no project and no user, and not one of its
    fields is gated on a permission: a number of days is not an amount, and the multiplier that
    would make it one is gated separately and is not in this payload (ADR-0005, addendum 2026-09-22
    SC-3-03, point 3). A caller parameter here would advertise a gate that is not there, which is
    the dangerous direction to be wrong in.

    **The day this repository returns the *cost* of a budget, that is a different field and a
    different gate** (point 4): a cost inside a response describing a project or a scenario goes
    through the SC-1-08 conjunction — `PERSONNEL_COSTS_READ` **and**
    `project_access.can_view_personnel_costs` — and the single-factor catalogue exception explicitly
    does not apply to it. Nothing here may be read as that gate already being in place.

    `statutory` is passed in rather than looked up, because this layer never receives a `Session`
    and must never grow a query of its own. `None` is the named state of ADR-0008's addendum (point
    8b): the regime is `"n/a"` and the nested object is absent — never `false`, which would read as
    a decided answer nobody decided, and never a `500`.
    """
    named = statutory is not None
    return AbsenceBudgetEntry(
        id=budget.id,
        calendar_id=budget.calendar_id,
        engagement_type_id=budget.engagement_type_id,
        budget_days=budget.budget_days,
        unit=budget.unit,
        source=budget.source,
        effective_from=budget.effective_from,
        # `assert`-free narrowing for the type checker: the column is nullable in the shared shape
        # of the pattern and a CHECK constraint refuses `NULL` on this table (ADR-0008, addendum
        # SC-3-03, point 10b), so a row that reaches here always carries an end date.
        effective_to=budget.effective_to,  # type: ignore[arg-type]
        statutory_leave_state=BUDGET_RESOLVED if named else NO_STATUTORY_LEAVE_TYPE,
        statutory_leave=(
            StatutoryLeaveRegime(
                absence_type_id=statutory.absence_type_id,
                name=statutory.name,
                generates_cost=statutory.generates_cost,
                generates_revenue=statutory.generates_revenue,
            )
            if statutory is not None
            else None
        ),
        # Read from the flagged type on every row, never copied onto the budget (criterion K-04):
        # a literal here would stop following the type the day somebody edits it.
        generates_cost=statutory.generates_cost if statutory is not None else NOT_APPLICABLE,
        generates_revenue=(
            statutory.generates_revenue if statutory is not None else NOT_APPLICABLE
        ),
        updated_at=budget.updated_at,
    )


def shape_absence_budget_list(
    budgets: Sequence[AbsenceBudget], statutory: StatutoryLeaveType | None
) -> AbsenceBudgetList:
    """Every budget through the function above — no second construction path.

    One `statutory` for the whole list, resolved once by the data layer: the flag is a property of
    the dictionary and not of a budget row, so resolving it per row would be N lookups answering one
    question — and it is the shape in which two rows of one response could disagree about which type
    the organisation settles against.
    """
    return AbsenceBudgetList(
        budgets=[shape_absence_budget(budget, statutory) for budget in budgets]
    )


def shape_absence_type(absence_type: AbsenceType) -> AbsenceTypeEntry:
    """One absence type with both of its flags, independently (F-05, SC-3-02).

    The two flags are read off the row and neither is derived from the other: `generates_revenue`
    is not `not generates_cost`, and an implementation that aliased one to the other would be the
    mutation criterion K-11 names. No `caller` argument — see `shape_working_calendar`.
    """
    return AbsenceTypeEntry(
        id=absence_type.id,
        name=absence_type.name,
        generates_cost=absence_type.generates_cost,
        generates_revenue=absence_type.generates_revenue,
        # The third flag (SC-3-03), read off the row like the other two and derived from nothing:
        # which type the leave budget settles against is a stored fact, not a name match.
        is_statutory_leave=absence_type.is_statutory_leave,
        updated_at=absence_type.updated_at,
    )


def shape_absence_type_list(absence_types: Sequence[AbsenceType]) -> AbsenceTypeList:
    """Every absence type through the function above — no second construction path."""
    return AbsenceTypeList(
        absence_types=[shape_absence_type(entry) for entry in absence_types]
    )


def shape_catalog_rate(rate: CatalogDefaultRate, caller: CallerIdentity) -> CatalogRate:
    """One catalogue rate row as this caller may see it.

    `effective_to` is passed through as stored — inclusive, `None` when open-ended. No day is added
    or subtracted anywhere on this path: that conversion exists in exactly one place, the generated
    `valid_period` column (ADR-0008, point 3).
    """
    return _without_catalog_personnel_costs(
        CatalogRate(
            id=rate.id,
            role_id=rate.role_id,
            seniority_id=rate.seniority_id,
            location_id=rate.location_id,
            engagement_type_id=rate.engagement_type_id,
            # Passed through for every caller, on all three paths that carry a rate. The vendor is
            # not a gated field: ADR-0005's addendum of 2026-09-21 (point 2) puts every vendor's
            # price list inside `CATALOG_READ`, explicitly and as a business decision. What *is*
            # gated is `default_cost_rate` below — identically for a vendor row and an internal one
            # (point 3, criterion K-06).
            vendor_id=rate.vendor_id,
            default_cost_rate=rate.default_cost_rate,
            default_selling_rate=rate.default_selling_rate,
            currency=rate.currency,
            unit=rate.unit,
            # Passed through for every caller, exactly like `vendor_id` above and for the identical
            # reason (SC-5-02, criterion K-04): a percentage/flag that only multiplies an
            # already-gated `default_cost_rate` is an organisational parameter classified under
            # `CATALOG_READ` alone (ADR-0005, aneks 2026-09-25, Q4) — never added to
            # `CATALOG_PERSONNEL_COST_FIELDS`, which stays the one-element set it always was.
            surcharge_percent=rate.surcharge_percent,
            includes_surcharge=rate.includes_surcharge,
            effective_from=rate.effective_from,
            effective_to=rate.effective_to,
            # ADR-0007's concurrency marker (SC-2-04), on every representation of the row and for
            # every caller. Not gated: it is a timestamp of the row, and a caller who cannot see the
            # cost rate still needs it to edit the fields they can see without overwriting somebody
            # else's change.
            updated_at=rate.updated_at,
        ),
        caller,
    )


def _shape_derived_capacity(capacity: MonthCapacity) -> dict[str, Any]:
    """The derived capacity of one month as the three fields the allocation payload carries.

    Returned as a mapping spread into `StaffingAllocation` rather than as a nested object, because
    the criterion these fields exist for is "the derived figure travels **beside** the typed one"
    (K-08) — and a nested object would put it one level away from `availability_hours`, where a
    client could plausibly render one without the other.

    The `no_calendar` branch produces `"n/a"` and no source, never `0.00` and never an exception
    (K-23). Nothing here decides *which* branch it is: `app.domain.capacity` did, and repeating the
    condition would be a second place answering one question.
    """
    if capacity.state == NO_CALENDAR:
        return {
            "derived_capacity_hours": capacity.hours,
            "derived_capacity_state": capacity.state,
            "derived_capacity_source": None,
            **_shape_absence_budget_share(capacity),
        }
    return {
        "derived_capacity_hours": capacity.hours,
        "derived_capacity_state": capacity.state,
        "derived_capacity_source": DerivedCapacitySource(
            calendar_id=capacity.calendar_id,
            calendar_name=capacity.calendar_name,
            standard_hours_per_day=capacity.standard_hours_per_day,
            working_days=capacity.working_days,
            absence_day_equivalents=capacity.absence_day_equivalents,
        ),
        **_shape_absence_budget_share(capacity),
    }


def _shape_absence_budget_share(capacity: MonthCapacity) -> dict[str, Any]:
    """The leave budget behind one month's capacity, as the three fields the payload carries.

    Nothing here decides which state it is, and nothing here computes or re-rounds an hour figure:
    `app.domain.absence_budget` resolved the state **and** allocated the month's hours (the
    remainder distribution of S-01/R-05 makes the month's slice depend on where it sits in the
    window), and `app.domain.capacity` subtracted exactly that figure. Reporting anything else here
    — a second `quantize`, a share recomputed from days — is what would let the payload and the
    capacity disagree by a cent.

    Every state that is not `resolved` answers `"n/a"` and no source: `no_budget`, `no_calendar` and
    `no_statutory_leave_type` alike. Never `0.00`, which is a number every later sum would add up
    and which a real budget of zero days already means (ADR-0008, addendum 2026-09-22 SC-3-03,
    point 7). The source object is present exactly when the budget was **applied**, which is a
    stronger statement than "a budget row exists": a budget nobody can settle against
    (`no_statutory_leave_type`) deducted nothing, and showing its figures beside a deduction of
    `"n/a"` would invite reading them as one that happened.
    """
    share: BudgetShare | None = capacity.budget
    if share is None or share.state != BUDGET_RESOLVED:
        return {
            "absence_budget_hours": NOT_APPLICABLE,
            # A month with no calendar shows the calendar's own state here too, so a client has one
            # named state to render and not two (addendum SC-3-03, point 3a).
            "absence_budget_state": NO_CALENDAR if share is None else share.state,
            "absence_budget_source": None,
        }
    return {
        "absence_budget_hours": share.hours,
        "absence_budget_state": share.state,
        "absence_budget_source": AbsenceBudgetSource(
            budget_id=share.budget_id,
            budget_days=share.budget_days,
            entitlement_days=share.entitlement_days,
            unit=share.unit,
            source=share.source,
            effective_from=share.effective_from,
            effective_to=share.effective_to,
            months_in_window=share.months_in_window,
            statutory_days_absorbed=share.statutory_days_absorbed,
        ),
    }


def shape_staffing_position(view: StaffingPositionView) -> StaffingPositionRead:
    """One staffing position with its month rows and absences, as the API returns it (SC-3-01/02).

    **No `caller` argument, and that absence is the statement.** Every other function in this module
    takes one because it gates a field on a permission; a staffing position has no gated field to
    remove — it carries a dimension tuple, a headcount, a period, hours and now a derived capacity,
    and not one figure a currency could be attached to (ADR-0005, addendum 2026-09-19, point 5; the
    absence type's flags are configuration, not a cost — addendum 2026-09-22, point 7). A caller
    parameter here would suggest a gate that is not there, which is the dangerous direction to be
    wrong in (the argument `app.data.catalog` makes for having no guard function).

    What that means for the day a resolved rate does appear on a position (F-07, plan block 5): this
    function grows a `caller` argument *and* the SC-1-08 conjunction — the caller's
    `PERSONNEL_COSTS_READ` **and** `project_access.can_view_personnel_costs` for the position's
    project — because that is a rate inside a response describing a scenario, where the addendum's
    single-factor exception explicitly does not apply. Reading a project's cost rate "through the
    staffing grid" must not become a way around the assignment flag.

    Hours are passed through as stored and nothing here rounds them: `NUMERIC(10,2)` is the input's
    own precision, and the one rounding of the *derived* figure already happened in
    `app.domain.capacity`, through `app.core.money.round_money` — the project's single rounding
    point (invariant-guardian rule 2). There is no second `quantize` on this path.

    It takes a `StaffingPositionView` rather than a `StaffingPosition` because the derived capacity
    is not on the row: this layer never receives a `Session` and must never grow a query of its own,
    so the figure arrives from the read that produced it, exactly as the personnel-cost flag does
    for a project.
    """
    position = view.position
    return StaffingPositionRead(
        id=position.id,
        role_id=position.role_id,
        seniority_id=position.seniority_id,
        location_id=position.location_id,
        engagement_type_id=position.engagement_type_id,
        headcount=position.headcount,
        start_date=position.start_date,
        end_date=position.end_date,
        updated_at=position.updated_at,
        allocations=[
            StaffingAllocation(
                id=allocation.id,
                period_month=allocation.period_month,
                availability_hours=allocation.availability_hours,
                planned_allocation_hours=allocation.planned_allocation_hours,
                billable_hours=allocation.billable_hours,
                **_shape_derived_capacity(view.capacity[allocation.period_month]),
            )
            for allocation in position.allocations
        ],
        absences=[shape_staffing_absence(absence) for absence in position.absences],
    )


def shape_staffing_absence(absence: StaffingPositionAbsence) -> StaffingAbsence:
    """One absence as the API returns it — from the list path and from both write paths.

    Four fields, and the ones that are missing are the point: there is no person and no note to
    shape, because there is no column for one (ADR-0005, addendum 2026-09-22, point 6). One function
    rather than three inline constructions, so a fifth field could not be added on one path and
    forgotten on another.
    """
    return StaffingAbsence(
        id=absence.id,
        absence_type_id=absence.absence_type_id,
        start_date=absence.start_date,
        end_date=absence.end_date,
    )


def shape_staffing_absence_list(
    absences: Sequence[StaffingPositionAbsence],
) -> StaffingAbsenceList:
    """An already scope-filtered sequence of absences — every row through the function above."""
    return StaffingAbsenceList(
        absences=[shape_staffing_absence(absence) for absence in absences]
    )


def shape_staffing_position_list(
    views: Sequence[StaffingPositionView],
) -> StaffingPositionList:
    """Shape an already scope-filtered sequence of positions — every row through the function above.

    This function decides no access and must never be asked to: the scope is applied in the query
    (`app.data.staffing`, which inherits it from `project_for_caller`), so a position of a scenario
    the caller may not see is never in this sequence in the first place.
    """
    return StaffingPositionList(positions=[shape_staffing_position(view) for view in views])


def shape_scenario_commercial_terms(view: ScenarioCommercialView) -> ScenarioCommercialTerms:
    """One scenario's commercial rule and revenue as the API returns them (SC-4-01).

    **No `caller` argument, and that absence is the statement** — the one `shape_staffing_position`
    makes. Nothing in this payload is a personnel cost: a revenue and a selling rate are what the
    client pays, not what a person costs (ADR-0005, addendum 2026-09-23 SC-4-01, point 3; the
    precedent is `default_selling_rate` staying outside `CATALOG_PERSONNEL_COST_FIELDS`). What makes
    that true is the schema, which has no cost field to remove — asserted by equality of the whole
    field set (criterion K-11). The first task adding profit or margin here grows a `caller`
    argument *and* the SC-1-08 conjunction — the results endpoint (SC-7-01) is that task, and it
    answers by adding a *different* payload (`ScenarioResults`) rather than a field here: this
    schema's field set is unchanged.

    Nothing is decided here: the state, the amount and the windows arrive resolved from
    `app.data.commercial_terms`; the amount was rounded once, through `app.core.money.round_money`,
    in `app.domain.revenue_time_and_material`, and is not re-rounded on the way out.
    """
    revenue = _revenue_read_of(view.revenue)
    terms = view.terms
    return ScenarioCommercialTerms(
        scenario_id=view.scenario.id,
        scenario_status=_SCENARIO_STATUS_LABELS[view.scenario.status],
        commercial_terms=(
            None
            if terms is None
            else CommercialTermsRead(
                id=terms.id,
                model_type=terms.model_type,
                updated_at=terms.updated_at,
                outcome_terms=_outcome_terms_read_of(view.outcome_terms),
            )
        ),
        revenue=revenue,
    )


def _outcome_terms_read_of(details: OutcomeTerms | None) -> OutcomeTermsRead | None:
    """Parametry reguły Outcome-based przepisane z wiersza (R-04, runda 2 SC-4-03) — bez
    zaokrąglenia i bez wartości domyślnej: `NULL` w bazie to `null` w odpowiedzi, nigdy `"0"`.
    Nazwy kolumn kategorii przez `units_column`/`probability_column` — jedna pisownia z modelem."""
    if details is None:
        return None
    return OutcomeTermsRead(
        currency=details.currency,
        fixed_fee=details.fixed_fee,
        success_bonus=details.success_bonus,
        unit_rate=details.unit_rate,
        revenue_min=details.revenue_min,
        revenue_max=details.revenue_max,
        categories=OutcomeCategoriesRead(
            **{
                category: OutcomeCategoryRead(
                    units=getattr(details, units_column(category)),
                    probability=getattr(details, probability_column(category)),
                )
                for category in OUTCOME_CATEGORIES
            }
        ),
    )


def _revenue_read_of(answer: RevenueResult | RevenueUnavailable) -> RevenueRead:
    """Build one revenue payload from its answer — shared by `shape_scenario_commercial_terms`
    above (SC-4-01) and the results endpoint (SC-7-01, `shape_scenario_results`), so the two cannot
    drift into different readings of the same `RevenueAnswer` (a second copy is how one figure ends
    up stated differently on two endpoints).

    Nothing is decided here: the state, the amount and the windows arrive resolved from
    `app.data.commercial_terms`; the amount was rounded once, through `app.core.money.round_money`,
    in `app.domain.revenue_time_and_material`, and is not re-rounded on the way out.
    """
    assumptions = answer.assumptions_used
    assumptions_read = RevenueAssumptionsRead(
        model_type=assumptions.model_type,
        hours_source=assumptions.hours_source,
        vendor_axis=assumptions.vendor_axis,
        rate_source=assumptions.rate_source,
        rate_windows=[
            RateWindowRead(
                source_rate_id=window.source_rate_id,
                effective_from=window.effective_from,
                effective_to=window.effective_to,
                default_selling_rate=window.selling_rate,
                currency=window.currency,
            )
            for window in assumptions.rate_windows
        ],
        unresolved_months=[
            UnresolvedMonthRead(position_id=month.position_id, period_month=month.period_month)
            for month in assumptions.unresolved_months
        ],
        currencies=list(assumptions.currencies),
    )
    if isinstance(answer, RevenueResult):
        # Przychód oczekiwany i per kategoria (SC-4-03) przechodzą tak, jak je podała domena —
        # zaokrąglone tam raz, tutaj niczego nie liczy się ani nie zaokrągla ponownie.
        return RevenueRead(
            state=REVENUE_CALCULATED,
            amount=answer.revenue,
            currency=answer.currency,
            assumptions_used=assumptions_read,
            expected_state=answer.expected_state,
            expected_amount=answer.expected_revenue,
            category_revenues=[
                CategoryRevenueRead(
                    category=category.category,
                    units=category.units,
                    probability=category.probability,
                    amount=category.revenue,
                )
                for category in answer.category_revenues
            ],
        )
    # Nazwany stan przychodu: żadnej kwoty w żadnym polu (ADR-0003, aneks SC-4-03, pkt 7; O-4).
    return RevenueRead(
        state=answer.reason,
        amount=NOT_APPLICABLE,
        currency=None,
        assumptions_used=assumptions_read,
        expected_state=EXPECTED_NOT_APPLICABLE,
        expected_amount=NOT_APPLICABLE,
        category_revenues=[],
    )


# --- a scenario's base personnel cost (SC-5-01) --------------------------------------------------
# The third shaping function with its own gate (ADR-0005, aneks 2026-09-23 SC-5-01, point 4): not an
# extension of `_without_personnel_costs` (the payload is not a project) nor of
# `_without_catalog_personnel_costs` (the rate is not a bare catalogue row — it sits inside a
# scenario, so the conjunction applies, never the catalogue's single-factor exception). The point
# also says this is the last one admitted without a new decision.


def _without_scenario_personnel_costs(
    item: PersonnelCostRead, view: ScenarioCostView, caller: CallerIdentity
) -> PersonnelCostRead:
    """Remove `SCENARIO_COST_FIELDS` unless *both* halves of the SC-1-08 conjunction say yes.

    - `caller.has(PERSONNEL_COSTS_READ)` — may this caller see personnel costs at all;
    - `view.can_view_personnel_costs` — is this caller's `project_access` row **for the project this
      scenario belongs to** one that allows it. It arrived with the view, from the same scope read
      that returned the scenario (`app.data.staffing.scenario_view_in_scope`); nothing here
      queries.

    A denial of the *field*: same `200`, same scenario, `state` and `cost_basis` intact, every
    member of `SCENARIO_COST_FIELDS` `null` (point 1) — since SC-5-06 the paid-absence amount, its
    budget part and its assumptions too. Never a `403` or a `404`.

    The same identity check as `_without_personnel_costs`, for the same reason: `view` and `caller`
    are two arguments, and shaping user A's flag with user B's permission set would widen the gate
    silently. Raised explicitly, so `python -O` cannot remove it, and before the conjunction reads
    a flag that may belong to somebody else.
    """
    if view.user_id != caller.user_id:
        raise AssertionError(
            "A scenario cost view built for one user is being shaped with another user's "
            "identity: the personnel-cost gate would combine one caller's assignment flag with "
            "another caller's permission set. Build the view through app.data.personnel_cost for "
            "the caller the response is for."
        )
    if caller.has(Permission.PERSONNEL_COSTS_READ) and view.can_view_personnel_costs:
        return item
    return item.model_copy(update=dict.fromkeys(SCENARIO_COST_FIELDS))


def _paid_absence_fields(answer: PaidAbsenceCostAnswer) -> dict[str, Any]:
    """The paid-absence component (SC-5-06) as the five `paid_absence_*` fields of the payload.

    Spread into `PersonnelCostRead` rather than built as a nested object, so the component's gated
    fields are members of `SCENARIO_COST_FIELDS` by name and go through the one
    `_without_scenario_personnel_costs` below — a nested object would need a second field set and a
    second removal, i.e. a second gate (ADR-0013, aneks 2026-09-23 SC-5-06, point 6: "the same
    conjunction", not a new one). Nothing is decided here: the state, both amounts and the hours
    arrive from `app.domain.paid_absence_cost`, rounded there once, not re-rounded here.
    """
    assumptions = answer.assumptions_used
    assumptions_read = PaidAbsenceAssumptionsRead(
        hours_source=assumptions.hours_source,
        months=[
            PaidAbsenceMonthHoursRead(
                position_id=month.position_id,
                period_month=month.period_month,
                manual_hours=month.manual_hours,
                budget_hours=month.budget_hours,
                budget_part=month.budget_part,
            )
            for month in assumptions.months
        ],
        unresolved_months=[
            UnresolvedPaidAbsenceMonthRead(
                position_id=month.position_id,
                period_month=month.period_month,
                reason=month.reason,
            )
            for month in assumptions.unresolved_months
        ],
        currencies=list(assumptions.currencies),
    )
    if isinstance(answer, PaidAbsenceCostResult):
        return {
            "paid_absence_state": COST_CALCULATED,
            "paid_absence_amount": answer.cost,
            "paid_absence_budget_amount": answer.budget_cost,
            "paid_absence_currency": answer.currency,
            "paid_absence_assumptions_used": assumptions_read,
        }
    return {
        "paid_absence_state": answer.reason,
        "paid_absence_amount": NOT_APPLICABLE,
        "paid_absence_budget_amount": NOT_APPLICABLE,
        "paid_absence_currency": None,
        "paid_absence_assumptions_used": assumptions_read,
    }


def _fully_loaded_fields(answer: FullyLoadedPersonnelCostAnswer) -> dict[str, Any]:
    """The base cost's fully loaded pair (SC-5-02, K-01/K-02) as the two `PersonnelCostRead`
    fields — `fully_loaded_amount`/`surcharge_amount`, spread the same way `_paid_absence_fields`
    is, and gated through the same `SCENARIO_COST_FIELDS`/`_without_scenario_personnel_costs`
    mechanism, never a second gate for a second money figure."""
    if isinstance(answer, FullyLoadedPersonnelCostResult):
        return {"fully_loaded_amount": answer.cost, "surcharge_amount": answer.surcharge_amount}
    return {"fully_loaded_amount": NOT_APPLICABLE, "surcharge_amount": NOT_APPLICABLE}


def _paid_absence_fully_loaded_fields(
    answer: FullyLoadedPaidAbsenceCostAnswer,
) -> dict[str, Any]:
    """The paid-absence component's fully loaded pair (SC-5-02, K-05) — the mirror of
    `_fully_loaded_fields`, on `paid_absence_fully_loaded_amount`/
    `paid_absence_surcharge_amount`."""
    if isinstance(answer, FullyLoadedPaidAbsenceCostResult):
        return {
            "paid_absence_fully_loaded_amount": answer.cost,
            "paid_absence_surcharge_amount": answer.surcharge_amount,
        }
    return {
        "paid_absence_fully_loaded_amount": NOT_APPLICABLE,
        "paid_absence_surcharge_amount": NOT_APPLICABLE,
    }


def _personnel_cost_read_of(view: ScenarioCostView) -> PersonnelCostRead:
    """Build one **ungated** base-cost payload from a `ScenarioCostView` — shared by
    `shape_scenario_personnel_cost` below (SC-5-01/SC-5-06) and the results endpoint (SC-7-01,
    `shape_scenario_results`). The gate (`_without_scenario_personnel_costs`) is applied by each
    caller separately, never inside this function, so there is exactly one place that decides
    whether the amount and the rates are shown and it stays that way as this function grows a
    second caller.

    Nothing is decided here but the payload's shape: the state, the amount and the windows arrive
    resolved from `app.data.personnel_cost`; the amount was rounded once, through
    `app.core.money.round_money`, in `app.domain.personnel_cost`, and is not re-rounded on the way
    out.
    """
    answer = view.cost
    assumptions = answer.assumptions_used
    assumptions_read = CostAssumptionsRead(
        hours_source=assumptions.hours_source,
        vendor_axis=assumptions.vendor_axis,
        rate_source=assumptions.rate_source,
        rate_windows=[
            CostRateWindowRead(
                source_rate_id=window.source_rate_id,
                effective_from=window.effective_from,
                effective_to=window.effective_to,
                default_cost_rate=window.cost_rate,
                currency=window.currency,
                surcharge_percent=window.surcharge_percent,
                includes_surcharge=window.includes_surcharge,
            )
            for window in assumptions.rate_windows
        ],
        unresolved_months=[
            UnresolvedCostMonthRead(
                position_id=month.position_id, period_month=month.period_month
            )
            for month in assumptions.unresolved_months
        ],
        currencies=list(assumptions.currencies),
    )
    paid_absence = _paid_absence_fields(view.paid_absence)
    fully_loaded = _fully_loaded_fields(view.fully_loaded_cost)
    paid_absence_fully_loaded = _paid_absence_fully_loaded_fields(view.fully_loaded_paid_absence)
    if isinstance(answer, PersonnelCostResult):
        return PersonnelCostRead(
            state=COST_CALCULATED,
            cost_basis=answer.basis,
            amount=answer.cost,
            currency=answer.currency,
            assumptions_used=assumptions_read,
            **paid_absence,
            **fully_loaded,
            **paid_absence_fully_loaded,
        )
    return PersonnelCostRead(
        state=answer.reason,
        cost_basis=answer.basis,
        amount=NOT_APPLICABLE,
        currency=None,
        assumptions_used=assumptions_read,
        **paid_absence,
        **fully_loaded,
        **paid_absence_fully_loaded,
    )


def shape_scenario_personnel_cost(
    view: ScenarioCostView, caller: CallerIdentity
) -> ScenarioPersonnelCost:
    """One scenario's base personnel cost as this caller may see it (SC-5-01).

    Nothing is decided here but the gate: the payload comes from `_personnel_cost_read_of`. Every
    representation goes through `_without_scenario_personnel_costs` — there is no path out of this
    function that skips it.
    """
    cost = _personnel_cost_read_of(view)
    return ScenarioPersonnelCost(
        scenario_id=view.scenario.id,
        scenario_status=_SCENARIO_STATUS_LABELS[view.scenario.status],
        personnel_cost=_without_scenario_personnel_costs(cost, view, caller),
    )


# --- a scenario's additional costs (SC-5-05) -----------------------------------------------------
# No gate, and no fourth shaping function with one (ADR-0005, aneks 2026-09-23 SC-5-05, point 1):
# additional costs are not personnel costs by nature, so the "fourth shaping function" that point
# 4 of the SC-5-01 addendum reserves stays unused. What makes that true is the absence of a
# `caller` argument below — there is nothing to decide per caller.


def shape_additional_cost(row: AdditionalCostRow) -> AdditionalCostRead:
    """One cost row as the API returns it — from the list, the create and the edit paths alike.

    **No `caller` argument, and that absence is the statement** (the one `shape_staffing_position`
    makes): nothing on this row is gated, under Q-7 = B of ADR-0014. The named risk that decision
    accepted — a cost on a `headcount = 1` position is indirectly about one person — is recorded in
    ADR-0005, aneks SC-5-05, point 2, not hidden here.
    """
    cost = row.cost
    return AdditionalCostRead(
        id=cost.id,
        category_id=cost.category_id,
        category_name=row.category_name,
        position_id=cost.position_id,
        amount=cost.amount,
        currency=cost.currency,
        cost_type=cost.cost_type,
        start_month=cost.start_month,
        end_month=cost.end_month,
        funding_source=cost.funding_source,
        updated_at=cost.updated_at,
    )


def _additional_cost_total_read_of(
    answer: AdditionalCostResult | AdditionalCostUnavailable,
) -> AdditionalCostTotalRead:
    """Build one sum payload from its answer — shared by `shape_scenario_additional_costs` below
    (SC-5-05) and the results endpoint (SC-7-01, `shape_scenario_results`); this one carries no
    gate to share, since none applies to it (module note above the additional-costs section).

    Nothing is decided here: the state, the amount and the spread arrive resolved from
    `app.data.additional_cost`; the amount was rounded once, through `app.core.money.round_money`,
    in `app.domain.additional_cost`, and is not re-rounded on the way out.
    """
    assumptions = answer.assumptions_used
    assumptions_read = AdditionalCostAssumptionsRead(
        costs=[
            AdditionalCostSpreadRead(
                cost_id=spread.line.cost_id,
                position_id=spread.line.position_id,
                category_id=spread.line.category_id,
                category_name=spread.line.category_name,
                funding_source=spread.line.funding_source,
                cost_type=spread.line.cost_type,
                amount=spread.line.amount,
                currency=spread.line.currency,
                months=list(spread.months),
            )
            for spread in assumptions.costs
        ],
        periods=[
            AdditionalCostPeriodRead(
                period_month=period.period_month, cost_ids=list(period.cost_ids)
            )
            for period in assumptions.periods
        ],
        currencies=list(assumptions.currencies),
    )
    if isinstance(answer, AdditionalCostResult):
        return AdditionalCostTotalRead(
            state=ADDITIONAL_COST_CALCULATED,
            amount=answer.amount,
            currency=answer.currency,
            assumptions_used=assumptions_read,
        )
    return AdditionalCostTotalRead(
        state=answer.reason,
        amount=NOT_APPLICABLE,
        currency=None,
        assumptions_used=assumptions_read,
    )


def shape_scenario_additional_costs(view: ScenarioAdditionalCostView) -> ScenarioAdditionalCosts:
    """One scenario's additional costs and their sum (SC-5-05).

    Nothing is decided here: the sum's payload comes from `_additional_cost_total_read_of`.
    """
    total = _additional_cost_total_read_of(view.total)
    return ScenarioAdditionalCosts(
        scenario_id=view.scenario.id,
        scenario_status=_SCENARIO_STATUS_LABELS[view.scenario.status],
        costs=[shape_additional_cost(row) for row in view.costs],
        additional_cost=total,
    )


def shape_catalog_rate_list(
    rates: Sequence[CatalogDefaultRate], caller: CallerIdentity, *, total: int
) -> CatalogRateList:
    """Shape a sequence of rate rows — every row through `shape_catalog_rate`, no exceptions.

    Not `[CatalogRate.model_validate(rate) for rate in rates]`: a list path that built payloads
    directly would be a second, ungated way out of the database, which is how the same field ends up
    removed on the detail path and present on the list one.

    `total` is passed through, not derived from `rates` (K-11): `rates` is already the bounded page
    `app.data.catalog.list_rates` returned, and `len()` on it would silently report "the whole
    catalogue" for however many rows fit in one page — exactly the field this parameter exists so a
    client never has to guess at.
    """
    return CatalogRateList(
        rates=[shape_catalog_rate(rate, caller) for rate in rates], total=total
    )


# --- a scenario's whole-life profit, margin and markup (SC-7-01) --------------------------------
# The fourth shaping function with its own gate (ADR-0005, aneks 2026-09-24): not an extension of
# `_without_scenario_personnel_costs` (this payload also carries `revenue` and `additional_cost`,
# neither of which that function's field set may touch) nor of `_without_personnel_costs` or
# `_without_catalog_personnel_costs` (neither payload here is a project row or a bare catalogue
# row). `personnel_cost` inside this payload goes through the existing SC-5-01/SC-5-06 gate
# unchanged, on the same `ScenarioCostView` — this section adds a gate for the four aggregate
# fields only, and does not touch any of the other three.


def _without_scenario_profitability(
    item: ScenarioResults, view: ScenarioCostView, caller: CallerIdentity
) -> ScenarioResults:
    """Remove `SCENARIO_PROFITABILITY_FIELDS` unless *both* halves of the SC-1-08 conjunction say
    yes — the same conjunction, and the same `ScenarioCostView`, `_without_scenario_personnel_costs`
    already applies to this payload's `personnel_cost` field.

    - `caller.has(PERSONNEL_COSTS_READ)` — may this caller see personnel costs at all;
    - `view.can_view_personnel_costs` — is this caller's `project_access` row **for the project this
      scenario belongs to** one that allows it.

    A denial of the *four fields*: same `200`, same scenario, `revenue`, `additional_cost` and
    `personnel_cost.state` intact — `profit`, `margin`, `markup` and `included_cost` become `null`.
    Never a `403` or a `404` (point 1 of the SC-5-01 gate, unchanged here).

    The same identity check as `_without_scenario_personnel_costs`, for the same reason: `view` and
    `caller` are two arguments, and shaping one caller's aggregate with another caller's permission
    set would widen the gate silently. Raised explicitly, so `python -O` cannot remove it, and
    before the conjunction reads a flag that may belong to somebody else.
    """
    if view.user_id != caller.user_id:
        raise AssertionError(
            "A scenario cost view built for one user is being shaped with another user's "
            "identity: the profitability gate would combine one caller's assignment flag with "
            "another caller's permission set. Build the view through app.data.scenario_results "
            "for the caller the response is for."
        )
    if caller.has(Permission.PERSONNEL_COSTS_READ) and view.can_view_personnel_costs:
        return item
    return item.model_copy(update=dict.fromkeys(SCENARIO_PROFITABILITY_FIELDS))


def shape_scenario_results(view: ScenarioResultsView, caller: CallerIdentity) -> ScenarioResults:
    """One scenario's whole-life profit, margin and markup, next to the three components they are
    built from (SC-7-01).

    `revenue` and `additional_cost` are built by the same functions their own endpoints use
    (`_revenue_read_of`, `_additional_cost_total_read_of`) — never re-derived here — and
    `personnel_cost` by `_personnel_cost_read_of`, then gated by the existing
    `_without_scenario_personnel_costs` exactly as SC-5-01 gates it. The aggregate itself is
    `app.domain.scenario_results.scenario_profitability`'s answer, computed from the three
    *answers* (`view.revenue`, `view.cost_view.cost`, `view.cost_view.paid_absence`,
    `view.additional_cost`) — never from the already-gated `PersonnelCostRead`, so the arithmetic
    cannot accidentally run on a `null` the gate produced.
    """
    cost_view = view.cost_view
    revenue = _revenue_read_of(view.revenue)
    personnel_cost = _without_scenario_personnel_costs(
        _personnel_cost_read_of(cost_view), cost_view, caller
    )
    additional_cost = _additional_cost_total_read_of(view.additional_cost)
    profitability = scenario_profitability(
        view.revenue, cost_view.cost, cost_view.paid_absence, view.additional_cost
    )
    result = ScenarioResults(
        scenario_id=view.scenario.id,
        scenario_status=_SCENARIO_STATUS_LABELS[view.scenario.status],
        revenue=revenue,
        personnel_cost=personnel_cost,
        additional_cost=additional_cost,
        included_cost=profitability.included_cost,
        profit=profitability.profit,
        margin=profitability.margin,
        markup=profitability.markup,
        profitability_state=profitability.state,
    )
    return _without_scenario_profitability(result, cost_view, caller)


# --- the salary-raise what-if (SC-6-04, ADR-0015) -------------------------------------------------
# Not a fifth gate: this reuses `_without_scenario_personnel_costs` and
# `_without_scenario_profitability` unchanged, on a `ScenarioCostView` whose `cost`/`paid_absence`
# are hypothetical but whose `user_id`/`can_view_personnel_costs` are the real ones
# `app.data.scenario_what_if.scenario_what_if_salary_raise_for_caller` resolved — the same
# conjunction, applied to a hypothetical figure exactly as it is applied to a real one.


def shape_scenario_what_if_salary_raise(
    view: ScenarioWhatIfView, caller: CallerIdentity
) -> ScenarioWhatIfSalaryRaiseResults:
    """One scenario's whole-life result under a hypothetical salary raise (SC-6-04, ADR-0015).

    Built from the same private helpers `shape_scenario_results` uses
    (`_revenue_read_of`, `_personnel_cost_read_of`, `_without_scenario_personnel_costs`,
    `_additional_cost_total_read_of`, `_without_scenario_profitability`,
    `app.domain.scenario_results.scenario_profitability`) — never `shape_scenario_results` itself:
    that function's own `ScenarioResultsView` is documented as built only by
    `scenario_results_for_caller`, from two reads of the scenario that are known to agree about its
    real status. A `ScenarioWhatIfView` makes no such claim about the *hypothetical* cost view it
    carries, so this is its own, small composition rather than a call that would misrepresent what
    it was handed (ADR-0015, point 4).
    """
    cost_view = view.cost_view
    revenue = _revenue_read_of(view.revenue)
    personnel_cost = _without_scenario_personnel_costs(
        _personnel_cost_read_of(cost_view), cost_view, caller
    )
    additional_cost = _additional_cost_total_read_of(view.additional_cost)
    profitability = scenario_profitability(
        view.revenue, cost_view.cost, cost_view.paid_absence, view.additional_cost
    )
    result = ScenarioWhatIfSalaryRaiseResults(
        scenario_id=view.scenario.id,
        scenario_status=_SCENARIO_STATUS_LABELS[view.scenario.status],
        salary_raise_percent=view.salary_raise_percent,
        revenue=revenue,
        personnel_cost=personnel_cost,
        additional_cost=additional_cost,
        included_cost=profitability.included_cost,
        profit=profitability.profit,
        margin=profitability.margin,
        markup=profitability.markup,
        profitability_state=profitability.state,
    )
    return _without_scenario_profitability(result, cost_view, caller)
