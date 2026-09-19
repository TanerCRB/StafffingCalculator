"""SQLAlchemy models. Importing this package registers every table on `Base.metadata`."""

from app.models.catalog import (
    CatalogDefaultRate,
    CatalogEngagementType,
    CatalogLocation,
    CatalogRole,
    CatalogSeniority,
)
from app.models.project import Project, ProjectStatus
from app.models.project_access import ProjectAccess
from app.models.scenario import Scenario, ScenarioStatus
from app.models.staffing import StaffingPosition, StaffingPositionAllocation

__all__ = [
    "CatalogDefaultRate",
    "CatalogEngagementType",
    "CatalogLocation",
    "CatalogRole",
    "CatalogSeniority",
    "Project",
    "ProjectAccess",
    "ProjectStatus",
    "Scenario",
    "ScenarioStatus",
    "StaffingPosition",
    "StaffingPositionAllocation",
]
