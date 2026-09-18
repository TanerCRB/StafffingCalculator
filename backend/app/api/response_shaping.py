"""The single layer that turns project rows into API payloads (ADR-0005).

ADR-0005 puts permission-dependent field removal here, in response shaping, so that the API,
the PDF/spreadsheet export (F-11) and any server-side rendering share one implementation
instead of three. Every endpoint returning project data goes through this module.

Personnel costs: `PERSONNEL_COST_FIELDS` is empty today because no personnel-cost column exists
in the schema yet (F-07/F-08, plan block 5). The gate is wired and takes the caller's
permission, but it currently removes nothing — this is a seam, not proof that AC-06 is
satisfied.
"""

from collections.abc import Sequence

from app.api.schemas.project import (
    DeliveryPeriod,
    ProjectListItem,
    ProjectListResponse,
    ScenarioListItem,
)
from app.core.identity import CallerIdentity, Permission
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


def _shape_project(project: Project, caller: CallerIdentity) -> ProjectListItem:
    item = ProjectListItem(
        id=project.id,
        name=project.name,
        client=project.client,
        delivery_period=DeliveryPeriod(
            start=project.delivery_period_start, end=project.delivery_period_end
        ),
        reporting_currency=project.reporting_currency,
        description=project.description,
        status=_PROJECT_STATUS_LABELS[project.status],
        scenarios=[_shape_scenario(scenario) for scenario in project.scenarios],
    )
    return _without_personnel_costs(item, caller)


def _without_personnel_costs(item: ProjectListItem, caller: CallerIdentity) -> ProjectListItem:
    """Remove personnel-cost fields unless the caller holds that specific permission.

    Separate from project access on purpose: a caller may legitimately see a project and still
    not be allowed to see individual costs (F-13, AC-06).
    """
    if caller.has(Permission.PERSONNEL_COSTS_READ) or not PERSONNEL_COST_FIELDS:
        return item
    return item.model_copy(update=dict.fromkeys(PERSONNEL_COST_FIELDS))


def shape_project_list(
    projects: Sequence[Project], caller: CallerIdentity
) -> ProjectListResponse:
    """Shape an already access-filtered sequence of projects.

    This function does not filter by access and must never be asked to: scope is applied in the
    query (`app.data.project_reads`), so an inaccessible project is never in this sequence in
    the first place.
    """
    return ProjectListResponse(projects=[_shape_project(project, caller) for project in projects])
