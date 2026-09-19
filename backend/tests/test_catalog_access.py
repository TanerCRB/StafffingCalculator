"""SC-2-01, K-01 and K-02 — the catalogue is organisational, and it still denies by default.

Two claims that pull in opposite directions and are proven here side by side:

- **K-01: no subject predicate.** A caller with zero `project_access` rows sees the same catalogue
  as everybody else. The catalogue is the first set of data in this system that belongs to no
  project (ADR-0005, addendum 2026-09-19 "pierwszy zbiór danych bez zasięgu projektu", point 1), so
  the absence of a scope filter here is a decision — and the contrast in the same test is that the
  *same* caller's project list is empty, which is a different boundary guarded by a different
  mechanism.
- **K-02: deny by default.** "No scope" is not "no permission". Every catalogue endpoint declares
  `CATALOG_READ` or `CATALOG_WRITE`, and the denied caller in every test below holds *all five
  project permissions* — so a `require_permission` mutated to any project permission would let
  them through and these tests would fail. A denied caller holding nothing at all would prove
  much less."""

from datetime import date

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.identity import Permission
from app.data.catalog import DIMENSION_MODELS
from app.models import CatalogRole
from tests.conftest import (
    IN_SCOPE_USER,
    UNKNOWN_USER,
    DimensionTuple,
    as_caller,
    caller_holding,
    count_dimension_entries,
    count_rates,
    make_dimension_tuple,
    make_project,
    make_rate,
    rate_payload,
)

WINDOW_START = date(2026, 1, 1)
WINDOW_END = date(2026, 6, 30)

EVERY_PROJECT_PERMISSION = (
    Permission.PROJECT_READ,
    Permission.PROJECT_CREATE,
    Permission.PROJECT_EDIT,
    Permission.PROJECT_COPY,
    Permission.PROJECT_ARCHIVE,
)
"""What the denied caller in the K-02 tests holds instead of a catalogue permission.

The point of handing them everything *but* `CATALOG_*`: ADR-0005's addendum (point 2) refuses to
widen `PROJECT_READ` onto the catalogue, and that refusal is only tested if a caller with full
project rights is still refused. It also makes the mutation K-02 names — swapping the required
permission for one the denied caller holds — fail these tests rather than survive them."""


def _dimension_paths() -> list[str]:
    """Every dictionary endpoint, from the mapping the router itself uses.

    Derived rather than typed out, so a fifth dictionary added later is covered by these tests
    on the day it appears instead of on the day somebody remembers to extend a literal list."""
    return [f"/catalog/dimensions/{segment}" for segment in sorted(DIMENSION_MODELS)]


def _one_rate(session: Session) -> DimensionTuple:
    dimensions = make_dimension_tuple(session)
    make_rate(session, dimensions, effective_from=WINDOW_START, effective_to=WINDOW_END)
    return dimensions


def test_k_01_a_caller_with_no_project_access_sees_the_whole_catalogue(
    client: TestClient, db_session: Session
) -> None:
    """K-01. The catalogue is not narrowed per caller; the project list is. One test, both facts.

    `UNKNOWN_USER` holds no `project_access` row at all — neither granted nor denied. Their project
    list is empty and their catalogue is not, while `IN_SCOPE_USER` (who has a project) receives an
    identical catalogue payload. Both mutations K-01 names die here:

    - a subject filter on the catalogue read (a join to `project_access`) empties `UNKNOWN_USER`'s
      rates and dictionaries, and makes the two callers' payloads differ;
    - a catalogue read returning a constant empty list fails the assertions naming the row that must
      be present — which is why the entry name and the rate's tuple are asserted, not just a length.
    """
    dimensions = _one_rate(db_session)
    make_project(db_session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))

    with_projects = {
        path: client.get(path, headers=as_caller(IN_SCOPE_USER)) for path in _dimension_paths()
    }
    with_projects["/catalog/rates"] = client.get(
        "/catalog/rates", headers=as_caller(IN_SCOPE_USER)
    )
    without_projects = {
        path: client.get(path, headers=as_caller(UNKNOWN_USER)) for path in _dimension_paths()
    }
    without_projects["/catalog/rates"] = client.get(
        "/catalog/rates", headers=as_caller(UNKNOWN_USER)
    )
    projects_of_each = {
        user: client.get("/projects", headers=as_caller(user))
        for user in (IN_SCOPE_USER, UNKNOWN_USER)
    }

    # The contrast, first: the project boundary is real and it is a different mechanism.
    assert [row["name"] for row in projects_of_each[IN_SCOPE_USER].json()["projects"]] == [
        "Aurora migration"
    ]
    assert projects_of_each[UNKNOWN_USER].json()["projects"] == [], (
        "the caller without project access can see a project — the contrast in this test is void"
    )

    for path, response in without_projects.items():
        assert response.status_code == 200, f"{path}: {response.text}"
        assert response.json() == with_projects[path].json(), (
            f"{path} answers differently for a caller with no project access"
        )

    # Named rows, so a constant empty list cannot satisfy the equality above.
    assert "Backend Engineer" in [
        entry["name"] for entry in without_projects["/catalog/dimensions/roles"].json()["entries"]
    ]
    rates = without_projects["/catalog/rates"].json()["rates"]
    assert [rate["role_id"] for rate in rates] == [str(dimensions.role_id)]


def test_k_02_reading_the_catalogue_is_denied_without_catalog_read(
    client: TestClient, db_session: Session
) -> None:
    """K-02, the read half: `403` for a caller with every project permission and no catalogue one.

    Every catalogue read path is asserted, the dictionaries and the two rate paths alike — a guard
    is per endpoint, so "the catalogue denies" is a claim about all of them and not about the first
    one. The payload is checked to carry no catalogue content, because a `403` whose body still
    named the row would be a leak with a status code on it.
    """
    dimensions = _one_rate(db_session)
    read_paths = [
        *_dimension_paths(),
        "/catalog/rates",
        "/catalog/rates/effective",
    ]

    with caller_holding(*EVERY_PROJECT_PERMISSION):
        responses = {
            path: client.get(
                path,
                params=(
                    {**dimensions.as_query(), "on_date": WINDOW_START.isoformat()}
                    if path.endswith("/effective")
                    else {}
                ),
                headers=as_caller(IN_SCOPE_USER),
            )
            for path in read_paths
        }

    for path, response in responses.items():
        assert response.status_code == 403, f"{path}: {response.status_code} {response.text}"
        assert "Backend Engineer" not in response.text, path
        assert str(dimensions.role_id) not in response.text, path


def test_k_02_reading_the_catalogue_is_denied_without_any_identity(
    client: TestClient, db_session: Session
) -> None:
    """K-02. No identity at all is a `401`, before any permission question and before the
    database."""
    _one_rate(db_session)

    response = client.get("/catalog/rates")

    assert response.status_code == 401
    assert "default_cost_rate" not in response.text


def test_k_02_writing_a_dimension_entry_is_denied_without_catalog_write(
    client: TestClient, db_session: Session
) -> None:
    """K-02, the write half for the dictionaries: `CATALOG_READ` is not enough, nothing is saved.

    Zero rows written is asserted from the database, not inferred from the status code: a refusal
    that answered `403` after having inserted the row would satisfy a status-only assertion.
    """
    with caller_holding(Permission.CATALOG_READ, *EVERY_PROJECT_PERMISSION):
        response = client.post(
            "/catalog/dimensions/roles",
            json={"name": "Backend Engineer"},
            headers=as_caller(IN_SCOPE_USER),
        )

    assert response.status_code == 403, response.text
    assert count_dimension_entries(db_session, CatalogRole) == 0


def test_k_02_writing_a_rate_is_denied_without_catalog_write(
    client: TestClient, db_session: Session
) -> None:
    """K-02, the write half for rates: a reader cannot write one, and the table stays empty.

    The caller here holds `CATALOG_READ` — the closest permission there is — plus every project
    permission, so the refusal cannot be an accident of holding nothing.
    """
    dimensions = make_dimension_tuple(db_session)

    with caller_holding(Permission.CATALOG_READ, *EVERY_PROJECT_PERMISSION):
        response = client.post(
            "/catalog/rates", json=rate_payload(dimensions), headers=as_caller(IN_SCOPE_USER)
        )

    assert response.status_code == 403, response.text
    assert count_rates(db_session) == 0


def test_k_02_a_caller_holding_the_catalogue_permissions_is_not_refused(
    client: TestClient, db_session: Session
) -> None:
    """The positive side of K-02 — without it, a guard that refused everybody would pass the four
    tests above and the catalogue would be unreachable rather than protected.

    Run as the placeholder identity, i.e. the caller the running system actually has (ADR-0005,
    addendum 2026-09-19 point 6 adds `CATALOG_READ`/`CATALOG_WRITE` to it and nothing else).
    """
    dimensions = make_dimension_tuple(db_session)

    created = client.post(
        "/catalog/dimensions/locations",
        json={"name": "Germany"},
        headers=as_caller(IN_SCOPE_USER),
    )
    listed = client.get("/catalog/dimensions/locations", headers=as_caller(IN_SCOPE_USER))
    rate = client.post(
        "/catalog/rates", json=rate_payload(dimensions), headers=as_caller(IN_SCOPE_USER)
    )

    assert created.status_code == 201, created.text
    assert listed.status_code == 200, listed.text
    assert "Germany" in [entry["name"] for entry in listed.json()["entries"]]
    assert rate.status_code == 201, rate.text


def test_an_unknown_dimension_segment_is_a_404_naming_the_known_ones(
    client: TestClient,
) -> None:
    """Not a criterion — the branch that resolves a path segment to a table.

    A `404` rather than a silent fallback to the first dictionary: reading a seniority list
    under the name "grades" and getting roles back is the kind of wrong answer no later test
    would question."""
    response = client.get("/catalog/dimensions/grades", headers=as_caller(IN_SCOPE_USER))

    assert response.status_code == 404, response.text
    assert "roles" in response.json()["detail"]
