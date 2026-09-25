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
- `app.data.additional_cost.additional_costs_for_caller` — the additional-cost sum (SC-5-05). It
  always reads live rows, whatever the scenario's status (ADR-0004, aneks SC-5-05, point 1 — group
  2, no snapshot), but it refreshes the same `Scenario` a third time, so its frozen status is part
  of the race below all the same (SC-7-03, reviewer R-01; ADR-0015, aneks SC-7-03, point 8).

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
saying so. `additional_costs_for_caller` never branches on status itself, but it is the **last**
call to `session.refresh` the shared `Scenario`: an approval landing between the cost read and it
left the first two statuses agreeing (`draft`) while the object this module returns — and whose
`.status` becomes the response's `scenario_status` — already said `approved` (reviewer R-01 of
SC-7-03). So the guard compares all three reads' statuses, and runs only after the third.

**The guard: compare, don't prevent by locking or by raising the isolation level.** Elevating this
read to `REPEATABLE READ`/`SERIALIZABLE` was considered and rejected here — not because it would not
work in production (a session that has issued no statement yet can have its isolation level raised),
but because it silently returns a stale-but-self-consistent answer with no signal to the caller, and
it is untestable against this repository's own `client`/`db_session` fixture (the shared connection
already has an open transaction — started by the fixture's own setup writes — by the time any
endpoint code runs, and PostgreSQL refuses to change isolation level once a transaction has issued a
statement). Comparing the reads' statuses after the fact needs neither.

**What is compared: the scenario's status as each of the three reads saw it** (SC-7-03, Issue
#118; ADR-0015, aneks SC-7-03, points 3 and 8). `commercial.status_at_read`,
`cost_view.status_at_read` and `additional.status_at_read` are each copied into an immutable
`ScenarioStatus` inside their own call, right after that call's own `session.refresh`. Any two
different values can only mean the status changed between two of the reads: the transition is
one-way (ADR-0004), so no other explanation exists. **No statement after the third read refreshes
the scenario** — the only `session.refresh(scenario)` calls on this path are the three frozen ones,
and no later statement loads `Scenario` with `populate_existing` — so the `.status` the response
serialises as `scenario_status` is the one all three reads agreed on. Any future call that adds a
`session.refresh(scenario)` to this composition must join the comparison (point 8). The same rule
for every commercial model, with no branch on `model_type` (ADR-0015, aneks SC-7-03, point 2): the
cost always depends on the status, so a result composed across a status change is inconsistent
whatever the revenue's model.

- **Never `commercial.scenario.status` against `cost_view.scenario.status`.** The two are
  frequently the *same* identity-mapped `Scenario`, refreshed a second time by the later call —
  comparing its `.status` after both calls compares one value with itself and switches the guard
  off without a sound (point 3 of that addendum; `tests/test_scenario_results_status_guard.py`
  kills exactly this mutation).
- **Never `assumptions_used.rate_source`.** SC-7-01 compared the two `rate_source` values, which
  only worked while every revenue was T&M (`live_catalog`/`approved_snapshot` = a function of the
  status). The revenue's `rate_source` is a descriptor whose vocabulary depends on the commercial
  model — Story Points reports `story_points_terms` — and the cost's is a different vocabulary that
  happens to share two values (ADR-0003, aneks SC-7-03): comparing them answered `409` for every
  Story Points scenario, with no race at all. `rate_source` drives no logic here any more.

`ScenarioResultsRaceDetected` is raised instead of returning a view — a `409`
(`app.api.scenario_results`), the same "state changed, re-read and retry" vocabulary every write
path in this repository already uses for exactly this shape of conflict, applied here to a composed
read instead of a write.

**The exception message names no status and no source, on purpose** (Reviewer, SC-7-01, R-02,
Low). The race is raised — and answered as a `409` — *before* `shape_scenario_results` and its gates
(`_without_scenario_personnel_costs`, `_without_scenario_profitability`) ever run: a literal
`"approved_snapshot"`/`"live_catalog"` (or a status) in the message would be a second, ungated
channel for exactly the fact `SCENARIO_COST_FIELDS` withholds on the ordinary `200` path, reaching a
caller with neither `PERSONNEL_COSTS_READ` nor the project's `can_view_personnel_costs` flag. The
three statuses are still kept, as plain in-process attributes on the exception (`revenue_status`,
`cost_status`, `additional_cost_status`), for a server-side log statement this repository does not
yet have — never for the text handed back to the caller.

**What this guard does not cover** (named, not fixed here — Reviewer, R-02 side note): two *live*
reads of the same (unapproved) scenario that disagree because the catalogue's active rate window was
edited between them see the same status (`draft` both times) and are not caught — a narrower,
rarer risk than R-01's status flip, out of this fix's declared scope.

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
from app.models.scenario import Scenario, ScenarioStatus


class ScenarioResultsRaceDetected(RuntimeError):
    """The scenario's approval status changed while its result was being composed (Reviewer,
    SC-7-01, R-01; SC-7-03).

    Raised, never swallowed and never turned into a computed number: the revenue, the base
    personnel cost and the additional-cost reads did not all see the scenario at the same status
    (`status_at_read` on each of the three views), which can only happen if `POST …/approve`
    committed between two of the reads this module makes. `app.api.scenario_results` turns this
    into a `409` — the caller's own read is stale the moment it is answered either way, and a retry
    reads a scenario that is either still a draft throughout or already fully approved throughout,
    never a mix of the two.

    **The message (`str(self)`) names no status and no source** (Reviewer, R-02): it is raised, and
    answered as a `409`, before the personnel-cost gate ever runs, so anything the reads saw put in
    it would reach a caller who may not see this scenario's personnel costs at all — a
    second, ungated channel for the exact fact `SCENARIO_COST_FIELDS` withholds on the ordinary
    `200` path. `revenue_status`, `cost_status` and `additional_cost_status` are kept as plain
    in-process attributes instead (ADR-0015, aneks SC-7-03, points 4 and 8), for a server-side log
    statement this repository does not yet have — read them, never `str(exc)`, if that log is ever
    added.
    """

    def __init__(
        self,
        *,
        revenue_status: ScenarioStatus,
        cost_status: ScenarioStatus,
        additional_cost_status: ScenarioStatus,
    ) -> None:
        self.revenue_status = revenue_status
        self.cost_status = cost_status
        self.additional_cost_status = additional_cost_status
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

    Never constructed except by `scenario_results_for_caller`, which builds it only once `revenue`,
    `cost_view.cost` and `additional_cost` are known to have read the scenario at the same status
    (their three `status_at_read` values agree; `ScenarioResultsRaceDetected` otherwise), and no
    statement after those reads refreshes `scenario` again — so `scenario.status` is that same
    agreed status, and a
    `ScenarioResultsView` in hand is itself the proof of that agreement, not something a reader of
    this type has to re-check.
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
    they agree on the scenario's *status* is a different question, checked explicitly below — after
    the third call, because each of the three refreshes the same `Scenario` — and it can disagree
    (`ScenarioResultsRaceDetected`): an approval commits its status and snapshot in one
    transaction, but nothing serialises it against a concurrent read of unrelated rows.
    """
    commercial = commercial_terms_for_caller(session, caller, project_id, scenario_id)
    if commercial is None:
        return None
    cost_view = scenario_cost_for_caller(session, caller, project_id, scenario_id)
    if cost_view is None:  # pragma: no cover — scope agrees with the call above by construction
        return None
    additional = additional_costs_for_caller(session, caller, project_id, scenario_id)
    if additional is None:  # pragma: no cover — scope agrees with the two calls above
        return None
    # The status each of the three calls froze right after its own refresh — never `.status` of the
    # shared, thrice-refreshed `Scenario`, never `rate_source` (ADR-0015, aneks SC-7-03, points 3
    # and 8). Nothing below refreshes the scenario again.
    if not (
        commercial.status_at_read == cost_view.status_at_read == additional.status_at_read
    ):
        raise ScenarioResultsRaceDetected(
            revenue_status=commercial.status_at_read,
            cost_status=cost_view.status_at_read,
            additional_cost_status=additional.status_at_read,
        )
    return ScenarioResultsView(
        scenario=commercial.scenario,
        revenue=commercial.revenue,
        cost_view=cost_view,
        additional_cost=additional.total,
    )
