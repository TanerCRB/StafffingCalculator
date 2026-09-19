"""Catalogue endpoints — role dimensions and default rates (F-03, SC-2-01).

- `GET  /catalog/dimensions/{dimension}` — one dictionary's entries (`CATALOG_READ`)
- `POST /catalog/dimensions/{dimension}` — add an entry (`CATALOG_WRITE`)
- `GET  /catalog/rates` — every rate, optionally only those effective on a given day
(`CATALOG_READ`)
- `GET  /catalog/rates/effective` — the one rate for one dimension tuple on one day (`CATALOG_READ`)
- `POST /catalog/rates` — add a rate window (`CATALOG_WRITE`)

Every endpoint declares its permission as a dependency, so the check cannot be reached around; none
builds a query of its own (they go through `app.data.catalog`) and none builds a payload of its own
(they go through `app.api.response_shaping`, which is where the cost-rate field is removed).

**No `project_access` filter anywhere here, on purpose.** A catalogue row belongs to no project, so
there is no scope to apply, and a caller holding `CATALOG_READ` with zero project access sees
exactly the same rows as anybody else (ADR-0005, addendum 2026-09-19, point 1 — criterion K-01).
This is the first set of data in the system for which that is true; the first catalogue column that
ties a row to a project, a business unit or a tenant ends the exception.

Refusals are also shaped differently from the project endpoints: a dimension entry that does not
exist is a plain `404` naming what was not found, because the existence of a role or a location
is not protected data and the indistinguishable-from-nonexistent rule of ADR-0005 (addendum
2026-09-18, point 3) is about projects outside a caller's scope (addendum 2026-09-19, point 5)."""

import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.api.response_shaping import shape_catalog_rate, shape_catalog_rate_list
from app.api.schemas.catalog import (
    CatalogRate,
    CatalogRateCreateRequest,
    CatalogRateList,
    DimensionEntry,
    DimensionEntryCreateRequest,
    DimensionEntryList,
)
from app.core.identity import CallerIdentity, Permission
from app.data.catalog import (
    DIMENSION_MODELS,
    CatalogWriteRefused,
    create_dimension_entry,
    create_rate,
    list_dimension_entries,
    list_rates,
    resolve_rate,
)
from app.db.session import get_session

router = APIRouter(prefix="/catalog", tags=["catalog"])

DimensionSegment = Annotated[
    str,
    Path(
        description="Which dimension dictionary: " + ", ".join(sorted(DIMENSION_MODELS)),
    ),
]
"""The dictionary is a path segment, not a body field, and the four dictionaries share one pair of
endpoints.

Why one pair rather than eight endpoints: the permission dependency is then declared once per verb.
Eight endpoints are eight chances for the fourth one to be added without a guard, and nothing in
FastAPI would notice. The mapping from segment to table is `app.data.catalog.DIMENSION_MODELS`.

A `str` validated against that mapping rather than a Python `Enum` of the four segments: an `Enum`
would render an unknown segment as a `422` naming the four valid ones, which reads like a schema
statement about the catalogue's *contents*. NF-10 keeps the contents data; the *kinds* are four
columns of the rate table, and they are validated below with a `404`, which is what an unknown
collection is."""

_REFUSED_BY_THE_DATABASE = "Refused by the database."
"""The prefix of a `409`, and deliberately all it says on its own.

It used to list the three causes a refusal *might* have had (an overlap, a missing dimension, a
bad unit). Measured consequence (R-01, reviewer 2026-09-19): an amount outside `NUMERIC(14,4)`
and an `effective_to` of `9999-12-31` — neither of them a conflict with anything — were answered
with that same sentence, so the endpoint asserted a cause the database had never reported. The
reason now comes from the SQLSTATE (`app.data.write_errors.REFUSAL_BY_SQLSTATE`) together with
the constraint name, and a failure with no SQLSTATE in that closed set is not turned into a
`409` at all: it propagates as a `500`, because "something broke" is the true answer and a
plausible false one is worse."""


def _dimension_model(dimension: str) -> type:
    """Resolve the path segment to a model, or `404` — never a silent fallback to the first one."""
    model = DIMENSION_MODELS.get(dimension)
    if model is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown catalogue dimension. Known: {', '.join(sorted(DIMENSION_MODELS))}.",
        )
    return model


@router.get(
    "/dimensions/{dimension}",
    response_model=DimensionEntryList,
    summary="List the entries of one dimension dictionary",
)
def list_dimension(
    dimension: DimensionSegment,
    caller: Annotated[CallerIdentity, Depends(require_permission(Permission.CATALOG_READ))],
    session: Annotated[Session, Depends(get_session)],
) -> DimensionEntryList:
    """Every entry of one dictionary — the same rows for every caller who may read the catalogue.

    `caller` is injected although nothing below reads it: the parameter *is* the permission
    check (`require_permission` runs as its dependency). Removing it would remove the guard, not
    tidy up an unused argument."""
    entries = list_dimension_entries(session, _dimension_model(dimension))
    return DimensionEntryList(
        entries=[DimensionEntry(id=entry.id, name=entry.name) for entry in entries]
    )


@router.post(
    "/dimensions/{dimension}",
    response_model=DimensionEntry,
    status_code=status.HTTP_201_CREATED,
    summary="Add an entry to one dimension dictionary",
)
def create_dimension(
    dimension: DimensionSegment,
    payload: DimensionEntryCreateRequest,
    caller: Annotated[CallerIdentity, Depends(require_permission(Permission.CATALOG_WRITE))],
    session: Annotated[Session, Depends(get_session)],
) -> DimensionEntry:
    """Add one role / seniority / location / engagement type.

    `CATALOG_WRITE`, not `CATALOG_READ`: NF-10 puts maintaining the catalogue with an organisation
    administrator and reading it with everyone who plans staffing, so a reader reaching this
    endpoint would collapse the distinction the two permissions exist to make.

    Adding a dimension is an `INSERT`, not a migration — that is what NF-10 ("data, not code") means
    here, and it is why no enum in this codebase lists the possible values.
    """
    try:
        entry = create_dimension_entry(session, _dimension_model(dimension), name=payload.name)
    except CatalogWriteRefused as refusal:
        # A duplicate name is the realistic case (`uq_<table>_name_normalized`). `409`, not `500`:
        # the request was understood and refused by state. The message names the mechanism and
        # quotes no row values — the exception carries SQLSTATE and the constraint name only
        # (NF-11). `CatalogWriteRefused`, not `CatalogWriteFailed`: a write that broke for a reason
        # no SQLSTATE classified (R-01) must reach the client as a `500`, not as a conflict with a
        # row that may not exist.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"{_REFUSED_BY_THE_DATABASE} {refusal}",
        ) from None
    return DimensionEntry(id=entry.id, name=entry.name)


@router.get(
    "/rates",
    response_model=CatalogRateList,
    summary="List default rates, optionally only those effective on a given day",
)
def list_catalog_rates(
    caller: Annotated[CallerIdentity, Depends(require_permission(Permission.CATALOG_READ))],
    session: Annotated[Session, Depends(get_session)],
    on_date: Annotated[
        date | None,
        Query(description="Only rates whose effective window covers this calendar date."),
    ] = None,
) -> CatalogRateList:
    """Rate rows, shaped through the cost gate row by row.

    `on_date` is an optional *filter* on a list, resolved by the same `valid_period @> :date`
    predicate as the single-rate lookup (`app.data.catalog.covering`) — one definition of "covers
    this day", not one per endpoint.

    It is a query parameter with no default value of "today": deriving the default from the
    system clock would make the same request answer differently tomorrow, and the boundary of
    "today" belongs to a team's working calendar and an injected time source, not to this
    process."""
    return shape_catalog_rate_list(list_rates(session, on_date=on_date), caller)


@router.get(
    "/rates/effective",
    response_model=CatalogRate,
    summary="The rate effective for one dimension tuple on one day",
    responses={404: {"description": "No rate window covers that date for that tuple."}},
)
def read_effective_rate(
    caller: Annotated[CallerIdentity, Depends(require_permission(Permission.CATALOG_READ))],
    session: Annotated[Session, Depends(get_session)],
    role_id: uuid.UUID,
    seniority_id: uuid.UUID,
    location_id: uuid.UUID,
    engagement_type_id: uuid.UUID,
    on_date: date,
) -> CatalogRate:
    """The one rate that applies — resolved by effective window, never by "latest row wins".

    All four dimension ids are required, because the full tuple is the key (gate-1 decision 3):
    optional ones would need a rule for "any", which is a second resolution mechanism on top of the
    date window, and rule 13 of the Invariant Guardian exists to keep the resolution single.

    A `404` here means "no window covers that date for that tuple" — a statement about the
    catalogue, not about the caller. It is emphatically *not* how a denied cost rate is answered:
    that is a `200` with the field removed (ADR-0005, addendum 2026-09-19, point 5), which is why
    this endpoint can afford a `404` at all without the two becoming indistinguishable.

    The consumer this exists for is plan blocks 4-5 (a calculation reading a rate). AC-04 — an
    approved calculation keeps the rate it was approved with — is *not* proven by this endpoint and
    cannot be: nothing reads a rate into a calculation yet, so there is nothing to snapshot.
    """
    rate = resolve_rate(
        session,
        role_id=role_id,
        seniority_id=seniority_id,
        location_id=location_id,
        engagement_type_id=engagement_type_id,
        on_date=on_date,
    )
    if rate is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No rate is effective for this dimension tuple on that date.",
        )
    return shape_catalog_rate(rate, caller)


@router.post(
    "/rates",
    response_model=CatalogRate,
    status_code=status.HTTP_201_CREATED,
    summary="Add a default rate for one dimension tuple over one effective window",
    responses={
        409: {
            "description": "Refused by the state of the data: the window overlaps an existing one "
            "for the same dimension tuple, a referenced dimension entry does not exist, or a check "
            "constraint rejected a value. The SQLSTATE and the constraint name say which."
        }
    },
)
def create_catalog_rate(
    payload: CatalogRateCreateRequest,
    caller: Annotated[CallerIdentity, Depends(require_permission(Permission.CATALOG_WRITE))],
    session: Annotated[Session, Depends(get_session)],
) -> CatalogRate:
    """Add one rate window — `409` if the state of the data refuses it, `500` if the write broke.

    The overlap refusal is not written here and not written in the data layer: it is the `EXCLUDE`
    constraint, evaluated inside the `INSERT` (ADR-0008). What this function does with it is map the
    refusal to a status code — and to a message that names the constraint without quoting the row,
    because the failing row carries `default_cost_rate` (NF-11).

    **Only `CatalogWriteRefused` becomes a `409`** (R-01). The data layer classifies by SQLSTATE,
    and a failure outside that closed set — `22003` for an amount wider than `NUMERIC(14,4)`,
    `22008` where `effective_to + 1 day` leaves the range of a date, a timeout, a dropped
    connection — propagates and is served as a `500`. Most of those never get this far any more:
    the request schema bounds both amounts to the column's precision and both dates to a planning
    horizon, so they are `422`s with a message about the field rather than errors about the
    database.

    The response goes through the same gate as every read, so a caller with `CATALOG_WRITE` but
    without `PERSONNEL_COSTS_READ` writes a cost rate and does not read it back. That is deliberate:
    a write path answering with the field would be a way around the gate, exactly the shape of leak
    ADR-0005's addendum names for the project write actions.
    """
    try:
        rate = create_rate(
            session,
            role_id=payload.role_id,
            seniority_id=payload.seniority_id,
            location_id=payload.location_id,
            engagement_type_id=payload.engagement_type_id,
            default_cost_rate=payload.default_cost_rate,
            default_selling_rate=payload.default_selling_rate,
            currency=payload.currency,
            unit=payload.unit,
            effective_from=payload.effective_from,
            effective_to=payload.effective_to,
        )
    except CatalogWriteRefused as refusal:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"{_REFUSED_BY_THE_DATABASE} {refusal}",
        ) from None
    return shape_catalog_rate(rate, caller)
