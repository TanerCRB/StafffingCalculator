"""SQLAlchemy models. Importing this package registers every table on `Base.metadata`."""

from app.models.approved_snapshot import (
    SNAPSHOT_TABLES,
    ApprovedSnapshotAbsenceType,
    ApprovedSnapshotWorkingCalendar,
    ApprovedSnapshotWorkingCalendarDay,
)
from app.models.catalog import (
    AbsenceType,
    CatalogDefaultRate,
    CatalogEngagementType,
    CatalogLocation,
    CatalogRole,
    CatalogSeniority,
    CatalogVendor,
    WorkingCalendar,
    WorkingCalendarDay,
    WorkingCalendarDayKind,
)
from app.models.project import Project, ProjectStatus
from app.models.project_access import ProjectAccess
from app.models.scenario import Scenario, ScenarioStatus
from app.models.staffing import (
    StaffingPosition,
    StaffingPositionAbsence,
    StaffingPositionAllocation,
)

__all__ = [
    "SNAPSHOT_TABLES",
    "AbsenceType",
    "ApprovedSnapshotAbsenceType",
    "ApprovedSnapshotWorkingCalendar",
    "ApprovedSnapshotWorkingCalendarDay",
    "CatalogDefaultRate",
    "CatalogEngagementType",
    "CatalogLocation",
    "CatalogRole",
    "CatalogSeniority",
    "CatalogVendor",
    "Project",
    "ProjectAccess",
    "ProjectStatus",
    "Scenario",
    "ScenarioStatus",
    "StaffingPosition",
    "StaffingPositionAbsence",
    "StaffingPositionAllocation",
    "WorkingCalendar",
    "WorkingCalendarDay",
    "WorkingCalendarDayKind",
]
