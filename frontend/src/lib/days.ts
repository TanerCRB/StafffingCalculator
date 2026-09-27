// The single days-formatting entry point — ADR-0002, addendum 2026-09-27 (SC-3-06): an absence
// budget's `budget_days` is a fourth, deliberately named class of fixed-point decimal value
// (`NUMERIC(6,2)`, backend/app/api/schemas/catalog.py's `BudgetDays`), after money, percentage
// (`lib/money.ts`) and hours (`lib/hours.ts`, ADR-0002 addendum 2026-09-26). It crosses the API
// boundary as a fixed-point string for the same reason those three do, and shares their rounding
// rule (ROUND_HALF_UP) — but it is a *count of days*, not an hours figure ("one multiplication away
// from money", the phrase `WorkingCalendarEntry.standard_hours_per_day`'s own docstring uses for
// hours and `BudgetDays`'s docstring pointedly does not repeat) and it carries no currency either.
// It gets its own module rather than either of the other two: reusing `formatHoursString` would
// print the wrong unit suffix ("20.00 h" for a day count), and widening `lib/money.ts` would need a
// currency this field never has. A component that formats `budget_days` with `Number()`/`toFixed()`
// at the call site, or through `formatHoursString`, is the mutation this module exists to survive.
//
// Reuses `roundDecimalString`/`isDecimalString` from `lib/money.ts` (the same annex `lib/hours.ts`
// follows) — the decimal grammar and its rounding rule live in exactly one place in this codebase;
// this module adds no second reading of it.

import { isDecimalString, roundDecimalString } from "./money";

export { isDecimalString };

const BUDGET_DAYS_FRACTION_DIGITS = 2;

/**
 * Renders a budget-days figure the API sent as a fixed-point decimal string, rounded half up to
 * match the backend's `NUMERIC(6,2)` column ("20.005" → "20.01 days").
 *
 * This is the *only* entry point a component may call for `AbsenceBudgetEntry.budget_days`. It
 * takes no sentinel case of its own: `budget_days` is never gated and never `"n/a"` (it is not a
 * personnel cost — ADR-0005, addendum 2026-09-22 SC-3-03, point 3) — a value that is not a
 * fixed-point decimal string is therefore always a broken contract, and this function throws on it
 * exactly as `formatHoursString`/`formatMoneyString`/`formatPercentString` do, for the same reason
 * (ADR-0002, ADR-0010 point 6): a malformed figure is not a number for this function to invent.
 */
export function formatBudgetDaysString(value: string): string {
  return `${roundDecimalString(value, BUDGET_DAYS_FRACTION_DIGITS)} days`;
}
