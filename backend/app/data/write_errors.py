"""Describing a failed write without quoting what was being written (NF-11).

Extracted from `app.data.project_writes` (where it was `_describe_without_values`) when the
catalogue write path needed the same thing. It is one function on purpose: a violated `EXCLUDE`
constraint on `catalog_default_rates` produces a `DETAIL: Failing row contains (…)` line
carrying the personnel cost rate, which is exactly the leak the project version was written
against — and ADR-0008 ("Konsekwencje") requires every table using that pattern to wrap the
error with *this* mechanism rather than a second one."""

from sqlalchemy.exc import SQLAlchemyError


class WriteFailed(RuntimeError):
    """The write failed, described without quoting anything that was being written (NF-11).

    Raised instead of the driver's own exception, which is not safe to let propagate: even with
    `hide_parameters=True` on the engine (which removes SQLAlchemy's `[parameters: …]` echo),
    PostgreSQL attaches its own `DETAIL: Failing row contains (…)` line to a constraint violation —
    the whole row — and whatever prints the unhandled exception prints that too.

    **This exception means "the write broke and nothing here knows why".** It carries no diagnosis
    beyond the identifiers the driver reported, and an API layer must not turn it into a refusal:
    a numeric overflow, a lost connection or a statement timeout answered as "your window overlaps
    an existing one" is a wrong answer with a plausible face on it (R-01, reviewer 2026-09-19). The
    refusals a caller can act on are the subclass below, and only the SQLSTATEs listed with it.
    """


REFUSAL_BY_SQLSTATE: dict[str, str] = {
    "23P01": (
        "It overlaps an existing row for the same key over an intersecting period "
        "(exclusion constraint)."
    ),
    "23505": "A row with that value already exists (unique constraint).",
    "23503": "It references a row that does not exist (foreign key).",
    "23514": "A value breaks the rule the named check constraint enforces.",
}
"""The only database failures that mean "refused by the state of the data", keyed by SQLSTATE.

Deliberately a closed list, and deliberately *not* extended by guesswork. Every other SQLSTATE —
`22003` numeric_field_overflow, `22008` datetime_field_overflow, `22001`
string_data_right_truncation, a timeout, a dropped connection — is a failure the caller cannot fix
by changing which row they are writing, so describing it as a conflict misleads twice: it names a
cause nobody confirmed, and it hides a defect behind a `409`.

The wording is per-code and table-independent, so `exchange_rates` (ADR-0006) and `commercial_terms`
(ADR-0003) inherit it together with the rest of the effective-range pattern (ADR-0008). What is
*not* in the wording: which of a table's several check constraints fired, or which column overlapped
— the constraint name that `describe_without_values` appends answers that, from the driver's own
diagnostics rather than from an assumption made here."""


class WriteRefused(WriteFailed):
    """The database refused the write for a reason the SQLSTATE names (see `REFUSAL_BY_SQLSTATE`).

    The distinction from the base class is what an API layer is allowed to do with it: this one is a
    `409` (the request was understood and the state of the data refused it), the base class is a
    `500` (something broke). Raising the same type for both is how a `409` starts claiming a cause
    that was never established.
    """


def describe_without_values(error: SQLAlchemyError, *, subject: str) -> str:
    """A diagnosis built only from identifiers: error class, SQLSTATE, constraint name.

    These come from psycopg's `diag` fields, which name *what* was violated and never carry column
    values, so the message stays loggable. Everything else about the failure is dropped on purpose —
    a failure is not worth a personal-data leak, and the SQLSTATE plus the constraint name is what a
    reader actually acts on.

    The constraint name is kept deliberately, and it is what the criterion tests assert on:
    "refused" is not a useful claim unless the refusal names the mechanism that refused."""
    diagnostics = getattr(getattr(error, "orig", None), "diag", None)
    parts = [type(error).__name__]
    for label, value in (
        ("sqlstate", sqlstate_of(error)),
        ("constraint", getattr(diagnostics, "constraint_name", None)),
    ):
        if value:
            parts.append(f"{label}={value}")
    return f"Writing the {subject} failed: " + ", ".join(parts)


def sqlstate_of(error: SQLAlchemyError) -> str | None:
    """The SQLSTATE the server reported, or `None` if the failure never reached one.

    `None` is the honest answer for a connection lost before the statement was sent, and it is the
    reason the classification below is "is this code one of the four", never "is this code absent
    from a list of fatal ones"."""
    diagnostics = getattr(getattr(error, "orig", None), "diag", None)
    return getattr(diagnostics, "sqlstate", None)


CONCURRENCY_MARKER_CONDITION = "updated_at_marker"
"""The name of the refusal that has no SQLSTATE: the ADR-0007 marker in the `UPDATE`'s `WHERE`.

A conditional `UPDATE` that matches no row raises nothing — it is a refusal the database *performed*
(the comparison ran inside the statement) but did not *report*, so `describe_without_values` above
has no `diag` to read and the message would otherwise have to be written free-hand at each call
site. This constant plays the part the constraint name plays for the other refusals: the one
identifier a `409` body carries to say which mechanism refused.

It matters because two unrelated causes share the status code on the catalogue's edit path (Issue
#49): an overlap or a duplicate name (SQLSTATE `23P01`/`23505`, a constraint name) and a stale
marker (no SQLSTATE, this condition). A client that cannot tell them apart cannot tell "re-read and
try again" from "this edit will never succeed as written"."""

CONCURRENCY_MARKER_REASON = (
    "The row changed since it was read (concurrency marker). Re-read it and edit again."
)
"""What a marker mismatch means, in the vocabulary of `REFUSAL_BY_SQLSTATE`'s entries.

Carries nothing about the competing change — not the field, not the value, not who made it. This is
a refusal, not a merge, and the other writer's value may well be data this caller is not entitled to
see (ADR-0007: "without information about the content of someone else's change"; NF-11)."""


def refusal_by_condition[RefusedT: WriteRefused](
    *,
    subject: str,
    condition: str,
    reason: str,
    refused: type[RefusedT],
) -> RefusedT:
    """Build the refusal for a check the database evaluated but reported no SQLSTATE for.

    The message is deliberately the *same shape* as `describe_without_values`' — "Writing the
    {subject} failed: {class}, {identifier}. {reason}" — with `condition=…` where the
    driver-reported refusals carry `sqlstate=…, constraint=…`. Same shape so both are readable by
    one client, and a different identifier so the two are never the same string: that
    distinguishability is the point (see `CONCURRENCY_MARKER_CONDITION`).

    Values are absent here by construction rather than by filtering: nothing about the row is passed
    in, so there is nothing to leak (NF-11).
    """
    return refused(
        f"Writing the {subject} failed: {refused.__name__}, condition={condition}. {reason}"
    )


def failure_for(
    error: SQLAlchemyError,
    *,
    subject: str,
    refused: type[WriteRefused] = WriteRefused,
    failed: type[WriteFailed] = WriteFailed,
) -> WriteFailed:
    """Build the exception to raise for a failed write: `refused` if the SQLSTATE says so, else
    `failed`.

    One function, so the decision "is this a refusal or a defect" is taken in one place for every
    table using this module, off the server's own SQLSTATE and off nothing else. The returned
    exception is never raised here — the caller raises it `from None`, which is what keeps
    PostgreSQL's `DETAIL: Failing row contains (…)` out of the traceback (NF-11).

    `refused`/`failed` let a table name its own pair (e.g. `CatalogWriteRefused` /
    `CatalogWriteFailed`) without re-deciding the classification; they must derive from
    `WriteRefused`/`WriteFailed`, so a layer catching the shared types still catches them.
    """
    reason = REFUSAL_BY_SQLSTATE.get(sqlstate_of(error) or "")
    description = describe_without_values(error, subject=subject)
    if reason is None:
        return failed(description)
    return refused(f"{description}. {reason}")
