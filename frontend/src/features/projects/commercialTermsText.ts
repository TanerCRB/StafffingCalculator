// What a scenario card's commercial-terms section says — every sentence in one place, so that the
// set can be held against itself (no message is a substring of another) by a single test, and so
// that two branches cannot come to word one state two ways. Same convention as
// `features/catalog/writeOutcome.ts`; like it, NOT a translation catalogue — no locale mechanism
// exists in this repository yet.
//
// Three rules hold for every sentence below, and each is asserted rather than stated:
//
//   * A withheld revenue is a named sentence, never `0`, never a blank (ADR-0003, point 9).
//   * None of them asserts a cause the response did not report. A `409` naming none of the three
//     causes the backend can state gets `SAVE_REFUSED_UNSTATED`, not the most plausible of them.
//   * None of them carries a value — not from the form (there is one fixed field) and not from a
//     response body (NF-11; ADR-0009, point 6).

import { ApiError, RequestTimeoutError } from "../../api/client";
import {
  TIME_AND_MATERIAL,
  type RateSource,
  type WithheldRevenueState,
} from "../../api/contracts/commercialTerms";
import {
  commercialTermsRefusalCauseOf,
  type CommercialTermsRefusalCause,
} from "../../api/contracts/commercialTermsRefusals";

// --- The six withheld revenue states (SC-4-06, K-01) ---------------------------------------------

/**
 * One sentence per state the backend names instead of a revenue. Exhaustive over
 * `WithheldRevenueState`, so a state added to the contract fails the build here rather than falling
 * into a generic sentence. The wording follows the backend's own definitions in
 * `backend/app/domain/revenue.py` and says what a reader can act on, without numbers.
 */
export const REVENUE_STATE_MESSAGES: Readonly<Record<WithheldRevenueState, string>> = {
  no_commercial_terms: "Revenue not stated — no commercial rule is set for this scenario.",
  incomplete_commercial_terms:
    "Revenue not stated — the commercial rule has no model details stored with it.",
  unsupported_model_type:
    "Revenue not stated — this version of the application cannot price the rule's commercial model.",
  no_rate:
    "Revenue not stated — at least one staffed month has no single catalogue selling rate " +
    "covering the whole month.",
  currency_mismatch:
    "Revenue not stated — the selling rates are in more than one currency, or in one other than " +
    "the scenario's, and nothing is converted.",
  no_revenue_currency:
    "Revenue not stated — there is no staffed month to take a currency from, and the scenario " +
    "declares none.",
};

/** The prefix a calculated revenue is shown with; the amount comes from `lib/money.ts` only. */
export const REVENUE_LABEL = "Revenue:";

// --- The rule itself (K-03) ----------------------------------------------------------------------

/** The section's heading — and, through `aria-labelledby`, its accessible name. */
export const COMMERCIAL_TERMS_HEADING = "Commercial terms";

export const COMMERCIAL_MODEL_LABEL = "Commercial model:";

/** `commercial_terms: null` — the scenario has no rule. */
export const NO_RULE = "not set";

/** The words this screen uses for the models it knows. A `Map`, not an object literal: an object
 * would answer `"constructor"` with a function. */
const MODEL_LABELS: ReadonlyMap<string, string> = new Map([[TIME_AND_MATERIAL, "Time & Material"]]);

/**
 * The name of a stored model. A model this version does not know is shown **as the server spelled
 * it** — the response side of `model_type` is open on purpose (SC-4-01, R-02), and the revenue's
 * own `unsupported_model_type` state says what that means. Inventing a name, or hiding the word,
 * would be this screen deciding something the server did not.
 */
export function commercialModelName(modelType: string): string {
  return MODEL_LABELS.get(modelType) ?? modelType;
}

/** The label of the only write this section offers (and its accessible name, with the scenario). */
export const SET_TIME_AND_MATERIAL = "Set Time & Material";

/** Presentation of the status the server just reported (ADR-0009, addendum 2026-09-23, point 4),
 * shown where the action would be — not a rule of this screen's own. */
export const APPROVED_SCENARIO_NOTE =
  "The commercial rule cannot be set here, because the server reports this scenario as approved.";

// --- `assumptions_used`, readably (gate 1, D-1 = option B) --------------------------------------

export const RATE_SOURCE_LABELS: Readonly<Record<RateSource, string>> = {
  live_catalog: "Rates taken from: the live catalogue",
  approved_snapshot: "Rates taken from: the rates frozen when the scenario was approved",
};

export const RATE_WINDOWS_LABEL = "Selling rate windows used:";
export const NO_RATE_WINDOWS = "Selling rate windows used: none";
export const UNRESOLVED_MONTHS_LABEL = "Months without a rate:";

// --- Reading (K-01, K-04) ------------------------------------------------------------------------

export const READ_LOADING = "Loading commercial terms.";

/** `403` — the caller may see the scenario (it is on the list) and may not see its commercial
 * terms. Only this section says so; the rest of the card stays (K-04). */
export const READ_DENIED =
  "Commercial terms not shown — you do not have permission to view them.";

/** `404` — the backend's one answer for "not yours, does not exist, or not in this project". A
 * sentence of its own, and never "no rule yet": that one offers a write (K-04). */
export const READ_NOT_FOUND =
  "Commercial terms not shown — the server answered that this scenario was not found.";

export const READ_TIMED_OUT =
  "Commercial terms could not be loaded — the server did not answer in time.";

/** A `2xx` whose body is not the contract — an unlisted revenue state included (ADR-0010,
 * point 2). Named, and distinct from the server failing: here it answered. */
export const READ_UNREADABLE =
  "Commercial terms could not be loaded — the server's answer was not in a form this screen can read.";

export const READ_FAILED = "Commercial terms could not be loaded — the server failed to answer.";

/** The control that re-reads this section only. */
export const READ_AGAIN = "Read commercial terms again";

// --- Writing (K-05, K-06) ------------------------------------------------------------------------

export const SAVING = "Setting the commercial rule.";

/** Shown with a state that came from the `201` body and from nothing else (K-05). */
export const SAVED =
  "Saved — the rule and the revenue shown are the server's answer to this save.";

/** `409`, `CommercialTermsFrozen`: approved between the read and this save (the race the hidden
 * control cannot close — ADR-0009, addendum 2026-09-23). Permanent: the guidance is a copy. */
export const SAVE_REFUSED_APPROVED =
  "Not saved — the scenario is approved, so its commercial rule can no longer be set. " +
  "Copy the scenario and set the rule on the copy.";

/** `409`, `uq_commercial_terms_scenario_id`: the scenario already has a rule — possibly this very
 * caller's own earlier save whose answer never arrived. */
export const SAVE_REFUSED_RULE_EXISTS =
  "Not saved — the scenario already has a commercial rule, possibly from an earlier save of your " +
  "own. Read the commercial terms again to see it.";

/** `409`, `CommercialTermsScenarioChanged`. */
export const SAVE_REFUSED_SCENARIO_CHANGED =
  "Not saved — the scenario changed since it was read. Read the commercial terms again before " +
  "deciding anything.";

/** A `409` whose body named none of the causes above. The honest ending, and a first-class one. */
export const SAVE_REFUSED_UNSTATED =
  "Not saved — the server refused to set the rule, and the answer did not say why.";

export const SAVE_DENIED =
  "Not saved — you do not have permission to set this scenario's commercial rule.";

export const SAVE_NOT_FOUND =
  "Not saved — the server answered that the scenario to set the rule on was not found.";

/** `422`. The request has one fixed field, so this is a contract drift, not a typing mistake. */
export const SAVE_INVALID = "Not saved — the server rejected the request as malformed.";

/** `500` or any non-2xx status without a name here. Says the outcome is unknown, not that nothing
 * was written — a write that broke mid-statement is not a write that provably did not happen. */
export const SAVE_FAILED =
  "Not saved as far as this screen can tell — the server failed while writing. " +
  "Read the commercial terms again to see what is stored.";

/** The deadline expired (ADR-0009, point 2). Unresolved: no idempotency key, so no guess. */
export const SAVE_UNRESOLVED =
  "Unresolved — the server did not answer in time, so the rule may or may not have been set. " +
  "Read the commercial terms again before trying again.";

/** A `2xx` whose body failed the shape check (Reviewer R-03 of SC-2-04, carried over). */
export const SAVE_UNRESOLVED_UNREADABLE_ANSWER =
  "Unresolved — the server reported success but answered in a form this screen could not read, " +
  "so the rule may or may not have been set. Read the commercial terms again before trying again.";

const CONFLICT_MESSAGES: Readonly<Record<CommercialTermsRefusalCause, string>> = {
  approved: SAVE_REFUSED_APPROVED,
  "rule-exists": SAVE_REFUSED_RULE_EXISTS,
  "scenario-changed": SAVE_REFUSED_SCENARIO_CHANGED,
  unstated: SAVE_REFUSED_UNSTATED,
};

/**
 * Turns whatever the commercial-rule write rejected with into the one sentence the card shows.
 *
 * The status decides first, the body second and only for `409` — where one status carries three
 * causes told apart by the backend's own words (`contracts/commercialTermsRefusals.ts`, D-3). A
 * `2xx` arriving here is `write`'s shape check refusing a body the server committed: unresolved,
 * never failed.
 */
export function describeCommercialTermsWriteFailure(error: unknown): string {
  if (error instanceof RequestTimeoutError) {
    return SAVE_UNRESOLVED;
  }
  if (!(error instanceof ApiError)) {
    return SAVE_FAILED;
  }
  if (error.status >= 200 && error.status < 300) {
    return SAVE_UNRESOLVED_UNREADABLE_ANSWER;
  }
  switch (error.status) {
    case 401:
    case 403:
      return SAVE_DENIED;
    case 404:
      return SAVE_NOT_FOUND;
    case 409:
      return CONFLICT_MESSAGES[commercialTermsRefusalCauseOf(error.detail)];
    case 422:
      return SAVE_INVALID;
    default:
      return SAVE_FAILED;
  }
}
