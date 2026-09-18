"""A failed project write must not put field values into anything a logger prints (NF-11).

The path this closes: a write to `projects` fails — a dropped connection, a statement timeout, a
NUL byte in a text field, a violated constraint — and the exception propagates out of the request
unhandled. Whatever prints it (uvicorn's traceback, an error tracker, a support ticket somebody
pastes it into) prints the bound values with it, and for this table that is the owner's name and
the project description. Reported independently by the Invariant Guardian (S-01), the Security
Auditor (B-01) and the Reviewer (R-01).

There are **two** channels, and the fix asked for closes one of them:

1. SQLAlchemy's own `[parameters: (…)]` echo, appended to every `StatementError` —
   closed by `hide_parameters=True` on the engine (`app.db.session`).
2. PostgreSQL's `DETAIL: Failing row contains (…)`, which the *server* attaches to a constraint
   violation and psycopg carries in the exception it raises. `hide_parameters` does not touch it;
   it is proven below that it does not. Closed by `app.data.project_writes` refusing to let the
   driver's exception escape at all.

All of these use a real PostgreSQL: the exact text of a driver error is the thing under test, so
a mock would be proving its own author's assumptions.
"""

import traceback
import uuid
from datetime import date

import pytest
import sqlalchemy as sa
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.core.identity import CallerIdentity, Permission
from app.data.project_writes import ProjectWriteFailed, create_project
from app.db.session import _build_engine, get_engine

OWNER_NAME = "Katarzyna Nowak-Personal-Data"
DESCRIPTION = "Confidential: rates negotiated with Northwind."
CALLER = CallerIdentity(user_id="pm-anna", permissions=frozenset({Permission.PROJECT_CREATE}))


def _failing_insert(engine: sa.Engine) -> str:
    """Run an INSERT into `projects` the database refuses, and return the error message.

    The refusal is a violated CHECK constraint (`delivery_period_end` before
    `delivery_period_start`), chosen because it happens server-side *after* the values have been
    bound and sent — the same position in the exchange as a timeout or a lost connection, unlike
    a client-side validation error that never has parameters attached in the first place.
    """
    statement = sa.text(
        "INSERT INTO projects (id, name, client, owner, delivery_period_start,"
        " delivery_period_end, reporting_currency, description, status)"
        " VALUES (:id, :name, :client, :owner, :start, :end, :currency, :description, 'active')"
    )
    parameters = {
        "id": uuid.uuid4(),
        "name": "Aurora migration",
        "client": "Northwind",
        "owner": OWNER_NAME,
        "start": date(2026, 11, 30),
        "end": date(2026, 3, 1),  # violates ck_projects_delivery_period_ordered
        "currency": "EUR",
        "description": DESCRIPTION,
    }
    with pytest.raises(sa.exc.StatementError) as error:
        with engine.begin() as connection:
            connection.execute(statement, parameters)
    return str(error.value)


@pytest.fixture
def migrated_url(engine: Engine, database_url: str) -> str:
    """The container's URL, with the migrations already applied.

    Depending on `engine` is what guarantees the tables exist: `database_url` alone hands out a
    connection string to an empty database, and an INSERT into a missing table fails for the
    wrong reason.
    """
    return database_url


def test_hide_parameters_removes_sqlalchemys_own_echo_of_the_bound_values(
    migrated_url: str,
) -> None:
    """Channel 1, closed: no `[parameters: …]` section, and the statement still readable."""
    engine = sa.create_engine(migrated_url, hide_parameters=True)
    try:
        message = _failing_insert(engine)
    finally:
        engine.dispose()

    assert "[parameters: " not in message
    assert "hidden due to hide_parameters" in message
    # Still diagnosable: the failing statement and the reason it failed remain.
    assert "INSERT INTO projects" in message
    assert "delivery_period_ordered" in message


def test_without_the_flag_sqlalchemy_echoes_every_bound_value(migrated_url: str) -> None:
    """The contrast, and the reason the flag is not decoration: the default really does print
    the personal data. If this test ever stops leaking, SQLAlchemy changed its default and the
    test above has stopped proving anything — better a failing test than a silent pass."""
    engine = sa.create_engine(migrated_url)
    try:
        message = _failing_insert(engine)
    finally:
        engine.dispose()

    assert "[parameters: " in message
    assert OWNER_NAME in message
    assert DESCRIPTION in message


def test_hide_parameters_does_not_stop_postgresql_quoting_the_failing_row(
    migrated_url: str,
) -> None:
    """Channel 2, still open at engine level — the evidence, not an accepted state.

    This is why `hide_parameters=True` alone does not satisfy the finding: with the flag on, the
    message still carries `DETAIL: Failing row contains (…, Katarzyna Nowak-Personal-Data, …)`,
    because the *server* composed that line and psycopg only relays it. The write path is what
    keeps this message from escaping (see the two tests below).

    If this assertion ever fails, PostgreSQL or psycopg stopped relaying the row and the
    sanitizing wrapper became belt-and-braces rather than the actual guard — worth knowing.
    """
    engine = sa.create_engine(migrated_url, hide_parameters=True)
    try:
        message = _failing_insert(engine)
    finally:
        engine.dispose()

    assert "Failing row contains" in message
    assert OWNER_NAME in message


def test_a_failed_create_project_raises_an_error_quoting_no_field_values(
    db_session: Session,
) -> None:
    """The write path, end to end: a real constraint violation, a real rollback, and an
    exception whose *entire formatted traceback* contains none of the row.

    `traceback.format_exception` is what the assertion is made on rather than `str(error)`,
    because a chained exception leaks through the traceback even when the top-level message is
    clean — that is precisely the mistake `raise … from None` avoids.
    """
    with pytest.raises(ProjectWriteFailed) as error:
        create_project(
            db_session,
            CALLER,
            name="Aurora migration",
            client="Northwind",
            owner=OWNER_NAME,
            # Refused by the database, not by the API schema: this call bypasses
            # `ProjectCreateRequest` entirely.
            delivery_period_start=date(2026, 11, 30),
            delivery_period_end=date(2026, 3, 1),
            reporting_currency="EUR",
            description=DESCRIPTION,
        )

    rendered = "".join(traceback.format_exception(error.value))
    assert OWNER_NAME not in rendered
    assert DESCRIPTION not in rendered
    assert "Northwind" not in rendered
    assert "Failing row contains" not in rendered


def test_the_sanitized_write_error_still_names_what_refused_the_write(
    db_session: Session,
) -> None:
    """The contrast: hiding the values must not turn the failure into "something went wrong".

    SQLSTATE and the constraint name are identifiers, never row values — they are what makes the
    failure actionable, and a sanitizer that dropped them too would be traded for a suppression.
    """
    with pytest.raises(ProjectWriteFailed) as error:
        create_project(
            db_session,
            CALLER,
            name="Aurora migration",
            client="Northwind",
            owner=OWNER_NAME,
            delivery_period_start=date(2026, 11, 30),
            delivery_period_end=date(2026, 3, 1),
            reporting_currency="EUR",
            description=DESCRIPTION,
        )

    message = str(error.value)
    assert "ck_projects_delivery_period_ordered" in message
    assert "sqlstate=23514" in message  # check_violation


def test_the_application_engine_is_built_with_parameters_hidden() -> None:
    """The flag has to be on the engine the application actually uses, not only on engines built
    inside a test. `create_engine` does not connect, so this asks the real factory without
    needing a database."""
    _build_engine.cache_clear()
    try:
        assert get_engine().hide_parameters is True
    finally:
        _build_engine.cache_clear()
