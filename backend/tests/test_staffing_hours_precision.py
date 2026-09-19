"""SC-3-01, S-01 — the precision bound on hours, asserted instead of cited.

`HoursAmount` in `app.api.schemas.staffing` carried `max_digits`/`decimal_places` with a comment
pointing at R-05, a defect measured in *another* table's suite (SC-2-01, `catalog_default_rates`).
Invariant Guardian S-01 (2026-09-19): a bound whose only evidence is a comment about a different
column is not evidence. This file is the missing half, in the shape the catalogue's own tests use
(`test_catalog_schema_constraints.py::test_r_05_…`), adjusted to `NUMERIC(10,2)`.

Two distinct claims, and they fail differently:

1. **More precision than the column can keep is refused, never truncated.** `7.125` stored as `7.13`
   is a value nobody entered, in a column nobody can audit afterwards — and hours are one
   multiplication away from money (NF-01, ADR-0002), so the rounding would reappear in every cost
   and revenue figure derived from the row. The refusal is a `422` naming the field; the
   alternative is not an error but a *silent* change of input.
2. **More integer digits than the column can hold is a `422`, not a `500`.** Without `max_digits`
   the value reaches PostgreSQL and comes back as SQLSTATE `22003`, deliberately absent from
   `app.data.write_errors.REFUSAL_BY_SQLSTATE` (R-01: an unclassified failure must not be dressed up
   as a conflict) — so it is served as a `500` for a request that is simply out of range.

Both come with the contrast that keeps them from passing by refusing everything: exactly two decimal
places, and trailing zeros beyond them, are accepted and kept.
"""

from decimal import Decimal

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.staffing import (
    HOURS_COLUMNS,
    HOURS_PRECISION,
    HOURS_SCALE,
    StaffingPositionAllocation,
)
from tests.conftest import (
    IN_SCOPE_USER,
    as_caller,
    count_allocations,
    count_positions,
    make_dimension_tuple,
    make_project,
    make_scenario,
    staffing_path,
    staffing_position_payload,
)

TOO_PRECISE = ["7.125", "160.001", "0.005", "120.4567"]
"""Values with more decimal places than `NUMERIC(10,2)` can keep.

`0.005` is here for the reason `0.00001` is in the catalogue's list: it rounds to `0.00`, i.e. to
*no work at all* — the point where silent truncation stops being a rounding error and becomes a
different fact about the plan."""

TOO_WIDE = "12345678901.00"
"""Eleven integer digits — `NUMERIC(10,2)` holds eight. Refused at the boundary, so it never becomes
the `22003` that used to arrive as a `500`."""


def _draft_scenario(session: Session):
    project = make_project(session, name="Aurora migration", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(session, project, name="Baseline")
    return project, scenario, make_dimension_tuple(session)


def _month(**overrides: object) -> dict[str, object]:
    month: dict[str, object] = {
        "period_month": "2026-03-01",
        "availability_hours": "160.00",
        "planned_allocation_hours": "120.00",
        "billable_hours": "100.00",
    }
    return month | overrides


@pytest.mark.parametrize("column", HOURS_COLUMNS)
@pytest.mark.parametrize("amount", TOO_PRECISE)
def test_s_01_an_hours_figure_more_precise_than_the_column_is_refused_not_truncated(
    client: TestClient, db_session: Session, column: str, amount: str
) -> None:
    """Claim 1, once per hour column: the bound is on the *type*, so all three inherit it.

    Parametrised over the columns as well as the values because `HoursAmount` is one annotated type
    used three times — a fourth hour figure added later without it would be visible here, and so
    would a `Field(...)` quietly dropped from one of the three.
    """
    project, scenario, dimensions = _draft_scenario(db_session)

    response = client.post(
        staffing_path(project.id, scenario.id),
        json=staffing_position_payload(dimensions, allocations=[_month(**{column: amount})]),
        headers=as_caller(IN_SCOPE_USER),
    )

    assert response.status_code == 422, response.text
    assert "decimal" in response.text.lower()
    assert count_positions(db_session) == 0
    assert count_allocations(db_session) == 0


def test_s_01_exactly_two_decimal_places_are_accepted_and_kept_exactly(
    client: TestClient, db_session: Session
) -> None:
    """Claim 1's contrast: the boundary refuses *more* precision than the column, not its own.

    Three values in one request — a half hour, a quarter hour, and a figure whose trailing zeros go
    past scale 2 without adding precision (`100.000000` *is* representable at scale 2, so refusing
    it would be refusing a spelling rather than a value). Read back from the row and from the
    payload: the response must agree with the row, down to the trailing zeros the column adds.

    Without this half, `decimal_places=0` would satisfy every refusal above while making the
    endpoint useless.
    """
    project, scenario, dimensions = _draft_scenario(db_session)

    response = client.post(
        staffing_path(project.id, scenario.id),
        json=staffing_position_payload(
            dimensions,
            allocations=[
                _month(
                    availability_hours="160.50",
                    planned_allocation_hours="120.25",
                    billable_hours="100.000000",
                )
            ],
        ),
        headers=as_caller(IN_SCOPE_USER),
    )

    assert response.status_code == 201, response.text
    allocation = response.json()["allocations"][0]
    assert (
        allocation["availability_hours"],
        allocation["planned_allocation_hours"],
        allocation["billable_hours"],
    ) == ("160.50", "120.25", "100.00")
    stored = db_session.execute(
        sa.select(
            StaffingPositionAllocation.availability_hours,
            StaffingPositionAllocation.planned_allocation_hours,
            StaffingPositionAllocation.billable_hours,
        )
    ).one()
    assert tuple(stored) == (Decimal("160.50"), Decimal("120.25"), Decimal("100.00"))


@pytest.mark.parametrize("column", HOURS_COLUMNS)
def test_s_01_an_hours_figure_wider_than_the_column_is_a_422_about_the_field_not_a_500(
    client_serving_server_errors: TestClient, db_session: Session, column: str
) -> None:
    """Claim 2, once per column — and served through the client that does **not** re-raise.

    `client_serving_server_errors` is the fixture in which an unhandled exception comes back as a
    `500` response instead of propagating (the distinction R-01 exists for). With the plain
    `client`, a regression here surfaces as an exception escaping the test rather than as a wrong
    status code, which reads like a broken test instead of a wrong answer to the caller.
    """
    project, scenario, dimensions = _draft_scenario(db_session)

    response = client_serving_server_errors.post(
        staffing_path(project.id, scenario.id),
        json=staffing_position_payload(dimensions, allocations=[_month(**{column: TOO_WIDE})]),
        headers=as_caller(IN_SCOPE_USER),
    )

    assert response.status_code == 422, response.text
    assert column in response.text
    assert count_positions(db_session) == 0
    assert count_allocations(db_session) == 0


@pytest.mark.parametrize("column", HOURS_COLUMNS)
def test_s_01_the_allocation_edit_path_bounds_precision_the_same_way(
    client: TestClient, db_session: Session, column: str
) -> None:
    """The same bound on the *other* write path — `PATCH` shares `HoursAmount`, which is the claim.

    An edit is where silent truncation would be hardest to notice: the caller sees a `200`, the grid
    shows a value one hundredth away from the one they typed, and no error was ever raised. The old
    value is asserted to be still in the row, so a refusal that had already written cannot pass.
    """
    from tests.conftest import allocation_path, make_allocation, make_staffing_position

    project, scenario, dimensions = _draft_scenario(db_session)
    position = make_staffing_position(db_session, scenario, dimensions)
    allocation = make_allocation(db_session, position, period_month=position.start_date)
    before = getattr(allocation, column)
    token = client.get(
        staffing_path(project.id, scenario.id), headers=as_caller(IN_SCOPE_USER)
    ).json()["positions"][0]["updated_at"]

    response = client.patch(
        allocation_path(project.id, scenario.id, position.id, position.start_date),
        json={"updated_at": token, column: "7.125"},
        headers=as_caller(IN_SCOPE_USER),
    )

    assert response.status_code == 422, response.text
    assert "decimal" in response.text.lower()
    db_session.expire_all()
    reread = db_session.get(StaffingPositionAllocation, allocation.id)
    assert reread is not None
    assert getattr(reread, column) == before


def test_the_api_bound_and_the_column_scale_are_the_same_number() -> None:
    """The bound is taken from the column's own constants, not typed out beside them.

    `HoursAmount` builds `decimal_places` from `HOURS_SCALE`, so the boundary cannot drift away from
    the storage it protects. This asserts the link rather than the number: a migration that changed
    the column's scale without touching the schema would fail here instead of starting to truncate.
    """
    from app.api.schemas.staffing import HoursAmount

    # The bound sits inside the `FieldInfo` the annotation carries, so the search goes one level
    # down rather than assuming a position in the annotation's metadata tuple.
    bounds = [
        item
        for annotated in HoursAmount.__metadata__
        for item in getattr(annotated, "metadata", [])
        if hasattr(item, "decimal_places")
    ]

    assert bounds, "HoursAmount lost its precision bound"
    assert bounds[0].decimal_places == HOURS_SCALE
    assert bounds[0].max_digits == HOURS_PRECISION
    assert (HOURS_PRECISION, HOURS_SCALE) == (10, 2), (
        "the column's precision changed — the migration and this bound have to move together"
    )
