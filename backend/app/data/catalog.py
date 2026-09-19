"""Reading and writing the organisational catalogue (F-03, SC-2-01).

**Why this module is not `catalog_reads.py` + `catalog_writes.py`, and has no guard function.**
ADR-0001's addendum of 2026-09-19 decides it explicitly: `project_reads.accessible_projects` exists
so that a *scope predicate* cannot be forgotten, and the catalogue has no scope predicate to forget
— a catalogue row belongs to no project, no user and no tenant (ADR-0005, addendum 2026-09-19,
point 1). A wrapper named symmetrically to `project_reads` would suggest a filter that is not
there, which is the dangerous direction to be wrong in. So: plain `select()`, in one module, with
no gate.

What this module therefore does **not** do:

- it does not filter by caller — anything under `CATALOG_READ` sees every row (K-01);
- it does not decide cost-rate visibility — that stays in the response-shaping layer
  (`app.api.response_shaping.shape_catalog_rate`), because two places deciding who sees costs is
  exactly what SC-1-08 consolidated (ADR-0001 addendum, point 3);
- it does not guard against overlapping effective windows in Python. The `EXCLUDE` constraint in the
  database is the guard (ADR-0008); a check-then-act pre-check here would be the mutation that has
  already survived delivered tests twice in this repository (SC-1-02, SC-1-04).

The expiry condition of the exception is structural, not a judgement call: the first per-caller
predicate on any of these tables (a catalogue per business unit, multi-tenancy, admin-only rows)
brings back the risk `project_reads` defends against, and then needs a guard function of its own
plus its own dated entry in ADR-0001."""

import uuid
from collections.abc import Sequence
from datetime import date
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.data.write_errors import WriteFailed, WriteRefused, failure_for
from app.models.catalog import (
    CatalogDefaultRate,
    CatalogEngagementType,
    CatalogLocation,
    CatalogRole,
    CatalogSeniority,
)

DimensionModel = type[CatalogRole | CatalogSeniority | CatalogLocation | CatalogEngagementType]

DIMENSION_MODELS: dict[str, DimensionModel] = {
    "roles": CatalogRole,
    "seniorities": CatalogSeniority,
    "locations": CatalogLocation,
    "engagement-types": CatalogEngagementType,
}
"""The four dictionaries, keyed by the path segment that addresses them.

As data rather than as four pairs of endpoints: the permission dependency is then declared once per
verb instead of eight times, so a dictionary cannot be the one that was added without a guard. The
four *kinds* being code here is not a breach of NF-10 — they are four columns of the rate table. The
*values* (which roles exist) are rows, and nothing in this codebase enumerates them.
"""


class CatalogWriteFailed(WriteFailed):
    """A write to the catalogue failed for a reason nothing here established (NF-11).

    Required by ADR-0008 ("Konsekwencje") specifically for this table: a violated `EXCLUDE` is a
    database error whose default message carries the failing row — which here includes
    `default_cost_rate`, the one field the response layer spends its effort removing. Letting the
    driver's exception escape would leak through the traceback what the gate removes from the body.

    **Not a refusal.** An amount outside `NUMERIC(14,4)`, an `effective_to` whose `+ 1 day` leaves
    the range of a date, a statement timeout or a dropped connection all arrive here, and an API
    layer must let this become a `500`. The refusals are `CatalogWriteRefused` below (R-01, reviewer
    2026-09-19: every one of these used to be answered with a `409` naming an overlap that had not
    happened).
    """


class CatalogWriteRefused(CatalogWriteFailed, WriteRefused):
    """The database refused a catalogue write for a reason its SQLSTATE names — a `409`.

    Subclass of both, so `except CatalogWriteFailed` still catches every catalogue write failure
    while `except CatalogWriteRefused` catches only the ones a caller can act on, and a future
    handler written against the shared `WriteRefused` sees it too.
    """


def _failure(error: SQLAlchemyError, *, subject: str) -> WriteFailed:
    """Classify one failed catalogue write by SQLSTATE, into this module's pair of exceptions.

    Spelled once so both write functions classify identically — a per-call-site `except` block is
    how one of them ends up answering `409` for a defect while the other does not. The returned
    object is always a `CatalogWriteFailed`, and a `CatalogWriteRefused` exactly when the server
    reported one of the SQLSTATEs in `app.data.write_errors.REFUSAL_BY_SQLSTATE`.
    """
    return failure_for(
        error, subject=subject, refused=CatalogWriteRefused, failed=CatalogWriteFailed
    )


def list_dimension_entries(
    session: Session, model: DimensionModel
) -> Sequence[CatalogRole | CatalogSeniority | CatalogLocation | CatalogEngagementType]:
    """Every row of one dimension dictionary, ordered by name.

    Every row, for every caller: there is no `WHERE` clause narrowing this by identity, and that is
    the criterion K-01 is about. Ordering is by name (then id) so a list response is stable — not a
    filter, not a page.
    """
    return list(session.execute(sa.select(model).order_by(model.name, model.id)).scalars().all())


def create_dimension_entry(
    session: Session, model: DimensionModel, *, name: str
) -> CatalogRole | CatalogSeniority | CatalogLocation | CatalogEngagementType:
    """Insert one dimension entry and commit it.

    The commit is here rather than in the endpoint, as in
    `app.data.project_writes.create_project`: "the row is persisted" is this layer's claim to
    make, and an endpoint that forgot to commit would otherwise answer `201` with a row that
    never reached the database."""
    entry = model(id=uuid.uuid4(), name=name)
    try:
        session.add(entry)
        session.commit()
    except SQLAlchemyError as error:
        session.rollback()
        # `from None`, as in `project_writes`: a chained exception is printed with its cause, so
        # re-raising *with* the original would put PostgreSQL's `DETAIL: Failing row contains (…)`
        # back into the traceback one line further down.
        raise _failure(error, subject="catalogue entry") from None
    return entry


def covering(on_date: date) -> sa.ColumnElement[bool]:
    """`valid_period @> :on_date` — the one way this codebase asks "which window covers this day".

    The generated column, never a rebuilt `daterange(effective_from, effective_to + 1, '[)')`
    expression (ADR-0008, points 2-3). The constraint and the lookup read the *same* column, so they
    cannot disagree by a day about where a window ends — and a disagreement of exactly one day is
    invisible to any test that asks about a date in the middle of a window.

    Returned as a SQL fragment so both the list filter and the single-row resolution compose on it
    instead of spelling it out twice.
    """
    # `bool_op`, and the value cast to `DATE` explicitly: without the cast the bound parameter
    # takes the column's own type and the comparison becomes `daterange @> daterange`, which is a
    # different (and silently wrong) question — "does this window contain that window".
    return CatalogDefaultRate.valid_period.bool_op("@>")(sa.cast(on_date, sa.Date))


def list_rates(
    session: Session,
    *,
    on_date: date | None = None,
) -> Sequence[CatalogDefaultRate]:
    """Every rate row, or only those whose window covers `on_date`.

    No caller predicate here either (K-01). `on_date` is an *optional filter* on a list; resolving
    one rate for one tuple is `resolve_rate` below, and the two share `covering` so that "which rate
    applies on day D" has one answer whichever entry point asks.
    """
    statement = sa.select(CatalogDefaultRate).order_by(
        CatalogDefaultRate.effective_from, CatalogDefaultRate.id
    )
    if on_date is not None:
        statement = statement.where(covering(on_date))
    return list(session.execute(statement).scalars().all())


def resolve_rate(
    session: Session,
    *,
    role_id: uuid.UUID,
    seniority_id: uuid.UUID,
    location_id: uuid.UUID,
    engagement_type_id: uuid.UUID,
    on_date: date,
) -> CatalogDefaultRate | None:
    """The rate for one full dimension tuple on one calendar day, or `None` if no window covers it.

    Two properties, both of them the point of the criterion (K-04):

    - **It resolves by window, not by recency.** There is no `ORDER BY effective_from DESC LIMIT 1`
      and there must never be one: a date inside an earlier window must yield the earlier rate,
      which is what makes a historical or forward-dated calculation reproducible
      (invariant-guardian rule 13). "Latest row wins" would silently answer today's price for last
      year's question.
    - **It does not have to choose.** `one_or_none()` rather than `first()`: with the `EXCLUDE`
      constraint in place two covering rows cannot exist, so if they somehow do, this raises instead
      of picking one. A `first()` would turn a broken invariant into a plausible answer.

    `on_date` is a parameter, never `date.today()`. The boundary of "today" belongs to the caller's
    working calendar and an injected time source, not to this process's clock — and a lookup that
    defaulted to today would make every calculation's result depend on when it was run.
    """
    statement = sa.select(CatalogDefaultRate).where(
        CatalogDefaultRate.role_id == role_id,
        CatalogDefaultRate.seniority_id == seniority_id,
        CatalogDefaultRate.location_id == location_id,
        CatalogDefaultRate.engagement_type_id == engagement_type_id,
        covering(on_date),
    )
    return session.execute(statement).scalars().one_or_none()


def create_rate(
    session: Session,
    *,
    role_id: uuid.UUID,
    seniority_id: uuid.UUID,
    location_id: uuid.UUID,
    engagement_type_id: uuid.UUID,
    default_cost_rate: Decimal,
    default_selling_rate: Decimal,
    currency: str,
    unit: str,
    effective_from: date,
    effective_to: date | None,
) -> CatalogDefaultRate:
    """Insert one rate row and commit it — or raise `CatalogWriteFailed` if the database refuses.

    **No pre-check for an overlapping window.** The refusal is the `EXCLUDE` constraint, evaluated
    by the database inside the `INSERT`. A `SELECT` here asking "does an overlapping row exist?"
    would be a check-then-act window: a competing connection committing its row between that
    `SELECT` and this `INSERT` produces two overlapping rows, and no single-threaded test can see
    it (ADR-0008, "Rozważane alternatywy"; the same mutation survived delivered tests in SC-1-02
    and SC-1-04).

    `valid_period` is absent from the arguments and from the insert: it is a generated column, and
    assigning it would be the second place the window's boundary is decided (ADR-0008, point 2).

    `unit` is an explicit argument with no default, so a second call site cannot omit it and inherit
    whatever this function happened to prefer; the accepted value is enforced by the database
    (`ck_catalog_default_rates_unit_is_hour`) and, for the API, by the request schema.

    Nothing here rounds `default_cost_rate` or `default_selling_rate`. The column's scale is
    larger than the currency's minor unit on purpose (ADR-0008, point 6) — rounding at write
    time would silently change an input value, and rounding is the consumer's rule
    (`app.core.money.round_money`). An amount carrying *more* than four decimal places is refused at
    the API boundary (`CatalogRateCreateRequest`) rather than quietly truncated here, because
    truncation is the one thing this paragraph promises not to do (R-05, reviewer 2026-09-19).

    A failed write is classified by SQLSTATE, not by assumption (`_failure`): an overlap, a
    duplicate, a missing reference or a broken check constraint raise `CatalogWriteRefused`, and
    everything else — an out-of-range amount or date, a timeout, a lost connection — raises plain
    `CatalogWriteFailed`, which the API must not answer with a `409`."""
    rate = CatalogDefaultRate(
        id=uuid.uuid4(),
        role_id=role_id,
        seniority_id=seniority_id,
        location_id=location_id,
        engagement_type_id=engagement_type_id,
        default_cost_rate=default_cost_rate,
        default_selling_rate=default_selling_rate,
        currency=currency,
        unit=unit,
        effective_from=effective_from,
        effective_to=effective_to,
    )
    try:
        session.add(rate)
        session.commit()
    except SQLAlchemyError as error:
        session.rollback()
        raise _failure(error, subject="catalogue rate") from None
    # Re-read after the commit so the row that is returned is the row the database holds: the
    # column is `NUMERIC(14,4)`, so `Decimal("185")` on the way in is `Decimal("185.0000")` once
    # stored, and the response must not disagree with the next `GET` about which of the two it is.
    # Sessions here run with `expire_on_commit=False` (`app.db.session`), so without this the
    # in-memory object would keep whatever Python value the caller passed.
    session.refresh(rate)
    return rate
