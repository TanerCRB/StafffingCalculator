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
"""

from collections.abc import Sequence
from typing import Any

from app.api.schemas.catalog import (
    AbsenceTypeEntry,
    AbsenceTypeList,
    CatalogRate,
    CatalogRateList,
    DimensionEntry,
    DimensionEntryList,
    WorkingCalendarDayEntry,
    WorkingCalendarEntry,
    WorkingCalendarList,
)
from app.api.schemas.project import (
    DeliveryPeriod,
    ProjectDetail,
    ProjectListItem,
    ProjectListResponse,
    ScenarioListItem,
)
from app.api.schemas.staffing import (
    DerivedCapacitySource,
    StaffingAbsence,
    StaffingAbsenceList,
    StaffingAllocation,
    StaffingPositionList,
    StaffingPositionRead,
)
from app.core.identity import CallerIdentity, Permission
from app.data.catalog import DimensionRow
from app.data.project_reads import CallerProjectView
from app.data.staffing import StaffingPositionView
from app.domain.capacity import NO_CALENDAR, MonthCapacity
from app.domain.scenario_readiness import assess
from app.models.catalog import AbsenceType, CatalogDefaultRate, WorkingCalendar
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

_PROJECT_STATUS_LABELS = {
    ProjectStatus.ACTIVE: "Active",
    ProjectStatus.ARCHIVED: "Archived",
}
_SCENARIO_STATUS_LABELS = {
    ScenarioStatus.DRAFT: "Draft",
    ScenarioStatus.APPROVED: "Approved",
}


def _shape_scenario(scenario: Scenario) -> ScenarioListItem:
    readiness = assess(scenario)
    return ScenarioListItem(
        id=scenario.id,
        name=scenario.name,
        status=_SCENARIO_STATUS_LABELS[scenario.status],
        missing_inputs=list(readiness.missing_inputs),
        ready_for_approval=readiness.ready_for_approval,
        target_margin_percent=scenario.target_margin_percent,
    )


def _common_project_fields(project: Project) -> dict[str, Any]:
    """The fields every project representation shares. One source, so the list row and the
    detail row cannot drift into disagreeing about the same project."""
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
        "scenarios": [_shape_scenario(scenario) for scenario in project.scenarios],
    }


def _shape_project(view: CallerProjectView, caller: CallerIdentity) -> ProjectListItem:
    return _without_personnel_costs(
        ProjectListItem(**_common_project_fields(view.project)), view, caller
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
        **_common_project_fields(project),
        owner=project.owner,
        # The concurrency token (ADR-0007). Added to the detail representation only, so the list
        # contract of SC-1-05/06 is unchanged: `_common_project_fields` stays the shared subset.
        updated_at=project.updated_at,
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
