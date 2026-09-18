// The single entry point for rendering dates and date ranges — same reasoning as money.ts:
// a date assembled at the call site is a second, silently diverging format.
//
// Deliberately locale-neutral for now: ISO-8601 calendar dates exactly as the API sends them,
// joined by an en dash. A locale-aware format (Intl.DateTimeFormat) is a product decision that
// needs a locale to come from somewhere — there is no locale/translation layer in this
// repository yet, and inventing one here would be a second mechanism nobody decided on.

const RANGE_SEPARATOR = "–"; // en dash

/** Renders a delivery/implementation period, e.g. "2026-01-01 – 2026-12-31". */
export function formatDeliveryPeriod(start: string, end: string): string {
  return `${start} ${RANGE_SEPARATOR} ${end}`;
}
