"""SQLAlchemy models. Importing this package registers every table on `Base.metadata`."""

from app.models.project import Project, ProjectStatus
from app.models.project_access import ProjectAccess
from app.models.scenario import Scenario, ScenarioStatus

__all__ = [
    "Project",
    "ProjectAccess",
    "ProjectStatus",
    "Scenario",
    "ScenarioStatus",
]
