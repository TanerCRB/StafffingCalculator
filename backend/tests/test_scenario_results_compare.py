"""SC-6-02, K-01..K-05 — comparing several scenarios of the same project in one call (F-09 point 2,
Issue #87; ADR-0001/ADR-0005, addendum 2026-09-24).

`GET /projects/{project_id}/scenarios/compare?scenario_id=...&scenario_id=...` composes
`app.data.scenario_results.scenario_results_for_caller` and
`app.api.response_shaping.shape_scenario_results` once per named `scenario_id`, unchanged — the
same functions `tests/test_scenario_results.py` (K-01/K-02/K-04/K-05, this file's numbering is the
comparison endpoint's own) and `tests/test_scenario_results_access.py` (K-06) already prove against
the single-scenario endpoint. This file proves only what changes when N scenarios are named at
once: the rows are independent, the personnel-cost gate applies per row, and the two all-or-nothing
refusals (404 for scope, 409 for a race) cover the *whole* response, never a partial one.

Every scenario built here lives in **one shared project** (decision 7, gate 1, SC-6-02): all
`scenario_id`s named in one comparison request must resolve through that project's own
`Project.scenarios` collection, so the fixtures below build two scenarios per project rather than
reusing `tests/test_scenario_results.py::_full_scenario` (which creates a fresh project every
call).
"""

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

import app.api.scenario_results as scenario_results_module
from app.api.scenario_results import MAX_COMPARE_SCENARIOS, SCENARIO_RESULTS_NOT_FOUND_DETAIL
from app.core.identity import Permission
from app.data.scenario_results import ScenarioResultsRaceDetected
from app.models import Project, ProjectAccess, Scenario, ScenarioStatus
from tests.conftest import (
    IN_SCOPE_USER,
    caller_holding,
    make_additional_cost,
    make_allocation,
    make_commercial_terms,
    make_cost_category,
    make_dimension_tuple,
    make_project,
    make_rate,
    make_scenario,
    make_staffing_position,
    make_working_calendar,
)
from tests.test_scenario_results import (
    ADDITIONAL_AMOUNT,
    BILLABLE_HOURS,
    COST_RATE,
    EVERYTHING,
    MAR,
    PLANNED_HOURS,
    SELLING_RATE,
    _ensure_statutory_bypass,
    results_path,
)

WITHOUT_PERSONNEL_COSTS_READ = EVERYTHING - {Permission.PERSONNEL_COSTS_READ}
WITHOUT_RESULTS_READ = EVERYTHING - {Permission.RESULTS_READ}


def compare_path(project_id: uuid.UUID, *scenario_ids: uuid.UUID) -> str:
    query = "&".join(f"scenario_id={sid}" for sid in scenario_ids)
    return f"/projects/{project_id}/scenarios/compare" + (f"?{query}" if query else "")


def _scenario_in_project(
    session: Session,
    project: Project,
    *,
    name: str,
    planned_hours: Decimal = PLANNED_HOURS,
    billable_hours: Decimal = BILLABLE_HOURS,
    additional_amount: Decimal = ADDITIONAL_AMOUNT,
    additional_currency: str = "PLN",
    mid_month_cost_change: bool = False,
    create_commercial_terms: bool = True,
    currency: str = "PLN",
) -> Scenario:
    """`tests/test_scenario_results.py::_full_scenario`, minus the project it would otherwise create
    — every scenario here is wired into an existing, shared `project` instead (decision 7: all
    `scenario_id`s in one comparison request must belong to the one project named in the URL path).
    """
    scenario = make_scenario(session, project, name=name, currency=currency)
    calendar = make_working_calendar(session, name=f"Calendar {name}")
    dimensions = make_dimension_tuple(session, suffix=f" {name}", calendar=calendar)
    position = make_staffing_position(session, scenario, dimensions, start_date=MAR)
    make_allocation(
        session,
        position,
        period_month=MAR,
        planned_allocation_hours=planned_hours,
        billable_hours=billable_hours,
    )
    if mid_month_cost_change:
        make_rate(
            session, dimensions, effective_from=date(2026, 1, 1), effective_to=date(2026, 3, 15),
            default_cost_rate=COST_RATE, default_selling_rate=SELLING_RATE, currency=currency,
        )
        make_rate(
            session, dimensions, effective_from=date(2026, 3, 16),
            default_cost_rate=Decimal("130.0000"), default_selling_rate=SELLING_RATE,
            currency=currency,
        )
    else:
        make_rate(
            session, dimensions, effective_from=date(2026, 1, 1),
            default_cost_rate=COST_RATE, default_selling_rate=SELLING_RATE, currency=currency,
        )
    if create_commercial_terms:
        make_commercial_terms(session, scenario)
    category = make_cost_category(session, name=f"Licences {name}")
    make_additional_cost(
        session, scenario, category, amount=additional_amount, start_month=MAR,
        currency=additional_currency,
    )
    return scenario


def _compared(
    client: TestClient, project_id: uuid.UUID, *scenario_ids: uuid.UUID
) -> dict[str, Any]:
    with caller_holding(*EVERYTHING):
        response = client.get(compare_path(project_id, *scenario_ids))
    assert response.status_code == 200, response.text
    return response.json()


# --- K-01: identical to N independent single-scenario calls; only the perturbed row changes -------


def test_k_01_comparison_matches_independent_single_scenario_calls(
    client: TestClient, db_session: Session
) -> None:
    """K-01 — two scenarios of one project, compared in one call, are byte-identical, field for
    field, to what `GET …/results` already answers for each independently. Mutation killed: any
    cross-scenario summing/averaging, or a comparison-only recomputation of the four aggregate
    fields."""
    _ensure_statutory_bypass(db_session)
    project = make_project(
        db_session, name="Compare K01", accessible_to=(IN_SCOPE_USER,),
        cost_visible_to=(IN_SCOPE_USER,),
    )
    scenario_a = _scenario_in_project(db_session, project, name="A")
    scenario_b = _scenario_in_project(
        db_session, project, name="B", additional_amount=Decimal("5000.00")
    )

    with caller_holding(*EVERYTHING):
        single_a = client.get(results_path(project.id, scenario_a.id))
        single_b = client.get(results_path(project.id, scenario_b.id))
    assert single_a.status_code == 200, single_a.text
    assert single_b.status_code == 200, single_b.text

    body = _compared(client, project.id, scenario_a.id, scenario_b.id)

    assert body["results"] == [single_a.json(), single_b.json()]


def test_k_01_perturbing_one_scenario_changes_only_its_own_row(
    client: TestClient, db_session: Session
) -> None:
    """K-01, the contrast — one input on one of the two compared scenarios changes; the other row is
    byte-identical to before. Mutation killed: a shared/aggregated computation that lets one
    scenario's input leak into another's row."""
    _ensure_statutory_bypass(db_session)
    project = make_project(
        db_session, name="Compare K01b", accessible_to=(IN_SCOPE_USER,),
        cost_visible_to=(IN_SCOPE_USER,),
    )
    scenario_a = _scenario_in_project(db_session, project, name="A")
    scenario_b = _scenario_in_project(db_session, project, name="B")

    before = _compared(client, project.id, scenario_a.id, scenario_b.id)

    extra_category = make_cost_category(db_session, name="Extra K01b")
    make_additional_cost(
        db_session, scenario_a, extra_category, amount=Decimal("1000.00"), start_month=MAR,
        currency="PLN",
    )

    after = _compared(client, project.id, scenario_a.id, scenario_b.id)

    assert after["results"][1] == before["results"][1]
    assert after["results"][0] != before["results"][0]
    profit_before = Decimal(before["results"][0]["profit"])
    profit_after = Decimal(after["results"][0]["profit"])
    assert profit_after == profit_before - Decimal("1000.00")


# --- K-02: the personnel-cost gate applies identically to every row -------------------------------


def test_k_02_the_personnel_cost_gate_withholds_the_four_fields_on_every_row(
    client: TestClient, db_session: Session
) -> None:
    """K-02 — a caller without the conjunction gets `null` on the four gated fields for **all**
    rows; `revenue`/`additional_cost` stay numeric on every row either way. Contrast: the same
    caller granted the conjunction sees numbers on every row. Mutation killed: the gate applied only
    to the first row (or only once for the whole response) instead of once per row."""
    _ensure_statutory_bypass(db_session)
    project = make_project(
        db_session, name="Compare K02", accessible_to=(IN_SCOPE_USER,),
        cost_visible_to=(IN_SCOPE_USER,),
    )
    scenario_a = _scenario_in_project(db_session, project, name="A")
    scenario_b = _scenario_in_project(
        db_session, project, name="B", additional_amount=Decimal("5000.00")
    )

    with caller_holding(*WITHOUT_PERSONNEL_COSTS_READ):
        withheld = client.get(compare_path(project.id, scenario_a.id, scenario_b.id))
    assert withheld.status_code == 200, withheld.text
    withheld_rows = withheld.json()["results"]
    assert len(withheld_rows) == 2
    for row in withheld_rows:
        assert row["profit"] is None
        assert row["margin"] is None
        assert row["markup"] is None
        assert row["included_cost"] is None
        assert row["personnel_cost"]["amount"] is None
        assert row["revenue"]["amount"] is not None
        assert row["additional_cost"]["amount"] is not None
    assert "6000.00" not in withheld.text

    with caller_holding(*EVERYTHING):
        shown = client.get(compare_path(project.id, scenario_a.id, scenario_b.id))
    assert shown.status_code == 200, shown.text
    shown_rows = shown.json()["results"]
    for row in shown_rows:
        assert row["profit"] is not None
        assert row["personnel_cost"]["amount"] is not None


def test_k_02_the_gate_verdict_is_never_cached_from_the_first_row_onto_later_rows(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """K-02, white-box contrast — real `project_access`/`PERSONNEL_COSTS_READ` can never make two
    rows of one comparison disagree about the gate (decision 7 forces every named `scenario_id` into
    the *same* project, so `view.can_view_personnel_costs` is identical for every row of one
    request; `caller.has(PERSONNEL_COSTS_READ)` is decided once per request too). That invariant is
    exactly what would let a bug of this shape hide behind an all-real-permissions test forever, so
    this test forces `shape_scenario_results` itself to disagree between the two rows — something no
    real caller/project combination can do — and checks `compare_scenario_results` passes each row's
    own verdict through unchanged rather than deciding the gate once (e.g. from the first named
    scenario) and force-applying it to the rest. Mutation killed: caching the first row's gate
    verdict (open/closed) and reusing it for every later row instead of asking
    `shape_scenario_results` again per id.
    """
    _ensure_statutory_bypass(db_session)
    project = make_project(
        db_session, name="Compare K02 Independent", accessible_to=(IN_SCOPE_USER,),
        cost_visible_to=(IN_SCOPE_USER,),
    )
    scenario_a = _scenario_in_project(db_session, project, name="A")
    scenario_b = _scenario_in_project(db_session, project, name="B")

    real_shape = scenario_results_module.shape_scenario_results

    def shape_disagreeing_by_scenario(view: Any, caller: Any) -> Any:
        shaped = real_shape(view, caller)
        if view.scenario.id == scenario_a.id:
            return shaped.model_copy(
                update={"profit": None, "margin": None, "markup": None, "included_cost": None}
            )
        return shaped.model_copy(
            update={
                "profit": Decimal("777.77"),
                "margin": Decimal("1.00"),
                "markup": Decimal("1.00"),
                "included_cost": Decimal("1.00"),
            }
        )

    monkeypatch.setattr(
        scenario_results_module, "shape_scenario_results", shape_disagreeing_by_scenario
    )

    with caller_holding(*EVERYTHING):
        response = client.get(compare_path(project.id, scenario_a.id, scenario_b.id))

    assert response.status_code == 200, response.text
    rows = response.json()["results"]
    assert rows[0]["scenario_id"] == str(scenario_a.id)
    assert rows[0]["profit"] is None
    assert rows[1]["scenario_id"] == str(scenario_b.id)
    assert rows[1]["profit"] == "777.77"


# --- K-03: any named scenario outside the caller's scope refuses the whole response, same body ----


def test_k_03_a_scenario_outside_scope_named_among_others_is_the_same_404_as_alone(
    client: TestClient, db_session: Session
) -> None:
    """K-03 — the caller has **no** `project_access` on this project at all. Naming one scenario, or
    naming both, refuses the whole response with the exact same body. Contrast: granting access
    makes the same request answer `200` with both scenarios now included, in request order."""
    _ensure_statutory_bypass(db_session)
    project = make_project(db_session, name="Compare K03", accessible_to=())
    scenario_a = _scenario_in_project(db_session, project, name="A")
    scenario_b = _scenario_in_project(db_session, project, name="B")

    with caller_holding(*EVERYTHING):
        alone = client.get(compare_path(project.id, scenario_a.id))
        together = client.get(compare_path(project.id, scenario_a.id, scenario_b.id))

    assert alone.status_code == 404, alone.text
    assert together.status_code == 404, together.text
    assert alone.json() == {"detail": SCENARIO_RESULTS_NOT_FOUND_DETAIL}
    assert together.json() == alone.json()
    assert len({alone.content, together.content}) == 1

    db_session.add(
        ProjectAccess(user_id=IN_SCOPE_USER, project_id=project.id, can_view_personnel_costs=True)
    )
    db_session.flush()

    with caller_holding(*EVERYTHING):
        included = client.get(compare_path(project.id, scenario_a.id, scenario_b.id))

    assert included.status_code == 200, included.text
    included_ids = [row["scenario_id"] for row in included.json()["results"]]
    assert included_ids == [str(scenario_a.id), str(scenario_b.id)]


def test_k_03_a_nonexistent_scenario_id_among_real_ones_is_the_same_404(
    client: TestClient, db_session: Session
) -> None:
    """K-03's other half — a `scenario_id` that never existed, named alongside a real, in-scope
    scenario of the same project, still refuses the whole response, same body as naming the
    nonexistent id alone. Never a partial `200` with the real scenario's row and a marker for the
    missing one."""
    _ensure_statutory_bypass(db_session)
    project = make_project(
        db_session, name="Compare K03b", accessible_to=(IN_SCOPE_USER,),
        cost_visible_to=(IN_SCOPE_USER,),
    )
    scenario_a = _scenario_in_project(db_session, project, name="A")
    missing = uuid.uuid4()

    with caller_holding(*EVERYTHING):
        alone = client.get(compare_path(project.id, missing))
        together = client.get(compare_path(project.id, scenario_a.id, missing))

    assert alone.status_code == 404, alone.text
    assert together.status_code == 404, together.text
    assert together.json() == alone.json() == {"detail": SCENARIO_RESULTS_NOT_FOUND_DETAIL}
    assert "results" not in together.json()


# --- K-04: scenario ids from two different projects, both individually in scope, are refused ------


def test_k_04_scenario_ids_from_two_different_projects_are_refused_even_when_both_in_scope(
    client: TestClient, db_session: Session
) -> None:
    """K-04 — `scenario_b` belongs to `project_b`, not `project_a` named in the URL path, but the
    caller *does* have full access to both projects individually. Naming `scenario_b` under
    `project_a`'s compare endpoint is refused with the same `404` as an out-of-scope id (K-03),
    proven with its own test rather than assumed from K-03's mechanism: this is a structural
    consequence of decision 7 (scope resolves only through `project_a`'s own `Project.scenarios`
    collection), not of the caller lacking access anywhere.

    Contrast: `scenario_a` alone, under its own project, resolves normally.
    """
    _ensure_statutory_bypass(db_session)
    project_a = make_project(
        db_session, name="Compare K04 A", accessible_to=(IN_SCOPE_USER,),
        cost_visible_to=(IN_SCOPE_USER,),
    )
    scenario_a = _scenario_in_project(db_session, project_a, name="A")
    project_b = make_project(
        db_session, name="Compare K04 B", accessible_to=(IN_SCOPE_USER,),
        cost_visible_to=(IN_SCOPE_USER,),
    )
    scenario_b = _scenario_in_project(db_session, project_b, name="B")

    with caller_holding(*EVERYTHING):
        cross = client.get(compare_path(project_a.id, scenario_a.id, scenario_b.id))
        alone = client.get(compare_path(project_a.id, scenario_a.id))
        native = client.get(compare_path(project_b.id, scenario_b.id))

    assert cross.status_code == 404, cross.text
    assert cross.json() == {"detail": SCENARIO_RESULTS_NOT_FOUND_DETAIL}

    assert alone.status_code == 200, alone.text
    assert len(alone.json()["results"]) == 1
    assert alone.json()["results"][0]["scenario_id"] == str(scenario_a.id)

    assert native.status_code == 200, native.text
    assert native.json()["results"][0]["scenario_id"] == str(scenario_b.id)


# --- K-05: a named unresolvable state on one row does not crash or collapse the others ------------


def test_k_05_a_named_unresolvable_state_on_one_row_leaves_the_other_row_correct(
    client: TestClient, db_session: Session
) -> None:
    """K-05 — one scenario with no commercial terms (`no_commercial_terms`) compared next to a
    healthy one: the broken row states its own reason on all four aggregate fields (`"n/a"`), the
    healthy row is a real number, and the response is a `200`, never a `500` and never one shared
    sentinel for both rows. Mutation killed: an unhandled exception on the broken component
    propagating out of the endpoint, or the broken row's `"n/a"` leaking into the healthy row.
    """
    _ensure_statutory_bypass(db_session)
    project = make_project(
        db_session, name="Compare K05", accessible_to=(IN_SCOPE_USER,),
        cost_visible_to=(IN_SCOPE_USER,),
    )
    broken = _scenario_in_project(db_session, project, name="Broken", create_commercial_terms=False)
    healthy = _scenario_in_project(db_session, project, name="Healthy")

    body = _compared(client, project.id, broken.id, healthy.id)
    rows = body["results"]
    assert len(rows) == 2

    assert rows[0]["scenario_id"] == str(broken.id)
    assert rows[0]["revenue"]["state"] == "no_commercial_terms"
    assert rows[0]["profit"] == "n/a"
    assert rows[0]["margin"] == "n/a"
    assert rows[0]["markup"] == "n/a"
    assert rows[0]["included_cost"] == "n/a"

    assert rows[1]["scenario_id"] == str(healthy.id)
    assert rows[1]["profit"] == "6000.00"
    assert "n/a" not in (
        rows[1]["profit"], rows[1]["margin"], rows[1]["markup"], rows[1]["included_cost"]
    )


def test_k_05_fixing_the_broken_scenario_changes_only_its_own_row_to_a_number(
    client: TestClient, db_session: Session
) -> None:
    """K-05's contrast — the same two-scenario comparison, `broken` given its commercial terms
    afterwards: its row becomes a real number, `healthy`'s row is byte-identical to before. Proves
    the `"n/a"` above was about the missing rule and not about the endpoint being broken."""
    _ensure_statutory_bypass(db_session)
    project = make_project(
        db_session, name="Compare K05b", accessible_to=(IN_SCOPE_USER,),
        cost_visible_to=(IN_SCOPE_USER,),
    )
    broken = _scenario_in_project(db_session, project, name="Broken", create_commercial_terms=False)
    healthy = _scenario_in_project(db_session, project, name="Healthy")

    before = _compared(client, project.id, broken.id, healthy.id)
    assert before["results"][0]["profit"] == "n/a"

    make_commercial_terms(db_session, broken)

    after = _compared(client, project.id, broken.id, healthy.id)

    assert after["results"][0]["profit"] == "6000.00"
    assert after["results"][1] == before["results"][1]


# --- decision 3 (gate 1): a race on any named scenario refuses the whole response, never partial --


def test_a_race_on_any_named_scenario_refuses_the_whole_response_with_409(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gate-1 decision 3 — if `scenario_results_for_caller` raises
    `ScenarioResultsRaceDetected` for *any* named scenario, the whole comparison answers `409`, even
    though an earlier scenario in the same request already resolved successfully. Its real number
    (`6000.00`, `AC-01`'s own fixture) never appears in a partial body, and neither does the
    withheld `rate_source` vocabulary (R-02, reused unchanged from the single-scenario endpoint).

    The underlying race condition itself (two composed reads disagreeing because an approval landed
    between them) is proven with real concurrency in `tests/test_scenario_results_race.py`; this
    test proves the compare endpoint's own addition — that catching the exception for the *second*
    call discards the *first* call's already-built row rather than returning it in a partial `200`
    — with the exception forced deterministically instead of raced for.
    """
    _ensure_statutory_bypass(db_session)
    project = make_project(
        db_session, name="Compare Race", accessible_to=(IN_SCOPE_USER,),
        cost_visible_to=(IN_SCOPE_USER,),
    )
    first = _scenario_in_project(db_session, project, name="First")
    second = _scenario_in_project(db_session, project, name="Second")

    real = scenario_results_module.scenario_results_for_caller

    def racing_on_second(
        session: Session, caller: Any, project_id: uuid.UUID, scenario_id: uuid.UUID
    ) -> Any:
        if scenario_id == second.id:
            raise ScenarioResultsRaceDetected(
                revenue_status=ScenarioStatus.DRAFT,
                cost_status=ScenarioStatus.APPROVED,
                additional_cost_status=ScenarioStatus.APPROVED,
            )
        return real(session, caller, project_id, scenario_id)

    monkeypatch.setattr(scenario_results_module, "scenario_results_for_caller", racing_on_second)

    with caller_holding(*EVERYTHING):
        response = client.get(compare_path(project.id, first.id, second.id))

    assert response.status_code == 409, response.text
    assert "6000.00" not in response.text
    assert "live_catalog" not in response.text
    assert "approved_snapshot" not in response.text


# --- R-01 (Reviewer/Security Auditor, 2026-09-24): scenario_id is bounded above -------------------


def test_r_01_more_than_the_bound_is_refused_before_any_database_round_trip(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R-01 — a request naming more than `MAX_COMPARE_SCENARIOS` ids is refused by request
    validation (`422`) before `compare_scenario_results`'s body, and therefore before a single call
    to `scenario_results_for_caller`, ever runs.

    Proven by replacing that function with a spy that fails the test the instant it is called: an
    over-length request reaching it would mean the bound did not stop the request at the boundary
    Pydantic/FastAPI validates — i.e. before the database round-trip loop R-01 is about. The named
    `scenario_id`s do not need to resolve to real scenarios: a bound enforced by request validation
    never reaches a lookup, real or not — that is the whole point of this fix.
    """
    project = make_project(db_session, name="Compare R01", accessible_to=(IN_SCOPE_USER,))

    def never_called(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError(
            "scenario_results_for_caller was called: the length bound on scenario_id did not "
            "stop the request before the database round-trip loop"
        )

    monkeypatch.setattr(scenario_results_module, "scenario_results_for_caller", never_called)

    over_the_bound = [uuid.uuid4() for _ in range(MAX_COMPARE_SCENARIOS + 1)]
    with caller_holding(*EVERYTHING):
        response = client.get(compare_path(project.id, *over_the_bound))

    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail[0]["loc"][-1] == "scenario_id"
    assert detail[0]["type"] == "too_long"


def test_r_01_exactly_the_bound_is_accepted_and_reaches_the_database(
    client: TestClient, db_session: Session
) -> None:
    """R-01's contrast — exactly `MAX_COMPARE_SCENARIOS` ids is not refused by the bound: a ceiling,
    not an off-by-one trap. Real, resolvable scenarios at that count would make this test as slow as
    the fixture it does not need — the bound is about request *validation*, not about what the
    database ultimately finds — so nonexistent ids stand in: the response must clear validation
    (never `422`) and reach the normal scope check, answering the same all-or-nothing `404` K-03
    already proves for one nonexistent id, never a length-related refusal.
    """
    project = make_project(
        db_session, name="Compare R01 Bound", accessible_to=(IN_SCOPE_USER,),
        cost_visible_to=(IN_SCOPE_USER,),
    )
    at_the_bound = [uuid.uuid4() for _ in range(MAX_COMPARE_SCENARIOS)]

    with caller_holding(*EVERYTHING):
        response = client.get(compare_path(project.id, *at_the_bound))

    assert response.status_code == 404, response.text
    assert response.json() == {"detail": SCENARIO_RESULTS_NOT_FOUND_DETAIL}


# --- structural: zero and one scenario_id, and the endpoint's own permission dependency -----------


def test_zero_scenario_ids_answers_an_empty_comparison_never_defaulting_to_all_scenarios(
    client: TestClient, db_session: Session
) -> None:
    """Gate-1 decision 1 — an omitted `scenario_id` is an empty comparison, never "every scenario of
    this project". Mutation killed: a default that lists every scenario of `project_id` when the
    query string names none."""
    _ensure_statutory_bypass(db_session)
    project = make_project(
        db_session, name="Compare Zero", accessible_to=(IN_SCOPE_USER,),
        cost_visible_to=(IN_SCOPE_USER,),
    )
    _scenario_in_project(db_session, project, name="A")
    _scenario_in_project(db_session, project, name="B")

    with caller_holding(*EVERYTHING):
        response = client.get(compare_path(project.id))

    assert response.status_code == 200, response.text
    assert response.json() == {"results": []}


def test_one_scenario_id_answers_one_row_matching_the_single_scenario_endpoint(
    client: TestClient, db_session: Session
) -> None:
    """A single named `scenario_id` is not a special case: one row, identical to `GET …/results` for
    that scenario alone."""
    _ensure_statutory_bypass(db_session)
    project = make_project(
        db_session, name="Compare One", accessible_to=(IN_SCOPE_USER,),
        cost_visible_to=(IN_SCOPE_USER,),
    )
    scenario = _scenario_in_project(db_session, project, name="Solo")

    with caller_holding(*EVERYTHING):
        single = client.get(results_path(project.id, scenario.id))
    assert single.status_code == 200, single.text

    body = _compared(client, project.id, scenario.id)

    assert body["results"] == [single.json()]


def test_a_caller_without_results_read_is_refused_the_whole_compare_endpoint(
    client: TestClient, db_session: Session
) -> None:
    """The same `RESULTS_READ` dependency the single-scenario endpoint declares, applied here too:
    `403`, no figure anywhere in the body. Mutation killed: the `require_permission` dependency
    dropped from the compare route specifically (it is already proven present on the single-scenario
    route by `tests/test_scenario_results_access.py`, which this repeats for the second router)."""
    _ensure_statutory_bypass(db_session)
    project = make_project(
        db_session, name="Compare Refused", accessible_to=(IN_SCOPE_USER,),
        cost_visible_to=(IN_SCOPE_USER,),
    )
    scenario = _scenario_in_project(db_session, project, name="Refused")

    with caller_holding(*WITHOUT_RESULTS_READ):
        refused = client.get(compare_path(project.id, scenario.id))

    assert refused.status_code == 403, refused.text
    assert "6000.00" not in refused.text
    assert "20000.00" not in refused.text
