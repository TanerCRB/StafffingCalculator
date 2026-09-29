// The shape of a refused write, as the backend actually reports it — see
// backend/app/data/write_errors.py. Part of the contracts layer for the same reason the response
// shapes are: a component that matched on `/overlap/i` in a message string would be re-guessing the
// contract at the call site, and would keep passing after the backend reworded the sentence.
//
// Two facts about the catalogue's write path make this module necessary rather than tidy
// (Issue #49, ADR-0007 addendum 2026-09-21 point 4):
//
//   * `409` has **two unrelated meanings on one endpoint**. A stale concurrency marker means
//     "re-read the row and make the change again, it may well succeed then"; an overlap or a
//     duplicate name means "this change will never succeed as written". Telling a person the wrong
//     one of those sends them into a loop, and the status code alone cannot tell them apart.
//   * The backend refuses to assert a cause it did not establish. A failure with no SQLSTATE in its
//     closed list is a `500`, not a plausible-sounding `409` (R-01) — so this module must have an
//     answer for a `409` whose body names no mechanism at all, and that answer is `"unstated"`,
//     never a guess.
//
// Nothing here parses a value out of a refusal, because a refusal carries none: the backend builds
// the message from identifiers only (NF-11, `describe_without_values`).

/**
 * The identifier a `409` carries when the ADR-0007 marker in the `UPDATE`'s `WHERE` matched no row.
 *
 * Mirrors `CONCURRENCY_MARKER_CONDITION` in backend/app/data/write_errors.py. It is the one refusal
 * with no SQLSTATE — a conditional `UPDATE` that matches nothing raises nothing — which is exactly
 * why the backend spells it as `condition=…` where the driver-reported ones carry `sqlstate=…`.
 */
export const CONCURRENCY_MARKER_CONDITION = "updated_at_marker";

/**
 * The identifier a `409` carries when a caller without `PERSONNEL_COSTS_READ` sent a `cost_rate_unit`
 * that differs from the stored one (SC-5-08). Mirrors `COST_RATE_UNIT_CONDITION` in
 * backend/app/data/catalog.py. Like the marker, it has no SQLSTATE; unlike it, retrying after a
 * re-read does not help, and the body names neither the stored unit nor any rate.
 */
export const COST_RATE_UNIT_CONDITION = "cost_rate_unit_precondition";

/**
 * The four SQLSTATEs the backend classifies as "refused by the state of the data"
 * (`REFUSAL_BY_SQLSTATE`). A closed list there, a closed list here.
 */
export const REFUSAL_SQLSTATE = {
  /** `23P01` — the `EXCLUDE` constraint: this window overlaps an existing one for the same key. */
  exclusionViolation: "23P01",
  /** `23505` — a unique index: another row already carries that value (a normalised name). */
  uniqueViolation: "23505",
  /** `23503` — a foreign key: this row references something that does not exist. */
  foreignKeyViolation: "23503",
  /** `23514` — a CHECK constraint refused a value. */
  checkViolation: "23514",
} as const;

/**
 * What a refusal said refused it. `"unstated"` is a first-class member, not a fallback for tidiness:
 * a `409` whose body names no mechanism must reach the screen as "refused, and the answer did not
 * say which rule" — never as the most likely-sounding of the others.
 */
export type RefusalCause =
  | "stale-marker"
  | "unit-precondition"
  | "overlap"
  | "duplicate-value"
  | "missing-reference"
  | "broken-rule"
  | "unstated";

/**
 * Reads the cause off the refusal's own message, by the identifiers the backend puts there.
 *
 * Order matters in exactly one place and is asserted by the criterion tests: the marker condition is
 * checked first, and a message carrying it never carries a SQLSTATE (the backend's own test
 * `test_the_stale_marker_conflict_and_the_overlap_conflict_are_distinguishable` asserts both
 * halves). Everything else is a lookup, so this function cannot come to contain a rule about the
 * catalogue that the database does not have.
 */
export function refusalCauseOf(detail: string | undefined): RefusalCause {
  if (detail === undefined || detail === "") {
    return "unstated";
  }
  if (detail.includes(CONCURRENCY_MARKER_CONDITION)) {
    return "stale-marker";
  }
  if (detail.includes(COST_RATE_UNIT_CONDITION)) {
    return "unit-precondition";
  }
  if (detail.includes(REFUSAL_SQLSTATE.exclusionViolation)) {
    return "overlap";
  }
  if (detail.includes(REFUSAL_SQLSTATE.uniqueViolation)) {
    return "duplicate-value";
  }
  if (detail.includes(REFUSAL_SQLSTATE.foreignKeyViolation)) {
    return "missing-reference";
  }
  if (detail.includes(REFUSAL_SQLSTATE.checkViolation)) {
    return "broken-rule";
  }
  return "unstated";
}
