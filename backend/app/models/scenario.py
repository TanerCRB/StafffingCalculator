"""Scenario — one independent calculation belonging to a project (F-01, F-02).

Naming: the mockup's "Calculation" and this entity are the same thing; the entity is called
`Scenario` in the data model and in the API (gate-1 decision 5).

Every configurable input below is nullable on purpose: F-01 requires saving an incomplete
calculation as a draft. Which of them are missing is answered by
`app.domain.scenario_readiness`, not by a hardcoded list in the API layer.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.project import Project


class ScenarioStatus(StrEnum):
    """`draft` / `approved` — the two states ADR-0004 names. This task reads them only; the
    approval transition and the immutability guard belong to ADR-0004's own tasks."""

    DRAFT = "draft"
    APPROVED = "approved"


class Scenario(Base):
    __tablename__ = "scenarios"

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[ScenarioStatus] = mapped_column(
        Enum(
            ScenarioStatus,
            name="scenario_status",
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=False,
        default=ScenarioStatus.DRAFT,
    )

    # --- configurable inputs, each independent per scenario (F-02) -----------------------------
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    working_calendar: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # NUMERIC, mapped to Decimal — never float, on a money path or next to one (NF-01, ADR-0002).
    full_time_hours_per_week: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    target_margin_percent: Mapped[Decimal | None] = mapped_column(Numeric(6, 3), nullable=True)
    # The scenario level of the assumption chain (F-02, ADR-0012): `NULL` on either of these two
    # means "no override on this scenario — inherit from the project, then the organisation"
    # (`app.domain.assumptions`). Read the *resolved* value through that module, never the column:
    # the column alone cannot tell "inherits 18 %" from "has no value".
    overload_threshold_percent: Mapped[Decimal | None] = mapped_column(
        Numeric(6, 3), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    project: Mapped["Project"] = relationship(back_populates="scenarios")

    __table_args__ = (
        # Two scenarios of one project are two independent rows, not two views of one row.
        UniqueConstraint("project_id", "name"),
        CheckConstraint(
            "end_date IS NULL OR start_date IS NULL OR end_date >= start_date",
            name="scenario_period_ordered",
        ),
        # Strictly positive on every level of the chain (SC-1-10, K-09; gate 1, G-2).
        CheckConstraint("overload_threshold_percent > 0", name="overload_threshold_positive"),
    )
