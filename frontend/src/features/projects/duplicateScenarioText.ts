// What the "Duplicate" control on a scenario card says — every sentence in one place, so the set
// can be held against itself (no message is a substring of another), the same convention as
// `commercialTermsText.ts` and `features/catalog/writeOutcome.ts`. Not a translation catalogue —
// no locale mechanism exists in this repository yet.
//
// Two rules, asserted rather than merely stated (SC-6-03, K-04/K-05):
//   * `403`, `404` and `409` are three separate, named endings — never merged into one generic
//     "failed" and never silent.
//   * None of them carries a value: the endpoint takes no request body (nothing to echo) and the
//     backend's own refusal text is never read here (NF-11) — there is nothing this screen adds a
//     cause to that the response did not already state.

import { ApiError, RequestTimeoutError } from "../../api/client";

/** The control's label, and its accessible name together with the scenario it copies. */
export const DUPLICATE_LABEL = "Duplicate";

export const DUPLICATING = "Duplicating the scenario.";

/** Shown once the new row is on screen — built from the `201` body and nothing else (K-01). */
export const DUPLICATED = "Duplicated — the new scenario now appears in the list.";

/** `403` — missing `SCENARIO_COPY`, enforced before the database (`backend/app/api/scenarios.py`). */
export const DUPLICATE_DENIED =
  "Not duplicated — you do not have permission to copy this scenario.";

/** `404` — one answer for "out of the caller's scope" and "does not exist" (ADR-0005). */
export const DUPLICATE_NOT_FOUND =
  "Not duplicated — the server answered that this scenario was not found.";

/** `409` — the backend exhausted its candidate name suffixes, or lost a concurrent naming race.
 * Nothing was written either way (ADR-0009, point 6: a refusal carries no value the caller typed —
 * this write has none to begin with). */
export const DUPLICATE_CONFLICT =
  "Not duplicated — the server could not find a free name for the copy, or lost a naming race. " +
  "Try again.";

/** The deadline expired (ADR-0009, point 2). Unresolved: no idempotency key, so no guess whether a
 * copy exists. The list is not re-read automatically — a person decides whether to look. */
export const DUPLICATE_UNRESOLVED =
  "Unresolved — the server did not answer in time, so the copy may or may not have been created.";

/** A `2xx` whose body failed `isScenarioListItemShape` — a payload this client cannot read is not
 * evidence that a row exists (K-01's contrast). Distinct from every named refusal below: the server
 * answered success, and the answer cannot be rendered. */
export const DUPLICATE_UNRESOLVED_UNREADABLE_ANSWER =
  "Unresolved — the server reported success but answered in a form this screen could not read, " +
  "so the copy may or may not have been created.";

/** Any other non-2xx status. Says the outcome is unknown, not that nothing was written. */
export const DUPLICATE_FAILED = "Not duplicated — the server failed while creating the copy.";

/**
 * Turns whatever the duplicate write rejected with into the one sentence the card shows.
 *
 * The status decides everything: this endpoint has no request body, so there is no `422` field to
 * name and no `409` cause worth reading out of the response body (NF-11) — unlike the commercial
 * terms write, whose `409` carries three distinguishable causes in prose.
 */
export function describeDuplicateScenarioFailure(error: unknown): string {
  if (error instanceof RequestTimeoutError) {
    return DUPLICATE_UNRESOLVED;
  }
  if (!(error instanceof ApiError)) {
    return DUPLICATE_FAILED;
  }
  if (error.status >= 200 && error.status < 300) {
    return DUPLICATE_UNRESOLVED_UNREADABLE_ANSWER;
  }
  switch (error.status) {
    case 401:
    case 403:
      return DUPLICATE_DENIED;
    case 404:
      return DUPLICATE_NOT_FOUND;
    case 409:
      return DUPLICATE_CONFLICT;
    default:
      return DUPLICATE_FAILED;
  }
}
