"""SC-6-08 - the risk endpoints as a client uses them: declare, rename, delete, page; and the delete
of a risk a representation still points at (K-04 through the API; ADR-0021, Q-8 = A; ADR-0017).

The database's own refusals are proven in `tests/test_risk_schema.py` with raw SQL; here the same
refusals are proven to reach the client as the named `409`, not a `500` and not a silent cascade.
"""

from datetime import date
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.conftest import (
    IN_SCOPE_USER,
    additional_cost_path,
    additional_cost_payload,
    additional_costs_path,
    as_caller,
    count_risks,
    make_cost_category,
    make_project,
    make_risk,
    make_scenario,
    reserve_path,
    reserves_path,
    risk_path,
    risks_path,
)

MAR = date(2026, 3, 1)
HEADERS = as_caller(IN_SCOPE_USER)


def _scenario(session: Session) -> dict[str, Any]:
    project = make_project(session, name="Aurora", accessible_to=(IN_SCOPE_USER,))
    return {
        "project": project,
        "scenario": make_scenario(session, project, name="Baseline", currency="EUR"),
        "category": make_cost_category(session),
    }


def test_a_risk_can_be_declared_renamed_and_deleted_and_names_are_unique_and_not_blank(
    client: TestClient, db_session: Session
) -> None:
    """Declare (201, representation `none`), rename with the marker (200), a duplicate name in the
    scenario is `409` "refused by the database" naming the unique constraint, a blank name is a
    `422`, delete with the marker is `204`. Contrast: the duplicate name in *another* scenario is a
    `201`."""
    fixture = _scenario(db_session)
    project_id, scenario_id = fixture["project"].id, fixture["scenario"].id
    other = make_scenario(db_session, fixture["project"], name="Variant", currency="EUR")

    created = client.post(
        risks_path(project_id, scenario_id), json={"name": "  Vendor delay "}, headers=HEADERS
    )
    assert created.status_code == 201, created.text
    assert created.json()["name"] == "Vendor delay"
    path = risk_path(project_id, scenario_id, created.json()["id"])

    duplicate = client.post(
        risks_path(project_id, scenario_id), json={"name": "Vendor delay"}, headers=HEADERS
    )
    assert duplicate.status_code == 409
    assert "uq_scenario_risk_scenario_id_name" in duplicate.json()["detail"]
    assert (
        client.post(
            risks_path(project_id, scenario_id), json={"name": "   "}, headers=HEADERS
        ).status_code
        == 422
    )
    assert (
        client.post(
            risks_path(project_id, other.id), json={"name": "Vendor delay"}, headers=HEADERS
        ).status_code
        == 201
    )

    renamed = client.patch(
        path,
        json={"updated_at": created.json()["updated_at"], "name": "Late vendor"},
        headers=HEADERS,
    )
    assert renamed.status_code == 200 and renamed.json()["name"] == "Late vendor"
    deleted = client.request(
        "DELETE", path, json={"updated_at": renamed.json()["updated_at"]}, headers=HEADERS
    )
    assert deleted.status_code == 204


def test_k_04_deleting_a_risk_a_representation_points_at_is_a_named_409_until_it_is_unlinked(
    client: TestClient, db_session: Session
) -> None:
    """K-04 through the API (R-09, Q-8 = A) - a risk with a linked cost event **and** a linked
    reserve: `DELETE` is a `409` saying a cost event or a reserve still points at it (not
    "approved",
    not "concurrency marker", and not a `500`), and the risk survives. Unlinking the reserve
    (delete)
    is still refused because of the cost event; unlinking that too (`risk_id: null`) makes the same
    `DELETE` a `204`. Mutation: `ON DELETE SET NULL` / `CASCADE` - the first `DELETE` would
    succeed."""
    fixture = _scenario(db_session)
    project_id, scenario_id = fixture["project"].id, fixture["scenario"].id
    risk = client.post(
        risks_path(project_id, scenario_id), json={"name": "Vendor delay"}, headers=HEADERS
    ).json()
    cost = client.post(
        additional_costs_path(project_id, scenario_id),
        json=additional_cost_payload(fixture["category"].id, risk_id=risk["id"]),
        headers=HEADERS,
    ).json()
    reserve = client.post(
        reserves_path(project_id, scenario_id),
        json={
            "risk_id": risk["id"],
            "amount": "5.0000",
            "currency": "EUR",
            "reserve_type": "one_off",
            "start_month": "2026-03-01",
        },
        headers=HEADERS,
    ).json()
    path = risk_path(project_id, scenario_id, risk["id"])

    def delete() -> Any:
        token = client.get(risks_path(project_id, scenario_id), headers=HEADERS).json()["risks"][0][
            "updated_at"
        ]
        return client.request("DELETE", path, json={"updated_at": token}, headers=HEADERS)

    refused = delete()
    assert refused.status_code == 409, refused.text
    assert "still points at this risk" in refused.json()["detail"]
    assert "approved" not in refused.json()["detail"] and "marker" not in refused.json()["detail"]
    assert count_risks(db_session) == 1

    removed = client.request(
        "DELETE",
        reserve_path(project_id, scenario_id, reserve["id"]),
        json={"updated_at": reserve["updated_at"]},
        headers=HEADERS,
    )
    assert removed.status_code == 204
    assert delete().status_code == 409, "the cost event still points at the risk"

    unlinked = client.patch(
        additional_cost_path(project_id, scenario_id, cost["id"]),
        json={"updated_at": cost["updated_at"], "risk_id": None},
        headers=HEADERS,
    )
    assert unlinked.status_code == 200 and unlinked.json()["risk_id"] is None
    assert delete().status_code == 204
    assert count_risks(db_session) == 0


def test_the_risk_list_is_paged_with_a_total_and_a_stable_order(
    client: TestClient, db_session: Session
) -> None:
    """ADR-0017 - five risks, two per page: `total` is 5 on every page, pages are disjoint and cover
    all five in name order, an offset past the end is a `200` with an empty list and the true total,
    and an out-of-range parameter is a `422` naming the field (never clamped). Contrast: no
    parameters at all is the whole list."""
    fixture = _scenario(db_session)
    for name in ("e", "b", "a", "d", "c"):
        make_risk(db_session, fixture["scenario"], name=name)
    path = risks_path(fixture["project"].id, fixture["scenario"].id)

    pages = [
        client.get(f"{path}?limit=2&offset={offset}", headers=HEADERS).json()
        for offset in (0, 2, 4, 40)
    ]

    assert [page["total"] for page in pages] == [5, 5, 5, 5]
    assert [[r["name"] for r in page["risks"]] for page in pages] == [
        ["a", "b"],
        ["c", "d"],
        ["e"],
        [],
    ]
    whole = client.get(path, headers=HEADERS).json()
    assert [r["name"] for r in whole["risks"]] == ["a", "b", "c", "d", "e"] and whole["total"] == 5
    refused = client.get(f"{path}?limit=0", headers=HEADERS)
    assert refused.status_code == 422
    assert refused.json()["detail"][0]["loc"] == ["query", "limit"]
