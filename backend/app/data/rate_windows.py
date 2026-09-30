"""Which catalogue windows a (position, month) reads — shared by the revenue and the cost paths.

**Geometry only, never a predicate.** This module answers "which internal windows of the position's
tuple overlap its month" and "how many days is a range", for the live catalogue and for the approval
snapshot. It does **not** answer whether those windows *resolve* the month: that question is asked
separately by each calculation over its own rate column —

- `app.data.commercial_terms.month_is_priced` over (`default_selling_rate`, `currency`) — revenue
  (ADR-0003, point 5, addendum R-01);
- `app.data.personnel_cost.month_has_cost_rate` over (`default_cost_rate`, `currency`) — cost
  (ADR-0013, point 1).

Extracted from `app.data.commercial_terms` in SC-5-01 so the cost path could use the same spelling
of "the month", "internal" and "the position's own snapshot" **without importing the revenue
module** (ADR-0004, addendum 2026-09-23 SC-5-01, point 3; rule 10 of the Invariant Guardian: cost
and revenue never import each other). A second, local copy in the cost module would have been the
other option, and it is the one that lets the two calculations drift apart by a clause — e.g. one
of them losing `vendor_id IS NULL` while the other keeps it.

Nothing here reads a rate column. Both importers stay independent of each other; this module
imports neither of them.
"""

from datetime import date

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import DATERANGE

from app.models.approved_snapshot import ApprovedSnapshotCatalogDefaultRate
from app.models.catalog import CatalogDefaultRate
from app.models.staffing import StaffingPosition, StaffingPositionAllocation


def whole_month(period_month: sa.ColumnElement[date]) -> sa.ColumnElement[object]:
    """`daterange(period_month, (period_month + interval '1 month')::date, '[)')` — one calendar
    month as a half-open range.

    Exact because `period_month` is the first day of its month by a CHECK in the database
    (`app.models.staffing.FIRST_DAY_OF_MONTH_EXPRESSION`), so the range is the calendar month and
    never "thirty days from some day". Half-open like PostgreSQL's canonical `daterange`, so no
    `- 1` appears anywhere.

    **This is not a second spelling of a rate window's boundary.** The window's own inclusive
    `effective_to` is converted exactly once, by the generated `valid_period` column (ADR-0008,
    point 3); this expression builds the *month* the window is asked about.
    """
    next_month = sa.cast(
        period_month + sa.literal_column("interval '1 month'", sa.Interval), sa.Date
    )
    return sa.func.daterange(
        period_month, next_month, sa.literal_column("'[)'"), type_=DATERANGE
    )


def shifted_calendar_month(
    period_month: sa.ColumnElement[date], months: int
) -> sa.ColumnElement[date]:
    """Shift a first-of-month date by whole calendar months."""
    if months < 0:
        raise ValueError("A delayed-start shift must be non-negative.")
    if months == 0:
        return period_month
    month_interval = sa.literal(months) * sa.literal_column("interval '1 month'", sa.Interval)
    return sa.cast(period_month + month_interval, sa.Date)


def shift_calendar_month_date(period_month: date, months: int) -> date:
    """Shift a first-of-month Python date by whole calendar months."""
    if months < 0:
        raise ValueError("A delayed-start shift must be non-negative.")
    month_index = period_month.year * 12 + period_month.month - 1 + months
    return date(month_index // 12, month_index % 12 + 1, 1)


def days_of(range_expression: sa.ColumnElement[object]) -> sa.ColumnElement[int]:
    """`upper(r) - lower(r)` — the number of days in a bounded, half-open date range.

    `NULL` for `NULL` (an outer-joined month with no window), which a caller's sum turns into `0`.
    Applied only to ranges intersected with a month, so it is always bounded.
    """
    return sa.type_coerce(
        sa.func.upper(range_expression) - sa.func.lower(range_expression), sa.Integer
    )


def days_covered_in_month(
    allocation_id: sa.ColumnElement[object],
    period_month: sa.ColumnElement[date],
    valid_period: sa.ColumnElement[object],
) -> sa.ColumnElement[bool]:
    """`true` when the windows overlapping one allocation's month together cover every day of it.

    A window function partitioned by the allocation: Σ days of (`valid_period` ∩ month) equals the
    days of the month. The sum is exact rather than approximate only because of the `EXCLUDE`
    constraint on `catalog_default_rates` — windows of one (tuple, vendor) key cannot overlap — and
    the frozen copies keep that property (one row per source window).

    A month with no overlapping window at all (the `LEFT JOIN` found nothing) has a `NULL` sum:
    `coalesce(…, 0)` turns it into "0 days covered", so the comparison is `false`, never `NULL`. A
    `NULL` must never be read as "resolved", and this is what guarantees it cannot arise.

    Half of each calculation's predicate; the other half — "one rate and one currency over the
    month" — is asked of each calculation's own rate column by its own module.
    """
    month = whole_month(period_month)
    covered = sa.func.coalesce(
        sa.func.sum(days_of(valid_period.op("*", return_type=DATERANGE)(month))).over(
            partition_by=allocation_id
        ),
        sa.literal(0, sa.Integer),
    )
    return covered == days_of(month)


def internal_catalog_windows_overlapping(
    period_month: sa.ColumnElement[date] | None = None,
) -> sa.ColumnElement[bool]:
    """The join condition "an internal catalogue window of the position's tuple overlapping the
    month".

    The four dimensions, `vendor_id IS NULL` — **internal, never "any vendor"** (ADR-0003, point 4;
    ADR-0008, addendum 2026-09-21, point 6; ADR-0013, point 1) — and `&&` with the month. Overlap
    here, not containment: *whether* the overlapping windows resolve the month is each
    calculation's own question.
    """
    rate = CatalogDefaultRate
    selected_month = (
        StaffingPositionAllocation.period_month if period_month is None else period_month
    )
    return sa.and_(
        rate.role_id == StaffingPosition.role_id,
        rate.seniority_id == StaffingPosition.seniority_id,
        rate.location_id == StaffingPosition.location_id,
        rate.engagement_type_id == StaffingPosition.engagement_type_id,
        rate.vendor_id.is_(None),
        rate.valid_period.bool_op("&&")(
            whole_month(selected_month)
        ),
    )


def frozen_windows_overlapping(
    period_month: sa.ColumnElement[date] | None = None,
) -> sa.ColumnElement[bool]:
    """The same condition asked of the approval snapshot of the position's own scenario.

    The same tuple, the same vendor axis, the same overlap with the month — against the snapshot's
    `valid_period`, generated from the same expression as the catalogue's — and **the position's own
    scenario's** frozen rows, never another approval's (`frozen.scenario_id ==
    position.scenario_id`).
    """
    frozen = ApprovedSnapshotCatalogDefaultRate
    selected_month = (
        StaffingPositionAllocation.period_month if period_month is None else period_month
    )
    return sa.and_(
        frozen.scenario_id == StaffingPosition.scenario_id,
        frozen.source_role_id == StaffingPosition.role_id,
        frozen.source_seniority_id == StaffingPosition.seniority_id,
        frozen.source_location_id == StaffingPosition.location_id,
        frozen.source_engagement_type_id == StaffingPosition.engagement_type_id,
        frozen.source_vendor_id.is_(None),
        frozen.valid_period.bool_op("&&")(
            whole_month(selected_month)
        ),
    )
