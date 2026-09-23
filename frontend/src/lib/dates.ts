// The single entry point for rendering dates and date ranges — same reasoning as money.ts:
// a date assembled at the call site is a second, silently diverging format.
//
// Deliberately locale-neutral for now: ISO-8601 calendar dates exactly as the API sends them,
// joined by an en dash. A locale-aware format (Intl.DateTimeFormat) is a product decision that
// needs a locale to come from somewhere — there is no locale/translation layer in this
// repository yet, and inventing one here would be a second mechanism nobody decided on.

// Nothing here parses a date. `new Date("2026-01-01")` is UTC midnight, and a local formatter west
// of Greenwich renders it as the previous day — a calendar date that is correct in the payload and
// wrong on the screen, with nothing thrown. The API sends calendar dates (`datetime.date`), so the
// string is the value.

const RANGE_SEPARATOR = "–"; // en dash

/** Renders a delivery/implementation period, e.g. "2026-01-01 – 2026-12-31". */
export function formatDeliveryPeriod(start: string, end: string): string {
  return `${start} ${RANGE_SEPARATOR} ${end}`;
}

/**
 * What an effective window with no end date is called on screen.
 *
 * A named absence, never a date: `effective_to: null` means "open-ended" (ADR-0008, point 2), and
 * the backend refuses a far-future sentinel precisely so that no client has to recognise one. A
 * rendered "9999-12-31" — or a blank cell — would turn a fact into either a lie or a gap.
 */
export const OPEN_ENDED_PERIOD = "Open-ended";

/**
 * Renders a rate's effective window, e.g. "2026-01-01 – 2026-12-31" or "2026-01-01 – Open-ended".
 *
 * `effective_to` is inclusive and is printed literally: the `+ 1 day` conversion to PostgreSQL's
 * half-open form exists in exactly one place, the generated `valid_period` column (ADR-0008,
 * point 3, and the addendum of 2026-09-19, point 3). Nothing here reproduces it.
 */
export function formatEffectivePeriod(from: string, to: string | null | undefined): string {
  return `${from} ${RANGE_SEPARATOR} ${to ?? OPEN_ENDED_PERIOD}`;
}

const CALENDAR_MONTH = /^(\d{4}-\d{2})-\d{2}$/;

/**
 * Renders a calendar month the API sends as the date of its first day ("2026-03-01" → "2026-03"),
 * e.g. a revenue's `unresolved_months[].period_month` (SC-4-06).
 *
 * Still no parsing — the month is cut out of the string, for the same UTC-midnight reason as above.
 * A value that is not a calendar date is printed exactly as sent: a month shown as the server spelled
 * it is a stranger-looking truth, a month this function re-spelled would be a guess.
 */
export function formatCalendarMonth(periodMonth: string): string {
  return CALENDAR_MONTH.exec(periodMonth)?.[1] ?? periodMonth;
}
