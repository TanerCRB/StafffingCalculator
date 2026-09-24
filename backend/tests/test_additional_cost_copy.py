"""SC-5-05, K-07 — a copy of a scenario carries both halves of its additional costs (ADR-0014,
point 10, Q-6 = A; ADR-0004, aneks SC-5-05, point 4; AC-02).

The two halves live in two places, and each has a canary that fails **on its own**:

- the costs **with no position** are copied by `copy_scenario_additional_costs`, an entry of their
  own in `SCENARIO_CHILD_COPIERS`;
- the costs **attached to a position** are copied inside `copy_staffing_positions`, the one place
  that holds the old-to-new position ids.

Everything is copied through the real `POST /projects/{id}/copy` — the one entry point of the copy
mechanism an API caller has today.
"""

import threading
import uuid
from datetime import date
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event
from sqlalchemy.orm import Session

from app.data.additional_cost import copy_scenario_additional_costs
from app.data.project_writes import SCENARIO_CHILD_COPIERS
from app.data.staffing import ADDITIONAL_COST_COLUMNS_NOT_COPIED
from app.models import AdditionalCost, Scenario, ScenarioStatus, StaffingPosition
from tests.conftest import (
    IN_SCOPE_USER,
    additional_cost_path,
    additional_costs_path,
    as_caller,
    make_additional_cost,
    make_allocation,
    make_cost_category,
    make_dimension_tuple,
    make_project,
    make_scenario,
    make_staffing_position,
    wait_until_a_lock_request_is_pending,
)

MAR = date(2026, 3, 1)
APR = date(2026, 4, 1)
JUN = date(2026, 6, 1)
HEADERS = as_caller(IN_SCOPE_USER)

COPIED_ADDITIONAL_COST_FIELDS = (
    "category_id",
    "amount",
    "currency",
    "cost_type",
    "start_month",
    "end_month",
    "funding_source",
)
"""Spelled out here rather than derived from the model: the drift guard below compares the two, so
a column added to `additional_cost` later fails it until somebody decides which side it is on."""


def _copy_project(client: TestClient, project_id: uuid.UUID) -> uuid.UUID:
    response = client.post(f"/projects/{project_id}/copy", headers=HEADERS)
    assert response.status_code == 201, response.text
    return uuid.UUID(response.json()["id"])


def _only_scenario(session: Session, project_id: uuid.UUID) -> Scenario:
    session.expire_all()
    return session.execute(
        sa.select(Scenario).where(Scenario.project_id == project_id)
    ).scalar_one()


def _costs_of(session: Session, scenario_id: uuid.UUID) -> list[AdditionalCost]:
    session.expire_all()
    return list(
        session.execute(
            sa.select(AdditionalCost)
            .where(AdditionalCost.scenario_id == scenario_id)
            .order_by(AdditionalCost.amount)
        ).scalars()
    )


def _positions_by_start(session: Session, scenario_id: uuid.UUID) -> dict[date, uuid.UUID]:
    return {
        position.start_date: position.id
        for position in session.execute(
            sa.select(StaffingPosition).where(StaffingPosition.scenario_id == scenario_id)
        ).scalars()
    }


def _values(cost: AdditionalCost) -> tuple[Any, ...]:
    return tuple(getattr(cost, field) for field in COPIED_ADDITIONAL_COST_FIELDS)


def _total(client: TestClient, project_id: uuid.UUID, scenario_id: uuid.UUID) -> dict[str, Any]:
    response = client.get(additional_costs_path(project_id, scenario_id), headers=HEADERS)
    assert response.status_code == 200, response.text
    return response.json()["additional_cost"]


def _source(session: Session, *, status: ScenarioStatus = ScenarioStatus.DRAFT) -> dict[str, Any]:
    """A scenario with two positions (starting in March and in April, so the copies can be told
    apart), a scenario-level cost, and one cost on each position — three different amounts."""
    project = make_project(session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(session, project, name="Baseline", currency="EUR", status=status)
    first = make_staffing_position(
        session, scenario, make_dimension_tuple(session, suffix=" 1"), start_date=MAR
    )
    second = make_staffing_position(
        session, scenario, make_dimension_tuple(session, suffix=" 2"), start_date=APR
    )
    make_allocation(session, first, period_month=MAR)
    make_allocation(session, second, period_month=APR)
    category = make_cost_category(session)
    return {
        "project": project,
        "scenario": scenario,
        "first": first,
        "second": second,
        "scenario_cost": make_additional_cost(
            session, scenario, category, amount=Decimal("1000.0000"), start_month=MAR,
        ),
        "first_cost": make_additional_cost(
            session, scenario, category, amount=Decimal("300.0000"), start_month=MAR,
            end_month=JUN, position=first, funding_source="rebilled_to_client",
        ),
        "second_cost": make_additional_cost(
            session, scenario, category, amount=Decimal("450.0000"), start_month=APR,
            position=second,
        ),
    }


def test_k_07_a_copy_carries_both_halves_with_new_ids_pointing_at_its_own_positions(
    client: TestClient, db_session: Session
) -> None:
    """K-07 (D-6) — the whole claim in one fixture:

    1. the copy has **three** costs, none of them with a source id;
    2. the scenario-level cost is copied with `position_id` `NULL`;
    3. each position cost points at **the copy of its own position** — the copied March position
       for the March cost, the copied April position for the April cost — never at a source
       position and never at the *other* position of the copy;
    4. every copied value (category, amount, currency, type, period, funding) is the source's;
    5. the copy's sum equals the source's sum (1000 + 4 × 300 + 450 = 2650.00);
    6. the source is unchanged — same ids, same values, same sum.
    """
    source = _source(db_session)
    source_ids = {source[key].id for key in ("scenario_cost", "first_cost", "second_cost")}
    source_values = {
        cost.id: _values(cost) for cost in _costs_of(db_session, source["scenario"].id)
    }

    copy_project_id = _copy_project(client, source["project"].id)
    copy = _only_scenario(db_session, copy_project_id)
    copied = _costs_of(db_session, copy.id)
    copied_positions = _positions_by_start(db_session, copy.id)

    assert len(copied) == 3
    assert {cost.id for cost in copied}.isdisjoint(source_ids)
    by_amount = {cost.amount: cost for cost in copied}
    assert by_amount[Decimal("1000.0000")].position_id is None
    assert by_amount[Decimal("300.0000")].position_id == copied_positions[MAR]
    assert by_amount[Decimal("450.0000")].position_id == copied_positions[APR]
    assert {cost.position_id for cost in copied}.isdisjoint(
        {source["first"].id, source["second"].id}
    )
    assert sorted(_values(cost) for cost in copied) == sorted(source_values.values())

    source_total = _total(client, source["project"].id, source["scenario"].id)
    copy_total = _total(client, copy_project_id, copy.id)
    assert (copy_total["state"], copy_total["amount"]) == ("calculated", "2650.00")
    assert (copy_total["amount"], copy_total["currency"]) == (
        source_total["amount"], source_total["currency"]
    )

    assert {
        cost.id: _values(cost) for cost in _costs_of(db_session, source["scenario"].id)
    } == source_values


def test_k_07_canary_the_scenario_level_half_is_copied_by_its_registry_entry(
    client: TestClient, db_session: Session
) -> None:
    """K-07's first canary — a source with **only** a scenario-level cost.

    Red on its own when `copy_scenario_additional_costs` is taken out of `SCENARIO_CHILD_COPIERS`
    (the copy then has no cost at all), and silent about the other half — which is the point of
    having two canaries rather than one.
    """
    project = make_project(db_session, name="Scenario half", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(db_session, project, name="Baseline", currency="EUR")
    make_additional_cost(
        db_session, scenario, make_cost_category(db_session), amount=Decimal("80.0000"),
        start_month=MAR,
    )

    copy = _only_scenario(db_session, _copy_project(client, project.id))
    copied = _costs_of(db_session, copy.id)

    assert [(cost.amount, cost.position_id) for cost in copied] == [(Decimal("80.0000"), None)], (
        "the scenario-level additional cost was not copied — is copy_scenario_additional_costs "
        "still in SCENARIO_CHILD_COPIERS? (ADR-0004, aneks SC-5-05, point 4)"
    )


def test_k_07_canary_the_position_half_is_copied_inside_the_staffing_aggregate_copier(
    client: TestClient, db_session: Session
) -> None:
    """K-07's second canary — a source with **only** a position cost.

    Red on its own when the fourth pass of `copy_staffing_positions` is removed (the copy then has
    the position but no cost), and silent about the scenario-level half.
    """
    project = make_project(db_session, name="Position half", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(db_session, project, name="Baseline", currency="EUR")
    position = make_staffing_position(
        db_session, scenario, make_dimension_tuple(db_session), start_date=MAR
    )
    make_additional_cost(
        db_session, scenario, make_cost_category(db_session), amount=Decimal("90.0000"),
        start_month=MAR, position=position,
    )

    copy = _only_scenario(db_session, _copy_project(client, project.id))
    copied = _costs_of(db_session, copy.id)
    copied_position = _positions_by_start(db_session, copy.id)[MAR]

    assert [(cost.amount, cost.position_id) for cost in copied] == [
        (Decimal("90.0000"), copied_position)
    ], (
        "the position-attached additional cost was not copied onto the copied position — is the "
        "fourth pass of copy_staffing_positions still there? (ADR-0014, point 10)"
    )


def test_k_07_a_copy_of_an_approved_scenario_is_a_draft_with_the_costs_and_the_source_stays(
    client: TestClient, db_session: Session
) -> None:
    """K-07 with an **approved** source — the legal way to change an approved calculation's costs
    (ADR-0004). The copy is a draft carrying all three costs; the source is still approved, with its
    three original rows. Nothing here needs a guard: the copier only ever writes into the draft."""
    source = _source(db_session, status=ScenarioStatus.APPROVED)

    copy = _only_scenario(db_session, _copy_project(client, source["project"].id))

    assert copy.status == ScenarioStatus.DRAFT
    assert len(_costs_of(db_session, copy.id)) == 3
    assert {cost.id for cost in _costs_of(db_session, source["scenario"].id)} == {
        source[key].id for key in ("scenario_cost", "first_cost", "second_cost")
    }
    assert db_session.get(Scenario, source["scenario"].id).status == ScenarioStatus.APPROVED


def test_the_scenario_level_copier_is_a_registered_entry_of_the_cascade() -> None:
    """ADR-0004, aneks SC-5-05, point 4 — registered once, as its own entry."""
    assert SCENARIO_CHILD_COPIERS.count(copy_scenario_additional_costs) == 1


def test_every_additional_cost_column_is_either_copied_or_explicitly_excluded() -> None:
    """The drift guard of `app.data.column_copy` — a column added to `additional_cost` later fails
    here until it is named as copied (and so compared value by value in K-07) or excluded with a
    reason in `ADDITIONAL_COST_COLUMNS_NOT_COPIED`."""
    mapped = {attribute.key for attribute in sa.inspect(AdditionalCost).column_attrs}

    assert mapped == set(COPIED_ADDITIONAL_COST_FIELDS) | ADDITIONAL_COST_COLUMNS_NOT_COPIED
    assert set(COPIED_ADDITIONAL_COST_FIELDS).isdisjoint(ADDITIONAL_COST_COLUMNS_NOT_COPIED)


# --- R-01 (gate 2): a copy reads one state of its source ------------------------------------------


def test_r_01_an_edit_moving_a_cost_off_its_position_during_a_copy_waits_and_the_copy_has_it_once(
    committing_client: TestClient, engine: Engine
) -> None:
    """R-01 (reviewer, gate 2; ADR-0014, point 10) — the two copier passes are separate statements,
    split by `position_id`, a column a `PATCH` can change.

    The copy of a project whose one cost is attached to a position is paused just before its
    **second** pass (the scenario-level copier's `SELECT … position_id IS NULL`), after the first
    pass has already copied the cost with its position. On another connection a `PATCH` detaches the
    cost (`position_id: null`) with its valid marker. With the source locked
    (`scenario_guard.copying_source_scenario`) the `PATCH` **waits** — asserted through `pg_locks`,
    not through a sleep — the second pass still sees the cost attached, and the copy holds it
    **exactly once**. The `PATCH` then succeeds against the source (200), so the lock serialised it
    rather than refusing it.

    Mutation: dropping the lock from `copy_scenario` — the `PATCH` does not block (`blocked` is
    false), commits in the window, the second pass sees a scenario-level cost and copies it again:
    two rows on the copy.
    """
    with Session(bind=engine, expire_on_commit=False, future=True) as setup:
        project = make_project(setup, name="Race copy", accessible_to=(IN_SCOPE_USER,))
        scenario = make_scenario(setup, project, name="Baseline", currency="EUR")
        position = make_staffing_position(
            setup, scenario, make_dimension_tuple(setup), start_date=MAR
        )
        cost = make_additional_cost(
            setup, scenario, make_cost_category(setup), amount=Decimal("123.0000"),
            start_month=MAR, position=position,
        )
        project_id, scenario_id, cost_id = project.id, scenario.id, cost.id
        setup.commit()
    token = committing_client.get(
        additional_costs_path(project_id, scenario_id), headers=HEADERS
    ).json()["costs"][0]["updated_at"]

    outcome: dict[str, Any] = {}
    fired: list[str] = []

    def detach_while_the_copy_is_between_passes(
        connection: Any,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        lowered = statement.lower()
        if fired or "from additional_cost" not in lowered or "position_id is null" not in lowered:
            return
        fired.append(statement)

        def writer() -> None:
            try:
                outcome["patch"] = committing_client.patch(
                    additional_cost_path(project_id, scenario_id, cost_id),
                    json={"updated_at": token, "position_id": None},
                    headers=HEADERS,
                )
            except BaseException as error:  # noqa: BLE001 — reported, never swallowed
                outcome["error"] = error

        thread = threading.Thread(target=writer, daemon=True)
        thread.start()
        outcome["thread"] = thread
        outcome["blocked"] = wait_until_a_lock_request_is_pending(engine)

    event.listen(Engine, "before_cursor_execute", detach_while_the_copy_is_between_passes)
    try:
        copied = committing_client.post(f"/projects/{project_id}/copy", headers=HEADERS)
    finally:
        event.remove(Engine, "before_cursor_execute", detach_while_the_copy_is_between_passes)

    assert fired, "the copy never reached its scenario-level pass"
    outcome["thread"].join(timeout=30)
    assert not outcome["thread"].is_alive(), "the PATCH never finished"
    assert "error" not in outcome, outcome.get("error")
    assert copied.status_code == 201, copied.text
    assert outcome["blocked"], (
        "the PATCH never waited for the copy — nothing serialises it against the copier passes"
    )

    with engine.connect() as check:
        copy_amounts = check.execute(
            sa.text(
                "SELECT c.amount FROM additional_cost c JOIN scenarios s ON s.id = c.scenario_id"
                " WHERE s.project_id = :project"
            ),
            {"project": uuid.UUID(copied.json()["id"])},
        ).scalars().all()
        source_position = check.execute(
            sa.text("SELECT position_id FROM additional_cost WHERE id = :id"), {"id": cost_id}
        ).scalar_one()
    assert copy_amounts == [Decimal("123.0000")], (
        f"the copy holds the cost {len(copy_amounts)} time(s), not exactly once"
    )
    assert outcome["patch"].status_code == 200, outcome["patch"].text
    assert source_position is None, "the PATCH was serialised, and then applied to the source"
