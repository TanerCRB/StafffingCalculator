"""`project_access` — which user sees which project (ADR-0005).

Deny by default: a project a user has no row for is not "hidden", it is simply not part of any
result set produced by `app.data.project_reads`.

`user_id` is a plain string, not a foreign key: there is no user table yet, because there is no
authentication yet (ADR-0005, addendum 2026-09-18 — a dated, temporary deviation). The column
becomes a foreign key in the migration that lands the authentication ADR.
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ProjectAccess(Base):
    __tablename__ = "project_access"

    user_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    project_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    # Third, independent permission dimension from ADR-0005. No personnel-cost field exists in
    # the schema yet, so nothing reads this column today — it is carried by the response-shaping
    # layer the moment such a field appears (F-13, AC-06).
    can_view_personnel_costs: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
