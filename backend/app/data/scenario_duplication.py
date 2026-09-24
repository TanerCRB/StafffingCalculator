"""Duplicating a scenario into its own project (F-09 pt.1, AC-02; SC-6-01, Issue #11).

The second of ADR-0004's three entry points into one copy mechanism — after copying a whole
project (SC-1-03, `app.data.project_writes.copy_project`) and before opening a new version of an
approved scenario (F-12, not yet built). All three call
`app.data.project_writes.copy_scenario`, which does not decide who may see the source and does
not, by itself, decide what to name the copy:

- **scope** is `app.data.staffing.scenario_in_scope`, the same `project_for_caller`-backed lookup
  every other scenario-scoped write in this repository resolves its target through (ADR-0005,
  addendum 2026-09-22, point 4) — never a second, local scope check;
- **naming** is this module's own problem, and the reason it exists as a module rather than a
  function appended to `app.data.project_writes`.

**The one thing project-copying never had to solve.** `copy_scenario` copies `name` verbatim, and
`scenarios` carries `UniqueConstraint("project_id", "name")` (`app.models.scenario`). Copying into
a freshly created project never collides — the target starts with no scenario at all — but copying
into the *source's own* project collides on every call, because the target already holds a
scenario named exactly `source.name`: the source itself. This is therefore the first caller of
`copy_scenario` that must supply a name of its own (gate 1, decision 1), which is why
`copy_scenario` grew an optional `name` keyword argument rather than staying exactly as SC-1-03
left it.

**The naming/retry algorithm** (gate 1, decision 1): `"<source.name> (copy)"`, then
`"<source.name> (copy 2)"`, `"<source.name> (copy 3)"`, … — checked against every scenario name the
*target* project already holds, read once from the `Project.scenarios` collection
`scenario_in_scope` already loaded (no second query). The first candidate not already taken is
used. `MAX_DUPLICATE_NAME_ATTEMPTS` candidates are tried before giving up; giving up raises
`NoAvailableDuplicateName` — a plain, named refusal instead of the `IntegrityError` a 1001st
already-taken candidate would otherwise let `copy_scenario`'s own flush raise.

**What this does not close: a genuine race.** The candidate is chosen from a snapshot of sibling
names read before the insert, not from a lock held on the project's scenario names for the
duration of the whole cascade copy — two duplications of the same source requested at the same
moment can both pick `"… (copy)"`, and the loser then loses the database's own
`UniqueConstraint`. That case is not retried here; it comes back as a `409` through the ordinary
`WriteRefused` classification (`app.data.write_errors.failure_for`), the same answer
`POST /projects/{id}/copy` gives a caller who submits it twice (ADR-0004, addendum 2026-09-18,
point 3: "kopiowanie nie jest idempotentne… zaakceptowany koszt"). Accepted for the same reason: a
losing racer can simply try again, and the alternative — locking the project's scenario names for
the whole cascade — would serialise every duplication of a project's scenarios behind one another
for a case this repository already ships unretried, one entry point over.

**The base name is bounded before any suffix is appended, and that bound is enforced here rather
than relied on from `scenarios.name`'s `String(200)`** (R-01, reviewer, fixed 2026-09-24). The
suffix grows the name on every hop of a chain — duplicating a duplicate suffixes an
already-suffixed name — and F-09 promises "duplicate and modify independently" as an iterative
workflow, so a chain of duplicates is expected use, not a contrived input. Left unchecked, the
name grows by roughly the suffix's own length on every hop and eventually exceeds the column's
bound; `String(200)` refuses it with SQLSTATE `22001`
(`string_data_right_truncation`), which `REFUSAL_BY_SQLSTATE` deliberately does not map (that
exclusion was written for a client-supplied overlong value, never for a server-generated one), so
the write would surface as an opaque, permanent `500` — permanent because no rename endpoint
exists to shorten the name and recover. `_bounded_base_name` below truncates the *source* name
portion, before the suffix is chosen, to whatever room is left after reserving space for the
longest suffix `MAX_DUPLICATE_NAME_ATTEMPTS` could ever produce — so every candidate this module
generates fits the column by construction, and truncating an already-truncated name (the chained
case) truncates to the same bound again rather than growing without limit.
"""

import itertools
import uuid
from collections.abc import Iterator

import sqlalchemy as sa
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.identity import CallerIdentity
from app.data.project_writes import copy_scenario
from app.data.staffing import scenario_in_scope
from app.data.write_errors import WriteFailed, WriteRefused, failure_for
from app.models.scenario import Scenario

MAX_DUPLICATE_NAME_ATTEMPTS = 1000
"""How many `"<name> (copy N)"` candidates this module tries before giving up (gate 1, decision
1). Generous on purpose: reaching it means a thousand duplicates of one scenario already share
this exact name prefix, which is the pathological case the gate-1 decision asks to fail cleanly
on rather than let `IntegrityError` reach a caller as a raw `500`.

Also the bound `_longest_possible_suffix` reserves room for (R-01): the two constants are read
together, once, at the point the base name is truncated, so raising this one automatically
reserves more room rather than silently letting a longer, untruncated suffix overflow the column.
"""


class ScenarioDuplicationFailed(WriteFailed):
    """The duplication broke for a reason nothing here established — a `500`, never a `409`.

    Same division as every other write path in this repository (R-01): a statement timeout, a
    dropped connection or an overflow must not reach a caller as a conflict nobody observed.
    """


class ScenarioDuplicationRefused(ScenarioDuplicationFailed, WriteRefused):
    """The database refused the duplicate for a reason its SQLSTATE names — a `409`.

    Covers the genuine race named in the module docstring: two callers computed the same "next
    available" candidate name from two snapshots taken before either committed, and the database's
    own `UniqueConstraint` is what actually decides between them.
    """


class NoAvailableDuplicateName(RuntimeError):
    """Every candidate up to `MAX_DUPLICATE_NAME_ATTEMPTS` was already taken (gate 1, decision 1).

    Carries no scenario name and no count of *which* names collided (NF-11): the message names
    only how many were tried. Raised only after `scenario_in_scope` has already let this caller
    through, so it cannot become a side channel about a scenario the caller may not see — but the
    source's own name must still not appear in it, on the same principle every other refusal in
    this repository follows.
    """


def _duplicate_name_candidates(base_name: str) -> Iterator[str]:
    """`"<base_name> (copy)"`, `"<base_name> (copy 2)"`, `"<base_name> (copy 3)"`, … — unbounded;
    the caller bounds it with `itertools.islice` (gate 1, decision 1)."""
    yield f"{base_name} (copy)"
    counter = 2
    while True:
        yield f"{base_name} (copy {counter})"
        counter += 1


def _longest_possible_suffix() -> str:
    """`" (copy <MAX_DUPLICATE_NAME_ATTEMPTS>)"` — the longest suffix `_duplicate_name_candidates`
    can produce within the attempt bound (R-01): the counter of the `n`-th candidate is `n` itself
    (for `n >= 2`), so the last of `MAX_DUPLICATE_NAME_ATTEMPTS` candidates carries the largest
    number and therefore the longest suffix. Read as a function of the module global rather than a
    precomputed constant, so a test that monkeypatches `MAX_DUPLICATE_NAME_ATTEMPTS` down (as
    `test_exhausting_every_candidate_name_is_a_clean_409_not_a_raw_500` already does) changes the
    reserved room to match, instead of the two silently disagreeing.
    """
    return f" (copy {MAX_DUPLICATE_NAME_ATTEMPTS})"


def _scenario_name_max_length() -> int:
    """The actual bound of `scenarios.name` (`String(200)`), read off the mapped column rather
    than hardcoded a second time here (R-01) — a future change to the column's length is picked up
    automatically, instead of this module silently reserving room for a bound that no longer
    matches the schema.
    """
    length = sa.inspect(Scenario).columns["name"].type.length
    if length is None:
        # Not reachable for `scenarios.name` today (`String(200)`) — named rather than silently
        # skipped, so a future unbounded column fails loudly here instead of letting an overlong
        # candidate reach the database the way R-01 describes.
        raise RuntimeError(
            "scenarios.name has no bounded length to truncate a duplicate's name against."
        )
    return length


def _bounded_base_name(base_name: str) -> str:
    """`base_name`, cut from the end so that even the longest candidate this module can generate
    from it still fits `scenarios.name` (R-01, reviewer, fixed 2026-09-24).

    Applied to the *source's* name before any suffix is chosen, every hop of a duplication chain
    truncates to the same bound again rather than compounding: a name already at the bound stays
    at the bound after being duplicated a second, a third, a hundredth time. A `base_name` short
    enough to begin with is returned unchanged — slicing to a length not exceeded is a no-op.
    """
    budget = _scenario_name_max_length() - len(_longest_possible_suffix())
    if budget <= 0:
        # Not reachable for `String(200)` and `MAX_DUPLICATE_NAME_ATTEMPTS = 1000` today (the
        # budget is 187), but a future change to either constant must fail loudly here rather than
        # silently emit a suffix-only name or a negative slice.
        raise RuntimeError(
            "The scenario name column has no room left for a duplicate suffix once "
            f"{MAX_DUPLICATE_NAME_ATTEMPTS} attempts are reserved for."
        )
    return base_name[:budget]


def _next_available_name(base_name: str, taken: frozenset[str]) -> str:
    """The first candidate of `_duplicate_name_candidates` not already in `taken`, generated from
    `base_name` truncated to leave room for the longest possible suffix (`_bounded_base_name`,
    R-01) — every candidate this function can return is therefore guaranteed to fit
    `scenarios.name`, whatever `base_name`'s own length was.

    Raises `NoAvailableDuplicateName` rather than looping forever — the generator itself is
    unbounded, so the bound has to live at the call site, once, rather than in the generator.
    """
    bounded_base_name = _bounded_base_name(base_name)
    for candidate in itertools.islice(
        _duplicate_name_candidates(bounded_base_name), MAX_DUPLICATE_NAME_ATTEMPTS
    ):
        if candidate not in taken:
            return candidate
    raise NoAvailableDuplicateName(
        f"Could not find an unused name for the duplicate after {MAX_DUPLICATE_NAME_ATTEMPTS} "
        "attempts. Rename or remove some of its existing copies and try again."
    )


def _failure(error: SQLAlchemyError) -> WriteFailed:
    """`failure_for` bound to this module's pair of exceptions — one place, so every statement of
    the duplication classifies identically (the same reasoning as `app.data.scenario_approval`)."""
    return failure_for(
        error,
        subject="scenario duplicate",
        refused=ScenarioDuplicationRefused,
        failed=ScenarioDuplicationFailed,
    )


def duplicate_scenario(
    session: Session, caller: CallerIdentity, project_id: uuid.UUID, scenario_id: uuid.UUID
) -> Scenario | None:
    """Duplicate one scenario into its own project, as a fresh `draft` (F-09 pt.1, AC-02).

    `None` means "no such scenario *for this caller*" and carries no way to tell why — resolved
    through `scenario_in_scope`, the same `project_for_caller`-backed lookup every scenario-scoped
    write in this repository uses, and resolved **before** any candidate name is computed (gate 1's
    ordering requirement: the scope check fails closed before the name-collision logic runs, so a
    404-vs-409 precedence is structural here exactly as it is on the approval path — a scenario
    outside the caller's scope can never reach a refusal that would confirm it exists).

    The duplicate always starts `draft`, whatever the source's status — decided inside
    `copy_scenario`, not here (ADR-0004) — and the source row is only ever read, never written: its
    own status, name and every child row are untouched by this call.
    """
    source = scenario_in_scope(session, caller, project_id, scenario_id)
    if source is None:
        return None

    taken = frozenset(scenario.name for scenario in source.project.scenarios)
    name = _next_available_name(source.name, taken)

    try:
        copy = copy_scenario(session, source, into_project=source.project, name=name)
        session.commit()
    except SQLAlchemyError as error:
        session.rollback()
        # `from None`, as on every write path here: a chained exception is printed with its cause,
        # so re-raising *with* the original would put PostgreSQL's `DETAIL: Failing row contains
        # (…)` back into the traceback one line further down (NF-11).
        raise _failure(error) from None
    return copy
