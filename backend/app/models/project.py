"""The Project aggregate (F-01).

Scope note: `status` (Active/Archived) is a visibility state and nothing more. The one code path
that writes it is `app.data.project_writes.archive_project` (SC-1-04); ADR-0004 (addendum
2026-09-18) keeps it out of the immutability question entirely — the only immutability boundary is
a scenario's `approved` status.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, Date, DateTime, Enum, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.organization_defaults import PERCENT_PRECISION, PERCENT_SCALE

if TYPE_CHECKING:
    from app.models.scenario import Scenario


class ProjectStatus(StrEnum):
    """Row status shown in the list's Status column (gate-1 decision 7)."""

    ACTIVE = "active"
    ARCHIVED = "archived"


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    client: Mapped[str] = mapped_column(String(200), nullable=False)
    owner: Mapped[str] = mapped_column(String(200), nullable=False)
    # Calendar dates, not points in time — a delivery period has no timezone
    # (invariant-guardian.md rule 15).
    delivery_period_start: Mapped[date] = mapped_column(Date, nullable=False)
    delivery_period_end: Mapped[date] = mapped_column(Date, nullable=False)
    # ISO-4217 code only. No monetary amount lives on the project row; when one appears it
    # arrives as an amount/currency pair (ADR-0002, ADR-0006).
    reporting_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # --- the project level of the assumption chain (F-02, ADR-0012) ----------------------------
    # Overrides of the organisation's defaults, `NULL` meaning "no override here — inherit". The
    # same column names as on `scenarios` and `organization_defaults`, so the resolution rule
    # (`app.domain.assumptions`) reads one name on three levels and cannot pair the wrong ones.
    # Group 2 of ADR-0004's addendum 2026-09-18: they enter the calculation, so once a scenario of
    # the project is approved they are frozen
    # (`app.data.project_writes.FROZEN_BY_APPROVED_SCENARIO`).
    target_margin_percent: Mapped[Decimal | None] = mapped_column(
        Numeric(PERCENT_PRECISION, PERCENT_SCALE), nullable=True
    )
    overload_threshold_percent: Mapped[Decimal | None] = mapped_column(
        Numeric(PERCENT_PRECISION, PERCENT_SCALE), nullable=True
    )
    status: Mapped[ProjectStatus] = mapped_column(
        Enum(
            ProjectStatus,
            name="project_status",
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=False,
        default=ProjectStatus.ACTIVE,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    scenarios: Mapped[list["Scenario"]] = relationship(
        back_populates="project", cascade="all, delete-orphan", order_by="Scenario.name"
    )

    __table_args__ = (
        CheckConstraint(
            "delivery_period_end >= delivery_period_start",
            name="delivery_period_ordered",
        ),
        CheckConstraint("char_length(reporting_currency) = 3", name="reporting_currency_iso4217"),
        # F-01 names `name`, `client` and `owner` as fields of a project; `NOT NULL` on its own
        # still admits `''` and `'   '`, and until now the only thing refusing those was the
        # Pydantic request schema. That defends the API and nothing else — a fixture, a seed
        # script or a future import writes straight to the table. The pattern requires at least
        # one non-whitespace character, the same claim `strip_whitespace=True` makes at the
        # boundary (added by migration `4f0a9c1b7d62`).
        CheckConstraint("name ~ '[^[:space:]]'", name="name_not_blank"),
        CheckConstraint("client ~ '[^[:space:]]'", name="client_not_blank"),
        CheckConstraint("owner ~ '[^[:space:]]'", name="owner_not_blank"),
        # Strictly positive on every level of the chain, in the database (SC-1-10, K-09; gate 1,
        # G-2). `NULL` passes a CHECK, which is what "no override" needs.
        CheckConstraint("overload_threshold_percent > 0", name="overload_threshold_positive"),
    )
