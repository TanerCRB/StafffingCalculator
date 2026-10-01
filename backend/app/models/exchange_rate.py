"""Manually maintained, directed exchange-rate windows (F-02, ADR-0006)."""

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Computed,
    Date,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import DATERANGE, ExcludeConstraint, Range
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

VALID_PERIOD_EXPRESSION = "daterange(effective_from, (effective_to + 1), '[)')"
RATE_NO_OVERLAP_CONSTRAINT = "ex_exchange_rates_no_overlapping_periods"
SCOPE_SENTINEL = "'00000000-0000-0000-0000-000000000000'::uuid"


class ExchangeRate(Base):
    __tablename__ = "exchange_rates"

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("projects.id"), nullable=True
    )
    scenario_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("scenarios.id"), nullable=True
    )
    source_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    target_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    valid_period: Mapped[Range[date]] = mapped_column(
        DATERANGE, Computed(VALID_PERIOD_EXPRESSION, persisted=True), nullable=False
    )
    rate: Mapped[Decimal] = mapped_column(Numeric(20, 10), nullable=False)
    source: Mapped[str] = mapped_column(String(200), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint("source_currency ~ '^[A-Z]{3}$'", name="source_currency_iso4217"),
        CheckConstraint("target_currency ~ '^[A-Z]{3}$'", name="target_currency_iso4217"),
        CheckConstraint("source_currency <> target_currency", name="currency_pair_distinct"),
        CheckConstraint("rate > 0", name="rate_positive"),
        CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from", name="period_ordered"
        ),
        CheckConstraint("source ~ '[^[:space:]]'", name="source_not_blank"),
        ExcludeConstraint(
            (text(f"coalesce(project_id, {SCOPE_SENTINEL})"), "="),
            (text(f"coalesce(scenario_id, {SCOPE_SENTINEL})"), "="),
            ("source_currency", "="),
            ("target_currency", "="),
            ("valid_period", "&&"),
            using="gist",
            name=RATE_NO_OVERLAP_CONSTRAINT,
        ),
    )
