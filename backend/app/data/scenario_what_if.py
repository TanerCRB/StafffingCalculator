"""Salary-raise "what-if": a scenario's whole-life result recomputed against a substituted, never
persisted, cost-rate structure (F-09 pt.3; SC-6-04, Issue #88; ADR-0015).

**Substitution into existing domain functions, never a new calculation** (ADR-0015, point 1). This
module calls the same readers/formulas every other personnel-cost path already calls —
`app.data.personnel_cost._worked_months`, `app.data.paid_absence_cost.paid_absence_months`,
`app.domain.personnel_cost.base_personnel_cost`, `app.domain.paid_absence_cost.paid_absence_cost`
(ADR-0013, addendum 2026-09-24: "jedna funkcja, cztery miejsca") — on a rate structure this module
built with `dataclasses.replace`, never on arithmetic written here. It also calls
`app.data.commercial_terms.commercial_terms_for_caller`,
`app.data.personnel_cost.scenario_cost_for_caller` and
`app.data.additional_cost.additional_costs_for_caller` for scope, the **real** revenue/cost/
additional-cost answers and the race guard — the same three calls
`app.data.scenario_results.scenario_results_for_caller` makes, in the same order, never that
function itself (ADR-0015, point 4): it would build a `ScenarioResultsView`, whose documented
invariant ("`revenue` and `cost_view.cost` agree on the scenario's real status") a *hypothetical*
cost view does not represent. This composition reads the same real sources separately and applies
the same guard, `app.data.scenario_results.refuse_a_status_race` — one function, never a second
copy of its rule — to the statuses the three real reads froze right after their own refreshes
(`status_at_read`), never to the shared `Scenario`'s `.status` (SC-7-03, Issue #118; ADR-0015,
addendum SC-7-03, points 2 and 4) — only *after* which does it touch a rate.

**Zero persistence, structurally** (ADR-0015, point 2). `WorkedMonth`, `MonthCostRate`,
`CostRateWindow` are plain `@dataclass(frozen=True)` (`app.domain.personnel_cost`), never
SQLAlchemy-mapped or session-tracked — `dataclasses.replace` on one cannot attach to a session's
identity map. This module issues no statement of its own: every `session.execute` happens inside
the reused readers, and nothing here calls `Session.add`/`flush`/`merge`/`commit`/`delete`. The one
ORM-mapped object on this path, `Scenario`, arrives already read by `scenario_cost_for_caller`
(`.status`, `.currency`) and is only ever read here too, never assigned to.

**The raise touches the shared rate dictionary once, before either consumer of it runs**
(ADR-0015, point 3). `_raised_months` substitutes every resolved `WorkedMonth.rate` and
`_raised_rates_by_month` rebuilds the `(position_id, period_month) -> MonthCostRate | None`
mapping from *that* substituted list — the same mapping shape `scenario_cost_for_caller` feeds
`paid_absence_months` — so `base_personnel_cost` and `paid_absence_cost` are costed from one
consistent, raised rate per (position, month), never from two rates that could disagree.

**The multiplier is applied to the `Decimal` rate itself, with no rounding of its own**
(ADR-0015, point 8): `cost_rate * (1 + salary_raise_percent / 100)`. Nothing here calls
`app.core.money.round_money` or `Decimal.quantize` — `base_personnel_cost`'s own single rounding
point, at the end of its sum, is still the only one the total passes through. At `0`,
`Decimal("0") / Decimal("100")` is the exact `Decimal("0")` and the multiplier is the exact
`Decimal("1")`, so a `0%` raise reproduces every rate figure bit for bit (K-01).

**`rate_source` becomes `WHAT_IF_HYPOTHETICAL`, and only on the cost side** (ADR-0015, point 4).
`commercial.revenue` — the real, unsubstituted revenue `commercial_terms_for_caller` read — is
carried through unchanged and untouched by the raise: this module never substitutes a selling rate
and imports nothing of the revenue formula beyond the answer type every other reader of it already
imports (F-06; rule 10 of the Invariant Guardian).

**Scope: `draft` only** (ADR-0015, point 5). A scenario that is, or becomes mid-request, `approved`
answers `None` here — the same "no such scenario for this caller" every scope failure in this
module already answers with, never a distinct error. The check runs *after* the inherited race
guard and *before* any substitution (ADR-0015, addendum SC-7-03, points 2, 5 and 8):

- an approval landing between the cost and the additional-cost read, for every model, or between
  the revenue and the cost read of a status-dependent revenue (T&M, or no rule at all), is caught
  by `refuse_a_status_race` first — a `409` (`app.api.scenario_what_if`), exactly as
  `scenario_results_for_caller` catches it;
- an approval landing between the revenue and the cost read of a revenue that does not depend on
  the status (Story Points, Outcome-based) is not a race: the revenue is the same before and after
  it, cost and additional cost both saw `approved`, and the request is equivalent to one read
  wholly after the approval — `None`, like a scenario `approved` throughout, for which every read
  agrees and the guard has nothing to catch.

**The additional-cost read runs before the guard, on purpose** (SC-7-03, reviewer R-01; ADR-0015,
addendum SC-7-03, point 8). It is the last of the three `session.refresh(scenario)` calls on this
path. Called after the guard (as before SC-7-03), an approval landing between the cost read and it
left the compared statuses agreeing on `draft` while its refresh flipped the shared `Scenario` to
`approved` — and `_worked_months(session, scenario)` below then branched on that live `approved`
and served a hypothesis computed on the approval snapshot. With the third status frozen and
compared with the cost's (rule (b)), and no `session.refresh(scenario)` anywhere after it
(`_worked_months` and `paid_absence_months` issue plain reads that never load `Scenario`), every
branch on `scenario.status` below sees the status the cost read was computed at.

**What "never" means here, precisely.** The *real*, unsubstituted revenue and cost —
`commercial.revenue`, `cost_view.cost` and the additional cost, the same figures that caller's own
`GET …/results` would show them for this scenario — are already read and computed in-process by the
three calls above *before* this status check runs, approved-snapshot rates included when the
scenario turns out to be approved: refusing scope earlier, before either read, would cost this
module a second statement duplicating `scenario_in_scope`. What never happens for a non-`draft`
scenario is the *hypothetical* half: `_worked_months`, the rate substitution and
`base_personnel_cost`/`paid_absence_cost` on a raised rate never run, and nothing computed above
this line is ever returned to a caller — `None` here, not a `ScenarioWhatIfView` built from it.
"Never a compute" is therefore "never a **served** compute against a substituted rate", not a claim
that the real components are never evaluated in memory.
"""

import uuid
from dataclasses import dataclass, replace
from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.identity import CallerIdentity
from app.data.additional_cost import additional_costs_for_caller
from app.data.commercial_terms import commercial_terms_for_caller
from app.data.paid_absence_cost import paid_absence_months
from app.data.personnel_cost import ScenarioCostView, _worked_months, scenario_cost_for_caller
from app.data.scenario_results import refuse_a_status_race
from app.domain.additional_cost import AdditionalCostAnswer
from app.domain.paid_absence_cost import fully_loaded_paid_absence_cost, paid_absence_cost
from app.domain.personnel_cost import (
    WHAT_IF_HYPOTHETICAL,
    MonthCostRate,
    WorkedMonth,
    base_personnel_cost,
    fully_loaded_personnel_cost,
)
from app.domain.revenue import RevenueAnswer
from app.models.scenario import Scenario, ScenarioStatus


@dataclass(frozen=True)
class ScenarioWhatIfView:
    """One scenario's whole-life result recomputed against a hypothetical salary raise.

    `revenue` and `additional_cost` are the **real**, unsubstituted answers the raise never
    touches. `cost_view` is a `ScenarioCostView` like every other reader of the base cost produces
    — except its `cost`/`paid_absence` are the hypothetical answers; its `user_id` and
    `can_view_personnel_costs` are the **real** ones `scenario_cost_for_caller` resolved, so the
    existing personnel-cost gate (`app.api.response_shaping._without_scenario_personnel_costs`/
    `_without_scenario_profitability`) applies to a hypothetical figure exactly as it applies to a
    real one — never a second gate built for this endpoint alone.
    """

    scenario: Scenario
    revenue: RevenueAnswer
    cost_view: ScenarioCostView
    additional_cost: AdditionalCostAnswer
    salary_raise_percent: Decimal


def _raised_rate(rate: MonthCostRate | None, multiplier: Decimal) -> MonthCostRate | None:
    """One (position, month)'s resolved rate, raised — or `None`, unchanged, when the month was
    never resolved: an unresolved month stays unresolved under any raise (K-01's mixed-state
    contrast). `dataclasses.replace`, never a `MonthCostRate` built field by field: every field this
    type carries but `cost_rate` (the currency, every window's id and dates, and — since SC-5-02 —
    `surcharge_percent`/`includes_surcharge`) survives untouched.

    **This is the whole of SC-5-02's criterion K-07.** `surcharge_percent` is a *percentage of*
    `cost_rate` (ADR-0013, addendum 2026-09-25, Q4), so raising `cost_rate` here and leaving
    `surcharge_percent` exactly as `replace` already leaves every field it is not told to change is
    what makes the fully loaded cost's surcharge amount rise proportionally with the base, with no
    line added to this function and no new substitution path in this module (ADR-0015, addendum
    2026-09-25 SC-5-02).
    """
    if rate is None:
        return None
    return replace(
        rate,
        cost_rate=rate.cost_rate * multiplier,
        windows=tuple(
            replace(window, cost_rate=window.cost_rate * multiplier) for window in rate.windows
        ),
    )


def _raised_months(months: list[WorkedMonth], multiplier: Decimal) -> list[WorkedMonth]:
    """Every worked month with its rate raised — `dataclasses.replace` on `WorkedMonth` itself, so
    `position_id`, `period_month` and `planned_allocation_hours` are the same values the real read
    produced and only `rate` differs."""
    return [replace(month, rate=_raised_rate(month.rate, multiplier)) for month in months]


def _raised_rates_by_month(
    months: list[WorkedMonth],
) -> dict[tuple[uuid.UUID, object], MonthCostRate | None]:
    """The `(position_id, period_month) -> rate` mapping `paid_absence_months` needs, built from the
    **already-raised** months — the same shape `scenario_cost_for_caller` builds from the real ones,
    so the paid-absence component is costed at literally the same raised rate the base cost is
    (ADR-0015, point 3; ADR-0013, addendum 2026-09-24)."""
    return {(month.position_id, month.period_month): month.rate for month in months}


def scenario_what_if_salary_raise_for_caller(
    session: Session,
    caller: CallerIdentity,
    project_id: uuid.UUID,
    scenario_id: uuid.UUID,
    *,
    salary_raise_percent: Decimal,
) -> ScenarioWhatIfView | None:
    """The scenario's whole-life result under a hypothetical salary raise — or `None`.

    `None` covers several indistinguishable cases, on purpose (K-03, K-06): no such scenario for
    this caller, a scenario belonging to another project, and a scenario that is (or becomes
    mid-request) `approved` — the last one this endpoint's own scope rule (ADR-0015, point 5),
    layered on top of the scope every nested scenario path already shares.
    """
    commercial = commercial_terms_for_caller(session, caller, project_id, scenario_id)
    if commercial is None:
        return None
    cost_view = scenario_cost_for_caller(session, caller, project_id, scenario_id)
    if cost_view is None:  # pragma: no cover — scope agrees with the call above by construction
        return None
    # Before the guard, not after it: its refresh is the last one on this path (addendum SC-7-03,
    # point 8), so its status has to be frozen and compared before anything branches on it.
    additional = additional_costs_for_caller(session, caller, project_id, scenario_id)
    if additional is None:  # pragma: no cover — scope agrees with the two calls above
        return None
    # The same function as `/results`, never a second copy of the rule (ADR-0015, addendum SC-7-03,
    # points 2 and 4): an approval landing between the real reads above is still a race whenever
    # the rule compares those two reads, whatever this endpoint goes on to compute from a
    # hypothetical rate. Every status is the one a real read froze right after its own refresh —
    # never `cost_view.scenario.status`, the identity-mapped object all three reads refresh — and
    # the raise has not been applied yet, so `WHAT_IF_HYPOTHETICAL` never reaches the guard.
    refuse_a_status_race(
        revenue_source=commercial.revenue.assumptions_used.rate_source,
        revenue_status=commercial.status_at_read,
        cost_status=cost_view.status_at_read,
        additional_cost_status=additional.status_at_read,
    )

    scenario = cost_view.scenario
    if scenario.status != ScenarioStatus.DRAFT:
        # `approved` at the cost and the additional-cost read (throughout, or — for a revenue that
        # does not depend on the status — since just after the revenue read; the guard above had
        # nothing to catch) — refused the same way as out of scope, never a distinct
        # error and never a SERVED compute against a substituted rate (ADR-0015, point 5): the
        # real revenue/cost above were read in-process (the module docstring's "what 'never'
        # means" note), but the hypothetical substitution below this line never runs.
        return None

    multiplier = Decimal("1") + salary_raise_percent / Decimal("100")
    _source, months = _worked_months(session, scenario)
    raised_months = _raised_months(list(months), multiplier)
    raised_rates = _raised_rates_by_month(raised_months)
    raised_absence_months = paid_absence_months(session, scenario, raised_rates)
    hypothetical_cost = base_personnel_cost(
        raised_months, rate_source=WHAT_IF_HYPOTHETICAL, scenario_currency=scenario.currency
    )
    hypothetical_paid_absence = paid_absence_cost(
        raised_absence_months, scenario_currency=scenario.currency
    )
    # The third and fourth consumers of the raised rate dictionary (SC-5-02, criterion K-07):
    # `fully_loaded_personnel_cost`/`fully_loaded_paid_absence_cost` read `surcharge_percent` off
    # the identical, already-raised `MonthCostRate` objects above — no separate substitution, no
    # second read of the catalogue (`_raised_rate`'s docstring carries the full argument).
    hypothetical_fully_loaded_cost = fully_loaded_personnel_cost(
        raised_months, rate_source=WHAT_IF_HYPOTHETICAL, scenario_currency=scenario.currency
    )
    hypothetical_fully_loaded_paid_absence = fully_loaded_paid_absence_cost(
        raised_absence_months, scenario_currency=scenario.currency
    )
    hypothetical_cost_view = ScenarioCostView(
        user_id=cost_view.user_id,
        scenario=scenario,
        can_view_personnel_costs=cost_view.can_view_personnel_costs,
        cost=hypothetical_cost,
        paid_absence=hypothetical_paid_absence,
        status_at_read=cost_view.status_at_read,
        fully_loaded_cost=hypothetical_fully_loaded_cost,
        fully_loaded_paid_absence=hypothetical_fully_loaded_paid_absence,
        # The fixed-amount component (SC-5-03) is carried through **unraised**: a salary raise is a
        # multiplier on `catalog_default_rates.default_cost_rate` (ADR-0015, point 3), and a
        # `fixed_amount` position reads no such rate at all (it is the position's own stated figure,
        # `app.domain.fixed_amount_cost`) — there is nothing for this what-if to substitute, and
        # that includes the fully loaded/surcharge pair SC-5-02 added: a fixed amount has none to
        # begin with (crossed with SC-5-02, see `ScenarioCostView.fixed_amount`'s own docstring in
        # `app.data.personnel_cost`). Named here rather than silently inherited: a future what-if
        # that *should* touch fixed amounts is a decision this module does not make on its own.
        fixed_amount=cost_view.fixed_amount,
    )
    return ScenarioWhatIfView(
        scenario=scenario,
        revenue=commercial.revenue,
        cost_view=hypothetical_cost_view,
        additional_cost=additional.total,
        salary_raise_percent=salary_raise_percent,
    )
