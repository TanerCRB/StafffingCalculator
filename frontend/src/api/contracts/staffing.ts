// API response shape for
// `GET /projects/{project_id}/scenarios/{scenario_id}/staffing-positions` — see
// agents/developer-frontend.md, "API response shapes are modeled in exactly one contracts layer".
//
// Mirrors backend/app/api/schemas/staffing.py (SC-3-01/SC-3-02/SC-3-03), consumed by SC-3-04
// (Issue #135, read-only screen). Only the fields this screen renders are declared here — the same
// convention `contracts/scenarioResults.ts` follows for `assumptions_used`: a field this client
// never reads is a field this contract does not need to promise a shape for. Concretely, not
// declared and never read: `derived_capacity_source`/`absence_budget_source` (gate 1, Q5 = "no" —
// number + named state only in this MVP; a future task rendering the full source breakdown adds its
// own fields here).
//
// **No field carries a rate, a cost or a currency** (ADR-0005, addendum 2026-09-19 SC-3-01,
// point 5) — `StaffingPositionRead` is a dimension tuple, a headcount, a period and hours, nothing
// this client could gate even if it wanted to. `cost_basis`/`fixed_amount`/`fixed_amount_currency`
// exist only on the write path and are not part of this read's response at all — out of scope by
// construction, not by a check this file performs.
//
// **Hours cross the boundary as fixed-point decimal strings, never JSON floats** (ADR-0002,
// addendum 2026-09-26 SC-3-04) — formatted by `lib/hours.ts` and nothing else. `derived_capacity_hours`/
// `absence_budget_hours` additionally carry the sentinel `"n/a"` when their own named state says the
// figure was not computed — read the two fields together, the same discipline
// `contracts/scenarioResults.ts` already documents for its gated fields.
//
// **`updated_at` is an opaque string, never parsed** (SC-1-02: `new Date()`/`.toISOString()` would
// round-trip through millisecond precision where the backend compares microseconds bit-for-bit).
// This screen does not write, so nothing here consumes the token for a `PATCH` — it round-trips
// only as far as this contract, unread past its own type.

/** The backend's `NOT_APPLICABLE` sentinel (`app.core.money`), exactly as the two derived hours
 * fields below carry it — the same literal `contracts/scenarioResults.ts` names
 * `RESULTS_NOT_APPLICABLE` for its own fields. Each domain names its own copy of this literal
 * rather than sharing one constant across domains (the established convention in this codebase). */
export const HOURS_NOT_APPLICABLE = "n/a";

/** Every `derived_capacity_state` the backend can emit — exactly the backend's `CapacityState`
 * literal (`app.domain.capacity`). */
export const CAPACITY_STATES = ["resolved", "no_calendar"] as const;

export type CapacityState = (typeof CAPACITY_STATES)[number];

/** Every `absence_budget_state` the backend can emit — exactly the backend's `AbsenceBudgetState`
 * literal (`app.domain.absence_budget`). Four states, each with its own reason (see the backend
 * schema's docstring); none of them may be reused as another's wording, and none may be rendered
 * as the generic `"n/a"` label — the screen's own named message for each is in
 * `staffingPlanText.ts`. */
export const ABSENCE_BUDGET_STATES = [
  "resolved",
  "no_budget",
  "no_statutory_leave_type",
  "no_calendar",
] as const;

export type AbsenceBudgetState = (typeof ABSENCE_BUDGET_STATES)[number];

/**
 * One month of a position's grid, on the way out (`StaffingAllocation`, F-04/F-05).
 *
 * `derived_capacity_hours`/`absence_budget_hours` are `"n/a"` **exactly when** their own state is
 * not `"resolved"` — never `0.00` for that case (a zero is a number every later sum would add up,
 * and it would be indistinguishable from a real capacity/budget of zero). The pairing is checked at
 * the network boundary (`api/client.ts`'s `isStaffingAllocationShape`), the same discipline
 * `isPersonnelCostSourceShape` already applies to its own gated fields — a payload that breaks the
 * pairing ends in this screen's own `unreadable` state, never in a render-phase throw.
 */
export interface StaffingAllocation {
  id: string;
  period_month: string;
  availability_hours: string;
  planned_allocation_hours: string;
  billable_hours: string;

  derived_capacity_hours: string | typeof HOURS_NOT_APPLICABLE;
  derived_capacity_state: CapacityState;

  absence_budget_hours: string | typeof HOURS_NOT_APPLICABLE;
  absence_budget_state: AbsenceBudgetState;
}

/**
 * One planned absence of one position, on the way out (`StaffingAbsence`, F-05, SC-3-02).
 *
 * **Exactly these four fields, and no others** — there is no person, no note, no justification
 * (ADR-0005, addendum 2026-09-22 SC-3-02, point 6). A component adding a fifth field here would be
 * inventing data the backend deliberately does not carry, not reading a gap in the contract.
 */
export interface StaffingAbsence {
  id: string;
  absence_type_id: string;
  start_date: string;
  end_date: string;
}

/**
 * One staffing position with its whole monthly grid and its absences (`StaffingPositionRead`).
 *
 * `end_date` is `null` for an open-ended position — the same "named absence, never a date"
 * convention `lib/dates.ts`'s `OPEN_ENDED_PERIOD` renders for a catalogue rate's window.
 * `updated_at` is typed `string` and nothing here ever wraps it in `Date` (SC-1-02).
 */
export interface StaffingPositionRead {
  id: string;

  role_id: string;
  seniority_id: string;
  location_id: string;
  engagement_type_id: string;

  headcount: number;
  start_date: string;
  end_date: string | null;

  /** Opaque concurrency marker (ADR-0007) — carried because it is on every representation of the
   * position, never parsed and never sent anywhere by this read-only screen (Out of scope: no
   * write path is reachable from here). */
  updated_at: string;

  allocations: StaffingAllocation[];
  absences: StaffingAbsence[];
}

/** An object, not a bare array — the same contract room `ProjectListResponse`/`CatalogRateList`
 * keep, for filtering or pagination later without breaking the contract. */
export interface StaffingPositionList {
  positions: StaffingPositionRead[];
}

export interface StaffingPositionDetailsEditRequest {
  updated_at: string;
  role_id: string;
  seniority_id: string;
  location_id: string;
  engagement_type_id: string;
  headcount: number;
  start_date: string;
  end_date: string | null;
}

export interface StaffingPositionCreateRequest extends Omit<StaffingPositionDetailsEditRequest, "updated_at"> {
  allocations: StaffingAllocationCreateRequest[];
}

export interface StaffingAllocationCreateRequest {
  updated_at: string;
  period_month: string;
  availability_hours: string;
  planned_allocation_hours: string;
  billable_hours: string;
}

export interface StaffingAllocationEditRequest {
  updated_at: string;
  availability_hours?: string;
  planned_allocation_hours?: string;
  billable_hours?: string;
}
