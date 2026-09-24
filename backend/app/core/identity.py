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

    PROJECT_COPY = "project:copy"
    """Copying a project is its own action permission (ADR-0005, addendum 2026-09-18 "uprawnienia
    akcji zapisu", point 1): the addendum keeps `PROJECT_EDIT`, `PROJECT_COPY` and
    `PROJECT_ARCHIVE` separate rather than folding them into one write permission, because the
    person allowed to archive is not necessarily the person allowed to edit or duplicate. A copy
    also writes a *new* `project_access` grant, so it is not covered by `PROJECT_CREATE` either —
    the subject being copied is one the caller must already be able to see."""

    PROJECT_ARCHIVE = "project:archive"
    """Archiving a project is its own action permission, separate from `PROJECT_EDIT` and
    `PROJECT_COPY` (ADR-0005, addendum 2026-09-18, point 1): archiving is routinely the right of
    a different person than editing — a project administrator retires a project, every author
    edits it. Folding the three into one "write" permission would make that distinction
    unexpressible, and a permission cannot be narrowed later without breaking the callers that
    grew to rely on the wide one. What this permission is *not*: a statement about immutability.
    ADR-0004 (addendum 2026-09-18, point 3) keeps archiving a visibility state, so holding this
    permission changes nothing about what may be written to the project's scenarios."""

    CATALOG_READ = "catalog:read"
    """Reading the organisational catalogue — role dimensions and default rates (F-03, SC-2-01).

    A new permission rather than a widened `PROJECT_READ` (ADR-0005, addendum 2026-09-19 "pierwszy
    zbiór danych bez zasięgu projektu", point 2): a catalogue row belongs to no project, so a
    permission whose *name* says "project" opening a table that has no project would be exactly
    the drift between name and mechanism the SC-1-08 addendum had just closed. It also carries no
    scope — unlike `PROJECT_READ`, which is paired with the `project_access` filter, there is no
    per-caller predicate on the catalogue at all, so holding this permission means seeing every
    catalogue row (same addendum, point 1: that absence is a decision, not an oversight).

    It covers the selling rate too. The *cost* rate is gated separately by `PERSONNEL_COSTS_READ`,
    as a removed field and not as a refused row (same addendum, points 3 and 5)."""

    CATALOG_WRITE = "catalog:write"
    """Writing the organisational catalogue — new dimension entries and new rate windows.

    Split from `CATALOG_READ` because NF-10 puts the two with different people: the catalogue is
    maintained by an organisation administrator and read by everyone who plans staffing. Each of
    the two has its own mandatory refusal test (ADR-0005, "Konsekwencje")."""

    STAFFING_READ = "staffing:read"
    """Reading a scenario's staffing positions and their monthly hours (F-04, SC-3-01).

    A new permission, but for a different reason than `CATALOG_*` (ADR-0005, addendum 2026-09-19
    "pozycje obsady", point 2): the catalogue argument was "a permission whose name says *project*
    must not open a table that has no project", and a staffing position *does* belong to a project.
    What applies here is the action-granularity argument of the 2026-09-18 addendum: planning
    staffing is routinely the right of a different person than editing the project header, and a
    permission once merged into one "write to the project" cannot be narrowed later without breaking
    callers.

    Unlike `CATALOG_READ`, this permission carries **no** authority over which rows: scope stays the
    `project_access` filter, inherited through `scenario_id → scenarios.project_id`
    (`app.data.staffing`). Holding it and holding access to no project means seeing nothing."""

    STAFFING_WRITE = "staffing:write"
    """Creating a staffing position and editing a month of its allocation (F-04, SC-3-01).

    Split from `STAFFING_READ` for the same reason `CATALOG_WRITE` is split from `CATALOG_READ`, and
    each of the two has its own mandatory refusal test (ADR-0005, "Konsekwencje").

    **The known widening it inherits** (ADR-0005, addendum 2026-09-19, point 6): a write endpoint
    that answers with the full representation of the row it wrote is in practice a read permission
    for that row — the same gap as `PROJECT_EDIT`/`COPY`/`ARCHIVE`, one table over. It stays latent
    while every caller is the one placeholder identity that holds everything; the closing condition
    is the authentication ADR, unchanged."""

    COMMERCIAL_READ = "commercial:read"
    """Reading a scenario's commercial rule **and the revenue derived from it** (F-06, SC-4-01).

    New, from the action-granularity argument (ADR-0005, addendum 2026-09-23 SC-4-01, point 2):
    commercial terms are routinely set by a different person than the one planning staffing. Like
    `STAFFING_READ` it carries no authority over *which* rows — scope stays the `project_access`
    filter, inherited through `scenario_id → scenarios.project_id`.

    **Not conjoined with `PERSONNEL_COSTS_READ`** (point 3): a revenue and a selling rate are not
    what a person costs. What makes that true is that the response carries no cost field at all —
    proven by field-set equality (criterion K-11). **Named consequence** (point 4): through
    `assumptions_used` this permission shows the selling rates and catalogue windows a scenario uses
    and the aggregate of its billable hours, to a caller who may hold neither `CATALOG_READ` nor
    `STAFFING_READ`."""

    COMMERCIAL_WRITE = "commercial:write"
    """Creating a scenario's commercial rule (SC-4-01). Split from `COMMERCIAL_READ` like every
    other read/write pair, each with its own refusal test. Inherits the known widening of every
    write permission here (point 6): the write answers with the rule and its revenue, so in
    practice it reads them too."""

    SCENARIO_COPY = "scenario:copy"
    """Duplicating a scenario into its own project (F-09 pt.1, AC-02; SC-6-01, Issue #11, gate 1
    decision 2) — its own permission, distinct from `PROJECT_COPY`. The two copy a different unit
    (one scenario vs. a whole project with every one of its scenarios) and are plausibly different
    people's rights, the same action-granularity argument that splits `PROJECT_EDIT`, `PROJECT_COPY`
    and `PROJECT_ARCHIVE` into three permissions rather than one (ADR-0005, addendum 2026-09-18,
    point 1). Carries no authority over *which* scenarios: scope stays the `project_access` filter,
    inherited through `scenario_id → scenarios.project_id` exactly as `STAFFING_WRITE` and
    `COMMERCIAL_WRITE` are — the duplicate's target project is always the source's own, so there is
    no second project for scope to be about. Inherits the known widening of every write permission
    here (point 6 above): the endpoint answers with the duplicate's own representation, so in
    practice this also reads it."""


@dataclass(frozen=True)
class CallerIdentity:
    """Who is calling, and what they are allowed to do — for the duration of one request."""

    user_id: str
    permissions: frozenset[Permission] = field(default_factory=frozenset)

    def has(self, permission: Permission) -> bool:
        return permission in self.permissions
