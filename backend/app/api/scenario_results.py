"""A scenario's whole-life profit, margin and markup (F-10, SC-7-01; ADR-0004 "fits"; Issue #12).

`GET /projects/{project_id}/scenarios/{scenario_id}/results` — `RESULTS_READ`.

**`RESULTS_READ` on the endpoint, `PERSONNEL_COSTS_READ` on four of its fields** (ADR-0005, aneks
2026-09-24), the same split SC-5-01 introduced: a caller holding `RESULTS_READ` without
`PERSONNEL_COSTS_READ` (or without `project_access.can_view_personnel_costs` for this project) gets
a `200` whose `profit`, `margin`, `markup` and `included_cost` are `null` — a refusal of *those
fields*, never of the resource. `revenue` and `additional_cost` are never gated (they are not
personnel costs by nature — SC-4-01, SC-5-05) and `personnel_cost` carries the SC-5-01/SC-5-06 gate
it already had. The conjunction is applied in
`app.api.response_shaping.shape_scenario_results`/`_without_scenario_profitability`.

**A router of its own**, like every nested scenario path here: the address carries both identifiers
because the scope predicate lives on the project (ADR-0001, addendum 2026-09-19), and a verb on the
commercial, personnel-cost or additional-cost router would put a mixed figure under a module whose
name promises a single, independent calculation (F-06: independent calculation per model).

**Every "nothing here for you" answers with one body** (`SCENARIO_RESULTS_NOT_FOUND_DETAIL`): a
project outside the caller's scope, a project that does not exist and a scenario of another project
are one `404` — the same shape every nested scenario path already has (criterion K-03).

**A `409` this endpoint alone can answer** (Reviewer, SC-7-01, R-01): the revenue and the base
personnel cost are read by two separate statements (`app.data.scenario_results`), and an approval
committing *between* them would otherwise price one against the live catalogue and cost the other
against the frozen snapshot — one `profit` built from two different moments of the same scenario.
`app.data.scenario_results.ScenarioResultsRaceDetected` (raised by `refuse_a_status_race`) names
that exact disagreement — reads that saw the scenario at two statuses that matter (SC-7-03: the
statuses the reads froze; the revenue's `rate_source` only classifies whether the revenue read's
status counts, and is never compared with the cost's — ADR-0015, aneks SC-7-03, point 2); this
endpoint answers it as a `409`, the same "the scenario changed since it was read, retry"
vocabulary every write path in this repository already uses, applied here to a read instead of a
write. Never a `200` with numbers mixed from two moments, and never a `500`.

**`str(race)` is generic, on purpose** (Reviewer, SC-7-01, R-02, Low): this `except` runs *before*
`shape_scenario_results` and its gates, so the `409` body is a second, ungated channel out of this
endpoint — `ScenarioResultsRaceDetected` never puts a `rate_source` (`"live_catalog"` /
`"approved_snapshot"`) or a status in its own message for exactly that reason, and this handler must
keep passing `str(race)` through unchanged rather than building a more detailed message from the
exception's `revenue_status`/`cost_status`/`additional_cost_status` attributes.

**Read only.** There is nothing to write: the three components are written under their own
permissions (`COMMERCIAL_WRITE`, `STAFFING_WRITE`).

## `GET /projects/{project_id}/scenarios/compare` — N scenarios of the same project, one call
(SC-6-02, F-09 pkt 2; ADR-0001/ADR-0005, aneks 2026-09-24)

**A comparison, not a new calculation.** `compare_scenario_results` calls
`scenario_results_for_caller`/`shape_scenario_results` once per named `scenario_id`, unchanged —
the same functions, the same gates, the same race guard `read_scenario_results` above already uses.
Nothing here sums, nets or averages a field across the compared scenarios (ADR-0005 aneks SC-7-01
pt.6, reconfirmed for this endpoint): the response is N independent rows.

**Scope, by construction, never a second check.** `scenario_id` is repeated as a query parameter;
each one is resolved by the *same* call this module already makes, which resolves scope through
`project_for_caller`/`Project.scenarios` (ADR-0001, addendum 2026-09-19) via
`app.data.staffing.scenario_in_scope`. A `scenario_id` from a project other than the one in the URL
path is not even nameable in this request's scope — the collection it would have to appear in
belongs to a different project — so it answers the same `404` as a nonexistent id, not a distinct
code path.

**All-or-nothing, both ways (gate-1 decision, not a partial-success shape).** The first named
`scenario_id` that does not resolve for the caller refuses the *whole* response with `404`
(`SCENARIO_RESULTS_NOT_FOUND_DETAIL`) — identical body to naming only that one id. The first named
`scenario_id` whose composed read raises `ScenarioResultsRaceDetected` refuses the whole response
with `409` — never a partial `200` with a per-row marker for either failure.

**Zero or one `scenario_id`.** Neither is rejected specially: zero yields `{"results": []}`, one
behaves exactly like the single-scenario endpoint's numbers wrapped in one row. The gate-1 decision
deliberately does not mandate a minimum count.

**Bounded above** (R-01, Reviewer and Security Auditor, independently, 2026-09-24 — the same shape
of defect `MAX_ALLOCATION_MONTHS` fixes in `app.api.schemas.staffing`, one router over). Each named
`scenario_id` costs `scenario_results_for_caller` three-or-more sequential round trips on the one
`Session` this request holds (`commercial_terms_for_caller`, `scenario_cost_for_caller`,
`additional_costs_for_caller`) — unbounded, a query string naming thousands of ids serialises
thousands of round trips before any response is sent, on an endpoint `RESULTS_READ`'s placeholder
membership (ADR-0005) makes reachable by any caller today. `scenario_id`'s `Query(...,
max_length=MAX_COMPARE_SCENARIOS)` below refuses an over-length list the same way FastAPI/Pydantic
already refuses one (`422`, `too_long`) before this module's handler — hence before the database
round-trip loop — ever runs, the same division of labour `StaffingAllocationEntry`'s bound relies
on.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.api.response_shaping import shape_scenario_results
from app.api.schemas.scenario_results import ScenarioResults, ScenarioResultsComparison
from app.core.identity import CallerIdentity, Permission
from app.data.scenario_results import ScenarioResultsRaceDetected, scenario_results_for_caller
from app.db.session import get_session

router = APIRouter(
    prefix="/projects/{project_id}/scenarios/{scenario_id}/results", tags=["scenario-results"]
)

MAX_COMPARE_SCENARIOS = 50
"""The most `scenario_id`s one comparison request may name (R-01, Reviewer/Security Auditor,
2026-09-24 — the same shape of bound as `app.api.schemas.staffing.MAX_ALLOCATION_MONTHS`).

F-09 describes "≥3" as typical PM usage, not an API ceiling — this is not that number. It is a
plausibility bound on work a client chooses the size of: a project's scenarios are variations of
one plan, not an open-ended catalogue, so comparing a few dozen at once is already generous
headroom over any real use this task's Issue describes, while keeping the per-request cost
bounded — at three-or-more sequential round trips per id (`scenario_results_for_caller`), 50 ids is
at most ~150 round trips, not an unbounded number chosen by whoever sends the request."""

compare_router = APIRouter(
    prefix="/projects/{project_id}/scenarios", tags=["scenario-results"]
)
"""A router of its own, not a route on `router` above: `router`'s prefix already carries
`{scenario_id}/results`, a single-scenario address this endpoint's `/compare` is not — it names a
*set* of scenarios via a repeated query parameter instead (gate-1 decision, SC-6-02)."""

SCENARIO_RESULTS_NOT_FOUND_DETAIL = "Scenario not found."
"""The same wording as `app.api.scenarios.SCENARIO_NOT_FOUND_DETAIL`: the thing that may not exist
is the scenario. The indistinguishability is not maintained by this constant alone —
`app.data.scenario_results` returns the same `None` for every such case."""


@router.get(
    "",
    response_model=ScenarioResults,
    summary=(
        "Read a scenario's whole-life profit, margin and markup, next to the revenue, personnel "
        "cost and additional cost they are built from"
    ),
    responses={
        404: {"description": SCENARIO_RESULTS_NOT_FOUND_DETAIL},
        409: {
            "description": "Refused: the scenario's approval status changed while this endpoint "
            "was composing revenue, personnel cost and additional cost into one result. Retry."
        },
    },
)
def read_scenario_results(
    project_id: uuid.UUID,
    scenario_id: uuid.UUID,
    caller: Annotated[CallerIdentity, Depends(require_permission(Permission.RESULTS_READ))],
    session: Annotated[Session, Depends(get_session)],
) -> ScenarioResults:
    """The scenario-wide result — or a `404` that says nothing about whether the scenario exists.

    - **200** — for a **draft**, priced and costed against the live catalogue; for an **approved**
      scenario, against the windows and rates frozen at its approval (AC-04), exactly as the three
      components' own endpoints already answer. `profit`, `margin`, `markup` and `included_cost`
      are `"n/a"` when at least one of the four sources (`revenue`, the base personnel cost, the
      paid-absence cost, the additional cost) is not itself `calculated` — each source still names
      its own state on its own field, never folded into one shared sentinel. They are `null`
      instead of a number or `"n/a"` when the caller may not see personnel costs of this scenario's
      project (K-06) — `revenue` and `additional_cost` stay visible either way.
    - **404** — the scenario is not the caller's, does not exist, or belongs to another project.
    - **409** — the scenario's approval status changed *while this endpoint was reading it*
      (`ScenarioResultsRaceDetected`, R-01): never a `200` mixing a live-catalogue revenue with an
      approved-snapshot cost.
    - **403** — the permission dependency (`RESULTS_READ`), before the database.
    """
    try:
        view = scenario_results_for_caller(session, caller, project_id, scenario_id)
    except ScenarioResultsRaceDetected as race:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(race)) from None
    if view is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=SCENARIO_RESULTS_NOT_FOUND_DETAIL
        )
    return shape_scenario_results(view, caller)


@compare_router.get(
    "/compare",
    response_model=ScenarioResultsComparison,
    summary=(
        "Compare the whole-life profit, margin and markup of several scenarios of the same "
        "project, in one call"
    ),
    responses={
        404: {
            "description": "Refused: at least one named scenario_id is not the caller's, does "
            "not exist, or belongs to another project. The whole response is refused, never a "
            "partial result with the missing one marked."
        },
        409: {
            "description": "Refused: at least one named scenario's approval status changed "
            "while this endpoint was composing its result. Retry. The whole response is "
            "refused, never a partial result with the racing one marked."
        },
        422: {
            "description": f"Refused: more than {MAX_COMPARE_SCENARIOS} scenario_id values "
            "named in one request (R-01) — refused by request validation, before any database "
            "round trip."
        },
    },
)
def compare_scenario_results(
    project_id: uuid.UUID,
    caller: Annotated[CallerIdentity, Depends(require_permission(Permission.RESULTS_READ))],
    session: Annotated[Session, Depends(get_session)],
    scenario_id: Annotated[
        list[uuid.UUID],
        Query(
            default_factory=list,
            max_length=MAX_COMPARE_SCENARIOS,
            description="Repeat to name each scenario to compare, e.g. "
            "?scenario_id=<uuid>&scenario_id=<uuid>. All must belong to project_id; omitted "
            "means an empty comparison, never 'every scenario of this project'. Refused "
            f"(422) above {MAX_COMPARE_SCENARIOS} ids (R-01).",
        ),
    ],
) -> ScenarioResultsComparison:
    """N independent rows, one per named `scenario_id`, in request order — never an aggregate
    across them (ADR-0005 aneks SC-7-01 pt.6).

    Composes `app.data.scenario_results.scenario_results_for_caller` and
    `app.api.response_shaping.shape_scenario_results` once per id, unchanged — the same functions,
    same gates and same race guard `read_scenario_results` above uses for one scenario. No new
    query, no new arithmetic (F-06, ADR-0002 not engaged — see module docstring).

    - **200** — every named `scenario_id` resolved; `results` carries one `ScenarioResults` row per
      id, each exactly what the single-scenario endpoint would answer for it, in the same order as
      requested. Personnel-cost gating (K-06) is applied per row, independently — a caller without
      `PERSONNEL_COSTS_READ` ∧ `can_view_personnel_costs` for this project sees `null` on
      `profit`/`margin`/`markup`/`included_cost` on *every* row; `revenue`/`additional_cost` stay
      visible on every row either way.
    - **404** — the first named `scenario_id` that is not the caller's, does not exist, or belongs
      to a project other than `project_id`: refuses the whole response with the same body a request
      naming only that one id would get (`SCENARIO_RESULTS_NOT_FOUND_DETAIL`). Scenarios of another
      project cannot be named at all in this request's scope by construction — path fixes
      `project_id`, and scope resolves through that project's own `Project.scenarios` collection.
    - **409** — the first named `scenario_id` whose composed read finds the scenario's approval
      status disagreeing between its two internal reads (`ScenarioResultsRaceDetected`): refuses the
      whole response, never a partial `200` with the racing scenario marked.
    - **403** — the permission dependency (`RESULTS_READ`), before the database.
    - **422** — more than `MAX_COMPARE_SCENARIOS` (R-01) named `scenario_id` values: refused by
      request validation, before this function body — hence before the database round-trip loop —
      ever runs.
    """
    rows: list[ScenarioResults] = []
    for one_scenario_id in scenario_id:
        try:
            view = scenario_results_for_caller(session, caller, project_id, one_scenario_id)
        except ScenarioResultsRaceDetected as race:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(race)) from None
        if view is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=SCENARIO_RESULTS_NOT_FOUND_DETAIL
            )
        rows.append(shape_scenario_results(view, caller))
    return ScenarioResultsComparison(results=rows)
