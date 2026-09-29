"""SC-6-08, K-01 and K-02 - how a risk is represented, and that saying so never moves a total
(F-09 pt 4-5; ADR-0021, points 1, 3, 5).

- **K-01** a risk is `cost_event` / `reserve` / `both` / `none` from the **declared link only**. The
  decoys are the point: a cost and a reserve with the same category-free amount and month but no
  link
  are never `both`; a position-level linked cost counts; a same-named risk of *another scenario of
  the same project* does not.
- **K-02** detection never alters a total: `additional_cost`, `/results` and `/compare` are
  byte-identical before a link, after `both`, and after the unlink; the reserve total is reported
  beside `additional_cost` and never inside `included_cost`; the reserve module and the revenue /
  personnel-cost modules never meet at any import depth (control R-08).

Real PostgreSQL through the API under test; nothing here reads a state by calling the function that
computes it.
"""

import json
import uuid
from datetime import date
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.domain.risk_reserve import risk_representation
from app.models import AdditionalCost
from tests.conftest import (
    IN_SCOPE_USER,
    additional_cost_payload,
    additional_costs_path,
    as_caller,
    caller_holding,
    link_cost_to_risk,
    make_additional_cost,
    make_cost_category,
    make_dimension_tuple,
    make_project,
    make_reserve,
    make_risk,
    make_scenario,
    make_staffing_position,
    reserves_path,
    risks_path,
)
from tests.test_additional_cost import (
    PERSONNEL_COST_MODULES,
    REVENUE_MODULES,
    _imports_of,
    _reachable_from,
)
from tests.test_scenario_results import EVERYTHING, _ensure_statutory_bypass, results_path
from tests.test_scenario_results_compare import _scenario_in_project, compare_path

MAR = date(2026, 3, 1)
APR = date(2026, 4, 1)
HEADERS = as_caller(IN_SCOPE_USER)


def _risks(client: TestClient, project_id: uuid.UUID, scenario_id: uuid.UUID) -> dict[str, Any]:
    """The risk read, keyed by risk name."""
    response = client.get(risks_path(project_id, scenario_id), headers=HEADERS)
    assert response.status_code == 200, response.text
    return {risk["name"]: risk for risk in response.json()["risks"]}


def _base(session: Session, *, name: str = "Aurora") -> dict[str, Any]:
    project = make_project(session, name=name, accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(session, project, name="Baseline", currency="EUR")
    return {
        "project": project,
        "scenario": scenario,
        "category": make_cost_category(session, name=f"Licences {name}"),
    }


# --- K-01: the state comes from the declared link, and from nothing else ------------------------


def test_k_01_a_risk_is_none_cost_event_reserve_or_both_from_its_declared_links(
    client: TestClient, db_session: Session
) -> None:
    """K-01 (R-01) - four risks, one per state, each with the counts the links give. The reserve
    and cost event of `both` are the only ones carrying both links. Mutation: computing the state
    from anything but the two link counts (e.g. any cost event and any reserve of the scenario =>
    `both`) turns `only cost` and `only reserve` into `both` here."""
    base = _base(db_session)
    scenario, category = base["scenario"], base["category"]
    none = make_risk(db_session, scenario, name="none")
    only_cost = make_risk(db_session, scenario, name="only cost")
    only_reserve = make_risk(db_session, scenario, name="only reserve")
    both = make_risk(db_session, scenario, name="both")
    cost_a = make_additional_cost(
        db_session, scenario, category, amount=Decimal("10"), start_month=MAR
    )
    cost_b = make_additional_cost(
        db_session, scenario, category, amount=Decimal("20"), start_month=MAR
    )
    link_cost_to_risk(db_session, cost_a, only_cost)
    link_cost_to_risk(db_session, cost_b, both)
    make_reserve(db_session, scenario, amount=Decimal("30"), start_month=MAR, risk=only_reserve)
    make_reserve(db_session, scenario, amount=Decimal("40"), start_month=MAR, risk=both)
    make_reserve(db_session, scenario, amount=Decimal("50"), start_month=APR, risk=both)
    assert none.id != both.id

    risks = _risks(client, base["project"].id, scenario.id)

    observed = {
        name: (
            r["representation"],
            r["double_represented"],
            r["cost_event_count"],
            r["reserve_count"],
        )
        for name, r in risks.items()
    }
    assert observed == {
        "none": ("none", False, 0, 0),
        "only cost": ("cost_event", False, 1, 0),
        "only reserve": ("reserve", False, 0, 1),
        "both": ("both", True, 1, 2),
    }


def test_k_01_unlinked_lookalikes_never_make_a_risk_double_represented(
    client: TestClient, db_session: Session
) -> None:
    """K-01 (R-01, decoys) - a risk with a linked cost event; beside it an **unlinked** reserve with
    the same amount in the same month, and an unlinked cost event, all in the same scenario. The
    risk stays `cost_event`. Contrast: linking that very reserve turns it `both`. Mutation: a
    heuristic (same month / same amount) in place of the link makes the first read `both`."""
    base = _base(db_session)
    scenario, category = base["scenario"], base["category"]
    risk = make_risk(db_session, scenario, name="Vendor delay")
    linked = make_additional_cost(
        db_session, scenario, category, amount=Decimal("100"), start_month=MAR
    )
    link_cost_to_risk(db_session, linked, risk)
    make_additional_cost(db_session, scenario, category, amount=Decimal("100"), start_month=MAR)
    decoy = make_reserve(db_session, scenario, amount=Decimal("100"), start_month=MAR)

    before = _risks(client, base["project"].id, scenario.id)["Vendor delay"]
    db_session.execute(
        sa.text("UPDATE risk_reserve SET risk_id = :risk WHERE id = :id"),
        {"risk": risk.id, "id": decoy.id},
    )
    after = _risks(client, base["project"].id, scenario.id)["Vendor delay"]

    assert (before["representation"], before["reserve_count"]) == ("cost_event", 0)
    assert (before["cost_event_count"], before["double_represented"]) == (1, False)
    assert (after["representation"], after["reserve_count"]) == ("both", 1)


def test_k_01_a_linked_position_level_cost_event_counts(
    client: TestClient, db_session: Session
) -> None:
    """K-01 (R-01) - a cost event attached to a **position** and linked to a risk is a cost event of
    that risk. Contrast: the same position cost without the link leaves the risk at `none`.
    Mutation: counting only `position_id IS NULL` cost events reports `none` for the linked one."""
    base = _base(db_session)
    scenario, category = base["scenario"], base["category"]
    position = make_staffing_position(
        db_session, scenario, make_dimension_tuple(db_session), start_date=MAR
    )
    risk = make_risk(db_session, scenario, name="Key person leaves")
    cost = make_additional_cost(
        db_session, scenario, category, amount=Decimal("5000"), start_month=MAR, position=position
    )

    unlinked = _risks(client, base["project"].id, scenario.id)["Key person leaves"]
    link_cost_to_risk(db_session, cost, risk)
    linked = _risks(client, base["project"].id, scenario.id)["Key person leaves"]

    assert (unlinked["representation"], unlinked["cost_event_count"]) == ("none", 0)
    assert (linked["representation"], linked["cost_event_count"]) == ("cost_event", 1)


def test_k_01_a_same_named_risk_in_another_scenario_of_the_same_project_does_not_count(
    client: TestClient, db_session: Session
) -> None:
    """K-01 (R-01, decoy) - scenarios A and B of one project each declare a risk named "Vendor
    delay"; A's carries a cost event, B's a reserve. Neither is `both`: each is what its own links
    say, and each read counts only its own scenario's rows. Contrast: giving A's risk its own
    reserve makes A's `both` while B's stays `reserve`. Mutation: aggregating by name, or by
    project,
    reports `both` for both risks at the first read."""
    base = _base(db_session)
    project, category = base["project"], base["category"]
    scenario_a = base["scenario"]
    scenario_b = make_scenario(db_session, project, name="Variant", currency="EUR")
    risk_a = make_risk(db_session, scenario_a, name="Vendor delay")
    risk_b = make_risk(db_session, scenario_b, name="Vendor delay")
    cost = make_additional_cost(
        db_session, scenario_a, category, amount=Decimal("10"), start_month=MAR
    )
    link_cost_to_risk(db_session, cost, risk_a)
    make_reserve(db_session, scenario_b, amount=Decimal("10"), start_month=MAR, risk=risk_b)

    a = _risks(client, project.id, scenario_a.id)["Vendor delay"]
    b = _risks(client, project.id, scenario_b.id)["Vendor delay"]
    assert (a["representation"], a["cost_event_count"], a["reserve_count"]) == ("cost_event", 1, 0)
    assert (b["representation"], b["cost_event_count"], b["reserve_count"]) == ("reserve", 0, 1)

    make_reserve(db_session, scenario_a, amount=Decimal("10"), start_month=MAR, risk=risk_a)
    a_after = _risks(client, project.id, scenario_a.id)["Vendor delay"]
    b_after = _risks(client, project.id, scenario_b.id)["Vendor delay"]
    assert a_after["representation"] == "both"
    assert b_after["representation"] == "reserve"


def test_k_01_linking_through_the_api_produces_the_same_states(
    client: TestClient, db_session: Session
) -> None:
    """K-01 end to end - a risk declared by `POST`, a cost event created with `risk_id` (then
    `cost_event`), a reserve created with `risk_id` (then `both`); the additional-cost read carries
    the linked risk id on that cost and `null` on an unlinked one (gate 1 G-2)."""
    base = _base(db_session)
    project_id, scenario_id = base["project"].id, base["scenario"].id
    risk = client.post(
        risks_path(project_id, scenario_id), json={"name": "Vendor delay"}, headers=HEADERS
    )
    assert risk.status_code == 201, risk.text
    assert risk.json()["representation"] == "none"
    risk_id = risk.json()["id"]

    linked = client.post(
        additional_costs_path(project_id, scenario_id),
        json=additional_cost_payload(base["category"].id, risk_id=risk_id),
        headers=HEADERS,
    )
    unlinked = client.post(
        additional_costs_path(project_id, scenario_id),
        json=additional_cost_payload(base["category"].id),
        headers=HEADERS,
    )
    assert (linked.status_code, unlinked.status_code) == (201, 201)
    assert _risks(client, project_id, scenario_id)["Vendor delay"]["representation"] == "cost_event"

    reserve = client.post(
        reserves_path(project_id, scenario_id),
        json={
            "risk_id": risk_id,
            "amount": "1200.0000",
            "currency": "EUR",
            "reserve_type": "one_off",
            "start_month": "2026-03-01",
        },
        headers=HEADERS,
    )
    assert reserve.status_code == 201, reserve.text
    assert _risks(client, project_id, scenario_id)["Vendor delay"]["representation"] == "both"

    costs = client.get(additional_costs_path(project_id, scenario_id), headers=HEADERS).json()[
        "costs"
    ]
    assert sorted(cost["risk_id"] or "" for cost in costs) == sorted([risk_id, ""])


def test_k_01_the_state_is_a_function_of_the_two_counts_alone() -> None:
    """K-01 - the domain function, exhaustively for the four combinations (and a negative count is a
    programming error, not a fifth state)."""
    assert risk_representation(cost_event_count=0, reserve_count=0) == "none"
    assert risk_representation(cost_event_count=3, reserve_count=0) == "cost_event"
    assert risk_representation(cost_event_count=0, reserve_count=2) == "reserve"
    assert risk_representation(cost_event_count=1, reserve_count=1) == "both"


# --- K-02: detection alters no total -------------------------------------------------------------


def _bytes(client: TestClient, path: str) -> bytes:
    with caller_holding(*EVERYTHING):
        response = client.get(path)
    assert response.status_code == 200, response.text
    return response.content


def _additional_cost_total(
    client: TestClient, project_id: uuid.UUID, scenario_id: uuid.UUID
) -> str:
    """The `additional_cost` object of the additional-cost read, serialised canonically."""
    with caller_holding(*EVERYTHING):
        response = client.get(additional_costs_path(project_id, scenario_id))
    assert response.status_code == 200, response.text
    return json.dumps(response.json()["additional_cost"], sort_keys=True)


def test_k_02_making_a_risk_double_represented_and_undoing_it_moves_no_total_byte(
    client: TestClient, db_session: Session
) -> None:
    """K-02 (R-02) - a complete scenario (revenue, personnel cost, one additional cost of 2000.00):
    the `additional_cost` total, `GET /results` and `GET /compare` are captured, then again after a
    risk is linked to that cost event **and** to a reserve (`both`), then again after the cost event
    is unlinked. All three captures are byte-identical to the first, and the risk read really did
    say `both` in between (the contrast - a run in which nothing was ever double-represented proves
    nothing). Mutations: detection that drops one of the two representations, or adds the reserve
    to `additional_cost`, moves `/results` here."""
    _ensure_statutory_bypass(db_session)
    project = make_project(
        db_session,
        name="Compare K02",
        accessible_to=(IN_SCOPE_USER,),
        cost_visible_to=(IN_SCOPE_USER,),
    )
    scenario = _scenario_in_project(db_session, project, name="A")
    other = _scenario_in_project(db_session, project, name="B")
    cost = db_session.execute(
        sa.select(AdditionalCost).where(AdditionalCost.scenario_id == scenario.id)
    ).scalar_one()

    def capture() -> tuple[str, bytes, bytes]:
        return (
            _additional_cost_total(client, project.id, scenario.id),
            _bytes(client, results_path(project.id, scenario.id)),
            _bytes(client, compare_path(project.id, scenario.id, other.id)),
        )

    before = capture()
    unlinked_reserve = make_reserve(
        db_session, scenario, amount=Decimal("777.00"), start_month=MAR, currency="PLN"
    )
    assert capture() == before, "an unlinked reserve moved a total"

    risk = make_risk(db_session, scenario, name="Vendor delay")
    link_cost_to_risk(db_session, cost, risk)
    db_session.execute(
        sa.text("UPDATE risk_reserve SET risk_id = :risk WHERE id = :id"),
        {"risk": risk.id, "id": unlinked_reserve.id},
    )
    assert _risks(client, project.id, scenario.id)["Vendor delay"]["representation"] == "both"
    assert capture() == before, "becoming double-represented moved a total"

    link_cost_to_risk(db_session, cost, None)
    assert _risks(client, project.id, scenario.id)["Vendor delay"]["representation"] == "reserve"
    assert capture() == before, "undoing the double representation moved a total"


def test_k_02_the_reserve_total_is_reported_beside_additional_cost_and_never_inside_it(
    client: TestClient, db_session: Session
) -> None:
    """K-02 (R-02; Q-3 = A) - a scenario with an additional cost of 2000.00 and a reserve of 777.00:
    the reserve endpoint reports `reserve_total` 777.00 as its own named result; the additional-cost
    total stays 2000.00 (not 2777.00) and `included_cost` of `/results` is unchanged by the reserve.
    Contrast: adding a second additional cost of 1.00 *does* move both (so the figures are live, not
    constant). Mutation: folding the reserve into `additional_cost` (or `included_cost`) moves the
    2000.00 to 2777.00."""
    _ensure_statutory_bypass(db_session)
    project = make_project(
        db_session,
        name="Compare K02b",
        accessible_to=(IN_SCOPE_USER,),
        cost_visible_to=(IN_SCOPE_USER,),
    )
    scenario = _scenario_in_project(db_session, project, name="A")

    def results() -> dict[str, Any]:
        with caller_holding(*EVERYTHING):
            return client.get(results_path(project.id, scenario.id)).json()

    included_before = results()["included_cost"]
    make_reserve(db_session, scenario, amount=Decimal("777.00"), start_month=MAR, currency="PLN")

    reserves = client.get(reserves_path(project.id, scenario.id), headers=HEADERS).json()
    additional = json.loads(_additional_cost_total(client, project.id, scenario.id))
    assert (reserves["reserve_total"]["state"], reserves["reserve_total"]["amount"]) == (
        "calculated",
        "777.00",
    )
    assert additional["amount"] == "2000.00"
    assert results()["included_cost"] == included_before
    assert "reserve_total" not in results() and "reserve_total" not in additional

    extra = make_cost_category(db_session, name="Extra")
    make_additional_cost(
        db_session, scenario, extra, amount=Decimal("1.00"), start_month=MAR, currency="PLN"
    )
    assert (
        json.loads(_additional_cost_total(client, project.id, scenario.id))["amount"] == "2001.00"
    )
    assert results()["included_cost"] != included_before


# --- K-02 / R-08: the reserve and the revenue / personnel-cost paths never meet ------------------

RESERVE_MODULES = {"app.data.risk_reserve", "app.domain.risk_reserve"}
RISK_MODULES = RESERVE_MODULES | {"app.data.risk"}
RESULT_PATH_MODULES = {
    "app.data.scenario_results",
    "app.data.scenario_what_if",
    "app.domain.scenario_results",
    "app.data.additional_cost",
    "app.domain.additional_cost",
}


def test_k_02_the_reserve_modules_and_the_revenue_and_personnel_cost_modules_never_meet() -> None:
    """K-02 / R-08, direct imports, both directions - the reserve modules import nothing of the
    revenue path or the personnel cost, and none of the revenue, personnel-cost, results, what-if or
    additional-cost modules imports a reserve module. The contrast keeps this from passing by
    reading files that import nothing: the copy registry imports the reserve data module, and the
    reserve data module imports its own domain module."""
    for path in ("app/data/risk_reserve.py", "app/domain/risk_reserve.py", "app/data/risk.py"):
        imports = _imports_of(path)
        assert not (imports & REVENUE_MODULES), f"{path} imports the revenue path"
        assert not (imports & PERSONNEL_COST_MODULES), f"{path} imports the personnel cost"
    for path in (
        "app/data/commercial_terms.py",
        "app/domain/revenue.py",
        "app/domain/revenue_time_and_material.py",
        "app/domain/revenue_story_points.py",
        "app/data/personnel_cost.py",
        "app/domain/personnel_cost.py",
        "app/data/scenario_results.py",
        "app/data/scenario_what_if.py",
        "app/domain/scenario_results.py",
        "app/data/additional_cost.py",
        "app/domain/additional_cost.py",
    ):
        assert not (_imports_of(path) & RISK_MODULES), f"{path} imports a risk/reserve module"

    assert "app.data.risk_reserve" in _imports_of("app/data/project_writes.py")
    assert "app.domain.risk_reserve" in _imports_of("app/data/risk_reserve.py")


def test_k_02_no_import_path_at_any_depth_joins_the_reserve_to_revenue_or_personnel_cost() -> None:
    """K-02 / R-08 - the graph and not only its edges (the K-10 walk of SC-5-05): the whole
    reachable set is walked from each reserve module and from each revenue, personnel-cost, results
    and additional-cost module. Measured contrast: `app.models.risk` **is** reachable from the
    revenue path (`commercial_terms` -> `staffing` -> `risk_copy` -> the model), which proves the
    walk follows more than one hop - while no *reserve module* is reachable from there."""
    for module in sorted(RISK_MODULES):
        crossing = _reachable_from(module) & (REVENUE_MODULES | PERSONNEL_COST_MODULES)
        assert crossing == set(), f"{module} reaches {sorted(crossing)}"
    for module in sorted(REVENUE_MODULES | PERSONNEL_COST_MODULES | RESULT_PATH_MODULES):
        crossing = _reachable_from(module) & RISK_MODULES
        assert crossing == set(), f"{module} reaches {sorted(crossing)}"

    assert "app.models.risk" not in _imports_of("app/data/commercial_terms.py")
    assert "app.models.risk" in _reachable_from("app.data.commercial_terms")
