// The shape of a refused commercial-rule write, as the backend actually reports it — see
// backend/app/data/commercial_terms.py (`_diagnose_refusal`) and backend/app/api/commercial_terms.py.
// Part of the contracts layer for the same reason `writeRefusals.ts` is: a component matching a
// message string would be re-guessing the contract at the call site.
//
// Why this reads prose rather than an identifier (gate 1 of SC-4-06, decision D-3 = option b):
// `POST …/commercial-terms` answers `409` for three unrelated reasons, and two of them carry no
// `condition=`/`sqlstate=` identifier at all — only a sentence:
//
//   * the scenario is **approved** (`CommercialTermsFrozen`) — permanent; the way forward is a copy
//     of the scenario, and re-reading changes nothing;
//   * the scenario **changed since it was read** (`CommercialTermsScenarioChanged`) — re-read;
//   * the scenario **already has a rule** — refused by the database's unique constraint
//     `uq_commercial_terms_scenario_id` (`sqlstate=23505`), e.g. an earlier save of the caller's own
//     that the caller never saw the answer to — re-read.
//
// Adding an identifier would have taken this task into `backend/`. Instead each marker below is a
// fragment of the backend's own wording, and `commercialTermsRefusals.test.ts` reads the backend
// source to hold every one of them against it: a reworded backend sentence fails the build here
// rather than quietly turning every such `409` into `"unstated"`.
//
// Nothing here parses a value out of a refusal: the backend builds all three from identifiers and
// fixed prose (NF-11).

/** A fragment of the `CommercialTermsFrozen` message — the scenario is approved (ADR-0004). */
export const APPROVED_SCENARIO_MARKER = "This scenario is approved";

/** A fragment of the `CommercialTermsScenarioChanged` message. */
export const SCENARIO_CHANGED_MARKER = "The scenario changed since it was read";

/** The unique constraint that refuses a second rule for one scenario
 * (`backend/app/models/commercial_terms.py`). The database names it in the refusal. */
export const ONE_RULE_PER_SCENARIO_CONSTRAINT = "uq_commercial_terms_scenario_id";

/**
 * What a `409` on the commercial-rule write said refused it. `"unstated"` is a first-class member:
 * a `409` whose body names none of the three must reach the screen as "refused, and the answer did
 * not say why" — never as the most plausible-sounding of the others.
 */
export type CommercialTermsRefusalCause =
  | "approved"
  | "scenario-changed"
  | "rule-exists"
  | "unstated";

/** Reads the cause off the refusal's own message. The three markers are disjoint in the backend's
 * wording (asserted by the contract test), so the order below decides nothing but readability. */
export function commercialTermsRefusalCauseOf(
  detail: string | undefined,
): CommercialTermsRefusalCause {
  if (detail === undefined || detail === "") {
    return "unstated";
  }
  if (detail.includes(APPROVED_SCENARIO_MARKER)) {
    return "approved";
  }
  if (detail.includes(SCENARIO_CHANGED_MARKER)) {
    return "scenario-changed";
  }
  if (detail.includes(ONE_RULE_PER_SCENARIO_CONSTRAINT)) {
    return "rule-exists";
  }
  return "unstated";
}
