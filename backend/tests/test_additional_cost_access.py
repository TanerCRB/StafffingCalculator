"""SC-5-05, K-08/K-09 — who reaches a scenario's additional costs, and under which permission.

- **K-08** project scope: `404`, never `403`, for every operation (read, create, edit, delete) —
  proven with a caller holding **every** permission (the precedent of SC-5-01 K-05), so a refusal
  here is provably about scope and not about permission. Path confusion separately: a cost id or a
  position id of another project, sent to a URL of a project in scope, is the same `404` and writes
  nothing (ADR-0005, aneks 2026-09-23 SC-5-05, point 4).
- **K-09** permissions: costs under `STAFFING_READ`/`STAFFING_WRITE` with no `PERSONNEL_COSTS_READ`
  conjunction; the category dictionary under `CATALOG_READ`/`CATALOG_WRITE`. The named, accepted
  risk of Q-7 = B is tested as what it is — a cost on a `headcount = 1` position visible to a caller
  without `PERSONNEL_COSTS_READ` — so that a later change of that decision is a visible test change,
  not a silent one. `PLACEHOLDER_PERMISSIONS` stays exactly as it was.
"""

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.additional_cost import ADDITIONAL_COST_NOT_FOUND_DETAIL
from app.api.deps import PLACEHOLDER_PERMISSIONS
from app.core.identity import Permission
from app.models import AdditionalCost, CatalogCostCategory
from tests.conftest import (
    IN_SCOPE_USER,
    OUT_OF_SCOPE_USER,
    additional_cost_path,
    additional_cost_payload,
    additional_costs_path,
    as_caller,
    caller_holding,
    count_additional_costs,
    make_additional_cost,
    make_cost_category,
    make_dimension_tuple,
    make_project,
    make_scenario,
    make_staffing_position,
)

MAR = date(2026, 3, 1)
EVERYTHING = frozenset(Permission)
HEADERS = as_caller(IN_SCOPE_USER)


def _costed(session: Session, *, name: str, user: str, headcount: int = 2) -> dict[str, Any]:
    """A project accessible to `user` only, a draft scenario, one position, one cost on it."""
    project = make_project(session, name=name, accessible_to=(user,))
    scenario = make_scenario(session, project, name="Baseline", currency="EUR")
    position = make_staffing_position(
        session, scenario, make_dimension_tuple(session, suffix=f" {name}"),
        headcount=headcount, start_date=MAR,
    )
    category = make_cost_category(session, name=f"Recruitment {name}")
    cost = make_additional_cost(
        session, scenario, category, amount=Decimal("5000.0000"), start_month=MAR,
        position=position,
    )
    return {
        "project": project, "scenario": scenario, "position": position,
        "category": category, "cost": cost,
    }


def _snapshot(session: Session) -> list[tuple[Any, ...]]:
    """Every cost row as stored — compared before and after, so "nothing was written" is a claim
    about the table and not about a status code."""
    session.expire_all()
    return [
        tuple(row)
        for row in session.execute(
            sa.select(
                AdditionalCost.id, AdditionalCost.scenario_id, AdditionalCost.position_id,
                AdditionalCost.amount, AdditionalCost.updated_at,
            ).order_by(AdditionalCost.id)
        ).all()
    ]


# --- K-08: out of scope is the same 404 as absent, for every operation ---------------------------


def test_k_08_every_operation_outside_the_callers_scope_is_the_same_404_never_a_403(
    client: TestClient, db_session: Session
) -> None:
    """K-08 — a caller holding **every** permission, four addresses, four operations each:

    1. a scenario of a project the caller has no access to (with a cost, so a leaking read would
       have a figure to show and a leaking write a row to change);
    2. a scenario id that does not exist, under the caller's project;
    3. a scenario of another project **in scope**, addressed through the caller's first project;
    4. a project id that does not exist.

    `GET`, `POST`, `PATCH` and `DELETE` (the last two with the target cost's real marker, so the
    refusal cannot be a stale marker's) all answer `404` with one body — identical bytes — and the
    table is unchanged. Contrast: the caller's own scenario reads `200` and edits `200`. Mutation: a
    `select(Scenario)` of the cost path's own instead of `scenario_in_scope`.
    """
    mine = _costed(db_session, name="Aurora", user=IN_SCOPE_USER)
    theirs = _costed(db_session, name="Borealis", user=OUT_OF_SCOPE_USER)
    other_mine = _costed(db_session, name="Cassiopeia", user=IN_SCOPE_USER)
    token = theirs["cost"].updated_at.isoformat()

    addresses = [
        (theirs["project"].id, theirs["scenario"].id, theirs["cost"].id),
        (mine["project"].id, uuid.uuid4(), mine["cost"].id),
        (mine["project"].id, other_mine["scenario"].id, other_mine["cost"].id),
        (uuid.uuid4(), mine["scenario"].id, mine["cost"].id),
    ]
    before = _snapshot(db_session)
    responses = []
    with caller_holding(*EVERYTHING):
        for project_id, scenario_id, cost_id in addresses:
            collection = additional_costs_path(project_id, scenario_id)
            item = additional_cost_path(project_id, scenario_id, cost_id)
            responses += [
                client.get(collection),
                client.post(collection, json=additional_cost_payload(mine["category"].id)),
                client.patch(item, json={"updated_at": token, "amount": "1.0000"}),
                client.request("DELETE", item, json={"updated_at": token}),
            ]

        own = client.get(additional_costs_path(mine["project"].id, mine["scenario"].id))
        own_token = own.json()["costs"][0]["updated_at"]
        own_edit = client.patch(
            additional_cost_path(mine["project"].id, mine["scenario"].id, mine["cost"].id),
            json={"updated_at": own_token, "amount": "5001.0000"},
        )

    for response in responses:
        assert response.status_code == 404, response.text
        assert response.json() == {"detail": ADDITIONAL_COST_NOT_FOUND_DETAIL}
    assert len({response.content for response in responses}) == 1
    # The contrast's own edit is the only change the table may show; every other row is as it was.
    after = {row[0]: row for row in _snapshot(db_session)}
    assert set(after) == {row[0] for row in before}, "a refused operation added or removed a row"
    for row in before:
        if row[0] != mine["cost"].id:
            assert after[row[0]] == row, "a refused operation changed a cost row"

    assert own.status_code == 200, own.text
    assert own.json()["additional_cost"]["amount"] == "5000.00"
    assert own_edit.status_code == 200, own_edit.text


def test_k_08_path_confusion_a_foreign_cost_or_position_id_on_a_url_in_scope_is_404_and_writes_nothing(  # noqa: E501 — the criterion names the case; the name is the contract
    client: TestClient, db_session: Session
) -> None:
    """K-08, path confusion — the URL is the caller's own project and scenario (fully in scope);
    only the id in it, or in the body, belongs to a project the caller cannot see:

    - `PATCH`/`DELETE …/additional-costs/{their cost id}` with that cost's real marker → `404`, and
      their row is untouched (the `WHERE scenario_id = :scenario_id` inside the statement);
    - `POST` with `position_id` = their position → `404`, and no row is written anywhere (the
      position is joined only within the URL's scenario);
    - `PATCH` of the caller's own cost re-attaching it to their position → `404`, own row unchanged.

    Every body is the one `404` body — the same bytes as for an id that never existed, so the
    answer confirms nothing about the other project. A caller holding every permission, so none of
    it is a permission refusal. Contrast: the same `POST` with the caller's own position is `201`.
    """
    mine = _costed(db_session, name="Aurora", user=IN_SCOPE_USER)
    theirs = _costed(db_session, name="Borealis", user=OUT_OF_SCOPE_USER)
    collection = additional_costs_path(mine["project"].id, mine["scenario"].id)
    their_item = additional_cost_path(mine["project"].id, mine["scenario"].id, theirs["cost"].id)
    never_item = additional_cost_path(mine["project"].id, mine["scenario"].id, uuid.uuid4())
    their_token = theirs["cost"].updated_at.isoformat()
    before = _snapshot(db_session)
    count_before = count_additional_costs(db_session)

    with caller_holding(*EVERYTHING):
        own_token = client.get(collection).json()["costs"][0]["updated_at"]
        responses = [
            client.patch(their_item, json={"updated_at": their_token, "amount": "1.0000"}),
            client.request("DELETE", their_item, json={"updated_at": their_token}),
            client.post(
                collection,
                json=additional_cost_payload(
                    mine["category"].id, position_id=str(theirs["position"].id)
                ),
            ),
            client.patch(
                additional_cost_path(mine["project"].id, mine["scenario"].id, mine["cost"].id),
                json={"updated_at": own_token, "position_id": str(theirs["position"].id)},
            ),
            client.patch(never_item, json={"updated_at": their_token, "amount": "1.0000"}),
        ]

        assert _snapshot(db_session) == before
        assert count_additional_costs(db_session) == count_before

        own_position = client.post(
            collection,
            json=additional_cost_payload(
                mine["category"].id, position_id=str(mine["position"].id)
            ),
        )

    for response in responses:
        assert response.status_code == 404, response.text
        assert response.json() == {"detail": ADDITIONAL_COST_NOT_FOUND_DETAIL}
    assert len({response.content for response in responses}) == 1
    assert own_position.status_code == 201, own_position.text


# --- K-09: STAFFING_* for costs, CATALOG_* for categories, no conjunction -------------------------


def test_k_09_costs_are_read_under_staffing_read_and_written_under_staffing_write(
    client: TestClient, db_session: Session
) -> None:
    """K-09 — each of the two permissions refused on its own, with every other permission held, and
    each sufficient on its own:

    - everything but `STAFFING_READ` → `GET` is `403`; `STAFFING_READ` alone → `200`;
    - everything but `STAFFING_WRITE` → `POST`/`PATCH`/`DELETE` are `403` and the table is
      unchanged; `STAFFING_WRITE` alone → `POST` is `201`.

    Mutation: declaring `PERSONNEL_COSTS_READ` (or a new permission) on the endpoint — the
    "alone" calls become `403`. Declaring `STAFFING_READ` on a write — the write succeeds without
    `STAFFING_WRITE`.
    """
    mine = _costed(db_session, name="Aurora", user=IN_SCOPE_USER)
    collection = additional_costs_path(mine["project"].id, mine["scenario"].id)
    item = additional_cost_path(mine["project"].id, mine["scenario"].id, mine["cost"].id)
    token = mine["cost"].updated_at.isoformat()
    before = _snapshot(db_session)

    with caller_holding(*(EVERYTHING - {Permission.STAFFING_READ})):
        no_read = client.get(collection)
    with caller_holding(Permission.STAFFING_READ):
        read_only = client.get(collection)
    with caller_holding(*(EVERYTHING - {Permission.STAFFING_WRITE})):
        no_write = [
            client.post(collection, json=additional_cost_payload(mine["category"].id)),
            client.patch(item, json={"updated_at": token, "amount": "1.0000"}),
            client.request("DELETE", item, json={"updated_at": token}),
        ]
    assert _snapshot(db_session) == before
    with caller_holding(Permission.STAFFING_WRITE):
        write_only = client.post(collection, json=additional_cost_payload(mine["category"].id))

    assert no_read.status_code == 403, no_read.text
    assert read_only.status_code == 200, read_only.text
    assert [response.status_code for response in no_write] == [403, 403, 403]
    assert write_only.status_code == 201, write_only.text


def test_k_09_the_named_risk_a_cost_on_a_headcount_one_position_is_visible_without_personnel_costs_read(  # noqa: E501 — the name states the accepted risk; it is the contract
    client: TestClient, db_session: Session
) -> None:
    """K-09 — the risk ADR-0005's addendum SC-5-05 (point 2) names and accepts, **tested as what it
    is, not as a gap**: a recruitment cost of 5000 on a position with `headcount = 1` indirectly
    describes one person, and a caller holding `STAFFING_READ` — without `PERSONNEL_COSTS_READ`, on
    a project whose `can_view_personnel_costs` flag is not set — sees it whole: the row's amount and
    the sum.

    If a later decision puts additional costs under the cost conjunction, this test must change with
    it — a visible change, rather than a gate that appears or disappears silently.
    """
    exposed = _costed(db_session, name="Solo", user=IN_SCOPE_USER, headcount=1)
    assert exposed["position"].headcount == 1

    with caller_holding(Permission.STAFFING_READ):
        response = client.get(
            additional_costs_path(exposed["project"].id, exposed["scenario"].id)
        )

    assert response.status_code == 200, response.text
    assert response.json()["costs"][0]["amount"] == "5000.0000"
    assert response.json()["costs"][0]["position_id"] == str(exposed["position"].id)
    assert response.json()["additional_cost"]["amount"] == "5000.00"


def test_k_09_the_category_dictionary_is_catalog_read_and_catalog_write_only(
    client: TestClient, db_session: Session
) -> None:
    """K-09 (ADR-0005, aneks SC-5-05, point 3) — the categories are served by the shared dictionary
    endpoints (`/catalog/dimensions/cost-categories`), under the catalogue's own pair:

    - everything but `CATALOG_READ` (so `STAFFING_*` held) → listing is `403`; `CATALOG_READ` alone
      → `200`, the entry listed;
    - everything but `CATALOG_WRITE` → adding is `403`, nothing written; `CATALOG_WRITE` alone →
      `201`;
    - a name differing only in case and spacing is the same name (`409`, the normalised-name index
      every dictionary has) — the same mechanism, not a fifth one.
    """
    make_cost_category(db_session, name="Cloud")
    path = "/catalog/dimensions/cost-categories"

    def categories() -> int:
        return db_session.execute(
            sa.select(sa.func.count()).select_from(CatalogCostCategory)
        ).scalar_one()

    with caller_holding(*(EVERYTHING - {Permission.CATALOG_READ})):
        no_read = client.get(path)
    with caller_holding(Permission.CATALOG_READ):
        read_only = client.get(path)
    with caller_holding(*(EVERYTHING - {Permission.CATALOG_WRITE})):
        no_write = client.post(path, json={"name": "Hardware"})
    assert categories() == 1
    with caller_holding(Permission.CATALOG_WRITE):
        write_only = client.post(path, json={"name": "Hardware"})
        duplicate = client.post(path, json={"name": "  hardware "})

    assert no_read.status_code == 403
    assert read_only.status_code == 200, read_only.text
    assert [entry["name"] for entry in read_only.json()["entries"]] == ["Cloud"]
    assert no_write.status_code == 403
    assert write_only.status_code == 201, write_only.text
    assert duplicate.status_code == 409, duplicate.text
    assert "uq_catalog_cost_categories_name_normalized" in duplicate.json()["detail"]
    assert categories() == 2


def test_k_09_the_placeholder_permission_set_is_unchanged_and_no_permission_was_added() -> None:
    """K-09 — no new permission (Q-7 = B) and no widening of the placeholder: the same set the
    existing canary (`test_access_control.py::
    test_personnel_cost_permission_is_not_granted_by_the_placeholder_identity`) asserts, and the
    same thirteen members of `Permission` (`test_catalog_access.py`,
    `test_absence_budget_access.py`) since SC-7-01 added `RESULTS_READ` — a permission of its own
    scenario-results endpoint, not of this task's additional-cost tables. Repeated here so this
    task's own suite states its claim rather than borrowing it."""
    assert len(Permission) == 13
    assert Permission.PERSONNEL_COSTS_READ not in PLACEHOLDER_PERMISSIONS
    assert {Permission.STAFFING_READ, Permission.STAFFING_WRITE} <= PLACEHOLDER_PERMISSIONS
    assert len(PLACEHOLDER_PERMISSIONS) == 12


def test_a_request_body_carrying_an_unknown_field_is_a_422_and_writes_nothing(
    client: TestClient, db_session: Session
) -> None:
    """ADR-0014 ("what is deliberately absent") — there is no column for a description, a note or a
    person, and the request schema says so out loud: `extra="forbid"` turns such a body into a
    `422` naming the field instead of silently dropping it."""
    mine = _costed(db_session, name="Aurora", user=IN_SCOPE_USER)
    before = count_additional_costs(db_session)

    response = client.post(
        additional_costs_path(mine["project"].id, mine["scenario"].id),
        json=additional_cost_payload(mine["category"].id, description="Laptop for Jan Kowalski"),
        headers=HEADERS,
    )

    assert response.status_code == 422, response.text
    assert count_additional_costs(db_session) == before
