"""The only path by which a scenario's whole-life profit, margin and markup are read (F-10; SC-7-01,
Issue #12; ADR-0004 "fits", ADR-0005 aneks 2026-09-24).

**This module calls three already-proven reads and computes nothing of its own.** No `select`, no
scope predicate and no cost/revenue formula of its own:

- `app.data.commercial_terms.commercial_terms_for_caller` — scope, the scenario and the revenue
  (SC-4-01, F-06);
- `app.data.personnel_cost.scenario_cost_for_caller` — the base personnel cost, the paid-absence
  component and the caller's `can_view_personnel_costs` flag for this scenario's project (SC-5-01,
  SC-5-06). Its whole `ScenarioCostView` is carried through unchanged (not flattened into loose
  fields), so `app.api.response_shaping._without_scenario_personnel_costs` — the gate SC-5-01
  already proved — can be applied to this endpoint's `personnel_cost` field with the exact same
  view object it was written for, rather than a second view type this task would have to prove the
  gate against again;
- `app.data.additional_cost.additional_costs_for_caller` — the additional-cost sum (SC-5-05), never
  part of the race below: it always reads live rows, whatever the scenario's status (ADR-0004, aneks
  SC-5-05, point 1 — group 2, no snapshot).

Each of the three already resolves scope through `project_for_caller` (ADR-0001, addendum
2026-09-19) and already chooses the live catalogue or the approval snapshot by the scenario's own
status (AC-04) — this module inherits both for free by calling them, rather than opening a **fifth**
statement that reads the scenario or its snapshot again.

**Named, not accepted: three separate reads of one scenario is an unverified foundation, not a risk
the impact map signed off on** (Reviewer, SC-7-01, R-01, High — corrected after an earlier revision
of this docstring overstated it as accepted). `commercial_terms_for_caller` and
`scenario_cost_for_caller` each call their own `session.refresh(scenario)`, on a plain `READ
COMMITTED` session (no isolation level is raised anywhere in `app.db.session`/`app.core.config`):
if `POST …/approve` commits its status flip *between* those two refreshes, the first has already
priced the revenue against the live catalogue and the second costs against the frozen snapshot —
one `profit` built from two different moments of the same scenario, with nothing in the response
saying so. `additional_costs_for_caller` cannot fall out of step this way (it never branches on
status), so the guard below checks only the pair that can.

**The guard: compare, don't prevent by locking or by raising the isolation level.** Elevating this
read to `REPEATABLE READ`/`SERIALIZABLE` was considered and rejected here — not because it would not
work in production (a session that has issued no statement yet can have its isolation level raised),
but because it silently returns a stale-but-self-consistent answer with no signal to the caller, and
it is untestable against this repository's own `client`/`db_session` fixture (the shared connection
already has an open transaction — started by the fixture's own setup writes — by the time any
endpoint code runs, and PostgreSQL refuses to change isolation level once a transaction has issued a
statement). Comparing `assumptions_used.rate_source` after the fact needs neither: `rate_source` is
`LIVE_CATALOG` or `APPROVED_SNAPSHOT`, decided once inside each of the two calls from the scenario's
status *at the moment each one read it*, and copied into an immutable `str` on the returned answer —
so it stays a true record of what that call saw even though `commercial.scenario` and
`cost_view.scenario` are frequently the *same* identity-mapped object, refreshed a second time by
the later call. Two different values can only mean the status changed between the two reads: the
transition is one-way (ADR-0004), so no other explanation exists. `ScenarioResultsRaceDetected` is
raised instead of returning a view — a `409` (`app.api.scenario_results`), the same "state changed,
re-read and retry" vocabulary every write path in this repository already uses for exactly this
shape of conflict, applied here to a composed read instead of a write.

**The exception message names no source, on purpose** (Reviewer, SC-7-01, R-02, Low). The two
`rate_source` values are raised — and answered as a `409` — *before* `shape_scenario_results` and
its gates (`_without_scenario_personnel_costs`, `_without_scenario_profitability`) ever run: a
literal `"approved_snapshot"`/`"live_catalog"` in the message would be a second, ungated channel for
exactly the fact `SCENARIO_COST_FIELDS` withholds on the ordinary `200` path, reaching a caller with
neither `PERSONNEL_COSTS_READ` nor the project's `can_view_personnel_costs` flag. The two values
are still kept, as plain attributes on the exception (`revenue_source`, `cost_source`), for a
server-side log statement this repository does not yet have — never for the text handed back to
the caller.

**What this guard does not cover** (named, not fixed here — Reviewer, R-02 side note): two *live*
reads of the same (unapproved) scenario that disagree because the catalogue's active rate window was
edited between them resolve to the same `rate_source` (`live_catalog` both times) and are not
caught — a narrower, rarer risk than R-01's status flip, out of this fix's declared scope.

**Never imports `app.domain.revenue*`, `app.domain.personnel_cost` or `app.domain.additional_cost`
beyond the *answer* types the three already export for exactly this composition** — no rate window,
no catalogue row and no SQL predicate of its own. The formula itself lives in
`app.domain.scenario_results`, not here (this module has no `Decimal` arithmetic of its own).
"""

import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.identity import CallerIdentity
from app.data.additional_cost import additional_costs_for_caller
from app.data.commercial_terms import commercial_terms_for_caller
from app.data.personnel_cost import ScenarioCostView, scenario_cost_for_caller
from app.domain.additional_cost import AdditionalCostAnswer
from app.domain.revenue import RevenueAnswer
from app.models.scenario import Scenario


class ScenarioResultsRaceDetected(RuntimeError):
    """The scenario's approval status changed while its result was being composed (Reviewer,
    SC-7-01, R-01).

    Raised, never swallowed and never turned into a computed number: `revenue` and the base
    personnel cost disagree on `assumptions_used.rate_source` (`live_catalog` vs
    `approved_snapshot`), which can only happen if `POST …/approve` committed between the two reads
    this module makes. `app.api.scenario_results` turns this into a `409` — the caller's own
    read is stale the moment it is answered either way, and a retry reads a scenario that is either
    still a draft throughout or already fully approved throughout, never a mix of the two.

    **The message (`str(self)`) names no source** (Reviewer, R-02): it is raised, and answered as a
    `409`, before the personnel-cost gate ever runs, so a literal `rate_source` in it would reach a
    caller who may not see this scenario's personnel costs at all — a second, ungated channel for
    the exact fact `SCENARIO_COST_FIELDS` withholds on the ordinary `200` path. `revenue_source` and
    `cost_source` are kept as plain attributes instead, for a server-side log statement this
    repository does not yet have — read them, never `str(exc)`, if that log is ever added.
    """

    def __init__(self, *, revenue_source: str, cost_source: str) -> None:
        self.revenue_source = revenue_source
        self.cost_source = cost_source
        super().__init__(
            "The scenario's approval status changed while its result was being computed. Retry "
            "the request."
        )


@dataclass(frozen=True)
class ScenarioResultsView:
    """One scenario's three components, resolved in one composed read.

    A value object for the reason `ScenarioCostView` is one: the shaping layer never receives a
    `Session` and must never grow a query of its own.

    `cost_view` is `app.data.personnel_cost.scenario_cost_for_caller`'s own answer, carried through
    whole: it is the one object `_without_scenario_personnel_costs` already knows how to gate, and
    handing it through unchanged is what lets this endpoint reuse that gate instead of asserting a
    second one that says the same thing.

    Never constructed except by `scenario_results_for_caller`, which builds it only once `revenue`
    and `cost_view.cost` are known to have read the scenario at the same status
    (`ScenarioResultsRaceDetected` otherwise) — so a `ScenarioResultsView` in hand is itself the
    proof of that agreement, not something a reader of this type has to re-check.
    """

    scenario: Scenario
    revenue: RevenueAnswer
    cost_view: ScenarioCostView
    additional_cost: AdditionalCostAnswer


def scenario_results_for_caller(
    session: Session, caller: CallerIdentity, project_id: uuid.UUID, scenario_id: uuid.UUID
) -> ScenarioResultsView | None:
    """The three components of one scenario's result — or `None`, with no way to tell why.

    `None` is "no such scenario *for this caller*" (the same `scenario_id`/`project_id` scope every
    nested scenario path uses); a scenario whose profit cannot be stated is a view whose components
    each carry their own named state, never `None` and never `0`.

    The three calls share one `(caller, project_id, scenario_id)` and therefore the same scope
    predicate (`project_for_caller`): if the first finds nothing in scope, the other two cannot
    either, so this function stops at the first `None` rather than running three queries to learn
    the same fact three times. The two calls after the first are asserted to agree on *scope* — a
    `None` from either at that point would mean the scope check itself disagreed with itself between
    two calls inside one request, which nothing in this repository's write model can cause. Whether
    they agree on the scenario's *status* is a different question, checked explicitly below, and it
    can disagree (`ScenarioResultsRaceDetected`) — an approval commits its status and snapshot in
    one transaction, but nothing serialises it against a concurrent read of unrelated rows.
    """
    commercial = commercial_terms_for_caller(session, caller, project_id, scenario_id)
    if commercial is None:
        return None
    cost_view = scenario_cost_for_caller(session, caller, project_id, scenario_id)
    if cost_view is None:  # pragma: no cover — scope agrees with the call above by construction
        return None
    revenue_source = commercial.revenue.assumptions_used.rate_source
    cost_source = cost_view.cost.assumptions_used.rate_source
    if revenue_source != cost_source:
        raise ScenarioResultsRaceDetected(revenue_source=revenue_source, cost_source=cost_source)
    additional = additional_costs_for_caller(session, caller, project_id, scenario_id)
    if additional is None:  # pragma: no cover — scope agrees with the two calls above
        return None
    return ScenarioResultsView(
        scenario=commercial.scenario,
        revenue=commercial.revenue,
        cost_view=cost_view,
        additional_cost=additional.total,
    )
