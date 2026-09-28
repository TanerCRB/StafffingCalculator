"""The register of named persons (F-03, SC-2-06) — the first personal-data register of this system.

Every column of this table is decided by ADR-0019 (`docs/architecture/decisions/`, point 3,
"Minimalizacja"), not chosen here, and the set is asserted by **equality** against the migrated
database (`tests/test_people_register.py`, criterion K-01):

- `id` — the technical identifier, the only reference a staffing position holds;
- `full_name` — first and last name, **one** column (fewer cultural assumptions, one field to
  correct and to anonymise; no calculation needs them split);
- `created_at` — record metadata; the one column a future retention rule can stand on;
- `updated_at` — ADR-0007's concurrency token for the correction path (RODO art. 16).

**What this row does not have and may not grow without a dated annex to ADR-0019** (and a re-armed,
never loosened, column-set test): e-mail, phone, note/comment, job title, department, manager, rate
(selling or cost — Q-1/Q-2), location (Q-5), personnel number, a link to a user account (P-2), an
"active" flag. Each widens the purpose or the scope of the processing.

**No uniqueness on `full_name`.** Two people with the same name are legal; they are told apart by
`id` alone (ADR-0019, point 3, PD-3).

**Organisational data with no project scope** (ADR-0019, "Decyzja" pt 1; ADR-0005, aneks 2026-09-27,
point 2): no column ties a row to a project, a user, a business unit or a tenant, so there is no
scope function for this table. The protection is the permission pair `PEOPLE_READ`/`PEOPLE_WRITE`,
refused as a whole resource (`403`), never a blanked field.

**Outside the approval snapshot, on purpose** (ADR-0004, aneks 2026-09-27 SC-2-06, point 1): the
name enters no calculation, and a snapshot has no `UPDATE` path, so a name frozen there could be
neither corrected nor erased. An approved scenario shows the *current* name. Not a scenario child
either — there is no entry for this table in `SCENARIO_CHILD_COPIERS`, and a copied position points
at the same person (point 4).

**Fictitious data only** until every condition of ADR-0019 point 8 is met — in every database,
fixture and seed script.
"""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, String, func
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

FULL_NAME_MAX_LENGTH = 200
"""`VARCHAR(200)` — ADR-0019, point 3. The request schema's bound comes from here."""

FULL_NAME_CANONICAL_EXPRESSION = (
    "full_name !~ '^[[:space:]]' AND full_name !~ '[[:space:]]$' AND char_length(full_name) > 0"
)
"""ADR-0019, point 3 and aneks 2026-09-28 (D-3 = A): the name in canonical form — not empty, no
whitespace character (`[[:space:]]`) at either end. The ASCII whitespace (space, tab, LF, CR, VT,
FF) is the guarantee; which non-ASCII characters the class covers depends on the database's
ctype — a named limit, and the request schema trims by Python's wider rule anyway.

A data rule, and at the same time **the only constraint that fails on a row carrying a real name**
(`' Jan Kowalski'`): PostgreSQL's `DETAIL: Failing row contains (…)` would then quote that name,
which is what gives criterion K-09 (NF-11, channel 2) a contrast at all. `NOT NULL` and the
`VARCHAR(200)` bound carry no value in their messages, so on their own they would leave that
criterion empty.

Spelled here and once more in migration `c4d7e2a9b1f6` (a migration keeps describing the schema it
produced); a schema-drift test compares the two copies."""

FULL_NAME_CANONICAL_CONSTRAINT = "ck_person_full_name_canonical"
"""The constraint's name in the database (naming convention `ck_<table>_<name>`), spelled once so
the tests asserting a refusal came from *this* mechanism do not repeat it."""


class Person(Base):
    """One named person who may be assigned to a staffing position (F-03: "Assigning a named person
    shall be optional"). A separate record, **not** a user account (decision P-2 = a)."""

    __tablename__ = "person"

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    full_name: Mapped[str] = mapped_column(String(FULL_NAME_MAX_LENGTH), nullable=False)
    """Personal data. Never in a URL, a log line, an exception message or a refusal body
    (ADR-0019, point 6) — every message on the write path names the constraint and the identifier,
    never this value (`app.data.write_errors`)."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    """ADR-0007's concurrency token for the correction of a name — the database's clock, never this
    process's. The same pattern as the catalogue dictionaries (SC-2-04), not a new mechanism
    (ADR-0004, aneks 2026-09-27 SC-2-06, point 5)."""

    __table_args__ = (CheckConstraint(FULL_NAME_CANONICAL_EXPRESSION, name="full_name_canonical"),)
