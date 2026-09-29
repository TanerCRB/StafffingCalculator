"""Request and response schemas for the person register (F-03, SC-2-06; ADR-0019).

**Exactly the approved fields, on the way in and on the way out** (ADR-0019, point 3): an id, a
name, and the concurrency token. No e-mail, phone, note, rate or location field exists here, and
`extra="forbid"` turns a body carrying one into a `422` instead of a silently dropped field — the
request schema says out loud what the table would refuse to have.

**The rule is the database's, the schema is the status code** (as everywhere at this boundary): the
name is trimmed and bounded here so a client's mistake is a `422` naming the field, but the
guarantee is `ck_person_full_name_canonical` and `VARCHAR(200)` — a fixture, a seed script or an
import never passes through this module.
"""

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import UUID4, AwareDatetime, BaseModel, ConfigDict, StringConstraints

from app.models.person import FULL_NAME_MAX_LENGTH

PersonFullName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=FULL_NAME_MAX_LENGTH),
]
"""A person's name on the way in: trimmed, non-empty, at most `VARCHAR(200)`.

Trimmed rather than refused when it carries spaces at either end — the canonical form the database
requires (`FULL_NAME_CANONICAL_EXPRESSION`) is then what reaches it, and a pasted trailing space is
not worth a refusal. The bound comes from the model's own constant, so the boundary cannot drift
from the column it protects."""


class PersonRead(BaseModel):
    """One person as the register returns it — from the list, the create and the correction.

    Three fields, and that is the whole row a client may see: `created_at` stays on the server (it
    is retention metadata, ADR-0019 point 8, not something a planner acts on)."""

    id: uuid.UUID
    full_name: str
    updated_at: datetime
    """ADR-0007's concurrency marker, on every read — required back on a correction."""


class PersonList(BaseModel):
    """One page of the register (ADR-0017): `{"people": [...], "total": int}`, `total` counted in
    the same read as the page, before `limit`/`offset`."""

    people: list[PersonRead]
    total: int


class PersonCreateRequest(BaseModel):
    """The body of `POST /people`: the id the **client** chose, and the name (ADR-0019 addendum
    2026-09-28, D-2 = B). The timestamps are the server's.

    `id` is **required**, so a retried request cannot create the same person twice: the retry hits
    the primary key and is a `409` (`app.data.people.create_person`). Optional would give every
    client that omits it the duplicate back. A version-4 UUID (`UUID4`; the nil UUID and other
    versions are a `422`) — hygiene, not a guarantee of randomness, which is the client's duty."""

    model_config = ConfigDict(extra="forbid")

    id: UUID4
    full_name: PersonFullName


class PersonCorrectionRequest(BaseModel):
    """The body of `PATCH /people/{person_id}` — correcting a name (GDPR art. 16).

    The marker is required, as on every edit (ADR-0007): an edit without one would make the
    protection opt-in for whoever forgets it. The name is required too: it is the only editable
    field, so "partial" and "complete" are the same request."""

    model_config = ConfigDict(extra="forbid")

    updated_at: AwareDatetime
    full_name: PersonFullName
