"""SC-7-01, R-01 (Reviewer, High) — the composed `GET …/results` read must detect a scenario
approval status change across its revenue, personnel-cost, and additional-cost reads.

**R-02 (Reviewer, Low), fixed alongside R-01.** The first cut of the `409` raised the two
`rate_source` values (`live_catalog`/`approved_snapshot`) straight into the exception message, and
that message became the response's `detail` — *before* `shape_scenario_results`'s gates ever ran, so
a caller with neither `PERSONNEL_COSTS_READ` nor `can_view_personnel_costs` on this project still
received a fragment of `assumptions_used` that `SCENARIO_COST_FIELDS` withholds on every `200`.
`test_r_01_..._is_refused_not_mixed` below is parametrized over both halves of that conjunction so
the absence of `"live_catalog"`/`"approved_snapshot"` from the `409` body is proven regardless of
the caller's own permissions, not only for the caller who happens to hold every one.

**The claim.** `app.data.scenario_results.scenario_results_for_caller` obtains revenue,
personnel-cost, and additional-cost views, each with its own `session.refresh(scenario)` on a plain
`READ COMMITTED` session. Each view carries the scenario status captured by that read in
`status_at_read`. If `POST …/approve` commits its status flip and six-table snapshot between these
reads, the guard compares the captured statuses and refuses a status-dependent mixed result with
`409`.

**Real concurrency, two connections, the real endpoints** — the shape of
`tests/test_project_group_two_race.py`, adapted to a read-side race: plain reads do not contend for
a lock under `READ COMMITTED`, so there is no lock to wait on. A cursor-execute hook fires exactly
once on the rate-window query naming `selling_rate`, while a background thread commits a real
approval through the real endpoint. The subsequent personnel-cost and additional-cost reads capture
the updated status, allowing the guard to detect the change across the three read snapshots.
"""

import threading
import uuid
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event
from sqlalchemy.orm import Session

from app.core.identity import Permission
from tests.conftest import (
    IN_SCOPE_USER,
    approve_path,
    as_caller,
    caller_holding,
    make_absence_type,
    make_allocation,
    make_commercial_terms,
    make_dimension_tuple,
    make_project,
    make_rate,
    make_scenario,
    make_staffing_position,
    make_working_calendar,
)
from tests.test_scenario_results import EVERYTHING, results_path

MAR = date(2026, 3, 1)
WITHOUT_PERSONNEL_COSTS_READ = EVERYTHING - {Permission.PERSONNEL_COSTS_READ}


def _committed_scenario(engine: Engine) -> dict[str, uuid.UUID]:
    """A committed, priced and costed draft scenario, visible to every connection: 100 planned and
    billable hours at a 120/200 rate, a T&M rule, no additional cost — revenue 20000.00, base cost
    12000.00, profit 8000.00. The same rate and hours `test_scenario_results.py`'s AC-01 fixture
    uses, so a leaking mix has real, recognisable wrong numbers to show.

    A calendar and a `generates_cost=False` statutory type are attached so the paid-absence
    component resolves to a real, calculated `0.00` (`tests/test_scenario_results.py::
    _ensure_statutory_bypass`) instead of the named `no_calendar` state — needed for the contrast
    test's `profit` to be a number at all, not for the race itself (which is about `revenue` and
    the *base* cost only)."""
    with Session(bind=engine, expire_on_commit=False, future=True) as setup:
        project = make_project(
            setup, name="Race", accessible_to=(IN_SCOPE_USER,), cost_visible_to=(IN_SCOPE_USER,)
        )
        scenario = make_scenario(setup, project, name="Baseline", currency="PLN")
        calendar = make_working_calendar(setup, name="Race calendar")
        make_absence_type(
            setup,
            name="Race statutory (no cost)",
            generates_cost=False,
            generates_revenue=False,
            is_statutory_leave=True,
        )
        dimensions = make_dimension_tuple(setup, suffix=" Race", calendar=calendar)
        position = make_staffing_position(setup, scenario, dimensions, start_date=MAR)
        make_allocation(
            setup,
            position,
            period_month=MAR,
            planned_allocation_hours=Decimal("100.00"),
            billable_hours=Decimal("100.00"),
        )
        make_rate(
            setup,
            dimensions,
            effective_from=date(2026, 1, 1),
            default_cost_rate=Decimal("120.0000"),
            default_selling_rate=Decimal("200.0000"),
            currency="PLN",
        )
        make_commercial_terms(setup, scenario)
        state = {"project_id": project.id, "scenario_id": scenario.id}
        setup.commit()
    return state


@pytest.mark.parametrize(
    "caller_permissions",
    [EVERYTHING, WITHOUT_PERSONNEL_COSTS_READ],
    ids=["every_permission", "without_personnel_costs_read"],
)
def test_r_01_an_approval_landing_between_the_revenue_and_cost_reads_is_refused_not_mixed(
    committing_client: TestClient, engine: Engine, caller_permissions: frozenset[Permission]
) -> None:
    """R-01 — the approval commits, on a separate connection and thread, after the revenue read's
    rate-window query returns and before the personnel-cost and additional-cost reads capture the
    scenario status.

    Without the status guard in `app.data.scenario_results.scenario_results_for_caller` this would answer
    `200` with `profit` built from a `20000.00` live-catalogue revenue and a `12000.00`
    approved-snapshot cost (`8000.00`) — recognisable numbers, chosen so a silent mix is a wrong
    answer and not merely "some number". The guard uses each view's `status_at_read`; with it, the
    response is `409`, and none of the three figures appears anywhere in the body.

    **Parametrized over the personnel-cost gate (R-02)**: the `409` must name no `rate_source`
    (`"live_catalog"`/`"approved_snapshot"`) whether the caller holds `PERSONNEL_COSTS_READ` or
    not — that gate has not even run yet when this response is built, so it must never be the thing
    standing between a caller and those two words. The mutation is adding source values to the
    `ScenarioResultsRaceDetected` message (or endpoint error response): they should remain hidden
    for both permission sets.
    """
    state = _committed_scenario(engine)
    fired: list[str] = []
    outcome: dict[str, Any] = {}

    def approve_once_revenue_has_priced_the_month(
        connection: Any,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        if fired or "selling_rate" not in statement.lower():
            return
        fired.append(statement)

        def approve() -> None:
            try:
                outcome["approval"] = committing_client.post(
                    approve_path(state["project_id"], state["scenario_id"]),
                    headers=as_caller(IN_SCOPE_USER),
                )
            except BaseException as error:  # noqa: BLE001 — reported, never swallowed
                outcome["error"] = error

        thread = threading.Thread(target=approve, daemon=True)
        thread.start()
        outcome["thread"] = thread
        # No lock exists to wait on (two plain reads never block each other under `READ
        # COMMITTED`): joining is enough to make the approval's commit precede the statement the
        # hook is about to let through.
        thread.join(timeout=30)

    event.listen(Engine, "after_cursor_execute", approve_once_revenue_has_priced_the_month)
    try:
        with caller_holding(*caller_permissions):
            response = committing_client.get(
                results_path(state["project_id"], state["scenario_id"])
            )
    finally:
        event.remove(Engine, "after_cursor_execute", approve_once_revenue_has_priced_the_month)

    assert fired, "revenue never reached its rate-window query — nothing below is about the race"
    thread = outcome["thread"]
    thread.join(timeout=30)
    assert not thread.is_alive(), "the approval never finished — the assertions below prove nothing"
    assert "error" not in outcome, outcome.get("error")
    assert outcome["approval"].status_code == 200, outcome["approval"].text

    assert response.status_code == 409, response.text
    assert "20000.00" not in response.text
    assert "12000.00" not in response.text
    assert "8000.00" not in response.text
    assert "live_catalog" not in response.text
    assert "approved_snapshot" not in response.text


def test_r_01_contrast_no_approval_in_flight_answers_normally(
    committing_client: TestClient, engine: Engine
) -> None:
    """The contrast: the same fixture, no concurrent write — a normal `200` with the real numbers.
    What stops the test above from being satisfied by a mechanism that refuses every request."""
    state = _committed_scenario(engine)

    with caller_holding(*EVERYTHING):
        response = committing_client.get(results_path(state["project_id"], state["scenario_id"]))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["revenue"]["amount"] == "20000.00"
    assert body["personnel_cost"]["amount"] == "12000.00"
    assert body["profit"] == "8000.00"
