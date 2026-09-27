// API response shape for `GET /projects/{project_id}/scenarios/{scenario_id}/results` — see
// agents/developer-frontend.md, "API response shapes are modeled in exactly one contracts layer".
//
// Mirrors backend/app/api/schemas/scenario_results.py (SC-7-01), consumed by SC-7-02 (Issue #94).
//
// `revenue` is exactly `RevenueRead` from `./commercialTerms` (SC-4-01) — reused, not re-declared
// (ADR-0004 "fits", confirmed at this task's gate 1). `personnel_cost` and `additional_cost` below
// model only the subset of `PersonnelCostRead` (backend/app/api/schemas/personnel_cost.py) and
// `AdditionalCostTotalRead` (backend/app/api/schemas/additional_cost.py) this screen reads — each
// source's own `state`, `amount`, `currency`. Neither's `assumptions_used` (rate windows, unresolved
// months, paid-absence detail) is declared here: this screen does not render it (Issue #94, out of
// scope — monthly/period breakdown), and a field this client never reads is a field this contract
// does not need to promise a shape for.
//
// **Four fields are gated as one group, never split** (ADR-0005, aneks 2026-09-24): `included_cost`,
// `profit`, `margin`, `markup` are `null` when the caller may not see personnel costs of this
// project — never inferred from `personnel_cost.state`, which is never gated further than its own
// endpoint already gates it (SC-5-01/SC-5-06). `personnel_cost.amount`/`currency` carry that very
// same gate independently of `personnel_cost.state` (the backend docstring: "`null` when the caller
// may not see personnel costs… " applies regardless of `state`) — read the two fields separately,
// never one inferred from the other.
//
// `null` (gate closed) and `"n/a"` (the backend's `NOT_APPLICABLE` sentinel, not computable) are
// different values on the wire and must render as different things — never collapsed into one.
//
// Money and ratios cross the boundary as fixed-point decimal strings, never JSON floats (ADR-0002).

import type { RevenueRead } from "./commercialTerms";

/** Every `personnel_cost.state` the backend can emit — exactly the backend's `PersonnelCostState`
 * literal. Never gated (only `amount`/`currency`/`assumptions_used` are). */
export const PERSONNEL_COST_STATES = [
  "calculated",
  "no_cost_rate",
  "currency_mismatch",
  "no_cost_currency",
] as const;

export type PersonnelCostState = (typeof PERSONNEL_COST_STATES)[number];

/** Every `additional_cost.state` the backend can emit — exactly the backend's `AdditionalCostState`
 * literal. Never gated (ADR-0014, point 11). */
export const ADDITIONAL_COST_STATES = ["calculated", "currency_mismatch", "no_cost_currency"] as const;

export type AdditionalCostState = (typeof ADDITIONAL_COST_STATES)[number];

/** Every `profitability_state` the backend can emit — exactly the backend's `ProfitabilityState`
 * literal (SC-4-03, R-01). Why the four aggregate fields are `"n/a"`, or `"calculated"` when they are
 * not withheld for a computational reason. **Never gated**: it states no figure. */
export const PROFITABILITY_STATES = ["calculated", "not_applicable", "currency_mismatch"] as const;

export type ProfitabilityState = (typeof PROFITABILITY_STATES)[number];

/** The backend's `NOT_APPLICABLE` sentinel, exactly as `RevenueRead`'s `REVENUE_NOT_APPLICABLE`
 * carries it (`backend/app/core/money.py`). */
export const RESULTS_NOT_APPLICABLE = "n/a";

/**
 * The base personnel cost, as much of `PersonnelCostRead` as this screen reads.
 *
 * `amount`/`currency` are `null` under the personnel-cost gate — independently of `state`, which is
 * never gated: a `"calculated"` state can sit beside a `null` amount (gate closed), and a named
 * non-computable state can sit beside a gate that is open (`amount: "n/a"`). Both must be read, and
 * read separately.
 */
export interface PersonnelCostSource {
  state: PersonnelCostState;
  amount: string | typeof RESULTS_NOT_APPLICABLE | null;
  currency: string | null;
}

/** The scenario's additional-cost sum, as much of `AdditionalCostTotalRead` as this screen reads.
 * Never gated — `amount` is a decimal string or `"n/a"`, never `null` (ADR-0014, point 11). */
export interface AdditionalCostSource {
  state: AdditionalCostState;
  amount: string | typeof RESULTS_NOT_APPLICABLE;
  currency: string | null;
}

/** One of the four fields gated as a group (ADR-0005, aneks 2026-09-24): a fixed-point decimal
 * string when computable and visible, the literal `"n/a"` when open but not computable, `null` when
 * the caller may not see personnel costs of this project. */
export type GatedResultField = string | typeof RESULTS_NOT_APPLICABLE | null;

/** `GET /projects/{project_id}/scenarios/{scenario_id}/results` (SC-7-01). */
export interface ScenarioResults {
  scenario_id: string;
  scenario_status: "Draft" | "Approved";
  revenue: RevenueRead;
  personnel_cost: PersonnelCostSource;
  /** Never gated (ADR-0014, point 11; ADR-0005, aneks 2026-09-23 SC-5-05) — visible exactly as its
   * own endpoint shows it, even when the four aggregate fields below are withheld. */
  additional_cost: AdditionalCostSource;
  /** `base` personnel cost + paid-absence cost + additional cost, all three already stated. */
  included_cost: GatedResultField;
  /** `revenue − included_cost`. */
  profit: GatedResultField;
  /** `profit / revenue × 100` — `"n/a"` when `revenue` is exactly `0.00`. */
  margin: GatedResultField;
  /** `profit / included_cost × 100` — `"n/a"` when `included_cost` is exactly `0.00`. */
  markup: GatedResultField;
  /**
   * Why the four fields above are `"n/a"` (SC-4-03, R-01; ADR-0003 addendum SC-4-07, point 5c). The
   * pairing is one-directional and checked in `api/client.ts`: `not_applicable`/`currency_mismatch`
   * exclude a number in any of the four, while `calculated` does not force one — a `margin` of
   * `"n/a"` beside `calculated` (AC-05, a zero revenue) is a valid payload.
   */
  profitability_state: ProfitabilityState;
}

/**
 * `GET /projects/{project_id}/scenarios/compare` (SC-6-02) — the same `ScenarioResults` shape
 * above, once per named `scenario_id`, in request order (SC-7-04, Issue #108).
 *
 * A set of independent rows, never an aggregate: nothing here sums, nets or averages a field
 * across the compared scenarios (mirrors `backend/app/api/schemas/scenario_results.py`'s own
 * docstring for this type). All-or-nothing on the wire — this shape is only ever returned once
 * every named `scenario_id` resolved for the caller; a `404`/`409` is the whole response, never a
 * row-shaped marker inside `results` (K-04).
 */
export interface ScenarioResultsComparison {
  results: ScenarioResults[];
}
