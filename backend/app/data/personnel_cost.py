"""The only path by which a scenario's base personnel cost is read (F-07, SC-5-01; ADR-0013).

Three mechanisms, none of them new — each is an existing mechanism of this repository applied to
the first calculation that reads `default_cost_rate`:

1. **Scope** — `app.data.staffing.scenario_view_in_scope`, i.e. `project_for_caller` plus membership
   of `Project.scenarios`, returning the scenario **together with the caller's project view**. The
   view carries `project_access.can_view_personnel_costs` for the project **this scenario belongs
   to**, resolved in the same statement that decided the caller may see it (ADR-0005, aneks
   2026-09-19, point 6; aneks 2026-09-23 SC-5-01, point 2). No scope function of its own, no
   `select(Scenario)` and no second read of `project_access` here: "no such project", "not yours"
   and "that scenario belongs to another project" are one `None` (criterion K-05).
2. **Cost-rate resolution by the whole month, in SQL, with one predicate for the live catalogue,
   the approval freeze and the snapshot reader** (ADR-0013, points 1 and 6; ADR-0004, aneks
   2026-09-23 SC-5-01, point 4). `month_has_cost_rate` is that predicate — "the internal windows
   overlapping the month cover every day of it and share one (`default_cost_rate`, `currency`)" —
   and `costed_month_windows` is the one statement shape that applies it. The approval's copier
   (`app.data.scenario_approval._copy_catalog_default_rates`) and the reader below both use it.
3. **The source is chosen by the scenario's status, and only by it**: a draft reads the live
   catalogue, an approved scenario reads its own snapshot and never the catalogue (AC-04, criterion
   K-07).

**Independent of the revenue path** (F-06; rule 10 of the Invariant Guardian; ADR-0013, point 1;
ADR-0004, aneks 2026-09-23 SC-5-01, point 3). This module never imports
`app.data.commercial_terms` or `app.domain.revenue*`, and neither of them imports it. What the two
share is geometry only — `app.data.rate_windows`: the month as a range, the day count, "the windows
cover the month", the two overlap joins. Whether the windows *resolve* the month is asked here of
the cost column and there of the selling column; a mid-month change of the selling rate therefore
does not uncost a month, and a mid-month change of the cost rate does not unprice one.

**What this module never reads: `default_selling_rate`**, from the catalogue or from the snapshot.

**What this module does not decide: who may see the figure.** The view carries the flag and the
caller's id; the conjunction `PERSONNEL_COSTS_READ` ∧ `can_view_personnel_costs` is applied by
`app.api.response_shaping` (ADR-0005, aneks 2026-09-23 SC-5-01, point 2), never here.
"""

import uuid
from dataclasses import dataclass
from datetime import date

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.core.identity import CallerIdentity
from app.data.paid_absence_cost import paid_absence_months
from app.data.rate_windows import (
    days_covered_in_month,
    frozen_windows_overlapping,
    internal_catalog_windows_overlapping,
)
from app.data.staffing import scenario_view_in_scope
from app.domain.paid_absence_cost import PaidAbsenceCostAnswer, paid_absence_cost
from app.domain.personnel_cost import (
    APPROVED_SNAPSHOT,
    LIVE_CATALOG,
    CostRateWindow,
    MonthCostRate,
    PersonnelCostAnswer,
    WorkedMonth,
    base_personnel_cost,
)
from app.models.approved_snapshot import ApprovedSnapshotCatalogDefaultRate
from app.models.catalog import CatalogDefaultRate
from app.models.scenario import Scenario, ScenarioStatus
from app.models.staffing import StaffingPosition, StaffingPositionAllocation

# --- the cost-rate predicate (ADR-0013, point 1) --------------------------------------------------
#
#     a (position, month) has a cost rate  <=>  the internal windows of its tuple overlapping the
#         month (a) together cover every day of it, and
#                (b) all carry the same (`default_cost_rate`, `currency`)
#
# and it is then costed at that one cost rate. A cost-rate change inside the month breaks (b), a
# currency change inside the month breaks (b), a gap breaks (a): all three are `no_cost_rate`. Where
# the *selling* boundaries fall no longer matters — the mirror of ADR-0003's aneks R-01.
#
# **One predicate, three callers** (ADR-0013, point 6; ADR-0004, aneks 2026-09-23 SC-5-01, point
# 4): the live read below, the approval's freeze and the snapshot reader below all build their
# statement with `costed_month_windows`, so what is frozen for the cost, what a draft reads and what
# an approved scenario reads cannot differ by a clause.


def month_has_cost_rate(
    allocation_id: sa.ColumnElement[uuid.UUID],
    period_month: sa.ColumnElement[date],
    valid_period: sa.ColumnElement[object],
    cost_rate: sa.ColumnElement[object],
    currency: sa.ColumnElement[str],
) -> sa.ColumnElement[bool]:
    """The cost predicate itself, as window functions over the windows overlapping one month.

    Evaluated per row of "(allocation, window overlapping its month)", partitioned by the
    allocation, so every row of one month carries the same answer:

    - **covered** — `app.data.rate_windows.days_covered_in_month` (never `NULL`: a month with no
      window is "0 days covered", so `false`, and `false AND NULL` is `false`);
    - **one cost rate** — `min = max` of the cost rate and of the currency across the month's
      windows.

    Not built from `app.data.commercial_terms.month_is_priced` and never reading the selling rate:
    a separate function in a separate module (ADR-0013, point 1).
    """
    covered = days_covered_in_month(allocation_id, period_month, valid_period)
    one_cost_rate = sa.and_(
        sa.func.min(cost_rate).over(partition_by=allocation_id)
        == sa.func.max(cost_rate).over(partition_by=allocation_id),
        sa.func.min(currency).over(partition_by=allocation_id)
        == sa.func.max(currency).over(partition_by=allocation_id),
    )
    return sa.and_(covered, one_cost_rate)


def costed_month_windows(*, from_snapshot: bool, scenario_id: uuid.UUID) -> sa.Select:
    """Every (allocation, overlapping window) row of one scenario, each carrying
    `month_has_cost_rate`.

    Columns: `scenario_id`, `position_id`, `allocation_id`, `period_month`,
    `planned_allocation_hours`, `window_id` (the catalogue row's id — on the snapshot, the frozen
    `source_rate_id`), `effective_from`, `effective_to`, `cost_rate`, `currency`,
    `month_has_cost_rate`.

    A `LEFT JOIN`, so a month with no overlapping window still yields one row (window columns
    `NULL`, `month_has_cost_rate` false) — which is what makes `no_cost_rate` a value the formula
    sees rather than a row that silently went missing from an inner join (criterion K-03).

    **The cost rate only — never `default_selling_rate`**, from the catalogue and from the snapshot
    alike. **`planned_allocation_hours` only** — not `billable_hours`, not the availability, not the
    headcount (ADR-0013, points 3 and 4).

    Callers filter on `month_has_cost_rate` **outside** this select (as a subquery), never inside
    it: a `WHERE` here would run before the window functions and change the partitions they see.
    """
    if from_snapshot:
        window = ApprovedSnapshotCatalogDefaultRate
        window_id = window.source_rate_id
        # The scenario's **own** frozen rows, re-asked the same predicate per month rather than
        # trusted as "whatever was frozen" (ADR-0004, aneks 2026-09-23 SC-5-01, point 4).
        condition = frozen_windows_overlapping()
    else:
        window = CatalogDefaultRate
        window_id = window.id
        condition = internal_catalog_windows_overlapping()
    return (
        sa.select(
            StaffingPosition.scenario_id.label("scenario_id"),
            StaffingPosition.id.label("position_id"),
            StaffingPositionAllocation.id.label("allocation_id"),
            StaffingPositionAllocation.period_month.label("period_month"),
            StaffingPositionAllocation.planned_allocation_hours.label("planned_allocation_hours"),
            window_id.label("window_id"),
            window.effective_from.label("effective_from"),
            window.effective_to.label("effective_to"),
            window.default_cost_rate.label("cost_rate"),
            window.currency.label("currency"),
            month_has_cost_rate(
                StaffingPositionAllocation.id,
                StaffingPositionAllocation.period_month,
                window.valid_period,
                window.default_cost_rate,
                window.currency,
            ).label("month_has_cost_rate"),
        )
        .select_from(StaffingPosition)
        .join(
            StaffingPositionAllocation,
            StaffingPositionAllocation.position_id == StaffingPosition.id,
        )
        .outerjoin(window, condition)
        .where(StaffingPosition.scenario_id == scenario_id)
    )


# --- reading the worked months of a scenario, costed ----------------------------------------------


def _worked_months(session: Session, scenario: Scenario) -> tuple[str, list[WorkedMonth]]:
    """Every allocation row of the scenario with its cost rate — live catalogue or snapshot.

    Python only groups the rows of one month together and reads the database's answer
    (`month_has_cost_rate`); it compares no date and no rate. Every row of a costed month carries
    the same cost rate and currency — that equality is part of the predicate.
    """
    approved = scenario.status == ScenarioStatus.APPROVED
    source = APPROVED_SNAPSHOT if approved else LIVE_CATALOG
    rows = costed_month_windows(from_snapshot=approved, scenario_id=scenario.id).subquery(
        "costed_month_windows"
    )
    statement = sa.select(rows).order_by(
        rows.c.period_month, rows.c.position_id, rows.c.effective_from, rows.c.window_id
    )
    grouped: dict[uuid.UUID, list[sa.Row]] = {}
    for row in session.execute(statement).all():
        grouped.setdefault(row.allocation_id, []).append(row)
    months = []
    for month_rows in grouped.values():
        first = month_rows[0]
        rate = None
        if first.month_has_cost_rate:
            rate = MonthCostRate(
                cost_rate=first.cost_rate,
                currency=first.currency,
                windows=tuple(
                    CostRateWindow(
                        source_rate_id=row.window_id,
                        effective_from=row.effective_from,
                        effective_to=row.effective_to,
                        cost_rate=row.cost_rate,
                        currency=row.currency,
                    )
                    for row in month_rows
                ),
            )
        months.append(
            WorkedMonth(
                position_id=first.position_id,
                period_month=first.period_month,
                planned_allocation_hours=first.planned_allocation_hours,
                rate=rate,
            )
        )
    return source, months


@dataclass(frozen=True)
class ScenarioCostView:
    """One scenario's base personnel cost **as one caller may see it**, resolved in one read.

    A value object for the reason `CallerProjectView` is one: the shaping layer never receives a
    `Session` and must never grow a query of its own, so the per-(caller, project) flag has to
    arrive from here, from the same scope read that returned the scenario.

    - `user_id` names the caller the flag was resolved for, so the shaping layer can refuse to shape
      this view for anybody else (the `_without_personnel_costs` assertion, applied again).
    - `can_view_personnel_costs` is the caller's `project_access` flag **for the project this
      scenario belongs to** — never "any of the caller's assignments" (criterion K-04).
    - `cost` is the full answer, amount and rates included. It is **not** safe to serialise as it
      stands: the conjunction is applied by `app.api.response_shaping`.
    """

    user_id: str
    scenario: Scenario
    can_view_personnel_costs: bool
    cost: PersonnelCostAnswer
    paid_absence: PaidAbsenceCostAnswer
    """The paid-absence component (SC-5-06; ADR-0013, aneks 2026-09-23 SC-5-06) — beside `cost`,
    never added to it. Carried on the same view so the same conjunction gates it (point 6)."""
    status_at_read: ScenarioStatus
    """The scenario's status **as this read saw it** — the status that chose live catalogue versus
    snapshot for `cost` — copied into an immutable value right after this read's own
    `session.refresh` (SC-7-03, Issue #118; ADR-0015, aneks SC-7-03, point 3). Never
    `scenario.status` read later: `scenario` is an identity-mapped object another read in the same
    session may refresh again. Compared against `app.data.commercial_terms.ScenarioCommercialView.
    status_at_read` by the race guard in `app.data.scenario_results`/`app.data.scenario_what_if`.
    Not a personnel-cost figure and never serialised: `app.api.response_shaping` does not read
    it."""


def scenario_cost_for_caller(
    session: Session, caller: CallerIdentity, project_id: uuid.UUID, scenario_id: uuid.UUID
) -> ScenarioCostView | None:
    """The base personnel cost of one scenario — or `None`, with no way to tell why (K-05).

    `None` is "no such scenario *for this caller*"; a scenario whose cost cannot be stated is a view
    whose cost is a named state, never `None` and never `0`.
    """
    in_scope = scenario_view_in_scope(session, caller, project_id, scenario_id)
    if in_scope is None:
        return None
    project_view, scenario = in_scope
    # Refreshed, not trusted from the identity map: the status decides live-versus-snapshot, and an
    # object loaded earlier in the same session may predate an approval committed since.
    session.refresh(scenario)
    status_at_read = scenario.status
    source, months = _worked_months(session, scenario)
    # The paid-absence component is costed at **these** rates — the base cost's resolution of each
    # (position, month), live or frozen by the same status — and asks no predicate of its own
    # (ADR-0013, aneks 2026-09-23 SC-5-06, point 3). Its hours are read from a different source,
    # and its amount is never added to the base amount (point 4).
    rates = {(month.position_id, month.period_month): month.rate for month in months}
    return ScenarioCostView(
        user_id=project_view.user_id,
        scenario=scenario,
        can_view_personnel_costs=project_view.can_view_personnel_costs,
        cost=base_personnel_cost(
            months, rate_source=source, scenario_currency=scenario.currency
        ),
        paid_absence=paid_absence_cost(
            paid_absence_months(session, scenario, rates),
            scenario_currency=scenario.currency,
        ),
        status_at_read=status_at_read,
    )
