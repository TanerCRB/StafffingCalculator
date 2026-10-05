"""Caller-scoped create replay records for additional costs (SC-5-13)."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AdditionalCostCreateIdempotency(Base):
    __tablename__ = "additional_cost_create_idempotency"

    caller_user_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    key: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    cost_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("additional_cost.id", ondelete="CASCADE"), unique=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
