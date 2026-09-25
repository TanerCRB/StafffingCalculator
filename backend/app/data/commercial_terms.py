"""The only path by which a scenario's commercial rule is read, written, copied and priced.

SC-4-01 (F-06.1, Issue #8), extended by SC-4-04 (F-06.4, Issue #68) — the second real model
(Story Points) registered in `REVENUE_BY_MODEL`/`DETAIL_TABLE_BY_MODEL` below, proving every
mechanism in this module generalises rather than being a property of the one model it shipped with.

Four mechanisms, none of them new — each is an existing mechanism of this repository applied to the
first table of plan block 4 (ADR-0003; ADR-0004, ADR-0005 and ADR-0008, addenda 2026-09-23 SC-4-01):

1. **Scope** — `app.data.staffing.scenario_in_scope`, i.e. `project_for_caller` plus membership of
   `Project.scenarios`. No scope function of its own and no `select(Scenario)` here: "no such
   project", "not yours" and "that scenario belongs to another project" are one `None`, for the read
   and for the write alike (ADR-0005, addendum SC-4-01, point 1; criterion K-05).
2. **The refusal of a write to an `approved` scenario, in the statement that writes** (ADR-0004,
   addendum SC-4-01, point 1a) — `app.data.scenario_guard.unapproved_scenario` embedded in the
   `INSERT … SELECT`, which also takes the scenario row lock that serialises the write against a
   concurrent approval (criterion K-06). One statement creates the rule **and** its details row
   (ADR-0003, point 3), so there is no second statement for the guard to be missing from.
3. **One copier for the aggregate** (`copy_commercial_terms`, one entry in
   `app.data.project_writes.SCENARIO_CHILD_COPIERS` — addendum SC-4-01, point 1b; criterion K-07).
4. **Rate resolution by the whole month, in SQL, with one predicate for the live catalogue, the
   approval freeze and the snapshot reader** (ADR-0003, point 5; ADR-0004, addendum SC-4-01, points
   2c and 2e). `month_is_priced` is that predicate — "the month's overlapping windows cover every
   day of it and share one selling rate and currency" (corrected at gate 2, R-01; the section below
   says why it is not "one window contains the month") — and `priced_month_windows` is the one
   statement shape that applies it. The approval's copier
   (`app.data.scenario_approval._copy_catalog_default_rates`) and the two readers below all use it,
   so the set of windows frozen is by construction the set the live read was pricing with.

**The revenue dispatcher chooses by `model_type` and by nothing else** (ADR-0003, point 9):
`REVENUE_BY_MODEL`. A model is added by a details table, a value of the discriminator CHECK and one
entry here — never by inspecting which rows happen to exist.

**What this module never reads: `default_cost_rate`** (ADR-0003, point 4; rule 10 of the Invariant
Guardian). Both readers select the selling rate, the currency and the window — from the catalogue
and from the snapshot alike, although the snapshot does hold the cost (ADR-0004, addendum SC-4-01,
point 2b).
"""

import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date

import sqlalchemy as sa
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.identity import CallerIdentity
from app.data.column_copy import values_to_copy
from app.data.rate_windows import (
    days_covered_in_month,
    frozen_windows_overlapping,
    internal_catalog_windows_overlapping,
)
from app.data.scenario_guard import unapproved_scenario
from app.data.staffing import scenario_in_scope
from app.data.write_errors import WriteFailed, WriteRefused, failure_for
from app.domain.revenue import (
    APPROVED_SNAPSHOT,
    INCOMPLETE_COMMERCIAL_TERMS,
    LIVE_CATALOG,
    NO_COMMERCIAL_TERMS,
    RATE_SOURCE_STORY_POINTS_TERMS,
    UNSUPPORTED_MODEL_TYPE,
    AssumptionsUsed,
    MonthPrice,
    RateWindow,
    RevenueAnswer,
    RevenueUnavailable,
)
from app.domain.revenue_story_points import story_points_revenue
from app.domain.revenue_time_and_material import BillableMonth, time_and_material_revenue
from app.models.approved_snapshot import ApprovedSnapshotCatalogDefaultRate
from app.models.catalog import CatalogDefaultRate
from app.models.commercial_terms import (
    MODEL_TYPE_STORY_POINTS,
    MODEL_TYPE_TIME_AND_MATERIAL,
    CommercialTerms,
    StoryPointsTerms,
    TmTerms,
)
from app.models.scenario import Scenario, ScenarioStatus
from app.models.staffing import StaffingPosition, StaffingPositionAllocation

_TERMS_TABLE = CommercialTerms.__table__

# --- the month-pricing predicate (ADR-0003, point 5; corrected at gate 2 of SC-4-01, R-01) --------
#
# **What "priced" means, and why it is not "one window contains the month".** ADR-0003 point 5
# is about a change of the **selling** rate inside a month: that month must not be priced with
# either of the two rates. The first implementation asked for a *single* catalogue window
# containing the whole month, which is stricter than that: a catalogue window carries the cost
# rate too, so a mid-month change of the **cost** alone (selling rate untouched) split the month
# into two windows and unpriced its revenue — a revenue blocked by a figure the revenue never
# reads (reviewer R-01, Medium).
#
# The rule now, decided by the human at gate 2 and applied identically everywhere it is asked:
#
#     a (position, month) is priced  <=>  the internal windows of its tuple overlapping the month
#         (a) together cover every day of it, and
#         (b) all carry the same (`default_selling_rate`, `currency`)
#
# and it is then priced at that one selling rate. A selling-rate change inside the month breaks (b)
# and a gap breaks (a): both are `no_rate`, as before. Where the cost boundaries fall, and how many
# windows there are, no longer matters.
#
# (a) is a **sum of day counts**, and that is exact rather than approximate only because of the
# `EXCLUDE` constraint on `catalog_default_rates`: windows of one (tuple, vendor) key cannot
# overlap, so the days they share with the month add up without double counting. The frozen
# copies keep that property (one row per source window — `_deduplicated_with_new_ids`).
#
# **One predicate, three callers** (ADR-0003 point 5, ADR-0004 addendum SC-4-01 points 2c/2e): the
# live read below, the approval's freeze (`app.data.scenario_approval._copy_catalog_default_rates`)
# and the snapshot reader below all build their statement with `priced_month_windows`, so what is
# frozen, what a draft reads and what an approved scenario reads cannot differ by a clause.


# **The month geometry lives in `app.data.rate_windows` since SC-5-01** — `whole_month`, the day
# count, "the windows overlapping the month cover it" and the two overlap joins (live catalogue and
# the scenario's own snapshot). Moved, not copied: the cost path (`app.data.personnel_cost`) needs
# the same spelling of "the month", "internal" and "this scenario's snapshot", and may not import
# this module to get it (ADR-0004, aneks 2026-09-23 SC-5-01, point 3; rule 10 of the Invariant
# Guardian). What stays here is the revenue's own half of the predicate: one *selling* rate.


def month_is_priced(
    allocation_id: sa.ColumnElement[uuid.UUID],
    period_month: sa.ColumnElement[date],
    valid_period: sa.ColumnElement[object],
    selling_rate: sa.ColumnElement[object],
    currency: sa.ColumnElement[str],
) -> sa.ColumnElement[bool]:
    """The predicate itself, as window functions over the windows overlapping one month.

    Evaluated per row of "(allocation, window overlapping its month)", partitioned by the
    allocation, so every row of one month carries the same answer:

    - **covered** — Σ days of (`valid_period` ∩ month) equals the days of the month;
    - **one price** — `min = max` of the selling rate and of the currency across the month's
      windows.

    The result is never `NULL`, and the one place that makes it so is the `coalesce` inside
    `app.data.rate_windows.days_covered_in_month` — see the comment below.
    """
    # A month with no overlapping window at all (the `LEFT JOIN` found nothing) is "0 days covered",
    # so `covered` is `false`, never `NULL`. That `false` also decides the conjunction below,
    # although `one_price` is `NULL` for such a month (`min`/`max` over nothing): `false AND NULL`
    # is `false` in SQL. A `NULL` must never be read as "priced".
    covered = days_covered_in_month(allocation_id, period_month, valid_period)
    one_price = sa.and_(
        sa.func.min(selling_rate).over(partition_by=allocation_id)
        == sa.func.max(selling_rate).over(partition_by=allocation_id),
        sa.func.min(currency).over(partition_by=allocation_id)
        == sa.func.max(currency).over(partition_by=allocation_id),
    )
    return sa.and_(covered, one_price)


def priced_month_windows(*, from_snapshot: bool, scenario_id: uuid.UUID) -> sa.Select:
    """Every (allocation, overlapping window) row of one scenario, each carrying `month_is_priced`.

    Columns: `scenario_id`, `position_id`, `allocation_id`, `period_month`, `billable_hours`,
    `window_id` (the catalogue row's id — on the snapshot, the frozen `source_rate_id`),
    `effective_from`, `effective_to`, `selling_rate`, `currency`, `month_is_priced`.

    A `LEFT JOIN`, so a month with no overlapping window still yields one row (window columns
    `NULL`, `month_is_priced` false) — which is what makes `no_rate` a value the formula sees rather
    than a row that silently went missing from an inner join (criterion K-10).

    **The selling rate only — never `default_cost_rate`** (ADR-0003, point 4), from the catalogue
    and from the snapshot alike, although the snapshot does hold the cost (ADR-0004, addendum
    SC-4-01, point 2b).

    Callers filter on `month_is_priced` **outside** this select (as a subquery), never inside it: a
    `WHERE` here would run before the window functions and change the partitions they see.
    """
    if from_snapshot:
        window = ApprovedSnapshotCatalogDefaultRate
        window_id = window.source_rate_id
        # Resolving per month on the frozen rows, with the same `month_is_priced`, rather than
        # trusting "whatever was frozen" is what point 2e of the addendum requires (K-09).
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
            StaffingPositionAllocation.billable_hours.label("billable_hours"),
            window_id.label("window_id"),
            window.effective_from.label("effective_from"),
            window.effective_to.label("effective_to"),
            window.default_selling_rate.label("selling_rate"),
            window.currency.label("currency"),
            month_is_priced(
                StaffingPositionAllocation.id,
                StaffingPositionAllocation.period_month,
                window.valid_period,
                window.default_selling_rate,
                window.currency,
            ).label("month_is_priced"),
        )
        .select_from(StaffingPosition)
        .join(
            StaffingPositionAllocation,
            StaffingPositionAllocation.position_id == StaffingPosition.id,
        )
        .outerjoin(window, condition)
        .where(StaffingPosition.scenario_id == scenario_id)
    )


# --- reading the billable months of a scenario, priced -------------------------------------------


def _billable_months(session: Session, scenario: Scenario) -> tuple[str, list[BillableMonth]]:
    """Every allocation row of the scenario with its price — live catalogue or snapshot.

    **The source is chosen by the scenario's status, and only by it** (ADR-0003, point 10): a draft
    reads the live catalogue, an approved scenario reads its snapshot and never the catalogue — so
    editing a rate after approval moves nothing (criterion K-09). The hours come from the scenario's
    own rows in both cases; they are frozen by the write guard, not by the snapshot (ADR-0004,
    addendum 2026-09-19).

    Python only groups the rows of one month together and reads the database's answer
    (`month_is_priced`); it compares no date and no rate. Every row of a priced month carries the
    same selling rate and currency — that equality is part of the predicate.
    """
    approved = scenario.status == ScenarioStatus.APPROVED
    source = APPROVED_SNAPSHOT if approved else LIVE_CATALOG
    rows = priced_month_windows(from_snapshot=approved, scenario_id=scenario.id).subquery(
        "priced_month_windows"
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
        price = None
        if first.month_is_priced:
            price = MonthPrice(
                selling_rate=first.selling_rate,
                currency=first.currency,
                windows=tuple(
                    RateWindow(
                        source_rate_id=row.window_id,
                        effective_from=row.effective_from,
                        effective_to=row.effective_to,
                        selling_rate=row.selling_rate,
                        currency=row.currency,
                    )
                    for row in month_rows
                ),
            )
        months.append(
            BillableMonth(
                position_id=first.position_id,
                period_month=first.period_month,
                billable_hours=first.billable_hours,
                price=price,
            )
        )
    return source, months


# --- the dispatcher (ADR-0003, point 9) ----------------------------------------------------------


@dataclass(frozen=True)
class _Rule:
    """The rule of one scenario as the dispatcher needs it: which model, and is the details row
    there."""

    terms: CommercialTerms
    has_details: bool


RevenueCalculator = Callable[[Session, Scenario, _Rule], RevenueAnswer]


def _time_and_material(session: Session, scenario: Scenario, rule: _Rule) -> RevenueAnswer:
    """T&M: a rule without its `tm_terms` row is `incomplete_commercial_terms`, never priced."""
    source, months = _billable_months(session, scenario)
    if not rule.has_details:
        return RevenueUnavailable(
            reason=INCOMPLETE_COMMERCIAL_TERMS,
            assumptions_used=AssumptionsUsed(
                model_type=rule.terms.model_type, rate_source=source
            ),
        )
    return time_and_material_revenue(
        months, rate_source=source, scenario_currency=scenario.currency
    )


def _story_points(session: Session, scenario: Scenario, rule: _Rule) -> RevenueAnswer:
    """Story Points (F-06.4, SC-4-04): a rule without its `story_points_terms` row is
    `incomplete_commercial_terms`, never priced — the same named state T&M uses for the same reason
    (ADR-0003, point 3).

    **Reads no staffing table** (criterion K-02): unlike `_time_and_material`, this branch never
    calls `_billable_months` and never touches `StaffingPosition`/`StaffingPositionAllocation` — the
    price is entirely the rule's own row, so a scenario's billable-hours plan can change arbitrarily
    without moving this revenue by a cent. `assumptions_used` names that explicitly
    (`HOURS_SOURCE_NOT_APPLICABLE`), not merely by omission.
    """
    if not rule.has_details:
        return RevenueUnavailable(
            reason=INCOMPLETE_COMMERCIAL_TERMS,
            assumptions_used=AssumptionsUsed(
                model_type=rule.terms.model_type, rate_source=RATE_SOURCE_STORY_POINTS_TERMS
            ),
        )
    details = session.execute(
        sa.select(StoryPointsTerms).where(
            StoryPointsTerms.commercial_terms_id == rule.terms.id
        )
    ).scalar_one()
    return story_points_revenue(
        price_per_point=details.price_per_point,
        accepted_points=details.accepted_points,
        currency=details.currency,
        scenario_currency=scenario.currency,
    )


REVENUE_BY_MODEL: dict[str, RevenueCalculator] = {
    MODEL_TYPE_TIME_AND_MATERIAL: _time_and_material,
    MODEL_TYPE_STORY_POINTS: _story_points,
}
"""`model_type` → the function that prices it. Chosen by the discriminator alone, never by the shape
of the data (ADR-0003, point 9). Keys must equal `app.models.commercial_terms.MODEL_TYPES`, and a
test asserts it: a model the CHECK admits and this map does not know would be a rule nobody can
price."""

DETAIL_TABLE_BY_MODEL: dict[str, sa.Table] = {
    MODEL_TYPE_TIME_AND_MATERIAL: TmTerms.__table__,
    MODEL_TYPE_STORY_POINTS: StoryPointsTerms.__table__,
}
"""`model_type` → its details table, for the write path that creates both rows in one statement."""


def _rule_of(session: Session, scenario_id: uuid.UUID) -> _Rule | None:
    """The rule of one (already in-scope) scenario and whether its details row exists.

    The details row is looked up in **the details table of the rule's own model**
    (`DETAIL_TABLE_BY_MODEL`), never in `tm_terms` by name (R-05, SC-4-01 gate 2): the day a second
    model joins the registry, a hard-coded `tm_terms` lookup would call every rule of that model
    incomplete. A model this version does not know has no table to look in — `has_details` is then
    `False`, and the dispatcher answers `unsupported_model_type` before it reads it.
    """
    terms = session.execute(
        sa.select(CommercialTerms).where(CommercialTerms.scenario_id == scenario_id)
    ).scalar_one_or_none()
    if terms is None:
        return None
    detail_table = DETAIL_TABLE_BY_MODEL.get(terms.model_type)
    has_details = detail_table is not None and session.execute(
        sa.select(sa.exists().where(detail_table.c.commercial_terms_id == terms.id))
    ).scalar_one()
    return _Rule(terms=terms, has_details=has_details)


def revenue_of(session: Session, scenario: Scenario, rule: _Rule | None) -> RevenueAnswer:
    """Price one scenario — or name why it cannot be priced. Never `0` for a missing input.

    `scenario` must be one `scenario_in_scope` returned: this function decides no access.

    **A model this version of the code does not know is a named state, not a `KeyError`** (R-02,
    gate 2). The discriminator CHECK admits `time_and_material` and `story_points` today (SC-4-04
    widened it, ADR-0003 addendum 2026-09-25), and this branch stays reachable for whichever value
    a future migration adds next, in the mixed-version window of expand → deploy → contract
    (ADR-0001): a later model's migration widens the CHECK before every instance runs the code that
    knows its table. Defensive only: nothing here prices another model.
    """
    source = APPROVED_SNAPSHOT if scenario.status == ScenarioStatus.APPROVED else LIVE_CATALOG
    if rule is None:
        return RevenueUnavailable(
            reason=NO_COMMERCIAL_TERMS,
            assumptions_used=AssumptionsUsed(model_type=None, rate_source=source),
        )
    calculator = REVENUE_BY_MODEL.get(rule.terms.model_type)
    if calculator is None:
        return RevenueUnavailable(
            reason=UNSUPPORTED_MODEL_TYPE,
            assumptions_used=AssumptionsUsed(
                model_type=rule.terms.model_type, rate_source=source
            ),
        )
    return calculator(session, scenario, rule)


@dataclass(frozen=True)
class ScenarioCommercialView:
    """One scenario's rule and the revenue derived from it, resolved in one read.

    A value object, for the reason `StaffingPositionView` is one: the shaping layer never receives a
    `Session`. **It carries no per-caller flag, and that absence is the statement** — nothing here
    is gated on `PERSONNEL_COSTS_READ` (ADR-0005, addendum 2026-09-23 SC-4-01, point 3): a revenue
    and a selling rate are not what a person costs. The day a cost, profit or margin joins this
    view, it grows the flag and the SC-1-08 conjunction.
    """

    scenario: Scenario
    terms: CommercialTerms | None
    revenue: RevenueAnswer
    status_at_read: ScenarioStatus
    """The scenario's status **as this read saw it**, copied into an immutable value right after
    this read's own `session.refresh` (SC-7-03, Issue #118; ADR-0015, aneks SC-7-03, point 3).

    Never `scenario.status` read later: `scenario` is an identity-mapped object that a later read
    in the same session (`app.data.personnel_cost.scenario_cost_for_caller`) refreshes again, so its
    `.status` stops being a record of *this* read the moment that call runs. This value does not
    move. It is what `app.data.scenario_results`/`app.data.scenario_what_if` compare to detect an
    approval landing between the revenue and the cost read — never `revenue.assumptions_used.
    rate_source`, whose vocabulary depends on the commercial model (`story_points_terms` has no
    status in it at all; ADR-0003, aneks SC-7-03)."""


def _view_of(session: Session, scenario: Scenario) -> ScenarioCommercialView:
    # Refreshed, not trusted from the identity map: the status decides live-versus-snapshot, and an
    # object loaded earlier in the same session may predate an approval committed since.
    session.refresh(scenario)
    status_at_read = scenario.status
    rule = _rule_of(session, scenario.id)
    return ScenarioCommercialView(
        scenario=scenario,
        terms=None if rule is None else rule.terms,
        revenue=revenue_of(session, scenario, rule),
        status_at_read=status_at_read,
    )


def commercial_terms_for_caller(
    session: Session, caller: CallerIdentity, project_id: uuid.UUID, scenario_id: uuid.UUID
) -> ScenarioCommercialView | None:
    """The rule and revenue of one scenario — or `None`, with no way to tell why (criterion K-05).

    `None` is "no such scenario *for this caller*"; a scenario with no rule is a view whose revenue
    is the named `no_commercial_terms` state, never `None` and never `0`.
    """
    scenario = scenario_in_scope(session, caller, project_id, scenario_id)
    if scenario is None:
        return None
    return _view_of(session, scenario)


# --- writing the rule (ADR-0003, point 3; ADR-0004, addendum SC-4-01, point 1a) ------------------


class CommercialTermsWriteFailed(WriteFailed):
    """A rule write failed for a reason nothing here established — a `500` (R-01)."""


class CommercialTermsWriteRefused(CommercialTermsWriteFailed, WriteRefused):
    """The database refused the write for a reason its SQLSTATE names — a `409`.

    The case a caller meets in practice: a second rule for one scenario, refused by
    `uq_commercial_terms_scenario_id` inside the `INSERT` (two connections included).
    """


class CommercialTermsWriteRejected(RuntimeError):
    """The write was understood and refused *by state* — a `409`, distinct from a broken write."""


class CommercialTermsFrozen(CommercialTermsWriteRejected):
    """The scenario is `approved`, so its commercial rule is part of an approved calculation.

    Permanent: the way forward is a copy of the scenario, which is a `draft` (ADR-0004).
    """


class CommercialTermsScenarioChanged(CommercialTermsWriteRejected):
    """The scenario was in scope a moment ago and no unapproved row matched — it changed since."""


def _failure(error: SQLAlchemyError) -> WriteFailed:
    return failure_for(
        error,
        subject="commercial terms",
        refused=CommercialTermsWriteRefused,
        failed=CommercialTermsWriteFailed,
    )


def create_commercial_terms(
    session: Session,
    caller: CallerIdentity,
    project_id: uuid.UUID,
    scenario_id: uuid.UUID,
    *,
    model_type: str,
    domain_values: Mapping[str, object] | None = None,
) -> ScenarioCommercialView | None:
    """Create the rule of one scenario **and** its details row, in one guarded statement.

    ```
    WITH new_commercial_terms AS (
        INSERT INTO commercial_terms (id, scenario_id, model_type)
        SELECT :id, open_scenario.id, :model_type
          FROM (SELECT id FROM scenarios
                 WHERE id = :scenario_id AND status <> 'approved' FOR UPDATE) AS open_scenario
        RETURNING id, model_type
    )
    INSERT INTO tm_terms (commercial_terms_id, model_type)
    SELECT id, model_type FROM new_commercial_terms
    RETURNING commercial_terms_id
    ```

    (`story_points_terms` widens the second `INSERT`'s column list with `domain_values` — see below;
    the shape above is unchanged for a model with none, which is why passing no `domain_values` for
    T&M is not a second, different statement.)

    - **The `approved` refusal and the lock are inside the statement that writes** (ADR-0004,
      addendum SC-4-01, point 1a; `app.data.scenario_guard`). No parent row → no rule → no details
      row → zero rows returned, diagnosed only afterwards. A Python status check before the insert
      is the mutation criterion K-06's race kills.
    - **Both rows in one statement** (ADR-0003, point 3): the details row cannot be forgotten by a
      second statement that failed or was never written, and it carries the rule's own
      `model_type` from the `RETURNING`, so the composite foreign key is satisfied by construction
      rather than by a second literal that could disagree with the first.
    - **A second rule for the scenario** is refused by `uq_commercial_terms_scenario_id`, i.e. by
      the database inside the `INSERT` — a `409` through `app.data.write_errors`, never a `SELECT`
      before it.
    - **`domain_values` (SC-4-04, ADR-0003 addendum 2026-09-25, D-6/A)** — a model's own fields
      (Story Points: `price_per_point`, `accepted_points`, `currency`), carried as literals of the
      detail table's own column type into the *same* guarded `INSERT … SELECT`, never a second,
      unguarded statement. Empty for a model with no domain column (T&M today), so this path is the
      one T&M already shipped, not a new one next to it.

    `None` means "no such scenario for this caller" and is decided before the guard, so a `409` can
    never confirm that a scenario outside the caller's scope exists (K-05).
    """
    if scenario_in_scope(session, caller, project_id, scenario_id) is None:
        return None

    detail_table = DETAIL_TABLE_BY_MODEL[model_type]
    domain_values = domain_values or {}
    open_scenario = unapproved_scenario(scenario_id).subquery("open_scenario")
    new_terms = (
        sa.insert(_TERMS_TABLE)
        .from_select(
            ["id", "scenario_id", "model_type"],
            sa.select(
                sa.literal(uuid.uuid4(), type_=_TERMS_TABLE.c.id.type).label("id"),
                open_scenario.c.id.label("scenario_id"),
                sa.literal(model_type, type_=_TERMS_TABLE.c.model_type.type).label(
                    "model_type"
                ),
            ).select_from(open_scenario),
        )
        .returning(_TERMS_TABLE.c.id, _TERMS_TABLE.c.model_type)
        .cte("new_commercial_terms")
    )
    detail_columns = ["commercial_terms_id", "model_type", *domain_values]
    detail_select_columns = [new_terms.c.id, new_terms.c.model_type, *(
        sa.literal(value, type_=detail_table.c[column].type).label(column)
        for column, value in domain_values.items()
    )]
    statement = (
        sa.insert(detail_table)
        .add_cte(new_terms)
        .from_select(detail_columns, sa.select(*detail_select_columns))
        .returning(detail_table.c.commercial_terms_id)
    )

    try:
        inserted = session.execute(statement).one_or_none()
        if inserted is None:
            # Nothing was written, so nothing is rolled back — the same reasoning as
            # `app.data.staffing.create_position`.
            raise _diagnose_refusal(session, scenario_id)
        session.commit()
    except SQLAlchemyError as error:
        session.rollback()
        # `from None`: PostgreSQL's `DETAIL: Failing row contains (…)` must not ride along (NF-11).
        raise _failure(error) from None
    scenario = scenario_in_scope(session, caller, project_id, scenario_id)
    if scenario is None:  # pragma: no cover — the scenario was in scope a statement ago
        return None
    return _view_of(session, scenario)


def _diagnose_refusal(session: Session, scenario_id: uuid.UUID) -> CommercialTermsWriteRejected:
    """Name the reason the guarded insert found no parent — after the refusal, never as the
    guard."""
    approved = session.execute(
        sa.select(
            sa.exists().where(
                Scenario.id == scenario_id, Scenario.status == ScenarioStatus.APPROVED
            )
        )
    ).scalar_one()
    if approved:
        return CommercialTermsFrozen(
            "This scenario is approved, so its commercial terms are part of an approved "
            "calculation and cannot be changed. Copy the scenario to open a new version and change "
            "the copy."
        )
    return CommercialTermsScenarioChanged(
        "The scenario changed since it was read. Re-read it and apply the change again."
    )


# --- the copying cascade (ADR-0004, addendum 2026-09-23 SC-4-01, point 1b) -----------------------

COMMERCIAL_TERMS_COLUMNS_NOT_COPIED: frozenset[str] = frozenset(
    {"id", "scenario_id", "created_at", "updated_at"}
)
"""Rule attributes a copy does not inherit: a new row (`id`), the copy's own scenario
(`scenario_id`), and its own timestamps — the ADR-0007 marker of the copy is its own, never the
source's. Everything else (today: `model_type`) is copied by reflection, and a drift guard asserts
every mapped attribute is on one side or the other."""

TM_TERMS_COLUMNS_NOT_COPIED: frozenset[str] = frozenset({"commercial_terms_id", "created_at"})
"""The same for the details row — of `tm_terms` today, and the convention every details table
follows (the key is the rule's id, `created_at` is the copy's own); the copier applies it to the
table `DETAIL_TABLE_BY_MODEL` names for the rule's model. `commercial_terms_id` is the one value
that cannot be reflected: it is the *copied* rule's id, which is why the details row has no
registry entry of its own."""


class CommercialTermsNotCopyable(RuntimeError):
    """The source scenario's rule names a model this version of the code cannot copy (R-03).

    Raised instead of copying the rule without its details row, which would leave the copy with a
    permanent `incomplete_commercial_terms` rule and no path to repair it. The whole copy is refused
    — `app.data.project_writes.copy_project` rolls back and the API answers `409` — because a
    half-copied project is worse than a failed copy. The message names the model and nothing else.
    """


def copy_commercial_terms(session: Session, source: Scenario, copy: Scenario) -> None:
    """Copy one scenario's rule **and its details row** onto the copy, with new identifiers.

    One entry in `SCENARIO_CHILD_COPIERS` for the aggregate, not one per table (ADR-0004, addendum
    2026-09-19, point 1, applied by the addendum of 2026-09-23 SC-4-01, point 1b): the registry's
    contract carries no mapping from the source rule's id to the copy's, and the details row needs
    exactly that. A copy with the rule and without its details row is an *incomplete* rule, not a
    copied one — criterion K-07's canary.

    A source rule that is itself incomplete (no details row) is copied as incomplete: the copy
    reproduces the source, it does not repair it.

    Nothing here is guarded against `approved`, and nothing needs to be: every copy is a `draft`
    (`copy_scenario` takes no status), and the source is only read.

    **A model outside `DETAIL_TABLE_BY_MODEL` is refused, never half-copied** (R-03, gate 2):
    `CommercialTermsNotCopyable`. Unreachable while the CHECK admits `time_and_material` alone; it
    covers the mixed-version window of ADR-0001 in which a later model's rows exist before every
    instance runs the code that knows its details table. Defensive only — nothing here copies
    another model's details.
    """
    terms = session.execute(
        sa.select(CommercialTerms).where(CommercialTerms.scenario_id == source.id)
    ).scalar_one_or_none()
    if terms is None:
        return
    if terms.model_type not in DETAIL_TABLE_BY_MODEL:
        raise CommercialTermsNotCopyable(
            f"The scenario's commercial terms use the model {terms.model_type!r}, which this "
            "version of the application cannot copy. Nothing was copied; retry once every "
            "instance runs a version that supports it."
        )
    new_terms_id = uuid.uuid4()
    session.add(
        CommercialTerms(
            id=new_terms_id,
            scenario_id=copy.id,
            **values_to_copy(terms, excluded=COMMERCIAL_TERMS_COLUMNS_NOT_COPIED),
        )
    )
    session.flush()
    # The details table of **the rule's own model** — the same registry entry the refusal above
    # checked, never `tm_terms` by name (R-05, SC-4-01 gate 2). A hard-coded `tm_terms` here would
    # stop matching the refusal the day a second model joins `DETAIL_TABLE_BY_MODEL`: the refusal
    # would let the rule through and this lookup would find no details, silently re-creating the
    # incomplete copy R-03 exists to prevent.
    detail_table = DETAIL_TABLE_BY_MODEL[terms.model_type]
    details = (
        session.execute(
            sa.select(detail_table).where(detail_table.c.commercial_terms_id == terms.id)
        )
        .mappings()
        .one_or_none()
    )
    if details is not None:
        session.execute(
            sa.insert(detail_table).values(
                {
                    **{
                        column: value
                        for column, value in details.items()
                        if column not in TM_TERMS_COLUMNS_NOT_COPIED
                    },
                    "commercial_terms_id": new_terms_id,
                }
            )
        )
        session.flush()
