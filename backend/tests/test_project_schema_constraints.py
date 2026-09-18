"""The guarantees that live in the database, not in the request schema.

`ProjectCreateRequest` refuses a blank name — but it only ever sees requests. A fixture, a seed
script, a future import or any second write path writes straight to the table, and until
migration `4f0a9c1b7d62` nothing there said `''` was not a name. These tests bypass the API
entirely and write through the ORM, which is the closest available stand-in for those paths.

Real PostgreSQL, real migration (ADR-0001): a CHECK constraint cannot be proven against a mock
that does not implement it.
"""

import uuid
from datetime import date

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Project

BLANK_VALUES = ["", "   ", "\t", "\n", " \t\n "]


def _project(**overrides: object) -> Project:
    """A valid project row, one field at a time replaced by the test."""
    fields: dict[str, object] = {
        "id": uuid.uuid4(),
        "name": "Aurora migration",
        "client": "Northwind",
        "owner": "Anna Kowalska",
        "delivery_period_start": date(2026, 3, 1),
        "delivery_period_end": date(2026, 11, 30),
        "reporting_currency": "EUR",
        "description": "",
    }
    return Project(**(fields | overrides))


@pytest.mark.parametrize("field", ["name", "client", "owner"])
@pytest.mark.parametrize("blank", BLANK_VALUES)
def test_database_refuses_a_blank_project_name_client_or_owner(
    db_session: Session, field: str, blank: str
) -> None:
    """Written through the ORM, with no `ProjectCreateRequest` anywhere in the path.

    The failure has to come from the constraint and not from something else, so the error is
    asserted to name it. `'\\t'` matters as much as `''`: the boundary trims all whitespace
    before checking, so a database that only rejected `''` would disagree with the API about
    what a blank name is.
    """
    db_session.add(_project(**{field: blank}))

    with pytest.raises(IntegrityError) as error:
        db_session.flush()

    assert f"ck_projects_{field}_not_blank" in str(error.value)
    db_session.rollback()


@pytest.mark.parametrize("field", ["name", "client", "owner"])
def test_database_accepts_the_same_row_once_the_field_has_content(
    db_session: Session, field: str
) -> None:
    """The contrast, one character apart: a constraint that rejected everything would satisfy
    the test above just as well."""
    project = _project(**{field: "A"})
    db_session.add(project)

    db_session.flush()
    db_session.expire_all()

    stored = db_session.get(Project, project.id)
    assert stored is not None
    assert getattr(stored, field) == "A"


def test_blank_description_stays_allowed(db_session: Session) -> None:
    """`description` is F-01's one optional field and must keep accepting `''` — the new
    constraints cover exactly three columns, and a blanket "no empty text" rule would quietly
    make an optional field mandatory."""
    db_session.add(_project(description=""))

    db_session.flush()
