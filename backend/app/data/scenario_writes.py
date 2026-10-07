"""Writes that create a fresh scenario in an existing project (SC-1-26)."""

import uuid

import sqlalchemy as sa
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.identity import CallerIdentity
from app.data.project_reads import CallerProjectView, project_for_caller
from app.data.write_errors import WriteFailed, WriteRefused, failure_for
from app.models.project import ProjectStatus
from app.models.scenario import Scenario, ScenarioStatus

_SCENARIO_NAME_UNIQUE_CONSTRAINT = next(
    constraint.name
    for constraint in Scenario.__table__.constraints
    if isinstance(constraint, sa.UniqueConstraint)
    and {column.name for column in constraint.columns} == {"project_id", "name"}
)


class ScenarioCreateFailed(WriteFailed):
    """The database could not complete a scenario create."""


class ScenarioCreateRefused(ScenarioCreateFailed, WriteRefused):
    """The database refused a scenario create."""


class ScenarioNameConflict(RuntimeError):
    """The exact scenario name already exists in the selected project."""


class ScenarioProjectArchived(RuntimeError):
    """The selected project is archived and cannot receive a fresh scenario."""


def create_scenario(
    session: Session, caller: CallerIdentity, project_id: uuid.UUID, name: str
) -> tuple[Scenario, CallerProjectView] | None:
    """Create and commit a draft under a caller-visible, active project.

    `None` collapses an unknown project and one outside the caller's scope. Exact-name conflicts
    are checked for a friendly path and still enforced by the database constraint for races.
    """
    project_view = project_for_caller(session, caller, project_id)
    if project_view is None:
        return None
    project = project_view.project
    if project.status == ProjectStatus.ARCHIVED:
        raise ScenarioProjectArchived("Scenario creation is refused for an archived project.")
    taken = session.scalar(
        sa.select(Scenario.id).where(Scenario.project_id == project_id, Scenario.name == name)
    )
    if taken is not None:
        raise ScenarioNameConflict(
            "A scenario with this exact name already exists in this project."
        )

    scenario = Scenario(project_id=project_id, name=name, status=ScenarioStatus.DRAFT)
    session.add(scenario)
    try:
        session.commit()
    except SQLAlchemyError as error:
        session.rollback()
        diagnostics = getattr(getattr(error, "orig", None), "diag", None)
        if (
            getattr(diagnostics, "sqlstate", None) == "23505"
            and getattr(diagnostics, "constraint_name", None)
            == _SCENARIO_NAME_UNIQUE_CONSTRAINT
        ):
            raise ScenarioNameConflict(
                "A scenario with this exact name already exists in this project."
            ) from None
        raise failure_for(
            error,
            subject="scenario create",
            refused=ScenarioCreateRefused,
            failed=ScenarioCreateFailed,
        ) from None
    return scenario, project_view
