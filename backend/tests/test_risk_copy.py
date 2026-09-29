"""SC-6-08, K-06 - a copy of a scenario carries its risks, reserves and linked cost events with new
identifiers, every link remapped to the copy's **own** risk (ADR-0021, point 8, Q-7 = A; ADR-0004,
addendum SC-6-08; AC-02).

The pieces live in four places, and each has a canary that fails **on its own**:

- the risks are copied by `copy_scenario_risks`, an entry of `SCENARIO_CHILD_COPIERS` **ordered
  before** `copy_staffing_positions` (the copy's risks must exist when a link is remapped);
- the cost events **attached to a position** are copied inside `copy_staffing_positions`, with the
  link remapped by the source risk's name;
- the cost events **with no position** are copied by `copy_scenario_additional_costs`, likewise;
- the reserves are copied by `copy_scenario_reserves`, likewise.

Everything is copied through the real `POST /projects/{id}/copy` - the one entry point of the copy
mechanism an API caller has today.
"""

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.data.additional_cost import copy_scenario_additional_costs
from app.data.project_writes import SCENARIO_CHILD_COPIERS
from app.data.risk_copy import (
    RESERVE_COLUMNS_NOT_COPIED,
    RISK_COLUMNS_NOT_COPIED,
    copy_scenario_risks,
)
from app.data.risk_reserve import copy_scenario_reserves
from app.data.staffing import ADDITIONAL_COST_COLUMNS_NOT_COPIED, copy_staffing_positions
from app.models import (
    AdditionalCost,
    RiskReserve,
    Scenario,
    ScenarioRisk,
    ScenarioStatus,
)
from tests.conftest import (
    IN_SCOPE_USER,
    as_caller,
    link_cost_to_risk,
    make_additional_cost,
    make_cost_category,
    make_dimension_tuple,
    make_project,
    make_reserve,
    make_risk,
    make_scenario,
    make_staffing_position,
    risks_path,
)

MAR = date(2026, 3, 1)
APR = date(2026, 4, 1)
JUN = date(2026, 6, 1)
HEADERS = as_caller(IN_SCOPE_USER)

COPIED_RISK_FIELDS = ("name",)
COPIED_RESERVE_FIELDS = ("amount", "currency", "reserve_type", "start_month", "end_month")
"""Spelled out here rather than derived from the model: the drift guards below compare them, so a
column added later fails until somebody decides which side it is on."""


def _copy_project(client: TestClient, project_id: uuid.UUID) -> uuid.UUID:
    response = client.post(f"/projects/{project_id}/copy", headers=HEADERS)
    assert response.status_code == 201, response.text
    return uuid.UUID(response.json()["id"])


def _scenario_named(session: Session, project_id: uuid.UUID, name: str) -> Scenario:
    session.expire_all()
    return session.execute(
        sa.select(Scenario).where(Scenario.project_id == project_id, Scenario.name == name)
    ).scalar_one()


def _risk_names(session: Session, scenario_id: uuid.UUID) -> dict[uuid.UUID, str]:
    return {
        risk.id: risk.name
        for risk in session.execute(
            sa.select(ScenarioRisk).where(ScenarioRisk.scenario_id == scenario_id)
        ).scalars()
    }


def _links(session: Session, scenario_id: uuid.UUID) -> dict[str, Any]:
    """`{what: name of the risk it links, or None}` - and the scenario that risk belongs to, so a
    link to another scenario's risk shows up as a wrong scenario, not merely a wrong name."""
    session.expire_all()
    names = {
        risk.id: (risk.name, risk.scenario_id)
        for risk in session.execute(sa.select(ScenarioRisk)).scalars()
    }
    result: dict[str, Any] = {}
    for cost in session.execute(
        sa.select(AdditionalCost).where(AdditionalCost.scenario_id == scenario_id)
    ).scalars():
        result[f"cost {cost.amount}"] = None if cost.risk_id is None else names[cost.risk_id]
    for reserve in session.execute(
        sa.select(RiskReserve).where(RiskReserve.scenario_id == scenario_id)
    ).scalars():
        result[f"reserve {reserve.amount}"] = (
            None if reserve.risk_id is None else names[reserve.risk_id]
        )
    return result


def _source(session: Session, *, status: ScenarioStatus = ScenarioStatus.DRAFT) -> dict[str, Any]:
    """A scenario with three risks ("Vendor delay", "Key person", "Unused"), two positions, a
    scenario-level cost linked to the first risk, a position-level cost linked to the second, an
    unlinked position cost, and two reserves (one linked to the first risk, one unlinked) - so
    "Vendor delay" is `both`, "Key person" `cost_event`, "Unused" `none`."""
    project = make_project(session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(session, project, name="Baseline", currency="EUR", status=status)
    first = make_staffing_position(
        session, scenario, make_dimension_tuple(session, suffix=" 1"), start_date=MAR
    )
    second = make_staffing_position(
        session, scenario, make_dimension_tuple(session, suffix=" 2"), start_date=APR
    )
    category = make_cost_category(session)
    vendor = make_risk(session, scenario, name="Vendor delay")
    key = make_risk(session, scenario, name="Key person")
    make_risk(session, scenario, name="Unused")
    scenario_cost = make_additional_cost(
        session, scenario, category, amount=Decimal("1000.0000"), start_month=MAR
    )
    first_cost = make_additional_cost(
        session,
        scenario,
        category,
        amount=Decimal("300.0000"),
        start_month=MAR,
        end_month=JUN,
        position=first,
    )
    make_additional_cost(
        session, scenario, category, amount=Decimal("450.0000"), start_month=APR, position=second
    )
    link_cost_to_risk(session, scenario_cost, vendor)
    link_cost_to_risk(session, first_cost, key)
    make_reserve(session, scenario, amount=Decimal("100.0000"), start_month=MAR, risk=vendor)
    make_reserve(session, scenario, amount=Decimal("50.0000"), start_month=MAR, end_month=JUN)
    return {"project": project, "scenario": scenario}


def test_k_06_a_copy_has_new_ids_and_every_link_points_at_the_copys_own_risk(
    client: TestClient, db_session: Session
) -> None:
    """K-06 (R-05) - the whole claim on a source with three risks:

    1. the copy has three risks, two reserves and three cost events, **none** with a source id;
    2. every copied link resolves to a risk **of the copy's scenario** carrying the name the
    source's
       link carried (`Vendor delay` on the scenario-level cost and the linked reserve, `Key person`
       on the position-level cost); unlinked rows stay unlinked;
    3. the source is unchanged - same ids, same links, still pointing at its own risks;
    4. the copy's risk read reports the same representation per risk (`both` / `cost_event` /
       `none`) and the copy's own counts.
    Mutations: `risk_id` reflected as it is (a link to the source's risk - the composite key refuses
    it), a remap without the scenario filter, or a link dropped to `NULL` (item 2)."""
    source = _source(db_session)
    scenario_id = source["scenario"].id
    source_ids = {
        model.id
        for model, column in (
            (ScenarioRisk, ScenarioRisk.scenario_id),
            (RiskReserve, RiskReserve.scenario_id),
            (AdditionalCost, AdditionalCost.scenario_id),
        )
        for model in db_session.execute(sa.select(model).where(column == scenario_id)).scalars()
    }
    links_before = _links(db_session, scenario_id)

    copy_project_id = _copy_project(client, source["project"].id)
    copy = _scenario_named(db_session, copy_project_id, "Baseline")
    copied_links = _links(db_session, copy.id)

    assert len(_risk_names(db_session, copy.id)) == 3
    copied_ids = {
        row.id
        for model, column in (
            (ScenarioRisk, ScenarioRisk.scenario_id),
            (RiskReserve, RiskReserve.scenario_id),
            (AdditionalCost, AdditionalCost.scenario_id),
        )
        for row in db_session.execute(sa.select(model).where(column == copy.id)).scalars()
    }
    assert len(copied_ids) == 3 + 2 + 3 and copied_ids.isdisjoint(source_ids)

    assert copied_links == {
        "cost 1000.0000": ("Vendor delay", copy.id),
        "cost 300.0000": ("Key person", copy.id),
        "cost 450.0000": None,
        "reserve 100.0000": ("Vendor delay", copy.id),
        "reserve 50.0000": None,
    }
    assert _links(db_session, scenario_id) == links_before
    assert all(link is None or link[1] == scenario_id for link in links_before.values())

    read = client.get(risks_path(copy_project_id, copy.id), headers=HEADERS).json()
    assert {
        r["name"]: (r["representation"], r["cost_event_count"], r["reserve_count"])
        for r in read["risks"]
    } == {
        "Vendor delay": ("both", 1, 1),
        "Key person": ("cost_event", 1, 0),
        "Unused": ("none", 0, 0),
    }


def test_k_06_a_copy_of_an_approved_source_is_a_draft_with_the_risks_and_the_source_stays(
    client: TestClient, db_session: Session
) -> None:
    """K-06 with an **approved** source - the legal way to change an approved calculation's risks.
    The copy is a draft carrying everything with links remapped; the source is still approved with
    its original rows and links."""
    source = _source(db_session, status=ScenarioStatus.APPROVED)
    links_before = _links(db_session, source["scenario"].id)

    copy = _scenario_named(db_session, _copy_project(client, source["project"].id), "Baseline")

    assert copy.status == ScenarioStatus.DRAFT
    assert {name for name, _ in _links(db_session, copy.id).items()} == set(links_before)
    assert all(link is None or link[1] == copy.id for link in _links(db_session, copy.id).values())
    assert db_session.get(Scenario, source["scenario"].id).status == ScenarioStatus.APPROVED
    assert _links(db_session, source["scenario"].id) == links_before


def test_k_06_same_named_risks_in_two_scenarios_of_one_project_each_remap_to_their_own_copy(
    client: TestClient, db_session: Session
) -> None:
    """K-06 (decoy) - scenarios A and B of one project both declare "Vendor delay", each with a
    linked reserve. After copying the project every copied reserve points at the risk of **its own
    copied scenario** (A' -> A's copy of the risk, B' -> B's), never the other scenario's. Mutation:
    joining the copy's risk by name only, without its scenario."""
    project = make_project(db_session, name="Two scenarios", accessible_to=(IN_SCOPE_USER,))
    for name, amount in (("A", "10.0000"), ("B", "20.0000")):
        scenario = make_scenario(db_session, project, name=name, currency="EUR")
        risk = make_risk(db_session, scenario, name="Vendor delay")
        make_reserve(db_session, scenario, amount=Decimal(amount), start_month=MAR, risk=risk)

    copy_project_id = _copy_project(client, project.id)

    for name, amount in (("A", "10.0000"), ("B", "20.0000")):
        copy = _scenario_named(db_session, copy_project_id, name)
        assert _links(db_session, copy.id) == {f"reserve {amount}": ("Vendor delay", copy.id)}


# --- the registry, and one canary per piece ------------------------------------------------------


def test_k_06_the_risk_copier_is_registered_once_and_runs_before_the_staffing_copier() -> None:
    """K-06 (Q-7 = A) - `copy_scenario_risks` is one entry of the cascade, **before**
    `copy_staffing_positions` (which remaps the position-level cost links and needs the copy's risks
    to exist); the reserve copier is one entry after the risk copier. Mutation: appending the risk
    entry at the end of the registry."""
    entries = list(SCENARIO_CHILD_COPIERS)

    assert entries.count(copy_scenario_risks) == 1
    assert entries.count(copy_scenario_reserves) == 1
    assert entries.index(copy_scenario_risks) < entries.index(copy_staffing_positions)
    assert entries.index(copy_scenario_risks) < entries.index(copy_scenario_reserves)
    assert entries.index(copy_scenario_risks) < entries.index(copy_scenario_additional_costs)


def test_k_06_canary_the_risk_entry_copies_risks_that_nothing_links_to(
    client: TestClient, db_session: Session
) -> None:
    """K-06's canary for the risk entry - a source with **only** risks (no cost event, no reserve).
    Red on its own when `copy_scenario_risks` is taken out of the registry (the copy has none)."""
    project = make_project(db_session, name="Risks only", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(db_session, project, name="Baseline", currency="EUR")
    make_risk(db_session, scenario, name="One")
    make_risk(db_session, scenario, name="Two")

    copy = _scenario_named(db_session, _copy_project(client, project.id), "Baseline")

    assert sorted(_risk_names(db_session, copy.id).values()) == ["One", "Two"], (
        "the declared risks were not copied - is copy_scenario_risks still in "
        "SCENARIO_CHILD_COPIERS? (ADR-0021, point 8)"
    )


def test_k_06_canary_the_scenario_level_cost_half_keeps_its_link(
    client: TestClient, db_session: Session
) -> None:
    """K-06's canary for the scenario-level cost half - only a scenario-level linked cost event.
    Red on its own when its remap (in `copy_scenario_additional_costs`) is removed."""
    project = make_project(db_session, name="Scenario half", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(db_session, project, name="Baseline", currency="EUR")
    risk = make_risk(db_session, scenario, name="Vendor delay")
    cost = make_additional_cost(
        db_session,
        scenario,
        make_cost_category(db_session),
        amount=Decimal("80.0000"),
        start_month=MAR,
    )
    link_cost_to_risk(db_session, cost, risk)

    copy = _scenario_named(db_session, _copy_project(client, project.id), "Baseline")

    assert _links(db_session, copy.id) == {"cost 80.0000": ("Vendor delay", copy.id)}


def test_k_06_canary_the_position_level_cost_half_keeps_its_link(
    client: TestClient, db_session: Session
) -> None:
    """K-06's canary for the position-level cost half - only a linked cost on a position. Red on its
    own when the link remap in the fourth pass of `copy_staffing_positions` is removed (the copy is
    then unlinked, or refused by the composite key)."""
    project = make_project(db_session, name="Position half", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(db_session, project, name="Baseline", currency="EUR")
    position = make_staffing_position(
        db_session, scenario, make_dimension_tuple(db_session), start_date=MAR
    )
    risk = make_risk(db_session, scenario, name="Key person")
    cost = make_additional_cost(
        db_session,
        scenario,
        make_cost_category(db_session),
        amount=Decimal("90.0000"),
        start_month=MAR,
        position=position,
    )
    link_cost_to_risk(db_session, cost, risk)

    copy = _scenario_named(db_session, _copy_project(client, project.id), "Baseline")

    assert _links(db_session, copy.id) == {"cost 90.0000": ("Key person", copy.id)}


def test_k_06_canary_the_reserve_entry_keeps_its_link(
    client: TestClient, db_session: Session
) -> None:
    """K-06's canary for the reserve entry - a source with only a linked reserve. Red on its own
    when `copy_scenario_reserves` is taken out of the registry."""
    project = make_project(db_session, name="Reserve only", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(db_session, project, name="Baseline", currency="EUR")
    risk = make_risk(db_session, scenario, name="Vendor delay")
    make_reserve(db_session, scenario, amount=Decimal("60.0000"), start_month=MAR, risk=risk)

    copy = _scenario_named(db_session, _copy_project(client, project.id), "Baseline")

    assert _links(db_session, copy.id) == {"reserve 60.0000": ("Vendor delay", copy.id)}


def test_every_risk_reserve_and_cost_link_column_is_either_copied_or_explicitly_excluded() -> None:
    """The drift guard of `app.data.column_copy` for the two new tables and the new column: a column
    added later fails here until it is named as copied or excluded with a reason. `risk_id` is on
    the
    excluded side of both the cost and the reserve - a link is remapped, never reflected."""
    mapped_risk = {a.key for a in sa.inspect(ScenarioRisk).column_attrs}
    mapped_reserve = {a.key for a in sa.inspect(RiskReserve).column_attrs}

    assert mapped_risk == set(COPIED_RISK_FIELDS) | RISK_COLUMNS_NOT_COPIED
    assert mapped_reserve == set(COPIED_RESERVE_FIELDS) | RESERVE_COLUMNS_NOT_COPIED
    assert "risk_id" in RESERVE_COLUMNS_NOT_COPIED
    assert "risk_id" in ADDITIONAL_COST_COLUMNS_NOT_COPIED


def test_k_06_the_remap_joins_the_copys_own_risk_by_name_and_never_a_same_named_risk_elsewhere(
    db_session: Session,
) -> None:
    """K-06 (decoy) - the remap function on its own: source scenario A, its copy A' (whose risk is
    created **before** the decoy), and an unrelated scenario B of the project holding a risk of the
    same name, created last. The mapping is exactly `{A's risk: A''s risk}`. Mutation: joining the
    copy's risk by name without its scenario id lets B's risk (the last match) win for A's risk."""
    from app.data.risk_copy import copied_risk_ids

    project = make_project(db_session, name="Remap decoy", accessible_to=(IN_SCOPE_USER,))
    source = make_scenario(db_session, project, name="A", currency="EUR")
    copy = make_scenario(db_session, project, name="A copy", currency="EUR")
    other = make_scenario(db_session, project, name="B", currency="EUR")
    source_risk = make_risk(db_session, source, name="Vendor delay")
    copied_risk = make_risk(db_session, copy, name="Vendor delay")
    make_risk(db_session, other, name="Vendor delay")

    assert copied_risk_ids(db_session, source.id, copy.id) == {source_risk.id: copied_risk.id}
