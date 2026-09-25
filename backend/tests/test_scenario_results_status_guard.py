"""SC-7-03, Issue #118 — the race guard of `GET …/results`, `GET …/compare` and `GET …/what-if`
compares the scenario's status **as each of its two reads saw it**, never `rate_source`
(ADR-0015, aneks SC-7-03; ADR-0003, aneks SC-7-03).

**The defect this file pins down.** SC-7-01's guard compared `revenue.assumptions_used.rate_source`
with `personnel_cost.assumptions_used.rate_source`. That was a stand-in for "the status changed
between the two reads" only while every revenue was T&M (`live_catalog`/`approved_snapshot`, a
function of the status). SC-4-04 added Story Points, whose revenue reports `story_points_terms` —
so every Story Points scenario answered `409` on all three endpoints, with no race at all.

**What the fix must not become.** `commercial.scenario` and `cost_view.scenario` are frequently the
*same* identity-mapped `Scenario`, refreshed by both calls (`app.data.commercial_terms._view_of`,
`app.data.personnel_cost.scenario_cost_for_caller`). A guard reading `.status` off that object after
both calls compares one value with itself and never fires. K-02/K-04 below race a real approval in
on a second connection, exactly where only a status *frozen at the moment of each read*
(`status_at_read`) can still tell the two reads apart.

**Real concurrency, the shape of `tests/test_scenario_results_race.py`** (which this task leaves
byte-for-byte unchanged, K-03): a cursor hook fires once, after the one statement only the Story
Points revenue path issues (the `story_points_terms` details row, recognised by `price_per_point` —
Story Points never issues T&M's `selling_rate` query), and commits a real approval through the real
endpoint on another thread before the revenue read hands control back to the cost read.

Criteria (Issue #118): K-01, K-02, K-04, K-05, K-06, A15-6, A15-7. K-03 is the unchanged sibling
file.
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
from app.models.scenario import Scenario
from tests.conftest import (
    IN_SCOPE_USER,
    approve_path,
    as_caller,
    caller_holding,
    make_absence_type,
    make_additional_cost,
    make_allocation,
    make_commercial_terms,
    make_cost_category,
    make_dimension_tuple,
    make_project,
    make_rate,
    make_scenario,
    make_staffing_position,
    make_story_points_terms,
    make_working_calendar,
)
from tests.test_scenario_results import (
    COST_RATE,
    EVERYTHING,
    MAR,
    SELLING_RATE,
    _ensure_statutory_bypass,
    results_path,
)
from tests.test_scenario_results_compare import _scenario_in_project, compare_path
from tests.test_scenario_what_if import SCENARIO_WHAT_IF_NOT_FOUND_DETAIL, what_if_path

WITHOUT_PERSONNEL_COSTS_READ = EVERYTHING - {Permission.PERSONNEL_COSTS_READ}

RACE_DETAIL = (
    "The scenario's approval status changed while its result was being computed. Retry the request."
)
"""The exact `409` body text before this task (`app.data.scenario_results.
ScenarioResultsRaceDetected`), duplicated as a literal on purpose: the test asserts what a client
receives, not the module under test agreeing with itself."""

WORDS_NEVER_IN_A_409 = (
    "live_catalog",
    "approved_snapshot",
    "story_points_terms",
    "what_if_hypothetical",
    "draft",
    "approved",
)
"""Neither `rate_source` vocabulary nor a status may reach the `409` (K-06, R-02): the race is
answered before the personnel-cost gate runs. Checked case-insensitively; the fixed message itself
contains none of them ("approval" is not "approved")."""

PERMISSION_SETS = pytest.mark.parametrize(
    "caller_permissions",
    [EVERYTHING, WITHOUT_PERSONNEL_COSTS_READ],
    ids=["every_permission", "without_personnel_costs_read"],
)


def _assert_generic_409(response: Any) -> None:
    """K-06 — the `409` is exactly the fixed message, and nothing either of the two reads saw."""
    assert response.status_code == 409, response.text
    assert response.json() == {"detail": RACE_DETAIL}
    lowered = response.text.lower()
    for word in WORDS_NEVER_IN_A_409:
        assert word not in lowered, word


# --- fixtures -----------------------------------------------------------------------------------


def _story_points_scenario_in_project(
    session: Session, project: Any, *, name: str
) -> Scenario:
    """`tests/test_scenario_results_compare.py::_scenario_in_project` — one position, 100 planned
    hours at cost rate 120 (base cost 12000.00), an additional cost of 2000.00 — with a **Story
    Points** rule instead of T&M: 25 accepted points × 1000.0000 = revenue 25000.00, a figure no T&M
    reading of the same plan (100 billable hours × 200 = 20000.00) could produce."""
    scenario = _scenario_in_project(session, project, name=name, create_commercial_terms=False)
    make_story_points_terms(session, scenario)
    return scenario


def _cost_visible_project(session: Session, *, name: str) -> Any:
    return make_project(
        session, name=name, accessible_to=(IN_SCOPE_USER,), cost_visible_to=(IN_SCOPE_USER,)
    )


def _committed_story_points_scenario(engine: Engine) -> dict[str, uuid.UUID]:
    """A committed Story Points draft visible to every connection — the Story Points twin of
    `tests/test_scenario_results_race.py::_committed_scenario`: 100 planned hours at cost rate 120
    (base cost 12000.00), no additional cost, revenue 25 × 1000.0000 = 25000.00, profit 13000.00."""
    with Session(bind=engine, expire_on_commit=False, future=True) as setup:
        project = _cost_visible_project(setup, name="SP Race")
        scenario = make_scenario(setup, project, name="Baseline", currency="PLN")
        calendar = make_working_calendar(setup, name="SP Race calendar")
        make_absence_type(
            setup,
            name="SP Race statutory (no cost)",
            generates_cost=False,
            generates_revenue=False,
            is_statutory_leave=True,
        )
        dimensions = make_dimension_tuple(setup, suffix=" SP Race", calendar=calendar)
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
            default_cost_rate=COST_RATE,
            default_selling_rate=SELLING_RATE,
            currency="PLN",
        )
        make_story_points_terms(setup, scenario)
        state = {"project_id": project.id, "scenario_id": scenario.id}
        setup.commit()
    return state


REVENUE_READ_TRIGGER = "price_per_point"
"""Selected only by `_story_points`' read of its details row — the last statement the Story Points
revenue read issues. Never `selling_rate`: Story Points never runs T&M's rate-window query, so a
hook keyed on it would never fire here."""

COST_READ_TRIGGER = "requested_months"
"""The leave-budget lookup (`app.data.absence_budget.budgets_for_months`, reached through
`app.data.paid_absence_cost`) — issued late in the personnel-cost read, after its own
`session.refresh` froze `status_at_read`, and before the additional-cost read's scope query and
refresh (A15-7). Not the very last statement of that read (`_live_cost_flags` follows it), but an
approval changes nothing that follows, so the race still lands in the cost → additional-cost window
— which `_get_with_an_approval_raced_in` asserts (no `additional_cost` statement before the hook).
No statement of the revenue read or of the scope queries names it."""


def _get_with_an_approval_raced_in(
    committing_client: TestClient,
    state: dict[str, uuid.UUID],
    path: str,
    caller_permissions: frozenset[Permission],
    *,
    trigger: str = REVENUE_READ_TRIGGER,
) -> Any:
    """GET `path` while a real approval commits on another connection right after the first
    statement naming `trigger` returns — by default the Story Points revenue read's last statement,
    i.e. after `commercial_terms_for_caller` froze `draft` and before `scenario_cost_for_caller`
    refreshes the scenario; with `COST_READ_TRIGGER`, after `scenario_cost_for_caller` froze `draft`
    and before `additional_costs_for_caller` refreshes it (A15-7). Asserts the hook really fired,
    that no additional-cost statement ran before it, and that the approval really committed — so
    the caller's assertions are about the race and nothing else.
    """
    fired: list[str] = []
    seen_before_firing: list[str] = []
    outcome: dict[str, Any] = {}

    def approve_once_the_trigger_statement_has_run(
        connection: Any,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        if fired:
            return
        if trigger not in statement.lower():
            seen_before_firing.append(statement.lower())
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
        # No lock exists to wait on (plain reads never block each other under `READ COMMITTED`):
        # joining makes the approval's commit precede the next statement of the request.
        thread.join(timeout=30)

    event.listen(Engine, "after_cursor_execute", approve_once_the_trigger_statement_has_run)
    try:
        with caller_holding(*caller_permissions):
            response = committing_client.get(path)
    finally:
        event.remove(Engine, "after_cursor_execute", approve_once_the_trigger_statement_has_run)

    assert fired, f"no statement named {trigger!r} — nothing below is about the race"
    assert not any("from additional_cost" in seen for seen in seen_before_firing), (
        "the additional-cost read ran before the approval — the race is not where this test says"
    )
    thread = outcome["thread"]
    thread.join(timeout=30)
    assert not thread.is_alive(), "the approval never finished — the assertions below prove nothing"
    assert "error" not in outcome, outcome.get("error")
    assert outcome["approval"].status_code == 200, outcome["approval"].text
    return response


# --- K-01 / A15-1: Story Points without any concurrent write answers 200 on all three paths ------


def test_k_01_story_points_results_answers_200_with_price_times_points_and_a_numeric_profit(
    client: TestClient, db_session: Session
) -> None:
    """K-01 — `GET …/results` for a Story Points draft with a cost rate and an allocation: `200`,
    `revenue` = `price_per_point × accepted_points` (1000.0000 × 25 = 25000.00), `profit` a number:
    25000.00 − (12000.00 base + 0.00 paid absence + 2000.00 additional) = 11000.00.

    Mutation killed: restoring the `rate_source` comparison in `app.data.scenario_results`
    (`story_points_terms` ≠ `live_catalog` → `409`)."""
    _ensure_statutory_bypass(db_session)
    project = _cost_visible_project(db_session, name="SP Results")
    scenario = _story_points_scenario_in_project(db_session, project, name="SP")

    with caller_holding(*EVERYTHING):
        response = client.get(results_path(project.id, scenario.id))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["revenue"]["state"] == "calculated"
    assert body["revenue"]["amount"] == "25000.00"
    assert body["revenue"]["assumptions_used"]["model_type"] == "story_points"
    assert body["personnel_cost"]["amount"] == "12000.00"
    assert body["included_cost"] == "14000.00"
    assert body["profit"] == "11000.00"


def test_k_01_compare_of_a_story_points_and_a_t_and_m_scenario_answers_200_with_two_rows(
    client: TestClient, db_session: Session
) -> None:
    """K-01 — `GET …/compare?scenario_id=<SP>&scenario_id=<T&M>`: `200`, two rows, in the named
    order, each with its own model's revenue (25000.00 Story Points, 20000.00 T&M) and profit.

    Mutation killed: the `rate_source` comparison restored in `app.data.scenario_results` — the
    compare endpoint calls the same function per row, so the SP row's `409` refuses the whole
    response."""
    _ensure_statutory_bypass(db_session)
    project = _cost_visible_project(db_session, name="SP Compare")
    story_points = _story_points_scenario_in_project(db_session, project, name="SP")
    time_and_material = _scenario_in_project(db_session, project, name="TM")

    with caller_holding(*EVERYTHING):
        response = client.get(compare_path(project.id, story_points.id, time_and_material.id))

    assert response.status_code == 200, response.text
    rows = response.json()["results"]
    assert [row["scenario_id"] for row in rows] == [str(story_points.id), str(time_and_material.id)]
    assert rows[0]["revenue"]["amount"] == "25000.00"
    assert rows[0]["revenue"]["assumptions_used"]["model_type"] == "story_points"
    assert rows[0]["profit"] == "11000.00"
    assert rows[1]["revenue"]["amount"] == "20000.00"
    assert rows[1]["revenue"]["assumptions_used"]["model_type"] == "time_and_material"
    assert rows[1]["profit"] == "6000.00"


def test_k_01_story_points_what_if_answers_200(client: TestClient, db_session: Session) -> None:
    """K-01 — `GET …/what-if?salary_raise_percent=10` for the same Story Points draft: `200`, the
    real revenue untouched (25000.00), the cost raised (100 h × 132 = 13200.00), profit
    25000.00 − 13200.00 − 2000.00 = 9800.00.

    Mutation killed: the `rate_source` comparison restored in `app.data.scenario_what_if` alone
    (mutated separately from `app.data.scenario_results`: the two guards are two copies)."""
    _ensure_statutory_bypass(db_session)
    project = _cost_visible_project(db_session, name="SP WhatIf")
    scenario = _story_points_scenario_in_project(db_session, project, name="SP")

    with caller_holding(*EVERYTHING):
        response = client.get(what_if_path(project.id, scenario.id, "10"))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["revenue"]["amount"] == "25000.00"
    assert body["personnel_cost"]["amount"] == "13200.00"
    assert body["profit"] == "9800.00"


# --- A15-6: disagreeing rate_source values with an agreeing status are not a race ----------------


def test_a15_6_disagreeing_rate_sources_with_an_agreeing_status_are_not_a_race(
    client: TestClient, db_session: Session
) -> None:
    """A15-6 — the two `rate_source` values disagree and the answer is still `200`, both while the
    scenario is a draft throughout (`story_points_terms` vs `live_catalog`) and once it is approved
    throughout (`story_points_terms` vs `approved_snapshot`, the cost now read from the snapshot).
    The same fact K-01 relies on, named on its own and extended to the approved side (ADR-0015,
    aneks SC-7-03, point 5: approved through both reads is not a race on `…/results`).

    Mutation killed: any guard still keyed on `rate_source` equality, in either status."""
    _ensure_statutory_bypass(db_session)
    project = _cost_visible_project(db_session, name="SP A15-6")
    scenario = _story_points_scenario_in_project(db_session, project, name="SP")

    with caller_holding(*EVERYTHING):
        draft = client.get(results_path(project.id, scenario.id))
        approval = client.post(
            approve_path(project.id, scenario.id), headers=as_caller(IN_SCOPE_USER)
        )
        approved = client.get(results_path(project.id, scenario.id))

    assert draft.status_code == 200, draft.text
    draft_body = draft.json()
    assert draft_body["revenue"]["assumptions_used"]["rate_source"] == "story_points_terms"
    assert draft_body["personnel_cost"]["assumptions_used"]["rate_source"] == "live_catalog"

    assert approval.status_code == 200, approval.text
    assert approved.status_code == 200, approved.text
    approved_body = approved.json()
    assert approved_body["revenue"]["assumptions_used"]["rate_source"] == "story_points_terms"
    assert approved_body["personnel_cost"]["assumptions_used"]["rate_source"] == (
        "approved_snapshot"
    )
    assert approved_body["revenue"]["amount"] == "25000.00"
    assert approved_body["personnel_cost"]["amount"] == "12000.00"


# --- K-02 / K-06: a real race on Story Points `…/results` is still a generic 409 -----------------


@PERMISSION_SETS
def test_k_02_an_approval_raced_between_the_story_points_revenue_and_cost_reads_is_a_409(
    committing_client: TestClient, engine: Engine, caller_permissions: frozenset[Permission]
) -> None:
    """K-02 (A15-2, A15-3) with K-06 (A15-5) — the approval commits on a second connection after
    the Story Points revenue read froze `draft` and before the cost read refreshes the scenario:
    `409`, never a `200` mixing a 25000.00 revenue with an approved-snapshot 12000.00 cost.

    Mutations killed (both answer `200`): (a) the guard comparing `commercial.scenario.status`
    with `cost_view.scenario.status` — one identity-mapped object refreshed by both reads, so both
    read `approved`; (b) the guard removed from `app.data.scenario_results`.

    Parametrized over the personnel-cost gate (K-06, as R-02): the body is exactly the fixed
    message and names no `rate_source` and no status, whether or not the caller may see personnel
    costs — the gate has not run yet when this response is built."""
    state = _committed_story_points_scenario(engine)

    response = _get_with_an_approval_raced_in(
        committing_client,
        state,
        results_path(state["project_id"], state["scenario_id"]),
        caller_permissions,
    )

    _assert_generic_409(response)
    assert "25000.00" not in response.text
    assert "12000.00" not in response.text
    assert "13000.00" not in response.text


def test_k_02_contrast_no_approval_in_flight_answers_200(
    committing_client: TestClient, engine: Engine
) -> None:
    """K-02's contrast — the same committed fixture, no concurrent write: `200` with the real
    numbers. What stops K-02 from being satisfied by a guard that refuses every Story Points
    scenario (the very defect of Issue #118)."""
    state = _committed_story_points_scenario(engine)

    with caller_holding(*EVERYTHING):
        response = committing_client.get(results_path(state["project_id"], state["scenario_id"]))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["revenue"]["amount"] == "25000.00"
    assert body["personnel_cost"]["amount"] == "12000.00"
    assert body["profit"] == "13000.00"


# --- K-04 / K-06: a real race on what-if is a 409, never the 404 of "approved throughout" --------


@PERMISSION_SETS
def test_k_04_an_approval_raced_between_the_what_if_real_reads_is_a_409_not_a_404(
    committing_client: TestClient, engine: Engine, caller_permissions: frozenset[Permission]
) -> None:
    """K-04 (ADR-0015, aneks SC-7-03, point 5 — Q5/A) with K-06 — the same race on
    `GET …/what-if?salary_raise_percent=10`: `409` with the fixed message, not the `404` a scenario
    approved through both reads answers (the contrasts below).

    Mutation killed: removing the guard from `app.data.scenario_what_if` alone — the cost read then
    sees `approved`, the endpoint's own `draft`-only refusal answers `None`, and the response is
    `404` instead of `409`. The same `404` results from comparing the shared `Scenario`'s `.status`
    there."""
    state = _committed_story_points_scenario(engine)

    response = _get_with_an_approval_raced_in(
        committing_client,
        state,
        what_if_path(state["project_id"], state["scenario_id"], "10"),
        caller_permissions,
    )

    _assert_generic_409(response)
    assert response.json() != {"detail": SCENARIO_WHAT_IF_NOT_FOUND_DETAIL}
    assert "25000.00" not in response.text
    assert "13200.00" not in response.text


def test_k_04_contrast_no_approval_in_flight_answers_200(
    committing_client: TestClient, engine: Engine
) -> None:
    """K-04's first contrast — no concurrent write: `200`, cost raised to 13200.00, profit
    25000.00 − 13200.00 = 11800.00."""
    state = _committed_story_points_scenario(engine)

    with caller_holding(*EVERYTHING):
        response = committing_client.get(
            what_if_path(state["project_id"], state["scenario_id"], "10")
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["revenue"]["amount"] == "25000.00"
    assert body["personnel_cost"]["amount"] == "13200.00"
    assert body["profit"] == "11800.00"


def test_k_04_contrast_approved_before_the_request_answers_404(
    committing_client: TestClient, engine: Engine
) -> None:
    """K-04's second contrast — approved *before* the what-if request, so both reads see `approved`:
    the guard has nothing to catch and the endpoint's own scope rule answers `404` (ADR-0015,
    point 5), never `409`. Together with the raced test above this is what makes K-04 tell `409`
    from `404` rather than accept either."""
    state = _committed_story_points_scenario(engine)

    with caller_holding(*EVERYTHING):
        approval = committing_client.post(
            approve_path(state["project_id"], state["scenario_id"]),
            headers=as_caller(IN_SCOPE_USER),
        )
        response = committing_client.get(
            what_if_path(state["project_id"], state["scenario_id"], "10")
        )

    assert approval.status_code == 200, approval.text
    assert response.status_code == 404, response.text
    assert response.json() == {"detail": SCENARIO_WHAT_IF_NOT_FOUND_DETAIL}


# --- A15-7: an approval between the cost read and the additional-cost read is a 409 too ----------


@PERMISSION_SETS
@pytest.mark.parametrize("endpoint", ["results", "what_if"])
def test_a15_7_an_approval_raced_between_the_cost_and_additional_cost_reads_is_a_409(
    committing_client: TestClient,
    engine: Engine,
    caller_permissions: frozenset[Permission],
    endpoint: str,
) -> None:
    """A15-7 (reviewer R-01 of SC-7-03; ADR-0015, aneks SC-7-03, point 8) — the approval commits
    after the personnel-cost read froze `draft` and before the additional-cost read refreshes the
    shared `Scenario`. Revenue and cost both froze `draft`, so a guard comparing only those two
    stays silent; the third refresh then flips the shared object to `approved`, which is what
    `…/results` serialises as `scenario_status` (`"Approved"` next to `rate_source: live_catalog`)
    and what what-if's `_worked_months` branches on (a hypothesis costed on the approval snapshot,
    served as `200`). Both endpoints must answer the generic `409` instead (K-06 parametrization).

    Mutation killed (each module separately): the third status left out of the comparison —
    `…/results` and what-if then answer `200`. Also red on the round-1 code of SC-7-03, where the
    additional-cost read ran after a two-status guard."""
    state = _committed_story_points_scenario(engine)
    path = (
        results_path(state["project_id"], state["scenario_id"])
        if endpoint == "results"
        else what_if_path(state["project_id"], state["scenario_id"], "10")
    )

    response = _get_with_an_approval_raced_in(
        committing_client, state, path, caller_permissions, trigger=COST_READ_TRIGGER
    )

    _assert_generic_409(response)
    assert "25000.00" not in response.text
    assert "12000.00" not in response.text
    assert "13200.00" not in response.text


# --- K-05 / A15-4: T&M responses are byte-for-byte what they were on 1739f1e ----------------------


def _literal_t_and_m_fixture(session: Session) -> dict[str, str]:
    """`tests/test_scenario_results.py::_full_scenario`'s AC-01 scenario, built inline so every
    generated id the response echoes is in hand for a full-equality literal: 100 planned/billable
    hours at 120/200 PLN, a T&M rule, a one-off additional cost of 2000.00 in March."""
    _ensure_statutory_bypass(session)
    project = _cost_visible_project(session, name="Literal")
    scenario = make_scenario(session, project, name="Baseline", currency="PLN")
    calendar = make_working_calendar(session, name="Calendar Literal")
    dimensions = make_dimension_tuple(session, suffix=" Literal", calendar=calendar)
    position = make_staffing_position(session, scenario, dimensions, start_date=MAR)
    make_allocation(
        session,
        position,
        period_month=MAR,
        planned_allocation_hours=Decimal("100.00"),
        billable_hours=Decimal("100.00"),
    )
    rate = make_rate(
        session, dimensions, effective_from=date(2026, 1, 1),
        default_cost_rate=COST_RATE, default_selling_rate=SELLING_RATE, currency="PLN",
    )
    make_commercial_terms(session, scenario)
    category = make_cost_category(session, name="Licences Literal")
    cost = make_additional_cost(
        session, scenario, category, amount=Decimal("2000.00"), start_month=MAR, currency="PLN"
    )
    return {
        "project_id": str(project.id),
        "scenario_id": str(scenario.id),
        "position_id": str(position.id),
        "rate_id": str(rate.id),
        "category_id": str(category.id),
        "cost_id": str(cost.id),
    }


def _expected_t_and_m_results(ids: dict[str, str]) -> dict[str, Any]:
    """The full `GET …/results` body for `_literal_t_and_m_fixture`, as served by 1739f1e."""
    return {
        "scenario_id": ids["scenario_id"],
        "scenario_status": "Draft",
        "revenue": {
            "state": "calculated",
            "amount": "20000.00",
            "currency": "PLN",
            "assumptions_used": {
                "model_type": "time_and_material",
                "hours_source": "billable_hours",
                "vendor_axis": "internal",
                "rate_source": "live_catalog",
                "rate_windows": [
                    {
                        "source_rate_id": ids["rate_id"],
                        "effective_from": "2026-01-01",
                        "effective_to": None,
                        "default_selling_rate": "200.0000",
                        "currency": "PLN",
                    }
                ],
                "unresolved_months": [],
                "currencies": ["PLN"],
            },
        },
        "personnel_cost": {
            "state": "calculated",
            "cost_basis": "base",
            "amount": "12000.00",
            "currency": "PLN",
            "assumptions_used": {
                "hours_source": "planned_allocation_hours",
                "vendor_axis": "internal",
                "rate_source": "live_catalog",
                "rate_windows": [
                    {
                        "source_rate_id": ids["rate_id"],
                        "effective_from": "2026-01-01",
                        "effective_to": None,
                        "default_cost_rate": "120.0000",
                        "currency": "PLN",
                    }
                ],
                "unresolved_months": [],
                "currencies": ["PLN"],
            },
            "paid_absence_state": "calculated",
            "paid_absence_amount": "0.00",
            "paid_absence_budget_amount": "0.00",
            "paid_absence_currency": "PLN",
            "paid_absence_assumptions_used": {
                "hours_source": "paid_absences_and_leave_budget_top_up",
                "months": [
                    {
                        "position_id": ids["position_id"],
                        "period_month": "2026-03-01",
                        "manual_hours": "0.00",
                        "budget_hours": "0.00",
                        "budget_part": "statutory_leave_not_cost_generating",
                    }
                ],
                "unresolved_months": [],
                "currencies": ["PLN"],
            },
        },
        "additional_cost": {
            "state": "calculated",
            "amount": "2000.00",
            "currency": "PLN",
            "assumptions_used": {
                "costs": [
                    {
                        "cost_id": ids["cost_id"],
                        "position_id": None,
                        "category_id": ids["category_id"],
                        "category_name": "Licences Literal",
                        "funding_source": "internal",
                        "cost_type": "one_off",
                        "amount": "2000.0000",
                        "currency": "PLN",
                        "months": ["2026-03-01"],
                    }
                ],
                "periods": [{"period_month": "2026-03-01", "cost_ids": [ids["cost_id"]]}],
                "currencies": ["PLN"],
            },
        },
        "included_cost": "14000.00",
        "profit": "6000.00",
        "margin": "30.00",
        "markup": "42.86",
    }


def test_k_05_t_and_m_results_body_is_the_1739f1e_literal(
    client: TestClient, db_session: Session
) -> None:
    """K-05 (A15-4) — `GET …/results` for a T&M draft: the whole body equals the literal, field for
    field (verified green against the production code of 1739f1e as well — see the SC-7-03 report).
    """
    ids = _literal_t_and_m_fixture(db_session)

    with caller_holding(*EVERYTHING):
        response = client.get(results_path(ids["project_id"], ids["scenario_id"]))

    assert response.status_code == 200, response.text
    assert response.json() == _expected_t_and_m_results(ids)


def test_k_05_t_and_m_compare_body_is_the_1739f1e_literal(
    client: TestClient, db_session: Session
) -> None:
    """K-05 (A15-4) — `GET …/compare` naming the same T&M scenario: one row, the literal."""
    ids = _literal_t_and_m_fixture(db_session)

    with caller_holding(*EVERYTHING):
        response = client.get(compare_path(ids["project_id"], ids["scenario_id"]))

    assert response.status_code == 200, response.text
    assert response.json() == {"results": [_expected_t_and_m_results(ids)]}


def test_k_05_t_and_m_what_if_body_is_the_1739f1e_literal(
    client: TestClient, db_session: Session
) -> None:
    """K-05 (A15-4) — `GET …/what-if?salary_raise_percent=10` for the same T&M scenario: the whole
    body equals the literal (cost rate 120 × 1.1 = 132.00000, cost 13200.00, profit 4800.00)."""
    ids = _literal_t_and_m_fixture(db_session)

    with caller_holding(*EVERYTHING):
        response = client.get(what_if_path(ids["project_id"], ids["scenario_id"], "10"))

    expected = _expected_t_and_m_results(ids)
    expected["personnel_cost"]["amount"] = "13200.00"
    expected["personnel_cost"]["assumptions_used"]["rate_source"] = "what_if_hypothetical"
    expected["personnel_cost"]["assumptions_used"]["rate_windows"][0]["default_cost_rate"] = (
        "132.00000"
    )
    expected["included_cost"] = "15200.00"
    expected["profit"] = "4800.00"
    expected["margin"] = "24.00"
    expected["markup"] = "31.58"
    expected["salary_raise_percent"] = "10"

    assert response.status_code == 200, response.text
    assert response.json() == expected
