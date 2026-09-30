"""SQLAlchemy models. Importing this package registers every table on `Base.metadata`."""

from app.models.additional_cost import AdditionalCost
from app.models.approved_snapshot import (
    SNAPSHOT_TABLES,
    ApprovedSnapshotAbsenceBudget,
    ApprovedSnapshotAbsenceType,
    ApprovedSnapshotCatalogDefaultRate,
    ApprovedSnapshotExchangeRate,
    ApprovedSnapshotOrganizationDefaults,
    ApprovedSnapshotWorkingCalendar,
    ApprovedSnapshotWorkingCalendarDay,
)
from app.models.audit_log import AuditActionType, AuditLog
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
from app.models.commercial_terms import (
    CommercialTerms,
    FixedPriceTerms,
    OutcomeTerms,
    StoryPointsTerms,
    TmTerms,
)
from app.models.exchange_rate import ExchangeRate
from app.models.organization_defaults import OrganizationDefaults
from app.models.person import Person
from app.models.project import Project, ProjectStatus
from app.models.project_access import ProjectAccess
from app.models.risk import RiskReserve, ScenarioRisk
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
    "ApprovedSnapshotExchangeRate",
    "ApprovedSnapshotOrganizationDefaults",
    "ApprovedSnapshotWorkingCalendar",
    "ApprovedSnapshotWorkingCalendarDay",
    "AuditActionType",
    "AuditLog",
    "CatalogCostCategory",
    "CatalogDefaultRate",
    "CatalogEngagementType",
    "CatalogLocation",
    "CatalogRole",
    "CatalogSeniority",
    "CatalogVendor",
    "CommercialTerms",
    "FixedPriceTerms",
    "ExchangeRate",
    "OrganizationDefaults",
    "OutcomeTerms",
    "Person",
    "Project",
    "ProjectAccess",
    "ProjectStatus",
    "RiskReserve",
    "Scenario",
    "ScenarioDeliverySegment",
    "ScenarioRisk",
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
