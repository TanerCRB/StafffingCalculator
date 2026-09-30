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

from app.models.project import Project
from app.models.scenario import Scenario, ScenarioStatus

_SCENARIOS = Scenario.__table__
_PROJECTS = Project.__table__


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

    **`.with_for_update()` here is the only lock the approval takes on `scenarios`, and removing it
    is not a simplification** (R-02, reviewer 2026-09-22; the measurements are in the module
    docstring). It is taken first, before anything is read or written, and held to the commit. The
    approval's later `unapproved_scenario` selects happen to lock the same row under today's plan,
    which is why removing this one damages no data — it turns a second, concurrent approval into an
    unhandled `500` instead of the `409` it is, and makes everything else depend on a plan nobody
    pinned. The predicate repeated in the final `UPDATE` is no substitute either: a predicate
    re-read under `READ COMMITTED` closes a window that has already opened, a lock stops it opening.

    **Since SC-1-10 the approval takes a second lock, on `projects`** — see
    `approving_project_lock` below. The two are on different tables and serve different windows;
    neither is a substitute for the other, and nothing here claims to be the only lock the approval
    holds overall.
    """
    return (
        sa.select(_SCENARIOS.c.id)
        .where(_SCENARIOS.c.id == scenario_id, _SCENARIOS.c.status == ScenarioStatus.DRAFT)
        .with_for_update()
    )


def draft_scenario_read_lock(scenario_id: uuid.UUID) -> sa.Select[tuple[uuid.UUID]]:
    """`SELECT id FROM scenarios WHERE id = :id AND status = 'draft' FOR SHARE`.

    A scoped read path may take this before reading a composed draft result and hold it through
    the request transaction. Every guarded child write and approval takes `FOR UPDATE` on this
    same row, so either the reader sees the committed write first or the writer waits until the
    reader has finished. The caller must resolve project scope before executing this lock, so a
    missing row remains indistinguishable from an inaccessible scenario.
    """
    return (
        sa.select(_SCENARIOS.c.id)
        .where(_SCENARIOS.c.id == scenario_id, _SCENARIOS.c.status == ScenarioStatus.DRAFT)
        .with_for_update(read=True)
    )


def copying_source_scenario(scenario_id: uuid.UUID) -> sa.Select[tuple[uuid.UUID]]:
    """`SELECT id FROM scenarios WHERE id = :id FOR SHARE` — the copy's lock on its source.

    Taken by `app.data.project_writes.copy_scenario` as its first statement and held to the copy's
    commit (SC-5-05, gate 2 reviewer R-01; ADR-0014, point 10). Every child write here embeds
    `unapproved_scenario`, i.e. `FOR UPDATE` on the same row, and the two modes conflict — so no
    child write of the source can commit **between** the copiers' separate `SELECT`s:

    - **the copy first** → a write to the source waits until the copy has committed, then goes
      ahead against the source (the copy holds the state before it);
    - **the write first** → the copy waits for its commit, and every copier then reads the state
      after it (under `READ COMMITTED` each later statement takes a fresh snapshot).

    Why it became necessary: `additional_cost` is the first child table split between two copiers
    by a column a user can edit (`position_id` — `copy_staffing_positions` takes the attached costs,
    `copy_scenario_additional_costs` the rest). An edit moving a cost across that line between the
    two passes would copy it twice or not at all. The lock covers every child table at once, as the
    approval's closure does, rather than the one table that exposed the window.

    `FOR SHARE`, not `FOR UPDATE`: the copy changes nothing on the source, and two copies of one
    scenario need not queue behind each other. It does queue behind — and hold back — an approval
    (`draft_scenario` is `FOR UPDATE`), which is the same serialisation, not a new one.
    """
    return (
        sa.select(_SCENARIOS.c.id)
        .where(_SCENARIOS.c.id == scenario_id)
        .with_for_update(read=True)
    )


# --- the project row: group-2 fields against an approval (SC-1-10, gate 1 P-C) ------------------
#
# The same window as above, one table up. `app.data.project_writes.update_project` refuses an edit
# of a group-2 project field (ADR-0004, addendum 2026-09-18) with `NOT EXISTS (SELECT … FROM
# scenarios WHERE status = 'approved')` inside its `UPDATE` — which excludes an approval that has
# already committed, and does not exclude one committing alongside the edit: the predicate reads
# `scenarios` without a lock, and the approval never touched `projects` at all. Measured before the
# fix (criterion K-08, "the edit first"): the edit updated the row, an approval of a scenario of the
# same project ran to its commit without waiting for anything, and the edit then committed a new
# `reporting_currency` under a scenario that was already approved.
#
# **The fix is a pair of row locks on the project, one per side, and it covers every group-2 field
# at once** — the set is `FROZEN_BY_APPROVED_SCENARIO`, and the lock is taken whenever an edit
# touches any member of it, so a field joining the set is covered on the day it joins:
#
# - the approval takes `approving_project_lock` (`FOR SHARE`) right after its `draft_scenario` lock,
#   and holds it to its commit;
# - the edit takes `project_group_two_lock` (`FOR NO KEY UPDATE`) *before* its guarded `UPDATE`.
#
# `FOR SHARE` and `FOR NO KEY UPDATE` conflict, so the two transactions serialise on the project
# row, and each order ends legal:
#
# - **the approval first** → the edit's lock waits for the approval's commit; its `UPDATE` is the
#   *next* statement, so under `READ COMMITTED` it takes a fresh snapshot, sees `approved`, matches
#   nothing and is refused (409). That is why the lock is a statement of its own rather than a
#   `FOR UPDATE` inside the `UPDATE`: a `NOT EXISTS` subquery is not re-evaluated when a row lock
#   the `UPDATE` waited for is released — PostgreSQL re-checks only the updated row, against the
#   statement's original snapshot of every other table — so the guard has to *start* after the wait,
#   not merely finish after it;
# - **the edit first** → it holds `FOR NO KEY UPDATE` from its lock to its commit (the `UPDATE`
#   itself takes the same mode), so the approval waits for the edit to commit and approves a
#   calculation that already contains the new value — a write before an approval, which is legal.
#
# **Why not the same shape as the child tables above** (the lock inside the writing statement): a
# child write locks the *scenario* row, which is the row an approval changes. Here the approval
# changes nothing on `projects`, so there is no updated row version for a re-check to follow; the
# lock has to be one the approval takes explicitly, and the edit's guard has to run after it.
#
# **Why `FOR SHARE` on the approving side and not `FOR UPDATE`**: two approvals of two different
# scenarios of one project do not conflict with each other and must not queue behind each other.
# And why `FOR NO KEY UPDATE` rather than `FOR UPDATE` on the editing side: the `UPDATE` never
# changes the key, and `FOR UPDATE` would additionally block every foreign-key check from a child
# row (`scenarios.project_id` inserts take `FOR KEY SHARE`), which nothing here needs.
#
# **No lock cycle**: the approval takes scenario → project, the edit takes project only and never
# locks a scenario row, and no other path in this repository locks a project row and then a
# scenario row. **Not a stricter isolation level** for the reasons given at the top of this module,
# and for one more measured on SC-3-03 (variant A): `REPEATABLE READ` would fix the edit's snapshot
# before its lock is granted — the very ordering this seam exists to avoid.


def project_group_two_lock(project_id: uuid.UUID) -> sa.Select[tuple[uuid.UUID]]:
    """`SELECT id FROM projects WHERE id = :id FOR NO KEY UPDATE` — the editing side of P-C.

    Executed by `app.data.project_writes.update_project` as its own statement, immediately before
    the guarded `UPDATE`, whenever the edit touches a field of `FROZEN_BY_APPROVED_SCENARIO`. It
    decides nothing and returns nothing anyone reads: zero rows (a project deleted meanwhile) is
    left for the `UPDATE` to find, and diagnosed exactly as before.
    """
    return (
        sa.select(_PROJECTS.c.id)
        .where(_PROJECTS.c.id == project_id)
        .with_for_update(key_share=True)
    )


def approving_project_lock(project_id: uuid.UUID) -> sa.Select[tuple[uuid.UUID]]:
    """`SELECT id FROM projects WHERE id = :id FOR SHARE` — the approving side of P-C.

    Taken by `app.data.scenario_approval.approve_scenario` right after `draft_scenario` and held to
    its commit. Removing it (or the editing side above) reopens the race criterion K-08 races, in
    the order "the edit first"; it is not a redundancy with the scenario lock, which no edit of
    `projects` ever asks for.
    """
    return sa.select(_PROJECTS.c.id).where(_PROJECTS.c.id == project_id).with_for_update(
        read=True
    )
