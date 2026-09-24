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
  PersonnelCostState,
} from "../../api/contracts/scenarioResults";
import { REVENUE_STATE_MESSAGES } from "./commercialTermsText";

export { REVENUE_STATE_MESSAGES };

// --- Labels (K-01, K-03) ---------------------------------------------------------------------------

export const RESULTS_HEADING = "Scenario results";

export const REVENUE_LABEL = "Revenue:";
export const PERSONNEL_COST_LABEL = "Personnel cost:";
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
