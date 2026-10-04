"""Request fingerprints for safely replaying project creation (SC-1-18, ADR-0009 addendum)."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ProjectCreateIdempotency(Base):
    __tablename__ = "project_create_idempotency"

    caller_user_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    key: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), unique=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
