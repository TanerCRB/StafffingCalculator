"""The register of named persons (F-03, SC-2-06; ADR-0019; ADR-0005, addendum 2026-09-27).

- `GET   /people` — one page of the register (`PEOPLE_READ`), `limit`/`offset`/`total` (ADR-0017)
- `POST  /people` — add a person (`PEOPLE_WRITE`)
- `PATCH /people/{person_id}` — correct a person's name, with the concurrency marker
(`PEOPLE_WRITE`)

**Refusal of the resource, not of a field** (ADR-0019, point 4; ADR-0005, addendum 2026-09-27, point
2). Unlike the catalogue — which answers `200` with the cost rate blanked, because "the existence of
a role is not protected data" — a caller without `PEOPLE_READ` gets `403` and no name at all: the
existence of a person in the register *is* personal data. The permissions are declared as
dependencies, so the refusal happens before any row is read.

**Neither permission is granted by the placeholder identity** (point 4): in the running system every
request here is refused. The positive branch is reachable from a test only, through
`app.dependency_overrides[get_caller_identity]` — a named limit, not an accident.

**No project scope, and no guard function** — a person row belongs to no project, user, business
unit or tenant (`app.data.people`). **No name in a URL**: the name travels only in request and
response bodies, never in a path segment or a query parameter, because access logs record URLs
(ADR-0019, point 6). Searching the register by name is out of scope for exactly that reason.

**The register returns persons only** — never the positions, scenarios or projects a person is
assigned to (ADR-0019, "Decision" pt 3).
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.api.response_shaping import shape_person, shape_person_list
from app.api.schemas.people import (
    PersonCorrectionRequest,
    PersonCreateRequest,
    PersonList,
    PersonRead,
)
from app.core.identity import CallerIdentity, Permission
from app.data.people import (
    DEFAULT_PERSON_LIST_LIMIT,
    MAX_PERSON_LIST_LIMIT,
    MAX_PERSON_LIST_OFFSET,
    PersonWriteRefused,
    correct_person_name,
    create_person,
    list_people,
)
from app.db.session import get_session

router = APIRouter(prefix="/people", tags=["people"])

PERSON_NOT_FOUND_DETAIL = "No such person in the register."
"""The `404` of the correction path. Names nothing but the fact — no id echo, no name. Reached only
by a caller holding `PEOPLE_WRITE`; the register has no scope to keep indistinguishable, so one
message for "never existed" is enough."""

_REFUSED_BY_THE_DATABASE = "Refused by the database."


@router.get(
    "",
    response_model=PersonList,
    summary="List one page of the register of named persons",
    responses={403: {"description": "The caller lacks people:read."}},
)
def list_register(
    caller: Annotated[CallerIdentity, Depends(require_permission(Permission.PEOPLE_READ))],
    session: Annotated[Session, Depends(get_session)],
    limit: Annotated[
        int,
        Query(
            ge=1,
            le=MAX_PERSON_LIST_LIMIT,
            description="Rows per page (ADR-0017). Refused (422) outside the bounds, never "
            "clamped. Omitted: a first page of the default size, not the whole register.",
        ),
    ] = DEFAULT_PERSON_LIST_LIMIT,
    offset: Annotated[
        int,
        Query(
            ge=0,
            le=MAX_PERSON_LIST_OFFSET,
            description="Rows to skip before the page. An offset past the end is an empty page.",
        ),
    ] = 0,
) -> PersonList:
    """One page of the register, ordered by name then id, and the total count.

    `caller` is injected although nothing below reads it: the parameter *is* the permission check.
    `limit`/`offset` are validated by FastAPI itself, which is safe here and would not be on a
    scoped resource (ADR-0017, point 8): the register has no scope whose existence a `422` could
    confirm, and the permission dependency runs before parameter validation anyway.
    """
    people, total = list_people(session, limit=limit, offset=offset)
    return shape_person_list(people, total=total)


@router.post(
    "",
    response_model=PersonRead,
    status_code=status.HTTP_201_CREATED,
    summary="Add a person to the register",
    responses={
        403: {"description": "The caller lacks people:write."},
        409: {
            "description": "Refused by the database; the message names the constraint — "
            "`pk_person` for an id already in the register (a retry, D-2 = B: always 409, never a "
            "comparison of names), `ck_person_full_name_canonical` for a name not in "
            "canonical form."
        },
    },
)
def add_person(
    payload: PersonCreateRequest,
    caller: Annotated[CallerIdentity, Depends(require_permission(Permission.PEOPLE_WRITE))],
    session: Annotated[Session, Depends(get_session)],
) -> PersonRead:
    """Add one person. `PEOPLE_WRITE` only: answering with the row just written tells the writer
    nothing it did not send (the id aside) — the known widening of every write permission (ADR-0005,
    addendum 2026-09-19, point 6), here without a second person's data in it.

    A refusal by the database is a `409` naming the SQLSTATE and the constraint, never the name
    (NF-11). A failure no SQLSTATE classifies is a `500` whose exception carries no value either
    (`app.data.people.PersonWriteFailed`).
    """
    try:
        person = create_person(session, person_id=payload.id, full_name=payload.full_name)
    except PersonWriteRefused as refusal:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=f"{_REFUSED_BY_THE_DATABASE} {refusal}"
        ) from None
    return shape_person(person)


@router.patch(
    "/{person_id}",
    response_model=PersonRead,
    summary="Correct the name of one person in the register",
    responses={
        403: {"description": "The caller lacks people:write."},
        404: {"description": PERSON_NOT_FOUND_DETAIL},
        409: {
            "description": "Refused: the person changed since it was read (concurrency marker, "
            "`condition=updated_at_marker`), or by the database (a SQLSTATE and a constraint name)."
        },
    },
)
def correct_person(
    person_id: uuid.UUID,
    payload: PersonCorrectionRequest,
    caller: Annotated[CallerIdentity, Depends(require_permission(Permission.PEOPLE_WRITE))],
    session: Annotated[Session, Depends(get_session)],
) -> PersonRead:
    """Correct one person's name (GDPR art. 16).

    **Succeeds for a person assigned in an approved scenario, and that is the point** (ADR-0004,
    addendum 2026-09-27 SC-2-06, point 3): the person row is not a child of any scenario, so the
    `approved` write guard does not cover it, and the approved scenario — which references the
    person by id and freezes no name — shows the corrected name from then on.

    `404` before `409`: a missing person is never reported as a conflict (ADR-0007).
    """
    try:
        person = correct_person_name(
            session,
            person_id,
            expected_updated_at=payload.updated_at,
            full_name=payload.full_name,
        )
    except PersonWriteRefused as refusal:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=f"{_REFUSED_BY_THE_DATABASE} {refusal}"
        ) from None
    if person is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=PERSON_NOT_FOUND_DETAIL)
    return shape_person(person)
