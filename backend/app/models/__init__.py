"""SQLAlchemy models. Importing this package registers every table on `Base.metadata`."""

from app.models.additional_cost import AdditionalCost
from app.models.approved_snapshot import (
    SNAPSHOT_TABLES,
    ApprovedSnapshotAbsenceBudget,
    ApprovedSnapshotAbsenceType,
    ApprovedSnapshotCatalogDefaultRate,
    ApprovedSnapshotOrganizationDefaults,
    ApprovedSnapshotWorkingCalendar,
    ApprovedSnapshotWorkingCalendarDay,
)
from app.models.catalog import (
    AbsenceBudget,
    AbsenceType,
    CatalogCostCategory,
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
from app.models.commercial_terms import CommercialTerms, StoryPointsTerms, TmTerms
from app.models.organization_defaults import OrganizationDefaults
from app.models.project import Project, ProjectStatus
from app.models.project_access import ProjectAccess
from app.models.scenario import Scenario, ScenarioStatus
from app.models.scenario_delivery_segment import ScenarioDeliverySegment
from app.models.staffing import (
    StaffingPosition,
    StaffingPositionAbsence,
    StaffingPositionAllocation,
)

__all__ = [
    "SNAPSHOT_TABLES",
    "AbsenceBudget",
    "AbsenceType",
    "AdditionalCost",
    "ApprovedSnapshotAbsenceBudget",
    "ApprovedSnapshotAbsenceType",
    "ApprovedSnapshotCatalogDefaultRate",
    "ApprovedSnapshotOrganizationDefaults",
    "ApprovedSnapshotWorkingCalendar",
    "ApprovedSnapshotWorkingCalendarDay",
    "CatalogCostCategory",
    "CatalogDefaultRate",
    "CatalogEngagementType",
    "CatalogLocation",
    "CatalogRole",
    "CatalogSeniority",
    "CatalogVendor",
    "CommercialTerms",
    "OrganizationDefaults",
    "Project",
    "ProjectAccess",
    "ProjectStatus",
    "Scenario",
    "ScenarioDeliverySegment",
    "ScenarioStatus",
    "StaffingPosition",
    "StaffingPositionAbsence",
    "StaffingPositionAllocation",
    "StoryPointsTerms",
    "TmTerms",
    "WorkingCalendar",
    "WorkingCalendarDay",
    "WorkingCalendarDayKind",
]
