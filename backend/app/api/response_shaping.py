"""The single layer that turns project rows into API payloads (ADR-0005).

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
"""

from collections.abc import Sequence
from typing import Any

from app.api.schemas.project import (
    DeliveryPeriod,
    ProjectDetail,
    ProjectListItem,
    ProjectListResponse,
    ScenarioListItem,
)
from app.core.identity import CallerIdentity, Permission
from app.data.project_reads import CallerProjectView
from app.domain.scenario_readiness import assess
from app.models.project import Project, ProjectStatus
from app.models.scenario import Scenario, ScenarioStatus

PERSONNEL_COST_FIELDS: frozenset[str] = frozenset()
"""Response fields carrying individual personnel costs. Empty until plan block 5 adds them."""

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
