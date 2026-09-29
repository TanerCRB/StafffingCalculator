"""Reading and writing the register of named persons (F-03, SC-2-06; ADR-0019).

**No guard function, like `app.data.catalog`, and for the same structural reason** (ADR-0005,
addendum 2026-09-27 SC-2-06, point 2; ADR-0001, addendum 2026-09-19 SC-2-01): a person row has no
column tying it to a project, a user, a business unit or a tenant, so there is no per-caller
predicate to forget.
What protects the register is the permission pair `PEOPLE_READ`/`PEOPLE_WRITE`, declared on every
endpoint (`app.api.people`) — a refusal of the whole resource, never a blanked field.

**What this module returns: persons, and nothing else** (ADR-0019, "Decision" pt 3). Never the
positions, scenarios or projects a person is assigned to. The first reverse query (person →
assignments) must go through `project_for_caller` per project and needs its own annex first.

**NF-11 — the name never reaches a log.** Every write classifies a failure through
`app.data.write_errors` and re-raises it `from None`, so PostgreSQL's `DETAIL: Failing row contains
(…)` — which for this table *is* the name — never travels in the exception or its traceback. The
engine's `hide_parameters=True` (`app.db.session`) closes the other channel. The messages built here
name the SQLSTATE, the constraint and the condition, never a value.
"""

import uuid
from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.data.write_errors import (
    CONCURRENCY_MARKER_CONDITION,
    CONCURRENCY_MARKER_REASON,
    WriteFailed,
    WriteRefused,
    failure_for,
    refusal_by_condition,
)
from app.models.person import Person

_PERSON_TABLE = Person.__table__

_SUBJECT = "person"
"""The subject every failure message names — "Writing the person failed: …". Never the name."""

DEFAULT_PERSON_LIST_LIMIT = 200
"""The page size when a caller names neither `limit` nor `offset` (ADR-0017, points 3 and 6).

**Default: a first page, not the whole register** — the decision ADR-0017 leaves to each consumer.
SC-3-05 kept "the whole grid" because a frontend already consumed the unpaginated shape; the
register is new, has no consumer to break, and is exactly the kind of list that grows without a
natural bound. The value is NF-03's reference scale, the same one `DEFAULT_STAFFING_POSITION_LIST_
LIMIT` uses."""

MAX_PERSON_LIST_LIMIT = 1000
"""The ceiling `limit` may name — refused (`422`) above it, never clamped (ADR-0017, point 7)."""

MAX_PERSON_LIST_OFFSET = 1_000_000
"""The ceiling `offset` may name — refused (`422`) above it, for the reasons
`app.data.staffing.MAX_STAFFING_POSITION_LIST_OFFSET` gives."""


class PersonWriteFailed(WriteFailed):
    """A write to the register failed for a reason nothing here established — a `500`.

    The message carries identifiers only (error class, SQLSTATE, constraint name). A person row
    carries one value worth protecting and it is the name, so this is the whole NF-11 claim for the
    table (criterion K-09)."""


class PersonWriteRefused(PersonWriteFailed, WriteRefused):
    """The database refused the write for a reason its SQLSTATE names — a `409`.

    The realistic case over HTTP is none: the request schema trims and bounds the name before it
    gets here. It exists for the writer that does not pass through the schema (a fixture, a seed
    script, a future import) — the database's `ck_person_full_name_canonical` is the guarantee, the
    schema only the status code."""


class PersonConcurrentEditConflict(PersonWriteRefused):
    """The person changed since the caller read it (ADR-0007) — a `409`, named by condition.

    A subclass, so an endpoint keeps one `except PersonWriteRefused` branch; the message names
    `condition=updated_at_marker` where a driver-reported refusal names a SQLSTATE. Carries nothing
    about the competing change — least of all the name somebody else wrote."""


def _failure(error: SQLAlchemyError) -> WriteFailed:
    """Classify one failed register write by SQLSTATE — spelled once for both write paths."""
    return failure_for(
        error, subject=_SUBJECT, refused=PersonWriteRefused, failed=PersonWriteFailed
    )


def _person_page_statement(*, limit: int, offset: int) -> sa.Select[tuple[uuid.UUID, int]]:
    """The one statement deciding which person ids are on this page, and the total (ADR-0017,
    point 5): `total` is a scalar subquery in the same `SELECT`, evaluated in the same snapshot.

    Ordered by `full_name`, then `id` — a total order (point 2), because two people may share a
    name (ADR-0019, point 3) and a name alone would let a page boundary fall between them
    differently on two reads."""
    page = (
        sa.select(Person.id, Person.full_name)
        .order_by(Person.full_name, Person.id)
        .limit(limit)
        .offset(offset)
        .subquery()
    )
    total = sa.select(sa.func.count()).select_from(Person).scalar_subquery()
    return sa.select(page.c.id, total.label("total")).order_by(page.c.full_name, page.c.id)


def list_people(
    session: Session, *, limit: int = DEFAULT_PERSON_LIST_LIMIT, offset: int = 0
) -> tuple[Sequence[Person], int]:
    """One page of the register, ordered by name then id, and the count of every person.

    Every row for every caller holding `PEOPLE_READ`: there is no `WHERE` narrowing this by
    identity, because nothing on the row could be narrowed by (module docstring). An empty page
    (`offset` past the end) is `([], total)`, never an error.
    """
    page_rows = session.execute(_person_page_statement(limit=limit, offset=offset)).all()
    if not page_rows:
        total = session.execute(sa.select(sa.func.count()).select_from(Person)).scalar_one()
        return [], total
    ids = [row.id for row in page_rows]
    by_id = {
        person.id: person
        for person in session.execute(sa.select(Person).where(Person.id.in_(ids))).scalars()
    }
    return [by_id[person_id] for person_id in ids], page_rows[0].total


def create_person(session: Session, *, person_id: uuid.UUID, full_name: str) -> Person:
    """Insert one person under the id the client chose, and commit — or raise a failure that quotes
    no value.

    **The id comes from the client** (ADR-0019 addendum 2026-09-28, D-2 = B), so a retried request
    hits `pk_person` and is refused (`PersonWriteRefused`, SQLSTATE `23505` → `409`) instead of
    creating the same person twice. **Always a refusal, whatever name the retry carries**: nothing
    here compares the sent name with the stored one — that comparison would tell a caller holding
    `PEOPLE_WRITE` but not `PEOPLE_READ` what the person under that id is called. The refusal names
    the constraint only; it carries neither name, nor the stored row, nor its marker, so its text is
    identical for an identical and for a different name, and distinguishable from the canonical-form
    refusal (`23514`, `ck_person_full_name_canonical`).

    A Core `INSERT`, not `session.add`: an ORM add of an id already in the session's identity map
    fails in the ORM (a `FlushError` with no SQLSTATE → a `500`) before the database can refuse it.
    The commit is here, as in `app.data.catalog.create_dimension_entry`: "the row is persisted" is
    this layer's claim. `created_at`/`updated_at` come from the database's clock.
    """
    try:
        session.execute(sa.insert(_PERSON_TABLE).values(id=person_id, full_name=full_name))
        session.commit()
    except SQLAlchemyError as error:
        session.rollback()
        # `from None`: a chained exception is printed together with its cause, and the cause's
        # `DETAIL: Failing row contains (…)` is the name (NF-11, criterion K-09); for the primary
        # key it is `Key (id)=(…)`, which is not echoed either.
        raise _failure(error) from None
    person = session.get(Person, person_id)
    if person is None:
        # Not an `assert` (stripped under `python -O`): a committed row that cannot be read back is
        # a defect, answered as a `500` that names no value.
        raise PersonWriteFailed("Writing the person failed: the committed row is not readable.")
    session.refresh(person)
    return person


def correct_person_name(
    session: Session,
    person_id: uuid.UUID,
    *,
    expected_updated_at: datetime,
    full_name: str,
) -> Person | None:
    """Correct one person's name (RODO art. 16) — `None` if no such person, a refusal if the
    concurrency marker moved.

    **No `approved` guard, and that absence is the decision** (ADR-0004, addendum 2026-09-27
    SC-2-06, point 3; ADR-0019, point 7): a person row is not a child of any scenario, and a
    guard here would block the correction of anybody ever assigned to an approved scenario. An
    approved scenario
    shows the corrected name — it references the person by `id` and freezes no name.

    `None` ("no such person") is established before the marker is looked at, so a missing row is a
    `404` and never a `409` (ADR-0007, "Konsekwencje") — the existence read is not the guard. The
    guard is the `WHERE updated_at = :expected` of the single `UPDATE` below, compared by the
    database in the same statement as the write, never in Python.
    """
    if session.get(Person, person_id) is None:
        return None
    statement = (
        sa.update(_PERSON_TABLE)
        .where(_PERSON_TABLE.c.id == person_id, _PERSON_TABLE.c.updated_at == expected_updated_at)
        .values(full_name=full_name, updated_at=sa.func.now())
        .returning(_PERSON_TABLE.c.id)
    )
    try:
        applied = session.execute(statement).one_or_none()
        if applied is None:
            # Nothing written to undo; the refusal below is not a `SQLAlchemyError`.
            raise refusal_by_condition(
                subject=_SUBJECT,
                condition=CONCURRENCY_MARKER_CONDITION,
                reason=CONCURRENCY_MARKER_REASON,
                refused=PersonConcurrentEditConflict,
            )
        session.commit()
    except SQLAlchemyError as error:
        session.rollback()
        raise _failure(error) from None
    person = session.get(Person, person_id)
    if person is not None:
        session.refresh(person)
    return person
