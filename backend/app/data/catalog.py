"""Reading and writing the organisational catalogue (F-03, SC-2-01, SC-2-03).

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
plus its own dated entry in ADR-0001.

**SC-2-03 added a vendor column and did not open that door** (ADR-0001/ADR-0005, addenda
2026-09-21). `vendor_id` names a counterparty, not a subject the caller acts for, and no function
here filters by it on the caller's behalf: the only place it appears in a `WHERE` clause is
`vendor_is`, where it comes from the request as a question, not from the identity as a scope. A
narrowing of "which vendors' price lists this caller may see" would be the first per-caller
predicate on this table and expires both exceptions."""

import uuid
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, aliased

from app.data.write_errors import (
    CONCURRENCY_MARKER_CONDITION,
    CONCURRENCY_MARKER_REASON,
    WriteFailed,
    WriteRefused,
    failure_for,
    refusal_by_condition,
)
from app.models.catalog import (
    CatalogCostCategory,
    CatalogDefaultRate,
    CatalogEngagementType,
    CatalogLocation,
    CatalogRole,
    CatalogSeniority,
    CatalogVendor,
)

DimensionRow = (
    CatalogRole
    | CatalogSeniority
    | CatalogLocation
    | CatalogEngagementType
    | CatalogVendor
    | CatalogCostCategory
)
"""One row of any of the five dictionaries. Named `…Row` rather than `…Entry` so it cannot be
confused with `app.api.schemas.catalog.DimensionEntry`, which is the payload, not the row."""

DimensionModel = type[DimensionRow]

DIMENSION_MODELS: dict[str, DimensionModel] = {
    "roles": CatalogRole,
    "seniorities": CatalogSeniority,
    "locations": CatalogLocation,
    "engagement-types": CatalogEngagementType,
    "vendors": CatalogVendor,
    # SC-5-05 (ADR-0014, point 2; ADR-0005, addendum 2026-09-23 SC-5-05, point 3): the categories of
    # an additional cost join as an entry here and nothing else — another dictionary, not another
    # mechanism, exactly as `"vendors"` joined in SC-2-03.
    "cost-categories": CatalogCostCategory,
}
"""The dictionaries, keyed by the path segment that addresses them.

As data rather than as five pairs of endpoints: the permission dependency is then declared once per
verb instead of ten times, so a dictionary cannot be the one that was added without a guard. The
five *kinds* being code here is not a breach of NF-10 — they are five columns of the rate table. The
*values* (which roles or vendors exist) are rows, and nothing in this codebase enumerates them.

`"vendors"` is an entry in this mapping and nothing else (SC-2-03, gate-1 decision 2): no endpoint
of its own, no permission of its own, no branch anywhere that asks whether a dictionary is the
vendor one. That is the whole content of criterion K-05, and the mutation it names is deleting this
line.
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


class CatalogConcurrentEditConflict(CatalogWriteRefused):
    """The row changed since the caller read it (ADR-0007, NF-05) — a `409`, and a different one.

    A subclass rather than a second, unrelated exception: an endpoint keeps **one** `except
    CatalogWriteRefused` branch, so a refusal cannot be the one somebody forgot to map. What makes
    the two distinguishable is the message, which names `condition=updated_at_marker` where a
    refusal the driver reported names `sqlstate=…, constraint=…` (`app.data.write_errors`).

    The distinction is not cosmetic: an overlap or a duplicate name is refused for as long as the
    other row exists, while a stale marker means "somebody else got there first, re-read and try
    again". Collapsing them into one message tells a caller to retry a write that cannot succeed, or
    not to retry one that would.

    Carries nothing about the competing change (ADR-0007; NF-11).
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


def list_dimension_entries(session: Session, model: DimensionModel) -> Sequence[DimensionRow]:
    """Every row of one dimension dictionary, ordered by name.

    Every row, for every caller: there is no `WHERE` clause narrowing this by identity, and that is
    the criterion K-01 is about. Ordering is by name (then id) so a list response is stable — not a
    filter, not a page.
    """
    return list(session.execute(sa.select(model).order_by(model.name, model.id)).scalars().all())


def create_dimension_entry(
    session: Session, model: DimensionModel, *, name: str
) -> DimensionRow:
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


COST_RATE_UNIT_CONDITION = "cost_rate_unit_precondition"
COST_RATE_UNIT_REASON = (
    "The cost rate unit sent does not match the one stored, and this caller may not change it. "
    "Nothing was written."
)
"""The refusal of a blind writer (no `PERSONNEL_COSTS_READ`) whose `cost_rate_unit` differs from the
stored one (SC-5-08, reviewer R-02; ADR-0005 addendum 2026-09-29, point 7, Q-B). The refusal body
names neither the stored unit nor any rate; the status still tells whether the guess was right (so
the unit is recoverable by probing, see the addendum)."""


def _marker_conflict(*, subject: str) -> CatalogConcurrentEditConflict:
    """The refusal a conditional `UPDATE` that matched no row means — built in one place.

    Shaped by `app.data.write_errors.refusal_by_condition`, i.e. the same mechanism and the same
    message format as the refusals the driver reports, so the two kinds of `409` on this path read
    alike and identify themselves differently (`condition=updated_at_marker` versus
    `sqlstate=…, constraint=…`).
    """
    return refusal_by_condition(
        subject=subject,
        condition=CONCURRENCY_MARKER_CONDITION,
        reason=CONCURRENCY_MARKER_REASON,
        refused=CatalogConcurrentEditConflict,
    )


def _apply_marked_update(
    session: Session,
    table: sa.Table,
    row_id: uuid.UUID,
    *,
    expected_updated_at: datetime,
    changes: Mapping[str, Any],
    subject: str,
    must_equal: Mapping[str, Any] | None = None,
) -> bool:
    """Run one `UPDATE … WHERE id = :id AND updated_at = :expected` and say whether it landed.

    `must_equal` (SC-5-08, R-02) adds `column = value` conditions to the same `WHERE`: a
    precondition
    the database evaluates in the same statement and snapshot as the write, never a Python check on
    a
    row read a moment earlier.

    **The whole mechanism of ADR-0007 is the `WHERE` clause of this one statement.** The marker is
    compared by the database, in the same statement and the same snapshot as the write — never in
    Python against a row read a moment earlier. The Python variant passes every single-threaded test
    (a marker nobody ever issued mismatches either way) and survived a full delivered suite in
    SC-1-02 before a two-connection race test killed it; it is the one shape this function exists to
    make unwritable here (`docs/architecture/capabilities.md`, mutation log 2026-09-19).

    Returns `False` when the statement matched no row, which is the marker having moved; the caller
    turns that into `CatalogConcurrentEditConflict` **after** it has established the row exists, so
    a `404` keeps its precedence over a `409` (ADR-0007, "Konsekwencje").

    `updated_at` is not in `changes` and must never be: the new value comes from the column's
    `onupdate=func.now()`, a SQL expression evaluated by the database, so two application instances
    cannot disagree about which write came last and no process's own clock enters the row.

    The Core `UPDATE` against the `Table` rather than the ORM entity, exactly as
    `app.data.project_writes.update_project` does it: the statement stays what is written here, with
    no ORM-level synchronisation strategy deciding to re-fetch rows or to evaluate the criteria in
    Python — which would be the mechanism this function refuses to have, one layer down.
    """
    statement = (
        sa.update(table)
        .where(
            table.c.id == row_id,
            table.c.updated_at == expected_updated_at,
            *(table.c[column] == value for column, value in (must_equal or {}).items()),
        )
        .values(**dict(changes))
        .returning(table.c.updated_at)
    )
    try:
        applied = session.execute(statement).one_or_none()
        if applied is None:
            # No `session.rollback()`: the single statement above matched no row, so there is
            # nothing written to undo, and a rollback would discard unrelated work the caller's
            # transaction may hold. "Nothing was saved" is a property of the statement not matching.
            return False
        session.commit()
    except SQLAlchemyError as error:
        session.rollback()
        # `from None` at the raise site, for the reason `create_dimension_entry` gives: PostgreSQL's
        # `DETAIL: Failing row contains (…)` carries the whole row, `default_cost_rate` included.
        raise _failure(error, subject=subject) from None
    return True


def update_dimension_entry(
    session: Session,
    model: DimensionModel,
    entry_id: uuid.UUID,
    *,
    expected_updated_at: datetime,
    name: str,
) -> DimensionRow | None:
    """Rename one dictionary entry — `None` if no such row, a refusal if the marker moved.

    One function for the five dictionaries, like every other function in this module: the dictionary
    arrives as a model, so the fifth one cannot be the one that got its own edit path (SC-2-03,
    K-05).

    **`None` means "no such entry", and it is established before the marker is looked at.** A
    deleted or never-existing row therefore answers `404` and not `409`, the same precedence
    ADR-0007 requires and SC-3-01's R-01 applied: a conflict reported for a row that is not there
    tells the caller it exists. The existence read is *not* the guard and is not check-then-act:
    the guard is the `WHERE` of the `UPDATE` below, which runs whatever this read saw.

    A duplicate name is refused by the database (`uq_<table>_name_normalized`), never by a `SELECT`
    here asking whether a similar name exists: that pre-check is the mutation that has already
    survived delivered tests twice in this repository (SC-1-02, SC-1-04), and two callers renaming
    two entries to "Senior" at once would both pass it.
    """
    entry = session.get(model, entry_id)
    if entry is None:
        return None
    if not _apply_marked_update(
        session,
        model.__table__,
        entry_id,
        expected_updated_at=expected_updated_at,
        changes={"name": name},
        subject="catalogue entry",
    ):
        raise _marker_conflict(subject="catalogue entry")
    # The in-memory row still holds the pre-update name, and `updated_at` was computed by the
    # database, so it is reloaded before anyone shapes a response out of it.
    session.refresh(entry)
    return entry


EDITABLE_RATE_FIELDS: frozenset[str] = frozenset(
    {
        "default_cost_rate",
        "default_selling_rate",
        "currency",
        "effective_from",
        "effective_to",
        # SC-5-02 (F-07): the surcharge percentage and the "already includes it" flag live on this
        # same row (ADR-0013, addendum 2026-09-25, Q5) and are edited through this same allow-list —
        # no second edit path for two columns of one row already being edited here.
        "surcharge_percent",
        "includes_surcharge",
        # SC-5-08 (F-07): the unit of `default_cost_rate` — half of the rate's definition, edited
        # through the same allow-list and **as a pair with the amount** (the request schema refuses
        # exactly one of the two, ADR-0005 addendum 2026-09-29, Q-B).
        "cost_rate_unit",
    }
)
"""Every column `update_rate` will ever write — an allow-list, never "whatever was sent".

What is deliberately absent, and why:

- the five key columns (`role_id`, `seniority_id`, `location_id`, `engagement_type_id`,
  `vendor_id`). They say *which* rate this row is; editing them turns a correction into a re-keying
  of an existing window onto another tuple, which is a decision nobody has taken (Issue #49 scopes
  this task to adding and editing, not re-keying). A caller who priced the wrong tuple adds the
  right one — the row they meant to write is a different row.
- `unit` (the **selling** rate's unit): the database admits exactly `'hour'`
  (`ck_catalog_default_rates_unit_is_hour`), so an editable field would be one whose every value but
  the current one is refused. Not to be confused with `cost_rate_unit`, which is editable above.
- `id`, `created_at`, `updated_at`, `valid_period`: not user input at all. `valid_period` in
  particular is generated, and naming it here would be the second place a window's boundary is
  decided (ADR-0008, point 3).

An allow-list rather than a deny-list, for the reason `app.data.project_writes.EDITABLE_FIELDS`
gives: a column added to this table later is unwritable until somebody decides it should be."""


class CatalogFieldNotEditable(RuntimeError):
    """A caller asked `update_rate` to write a column outside `EDITABLE_RATE_FIELDS`, or nothing.

    A programming error, not a client error: the API request schema can express neither case, so
    this guards the *next* call site (an import, a script, a future endpoint) rather than the one
    that exists today — the same role as `app.data.project_writes.ProjectFieldNotEditable`.
    """


def update_rate(
    session: Session,
    rate_id: uuid.UUID,
    *,
    expected_updated_at: datetime,
    changes: Mapping[str, Any],
    stored_cost_rate_unit_must_be: str | None = None,
) -> CatalogDefaultRate | None:
    """Edit one rate window — `None` if no such rate, a refusal if the marker or the data says no.

    **Partial by construction** (Issue #49, gate-1 decision Q-2). `changes` carries exactly the
    fields the caller named; a field that is absent is not written, and that is what makes the row
    editable at all by a caller who cannot *see* one of its fields: `default_cost_rate` is removed
    from every response to a caller without `PERSONNEL_COSTS_READ`
    (`app.api.response_shaping.shape_catalog_rate`), so their edit request cannot carry it back and
    must not have to. An omitted cost rate leaves the stored one exactly as it was — never
    coerced to null, never required, never replaced by a value the client invented.

    Three refusals, and they stay three different answers:

    - **`None`** — no such rate. Established before the marker, so `404` keeps precedence over
      `409` (ADR-0007; SC-3-01 R-01).
    - **`CatalogConcurrentEditConflict`** — the marker moved between the read and this write. The
      comparison is the `WHERE` clause of the `UPDATE`, evaluated by the database.
    - **`CatalogWriteRefused`** — the state of the data refuses the new values: the window now
      overlaps another one for the same tuple (`EXCLUDE`, SQLSTATE `23P01`), or a CHECK constraint
      rejects them. Not pre-checked here either (ADR-0008): the `EXCLUDE` is evaluated inside this
      `UPDATE`, so an edit that moves a window onto a competitor's is refused even if the competitor
      committed a moment ago.

    Nothing here rounds an amount. The column's scale is deliberately larger than any currency's
    minor unit (ADR-0008, point 6), rounding is the consumer's rule (`app.core.money.round_money`),
    and an amount more precise than the column is refused at the API boundary rather than truncated
    here (R-05) — on the edit path exactly as on the create path.
    """
    forbidden = sorted(set(changes) - EDITABLE_RATE_FIELDS)
    if forbidden:
        raise CatalogFieldNotEditable(
            "These rate fields cannot be edited through this function: "
            + ", ".join(forbidden)
            + f". Editable: {', '.join(sorted(EDITABLE_RATE_FIELDS))}."
        )
    if not changes:
        raise CatalogFieldNotEditable("An edit must name at least one field to change.")

    rate = session.get(CatalogDefaultRate, rate_id)
    if rate is None:
        return None
    if not _apply_marked_update(
        session,
        CatalogDefaultRate.__table__,
        rate_id,
        expected_updated_at=expected_updated_at,
        changes=changes,
        subject="catalogue rate",
        must_equal=(
            None
            if stored_cost_rate_unit_must_be is None
            else {"cost_rate_unit": stored_cost_rate_unit_must_be}
        ),
    ):
        if stored_cost_rate_unit_must_be is not None:
            # Which condition failed is told apart **after** the fact, from a fresh read — the
            # guard itself was the `WHERE`. The refusal body names the condition, neither the
            # stored unit nor any rate; the status still tells whether the guess was right
            # (ADR-0005 addendum 2026-09-29, point 7, Q-B).
            session.refresh(rate)
            if (
                rate.updated_at == expected_updated_at
                and rate.cost_rate_unit != stored_cost_rate_unit_must_be
            ):
                raise refusal_by_condition(
                    subject="catalogue rate",
                    condition=COST_RATE_UNIT_CONDITION,
                    reason=COST_RATE_UNIT_REASON,
                    refused=CatalogWriteRefused,
                )
        raise _marker_conflict(subject="catalogue rate")
    # Re-read after the commit, for the reason `create_rate` gives: the columns are `NUMERIC(14,4)`
    # and `valid_period` is generated, so the row the response describes must be the row the
    # database holds rather than the values Python passed in.
    session.refresh(rate)
    return rate


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


DEFAULT_RATE_LIST_LIMIT = 2000
"""The page size `GET /catalog/rates` uses when the caller names none (K-11).

Measured, not guessed (reviewer, R-02): five subcontractors' catalogues answered the unbounded
query in 9.4s and 125MB, against the client's 12s budget — with no `LIMIT` at all, that number
grows with the catalogue and eventually the endpoint answers nothing at all. 2000 rows is well
inside a single response the client already renders comfortably, and a caller that genuinely needs
more pages by asking for them, deliberately, rather than by the catalogue quietly growing past a
budget nobody set."""

MAX_RATE_LIST_LIMIT = 5000
"""The ceiling `limit` may name — above it the API refuses with a `422`, not a silent clamp to this
value (K-11): a clamp would make `?limit=50000` behave like a lie the server tells back, answering
5000 rows to a caller who asked for 50000 and has no way to tell the difference from "there are
only 5000")."""

MAX_RATE_LIST_OFFSET = 1_000_000
"""The ceiling `offset` may name — refused with a `422` above it, exactly as `limit` is (R-02,
gate-2 review 2026-09-21).

Two separate things made this necessary, and only the first was reported:

1. **A large enough value was a `500`.** `offset` had a floor (`ge=0`) and no ceiling, so anything
   Python accepts as an `int` reached `OFFSET :n`. Past PostgreSQL's `bigint` the driver refuses the
   parameter, and this application installs no exception handler — so a query string a client can
   type by accident surfaced as an unhandled server error instead of a `422` naming the field.
2. **An unbounded `offset` reopens R-01.** The page query is a bounded top-N now, but what it is
   bounded *by* is `limit + offset`: `OFFSET n` still walks and discards `n` rows through the index
   before the first row of the page is emitted. A ceiling on `limit` alone would leave the server
   side unbounded through the other parameter, which is the finding this pair of changes exists to
   close.

A million rows is past any catalogue this system plans for (`DEFAULT_RATE_LIST_LIMIT` documents the
measured one at five subcontractors), so the refusal falls only on a caller that is paging past the
end of everything — and it is a **refusal, not a clamp**, for the reason `MAX_RATE_LIST_LIMIT`
gives: silently answering a different page from the one asked for is a lie the caller cannot detect.
A client that genuinely needs to walk deeper than this needs keyset paging (a `WHERE
(effective_from, id) < (…)` cursor), which is a different API and a decision nobody has taken."""


def _rate_filters(on_date: date | None) -> tuple[sa.ColumnElement[bool], ...]:
    """The `WHERE` of both halves of a rate listing — spelled once so they cannot disagree.

    The page and `total` answer the same question about different numbers of rows, so a filter
    applied to one and not the other makes the response describe a set that does not exist.
    Dropping `where(*filters)` from the count alone left the whole suite green once already
    (QA, 2026-09-21) — this is the shape that makes the same edit impossible to write."""
    return (covering(on_date),) if on_date is not None else ()


def rate_page_statement(
    *, on_date: date | None, limit: int, offset: int
) -> sa.Select[tuple[CatalogDefaultRate, int]]:
    """The **one** statement `list_rates` runs: one page of rates, plus the total, in one snapshot.

    Public rather than private because its *plan* is a claim this module makes
    (`test_r_01_a_page_is_read_as_a_bounded_top_n_not_a_sort_of_the_whole_catalogue`): a test that
    re-built the statement for itself would be asking the planner about a query nobody runs.

    Two properties that pull in opposite directions, both kept:

    - **One statement** (R-04). `total` is a scalar subquery in the target list, not a second
      `SELECT`: it is evaluated inside the same statement and therefore the same snapshot as the
      page, so no concurrent write can land between the two and make the response describe a table
      that never existed at any instant (a `total` below the page's own length, or one counting a
      row the page could not see).
    - **A bounded page** (R-01, gate-2 review 2026-09-21). The page is a subquery carrying its own
      `ORDER BY … LIMIT … OFFSET`, so with `ix_catalog_default_rates_effective_from_id` in place the
      planner answers it with an index scan that stops after `limit + offset` rows.
      `count(*) OVER ()` — what this replaced — could not: a window function with no partition has
      to consume every filtered row before it can emit the first one, so the `LIMIT` above it cut
      the *response* while the server still sorted the whole catalogue underneath. That is the
      single reason for the subquery shape here; read as a stylistic rewrite, it invites being
      folded back into the shorter window-function spelling that was the defect.

    **The two are not in tension, and the resolution is worth naming**: what has to be bounded is
    the *page*, and `total` is a count of the filtered set, which is linear in that set whichever
    statement produces it and whichever index exists. So the count stays deliberately linear,
    inside the page's snapshot, and the work that scales with the catalogue is one `count(*)` the
    planner can answer from an index-only scan rather than a full sort plus a materialised window.
    The outer `ORDER BY` repeats the page's own: a subquery's order is not guaranteed to survive
    into the enclosing query by SQL's rules, and it is free here (the planner sees the input already
    sorted), unlike the sort this change exists to remove.
    """
    filters = _rate_filters(on_date)
    page = (
        sa.select(CatalogDefaultRate)
        .where(*filters)
        .order_by(CatalogDefaultRate.effective_from.desc(), CatalogDefaultRate.id.desc())
        .limit(limit)
        .offset(offset)
        .subquery()
    )
    paged_rate = aliased(CatalogDefaultRate, page)
    total = (
        sa.select(sa.func.count()).select_from(CatalogDefaultRate).where(*filters).scalar_subquery()
    )
    return sa.select(paged_rate, total.label("total")).order_by(
        paged_rate.effective_from.desc(), paged_rate.id.desc()
    )


def rate_total_statement(*, on_date: date | None) -> sa.Select[tuple[int]]:
    """`count(*)` over the same filtered set — the fallback for the one case the page cannot carry.

    Used only when the page comes back empty: the total travels *on* the page rows, so an empty page
    has nothing to carry it (see `list_rates`). Shares `_rate_filters` with the page, which is what
    keeps "the count of what the filter matched" one definition rather than two."""
    return sa.select(sa.func.count()).select_from(CatalogDefaultRate).where(*_rate_filters(on_date))


def list_rates(
    session: Session,
    *,
    on_date: date | None = None,
    limit: int = DEFAULT_RATE_LIST_LIMIT,
    offset: int = 0,
) -> tuple[Sequence[CatalogDefaultRate], int]:
    """One page of rate rows, or only those whose window covers `on_date`, and the total count.

    No caller predicate here either (K-01). `on_date` is an *optional filter* on a list; resolving
    one rate for one tuple is `resolve_rate` below, and the two share `covering` so that "which rate
    applies on day D" has one answer whichever entry point asks.

    **K-11.** Returns `(page, total)`: `page` is at most `limit` rows, `total` is every row
    matching `on_date` (or the whole table, with none) counted without a limit — so a caller can
    tell "this is everything" from "this is page one of more" without a second request.

    Ordered by `(effective_from, id)`, both **descending**, both always present (R-02, reviewer
    2026-09-21). Newest window first: with a realistic catalogue carrying several windows per
    dimension tuple (`docs/PLAN.md`), an ascending default page put the entire oldest, expired
    window first and nothing in the frontend lets a caller move to a later page — the default page
    is the only page a user ever sees, so it must be the one whose rates plausibly apply today, not
    the oldest history the table happens to hold. `id` remains the tie-breaker for the identical
    reason it always was — two rows sharing `effective_from` still need one fixed order between
    calls — descending along with `effective_from` rather than mixed, so the combined key stays one
    direction and a page boundary is still `(effective_from, id)` compared the same way on both
    columns. Without the tie-breaker, `offset`/`limit` paging over rows the database is free to
    reorder among ties could duplicate or skip a row between two calls, the gap K-11 asks a test to
    close.

    **R-04.** `total` and `page` are read from *one* statement, not two. Two separate `SELECT`s
    (a `count(*)` then the page) each get their own snapshot under READ COMMITTED, so a concurrent
    write between them could move `total` and `len(page)` out of step with each other — a `total`
    that undercounts what the page just proved exists ("fewer rows than this" while holding more),
    or one that overcounts a row the page's own snapshot no longer sees. `rate_page_statement` folds
    the total into the same query, and therefore the same snapshot, as the page — closing that
    window for the case that matters: whenever the page is non-empty, `total` describes the exact
    snapshot the page came from.

    **R-01 (gate-2 review 2026-09-21).** That fold used to be `count(*) OVER ()`, which made the
    `LIMIT` bound the response and nothing else: the window function has to consume every filtered
    row before emitting the first, so the server sorted the whole catalogue to answer one page. It
    is now a scalar subquery next to a self-contained page subquery, over an index that produces the
    page's order — see `rate_page_statement` for why that keeps both properties at once, and for
    what is still linear on purpose (`total` itself).

    The total travels *on* the page's rows, so a page with no rows carries no total — which reads
    identically to "the query matched nothing", whether that is because the table is empty or
    because `offset` overshot a non-empty one. Either way a plain `count(*)` is needed to tell the
    two apart, so that one case alone keeps a second statement (`rate_total_statement`).
    """
    rows = session.execute(rate_page_statement(on_date=on_date, limit=limit, offset=offset)).all()
    if not rows:
        return [], session.execute(rate_total_statement(on_date=on_date)).scalar_one()
    page = [row[0] for row in rows]
    total = rows[0].total
    return page, total


def vendor_is(vendor_id: uuid.UUID | None) -> sa.ColumnElement[bool]:
    """`vendor_id = :vendor` — or `vendor_id IS NULL` when no vendor is named (SC-2-03, K-03/K-04).

    The one place this codebase turns "which vendor" into SQL, for the reason `covering` exists:
    **omitting the vendor means "the organisation's own rate", never "any vendor"**, and a second
    spelling of that rule is a second chance to get it backwards.

    Written as an explicit `is_(None)` rather than left to SQLAlchemy's `== None`: the two compile
    the same way today, and the difference between them is invisible until somebody passes the value
    through a bound parameter — at which point `vendor_id = NULL` matches nothing and every internal
    lookup silently starts answering `404`.

    What "any vendor" would cost, concretely: a tuple priced both internally and by a vendor covers
    one day with two rows, so a lookup without this predicate makes `one_or_none()` raise on valid
    data (K-03), and a lookup that picked one would answer a subcontractor's price to a question
    about the organisation's own (K-04).
    """
    if vendor_id is None:
        return CatalogDefaultRate.vendor_id.is_(None)
    return CatalogDefaultRate.vendor_id == vendor_id


def resolve_rate(
    session: Session,
    *,
    role_id: uuid.UUID,
    seniority_id: uuid.UUID,
    location_id: uuid.UUID,
    engagement_type_id: uuid.UUID,
    vendor_id: uuid.UUID | None,
    on_date: date,
) -> CatalogDefaultRate | None:
    """The rate for one full dimension tuple on one calendar day, or `None` if no window covers it.

    `vendor_id` is a required keyword argument with **no default** (R-03, reviewer 2026-09-21) —
    unlike `create_rate`'s, which keeps its default for the opposite reason documented there. A
    resolution call is answering "whose price", and `None` is one specific, valid answer to that
    question (**the internal rate**, the same thing it means in the column, SC-2-03) — but it must
    be an answer the caller chose, not one this function chose for a caller that forgot to. The
    concrete failure a default would reopen: a future consumer resolving a rate from a full
    dimension tuple it already has in hand (Issue #9, a rate on a staffing position) calls this with
    the tuple's fields spread in and nothing else, gets the organisation's own rate for a tuple that
    may only be priced by a subcontractor, and never sees an error — `unit` on `create_rate` is kept
    explicit for the identical reason (`app.data.catalog.create_rate`). The one call site there is
    (`app.api.catalog.read_effective_rate`) already passes `vendor_id` explicitly, so this changes
    no behaviour today; it only closes the door a later, careless call site would otherwise walk
    through.

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
        vendor_is(vendor_id),
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
    vendor_id: uuid.UUID | None = None,
    default_cost_rate: Decimal,
    default_selling_rate: Decimal,
    currency: str,
    unit: str,
    effective_from: date,
    effective_to: date | None,
    surcharge_percent: Decimal = Decimal("0"),
    includes_surcharge: bool = False,
    cost_rate_unit: str = "hour",
) -> CatalogDefaultRate:
    """Insert one rate row and commit it — or raise `CatalogWriteFailed` if the database refuses.

    `cost_rate_unit` (SC-5-08) is the unit `default_cost_rate` is stated in and defaults to `hour`,
    what every rate before it was; the accepted values are enforced by the database
    (`ck_catalog_default_rates_cost_rate_unit_is_known`) and, for the API, by the request schema.

    `surcharge_percent`/`includes_surcharge` default to `0`/`False` (SC-5-02) — the same "no
    surcharge configured" a tuple written before this task existed carries, never an unanswered
    question: `0` is a legal, meaningful percentage (unlike a missing cost rate).

    **No pre-check for an overlapping window.** The refusal is the `EXCLUDE` constraint, evaluated
    by the database inside the `INSERT`. A `SELECT` here asking "does an overlapping row exist?"
    would be a check-then-act window: a competing connection committing its row between that
    `SELECT` and this `INSERT` produces two overlapping rows, and no single-threaded test can see
    it (ADR-0008, "Considered alternatives"; the same mutation survived delivered tests in SC-1-02
    and SC-1-04).

    `valid_period` is absent from the arguments and from the insert: it is a generated column, and
    assigning it would be the second place the window's boundary is decided (ADR-0008, point 2).

    `unit` is an explicit argument with no default, so a second call site cannot omit it and inherit
    whatever this function happened to prefer; the accepted value is enforced by the database
    (`ck_catalog_default_rates_unit_is_hour`) and, for the API, by the request schema.

    `vendor_id` does have a default, and the opposite reasoning applies: `None` is not "unspecified"
    but the stored value that means "the organisation's own rate" (SC-2-03), so an omitted argument
    writes a well-defined row rather than an ambiguous one. `NULL` is written as `NULL` and never as
    `VENDOR_KEY_SENTINEL`: the sentinel exists inside the `EXCLUDE` key expression and nowhere in
    the data (K-07).

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
        vendor_id=vendor_id,
        default_cost_rate=default_cost_rate,
        default_selling_rate=default_selling_rate,
        currency=currency,
        unit=unit,
        effective_from=effective_from,
        effective_to=effective_to,
        surcharge_percent=surcharge_percent,
        includes_surcharge=includes_surcharge,
        cost_rate_unit=cost_rate_unit,
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
