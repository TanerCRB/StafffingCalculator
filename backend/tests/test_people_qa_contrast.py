"""SC-2-06 (Issue #31) — contrast tests added by QA, beside the developer's two files.

Two contrasts the suite did not have:

1. **K-09, the correction path.** `test_k_09_the_contrast_the_driver_error_itself_quotes_the_name`
   proves that PostgreSQL leaks the name on a refused `INSERT`; nothing proved the same for a
   refused `UPDATE`, so `test_k_09_a_failed_name_correction_does_not_log_the_old_or_the_new_name`
   had no channel shown to be live for its own statement. The contrast below shows the channel is
   live for the *new* name — and that the *old* name is not in the failing row at all, so the "old
   name" half of that test cannot fail through this channel (a limit, recorded here, not a defect).
2. **K-03, the assignment endpoint's own response.** Since D-1 = A (2026-09-28) the endpoint
   itself requires `STAFFING_READ`, so the caller without it gets a `403` rather than a hidden
   field; the contrast still differs in one permission only (`STAFFING_READ`).

Every person here is fictitious (ADR-0019, point 8).
"""

import uuid

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.core.identity import Permission
from tests.conftest import (
    FICTITIOUS_PERSON_NAME,
    IN_SCOPE_USER,
    caller_holding,
    make_dimension_tuple,
    make_person,
    make_project,
    make_scenario,
    make_staffing_position,
    staffing_path,
)
from tests.test_people_register import LEAKY_NAME


def test_k_09_contrast_a_refused_raw_update_quotes_the_new_name_and_not_the_old_one(
    engine: Engine, database_url: str
) -> None:
    """Contrast for the correction half of K-09: the same refused `UPDATE` the write path makes,
    straight through the driver with `hide_parameters=True`. PostgreSQL's `DETAIL: Failing row
    contains (…)` quotes the **new** name; the old one is not in the failing row. The
    `engine` fixture is requested only so the migrations have run."""
    raw = sa.create_engine(database_url, hide_parameters=True)
    person_id = uuid.uuid4()
    try:
        with pytest.raises(sa.exc.StatementError) as error:
            with raw.begin() as connection:
                connection.execute(
                    sa.text("INSERT INTO person (id, full_name) VALUES (:id, :name)"),
                    {"id": person_id, "name": FICTITIOUS_PERSON_NAME},
                )
                connection.execute(
                    sa.text("UPDATE person SET full_name = :name WHERE id = :id"),
                    {"id": person_id, "name": LEAKY_NAME},
                )
    finally:
        raw.dispose()
    message = str(error.value)
    assert "Failing row contains" in message
    assert LEAKY_NAME.strip() in message
    assert "[parameters: " not in message
    assert FICTITIOUS_PERSON_NAME not in message


def test_k_03_the_assignment_response_shows_the_person_only_with_staffing_read_as_well(
    client: TestClient, db_session: Session
) -> None:
    """K-03 on the assignment endpoint's own answer — re-worded after D-1 = A (human decision
    2026-09-28; ADR-0005 aneks 2026-09-28, point 1): the endpoint now *requires* `STAFFING_READ`, so
    the caller without it is refused `403` before anything is read or written, instead of getting a
    `200` with the person hidden. Two callers differing in `STAFFING_READ` only: the first gets a
    `403` naming the permission and no person id, and its position stays anonymous; the second gets
    `200` with the id. The database is checked for both."""
    project = make_project(db_session, name="Aurora", accessible_to=(IN_SCOPE_USER,))
    scenario = make_scenario(db_session, project, name="Baseline")
    dimensions = make_dimension_tuple(db_session)
    first = make_staffing_position(db_session, scenario, dimensions, headcount=1)
    second = make_staffing_position(db_session, scenario, dimensions, headcount=1)
    person = make_person(db_session)
    base = staffing_path(project.id, scenario.id)

    def assign(position: object) -> object:
        return client.patch(
            f"{base}/{position.id}/person",
            json={
                "person_assignment_updated_at": position.person_assignment_updated_at.isoformat(),
                "person_id": str(person.id),
            },
        )

    with caller_holding(Permission.STAFFING_WRITE, Permission.PEOPLE_READ):
        refused = assign(first)
    with caller_holding(
        Permission.STAFFING_WRITE, Permission.PEOPLE_READ, Permission.STAFFING_READ
    ):
        shown = assign(second)

    assert refused.status_code == 403, refused.text
    assert Permission.STAFFING_READ.value in refused.text
    assert str(person.id) not in refused.text
    assert shown.status_code == 200, shown.text
    assert shown.json()["person_id"] == str(person.id)

    db_session.expire_all()
    stored = dict(
        db_session.execute(
            sa.text("SELECT id, person_id FROM staffing_position WHERE scenario_id = :id"),
            {"id": scenario.id},
        ).all()
    )
    assert stored == {first.id: None, second.id: person.id}
