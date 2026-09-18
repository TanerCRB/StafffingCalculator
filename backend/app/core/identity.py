"""Caller identity and permission vocabulary.

ADR-0005 defines three independent dimensions: role (what you may *do*), project scope
(*which* projects you see at all, via `project_access`) and `can_view_personnel_costs`.
This module holds the first and third as data; the second one is not represented here on
purpose — project scope is a database filter applied by `app.data.project_reads`, never a
set carried around in memory.

The identity object is built per request (see `app.api.deps`) and never cached at module or
session scope — a cached identity is exactly the failure mode `agents/invariant-guardian.md`
describes for shared state.
"""

from dataclasses import dataclass, field
from enum import StrEnum


class Permission(StrEnum):
    """Permissions an endpoint can require. Deny-by-default: an endpoint that does not declare
    one gets no caller at all (ADR-0005, "Konsekwencje", rule 7 of the Invariant Guardian)."""

    PROJECT_READ = "project:read"
    PERSONNEL_COSTS_READ = "personnel_costs:read"


@dataclass(frozen=True)
class CallerIdentity:
    """Who is calling, and what they are allowed to do — for the duration of one request."""

    user_id: str
    permissions: frozenset[Permission] = field(default_factory=frozenset)

    def has(self, permission: Permission) -> bool:
        return permission in self.permissions
