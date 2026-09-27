// What the staffing-plan section says — every sentence in one place, the same convention as
// `scenarioResultsText.ts`/`commercialTermsText.ts`. Not a translation catalogue — no locale
// mechanism exists in this repository yet.
//
// Two rules hold for every sentence below, mirroring `scenarioResultsText.ts` (Issue #135):
//
//   * `STAFFING_REFUSED` is the one merged answer for both `403` and `404` on
//     `GET …/staffing-positions` (gate 1, Q3 = option A) — it never names which of the two it was.
//   * Each of `derived_capacity_state`'s and `absence_budget_state`'s own named, non-computable
//     states gets a sentence that belongs to no other state — no sentence below is shared across
//     two different states of the same field, and `"no_calendar"` on the two different fields is
//     worded differently even though it names the same underlying fact, so that neither line could
//     be mistaken for the other by its text alone (K-02, K-03).

import type { AbsenceBudgetState, CapacityState } from "../../api/contracts/staffing";

// --- Labels (K-01) -------------------------------------------------------------------------------

export const STAFFING_HEADING = "Staffing plan";

export const ROLE_LABEL = "Role:";
export const SENIORITY_LABEL = "Seniority:";
export const LOCATION_LABEL = "Location:";
export const ENGAGEMENT_TYPE_LABEL = "Engagement type:";
export const HEADCOUNT_LABEL = "Headcount:";
export const PERIOD_LABEL = "Period:";

export const AVAILABILITY_HOURS_LABEL = "Availability:";
export const PLANNED_ALLOCATION_HOURS_LABEL = "Planned allocation:";
export const BILLABLE_HOURS_LABEL = "Billable:";
export const DERIVED_CAPACITY_LABEL = "Derived capacity:";
export const ABSENCE_BUDGET_LABEL = "Leave budget:";

export const ABSENCES_HEADING = "Planned absences:";
export const ABSENCES_EMPTY = "No planned absences.";
export const ALLOCATIONS_EMPTY = "No months planned yet.";

// --- Reading the position list (K-05, K-06) ------------------------------------------------------

export const STAFFING_LOADING = "Loading staffing plan.";

/** `403` and `404` alike (Issue #135, K-05 — mirrors `RESULTS_REFUSED` of SC-7-02): the caller gets
 * no signal telling "not yours to see" from "does not exist" apart, for either status. */
export const STAFFING_REFUSED = "Staffing plan not shown.";

export const STAFFING_TIMED_OUT =
  "Staffing plan could not be loaded — the server did not answer in time.";

/** A `2xx` whose body is not the contract (K-06: the shape check refused it before any render). */
export const STAFFING_UNREADABLE =
  "Staffing plan could not be loaded — the server's answer was not in a form this screen can read.";

export const STAFFING_FAILED = "Staffing plan could not be loaded — the server failed to answer.";

/** `200 {positions: []}` — a statement from the server, never confused with a read failure
 * (K-05's contrast). */
export const STAFFING_EMPTY = "No staffing positions planned for this scenario yet.";

// --- Resolving the five catalogue identifiers (K-04) ---------------------------------------------

export const ROLE_NAME_LOADING = "Loading role…";
export const SENIORITY_NAME_LOADING = "Loading seniority…";
export const LOCATION_NAME_LOADING = "Loading location…";
export const ENGAGEMENT_TYPE_NAME_LOADING = "Loading engagement type…";
export const ABSENCE_TYPE_NAME_LOADING = "Loading absence type…";

/**
 * What every one of the five catalogue identifiers reads as when the catalogue read
 * (`CATALOG_READ`, independent of this section's own `STAFFING_READ`) failed for any reason — one
 * word, deliberately not distinguishing *why* the catalogue read failed (gate 1, Q6: "one merged
 * state, consistent with K-05"). Never the raw UUID (K-04's own mutation), and never a blank cell
 * or a row silently dropped — the rest of the row (headcount, hours, capacity/budget states) still
 * renders normally beside this.
 */
export const CATALOG_NAME_UNAVAILABLE = "Name unavailable";

/** The catalogue answered, but named no entry with this id — a gap between two reads (an entry
 * renamed or removed between the staffing read and the catalogue read), not a failure of either
 * read on its own. Distinct from `CATALOG_NAME_UNAVAILABLE`: this is "the catalogue was readable
 * and simply does not contain this id", not "the catalogue could not be read at all". */
export const CATALOG_NAME_UNKNOWN = "Unknown";

// --- The derived-capacity field's own named states (K-02) ----------------------------------------

export const DERIVED_CAPACITY_STATE_MESSAGES: Readonly<Record<Exclude<CapacityState, "resolved">, string>> =
  {
    no_calendar: "No capacity derived — this position's location has no working calendar.",
  };

// --- The leave-budget field's own four named states (K-03) ----------------------------------------

export const ABSENCE_BUDGET_STATE_MESSAGES: Readonly<
  Record<Exclude<AbsenceBudgetState, "resolved">, string>
> = {
  no_budget:
    "No leave budget applied — no budget has been entered for this pair, or every window has expired.",
  no_statutory_leave_type:
    "No leave budget applied — no absence type in the catalogue is marked as the statutory leave type.",
  // Deliberately worded differently from `DERIVED_CAPACITY_STATE_MESSAGES.no_calendar`, even though
  // both name the same underlying fact (no working calendar for the position's location) — neither
  // sentence is a substring of the other, so the two lines cannot be mistaken for one another by
  // their text alone, only by their (different) labels.
  no_calendar: "No leave budget applied — this position's location has no working calendar.",
};
