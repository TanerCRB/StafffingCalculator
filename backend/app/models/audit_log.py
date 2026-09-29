"""`audit_log` — the change history F-12 asks for: who did what, and when (ADR-0004; SC-8-01).

**The first table of plan block 8.** ADR-0004's "Konsekwencje" section named it from the start
("Change history (F-12, 'change history identifying the author, time, and affected data') requires
a separate `audit_log` table independent of the snapshots") and its addendum of 2026-09-18 deferred
it, dated and named, to this block. Nothing before this task ever wrote a row here.

**Dedicated to scenario lifecycle events, not a generic system-wide table** (ADR-0004, addendum
2026-09-27 SC-8-01, point 1). A polymorphic `resource_type`/`resource_id` pair would let a single
table absorb every future kind of event this repository ever grows, and that is exactly what is
rejected: the columns below are real foreign keys (`scenario_id`, `project_id`), and `action_type`
is a **closed** vocabulary. Today it carries exactly one member, `SCENARIO_APPROVED` — adding a
second is a schema change (a new enum value plus a new writer), never a free-text column that lets
a caller invent one.

**`affected_data` is a reference, never a descriptive copy** (point 2 of the same addendum). No
field of the scenario or the project — name, owner, status — is copied into a row here: the approval
snapshot (`app.models.approved_snapshot`) is already the source of truth for *what* was approved,
and a second, independent copy of descriptive fields is the first place the two could drift apart.
What this table records is *that* an approval happened, *who* triggered it and *when* — the
scenario/project ids are the reference, resolved by joining back to the live rows (or, for an
approved scenario, to the snapshot) rather than by re-reading a frozen description here.

**Written once, inside the same transaction as the snapshot and the status flip, after the status
update is confirmed and before the commit** (point 3) —
`app.data.scenario_approval.approve_scenario` is the only writer, and it is not a fourth
data-modifying CTE of `_snapshot_statement`: the snapshot tables answer "what was approved", this
table answers "who approved it and when", and the mandated order (snapshot → status → audit row →
commit) keeps the second question answered only once the first two are irreversibly true.

**Not a child of `scenarios` in the sense the write-guard or the copy registry know**
(point 4). It is absent from `SNAPSHOT_TABLES` (it holds no organisational value AC-04/AC-10 need
frozen) and absent from `SCENARIO_CHILD_COPIERS` (duplicating a scenario must not duplicate its
history — a copy is a new, unapproved draft, and a history entry pointing at it would say a
duplicate had been approved when only the original ever was). Both absences are required, not
merely permitted, and the canary this task's tests carry is exactly `SNAPSHOT_TABLES`'s own: a
duplicate of an approved scenario has zero `audit_log` rows naming it as `scenario_id`.

**Append-only, and that is a deliberate difference from every other child table of `scenarios`**
(point 5). There is no `UPDATE`, no `DELETE`, and no ADR-0007 concurrency token: a row is written
exactly once, by exactly one writer, and there is no second editor for a token to arbitrate
between — the same reasoning `_ApprovedSnapshotRow` already gives for omitting `updated_at`, except
that here the omission is named as the point of the table rather than as a consequence of it being
a snapshot.

**No new write guard, no new permission** (point 6). Writing this row is a side effect of an
already-gated action (`PROJECT_EDIT`, the same permission `approve_scenario` already requires), not
a second action somebody could reach independently — there is no endpoint that writes only this
table. `app.data.scenario_guard` gains no new predicate for it: the one and only writer runs after
the scenario is already confirmed frozen, so there is nothing left for a `status <> 'approved'`
predicate to protect against.

**Scope inherited like the snapshot** (point 7): this table carries no read path of its own in this
task (out of scope — see the module's own report), so there is nothing yet to scope. The row is
reachable only by joining from a scenario/project a caller's `project_access` already admits, the
same inheritance `approved_snapshot_*` rows rely on.

**What this table does not carry, and will not until named elsewhere:**

- **A real, multi-subject identity.** `performed_by` is today's placeholder caller string
  (`app.core.identity.CallerIdentity.user_id`, `PLACEHOLDER_PERMISSIONS`) — the same one every other
  write path in this repository reads from the request, and the same reservation: it says *which*
  string the request carried, not *who*, in any authenticated sense. The authentication ADR is the
  named closing condition (ADR-0004, addendum 2026-09-27 SC-8-01).
- **Any action besides approval.** Duplication, archiving, editing and copying remain unaudited
  after this task, exactly as they were before it — F-12's "change history" is proven here for one
  action, not for all of them.
- **A read path.** No endpoint returns a row of this table (F-13 access control to the resource is
  a separate, future concern).
"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, Enum, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AuditActionType(StrEnum):
    """The closed vocabulary of `audit_log.action_type` (ADR-0004, addendum 2026-09-27 SC-8-01,
    point 1). Exactly one member today — a second one is a schema change, never a free-text value
    a caller could invent."""

    SCENARIO_APPROVED = "scenario_approved"


class AuditLog(Base):
    """One row per recorded lifecycle event. Today: exactly one write site, one action, one row
    per successful approval (see the module docstring)."""

    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    scenario_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("scenarios.id", ondelete="RESTRICT", name="fk_audit_log_scenario_id"),
        nullable=False,
        index=True,
    )
    """A real foreign key — unlike every `source_*_id` column of `approved_snapshot_*` — because
    this row is a reference to the scenario it is about, never a copy of anything describing it
    (ADR-0004, addendum 2026-09-27 SC-8-01, point 2). `ON DELETE RESTRICT`, explicit: nothing in
    this repository deletes a scenario today, and this spells out that nothing may start doing so
    quietly out from under a history row that names it."""

    project_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="RESTRICT", name="fk_audit_log_project_id"),
        nullable=False,
        index=True,
    )
    """Also a real foreign key, for the same reason. Carried alongside `scenario_id` rather than
    resolved by a join through `scenarios.project_id`, because the whole point of this table is to
    survive as a readable record independent of how the scenario's own row is queried."""

    action_type: Mapped[AuditActionType] = mapped_column(
        Enum(
            AuditActionType,
            name="audit_action_type",
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=False,
    )

    performed_by: Mapped[str] = mapped_column(String(200), nullable=False)
    """The caller identity the request carried — `CallerIdentity.user_id`, the same placeholder
    string `project_access.user_id` already stores (`app.models.project_access`), and under the
    same named, dated reservation: no user table exists yet, so this is not a foreign key. It
    becomes one in the migration that lands the authentication ADR."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    """When the database wrote the row — the database's own clock, never this process's, and never
    updated: this table has no `UPDATE` path (module docstring, point 5)."""
