"""Catalogue endpoints — role dimensions, vendors and default rates (F-03, SC-2-01, SC-2-03).

- `GET   /catalog/working-calendars` — the calendars with their days (`CATALOG_READ`, SC-3-02)
- `GET   /catalog/absence-types` — the absence types with their three flags (`CATALOG_READ`)
- `GET   /catalog/absence-budgets` — the leave budgets with the regime they settle against
(`CATALOG_READ`, SC-3-03)
- `POST  /catalog/absence-budgets` — add a budget window (`CATALOG_WRITE`, SC-3-03)
- `GET   /catalog/dimensions/{dimension}` — one dictionary's entries (`CATALOG_READ`)
- `POST  /catalog/dimensions/{dimension}` — add an entry (`CATALOG_WRITE`)
- `PATCH /catalog/dimensions/{dimension}/{entry_id}` — rename an entry (`CATALOG_WRITE`)
- `GET   /catalog/rates` — a page of rates (default 2000, max 5000), optionally only those
effective on a given day (`CATALOG_READ`)
- `GET   /catalog/rates/effective` — the one rate for one dimension tuple on one day
(`CATALOG_READ`)
- `POST  /catalog/rates` — add a rate window (`CATALOG_WRITE`)
- `PATCH /catalog/rates/{rate_id}` — edit a rate window's amounts, currency or dates
(`CATALOG_WRITE`)

The two SC-3-02 endpoints are **read-only, and that is a named limit of this task**: ADR-0007's
addendum of 2026-09-22 (point 3) says in so many words that SC-3-02 gives these two dictionaries no
form. Both tables carry `updated_at` from the migration that creates them, so the edit path of
SC-2-04 can be extended to them without a schema change — until then, a calendar, its days and an
absence type are created by a migration, a seed script or an import, and `catalog_locations
.calendar_id` has no endpoint that sets it at all.

The two `PATCH` endpoints are SC-2-04. Both carry ADR-0007's concurrency marker: every read of a
catalogue row returns `updated_at`, every edit must send it back, and the comparison happens inside
the `UPDATE` statement in `app.data.catalog` — never in Python against a row read a moment earlier.
Both dictionaries and rates use one mechanism, one permission and one pair of refusals.

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
from app.api.response_shaping import (
    shape_absence_budget,
    shape_absence_budget_list,
    shape_absence_type_list,
    shape_catalog_rate,
    shape_catalog_rate_list,
    shape_dimension_entry,
    shape_dimension_entry_list,
    shape_working_calendar_list,
)
from app.api.schemas.catalog import (
    AbsenceBudgetCreateRequest,
    AbsenceBudgetEntry,
    AbsenceBudgetList,
    AbsenceTypeList,
    CatalogRate,
    CatalogRateCreateRequest,
    CatalogRateEditRequest,
    CatalogRateList,
    DimensionEntry,
    DimensionEntryCreateRequest,
    DimensionEntryEditRequest,
    DimensionEntryList,
    WorkingCalendarList,
)
from app.api.schemas.exchange_rate import ExchangeRateCreate, ExchangeRateRead
from app.core.identity import CallerIdentity, Permission
from app.data.absence_budget import (
    AbsenceBudgetWriteRefused,
    create_budget,
    list_budgets,
    statutory_leave_type,
)
from app.data.catalog import (
    DEFAULT_RATE_LIST_LIMIT,
    DIMENSION_MODELS,
    MAX_RATE_LIST_LIMIT,
    MAX_RATE_LIST_OFFSET,
    CatalogWriteRefused,
    create_dimension_entry,
    create_rate,
    list_dimension_entries,
    list_rates,
    resolve_rate,
    update_dimension_entry,
    update_rate,
)
from app.data.exchange_rates import (
    ExchangeRateWriteRefused,
    create_exchange_rate,
    list_exchange_rates,
)
from app.data.working_calendar import list_absence_types, list_calendars
from app.db.session import get_session

router = APIRouter(prefix="/catalog", tags=["catalog"])

DimensionSegment = Annotated[
    str,
    Path(
        description="Which dimension dictionary: " + ", ".join(sorted(DIMENSION_MODELS)),
    ),
]
"""The dictionary is a path segment, not a body field, and the five dictionaries share one pair of
endpoints.

Why one pair rather than ten endpoints: the permission dependency is then declared once per verb.
Ten endpoints are ten chances for the fifth one to be added without a guard, and nothing in
FastAPI would notice — which is exactly what SC-2-03 tested when it added `vendors` and wrote no
endpoint at all. The mapping from segment to table is `app.data.catalog.DIMENSION_MODELS`.

A `str` validated against that mapping rather than a Python `Enum` of the five segments: an `Enum`
would render an unknown segment as a `422` naming the five valid ones, which reads like a schema
statement about the catalogue's *contents*. NF-10 keeps the contents data; the *kinds* are five
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
    return shape_dimension_entry_list(list_dimension_entries(session, _dimension_model(dimension)))


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
    """Add one role / seniority / location / engagement type / vendor.

    Vendors go through this endpoint and no other (SC-2-03, criterion K-05): one pair of endpoints,
    one pair of permissions, one refusal of duplicate names. A vendor-specific endpoint would be a
    fifth mechanism where a fifth dictionary was decided, and `CATALOG_READ`/`CATALOG_WRITE`
    covering subcontractors is an explicit business decision (ADR-0005, addendum 2026-09-21,
    point 2), not an accident of reusing a route.

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
    return shape_dimension_entry(entry)


_ENTRY_NOT_FOUND_DETAIL = "No such entry in this catalogue dimension."
"""The `404` of the edit path — a statement about the catalogue, not about the caller.

Spelled once and deliberately identical for "never existed" and "no longer exists": a catalogue row
has no scope, so unlike the project endpoints there is nothing here to keep indistinguishable, and
this is only about not inventing two messages for one fact. It takes precedence over the `409`
(ADR-0007, "Konsekwencje"; SC-3-01 R-01) — reporting a conflict on a row that is not there would
tell the caller the row exists."""


@router.patch(
    "/dimensions/{dimension}/{entry_id}",
    response_model=DimensionEntry,
    summary="Rename an entry of one dimension dictionary",
    responses={
        404: {"description": _ENTRY_NOT_FOUND_DETAIL},
        409: {
            "description": "Refused by the state of the data: the entry changed since it was read "
            "(concurrency marker), or another entry already carries that name. The message names "
            "which — `condition=updated_at_marker` versus a SQLSTATE and a constraint name."
        },
    },
)
def edit_dimension(
    dimension: DimensionSegment,
    entry_id: uuid.UUID,
    payload: DimensionEntryEditRequest,
    caller: Annotated[CallerIdentity, Depends(require_permission(Permission.CATALOG_WRITE))],
    session: Annotated[Session, Depends(get_session)],
) -> DimensionEntry:
    """Rename one role / seniority / location / engagement type / vendor.

    The fifth endpoint of the table-driven pair, not five endpoints (SC-2-03, K-05): the dictionary
    is still a path segment resolved through `DIMENSION_MODELS`, so the five dictionaries share this
    edit path, this permission dependency and this pair of refusals — a sixth dictionary inherits
    them by being added to that mapping and by nothing else.

    `CATALOG_WRITE`, the same permission as adding: NF-10 puts maintaining the catalogue with an
    organisation administrator, and editing is maintaining. A separate `CATALOG_EDIT` would be a
    distinction nobody has decided (and nothing grants today).

    Two refusals share the `409`, and they are two different answers (Issue #49):

    - the marker moved — somebody committed an edit of this row after the caller read it. Re-read
      and edit again, and it may well succeed.
    - the name collides with another entry (`uq_<table>_name_normalized`, SQLSTATE `23505`). No
      amount of retrying helps; the name has to change.

    Neither message quotes a row value (NF-11), and neither is written here: both come from
    `app.data.catalog`, which builds them from the shared mechanism in `app.data.write_errors`.
    """
    try:
        entry = update_dimension_entry(
            session,
            _dimension_model(dimension),
            entry_id,
            expected_updated_at=payload.updated_at,
            name=payload.name,
        )
    except CatalogWriteRefused as refusal:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"{_REFUSED_BY_THE_DATABASE} {refusal}",
        ) from None
    if entry is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_ENTRY_NOT_FOUND_DETAIL)
    return shape_dimension_entry(entry)


@router.get(
    "/working-calendars",
    response_model=WorkingCalendarList,
    summary="List the working calendars with their standard day, week pattern and exceptional days",
)
def list_working_calendars(
    caller: Annotated[CallerIdentity, Depends(require_permission(Permission.CATALOG_READ))],
    session: Annotated[Session, Depends(get_session)],
) -> WorkingCalendarList:
    """Every working calendar — the same rows for every caller who may read the catalogue (F-05).

    **`CATALOG_READ`, and no new permission** (ADR-0005, addendum 2026-09-22, point 3). A calendar
    is the sixth dictionary of the catalogue, not a sixth mechanism: it belongs to no project, so
    there is no `project_access` filter here and a caller holding this permission with zero project
    access sees exactly these rows (criterion K-09). The `Permission` enum is unchanged by SC-3-02
    and a canary asserts its size.

    **A route of its own rather than `/catalog/dimensions/working-calendars`**, and that is the
    condition SC-2-03 attached to the shared route when it added vendors: the first attribute beyond
    `name` takes a dictionary out of it. A calendar has three, plus a child table — squeezed through
    `DimensionEntry` it would answer with an id and a name, and a client could not compute a single
    capacity from that.

    `caller` is injected although nothing below reads it: the parameter *is* the permission check.
    Removing it would remove the guard, not tidy up an unused argument.
    """
    return shape_working_calendar_list(list_calendars(session))


@router.get(
    "/absence-types",
    response_model=AbsenceTypeList,
    summary="List the absence types with their cost and revenue flags",
)
def list_catalog_absence_types(
    caller: Annotated[CallerIdentity, Depends(require_permission(Permission.CATALOG_READ))],
    session: Annotated[Session, Depends(get_session)],
) -> AbsenceTypeList:
    """Every absence type, with both flags (F-05) — the seventh dictionary, same permission.

    **Neither flag is a cost, so no gate applies** (ADR-0005, addendum 2026-09-22, point 7):
    "absence of this kind still costs the organisation money" is configuration, not an amount, and
    the SC-1-08 conjunction is about amounts. The first response that carries *the cost of an
    absence* (F-07) reinstates that conjunction and has to prove it with a criterion of its own —
    nothing here may be read as that gate already being in place.

    **Nothing in this repository reads these flags into a calculation yet.** They round-trip, and
    that is the whole of criterion K-11.
    """
    return shape_absence_type_list(list_absence_types(session))


@router.get(
    "/absence-budgets",
    response_model=AbsenceBudgetList,
    summary="List the leave budgets with the regime of the absence type they settle against",
)
def list_catalog_absence_budgets(
    caller: Annotated[CallerIdentity, Depends(require_permission(Permission.CATALOG_READ))],
    session: Annotated[Session, Depends(get_session)],
) -> AbsenceBudgetList:
    """Every leave budget — the same rows for every caller who may read the catalogue (F-05).

    **`CATALOG_READ`, and no new permission** (ADR-0005, addendum 2026-09-22 SC-3-03, point 2). The
    eighth table of the catalogue, not an eighth mechanism: it belongs to no project, so there is no
    `project_access` filter here and a caller holding this permission with zero project access sees
    exactly these rows. The `Permission` enum is unchanged by SC-3-03 and a canary asserts its
    membership by set equality (criterion K-09).

    **Not gated by `PERSONNEL_COSTS_READ`, and that is a decision rather than an omission** (same
    addendum, point 3, gate-1 decision Q-4). A budget is a number of *days* — an entitlement
    parameter of the organisation, not an amount and not any person's cost. NF-11/AC-06 protect
    individual personnel costs, and the multiplier that would turn these days into one, a cost rate,
    is gated separately and is not in this payload. Two things follow, both stated so neither is
    discovered later: a caller holding `CATALOG_READ` and not `PERSONNEL_COSTS_READ` receives the
    figure **whole**, not whitened; and the first response carrying the *cost* of a budget (F-07)
    reinstates the SC-1-08 conjunction and has to prove it with a criterion of its own (point 4).

    The regime — whether a day of this budget costs money and whether it is billable — is read from
    the single absence type flagged `is_statutory_leave`, once per response. With no flagged type
    the answer is the named state `"no_statutory_leave_type"` and `"n/a"`, never a guess and never a
    `500` (ADR-0008, addendum 2026-09-22 SC-3-03, point 8b).

    `caller` is injected although nothing below reads it: the parameter *is* the permission check.
    """
    return shape_absence_budget_list(list_budgets(session), statutory_leave_type(session))


@router.post(
    "/absence-budgets",
    response_model=AbsenceBudgetEntry,
    status_code=status.HTTP_201_CREATED,
    summary="Add a leave budget for one calendar and engagement type over one window",
    responses={
        409: {
            "description": "Refused by the state of the data: the window overlaps an existing one "
            "for the same (calendar, engagement type) pair, a referenced row does not exist, or a "
            "check constraint rejected a value. The SQLSTATE and the constraint name say which."
        }
    },
)
def create_absence_budget(
    payload: AbsenceBudgetCreateRequest,
    caller: Annotated[CallerIdentity, Depends(require_permission(Permission.CATALOG_WRITE))],
    session: Annotated[Session, Depends(get_session)],
) -> AbsenceBudgetEntry:
    """Add one budget window — `409` if the state of the data refuses it, `500` if the write broke.

    `CATALOG_WRITE`, the same permission as every other catalogue write and no new one (ADR-0005,
    addendum 2026-09-22 SC-3-03, point 2): NF-10 puts maintaining the catalogue with an organisation
    administrator, and a leave regulation is exactly that kind of maintenance.

    The overlap refusal is not written here and not in the data layer: it is the `EXCLUDE`
    constraint, evaluated inside the `INSERT` (ADR-0008). What this function does with it is map the
    refusal to a status code and to a message that names the constraint without quoting the row.

    **The source of the number is mandatory, and an author is not a substitute** (criterion K-02).
    A body with no `source` is a `422`; a body naming `author`, `entered_by` or `approved_by` is a
    `422` as well, because `extra="forbid"` refuses fields this API has no column for — and there is
    no column for one deliberately (ADR-0005, addendum 2026-09-21 SC-2-04, point 6). The database
    refuses a blank source a second time, for every path that never sees the request schema.

    **The personal-data precondition this endpoint activates, and how it was resolved.** ADR-0008's
    addendum (SC-3-03, point 6) made the first write path for this free-text field conditional on
    resolving, *explicitly*, either a content restriction or an erasure/rectification rule for the
    `approved_snapshot_*` rows that copy the text. ADR-0005's addendum (SC-3-03, point 6 — a human's
    decision of 2026-09-22) resolves it as a **named risk with no technical enforcement**: the same
    precedent as `absence_type.name` and `catalog_vendors.name`, with no name detection (unreliable,
    and a false sense of safety is worse than none) and no erasure mechanism designed for one column
    of one table.

    What this endpoint does is therefore exactly what that decision allows and nothing more: the
    field is mandatory, non-blank and bounded at 500 characters, and a body naming a *person* rather
    than a *source* is refused with a `422` because there is no column for one and there must not be
    (ADR-0005, addendum 2026-09-21 SC-2-04, point 6). The reopening condition belongs to the erasure
    mechanism rather than to this endpoint: the first task that designs one for
    `approved_snapshot_*` — for any reason, for any column — must cover `absence_type.name` and
    `absence_budget.source` together, not one at a time as tasks happen to touch them.

    **Only `AbsenceBudgetWriteRefused` becomes a `409`** (R-01). The data layer classifies by
    SQLSTATE, and a failure outside that closed set — a value wider than `NUMERIC(6,2)`, a date
    arithmetic overflow, a timeout, a dropped connection — propagates and is served as a `500`. Most
    of those never get this far: the request schema bounds the figure to the column's precision and
    both dates to a planning horizon, so they are `422`s about a field rather than errors about the
    database.
    """
    try:
        budget = create_budget(
            session,
            calendar_id=payload.calendar_id,
            engagement_type_id=payload.engagement_type_id,
            budget_days=payload.budget_days,
            unit=payload.unit,
            source=payload.source,
            effective_from=payload.effective_from,
            effective_to=payload.effective_to,
        )
    except AbsenceBudgetWriteRefused as refusal:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"{_REFUSED_BY_THE_DATABASE} {refusal}",
        ) from None
    return shape_absence_budget(budget, statutory_leave_type(session))


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
    limit: Annotated[
        int,
        Query(
            ge=1,
            le=MAX_RATE_LIST_LIMIT,
            description="Maximum rows to return. Refused above the maximum rather than clamped.",
        ),
    ] = DEFAULT_RATE_LIST_LIMIT,
    offset: Annotated[
        int,
        Query(
            ge=0,
            le=MAX_RATE_LIST_OFFSET,
            description="Rows to skip before the page, for stable paging with limit. Refused "
            "above the maximum rather than clamped.",
        ),
    ] = 0,
) -> CatalogRateList:
    """One page of rate rows, shaped through the cost gate row by row, with the total row count.

    `on_date` is an optional *filter* on a list, resolved by the same `valid_period @> :date`
    predicate as the single-rate lookup (`app.data.catalog.covering`) — one definition of "covers
    this day", not one per endpoint.

    It is a query parameter with no default value of "today": deriving the default from the
    system clock would make the same request answer differently tomorrow, and the boundary of
    "today" belongs to a team's working calendar and an injected time source, not to this
    process.

    **K-11.** `limit`/`offset` bound the page — default 2000, refused above 5000 rather than
    silently clamped (a clamp would answer fewer rows than asked for without saying so). The
    default with no query parameters at all is never "the whole catalogue": that was the shape
    measured at 125MB/9.4s against a 12s client budget (reviewer, R-02), and it is now unreachable
    at any catalogue size, not merely improved for today's one. `total` in the response is the
    count matching `on_date` before the page is applied, so a client can tell a full answer from
    page one of more.

    **`offset` is bounded at both ends, for the same two reasons** (R-02, gate-2 review
    2026-09-21). It used to have a floor and no ceiling, so a value past PostgreSQL's `bigint`
    reached the driver and came back as an unhandled `500` — this application installs no exception
    handler, deliberately (`app.data.catalog.CatalogWriteFailed`), so nothing downstream was going
    to turn that into an answer about the request. And a `LIMIT` alone does not bound the server:
    `OFFSET n` walks and discards `n` rows first, so the page is bounded by `limit + offset` and
    both halves need a ceiling (`app.data.catalog.MAX_RATE_LIST_OFFSET`). Refused with a `422`
    naming the field, never clamped.

    Rows come back newest window first (`app.data.catalog.list_rates`, R-02, reviewer 2026-09-21):
    the frontend has no control to move past page one, so the default, no-parameters call is the
    only page most callers ever see, and it must be the one carrying today's rates rather than the
    catalogue's oldest, expired window."""
    rates, total = list_rates(session, on_date=on_date, limit=limit, offset=offset)
    return shape_catalog_rate_list(rates, caller, total=total)


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
    vendor_id: Annotated[
        uuid.UUID | None,
        Query(
            description="Whose rate to resolve. Omit it for the organisation's own (internal) "
            "rate — omitting it never means 'any vendor'."
        ),
    ] = None,
) -> CatalogRate:
    """The one rate that applies — resolved by effective window, never by "latest row wins".

    All four dimension ids are required, because the full tuple of business dimensions is the key
    to *what* is priced (gate-1 decision 3): optional ones would need a rule for "any", which is a
    second resolution mechanism on top of the date window, and rule 13 of the Invariant Guardian
    exists to keep the resolution single. **This tuple alone is not, since SC-2-03, the full key of
    a rate's `EXCLUDE` constraint** — `vendor_id` below is the fifth element
    (`app.models.catalog.RATE_EXCLUDE_KEY`), answering *whose* price it is rather than *what* is
    priced, which is why it is a separate parameter with its own required decision in
    `resolve_rate` rather than a fifth dimension id here.

    **`vendor_id` is optional and its absence is an answer, not a wildcard** (SC-2-03, K-03/K-04).
    Omitted, it resolves the internal rate; a tuple priced only by a subcontractor therefore answers
    `404` to a request that names no vendor, rather than quietly returning the subcontractor's
    price. Read as "any" it would be the second resolution mechanism this endpoint refuses to have —
    and the failure would not even be subtle: an internal rate and a vendor rate covering one day
    are two rows, so `one_or_none()` would raise on perfectly valid data.

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
        vendor_id=vendor_id,
        on_date=on_date,
    )
    if rate is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No rate is effective for this dimension tuple on that date.",
        )
    return shape_catalog_rate(rate, caller)


@router.get("/exchange-rates", response_model=list[ExchangeRateRead])
def read_exchange_rates(
    caller: Annotated[CallerIdentity, Depends(require_permission(Permission.CATALOG_READ))],
    session: Annotated[Session, Depends(get_session)],
    project_id: Annotated[uuid.UUID | None, Query()] = None,
    scenario_id: Annotated[uuid.UUID | None, Query()] = None,
) -> list[ExchangeRateRead]:
    """Read organization defaults or rates visible inside the requested project scope."""
    rates = list_exchange_rates(session, caller, project_id=project_id, scenario_id=scenario_id)
    if rates is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scenario not found.")
    return [ExchangeRateRead.model_validate(rate) for rate in rates]


@router.post("/exchange-rates", response_model=ExchangeRateRead, status_code=201)
def add_exchange_rate(
    payload: ExchangeRateCreate,
    caller: Annotated[CallerIdentity, Depends(require_permission(Permission.CATALOG_WRITE))],
    session: Annotated[Session, Depends(get_session)],
) -> ExchangeRateRead:
    """Add an organization default or scoped project/scenario override."""
    if payload.source_currency == payload.target_currency:
        raise HTTPException(status_code=422, detail="Currency pair must contain two currencies.")
    try:
        rate = create_exchange_rate(
            session,
            caller,
            source_currency=payload.source_currency,
            target_currency=payload.target_currency,
            effective_from=payload.effective_from,
            effective_to=payload.effective_to,
            rate=payload.rate,
            source=payload.source,
            project_id=payload.project_id,
            scenario_id=payload.scenario_id,
        )
    except ExchangeRateWriteRefused as refusal:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Refused by the database. {refusal}",
        ) from None
    if rate is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Project or scenario not found."
        )
    return ExchangeRateRead.model_validate(rate)


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
            vendor_id=payload.vendor_id,
            default_cost_rate=payload.default_cost_rate,
            default_selling_rate=payload.default_selling_rate,
            currency=payload.currency,
            unit=payload.unit,
            cost_rate_unit=payload.cost_rate_unit,
            effective_from=payload.effective_from,
            effective_to=payload.effective_to,
            surcharge_percent=payload.surcharge_percent,
            includes_surcharge=payload.includes_surcharge,
        )
    except CatalogWriteRefused as refusal:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"{_REFUSED_BY_THE_DATABASE} {refusal}",
        ) from None
    return shape_catalog_rate(rate, caller)


_RATE_NOT_FOUND_DETAIL = "No such rate in the catalogue."
"""The `404` of the rate edit path. Same reading as `_ENTRY_NOT_FOUND_DETAIL`, and the same
precedence over the `409`."""


@router.patch(
    "/rates/{rate_id}",
    response_model=CatalogRate,
    summary="Edit a default rate's amounts, currency or effective window",
    responses={
        404: {"description": _RATE_NOT_FOUND_DETAIL},
        409: {
            "description": "Refused by the state of the data: the rate changed since it was read "
            "(concurrency marker), or the edited window now overlaps another one for the same "
            "dimension tuple, or a check constraint rejected a value. The message names which — "
            "`condition=updated_at_marker` versus a SQLSTATE and a constraint name."
        },
    },
)
def edit_catalog_rate(
    rate_id: uuid.UUID,
    payload: CatalogRateEditRequest,
    caller: Annotated[CallerIdentity, Depends(require_permission(Permission.CATALOG_WRITE))],
    session: Annotated[Session, Depends(get_session)],
) -> CatalogRate:
    """Correct one rate window — the amounts, the currency, the dates. Never the tuple it prices.

    **`PATCH`, and partial in the strict sense** (Issue #49, gate-1 decision Q-2): the body names
    the fields that change, and an omitted field is not an edit of that field. The case that makes
    this a requirement rather than a preference is `default_cost_rate`. A caller holding
    `CATALOG_WRITE` without `PERSONNEL_COSTS_READ` never receives it on any read — the gate below
    removes it from this very response — so their correction of a selling rate or an end date has to
    be expressible without it, and it is: they omit the field and the stored cost rate is untouched.
    A `PUT` would leave them inventing a number or unable to edit the row at all.

    **What cannot be edited is as deliberate as what can**
    (`app.data.catalog.EDITABLE_RATE_FIELDS`): the four dimension ids and `vendor_id` say *which*
    rate this is, and moving an existing window onto another tuple or another vendor is a re-keying
    nobody has decided. `extra="forbid"` on the request makes an attempt a `422` rather than a
    silent no-op.

    **The response goes through the same gate as every read** (`shape_catalog_rate`), which is the
    point of the shaping layer having one entry per row shape: a write path answering with the cost
    rate would be a way around the gate — the leak ADR-0005's addendum names for the project write
    actions — and it would be a particularly quiet one here, because the caller just sent the value
    back in the request and would recognise it in the answer.

    `404` before `409`, decided in `update_rate` and not here: the row's existence is established
    before its marker is compared, so a conflict is never the answer that confirms a rate exists.
    """
    changes = payload.changes()
    # R-02 (SC-5-08; ADR-0005 addendum 2026-09-29, point 7, Q-B): a caller who cannot read the
    # stored
    # unit (no `PERSONNEL_COSTS_READ`, the same single factor the catalogue gate uses) may write the
    # amount only against the unit they *state* — it becomes a precondition of the `UPDATE`, so a
    # wrong guess is a `409` with no write and the caller may change the unit only by being able to
    # read it.
    blind_unit = (
        None
        if caller.has(Permission.PERSONNEL_COSTS_READ) or "cost_rate_unit" not in changes
        else payload.cost_rate_unit
    )
    try:
        rate = update_rate(
            session,
            rate_id,
            expected_updated_at=payload.updated_at,
            changes=changes,
            stored_cost_rate_unit_must_be=blind_unit,
        )
    except CatalogWriteRefused as refusal:
        # One `except`, two distinguishable bodies: `CatalogConcurrentEditConflict` (the marker
        # moved, `condition=updated_at_marker`) is a subclass, so it cannot be the refusal somebody
        # forgot to map — while its message still says which of the two causes fired. As on the
        # create path, only a `CatalogWriteRefused` becomes a `409`: an unclassified
        # `CatalogWriteFailed` propagates and is served as a `500`, because "something broke" is the
        # true answer and a plausible false one is worse (R-01).
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"{_REFUSED_BY_THE_DATABASE} {refusal}",
        ) from None
    if rate is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_RATE_NOT_FOUND_DETAIL)
    return shape_catalog_rate(rate, caller)
