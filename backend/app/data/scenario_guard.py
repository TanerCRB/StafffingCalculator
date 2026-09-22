"""The one seam that serialises a write to a scenario's children against its approval (ADR-0004).

Every guarded write in this repository already carries the predicate `status <> 'approved'` **inside
the statement that writes** — that is what makes the refusal a count of affected rows rather than a
Python `if` (ADR-0004, addendum 2026-09-19, point 2). What that predicate does *not* do on its own,
and was named as an open risk when it was introduced, is exclude an approval committing
**concurrently with** the write: under `READ COMMITTED` a plain read of the parent row takes no
lock, so the approval and the child write can both succeed and the child row lands under an
`approved` scenario.

ADR-0004's addendum of 2026-09-22 (point 5) makes closing that window a condition of the approval
endpoint existing at all, and requires it closed **for every child table of `scenarios` at once**,
not for the tables of whichever task happens to be running. This module is that closure, and it is
one mechanism rather than one per table.

**How it works.** The guarded statement selects the parent row `FOR UPDATE` as part of itself.
Two outcomes, and there is no third:

- the approval got there first and committed → the locking scan follows the updated row version and
  re-evaluates its qualification against it (PostgreSQL's `READ COMMITTED` rule for locking
  clauses). `status <> 'approved'` is now false, the row is filtered out, the write matches nothing
  and is refused;
- the child write got there first → it holds the row lock until it commits, so the approval waits
  and lands *after* the child row. A row written before an approval is a row of the calculation
  being approved, which is legal and is what the contrast of criterion K-20 asserts.

The postcondition is therefore the pair, not either half: there is no interleaving in which a child
row exists **and** the scenario was already approved when it was written.

**Two locks on the approving side, and only one of them is promised** (R-02, reviewer 2026-09-22).
The approval transaction (`app.data.scenario_approval`) takes `draft_scenario` as its **first**
statement and holds it to the commit. That is the only lock it takes *by contract* — before any row
is read or written, whatever the scenario contains, whatever the planner decides. The
`unapproved_scenario` selects embedded in the approval's own snapshot inserts lock the same row as
well, but what they lock is a property of the **chosen plan** and of nothing that is written down::

    Nested Loop
      ->  Subquery Scan on s
            ->  LockRows
                  ->  Index Scan using pk_scenarios on scenarios

Measured on PostgreSQL 16 on 2026-09-22, and it settles a question the review left open: `LockRows`
sits directly above the `scenarios` scan and **below** the join, so the parent row is locked even
when the join matches nothing. An approval of a scenario with *no positions* writes zero snapshot
rows and still holds the scenario row — checked by taking `FOR UPDATE NOWAIT` from a second
transaction, which was refused. The conditionality is therefore not "no positions, no lock"; it is
"a plan that drives the join from the other side, no lock", and nothing in the statement forbids
such a plan.

So `draft_scenario`'s `.with_for_update()` is not a redundancy and must not be removed as one — but
the reason is narrower than "nothing else serialises". Removing it does not corrupt a snapshot
today, precisely because of the plan above. What it does is turn a legitimate second, concurrent
approval from a clean `409` into an unhandled `500` ("the draft row locked for this approval could
not be frozen"), and leave the rest of the serialisation resting on a plan. That mutation was run on
2026-09-22: it is killed by `test_k_19_two_concurrent_approvals_of_one_draft_leave_one_snapshot_…`
in `tests/test_scenario_approval.py`, and by nothing else in the suite.

**Why a locking clause rather than a stricter isolation level.** `SERIALIZABLE` would close the same
window, but it closes it by aborting one of the two transactions with a serialisation failure the
application would then have to classify and retry — a second refusal vocabulary next to the two
ADR-0007 already has. A row lock on the parent refuses with the mechanism that is already there
(zero rows affected, diagnosed afterwards), and it is scoped to one scenario rather than to every
transaction in the process.

**Why not a Python `SELECT ... FOR UPDATE` followed by the write.** It would work, and it was
rejected: it makes the *guard* a two-statement sequence again, and two statements are what the
delivered mutation log in this repository records as surviving tests three times (SC-1-02 ×2,
SC-2-01). The lock belongs inside the statement that writes, for the same reason the predicate does.

**What this module is not.** It is not a scope check. Whether the caller may see the scenario at all
is `app.data.project_reads.project_for_caller`, resolved before any of this runs, so a refusal built
here is never the answer that confirms a scenario exists (ADR-0005, addendum 2026-09-22, point 4).
"""

import uuid

import sqlalchemy as sa

from app.models.scenario import Scenario, ScenarioStatus

_SCENARIOS = Scenario.__table__


def unapproved_scenario(scenario_id: uuid.UUID) -> sa.Select[tuple[uuid.UUID]]:
    """`SELECT id FROM scenarios WHERE id = :id AND status <> 'approved' FOR UPDATE`.

    Returned as a statement fragment, never executed here: the caller embeds it in the statement
    that writes — as the source of an `INSERT ... SELECT`, or as `scenario_id IN (…)` inside the
    `WHERE` of the guarded `UPDATE`. Embedded, it is one statement that locks, checks and writes;
    executed separately it would be the check-then-act shape this module exists to avoid.

    Zero rows is the refusal, and it carries no reason: the reason is diagnosed *after* the refusal
    (`app.data.staffing._diagnose_*`), exactly as on every other write path here.
    """
    return (
        sa.select(_SCENARIOS.c.id)
        .where(_SCENARIOS.c.id == scenario_id, _SCENARIOS.c.status != ScenarioStatus.APPROVED)
        .with_for_update()
    )


def draft_scenario(scenario_id: uuid.UUID) -> sa.Select[tuple[uuid.UUID]]:
    """`SELECT id FROM scenarios WHERE id = :id AND status = 'draft' FOR UPDATE`.

    The approval transaction's first statement (`app.data.scenario_approval`). It differs from
    `unapproved_scenario` above in what it is *for*: that one is embedded in a child write and
    refuses it, this one takes the lock the whole approval transaction then holds, and answers "is
    there still a draft to approve?" in the same statement that takes it.

    **This is not check-then-act, and the difference is the lock.** A bare `SELECT status` followed
    by writes would leave the status free to change in between; `FOR UPDATE` holds the row until
    this transaction ends, so nothing can approve, copy-guard or child-write past it. The predicate
    is additionally repeated in the final `UPDATE … WHERE status = 'draft'`, so the guard still
    holds for a caller that reached that statement another way.

    **`.with_for_update()` here is the only lock the approval takes by contract, and removing it is
    not a simplification** (R-02, reviewer 2026-09-22; the measurements are in the module
    docstring). It is taken first, before anything is read or written, and held to the commit. The
    approval's later `unapproved_scenario` selects happen to lock the same row under today's plan,
    which is why removing this one damages no data — it turns a second, concurrent approval into an
    unhandled `500` instead of the `409` it is, and makes everything else depend on a plan nobody
    pinned. The predicate repeated in the final `UPDATE` is no substitute either: a predicate
    re-read under `READ COMMITTED` closes a window that has already opened, a lock stops it opening.
    """
    return (
        sa.select(_SCENARIOS.c.id)
        .where(_SCENARIOS.c.id == scenario_id, _SCENARIOS.c.status == ScenarioStatus.DRAFT)
        .with_for_update()
    )
