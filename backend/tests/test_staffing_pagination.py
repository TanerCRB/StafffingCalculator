"""SC-3-05 — criteria K-01 through K-06 and K-08: paginating `GET .../staffing-positions`
(ADR-0017), and the precedence between the 404 (scope) and the 422 (pagination parameters) that
protects it.

Every test here runs against the real PostgreSQL `db_session`/`client` fixtures already provide
(`tests/conftest.py`) — no mock stands in for the ordering or counting claims below, which is what
K-03/K-04 in particular ask for.

- **K-01** no parameters is still "the whole grid", exactly as SC-3-01 shipped it — proven against a
  fixture larger than `DEFAULT_STAFFING_POSITION_LIST_LIMIT`, so a silent default page size would
  fail this test rather than pass it by coincidence (the mutation the criterion names).
- **K-02** a named page size bounds the response and carries `total` distinct from what was
  returned.
- **K-03** the sort order is a total order: a tie on `start_date` (today's first sort key) does not
  lose or duplicate a row between pages.
- **K-04** the sum of every page equals today's full list, with no duplicate and no loss.
- **K-05** pagination is not a second channel confirming scope: an out-of-scope scenario answers the
  same 404 whatever the pagination parameters name; the identical extreme request against a scenario
  the caller *can* see, with fewer positions than the page, is a 200 with an empty list.
- **K-06** a parameter outside its bound is refused (422) naming the field, never silently clamped;
  the boundary value itself is accepted.
- **K-08** the 404 (scope) takes precedence over the 422 (pagination), on the same request.

**Gate-2 fixes (guardian STOP S-01, reviewer R-01/R-02, human decision 2026-09-27: fix all three):**

- **S-01** an unparsable `limit` (e.g. `limit=abc`) must not reach the client as a `422` before the
  scenario's scope has been checked — the same K-08 guarantee, extended to a value that is not even
  a well-formed integer, which a bare `int | None` type annotation used to let through *before*
  `scenario_in_scope` ever ran.
- **R-01** the `422` `detail` this endpoint answers must be a *list* of objects
  (`{"type", "loc", "msg", "input"}`), the native FastAPI/Pydantic shape every other `422` on this
  API already has (confirmed against `GET /catalog/rates`), not the plain string this endpoint used
  to answer alone.
- **R-02** the page is read as a bounded top-N against a real plan, not merely claimed to be one —
  `STAFFING_POSITION_PAGE_INDEX` (migration `f1a2c4b6d8e0`) is what the docstring's claim now rests
  on.
"""

import uuid
from datetime import date, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.staffing import STAFFING_NOT_FOUND_DETAIL
from app.data.staffing import (
    DEFAULT_STAFFING_POSITION_LIST_LIMIT,
    MAX_STAFFING_POSITION_LIST_LIMIT,
    MAX_STAFFING_POSITION_LIST_OFFSET,
    _staffing_position_page_statement,
)
from app.models import StaffingPosition
from app.models.staffing import STAFFING_POSITION_PAGE_INDEX
from tests.conftest import (
    IN_SCOPE_USER,
    OUT_OF_SCOPE_USER,
    DimensionTuple,
    as_caller,
    make_dimension_tuple,
    make_project,
    make_scenario,
    staffing_path,
)

_SUBQUERY_RELATIONSHIPS = ("InitPlan", "SubPlan")


def _page_branch_nodes(node: dict[str, Any]) -> list[dict[str, Any]]:
    """Every plan node reached from `node` without descending into a subplan branch.

    Copied from `tests/test_catalog_schema_constraints.py` (R-02's own construction for
    `GET /catalog/rates`) rather than imported: a test helper is not production code shared through
    `app`, and the two pagination endpoints read their `EXPLAIN` output independently.
    """
    nodes = [node]
    for child in node.get("Plans", ()):
        if child.get("Parent Relationship") in _SUBQUERY_RELATIONSHIPS:
            continue
        nodes.extend(_page_branch_nodes(child))
    return nodes


def _dimensions_and_draft_scenario(session: Session, *, suffix: str = ""):
    """A project in `IN_SCOPE_USER`'s scope, one draft scenario, one full catalogue tuple."""
    project = make_project(
        session, name=f"Aurora migration{suffix}", accessible_to=(IN_SCOPE_USER,)
    )
    scenario = make_scenario(session, project, name="Baseline")
    return project, scenario, make_dimension_tuple(session, suffix=suffix)


def _make_positions(
    session: Session,
    scenario,
    dimensions: DimensionTuple,
    count: int,
    *,
    start: date = date(2026, 1, 1),
    same_start: bool = False,
) -> list[uuid.UUID]:
    """`count` staffing positions directly, one flush at the end — no endpoint, no request schema.

    Positions get *distinct* `start_date`s by default, one day apart, so the total order
    (`start_date`, `id`) is decided by the date alone and every test that does not care about ties
    stays independent of the id ordering that ties depend on. `same_start=True` is the one case that
    exists to *force* a tie (K-03): every position shares the same day, so only `id` — a total order
    (`start_date`, `id`) requires — can tell them apart.
    """
    ids: list[uuid.UUID] = []
    for index in range(count):
        position_start = start if same_start else start + timedelta(days=index)
        position = StaffingPosition(
            id=uuid.uuid4(),
            scenario_id=scenario.id,
            role_id=dimensions.role_id,
            seniority_id=dimensions.seniority_id,
            location_id=dimensions.location_id,
            engagement_type_id=dimensions.engagement_type_id,
            headcount=1,
            start_date=position_start,
        )
        session.add(position)
        ids.append(position.id)
    session.flush()
    return ids


# --- K-01: no parameters is still the whole grid --------------------------------------------------


def test_k_01_no_parameters_returns_exactly_what_the_endpoint_returned_before_pagination(
    client: TestClient, db_session: Session
) -> None:
    """K-01 — a fixture larger than the default page size, so a silent default cannot pass by luck.

    `DEFAULT_STAFFING_POSITION_LIST_LIMIT + 5` positions, not merely "50 or more": the criterion's
    own mutation is "a silent default different from 'no limit'", and a fixture of 55 positions
    against a hypothetical silent default of 200 would still return everything and pass by accident.
    Sizing the fixture *past* the default is what makes the mutation observable.
    """
    project, scenario, dimensions = _dimensions_and_draft_scenario(db_session)
    count = DEFAULT_STAFFING_POSITION_LIST_LIMIT + 5
    _make_positions(db_session, scenario, dimensions, count)

    no_params = client.get(staffing_path(project.id, scenario.id), headers=as_caller(IN_SCOPE_USER))

    assert no_params.status_code == 200, no_params.text
    body = no_params.json()
    assert len(body["positions"]) == count, "no parameters must still answer the whole grid"
    assert body["total"] == count

    # Contrast: the same call with an explicit small page size returns a subset — so "no parameters"
    # above is not merely a coincidence of an implementation that always returns everything.
    small_page = client.get(
        staffing_path(project.id, scenario.id),
        params={"limit": 3},
        headers=as_caller(IN_SCOPE_USER),
    )
    assert small_page.status_code == 200, small_page.text
    small_body = small_page.json()
    assert len(small_body["positions"]) == 3
    assert small_body["total"] == count


# --- K-02: a named page size bounds the response and carries a total distinct from it


def test_k_02_a_named_page_size_bounds_the_response_and_signals_there_is_more(
    client: TestClient, db_session: Session
) -> None:
    project, scenario, dimensions = _dimensions_and_draft_scenario(db_session)
    _make_positions(db_session, scenario, dimensions, 7)

    page = client.get(
        staffing_path(project.id, scenario.id),
        params={"limit": 3, "offset": 0},
        headers=as_caller(IN_SCOPE_USER),
    )

    assert page.status_code == 200, page.text
    body = page.json()
    assert len(body["positions"]) == 3
    assert body["total"] == 7, "total must differ from the count returned, not echo it"

    # Contrast: a page large enough to hold everything returns everything, and total then equals
    # what was returned — the shape "no parameters" also produces.
    whole = client.get(
        staffing_path(project.id, scenario.id),
        params={"limit": 10},
        headers=as_caller(IN_SCOPE_USER),
    )
    assert whole.status_code == 200, whole.text
    whole_body = whole.json()
    assert len(whole_body["positions"]) == 7
    assert whole_body["total"] == 7


def test_k_02_naming_only_offset_fills_limit_from_its_own_default(
    client: TestClient, db_session: Session
) -> None:
    """K-02's other partial request — the half `test_k_01_...` and `test_k_02_...` above never
    exercise: `offset` named, `limit` omitted.

    `_validated_staffing_position_page` only takes its "neither parameter named" shortcut when
    *both* are absent (K-01) — naming `offset` alone must still fill `limit` from
    `DEFAULT_STAFFING_POSITION_LIST_LIMIT`, not from a silent, unbounded "the rest of the grid".
    A fixture larger than the default page size, for the same reason K-01's own fixture is: a page
    of `DEFAULT_STAFFING_POSITION_LIST_LIMIT` rows would satisfy a *smaller* fixture by coincidence.
    """
    project, scenario, dimensions = _dimensions_and_draft_scenario(db_session)
    count = DEFAULT_STAFFING_POSITION_LIST_LIMIT + 5
    _make_positions(db_session, scenario, dimensions, count)

    offset_only = client.get(
        staffing_path(project.id, scenario.id),
        params={"offset": 1},
        headers=as_caller(IN_SCOPE_USER),
    )

    assert offset_only.status_code == 200, offset_only.text
    body = offset_only.json()
    assert len(body["positions"]) == DEFAULT_STAFFING_POSITION_LIST_LIMIT, (
        "offset named alone must still page at the default limit, not at an arbitrary size"
    )
    assert body["total"] == count


# --- K-03: the sort is a total order — a tie on start_date loses or duplicates nothing


def _make_positions_tied_on_start_date(
    session: Session,
    scenario,
    dimensions: DimensionTuple,
    count: int,
    *,
    start: date = date(2026, 1, 1),
) -> list[uuid.UUID]:
    """`count` positions sharing **one** `start_date`, inserted in **descending id order** on
    purpose — mirrors `test_catalog_schema_constraints._make_rates_tied_on_effective_from`.

    Without the `id` tie-breaker, `ORDER BY start_date` alone leaves every key tied, and PostgreSQL
    returns the rows close to the order the scan handed them — insertion order here. Inserting them
    *ascending* would make a missing tie-breaker's output indistinguishable from a correctly sorted
    (ascending) one; inserting them descending is what makes the defect observable: a correct,
    tie-broken read must still come back ascending regardless of the order they were written in.
    """
    ids = sorted((uuid.uuid4() for _ in range(count)), reverse=True)
    for position_id in ids:
        session.add(
            StaffingPosition(
                id=position_id,
                scenario_id=scenario.id,
                role_id=dimensions.role_id,
                seniority_id=dimensions.seniority_id,
                location_id=dimensions.location_id,
                engagement_type_id=dimensions.engagement_type_id,
                headcount=1,
                start_date=start,
            )
        )
    session.flush()
    return ids


def test_k_03_a_tie_on_start_date_does_not_lose_or_duplicate_a_row_between_pages(
    client: TestClient, db_session: Session
) -> None:
    """K-03, against a real PostgreSQL — `db_session`/`client` are bound to one (`tests/conftest`).

    Six positions sharing one `start_date` (today's first sort key), inserted in descending `id`
    order (`_make_positions_tied_on_start_date`) and walked two at a time. Without `id` as the
    tie-breaker, `ORDER BY start_date` alone leaves every key tied, and PostgreSQL is free to order
    tied rows however the scan handed them between two statements carrying different
    `LIMIT`/`OFFSET` — which, given the descending insertion order, is *not* the ascending
    `(start_date, id)` order this endpoint claims, and not even a consistent partition of the six
    rows: the row on the boundary of one page can then appear on both pages (a duplicate) or on
    neither (a loss).
    """
    project, scenario, dimensions = _dimensions_and_draft_scenario(db_session)
    ids = _make_positions_tied_on_start_date(db_session, scenario, dimensions, 6)
    assert ids != sorted(ids), "the fixture must not hand the rows over pre-sorted"
    expected_ascending = [str(position_id) for position_id in sorted(ids)]

    def _walk_all_pages() -> list[str]:
        collected: list[str] = []
        offset = 0
        while True:
            response = client.get(
                staffing_path(project.id, scenario.id),
                params={"limit": 2, "offset": offset},
                headers=as_caller(IN_SCOPE_USER),
            )
            assert response.status_code == 200, response.text
            body = response.json()
            page_ids = [row["id"] for row in body["positions"]]
            if not page_ids:
                break
            collected.extend(page_ids)
            offset += 2
            if offset >= body["total"]:
                break
        return collected

    first_walk = _walk_all_pages()
    second_walk = _walk_all_pages()

    assert first_walk == expected_ascending, (
        "rows sharing one start_date did not come back in ascending id order — the tie-breaker is "
        "gone, and the pages walked are no longer the total order this endpoint claims"
    )
    assert len(set(first_walk)) == 6, "a row was returned on two pages, or one was lost"
    assert first_walk == second_walk, "the same paged walk produced a different order on repeat"


# --- K-04: the sum of every page equals today's full list


def test_k_04_the_sum_of_every_page_equals_the_full_list_with_no_duplicate_or_loss(
    client: TestClient, db_session: Session
) -> None:
    project, scenario, dimensions = _dimensions_and_draft_scenario(db_session)
    ids = _make_positions(db_session, scenario, dimensions, 11)
    expected = {str(position_id) for position_id in ids}

    collected: list[str] = []
    for offset, size in ((0, 4), (4, 4), (8, 3)):
        response = client.get(
            staffing_path(project.id, scenario.id),
            params={"limit": size, "offset": offset},
            headers=as_caller(IN_SCOPE_USER),
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["total"] == 11, "total drifted between pages of the same, unwritten-to scenario"
        collected.extend(row["id"] for row in body["positions"])

    full = client.get(staffing_path(project.id, scenario.id), headers=as_caller(IN_SCOPE_USER))
    assert full.status_code == 200, full.text
    full_ids = [row["id"] for row in full.json()["positions"]]

    assert len(collected) == 11
    assert set(collected) == expected == set(full_ids)
    assert sorted(collected) == sorted(full_ids)


# --- K-05: pagination is not a second channel confirming scope


def test_k_05_pagination_does_not_become_a_second_channel_confirming_scope(
    client: TestClient, db_session: Session
) -> None:
    project, scenario, dimensions = _dimensions_and_draft_scenario(db_session)
    _make_positions(db_session, scenario, dimensions, 3)
    path = staffing_path(project.id, scenario.id)
    unknown_path = staffing_path(uuid.uuid4(), uuid.uuid4())

    denied_no_params = client.get(path, headers=as_caller(OUT_OF_SCOPE_USER))
    denied_extreme_page = client.get(
        path, params={"limit": 10, "offset": 999}, headers=as_caller(OUT_OF_SCOPE_USER)
    )
    never_existed = client.get(unknown_path, headers=as_caller(OUT_OF_SCOPE_USER))

    for denied in (denied_no_params, denied_extreme_page):
        assert denied.status_code == 404, denied.text
        assert denied.json()["detail"] == STAFFING_NOT_FOUND_DETAIL
        assert denied.text == never_existed.text
        assert denied.headers.get("content-length") == never_existed.headers.get("content-length")

    # Contrast: the identical extreme page, on a scenario the caller *can* see, with fewer positions
    # than the requested page — an empty page, not a 404. Without this half, "always 404" would pass
    # the assertions above too.
    in_scope_extreme_page = client.get(
        path, params={"limit": 10, "offset": 999}, headers=as_caller(IN_SCOPE_USER)
    )
    assert in_scope_extreme_page.status_code == 200, in_scope_extreme_page.text
    body = in_scope_extreme_page.json()
    assert body["positions"] == []
    assert body["total"] == 3


# --- K-06: a parameter outside its bound is refused, never clamped


@pytest.mark.parametrize(
    "params,field",
    [
        ({"limit": 0}, "limit"),
        ({"limit": MAX_STAFFING_POSITION_LIST_LIMIT + 1}, "limit"),
        ({"offset": -1}, "offset"),
        ({"offset": MAX_STAFFING_POSITION_LIST_OFFSET + 1}, "offset"),
    ],
)
def test_k_06_a_pagination_parameter_outside_its_bound_is_refused_not_clamped(
    client: TestClient, db_session: Session, params: dict[str, int], field: str
) -> None:
    project, scenario, dimensions = _dimensions_and_draft_scenario(db_session)
    _make_positions(db_session, scenario, dimensions, 3)

    response = client.get(
        staffing_path(project.id, scenario.id), params=params, headers=as_caller(IN_SCOPE_USER)
    )

    assert response.status_code == 422, response.text
    assert field in response.text


def test_k_06_the_contrast_a_value_exactly_at_the_boundary_is_accepted(
    client: TestClient, db_session: Session
) -> None:
    """K-06's contrast — the ceiling refuses what is past it, not what is at it.

    Without this half, `le=0` (or any bound tightened by accident) would satisfy the refusals above
    while making every realistic request fail.
    """
    project, scenario, dimensions = _dimensions_and_draft_scenario(db_session)
    _make_positions(db_session, scenario, dimensions, 3)

    at_limit_bound = client.get(
        staffing_path(project.id, scenario.id),
        params={"limit": MAX_STAFFING_POSITION_LIST_LIMIT},
        headers=as_caller(IN_SCOPE_USER),
    )
    at_offset_bound = client.get(
        staffing_path(project.id, scenario.id),
        params={"limit": 1, "offset": MAX_STAFFING_POSITION_LIST_OFFSET},
        headers=as_caller(IN_SCOPE_USER),
    )

    assert at_limit_bound.status_code == 200, at_limit_bound.text
    assert at_offset_bound.status_code == 200, at_offset_bound.text
    assert at_offset_bound.json()["positions"] == [], "a page this far out has nothing left on it"


# --- K-08: 404 (scope) takes precedence over 422 (pagination parameters)


def test_k_08_the_404_of_scope_takes_precedence_over_the_422_of_pagination(
    client: TestClient, db_session: Session
) -> None:
    project, scenario, dimensions = _dimensions_and_draft_scenario(db_session)
    _make_positions(db_session, scenario, dimensions, 2)
    path = staffing_path(project.id, scenario.id)

    out_of_scope_invalid = client.get(
        path, params={"limit": -1}, headers=as_caller(OUT_OF_SCOPE_USER)
    )
    in_scope_invalid = client.get(path, params={"limit": -1}, headers=as_caller(IN_SCOPE_USER))

    assert out_of_scope_invalid.status_code == 404, out_of_scope_invalid.text
    assert out_of_scope_invalid.json()["detail"] == STAFFING_NOT_FOUND_DETAIL

    # Contrast: the identical malformed parameter, for the caller who *can* see the scenario, is the
    # 422 — so "always 404" is not what is passing the assertion above.
    assert in_scope_invalid.status_code == 422, in_scope_invalid.text
    assert "limit" in in_scope_invalid.text


# --- S-01 (guardian STOP, gate 2): an unparsable limit must not leak past the scope check ---------


def test_s_01_an_unparsable_limit_does_not_leak_past_the_scope_check(
    client: TestClient, db_session: Session
) -> None:
    """S-01 — the defect named at gate 2: `limit=abc` is not even a well-formed integer, and a
    bare `int | None` type annotation was, on its own, enough for FastAPI/Pydantic to coerce and
    validate it *before* `scenario_in_scope` ever ran — so a caller entirely outside a project's
    scope used to learn (via a `422` instead of the `404` every other malformed request on this
    path answers) that the pagination parameters, at least, were being looked at, for a scenario it
    cannot see at all.

    Empirical, not merely a unit test of `_validated_staffing_position_page` with an in-range int
    (which the S-01 finding explicitly asked not to be the only proof): the request goes through the
    real endpoint, against a scenario genuinely outside `OUT_OF_SCOPE_USER`'s `project_access`.
    """
    project, scenario, dimensions = _dimensions_and_draft_scenario(db_session)
    _make_positions(db_session, scenario, dimensions, 2)
    path = staffing_path(project.id, scenario.id)

    out_of_scope_unparsable = client.get(
        path, params={"limit": "abc"}, headers=as_caller(OUT_OF_SCOPE_USER)
    )

    assert out_of_scope_unparsable.status_code == 404, out_of_scope_unparsable.text
    assert out_of_scope_unparsable.json()["detail"] == STAFFING_NOT_FOUND_DETAIL

    # Contrast: the identical unparsable value, for the caller who *can* see the scenario, is a 422
    # — so "this endpoint always answers 404" is not what the assertion above is really proving.
    in_scope_unparsable = client.get(
        path, params={"limit": "abc"}, headers=as_caller(IN_SCOPE_USER)
    )
    assert in_scope_unparsable.status_code == 422, in_scope_unparsable.text
    detail = in_scope_unparsable.json()["detail"]
    assert isinstance(detail, list) and len(detail) == 1
    assert detail[0]["loc"] == ["query", "limit"]
    assert detail[0]["type"] == "int_parsing"
    assert detail[0]["input"] == "abc"


def test_s_01_the_out_of_scope_404_is_identical_whether_or_not_limit_parses(
    client: TestClient, db_session: Session
) -> None:
    """S-01's own contrast, the other way round: an out-of-scope caller gets the *same* 404 body
    for an unparsable `limit` as for a well-formed one — proving the scope check, and not some
    earlier validation step, is what always answers first.
    """
    project, scenario, dimensions = _dimensions_and_draft_scenario(db_session)
    _make_positions(db_session, scenario, dimensions, 2)
    path = staffing_path(project.id, scenario.id)

    unparsable = client.get(path, params={"limit": "abc"}, headers=as_caller(OUT_OF_SCOPE_USER))
    well_formed_out_of_range = client.get(
        path, params={"limit": -1}, headers=as_caller(OUT_OF_SCOPE_USER)
    )
    never_existed = client.get(
        staffing_path(uuid.uuid4(), uuid.uuid4()), headers=as_caller(OUT_OF_SCOPE_USER)
    )

    for response in (unparsable, well_formed_out_of_range, never_existed):
        assert response.status_code == 404, response.text
    assert unparsable.text == well_formed_out_of_range.text == never_existed.text


# --- R-01 (reviewer, gate 2): the 422 detail is a list, the native FastAPI/Pydantic shape ---------


def test_r_01_the_422_detail_is_a_list_of_objects_not_a_string(
    client: TestClient, db_session: Session
) -> None:
    """R-01 — before this fix this endpoint was the only place in the backend answering a `422`
    with a string `detail`; `frontend/src/api/client.ts::refusalOf` uses exactly that distinction
    (string vs. list) to tell an application refusal (403/404/409) from a validation error (422).

    The exact shape, not merely "it is a list": `type`/`loc`/`msg`/`input`/`ctx`, matching what
    `GET /catalog/rates` answers natively for the identical defect (`ge`/`le` on a `Query(...)`) —
    confirmed empirically against this repository's pinned pydantic/fastapi versions, not assumed.
    """
    project, scenario, dimensions = _dimensions_and_draft_scenario(db_session)
    _make_positions(db_session, scenario, dimensions, 2)

    too_small = client.get(
        staffing_path(project.id, scenario.id),
        params={"limit": 0},
        headers=as_caller(IN_SCOPE_USER),
    )
    too_large = client.get(
        staffing_path(project.id, scenario.id),
        params={"offset": MAX_STAFFING_POSITION_LIST_OFFSET + 1},
        headers=as_caller(IN_SCOPE_USER),
    )

    assert too_small.status_code == 422, too_small.text
    small_detail = too_small.json()["detail"]
    assert small_detail == [
        {
            "type": "greater_than_equal",
            "loc": ["query", "limit"],
            "msg": "Input should be greater than or equal to 1",
            "input": "0",
            "ctx": {"ge": 1},
        }
    ]

    assert too_large.status_code == 422, too_large.text
    large_detail = too_large.json()["detail"]
    assert large_detail == [
        {
            "type": "less_than_equal",
            "loc": ["query", "offset"],
            "msg": f"Input should be less than or equal to {MAX_STAFFING_POSITION_LIST_OFFSET}",
            "input": str(MAX_STAFFING_POSITION_LIST_OFFSET + 1),
            "ctx": {"le": MAX_STAFFING_POSITION_LIST_OFFSET},
        }
    ]


# --- R-02 (reviewer, gate 2): the page is read as a bounded top-N, proven against the real plan ---


def test_r_02_a_page_is_read_as_a_bounded_top_n_not_a_sort_of_the_scenario(
    client: TestClient, db_session: Session
) -> None:
    """R-02 — the docstring on `_staffing_position_page_statement` used to claim "an index scan
    that stops after limit + offset rows" with no index in the schema that could produce
    `ORDER BY start_date, id` filtered by one `scenario_id`; this table carried only the primary
    key (on `id` alone) and the plain, single-column index on `scenario_id`. Migration
    `f1a2c4b6d8e0` adds `STAFFING_POSITION_PAGE_INDEX`, the composite btree the claim needed.

    Same construction as
    `test_catalog_schema_constraints.test_r_01_a_page_is_read_as_a_bounded_top_n_not_a_sort_of_the_whole_catalogue`:
    `enable_seqscan`/`enable_sort` switched off so a plan that still reads the whole scenario proves
    that *no* bounded path exists, and the plan is asked, by name, whether it used
    `STAFFING_POSITION_PAGE_INDEX` — a passing count-of-rows assertion elsewhere in this file cannot
    tell a bounded top-N from a full sort that happens to return the right rows.
    """
    project, scenario, dimensions = _dimensions_and_draft_scenario(db_session)
    scenario_size = 300
    page_size = 10
    _make_positions(db_session, scenario, dimensions, scenario_size)

    answer = client.get(
        staffing_path(project.id, scenario.id),
        params={"limit": page_size},
        headers=as_caller(IN_SCOPE_USER),
    )
    assert answer.status_code == 200, answer.text
    assert len(answer.json()["positions"]) == page_size
    assert answer.json()["total"] == scenario_size, "the answer must describe the whole scenario"

    db_session.execute(sa.text("SET LOCAL enable_seqscan = off"))
    db_session.execute(sa.text("SET LOCAL enable_sort = off"))
    statement = _staffing_position_page_statement(scenario.id, limit=page_size, offset=0)
    compiled = statement.compile(
        bind=db_session.get_bind(), compile_kwargs={"literal_binds": True}
    )
    plan = db_session.execute(sa.text(f"EXPLAIN (ANALYZE, FORMAT JSON) {compiled}")).scalar_one()
    page_branch = _page_branch_nodes(plan[0]["Plan"])

    oversized = [node for node in page_branch if node.get("Actual Rows", 0) > page_size]
    assert not oversized, (
        f"a plan node read more than the {page_size} rows the page asked for, over a scenario of "
        f"{scenario_size} positions: the LIMIT is bounding the response and not the work — "
        f"{oversized}"
    )
    used_page_index = [
        node for node in page_branch if node.get("Index Name") == STAFFING_POSITION_PAGE_INDEX
    ]
    assert used_page_index, (
        f"the page's order did not come from {STAFFING_POSITION_PAGE_INDEX}; without it the rows "
        f"have to be sorted before the LIMIT can cut — {plan}"
    )
