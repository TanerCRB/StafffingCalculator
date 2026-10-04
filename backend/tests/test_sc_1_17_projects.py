"""SC-1-17 API evidence for scoped project search, status filtering, and pagination."""

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import ProjectStatus
from tests.conftest import IN_SCOPE_USER, OUT_OF_SCOPE_USER, as_caller, make_project


def test_k_01_project_search_matches_name_client_and_owner_case_insensitively(
    client: TestClient, db_session: Session
) -> None:
    name = make_project(db_session, name="SC117Zebra Migration", accessible_to=(IN_SCOPE_USER,))
    client_match = make_project(
        db_session,
        name="Different name",
        client_name="SC117NORTHWIND Labs",
        accessible_to=(IN_SCOPE_USER,),
    )
    owner_match = make_project(
        db_session,
        name="Another project",
        owner="SC117Ada Lovelace",
        accessible_to=(IN_SCOPE_USER,),
    )
    make_project(db_session, name="SC117Zebra hidden", accessible_to=(OUT_OF_SCOPE_USER,))

    for query, expected in (
        ("sC117zEbRa", name),
        ("sc117northwind", client_match),
        ("sc117ada lovelace", owner_match),
    ):
        response = client.get(
            "/projects", params={"search": query}, headers=as_caller(IN_SCOPE_USER)
        )
        assert response.status_code == 200
        assert [row["id"] for row in response.json()["projects"]] == [str(expected.id)]
        assert response.json()["total"] == 1


def test_k_02_project_status_filter_selects_status_and_unfiltered_list_includes_archived(
    client: TestClient, db_session: Session
) -> None:
    active = make_project(db_session, name="Active match", accessible_to=(IN_SCOPE_USER,))
    archived = make_project(
        db_session,
        name="Archived match",
        status=ProjectStatus.ARCHIVED,
        accessible_to=(IN_SCOPE_USER,),
    )
    active_response = client.get(
        "/projects", params={"status": "Active"}, headers=as_caller(IN_SCOPE_USER)
    )
    archived_response = client.get(
        "/projects", params={"status": "Archived"}, headers=as_caller(IN_SCOPE_USER)
    )
    all_response = client.get("/projects", headers=as_caller(IN_SCOPE_USER))
    assert [row["id"] for row in active_response.json()["projects"]] == [str(active.id)]
    assert [row["id"] for row in archived_response.json()["projects"]] == [str(archived.id)]
    assert {row["id"] for row in all_response.json()["projects"]} == {
        str(active.id),
        str(archived.id),
    }


def test_k_04_project_search_status_and_total_are_applied_inside_caller_scope(
    client: TestClient, db_session: Session
) -> None:
    own = make_project(db_session, name="Match own", accessible_to=(IN_SCOPE_USER,))
    make_project(db_session, name="Match foreign", accessible_to=(OUT_OF_SCOPE_USER,))
    response = client.get(
        "/projects",
        params={"search": "match", "status": "Active"},
        headers=as_caller(IN_SCOPE_USER),
    )
    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert [row["id"] for row in response.json()["projects"]] == [str(own.id)]


def test_k_05_project_pages_return_every_accessible_match_once_in_stable_order(
    client: TestClient, db_session: Session
) -> None:
    expected = [
        make_project(db_session, name=f"Project {index:02d}", accessible_to=(IN_SCOPE_USER,))
        for index in range(23)
    ]
    tied = [
        make_project(db_session, name="Project tie", accessible_to=(IN_SCOPE_USER,))
        for _ in range(2)
    ]
    expected.extend(tied)
    expected_ids = [
        str(project.id)
        for project in sorted(expected, key=lambda row: (row.name, str(row.id)))
    ]

    def read_all_pages() -> tuple[int, list[str]]:
        ids: list[str] = []
        total = 0
        for offset in (0, 20):
            response = client.get(
                "/projects",
                params={"limit": 20, "offset": offset},
                headers=as_caller(IN_SCOPE_USER),
            )
            assert response.status_code == 200
            payload = response.json()
            total = payload["total"]
            ids.extend(row["id"] for row in payload["projects"])
        return total, ids

    total, observed = read_all_pages()
    repeated_total, repeated = read_all_pages()
    assert total == repeated_total == len(expected_ids) == 25
    assert len(observed) == len(set(observed)) == 25
    assert observed == repeated == expected_ids
