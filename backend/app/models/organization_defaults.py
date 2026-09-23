"""The organisation's default assumptions — the top of the chain organisation → project → scenario
(F-02, ADR-0012).

**One row, and the database says so.** The primary key is a `smallint` that a CHECK pins to `1`, so
a second row is refused by the primary key itself — not by a convention, not by a `SELECT` before an
`INSERT` (which two connections would both pass), and not by application code that a fixture, a
seed script or a future import never runs through. There is no organisation id anywhere in the
schema, so a key naming "which organisation" would be a column nobody can fill; the pinned key says
what is actually true: there is exactly one organisation this deployment plans for.

**No scope column** (ADR-0001, addendum 2026-09-19, the structural criterion): these values belong
to no project and no user. They are read into every scenario of every project, which is why an
approved scenario freezes them (`approved_snapshot_organization_defaults`) rather than reading this
row again (ADR-0012, point 6).

**Every value column is nullable, and `NULL` is a named state** — "the organisation has no default
for this" — never a zero (ADR-0012, point 1). The migration seeds no row (gate 1, P-D): a deployment
that never configured a default resolves every assumption to the named "no value" state rather than
to a number somebody invented in a migration.

**No write path in this task** (SC-1-10, out of scope): the row is written by fixtures only. The
`updated_at` marker is here anyway, because ADR-0007 requires every editable row to carry one and
the task that adds the edit path must not have to migrate this table to get it.
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, DateTime, Numeric, SmallInteger, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

PERCENT_PRECISION = 6
PERCENT_SCALE = 3
"""`NUMERIC(6, 3)` — the type `scenarios.target_margin_percent` has had since SC-1-05, used on every
level of the chain so that a value resolved from the organisation and one set on the scenario are
the same number in the same type, and nothing is narrowed on the way down (NF-01, ADR-0002)."""

SINGLETON_KEY = 1
"""The one value the primary key of `organization_defaults` may hold."""


class OrganizationDefaults(Base):
    __tablename__ = "organization_defaults"

    id: Mapped[int] = mapped_column(
        SmallInteger, primary_key=True, default=SINGLETON_KEY, autoincrement=False
    )
    """Pinned to `SINGLETON_KEY` by `ck_organization_defaults_singleton` — see the module
    docstring. Together with the primary key this refuses a second row inside the statement that
    tries to write it."""

    target_margin_percent: Mapped[Decimal | None] = mapped_column(
        Numeric(PERCENT_PRECISION, PERCENT_SCALE), nullable=True
    )
    """The organisation's default target margin. No range CHECK, on purpose and in line with
    `scenarios.target_margin_percent`, which has never had one: `0` is a legal margin (ADR-0012,
    point 1 — a value, not an absence), and which negative or very high targets are meaningful is a
    business decision nobody has taken yet."""

    overload_threshold_percent: Mapped[Decimal | None] = mapped_column(
        Numeric(PERCENT_PRECISION, PERCENT_SCALE), nullable=True
    )
    """The allocation above which a position counts as overloaded, as a percentage of its
    **derived capacity** (`derived_capacity_hours`, SC-3-02) — gate 1, Q-2. Above `100` is legal
    (over-allocation is a legal state, SC-3-01). Strictly positive
    (`ck_organization_defaults_overload_threshold_positive`, gate 1, G-2): `0 %` would declare
    every allocated hour an overload, and "no threshold" is `NULL`, not `0`. Nothing reads this
    value yet — its consumer is the overload warning SC-3-01 deferred."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint(f"id = {SINGLETON_KEY}", name="singleton"),
        CheckConstraint("overload_threshold_percent > 0", name="overload_threshold_positive"),
    )
