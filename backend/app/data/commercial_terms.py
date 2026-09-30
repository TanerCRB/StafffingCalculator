"""The only path by which a scenario's commercial rule is read, written, copied and priced.

SC-4-01 (F-06.1, Issue #8), extended by SC-4-04 (F-06.4, Issue #68), SC-4-03 (F-06.3, Issue
#67) and SC-4-02 (F-06.2, Issue #66) — Story Points, Outcome-based and Fixed Price registered in
`REVENUE_BY_MODEL`/`DETAIL_TABLE_BY_MODEL` below, proving every mechanism in this module
generalises rather than being a property of the one model it shipped with.

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

**Fixed Price (SC-4-02, F-06.2, Issue #66) is an entry of both registries** (ADR-0003, addendum
2026-09-25 SC-4-02). Its branch of the dispatcher reads one row — the agreed price and its
currency from `fixed_price_terms` — and hands it to `app.domain.revenue_fixed_price`; it never calls
`_billable_months`, so a catalogue gap, an allocation or a cost cannot reach its revenue. Two write
paths, both with the refusal of `approved` inside the statement that writes (ADR-0004, addendum
2026-09-25 SC-4-02, point 1): the creation — the same guarded `INSERT` as T&M, with the price in the
details row (`create_commercial_terms`) — and the price edit, the aggregate's first `UPDATE`, with
ADR-0007's marker compared in the same statement (`update_fixed_price`).
"""

import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime

import sqlalchemy as sa
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, aliased

from app.core.identity import CallerIdentity
from app.data.column_copy import values_to_copy
from app.data.rate_windows import (
    days_covered_in_month,
    frozen_windows_overlapping,
    internal_catalog_windows_overlapping,
    shifted_calendar_month,
)
from app.data.scenario_guard import draft_scenario_read_lock, unapproved_scenario
from app.data.staffing import scenario_in_scope
from app.data.write_errors import WriteFailed, WriteRefused, failure_for
from app.domain.revenue import (
    APPROVED_SNAPSHOT,
    INCOMPLETE_COMMERCIAL_TERMS,
    LIVE_CATALOG,
    NO_COMMERCIAL_TERMS,
    UNSUPPORTED_MODEL_TYPE,
    AssumptionsUsed,
    MonthPrice,
    RateWindow,
    RevenueAnswer,
    RevenueUnavailable,
)
from app.domain.revenue_fixed_price import AgreedPrice, fixed_price_revenue
from app.domain.revenue_outcome_based import (
    OutcomeCategoryInput,
    OutcomeTermsInput,
    outcome_assumptions,
    outcome_based_revenue,
)
from app.domain.revenue_story_points import story_points_assumptions, story_points_revenue
from app.domain.revenue_time_and_material import BillableMonth, time_and_material_revenue
from app.models.approved_snapshot import ApprovedSnapshotCatalogDefaultRate
from app.models.catalog import CatalogDefaultRate
from app.models.commercial_terms import (
    MODEL_TYPE_FIXED_PRICE,
    MODEL_TYPE_OUTCOME_BASED,
    MODEL_TYPE_STORY_POINTS,
    MODEL_TYPE_TIME_AND_MATERIAL,
    OUTCOME_CATEGORIES,
    CommercialTerms,
    FixedPriceTerms,
    OutcomeTerms,
    StoryPointsTerms,
    TmTerms,
    probability_column,
    units_column,
)
from app.models.scenario import Scenario, ScenarioStatus
from app.models.scenario_delivery_segment import ScenarioDeliverySegment
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
# this module to get it (ADR-0004, addendum 2026-09-23 SC-5-01, point 3; rule 10 of the Invariant
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


def priced_month_windows(
    *,
    from_snapshot: bool,
    scenario_id: uuid.UUID,
    include_planned_allocation_hours: bool = False,
    period_shift_months: int = 0,
) -> sa.Select:
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
    period_month = shifted_calendar_month(
        StaffingPositionAllocation.period_month, period_shift_months
    )
    if from_snapshot:
        window = ApprovedSnapshotCatalogDefaultRate
        window_id = window.source_rate_id
        # Resolving per month on the frozen rows, with the same `month_is_priced`, rather than
        # trusting "whatever was frozen" is what point 2e of the addendum requires (K-09).
        condition = frozen_windows_overlapping(period_month)
    else:
        window = CatalogDefaultRate
        window_id = window.id
        condition = internal_catalog_windows_overlapping(period_month)
    selected_columns = [
        StaffingPosition.scenario_id.label("scenario_id"),
        StaffingPosition.id.label("position_id"),
        StaffingPositionAllocation.id.label("allocation_id"),
        period_month.label("period_month"),
        StaffingPositionAllocation.billable_hours.label("billable_hours"),
        window_id.label("window_id"),
        window.effective_from.label("effective_from"),
        window.effective_to.label("effective_to"),
        window.default_selling_rate.label("selling_rate"),
        window.currency.label("currency"),
        month_is_priced(
            StaffingPositionAllocation.id,
            period_month,
            window.valid_period,
            window.default_selling_rate,
            window.currency,
        ).label("month_is_priced"),
    ]
    if include_planned_allocation_hours:
        selected_columns.append(
            StaffingPositionAllocation.planned_allocation_hours.label(
                "planned_allocation_hours"
            )
        )
    return (
        sa.select(*selected_columns)
        .select_from(StaffingPosition)
        .join(
            StaffingPositionAllocation,
            StaffingPositionAllocation.position_id == StaffingPosition.id,
        )
        .outerjoin(window, condition)
        .where(StaffingPosition.scenario_id == scenario_id)
    )


# --- reading the billable months of a scenario, priced -------------------------------------------


def _billable_months(
    session: Session,
    scenario: Scenario,
    *,
    include_planned_allocation_hours: bool = False,
    period_shift_months: int = 0,
) -> tuple[str, list[BillableMonth]]:
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
    rows = priced_month_windows(
        from_snapshot=approved,
        scenario_id=scenario.id,
        include_planned_allocation_hours=include_planned_allocation_hours,
        period_shift_months=period_shift_months,
    ).subquery("priced_month_windows")
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
                planned_allocation_hours=(
                    first.planned_allocation_hours if include_planned_allocation_hours else None
                ),
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


def _outcome_based(session: Session, scenario: Scenario, rule: _Rule) -> RevenueAnswer:
    """Outcome-based (F-06.3; ADR-0003, addendum 2026-09-25 SC-4-03): reads **only** the
    `outcome_terms` row of this rule — no staffing, no allocation, no catalogue, no snapshot.

    That is why the source does not depend on the scenario's status (`rate_source = not_applicable`,
    point 8): the row is the scenario's own data, protected by the write guard (ADR-0004, addendum
    SC-4-03), and an approval freezes nothing new. A rule without its details row is
    `incomplete_commercial_terms`, never a revenue of `0` (point 1).
    """
    details = _outcome_details_of(session, rule)
    if details is None:
        return RevenueUnavailable(
            reason=INCOMPLETE_COMMERCIAL_TERMS, assumptions_used=outcome_assumptions()
        )
    return outcome_based_revenue(_outcome_input(details), scenario_currency=scenario.currency)


def _outcome_details_of(session: Session, rule: _Rule) -> OutcomeTerms | None:
    """The rule's `outcome_terms` row — `None` for another model or a rule without details.

    One read, shared by the pricing (`_outcome_based`) and by the rule's parameters in the
    response (`ScenarioCommercialView.outcome_terms`, R-04), so both read the same row."""
    if rule.terms.model_type != MODEL_TYPE_OUTCOME_BASED or not rule.has_details:
        return None
    return session.execute(
        sa.select(OutcomeTerms).where(OutcomeTerms.commercial_terms_id == rule.terms.id)
    ).scalar_one_or_none()


def _outcome_input(details: OutcomeTerms) -> OutcomeTermsInput:
    """The `outcome_terms` row as the input of a pure function — categories in a fixed order."""
    return OutcomeTermsInput(
        currency=details.currency,
        fixed_fee=details.fixed_fee,
        success_bonus=details.success_bonus,
        unit_rate=details.unit_rate,
        revenue_min=details.revenue_min,
        revenue_max=details.revenue_max,
        categories=tuple(
            OutcomeCategoryInput(
                category=category,
                units=getattr(details, units_column(category)),
                probability=getattr(details, probability_column(category)),
            )
            for category in OUTCOME_CATEGORIES
        ),
    )


def _story_points(session: Session, scenario: Scenario, rule: _Rule) -> RevenueAnswer:
    """Story Points (F-06.4, SC-4-04): a rule without its `story_points_terms` row is
    `incomplete_commercial_terms`, never priced — the same named state T&M uses for the same reason
    (ADR-0003, point 3).

    **Reads no staffing table** (criterion K-02): unlike `_time_and_material`, this branch never
    calls `_billable_months` and never touches `StaffingPosition`/`StaffingPositionAllocation` — the
    price is entirely the rule's own row, so a scenario's billable-hours plan can change arbitrarily
    without moving this revenue by a cent. `assumptions_used` names that explicitly
    (`HOURS_SOURCE_NOT_APPLICABLE`), not merely by omission — on the incomplete branch too, through
    the same `story_points_assumptions` the priced path uses (verification SC-4-07, R-01).
    """
    if not rule.has_details:
        return RevenueUnavailable(
            reason=INCOMPLETE_COMMERCIAL_TERMS, assumptions_used=story_points_assumptions()
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


def _fixed_price(session: Session, scenario: Scenario, rule: _Rule) -> RevenueAnswer:
    """Fixed Price: the agreed price of the rule's own details row — and nothing else is read.

    **No `_billable_months`, no catalogue, no snapshot, no allocation** (ADR-0003, addendum
    2026-09-25 SC-4-02, points 3 and 5; criterion K-01): the price is own data of the scenario, read
    from its own row for a draft and for an approved scenario alike — which is why
    `assumptions_used.rate_source` is `fixed_price_terms` whatever the status (the SC-4-04 pattern).
    A rule without its details row is `incomplete_commercial_terms` (`fixed_price_revenue(None)`),
    never a revenue of `0`.
    """
    return _fixed_price_answer(_fixed_price_details_of(session, rule), scenario)


def _fixed_price_answer(price: AgreedPrice | None, scenario: Scenario) -> RevenueAnswer:
    """The Fixed Price answer — result or named state, with its `assumptions_used` — for a price
    already read (`_fixed_price_details_of`) and the scenario's currency.

    The one place the data layer turns an `AgreedPrice` into a revenue answer, shared by the
    `REVENUE_BY_MODEL` entry (`_fixed_price`) and by `_view_of`, which reads the price once and
    passes the same value here and to `ScenarioCommercialView.agreed_price` (R-01 of the SC-4-02
    review, 2026-09-28; QA round 2: two calls of the formula could drift apart untested). Reads
    nothing."""
    return fixed_price_revenue(price, scenario_currency=scenario.currency)


def _fixed_price_details_of(session: Session, rule: _Rule) -> AgreedPrice | None:
    """The agreed price of the rule's `fixed_price_terms` row — `None` for a rule of another model
    or a Fixed Price rule without its details row.

    The one read of the price. `_view_of` calls it **once** and hands the same `AgreedPrice` value
    to the pricing (`fixed_price_revenue`) and to the rule's parameters in the answer
    (`ScenarioCommercialView.agreed_price`), so a price edit committed between two statements of
    the read cannot put one price in `revenue.amount` and another in `agreed_price` (R-01 of the
    SC-4-02 review, 2026-09-28 — two reads under `READ COMMITTED` could). `_fixed_price` (the
    dispatcher's branch, used by every other reader) calls it once too. Keyed by **this rule's id**,
    which is what keeps two scenarios' prices apart (criterion K-02)."""
    if rule.terms.model_type != MODEL_TYPE_FIXED_PRICE or not rule.has_details:
        return None
    row = session.execute(
        sa.select(_FIXED_PRICE_TABLE.c.agreed_price, _FIXED_PRICE_TABLE.c.currency).where(
            _FIXED_PRICE_TABLE.c.commercial_terms_id == rule.terms.id
        )
    ).one_or_none()
    if row is None:
        return None
    return AgreedPrice(amount=row.agreed_price, currency=row.currency)


REVENUE_BY_MODEL: dict[str, RevenueCalculator] = {
    MODEL_TYPE_TIME_AND_MATERIAL: _time_and_material,
    MODEL_TYPE_STORY_POINTS: _story_points,
    MODEL_TYPE_OUTCOME_BASED: _outcome_based,
    MODEL_TYPE_FIXED_PRICE: _fixed_price,
}
"""`model_type` → the function that prices it. Chosen by the discriminator alone, never by the shape
of the data (ADR-0003, point 9). Keys must equal `app.models.commercial_terms.MODEL_TYPES`, and a
test asserts it: a model the CHECK admits and this map does not know would be a rule nobody can
price."""

DETAIL_TABLE_BY_MODEL: dict[str, sa.Table] = {
    MODEL_TYPE_TIME_AND_MATERIAL: TmTerms.__table__,
    MODEL_TYPE_STORY_POINTS: StoryPointsTerms.__table__,
    MODEL_TYPE_OUTCOME_BASED: OutcomeTerms.__table__,
    MODEL_TYPE_FIXED_PRICE: FixedPriceTerms.__table__,
}
"""`model_type` → its details table, for the write path that creates both rows in one statement."""

_FIXED_PRICE_TABLE = FixedPriceTerms.__table__
_OTHER_TERMS = CommercialTerms.__table__.alias("other_commercial_terms")


class MultipleCommercialRulesNotSupported(RuntimeError):
    """A scenario carries more than one `commercial_terms` row (SC-4-05, D-3=A: `scope_ref` admits
    more than the single row `_rule_of`/`commercial_terms_for_caller` assume), and this read path
    has no way to say which one is "the" rule.

    Raised instead of letting `scalar_one_or_none()` throw the raw, unnamed
    `sqlalchemy.exc.MultipleResultsFound` — fail loud with a name a caller can catch or at least
    read in a log, not a stack trace attached to nothing (reviewer/invariant-guardian, gate 2 of
    SC-4-05). Unreachable through the running API today: no request schema writes `scope_ref`, so
    the only way to reach this state is a direct write at the data layer (tests, or a future caller
    of `create_commercial_terms` with `scope_ref` set) — named here rather than left to surface as
    an unhandled `500` with no distinguishing type. `rules_of_scenario`/`revenue_by_model_type` are
    the N-row-aware read path; nothing here decides which of several rules would be "the" one to
    show through this older, single-row-assuming path.
    """


def _rule_of(session: Session, scenario_id: uuid.UUID) -> _Rule | None:
    """The rule of one (already in-scope) scenario and whether its details row exists.

    The details row is looked up in **the details table of the rule's own model**
    (`DETAIL_TABLE_BY_MODEL`), never in `tm_terms` by name (R-05, SC-4-01 gate 2): the day a second
    model joins the registry, a hard-coded `tm_terms` lookup would call every rule of that model
    incomplete. A model this version does not know has no table to look in — `has_details` is then
    `False`, and the dispatcher answers `unsupported_model_type` before it reads it.

    **Raises `MultipleCommercialRulesNotSupported` if the scenario carries more than one row**
    (SC-4-05) — this function still answers "the" rule of a scenario, a question `scope_ref` makes
    ill-posed once more than one row exists; `rules_of_scenario` is the caller that expects N rows.
    """
    # `populate_existing`: since SC-4-02 the rule has an `UPDATE` path (`update_fixed_price`) that
    # rotates `updated_at` in SQL, so a `CommercialTerms` already in the identity map — loaded
    # earlier in the same session — would otherwise answer with the marker it had before the edit,
    # and the client's next edit would be refused as stale.
    rows = session.execute(
        sa.select(CommercialTerms)
        .where(CommercialTerms.scenario_id == scenario_id)
        .execution_options(populate_existing=True)
    ).scalars().all()
    if not rows:
        return None
    if len(rows) > 1:
        raise MultipleCommercialRulesNotSupported(
            f"Scenario {scenario_id} carries {len(rows)} commercial_terms rows; this read path "
            "assumes at most one. Use rules_of_scenario/revenue_by_model_type instead."
        )
    terms = rows[0]
    detail_table = DETAIL_TABLE_BY_MODEL.get(terms.model_type)
    has_details = detail_table is not None and session.execute(
        sa.select(sa.exists().where(detail_table.c.commercial_terms_id == terms.id))
    ).scalar_one()
    return _Rule(terms=terms, has_details=has_details)


def rules_of_scenario(session: Session, scenario_id: uuid.UUID) -> list[_Rule]:
    """Every commercial rule of one (already in-scope) scenario — 0, 1 or N rows (SC-4-05:
    `scope_ref` admits more than the single row `_rule_of` assumes), each paired with whether its
    details row exists, the same way `_rule_of` pairs the one row it reads.

    Ordered with the whole-scenario rule first (`scope_ref IS NULL`), then by `scope_ref` — a
    stable, arbitrary order, not a claim about precedence: nothing here decides which rule "wins"
    for a given unit of work, because nothing here knows which unit of work belongs to which
    segment (F-04, out of scope of SC-4-05).
    """
    rows = session.execute(
        sa.select(CommercialTerms)
        .where(CommercialTerms.scenario_id == scenario_id)
        .order_by(CommercialTerms.scope_ref.is_(None).desc(), CommercialTerms.scope_ref)
    ).scalars().all()
    rules: list[_Rule] = []
    for terms in rows:
        detail_table = DETAIL_TABLE_BY_MODEL.get(terms.model_type)
        has_details = detail_table is not None and session.execute(
            sa.select(sa.exists().where(detail_table.c.commercial_terms_id == terms.id))
        ).scalar_one()
        rules.append(_Rule(terms=terms, has_details=has_details))
    return rules


class MultipleRulesOfOneModelNotSupported(RuntimeError):
    """More than one rule of one `model_type` exists for a scenario, and that model's revenue is
    not *proven* independent of which row priced it — so picking one and dropping the rest would
    silently lose the dropped rule's revenue (reviewer R-01, gate 2 of SC-4-05).

    Time & Material is the one model this is proven safe for (`_MODEL_TYPES_WITH_SHARED_SCENARIO_
    REVENUE` below): its formula reads the whole scenario's shared, unscoped `staffing_position`/
    `staffing_position_allocation` rows regardless of which rule computed it, so two T&M rules of
    one scenario are provably the same answer — pricing one *is* pricing all of them. Story Points
    is not: `story_points_terms.price_per_point`/`accepted_points` are carried on the rule's own
    row, so two Story Points rules (legal under K-02/D-3=A — a whole-scenario rule and a segment
    rule, say) can name genuinely different figures, and `revenue_by_model_type` refuses to guess
    which one to keep rather than return a `calculated` answer with the other's revenue silently
    gone.
    """


_MODEL_TYPES_WITH_SHARED_SCENARIO_REVENUE: frozenset[str] = frozenset(
    {MODEL_TYPE_TIME_AND_MATERIAL}
)
"""Models whose revenue formula reads data shared by the whole scenario rather than data carried on
the rule's own row — so two or more rules of this model in one scenario are provably the same
answer, and `revenue_by_model_type` may price the model once, from any one of them (K-01). Every
other model (today: Story Points, Outcome-based — `_outcome_based` reads **only** the rule's own
`outcome_terms` row, SC-4-03 — and Fixed Price — `_fixed_price` reads **only** the rule's own
`fixed_price_terms` row, SC-4-02) is priced from `commercial_terms`'s own details row, where two
rules can legitimately disagree — those raise `MultipleRulesOfOneModelNotSupported` instead of
picking one silently. A model joins this set only when its formula is checked to have the same
shared-data property T&M has — not by default."""


def revenue_by_model_type(
    session: Session, scenario: Scenario, rules: list[_Rule]
) -> dict[str, RevenueAnswer]:
    """The scenario's revenue, once per distinct `model_type` present among `rules` (criterion
    K-01) — never once per rule row, and never a silent drop of a second rule's revenue.

    Two or more rules of the *same* model — a whole-scenario T&M rule and a segment T&M rule, say —
    read the identical `staffing_position`/`staffing_position_allocation` rows: no segment-to-
    position link exists (F-04 is out of scope of SC-4-05, `docs/PLAN.md`'s "Out of scope"), so
    pricing every row and summing would double the true amount. Pricing **one** T&M rule (any one)
    is therefore pricing the model correctly — the disjointness predicate K-01's mutation removes;
    dropping it and pricing every rule row instead reproduces exactly that double count for two T&M
    rules of one scenario, and changes nothing for a scenario carrying one rule of each model — Time
    & Material and Story Points share no countable unit (hours vs. points), so their two independent
    amounts are not a double count of either, and that pairing cannot kill this mutation
    (`docs/PLAN.md`, K-01's own note).

    **For a model outside `_MODEL_TYPES_WITH_SHARED_SCENARIO_REVENUE`, two or more rules raise
    `MultipleRulesOfOneModelNotSupported`** instead of picking one (reviewer R-01): unlike T&M,
    nothing here has checked that model's formula reads scenario-shared data rather than the rule's
    own row, so "any one of them" is not provably "all of them" — picking one would be a silent,
    undetectable loss of the others' revenue, exactly the failure mode K-01 exists to prevent, one
    level up.

    Returns one answer **per distinct model**, not one combined scenario total: combining two
    models' revenue into a single figure (reconciling currency, in particular) is the per-segment
    allocation F-06.5 asks for once a position knows its segment — F-04, out of scope here (ADR-0003
    addendum 2026-09-25, closing annex: "SC-4-05 proves disjointness at the SCHEMA level, not at the
    REVENUE level").
    """
    rules_by_model_type: dict[str, list[_Rule]] = {}
    for rule in rules:
        rules_by_model_type.setdefault(rule.terms.model_type, []).append(rule)

    answers: dict[str, RevenueAnswer] = {}
    for model_type, model_rules in rules_by_model_type.items():
        if len(model_rules) > 1 and model_type not in _MODEL_TYPES_WITH_SHARED_SCENARIO_REVENUE:
            raise MultipleRulesOfOneModelNotSupported(
                f"Scenario {scenario.id} carries {len(model_rules)} rules of model "
                f"{model_type!r}, whose revenue is not proven independent of which rule priced "
                "it. Pricing one and silently dropping the rest is refused."
            )
        answers[model_type] = revenue_of(session, scenario, model_rules[0])
    return answers


def revenue_of(session: Session, scenario: Scenario, rule: _Rule | None) -> RevenueAnswer:
    """Price one scenario — or name why it cannot be priced. Never `0` for a missing input.

    `scenario` must be one `scenario_in_scope` returned: this function decides no access.

    **A model this version of the code does not know is a named state, not a `KeyError`** (R-02,
    gate 2). The discriminator CHECK admits only models this version knows (`time_and_material`,
    `story_points` since SC-4-04, `outcome_based` since SC-4-03, `fixed_price` since SC-4-02), so
    the branch is unreachable in a single-version deployment; it is reachable in the mixed-version
    window of expand → deploy → contract (ADR-0001), when a later model's migration has widened the
    CHECK and an instance still runs older code — since SC-4-03 proven on a real `outcome_based` row
    read by a code version without that branch (`tests/test_outcome_revenue_copy.py`).
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
    this read's own `session.refresh` — for every commercial model alike, before the dispatcher
    runs (SC-7-03, Issue #118; ADR-0015, addendum SC-7-03, points 1 and 3).

    Never `scenario.status` read later: `scenario` is an identity-mapped object that a later read
    in the same session (`app.data.personnel_cost.scenario_cost_for_caller`) refreshes again, so its
    `.status` stops being a record of *this* read the moment that call runs. This value does not
    move. It is what `app.data.scenario_results.refuse_a_status_race` compares with the cost read's
    status — only when `revenue.assumptions_used.rate_source` classifies the revenue as
    status-dependent (`STATUS_DEPENDENT_SOURCES`); `rate_source` itself is never compared with the
    cost's (ADR-0003, addendum SC-7-03)."""
    outcome_terms: OutcomeTerms | None = None
    """The Outcome-based rule's details row, to show its parameters (R-04) — `None` for
    every other model and for a rule without a details row. Revenue parameters, not cost."""
    agreed_price: AgreedPrice | None = None
    """The Fixed Price details row of the rule, as stored (SC-4-02) — `None` for a rule of another
    model and for a Fixed Price rule without its details row (`incomplete_commercial_terms`). Stated
    at full stored precision: this is the input a client edits, not the rounded revenue."""


    billable_months: tuple[BillableMonth, ...] | None = None
    """Temporary T&M inputs, included only when a what-if needs a substitution."""


def _view_of(
    session: Session,
    scenario: Scenario,
    *,
    include_billable_months: bool = False,
    period_shift_months: int = 0,
) -> ScenarioCommercialView:
    # Refreshed, not trusted from the identity map: the status decides live-versus-snapshot, and an
    # object loaded earlier in the same session may predate an approval committed since.
    session.refresh(scenario)
    status_at_read = scenario.status
    rule = _rule_of(session, scenario.id)
    agreed_price: AgreedPrice | None = None
    billable_months: tuple[BillableMonth, ...] | None = None
    if rule is not None and rule.terms.model_type == MODEL_TYPE_FIXED_PRICE:
        # One read of the price for the whole view (R-01, 2026-09-28): the revenue is priced from
        # the very value the answer states as `agreed_price`, never from a second read of the row.
        # The same helper the `REVENUE_BY_MODEL` entry (`_fixed_price`) calls.
        agreed_price = _fixed_price_details_of(session, rule)
        revenue = _fixed_price_answer(agreed_price, scenario)
    elif (
        include_billable_months
        and rule is not None
        and rule.terms.model_type == MODEL_TYPE_TIME_AND_MATERIAL
        and rule.has_details
    ):
        source, months = _billable_months(
            session,
            scenario,
            include_planned_allocation_hours=True,
            period_shift_months=period_shift_months,
        )
        billable_months = tuple(months)
        revenue = time_and_material_revenue(
            months, rate_source=source, scenario_currency=scenario.currency
        )
    else:
        revenue = revenue_of(session, scenario, rule)
    return ScenarioCommercialView(
        scenario=scenario,
        terms=None if rule is None else rule.terms,
        revenue=revenue,
        status_at_read=status_at_read,
        outcome_terms=None if rule is None else _outcome_details_of(session, rule),
        agreed_price=agreed_price,
        billable_months=billable_months,
    )


def commercial_terms_for_caller(
    session: Session,
    caller: CallerIdentity,
    project_id: uuid.UUID,
    scenario_id: uuid.UUID,
    *,
    include_billable_months: bool = False,
    lock_draft_scenario_for_composition: bool = False,
    period_shift_months: int = 0,
) -> ScenarioCommercialView | None:
    """The rule and revenue of one scenario — or `None`, with no way to tell why (criterion K-05).

    `None` is "no such scenario *for this caller*"; a scenario with no rule is a view whose revenue
    is the named `no_commercial_terms` state, never `None` and never `0`. When
    `lock_draft_scenario_for_composition` is requested, scope is resolved first and then a draft
    scenario `FOR SHARE` lock is retained by this session through the caller's composed read.
    """
    scenario = scenario_in_scope(session, caller, project_id, scenario_id)
    if scenario is None:
        return None
    if lock_draft_scenario_for_composition and (
        session.execute(draft_scenario_read_lock(scenario.id)).scalar_one_or_none() is None
    ):
        return None
    return _view_of(
        session,
        scenario,
        include_billable_months=include_billable_months,
        period_shift_months=period_shift_months,
    )


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
    scope_ref: uuid.UUID | None = None,
    domain_values: Mapping[str, object] | None = None,
) -> ScenarioCommercialView | None:
    """Create the rule of one scenario **and** its details row, in one guarded statement.

    `domain_values` — the domain columns of the details row (for `outcome_based`: fee, bonus,
    rate, min/max, currency, units and the categories' probabilities; for `story_points`: price
    per point, number of points, currency; none for T&M). They enter the **same**
    statement as literals next to the rule's `RETURNING`, so the `approved` guard covers them
    the same way (ADR-0004, addendum 2026-09-25 SC-4-03, point 2), and a refusal writes no row.

    For Fixed Price (SC-4-02) the domain columns are `agreed_price` and `currency`. Since SC-4-02
    `domain_values` must be exactly the table's domain columns (`_domain_columns_of`), for every
    model: a Fixed Price rule without its price is unwritable here as well as at the API (`422`) and
    in the database (`NOT NULL`), and it is never created as an incomplete rule to be filled in
    later (ADR-0003, addendum 2026-09-25 SC-4-02, point 2).

    ```
    WITH new_commercial_terms AS (
        INSERT INTO commercial_terms (id, scenario_id, model_type, scope_ref)
        SELECT :id, open_scenario.id, :model_type, :scope_ref
          FROM (SELECT id FROM scenarios
                 WHERE id = :scenario_id AND status <> 'approved' FOR UPDATE) AS open_scenario
        RETURNING id, model_type
    )
    INSERT INTO tm_terms (commercial_terms_id, model_type)
    SELECT id, model_type FROM new_commercial_terms
    RETURNING commercial_terms_id
    ```

    **`scope_ref` (SC-4-05, Issue #69, D-3=A)** — `None` (the default) writes a whole-scenario rule,
    exactly SC-4-01's shape. A segment id writes a rule scoped to that `scenario_delivery_segment`,
    carried as a literal into the *same* guarded `INSERT … SELECT` — never a second statement — so
    the composite foreign key `app.models.commercial_terms.SCOPE_REF_FOREIGN_KEY` and the two
    partial unique indexes (K-02, K-04) are the only things that can refuse it, and they refuse it
    inside the one statement that writes, exactly as the `approved` guard does. No HTTP request
    schema carries this parameter yet (ADR-0016, point 8: no API surface for a segment) — SC-4-05
    proves the write path exists and is correctly guarded; wiring it to a client-facing endpoint is
    left to the task that decides the API shape of "which segment" (an explicit gap, named in the
    SC-4-05 developer report).

    (`story_points_terms`, `outcome_terms` and `fixed_price_terms` widen the second `INSERT`'s
    column list with `domain_values` — see below; the shape above is unchanged for a model with
    none, which is why passing no `domain_values` for T&M is not a second, different statement.)

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
      (Story Points: `price_per_point`, `accepted_points`, `currency`; Fixed Price, SC-4-02:
      `agreed_price`, `currency`), carried as literals of the
      detail table's own column type into the *same* guarded `INSERT … SELECT`, never a second,
      unguarded statement. Empty for a model with no domain column (T&M today), so this path is the
      one T&M already shipped, not a new one next to it.

    `None` means "no such scenario for this caller" and is decided before the guard, so a `409` can
    never confirm that a scenario outside the caller's scope exists (K-05).
    """
    detail_table = DETAIL_TABLE_BY_MODEL[model_type]
    domain_values = dict(domain_values or {})
    expected = _domain_columns_of(detail_table)
    if set(domain_values) != expected:
        raise ValueError(
            f"The {model_type!r} details row takes exactly these fields: "
            f"{', '.join(sorted(expected)) or '(none)'}; "
            f"got: {', '.join(sorted(domain_values)) or '(none)'}."
        )

    if scenario_in_scope(session, caller, project_id, scenario_id) is None:
        return None

    open_scenario = unapproved_scenario(scenario_id).subquery("open_scenario")
    new_terms = (
        sa.insert(_TERMS_TABLE)
        .from_select(
            ["id", "scenario_id", "model_type", "scope_ref"],
            sa.select(
                sa.literal(uuid.uuid4(), type_=_TERMS_TABLE.c.id.type).label("id"),
                open_scenario.c.id.label("scenario_id"),
                sa.literal(model_type, type_=_TERMS_TABLE.c.model_type.type).label(
                    "model_type"
                ),
                sa.literal(scope_ref, type_=_TERMS_TABLE.c.scope_ref.type).label(
                    "scope_ref"
                ),
            ).select_from(open_scenario),
        )
        .returning(_TERMS_TABLE.c.id, _TERMS_TABLE.c.model_type)
        .cte("new_commercial_terms")
    )
    # The domain values ride in the same `INSERT … SELECT` as bound literals typed by their own
    # columns: a price is written by the statement that is guarded, never by a second one that
    # could run against a scenario approved in between.
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


_DETAILS_BOOKKEEPING_COLUMNS: frozenset[str] = frozenset(
    {"commercial_terms_id", "model_type", "created_at"}
)
"""The columns every details table has and no client supplies: the rule's id and model (both taken
from the rule inside the statement) and the row's own timestamp."""


def _domain_columns_of(detail_table: sa.Table) -> set[str]:
    """The columns of a details table a writer supplies — none for `tm_terms`, the price, the points
    and the currency for `story_points_terms`, the price pair for `fixed_price_terms`."""
    return {column.name for column in detail_table.columns} - _DETAILS_BOOKKEEPING_COLUMNS


# --- editing the Fixed Price price (ADR-0003, addendum 2026-09-25 SC-4-02, point 7; D-6 = A) ------


class CommercialTermsNotFound(RuntimeError):
    """The scenario is in scope but has no Fixed Price price to edit — a `404`, decided before any
    `409` (the R-01 order: the target first, then `approved`, then the marker)."""


class CommercialTermsEditAmbiguous(CommercialTermsWriteRejected):
    """The scenario carries more than one commercial rule (SC-4-05 `scope_ref`: a whole-scenario
    rule next to a segment-scoped one), so the price edit is refused before anything is written — a
    `409`, permanent for the scenario as it is (R-03 of the SC-4-02 review, 2026-09-28).

    Without this refusal the guarded `UPDATE` would commit the new price and the read that builds
    the answer (`_view_of` → `_rule_of`) would then raise `MultipleCommercialRulesNotSupported`: a
    `500` after a write that landed. Editing a segment-scoped Fixed Price rule is out of scope of
    SC-4-02."""


class CommercialTermsEditConflict(CommercialTermsWriteRejected):
    """ADR-0007: the rule changed since the caller read it — a `409` told apart from the `approved`
    one by its message. Transient: re-read and apply again (the `approved` refusal is permanent)."""


EDITABLE_FIXED_PRICE_FIELDS: frozenset[str] = frozenset({"agreed_price", "currency"})
"""What the price edit may change: the price pair and nothing else — never `model_type` (ADR-0003,
point 2: immutable after the write)."""


def update_fixed_price(
    session: Session,
    caller: CallerIdentity,
    project_id: uuid.UUID,
    scenario_id: uuid.UUID,
    *,
    expected_updated_at: datetime,
    changes: Mapping[str, object],
) -> ScenarioCommercialView | None:
    """Change the agreed price of a draft scenario's Fixed Price rule — or refuse. D-6 = A.

    `None` means "no such scenario for this caller" and is decided first, so no `409` can confirm
    that a scenario outside the caller's scope exists (K-05). Everything else is **one statement**:

    ```
    WITH guarded_commercial_terms AS (
        UPDATE commercial_terms SET updated_at = now()
         WHERE scenario_id = :scenario_id
           AND scope_ref IS NULL                           -- the whole-scenario rule (SC-4-05)
           AND model_type = 'fixed_price'
           AND updated_at = :expected                      -- ADR-0007, the aggregate's marker
           AND scenario_id IN (SELECT id FROM scenarios
                                WHERE id = :scenario_id AND status <> 'approved'
                                  FOR UPDATE)              -- ADR-0004, and the lock (K-05 race)
           AND EXISTS (SELECT 1 FROM fixed_price_terms
                        WHERE commercial_terms_id = commercial_terms.id)
           AND NOT EXISTS (SELECT 1 FROM commercial_terms other  -- the only rule (R-03)
                            WHERE other.scenario_id = :scenario_id
                              AND other.id <> commercial_terms.id)
        RETURNING id
    )
    UPDATE fixed_price_terms SET agreed_price = :price, currency = :currency
      FROM guarded_commercial_terms
     WHERE fixed_price_terms.commercial_terms_id = guarded_commercial_terms.id
    RETURNING fixed_price_terms.commercial_terms_id
    ```

    - **The marker is compared and rotated by the database, in the statement that writes the
      price** — the shape ADR-0007 proved for the position aggregate (`guarded_position`, addendum
      2026-09-22 SC-3-02, point 1), applied to the rule, whose marker covers its details row
      (ADR-0003, "Konsekwencje"). No second marker on `fixed_price_terms`.
    - **The `approved` refusal and the scenario row lock are inside the same statement**
      (`app.data.scenario_guard.unapproved_scenario`, ADR-0004, addendum 2026-09-25 SC-4-02,
      point 1): a Python status check before it is the mutation the race test kills.
    - **The `EXISTS` keeps "nothing matched" equal to "nothing written"**: without it, a rule
      with no details row would have its marker rotated by the CTE while the outer `UPDATE`
      changed nothing.
    - **`model_type = 'fixed_price'` is in the `WHERE`**, not checked in Python: the edit of a T&M
      rule matches nothing and is diagnosed as "no price to edit" (`404`).
    - **`scope_ref IS NULL` is in the `WHERE`** (SC-4-05 adaptation, sync of 2026-09-28): this path
      edits the scenario's *whole-scenario* rule — the one rule every single-rule read path here
      (`_rule_of`) answers with — so `uq_commercial_terms_scenario_id` (at most one rule with
      `scope_ref IS NULL` per scenario) bounds the CTE to at most one row. A segment-scoped rule
      (writable only at the data layer — no request schema carries `scope_ref`) is not reached by
      it and is diagnosed as "no price to edit" (`404`).
    - **`NOT EXISTS` another rule of the scenario is in the `WHERE`** (R-03 of the SC-4-02 review,
      2026-09-28): the edit writes only when the whole-scenario rule is the scenario's *only* rule,
      so it can never commit a price the answer's single-rule read (`_rule_of`) then refuses to
      show — `CommercialTermsEditAmbiguous`, a `409`, and nothing written. The predicate is
      evaluated against the statement's snapshot: a segment rule committed by another transaction
      after it started is not seen (such rules are writable only at the data layer today).

    A price below zero is refused by `ck_fixed_price_terms_agreed_price_non_negative` inside the
    `UPDATE` — a `409` naming the constraint (`app.data.write_errors`) for a caller that got
    past the schema's own bound.
    """
    forbidden = sorted(set(changes) - EDITABLE_FIXED_PRICE_FIELDS)
    if forbidden or not changes:
        raise ValueError(
            "A Fixed Price edit changes at least one of "
            f"{', '.join(sorted(EDITABLE_FIXED_PRICE_FIELDS))} and nothing else; "
            f"got: {', '.join(sorted(changes)) or '(none)'}."
        )

    if scenario_in_scope(session, caller, project_id, scenario_id) is None:
        return None

    guarded = (
        sa.update(_TERMS_TABLE)
        .where(
            _TERMS_TABLE.c.scenario_id == scenario_id,
            _TERMS_TABLE.c.scope_ref.is_(None),
            _TERMS_TABLE.c.model_type == MODEL_TYPE_FIXED_PRICE,
            _TERMS_TABLE.c.updated_at == expected_updated_at,
            _TERMS_TABLE.c.scenario_id.in_(unapproved_scenario(scenario_id)),
            sa.exists().where(_FIXED_PRICE_TABLE.c.commercial_terms_id == _TERMS_TABLE.c.id),
            ~sa.exists().where(
                _OTHER_TERMS.c.scenario_id == scenario_id, _OTHER_TERMS.c.id != _TERMS_TABLE.c.id
            ),
        )
        # Explicit rather than left to the column's `onupdate`: the rotation of the marker is part
        # of what this statement claims, and `now()` is the database's clock.
        .values(updated_at=sa.func.now())
        .returning(_TERMS_TABLE.c.id)
        .cte("guarded_commercial_terms")
    )
    statement = (
        sa.update(_FIXED_PRICE_TABLE)
        .add_cte(guarded)
        .where(_FIXED_PRICE_TABLE.c.commercial_terms_id == guarded.c.id)
        .values(**dict(changes))
        .returning(_FIXED_PRICE_TABLE.c.commercial_terms_id)
    )

    try:
        applied = session.execute(statement).one_or_none()
        if applied is None:
            # The `EXISTS` above makes "no row returned" mean "the CTE matched nothing" — nothing
            # was written, so nothing is rolled back (the reasoning of `create_commercial_terms`).
            raise _diagnose_edit_refusal(session, scenario_id)
        session.commit()
    except SQLAlchemyError as error:
        session.rollback()
        # `from None`: PostgreSQL's `DETAIL: Failing row contains (…)` must not ride along (NF-11).
        raise _failure(error) from None
    scenario = scenario_in_scope(session, caller, project_id, scenario_id)
    if scenario is None:  # pragma: no cover — the scenario was in scope a statement ago
        return None
    return _view_of(session, scenario)


def _diagnose_edit_refusal(session: Session, scenario_id: uuid.UUID) -> Exception:
    """Name the reason the guarded price edit matched nothing — after the refusal, never as the
    guard.

    The R-01 order of `app.data.additional_cost._diagnose_row_refusal`: the target first (a Fixed
    Price rule **with** its price row, in this scenario — `404` even under `approved`, since "copy
    the scenario and edit the price there" would not help), then the permanent reasons (another
    rule next to it — R-03, a copy would carry both; then `approved`), then the transient one (the
    marker). Every lookup is narrowed to a scenario `scenario_in_scope`
    has already returned.
    """
    has_price = session.execute(
        sa.select(
            sa.exists().where(
                _TERMS_TABLE.c.scenario_id == scenario_id,
                _TERMS_TABLE.c.scope_ref.is_(None),
                _TERMS_TABLE.c.model_type == MODEL_TYPE_FIXED_PRICE,
                _FIXED_PRICE_TABLE.c.commercial_terms_id == _TERMS_TABLE.c.id,
            )
        )
    ).scalar_one()
    if not has_price:
        return CommercialTermsNotFound("This scenario has no Fixed Price commercial terms to edit.")
    rules = session.execute(
        sa.select(sa.func.count()).where(_TERMS_TABLE.c.scenario_id == scenario_id)
    ).scalar_one()
    if rules > 1:
        return CommercialTermsEditAmbiguous(
            "This scenario has more than one set of commercial terms, so its Fixed Price price "
            "cannot be edited here."
        )
    refusal = _diagnose_refusal(session, scenario_id)
    if isinstance(refusal, CommercialTermsFrozen):
        return refusal
    return CommercialTermsEditConflict(
        "The commercial terms changed since they were read (concurrency marker). Re-read them and "
        "apply the change again."
    )


# --- the copying cascade (ADR-0004, addendum 2026-09-23 SC-4-01, point 1b) -----------------------

COMMERCIAL_TERMS_COLUMNS_NOT_COPIED: frozenset[str] = frozenset(
    {"id", "scenario_id", "scope_ref", "created_at", "updated_at"}
)
"""Rule attributes a copy does not inherit: a new row (`id`), the copy's own scenario
(`scenario_id`), and its own timestamps — the ADR-0007 marker of the copy is its own, never the
source's. `scope_ref` (SC-4-05) is excluded from blind reflection for a different reason than the
other three: it is not *dropped*, it is *remapped* — the source's segment id means nothing on the
copy (the composite foreign key would refuse it outright, pointing at another scenario's segment) —
so `copy_commercial_terms` below computes the copy's own `scope_ref` explicitly instead of letting
`values_to_copy` carry the source's value across verbatim. Everything else (today: `model_type`) is
copied by reflection, and a drift guard asserts every mapped attribute is on one side or the
other."""

TM_TERMS_COLUMNS_NOT_COPIED: frozenset[str] = frozenset({"commercial_terms_id", "created_at"})
"""The same for the details row — of `tm_terms` today, and the convention every details table
follows (the key is the rule's id, `created_at` is the copy's own); the copier applies it to the
table `DETAIL_TABLE_BY_MODEL` names for the rule's model. `commercial_terms_id` is the one value
that cannot be reflected: it is the *copied* rule's id, which is why the details row has no
registry entry of its own."""

FIXED_PRICE_TERMS_COLUMNS_NOT_COPIED: frozenset[str] = frozenset(
    {"commercial_terms_id", "created_at"}
)
"""The same convention for `fixed_price_terms` (SC-4-02; ADR-0004, addendum 2026-09-25, point 2) —
a details table whose copy carries domain values: `agreed_price` and `currency` are copied
by reflection, `model_type` too, and only the key and the copy's own timestamp are not. A separate
constant rather than a reuse of the T&M one, so the drift guard of each table is its own and a
column added to one table cannot be decided for it by the other's set."""

DETAIL_COLUMNS_NOT_COPIED_BY_MODEL: dict[str, frozenset[str]] = {
    MODEL_TYPE_TIME_AND_MATERIAL: TM_TERMS_COLUMNS_NOT_COPIED,
    # Story Points keeps exactly the set its copy used when SC-4-04 shipped (the T&M convention,
    # "the convention every details table follows"): this registry changes nothing of its copy.
    MODEL_TYPE_STORY_POINTS: TM_TERMS_COLUMNS_NOT_COPIED,
    # Outcome-based likewise keeps the set its copy used when SC-4-03 shipped.
    MODEL_TYPE_OUTCOME_BASED: TM_TERMS_COLUMNS_NOT_COPIED,
    MODEL_TYPE_FIXED_PRICE: FIXED_PRICE_TERMS_COLUMNS_NOT_COPIED,
}
"""`model_type` → the columns its details row does not pass to a copy. Keyed like
`DETAIL_TABLE_BY_MODEL`, and a test asserts the two registries have the same keys."""


class CommercialTermsNotCopyable(RuntimeError):
    """The source scenario's rule names a model this version of the code cannot copy (R-03).

    Raised instead of copying the rule without its details row, which would leave the copy with a
    permanent `incomplete_commercial_terms` rule and no path to repair it. The whole copy is refused
    — `app.data.project_writes.copy_project` rolls back and the API answers `409` — because a
    half-copied project is worse than a failed copy. The message names the model and nothing else.
    """


def copy_commercial_terms(session: Session, source: Scenario, copy: Scenario) -> None:
    """Copy every rule of `source` onto `copy` — 0, 1 or N rows (SC-4-05: `scope_ref` admits more
    than the single row SC-4-01 assumed) — **and** each rule's details row, with new identifiers.

    Still one entry in `SCENARIO_CHILD_COPIERS` for the aggregate, not one per table (ADR-0004,
    addendum 2026-09-19, point 1, applied by the addendum of 2026-09-23 SC-4-01, point 1b): the
    registry's contract carries no mapping from a source row's id to the copy's, and the details row
    of *each* rule needs exactly that — built locally in this closure, the way the staffing copier
    holds its own old-to-new position map.

    **`scope_ref` is remapped, not reflected** (D-4=A, ADR-0003 addendum 2026-09-25): a rule whose
    source points at segment X must point at *the copy's own* segment named the same as X, never at
    X itself (X belongs to `source`; the composite foreign key would refuse a copy's rule pointing
    at another scenario's segment — criterion K-04). This is why `copy_scenario_delivery_segments`
    now runs *before* this function in `SCENARIO_CHILD_COPIERS` (D-4=A): by the time this closure
    runs, `copy`'s segments already exist, each with the exact `name` its source segment had
    (ADR-0016, point 5 — `UNIQUE (scenario_id, name)`), and the map below is built by joining
    source-segment-of-`source.id` to copy-segment-of-`copy.id` **by that name**. If segment names
    ever stop being unique per scenario this join stops being well-defined, and the registry's
    contract needs a shared id-mapping channel instead (`ScenarioChildCopier`'s signature would grow
    a parameter — the option the impact map named as D-4/B and left for that day, not built now
    because D-4/A is cheaper and names are unique today).

    A source rule that is itself incomplete (no details row) is copied as incomplete: the copy
    reproduces the source, it does not repair it.

    Nothing here is guarded against `approved`, and nothing needs to be: every copy is a `draft`
    (`copy_scenario` takes no status), and the source is only read.

    **A model outside `DETAIL_TABLE_BY_MODEL` is refused, never half-copied** (R-03, gate 2 of
    SC-4-01): `CommercialTermsNotCopyable`, checked for every rule *before* any of them is
    written — one uncopyable rule among several never leaves the earlier ones half-written in a
    transaction the caller (`copy_project`) is about to roll back anyway; checking first only
    makes the failure unconditional on write order rather than a new correctness property.
    Unreachable in a single-version deployment; it covers the mixed-version window of ADR-0001 in
    which a later model's rows exist before every instance runs the code that knows its details
    table (since SC-4-03 proven on a real `outcome_based` row). Every details table is copied by
    reflection here — `outcome_terms` with all its domain columns (ADR-0004, addendum 2026-09-25
    SC-4-03, point 3) — so no model needs a branch of its own, and `scope_ref` is remapped above the
    model dispatch, identically for every model. `fixed_price_terms` (SC-4-02) is copied the same
    way, with its price pair; the columns each details table does not pass on are named per model by
    `DETAIL_COLUMNS_NOT_COPIED_BY_MODEL`.
    """
    rules = list(
        session.execute(
            sa.select(CommercialTerms)
            .where(CommercialTerms.scenario_id == source.id)
            .order_by(CommercialTerms.scope_ref.is_(None).desc(), CommercialTerms.scope_ref)
        )
        .scalars()
        .all()
    )
    if not rules:
        return
    for terms in rules:
        if terms.model_type not in DETAIL_TABLE_BY_MODEL:
            raise CommercialTermsNotCopyable(
                f"The scenario's commercial terms use the model {terms.model_type!r}, which this "
                "version of the application cannot copy. Nothing was copied; retry once every "
                "instance runs a version that supports it."
            )

    # The old-segment-id -> new-segment-id map (D-4=A), built once, only if some rule needs it.
    segment_id_by_source_segment_id: dict[uuid.UUID, uuid.UUID] = {}
    if any(terms.scope_ref is not None for terms in rules):
        source_segment = aliased(ScenarioDeliverySegment)
        copy_segment = aliased(ScenarioDeliverySegment)
        segment_id_by_source_segment_id = dict(
            session.execute(
                sa.select(source_segment.id, copy_segment.id)
                .join(copy_segment, copy_segment.name == source_segment.name)
                .where(
                    source_segment.scenario_id == source.id,
                    copy_segment.scenario_id == copy.id,
                )
            ).all()
        )
    for terms in rules:
        new_terms_id = uuid.uuid4()
        new_scope_ref: uuid.UUID | None = None
        if terms.scope_ref is not None:
            new_scope_ref = segment_id_by_source_segment_id.get(terms.scope_ref)
            if new_scope_ref is None:  # pragma: no cover — D-4's ordering guarantees this
                raise RuntimeError(
                    "The source rule points at a segment that was not found among the copy's own "
                    "segments — copy_scenario_delivery_segments must run before "
                    "copy_commercial_terms in SCENARIO_CHILD_COPIERS (D-4=A)."
                )
        session.add(
            CommercialTerms(
                id=new_terms_id,
                scenario_id=copy.id,
                scope_ref=new_scope_ref,
                **values_to_copy(terms, excluded=COMMERCIAL_TERMS_COLUMNS_NOT_COPIED),
            )
        )
        session.flush()
        # The details table of **the rule's own model** — the same registry entry the refusal above
        # checked, never `tm_terms` by name (R-05, SC-4-01 gate 2). A hard-coded `tm_terms` here
        # would stop matching the refusal the day a second model joins `DETAIL_TABLE_BY_MODEL`: the
        # refusal would let the rule through and this lookup would find no details, silently
        # re-creating the incomplete copy R-03 exists to prevent.
        detail_table = DETAIL_TABLE_BY_MODEL[terms.model_type]
        not_copied = DETAIL_COLUMNS_NOT_COPIED_BY_MODEL[terms.model_type]
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
                            if column not in not_copied
                        },
                        "commercial_terms_id": new_terms_id,
                    }
                )
            )
            session.flush()
