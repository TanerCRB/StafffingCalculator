// API response shapes live here and only here — see agents/developer-frontend.md,
// "API response shapes are modeled in exactly one contracts layer".
//
// Mirrors backend/app/api/schemas/commercial_terms.py (SC-4-01, SC-4-03, SC-4-04), consumed by
// SC-4-06 and — for the Story Points and Outcome-based models — by SC-4-07. Three
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

/** Time & Material (`ModelType` in the backend schema). */
export const TIME_AND_MATERIAL = "time_and_material";

/** Fixed Price (SC-4-02). */
export const FIXED_PRICE = "fixed_price";

/** What a Time & Material `POST …/commercial-terms` sends. */
export interface TimeAndMaterialTermsCreateRequest {
  model_type: typeof TIME_AND_MATERIAL;
}

export interface FixedPriceTermsCreateRequest {
  model_type: typeof FIXED_PRICE;
  agreed_price: string;
  currency: string;
}

export type CommercialTermsCreateRequest =
  | TimeAndMaterialTermsCreateRequest
  | FixedPriceTermsCreateRequest;

/** Fixed Price edits replace the agreed price and carry the rule's concurrency marker. */
export interface FixedPriceTermsEditRequest {
  updated_at: string;
  agreed_price: string;
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
 * approved scenario (AC-04). Time & Material only — the two values whose meaning depends on the
 * scenario's status. */
export const RATE_SOURCES = ["live_catalog", "approved_snapshot"] as const;
export type RateSource = (typeof RATE_SOURCES)[number];

// --- The models this client can render, and the sources each of them names (SC-4-07) ------------
//
// ADR-0003, addendum 2026-09-25 SC-4-07, points 3-5: the render is chosen by
// `assumptions_used.model_type`, never by `rate_source`/`hours_source`/`vendor_axis`, and the three
// source fields are checked *in a pair with* that `model_type` (`api/client.ts`,
// `isRevenueAssumptionsShape`) — never as a per-field union of values, which would let a
// Time & Material revenue through with `not_applicable`. Each model adds only its own values; no
// Fixed Price value is declared here (#113).

/** Story Points (F-06.4, SC-4-04). */
export const STORY_POINTS = "story_points";
/** Outcome-based (F-06.3, SC-4-03). */
export const OUTCOME_BASED = "outcome_based";

/** The backend's `SourceNotApplicable` — a source the model's calculation does not read. */
export const SOURCE_NOT_APPLICABLE = "not_applicable";
/** Story Points' `rate_source`: the rule's own price per point, never the catalogue. */
export const STORY_POINTS_RATE_SOURCE = "story_points_terms";
/** Fixed Price revenue comes from its own agreed-price row. */
export const FIXED_PRICE_RATE_SOURCE = "fixed_price_terms";

/** Every `assumptions_used.model_type` this client can read besides `null` (the scenario without a
 * rule). A stored model this version does not know is readable only as the named
 * `unsupported_model_type` state (the response side of `model_type` is open — SC-4-01, R-02), and
 * then with the catalogue sources the backend's dispatcher gives it. */
export const REVENUE_MODEL_TYPES = [TIME_AND_MATERIAL, STORY_POINTS, OUTCOME_BASED, FIXED_PRICE] as const;

/** The backend's `NOT_APPLICABLE` sentinel, as `revenue.amount` carries it for a withheld state. */
export const REVENUE_NOT_APPLICABLE = "n/a";

/** The four fixed outcome categories (ADR-0003, addendum 2026-09-25 SC-4-03, point 3), in the
 * backend's canonical order. A category is always identified by this word, never by its position. */
export const OUTCOME_CATEGORIES = ["not_achieved", "partial", "achieved", "exceeded"] as const;
export type OutcomeCategory = (typeof OUTCOME_CATEGORIES)[number];

/** One outcome category as the rule stores it — `null` is "not given", never `0`. */
export interface OutcomeCategoryRead {
  /** Fixed-point decimal string (`NUMERIC(14,4)`), or `null`. */
  units: string | null;
  /** Percentage as a fixed-point decimal string (`NUMERIC(5,2)`), or `null`. */
  probability: string | null;
}

/** An Outcome-based rule's parameters as stored (`OutcomeTermsRead`, SC-4-03 R-04). Amounts are
 * `NUMERIC(14,4)` strings copied from the row; an optional component that is absent is `null`,
 * never `0` (addendum SC-4-03, point 2). No cost field. */
export interface OutcomeTermsRead {
  /** The rule's own currency — the currency of every amount below. */
  currency: string;
  fixed_fee: string;
  success_bonus: string | null;
  unit_rate: string | null;
  revenue_min: string | null;
  revenue_max: string | null;
  categories: Record<OutcomeCategory, OutcomeCategoryRead>;
}

export interface CommercialTermsRead {
  id: string;
  /** Open on purpose (`StoredModelType`): whatever the stored row says. */
  model_type: string;
  /** ADR-0007's marker. Carried, not used: this task has no edit path (ADR-0003, point 2). */
  updated_at: string;
  /** The Outcome-based rule's parameters — `null` for every other model, and for an Outcome-based
   * rule without its details row (then `revenue.state` is `incomplete_commercial_terms`). */
  outcome_terms: OutcomeTermsRead | null;
  /** Fixed Price only; null with an incomplete details row. */
  agreed_price?: string | null;
  /** Fixed Price only; null with an incomplete details row. */
  currency?: string | null;
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

interface RevenueAssumptionsCommon {
  /** Empty for a model that reads no rate window (Story Points, Outcome-based). */
  rate_windows: RateWindowRead[];
  /** Empty for a model that reads no staffed month. */
  unresolved_months: UnresolvedMonthRead[];
  currencies: string[];
}

/** Time & Material — and the scenario without a rule (`model_type: null`), and a stored model this
 * version cannot price (`unsupported_model_type`): the catalogue sources the dispatcher names. */
export interface CatalogRevenueAssumptionsRead extends RevenueAssumptionsCommon {
  model_type: string | null;
  hours_source: "billable_hours";
  vendor_axis: "internal";
  rate_source: RateSource;
}

/** Story Points (SC-4-04): no hour, no vendor axis, the rule's own price. */
export interface StoryPointsRevenueAssumptionsRead extends RevenueAssumptionsCommon {
  model_type: typeof STORY_POINTS;
  hours_source: typeof SOURCE_NOT_APPLICABLE;
  vendor_axis: typeof SOURCE_NOT_APPLICABLE;
  rate_source: typeof STORY_POINTS_RATE_SOURCE;
}

/** Outcome-based (SC-4-03, point 8): none of the three sources is read. */
export interface OutcomeRevenueAssumptionsRead extends RevenueAssumptionsCommon {
  model_type: typeof OUTCOME_BASED;
  hours_source: typeof SOURCE_NOT_APPLICABLE;
  vendor_axis: typeof SOURCE_NOT_APPLICABLE;
  rate_source: typeof SOURCE_NOT_APPLICABLE;
}

/** Fixed Price (SC-4-02): revenue reads the agreed-price row, not hours or a rate catalogue. */
export interface FixedPriceRevenueAssumptionsRead extends RevenueAssumptionsCommon {
  model_type: typeof FIXED_PRICE;
  hours_source: typeof SOURCE_NOT_APPLICABLE;
  vendor_axis: typeof SOURCE_NOT_APPLICABLE;
  rate_source: typeof FIXED_PRICE_RATE_SOURCE;
}

export type RevenueAssumptionsRead =
  | CatalogRevenueAssumptionsRead
  | StoryPointsRevenueAssumptionsRead
  | OutcomeRevenueAssumptionsRead
  | FixedPriceRevenueAssumptionsRead;

/** Which rendering a revenue gets — chosen by `assumptions_used.model_type` and by
 * nothing else (ADR-0003, addendum SC-4-07, point 3), except that the named `unsupported_model_type`
 * state comes first: a backend instance that cannot price the model says so with the dispatcher's
 * catalogue sources, whatever the model's word (SC-4-07, verification R-01). `"catalog"` covers
 * Time & Material, the scenario without a rule, and every unsupported model. */
export type RevenueModelKind = "catalog" | typeof STORY_POINTS | typeof OUTCOME_BASED | typeof FIXED_PRICE;

export function revenueModelKind(revenue: RevenueRead): RevenueModelKind {
  if (revenue.state === "unsupported_model_type") {
    return "catalog";
  }
  if (revenue.assumptions_used.model_type === STORY_POINTS) {
    return STORY_POINTS;
  }
  if (revenue.assumptions_used.model_type === OUTCOME_BASED) {
    return OUTCOME_BASED;
  }
  if (revenue.assumptions_used.model_type === FIXED_PRICE) {
    return FIXED_PRICE;
  }
  return "catalog";
}

/** The catalogue sources of a revenue rendered as `"catalog"`, or `null` — sound because
 * `isRevenueSourcePairing` admits catalogue sources exactly for the unsupported state and for a
 * model other than Story Points and Outcome-based, which is what `revenueModelKind` reads. */
export function catalogAssumptionsOf(revenue: RevenueRead): CatalogRevenueAssumptionsRead | null {
  return revenueModelKind(revenue) === "catalog"
    ? (revenue.assumptions_used as CatalogRevenueAssumptionsRead)
    : null;
}

/** The backend's `ExpectedRevenueState` (SC-4-03, point 5b-c). */
export const EXPECTED_REVENUE_STATES = ["calculated", "no_probabilities", "not_applicable"] as const;
export type ExpectedRevenueState = (typeof EXPECTED_REVENUE_STATES)[number];

/** One outcome category's revenue after the min/max bounds (SC-4-03, points 5b, 6). */
export interface CategoryRevenueRead {
  category: OutcomeCategory;
  /** `null` when the rule has no unit rate and no units were given — never `0`. */
  units: string | null;
  /** `null` when the rule has no probabilities — never `0`. */
  probability: string | null;
  /** Fixed-point decimal string, in the revenue's own currency. */
  amount: string;
}

/**
 * The additive SC-4-03 fields every revenue carries. The pairing `expected_state` ⇄
 * `expected_amount` (an amount only with `"calculated"`, `"n/a"` otherwise) is checked in
 * `api/client.ts`, like the `state` ⇄ `amount` pairing below.
 */
interface ExpectedRevenueFields {
  expected_state: ExpectedRevenueState;
  /** Fixed-point decimal string when `expected_state` is `"calculated"`, `"n/a"` otherwise. */
  expected_amount: string;
  /** Empty for a model without categories and for a withheld revenue. */
  category_revenues: CategoryRevenueRead[];
}

/**
 * A revenue the server stated. The pairing of `state` with `amount`/`currency` is the backend's
 * prose contract ("a fixed-point string when `state` is `calculated`, `n/a` otherwise"), made a type
 * here and checked in `api/client.ts` (`isRevenueShape`).
 */
export interface CalculatedRevenueRead extends ExpectedRevenueFields {
  state: "calculated";
  /** Fixed-point decimal string — possibly `"0.00"`, which is a legal, computed zero. For
   * Outcome-based: the **guaranteed** revenue (addendum SC-4-03, point 5a). */
  amount: string;
  /** The revenue's own currency — never substituted by the project's reporting currency. */
  currency: string;
  assumptions_used: RevenueAssumptionsRead;
}

/** A revenue the server withheld, with the named state saying why (ADR-0003, point 9). */
export interface WithheldRevenueRead extends ExpectedRevenueFields {
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
