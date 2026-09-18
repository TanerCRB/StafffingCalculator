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
    PROJECT_CREATE = "project:create"
    """Creating a project is an *action*, not a read. ADR-0005 splits the permission model by
    role precisely along this line ("rola określa czynności: tworzenie/edycja vs. tylko odczyt"),
    so a viewer holding `PROJECT_READ` must not be able to create — hence a separate permission
    rather than an overloaded one. Project *scope* stays out of this enum: a project that does
    not exist yet cannot be scoped, and scope for existing rows is a database filter
    (`app.data.project_reads`), never a set carried in memory."""

    PERSONNEL_COSTS_READ = "personnel_costs:read"

    PROJECT_EDIT = "project:edit"
    """Editing an existing project (SC-1-02) — separate from `PROJECT_CREATE` and from
    `PROJECT_READ`, per ADR-0005's addendum 2026-09-18 ("uprawnienia akcji zapisu"): the
    granularity is there because archiving, copying and editing are plausibly different people's
    rights. Holding this permission says nothing about *which* projects may be edited: scope stays
    a database filter (`app.data.project_reads`), so an edit of a project outside the caller's
    `project_access` is not a forbidden edit but an invisible one (404, not 403)."""


@dataclass(frozen=True)
class CallerIdentity:
    """Who is calling, and what they are allowed to do — for the duration of one request."""

    user_id: str
    permissions: frozenset[Permission] = field(default_factory=frozenset)

    def has(self, permission: Permission) -> bool:
        return permission in self.permissions
