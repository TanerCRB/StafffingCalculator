// The single hours-formatting entry point — ADR-0002, addendum 2026-09-26 (SC-3-04): hours are a
// third, deliberately named class of fixed-point decimal value (`NUMERIC(10,2)`,
// backend/app/api/schemas/staffing.py — "hours are one multiplication away from [money]", NF-01).
// They cross the API boundary as fixed-point strings for exactly the same reason money does, and
// share its rounding rule (ROUND_HALF_UP) — but they carry neither a currency nor a percent sign,
// so they get their own module rather than a silently widened `lib/money.ts` (see that file's own
// header: "money/percentage only"). A component that formats an hours value with `Number()`/
// `toFixed()` at the call site instead of through this module is the mutation K-01 (Issue #135) is
// written against.
//
// Reuses `roundDecimalString`/`isDecimalString` from `lib/money.ts` (ADR-0002 annex, point 1) — the
// decimal grammar and its rounding rule live in exactly one place in this codebase; this module
// adds no second reading of it.

import { isDecimalString, roundDecimalString } from "./money";

export { isDecimalString };

const HOURS_FRACTION_DIGITS = 2;

/**
 * Renders an hours figure the API sent as a fixed-point decimal string, rounded half up to match
 * the backend's `NUMERIC(10,2)` column ("7.005" → "7.01 h").
 *
 * This is the *only* entry point a component may call for `availability_hours`/
 * `planned_allocation_hours`/`billable_hours`, and for `derived_capacity_hours`/
 * `absence_budget_hours` once their own named state says the figure is resolved. It takes no
 * sentinel case of its own: a field that can read `"n/a"` carries its own named state
 * (`derived_capacity_state`/`absence_budget_state`) precisely so that a component branches on the
 * state — never on the shape of this value — before ever calling this function (K-02, K-03; the
 * same discipline `ScenarioResultsSection`'s `GatedMoneyLine` already applies to
 * `RESULTS_NOT_APPLICABLE`). Passing `"n/a"` here is therefore a caller error, and it throws like
 * any other malformed value — there is no quiet fallback that would let a `"n/a"` on the wire slip
 * past an unwritten state check and render as a number or as this module's own text.
 *
 * Throws on a value that is not a fixed-point decimal string, exactly as `formatMoneyString`/
 * `formatPercentString` do, and for the same reason (ADR-0002, ADR-0010 point 6): a malformed hours
 * figure is a broken contract, not a number for this function to invent.
 */
export function formatHoursString(value: string): string {
  return `${roundDecimalString(value, HOURS_FRACTION_DIGITS)} h`;
}
