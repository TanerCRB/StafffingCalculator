// Words for the Working calendars screen (SC-3-06) — the same "one place, not a component's own
// guess" convention as `dimensionLabels.ts` and `writeOutcome.ts`.
//
// Note: this is NOT a translation catalog — no locale mechanism exists in this repository yet (see
// frontend/README.md).

import { BUDGET_REGIME_NOT_APPLICABLE, type StatutoryLeaveState } from "../../api/contracts/catalog";

// --- `week_pattern` (criterion K-01) --------------------------------------------------------------

/** The seven weekday names, Monday first — the same order `week_pattern` is written in
 * (`backend/app/api/schemas/catalog.py`: "Seven characters, Monday first"). */
export const WEEKDAY_NAMES: readonly string[] = [
  "Monday",
  "Tuesday",
  "Wednesday",
  "Thursday",
  "Friday",
  "Saturday",
  "Sunday",
];

/** One weekday of a calendar's `week_pattern`, already interpreted — never the raw character. */
export interface WeekPatternDay {
  readonly weekday: string;
  readonly working: boolean;
}

const WEEK_PATTERN_GRAMMAR = /^[01]{7}$/;

/**
 * Interprets `week_pattern` per weekday, Monday first (criterion K-01: "never as a raw string
 * without interpretation").
 *
 * Throws on anything but the seven-character, `'0'`/`'1'` string the backend's schema promises
 * (`WorkingCalendarEntry.week_pattern`) — a malformed pattern is a broken contract, not a shape for
 * this function to guess at, the same discipline every formatter in this codebase keeps for a
 * malformed decimal string (ADR-0002, ADR-0010 point 6).
 */
export function weekPatternDays(pattern: string): WeekPatternDay[] {
  if (!WEEK_PATTERN_GRAMMAR.test(pattern)) {
    throw new Error(`Not a seven-character working/non-working pattern: "${pattern.length}" chars`);
  }
  return WEEKDAY_NAMES.map((weekday, index) => ({
    weekday,
    working: pattern.charAt(index) === "1",
  }));
}

export const WORKING_DAY_LABEL = "Working";
export const NON_WORKING_DAY_LABEL = "Non-working";

/** What one weekday of the pattern says, e.g. "Monday: Working". The one sentence both the
 * accessible calendar widget and the plain-text list beside it build from — see
 * `WeekPatternCalendar.tsx`. */
export function weekPatternDayLabel(day: WeekPatternDay): string {
  return `${day.weekday}: ${day.working ? WORKING_DAY_LABEL : NON_WORKING_DAY_LABEL}`;
}

// --- Exceptional days, `days[]` (criterion K-02) --------------------------------------------------

export const EXCEPTION_NON_WORKING_LABEL = "Non-working (exception)";
export const EXCEPTION_WORKING_LABEL = "Working (exception)";

/**
 * What one exceptional day's `kind` says — never the raw `"non_working"`/`"working"` string
 * (criterion K-02). A `kind` outside the two the backend's enum names is a payload this screen
 * cannot read, and it says so rather than guessing (ADR-0010, point 6).
 */
export function calendarDayKindLabel(kind: string): string {
  if (kind === "non_working") {
    return EXCEPTION_NON_WORKING_LABEL;
  }
  if (kind === "working") {
    return EXCEPTION_WORKING_LABEL;
  }
  throw new Error(`Not a recognised exceptional-day kind: "${kind.length}" chars`);
}

// --- The absence budget's statutory-leave regime (criterion K-03) --------------------------------

/** What each `statutory_leave_state` says — two distinct, named sentences (criterion 2 of Issue
 * #140: "never as false/an empty cell pretending to be a decision the backend did not take"). */
export const STATUTORY_LEAVE_LABELS: Readonly<Record<StatutoryLeaveState, string>> = {
  resolved: "Resolved against a named statutory-leave type",
  no_statutory_leave_type: "No absence type in the catalogue is flagged as statutory leave",
};

const REGIME_YES = "Yes";
const REGIME_NO = "No";
export const REGIME_NOT_APPLICABLE_LABEL = "Not applicable";

/**
 * `generates_cost`/`generates_revenue`, as a word — the three-valued rendering criterion K-03 asks
 * for: a real "Yes"/"No" when the backend resolved the regime, and the named "Not applicable" for
 * the sentinel `"n/a"` — never the falsy answer `Boolean("n/a")` (a truthy string!) would in fact
 * produce, and never a blank cell either. This is the one function a table cell may call for either
 * field; a cell that renders `String(value)` or `value ? "Yes" : "No"` directly is the mutation
 * criterion K-03 is written against.
 */
export function budgetRegimeFlagLabel(
  value: boolean | typeof BUDGET_REGIME_NOT_APPLICABLE,
): string {
  if (value === BUDGET_REGIME_NOT_APPLICABLE) {
    return REGIME_NOT_APPLICABLE_LABEL;
  }
  return value ? REGIME_YES : REGIME_NO;
}

// --- Names this screen has no dictionary entry for --------------------------------------------

/** What a budget row says when its `calendar_id` matches no calendar the screen read — the same
 * "named gap between two reads" convention `dimensionLabels.ts`'s `unknownEntryLabel` states for the
 * five catalogue dictionaries (SC-2-02, gate-1 decision 8): the two reads are separate requests, so
 * a calendar can be renamed or removed between them. */
export const UNKNOWN_CALENDAR_LABEL = "Not in the working calendars dictionary";
