// What a scenario card's results section says — every sentence in one place, the same convention as
// `commercialTermsText.ts`. Not a translation catalogue — no locale mechanism exists in this
// repository yet.
//
// Two rules hold for every sentence below, and each is asserted rather than stated (Issue #94):
//
//   * The gate's generic "unavailable" message (gate 1, Q2 = option b) never names the mechanism
//     that withheld the figure — no "PERSONNEL_COSTS_READ", no "permission". It is deliberately the
//     same four words wherever the gate applies, on every field and every source it touches.
//   * Each of the three composed sources (revenue, personnel cost, additional cost) keeps its own
//     named non-computable state — no sentence below is shared across two different sources or two
//     different states (K-03).

import type {
  AdditionalCostState,
  PaidAbsenceCostState,
  PersonnelCostState,
} from "../../api/contracts/scenarioResults";
import {
  GUARANTEED_REVENUE_LABEL,
  REVENUE_STATE_MESSAGES,
  RULE_CURRENCY_MISMATCH,
  revenueStateMessage,
} from "./commercialTermsText";

// One wording per revenue state and label, shared with the commercial-terms section — the two
// sections must not come to word the same revenue two ways (SC-4-07).
export { GUARANTEED_REVENUE_LABEL, REVENUE_STATE_MESSAGES, RULE_CURRENCY_MISMATCH, revenueStateMessage };

// --- Labels (K-01, K-03) ---------------------------------------------------------------------------

export const RESULTS_HEADING = "Scenario results";

export const REVENUE_LABEL = "Revenue:";
export const PERSONNEL_COST_LABEL = "Base personnel cost:";
export const PAID_ABSENCE_COST_LABEL = "Paid absence cost:";
export const ADDITIONAL_COST_LABEL = "Additional costs:";
export const INCLUDED_COST_LABEL = "Scenario cost:";
export const PROFIT_LABEL = "Profit:";
export const MARGIN_LABEL = "Margin:";
export const MARKUP_LABEL = "Markup:";

/**
 * The personnel-cost gate's rendered answer (gate 1, Q2 = option b) — shown for `profit`, `margin`,
 * `markup`, `included_cost` and for `personnel_cost` itself when any of them is `null`. Deliberately
 * says nothing about why: not "you lack permission", not "personnel costs are hidden" — the security-
 * conscious choice already made for `PERSONNEL_COSTS_READ` elsewhere (SC-7-01, risk B-01). Worded so
 * that it cannot be mistaken for `lib/money.ts`'s `NOT_APPLICABLE` ("Not applicable"): a gate closed
 * and a figure that is not computable are two different states and must never read as the same
 * sentence (K-02).
 */
export const RESULTS_FIELD_UNAVAILABLE = "Not shown on this screen.";

/**
 * `profitability_state = "currency_mismatch"` (SC-4-03, R-01): every component is stated, but not
 * all in one currency, so profit, margin and markup are not summed across currencies (F-10). A status
 * line of the section, shown whether or not the personnel-cost gate is open (SC-4-07, Q-A = B;
 * ADR-0005, addendum 2026-09-25 SC-4-07): it states the fact of the mismatch only — no amount, no
 * currency code — and the currencies it rests on are on the component payloads anyway.
 *
 * Distinct from `RESULTS_FIELD_UNAVAILABLE` (the gate), from `lib/money.ts`'s `NOT_APPLICABLE` (a
 * field not computable) and from every revenue/cost `currency_mismatch` sentence — none is a
 * substring of another (asserted). A margin of `"n/a"` beside `calculated` (AC-05) is not this.
 */
export const PROFITABILITY_CURRENCY_MISMATCH =
  "Profit, margin and markup not stated — the scenario's revenue and costs are in different " +
  "currencies, and nothing is converted.";

// --- The personnel-cost source's own non-computable states (K-03) ----------------------------------

export const PERSONNEL_COST_STATE_MESSAGES: Readonly<
  Record<Exclude<PersonnelCostState, "calculated">, string>
> = {
  no_cost_rate:
    "Personnel cost not stated — at least one staffed month has no single catalogue cost rate " +
    "covering the whole month.",
  currency_mismatch:
    "Personnel cost not stated — the cost rates are in more than one currency, or in one other " +
    "than the scenario's, and nothing is converted.",
  no_cost_currency:
    "Personnel cost not stated — there is no staffed month to take a cost rate's currency from, " +
    "and the scenario declares none.",
};

export const PAID_ABSENCE_COST_STATE_MESSAGES: Readonly<
  Record<Exclude<PaidAbsenceCostState, "calculated">, string>
> = {
  no_calendar: "Paid absence cost not stated — no working calendar is available for the absence.",
  no_statutory_leave_type: "Paid absence cost not stated — no statutory leave type is configured.",
  no_budget: "Paid absence cost not stated — no leave budget is available.",
  no_cost_rate: PERSONNEL_COST_STATE_MESSAGES.no_cost_rate,
  currency_mismatch: PERSONNEL_COST_STATE_MESSAGES.currency_mismatch,
  no_cost_currency: PERSONNEL_COST_STATE_MESSAGES.no_cost_currency,
  no_working_days: "Paid absence cost not stated — the absence has no working days to cost.",
};

// --- The additional-cost source's own non-computable states (K-03) ---------------------------------

export const ADDITIONAL_COST_STATE_MESSAGES: Readonly<
  Record<Exclude<AdditionalCostState, "calculated">, string>
> = {
  currency_mismatch:
    "Additional costs not stated — the cost rows are in more than one currency, or in one other " +
    "than the scenario's, and nothing is converted.",
  no_cost_currency:
    "Additional costs not stated — there is no cost row to take a currency from, and the " +
    "scenario declares none.",
};

// --- Reading (K-04, K-05, K-06) ---------------------------------------------------------------------

export const RESULTS_LOADING = "Loading scenario results.";

/**
 * `403` and `404` alike (Issue #94, K-04 — the deliberate difference from
 * `ScenarioCommercialTermsSection`'s `READ_DENIED`/`READ_NOT_FOUND`): the caller gets no signal
 * telling "not yours to see" from "does not exist" apart, for either status.
 */
export const RESULTS_REFUSED = "Scenario results not shown.";

/** `409` — the rate-source race (SC-7-01): revenue and cost were read a moment apart, and the
 * scenario's approval state moved between them. Its own state, and the only one that names retry. */
export const RESULTS_CONFLICT =
  "Scenario results could not be loaded — the scenario changed while this was being read. " +
  "Read again.";

export const RESULTS_TIMED_OUT =
  "Scenario results could not be loaded — the server did not answer in time.";

/** A `2xx` whose body is not the contract. */
export const RESULTS_UNREADABLE =
  "Scenario results could not be loaded — the server's answer was not in a form this screen can read.";

export const RESULTS_FAILED = "Scenario results could not be loaded — the server failed to answer.";

/** The control that re-reads this section only. */
export const RESULTS_READ_AGAIN = "Read scenario results again";
