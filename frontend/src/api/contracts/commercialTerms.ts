// API response shapes live here and only here — see agents/developer-frontend.md,
// "API response shapes are modeled in exactly one contracts layer".
//
// Mirrors backend/app/api/schemas/commercial_terms.py (SC-4-01), consumed by SC-4-06. Three
// properties of that schema are part of the contract and must not be re-interpreted in a component:
//
//   * **No field carries a cost** — not `default_cost_rate`, not a cost, profit or margin (ADR-0005,
//     addendum 2026-09-23 SC-4-01, point 3). Nothing here may declare one: a cost field added to this
//     layer would be a client re-guessing a field the server deliberately never sends.
//   * **Money crosses the boundary as a fixed-point string** (ADR-0002). `revenue.amount` is such a
//     string when `state` is `"calculated"` and the literal `"n/a"` otherwise — never `0`, never
//     `null`. It is formatted by `lib/money.ts` and nothing else.
//   * **`model_type` is closed on the request and open on the response.** The server creates only
//     what it can price (`"time_and_material"`), but a row of a later model can exist while this code
//     runs (ADR-0001, mixed-version window). Such a rule is the named state
//     `unsupported_model_type`, not a payload this client cannot read.

import type { ScenarioStatus } from "./projects";

/** The one model the request side accepts today (`ModelType` in the backend schema). */
export const TIME_AND_MATERIAL = "time_and_material";

/** What a `POST …/commercial-terms` sends. Closed, like the backend's `extra="forbid"` model. */
export interface CommercialTermsCreateRequest {
  model_type: typeof TIME_AND_MATERIAL;
}

/** Every `revenue.state` the backend can emit — exactly the backend's `RevenueState` literal. */
export const REVENUE_STATES = [
  "calculated",
  "no_commercial_terms",
  "incomplete_commercial_terms",
  "unsupported_model_type",
  "no_rate",
  "currency_mismatch",
  "no_revenue_currency",
] as const;

export type RevenueState = (typeof REVENUE_STATES)[number];

/** The six states that withhold a revenue (ADR-0003, point 9) — every state but `"calculated"`. */
export type WithheldRevenueState = Exclude<RevenueState, "calculated">;

/** Where the rates came from: the live catalogue for a draft, the approval's frozen windows for an
 * approved scenario (AC-04). */
export const RATE_SOURCES = ["live_catalog", "approved_snapshot"] as const;
export type RateSource = (typeof RATE_SOURCES)[number];

/** The backend's `NOT_APPLICABLE` sentinel, as `revenue.amount` carries it for a withheld state. */
export const REVENUE_NOT_APPLICABLE = "n/a";

export interface CommercialTermsRead {
  id: string;
  /** Open on purpose (`StoredModelType`): whatever the stored row says. */
  model_type: string;
  /** ADR-0007's marker. Carried, not used: this task has no edit path (ADR-0003, point 2). */
  updated_at: string;
}

export interface RateWindowRead {
  source_rate_id: string;
  /** ISO-8601 calendar date. */
  effective_from: string;
  /** Inclusive; `null` when open-ended (ADR-0008, point 3). */
  effective_to: string | null;
  /** Fixed-point decimal string — the selling rate only, never a cost. */
  default_selling_rate: string;
  currency: string;
}

export interface UnresolvedMonthRead {
  /** Identifies a staffing position. Never rendered: resolving it to a name needs `STAFFING_READ`
   * as well, which this task does not have (gate 1, D-1). */
  position_id: string;
  /** ISO-8601 calendar date of the month's first day, e.g. "2026-03-01". */
  period_month: string;
}

export interface RevenueAssumptionsRead {
  model_type: string | null;
  hours_source: "billable_hours";
  vendor_axis: "internal";
  rate_source: RateSource;
  rate_windows: RateWindowRead[];
  unresolved_months: UnresolvedMonthRead[];
  currencies: string[];
}

/**
 * A revenue the server stated. The pairing of `state` with `amount`/`currency` is the backend's
 * prose contract ("a fixed-point string when `state` is `calculated`, `n/a` otherwise"), made a type
 * here and checked in `api/client.ts` (`isRevenueShape`).
 */
export interface CalculatedRevenueRead {
  state: "calculated";
  /** Fixed-point decimal string — possibly `"0.00"`, which is a legal, computed zero. */
  amount: string;
  /** The revenue's own currency — never substituted by the project's reporting currency. */
  currency: string;
  assumptions_used: RevenueAssumptionsRead;
}

/** A revenue the server withheld, with the named state saying why (ADR-0003, point 9). */
export interface WithheldRevenueRead {
  state: WithheldRevenueState;
  amount: typeof REVENUE_NOT_APPLICABLE;
  currency: string | null;
  assumptions_used: RevenueAssumptionsRead;
}

export type RevenueRead = CalculatedRevenueRead | WithheldRevenueRead;

/** `GET`/`POST /projects/{project_id}/scenarios/{scenario_id}/commercial-terms`. */
export interface ScenarioCommercialTerms {
  scenario_id: string;
  /** The same two labels the project list carries (`ScenarioStatusLabel`). */
  scenario_status: ScenarioStatus;
  /** `null` when the scenario has no rule — and then `revenue.state` is `"no_commercial_terms"`. */
  commercial_terms: CommercialTermsRead | null;
  revenue: RevenueRead;
}
