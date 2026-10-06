# ADR-0022 — Application roles and permission assignment

**Status:** Accepted (human approval 2026-10-05, Issue #253)

## Context

ADR-0018 establishes the OIDC caller identity and opaque `sub`; it expressly leaves role and
permission assignment to SC-1-14. ADR-0005 defines role, project scope, and personnel-cost visibility
as separate dimensions, but does not contain a complete role-to-permission mapping or grant
authority. Its 2026-10-01 routing addendum sends the listed authorization conditions here.

This ADR defines authorization policy only. OIDC runtime configuration, API enforcement, and UI
remain separate work. It does not claim that authentication or authorization is implemented.

## Decision

Human-approved Gate 1 boundaries (2026-10-05, Issue #253 comment 5994245635):

1. Use a separate role/permission ADR; ADR-0018 remains focused on caller identity.
2. Use dedicated organization-default permission(s), separate from catalog permissions and project
   access.
3. Use dedicated scenario-assumption read/write permissions, scoped to the caller's assigned
   projects.
4. Preserve the existing project and organization boundaries; project membership does not grant
   organization-default access, and a role does not bypass project assignment.
5. Keep scenario approval authority distinct from planning and draft editing.
6. Assign permissions independently; do not infer grants from broad `admin`, `author`, or
   `viewer` bundles. Central identity administrators grant permission-specific application roles.
   A separate project-access administrator manages `project_access` assignments through a future
   access-management workflow; central identity administrators appoint project-access
   administrators.
7. Define dedicated `ORGANIZATION_DEFAULTS_READ` and `ORGANIZATION_DEFAULTS_WRITE` permissions.
   Define dedicated `SCENARIO_ASSUMPTIONS_READ` and `SCENARIO_ASSUMPTIONS_WRITE` permissions;
   both are required to edit. A write permission does not imply read.
8. Assign scenario approval to a designated approval administrator, distinct from scenario authors.
9. Preserve `PERSONNEL_COSTS_READ` plus the project flag; keep all three B-01 combinations denied
   until each response path is independently gated and proven.
10. Preserve global `PEOPLE_READ` scope subject to ADR-0019 gates; require the relevant read
    `SCENARIO_READ` permission for `SCENARIO_COPY` response data; defer history-read scope to SC-8-02; and grant
    `COMMERCIAL_ADJUSTMENT_APPROVE` only to a designated approval administrator, separate from
    `COMMERCIAL_WRITE` and never to placeholder identity.

Retain ADR-0005's existing role vocabulary (`admin`, `author`, `viewer`) as descriptive labels,
not permission bundles. Project scope remains through `project_access`, and personnel-cost access
continues to require the independent `project_access.can_view_personnel_costs` flag. The human
approved the routed choices below on 2026-10-05 by accepting this draft's recommendations; this
policy does not claim that any grant workflow or enforcement is implemented.

## Routed conditions and existing boundaries

| Condition | Disposition |
|---|---|
| ADR-0004 scenario approval | Approval is a separate action from planning and draft editing. The role that may approve remains a human decision. Approved scenarios remain immutable. Audit history remains governed by its separate decision. |
| History-read access | Authentication alone grants no history access. Permission and scope are to be decided jointly with SC-8-02 or explicitly decided here before acceptance. |
| Placeholder identity | Preserve ADR-0018/ADR-0005: opt-in only, disabled by default, refused outside development/test. Do not widen `PLACEHOLDER_PERMISSIONS` or use it as the production role policy. |
| Project actions | Project permissions remain constrained by `project_access`. Assignment of each action permission and its response disclosure must be explicit; an action permission is not silently treated as `PROJECT_READ`. |
| Personnel-cost visibility | Preserve the accepted conjunction `PERSONNEL_COSTS_READ` and the target project's `can_view_personnel_costs`. Assignment/composition and the routed inference cases remain open below. |
| Organization defaults | Dedicated organization-level permission(s); not inherited from catalog or project access. Exact read/write granularity and grant authority remain open. |
| Scenario assumptions | Dedicated read and write permissions, both project-scoped. Their role mapping and whether write implies read remain open. Approved-scenario writes remain forbidden under ADR-0004. |
| Supplier visibility | Existing catalog permissions and the synthetic-only supplier-data restriction for development/test remain unchanged. Supplier-specific scope requires its separate dated access decision. |
| Named-person access | ADR-0019 and Q-4 continue to govern privacy and real-data admission. SC-1-14 decides who may receive `PEOPLE_READ`; its scope remains open below. |
| Scenario copy | `SCENARIO_COPY` remains distinct from `PROJECT_COPY`, and project scope is unchanged. Whether copy permission includes reading the returned copy remains open. |
| Commercial adjustment approval | Preserve the accepted separation of `COMMERCIAL_ADJUSTMENT_APPROVE` from `COMMERCIAL_WRITE`; it does not itself grant read access. Its role assignment and grant authority remain open. |
| B-01 cost inference | Explicitly decide all three routed combinations: catalog rates plus staffing inputs; addition of absence-budget inputs; and fully loaded costs including surcharge. The decision must not leave a role combination that reconstructs cost outside the selected personnel-cost control. |
| Cost-rate unit and derived states | Keep the ADR-0005 cost-field boundary; a rate unit or derived cost state does not bypass the personnel-cost controls. Role composition remains subject to the B-01 decision. |
| Personnel-cost positive path | ADR-0013 and ADR-0018's authentication implementation successor remain prerequisites. This ADR alone does not activate the path. |
| Headcount-dependent person/absence exposure | Privacy classification and exposure remain with ADR-0019/Q-4. This ADR allocates only any permission that the selected privacy control requires. |

## Human-approved routed choices and alternatives

| ID | Decision | Alternatives and consequences | Approved choice |
|---|---|---|
| Q1 | Map existing and new permissions to `admin`, `author`, and `viewer`. | A — role bundles by action category: simpler to administer, but can grant broad permission intersections. B — assign each action permission independently: narrower control, but more grant decisions and less meaning carried by the three role labels. | B, because explicit grants preserve least privilege and make each combination reviewable. |
| Q2 | Name the grant authority and assignment source for global roles, organization defaults, and project access. | A — central identity administrator grants roles while a separate authority manages project assignments: separates identity from project scope but divides operations. B — one designated organization/application authority manages both: simpler ownership but must still enforce project boundaries. No current user-management UI or general grant path establishes either actor. | A. Central identity administrators grant permission-specific roles and appoint project-access administrators; project-access administrators manage scoped `project_access` assignments through a future workflow. |
| Q3 | Choose organization-default read/write granularity and name who grants it. | A — one dedicated permission: simpler but couples viewing and editing. B — separate dedicated read and write permissions: separates duties but needs independent grants. Both retain organization-only scope and remain distinct from catalog permissions. | B, because view access should not automatically permit organization-wide changes. |
| Q4 | Decide whether scenario-assumption write implies read. | A — write includes reading the target and response: simpler for editors but widens disclosure. B — callers need both dedicated read and write permissions: narrower but requires both grants to edit. Both remain limited to assigned projects. | B, because a write permission should not grant extra read access implicitly. |
| Q5 | Assign scenario-approval authority. | A — `admin` only: stronger separation from planning but concentrates approvals. B — `admin` and `author`: more flexible but grants approvers planning/edit authority as well. Approval remains a distinct action permission under either option. | A, because approval is a separate authority from planning and draft editing. |
| Q6 | Resolve `PERSONNEL_COSTS_READ` composition and each B-01 combination: (1) `CATALOG_READ` plus `STAFFING_READ` can reconstruct base cost for a caller with global `PERSONNEL_COSTS_READ` but no project flag; (2) adding `STAFFING_READ` access to absence-budget hours and `CATALOG_READ` access to cost-generating rates extends that reconstruction; and (3) `CATALOG_READ` exposure of base rate plus surcharge and `STAFFING_READ` can reconstruct fully loaded cost. | A — retain the accepted global `PERSONNEL_COSTS_READ` plus project-flag conjunction and deny all three combinations until the exact response paths are independently gated and proven; this preserves the current control but keeps the combinations unavailable. B — let global `PERSONNEL_COSTS_READ` bypass the project flag for all three combinations; this enables broader use but deliberately crosses the existing project boundary and requires a dated ADR-0005 addendum. | A, because the accepted conjunction is the only established control; keep all three combinations denied until each authorization path is independently implemented and verified. |
| Q7 | Decide `PEOPLE_READ` scope and grant authority. | A — global: simpler, but every holder can read the register across projects. B — project-derived: narrower, but changes the previously accepted global choice and requires a dated ADR-0005 addendum. ADR-0019 privacy conditions apply either way. | A, because it preserves the existing decision; named-person data remains unavailable until ADR-0019 admission conditions are met. |
| Q8 | Decide whether `SCENARIO_COPY` grants read access to its returned copy. | A — copy permission includes that response: matches the current response shape but widens the action grant. B — require a separate read permission: narrower but adds a permission-composition requirement to callers. | B, because a copy action should not implicitly grant continuing read access. |
| Q9 | Decide history-read permission and scope here or defer to SC-8-02. | A — decide now: closes the authorization dependency earlier. B — decide with SC-8-02: preserves feature-specific review but leaves that condition open until the history-read contract is defined. | B, because the history feature should set its read scope with its own user-visible contract. |
| Q10 | Assign `COMMERCIAL_ADJUSTMENT_APPROVE` and name its grant authority. | A — grant it only to `admin`, through the central identity administrator: separates decisions from commercial editing but concentrates approval. B — grant it to `admin` and `author`, through the commercial project owner: increases flexibility but overlaps editing and approval. The accepted rule that this permission grants no read access remains fixed in either case. | A, because approval remains separate from `COMMERCIAL_WRITE` and placeholder callers must not receive this permission. |

## Consequences

- Project permissions cannot cross `project_access`; organization-default access is separate from
  project and catalog permissions.
- Scenario-assumption permissions apply only within assigned projects and cannot write approved
  scenarios.
- Approval and commercial-adjustment approval remain separate from draft or commercial editing.
- Existing placeholder, supplier-data, personnel-cost, and named-person boundaries remain in force.
- Any resolution changing an accepted ADR-0005 rule requires a dated addendum before implementation.

## Controls

| Control | Acceptance criterion |
|---|---|
| A22-1 | The accepted ADR names the permission-to-role mapping and grant authority for project, organization-default, scenario-assumption, and approval permissions. |
| A22-2 | Organization-default permissions remain separate from catalog and project access; scenario-assumption permissions remain scoped to assigned projects. |
| A22-3 | Approval authority is distinct from planning and draft editing, and approved scenarios remain immutable. |
| A22-4 | The accepted personnel-cost policy explicitly disposes of all three B-01 combinations without silently bypassing the selected cost control. |
| A22-5 | The accepted ADR explicitly disposes of history-read scope, `PEOPLE_READ`, `SCENARIO_COPY` response access, and `COMMERCIAL_ADJUSTMENT_APPROVE` assignment, or names the approved successor decision for each. |
| A22-6 | Placeholder and supplier-data restrictions remain consistent with ADR-0018, ADR-0005, and ADR-0019. |

## Related requirements and decisions

F-13, NF-04, NF-11, F-12, AC-06; ADR-0004, ADR-0005 (including the 2026-10-01 routing
addendum), ADR-0018, ADR-0019; Issues #150, #235, and #253.
### Addendum 2026-10-05 (Issue #239, SC-8-02 — history-read access)

**Status:** Accepted by the human on 2026-10-05 (Gate 1 reply B).

Reading scenario history requires a dedicated history-read permission in addition to `PROJECT_READ`. The read remains scoped to the caller’s assigned project through `project_access`; a history permission does not grant access to unassigned projects, and an out-of-scope project remains indistinguishable from a missing project. Authentication or project assignment alone does not grant history access.

History access does not grant individual personnel-cost visibility. Any such values remain subject to both `PERSONNEL_COSTS_READ` and the target project’s `project_access.can_view_personnel_costs` gate.

This resolves ADR-0022 Q9 for SC-8-02. The synthetic-only, unverified-placeholder boundary and prerequisites for real personal data remain those in ADR-0019’s SC-8-02 addendum. This decision does not authorize additional event types, change snapshot immutability, or permit version comparison.
