import { DayPicker, type Modifiers } from "react-day-picker";
import "react-day-picker/style.css";

import { WEEKDAY_NAMES, weekPatternDayLabel, weekPatternDays } from "./workingCalendarLabels";
import "./WeekPatternCalendar.css";

/**
 * The visual half of criterion K-01 (gate-1 decision, 2026-09-27: a calendar-rendering library, not
 * a bespoke 7-cell `<div>` grid — see the developer's report for the size/maintenance/licence
 * comparison this choice is based on). `react-day-picker`'s `dayOfWeek` modifier matches every
 * occurrence of a weekday across the displayed month, which is exactly the recurring rule
 * `week_pattern` states — the same weekday is highlighted the same way in every month a person
 * navigates to, because the rule is a property of the weekday, not of any one month.
 *
 * A fixed reference month, deliberately not "this month" (`new Date()`): `week_pattern` names a rule
 * with no effective-date window of its own — the backend schema's own docstring: "the unit of
 * versioning is the calendar" — and showing today's month next to it would imply the opposite, that
 * the pattern is a fact about this month rather than about every week. `2024-01-01` is used for one
 * property only: it is a Monday, so the grid's first row lines up with `WEEKDAY_NAMES` without a
 * single "outside day" cell — and, being fixed, it keeps this widget's rendering independent of the
 * wall clock, the same reason `lib/dates.ts` never routes a calendar date through
 * `new Date(isoString)`.
 */
const REFERENCE_MONTH = new Date(2024, 0, 1);

/** JS `Date#getDay()`'s order (Sunday first, 0-6) for each of `WEEKDAY_NAMES`'s Monday-first
 * indices — the one place this module converts between the two conventions, so the conversion is
 * not repeated at every call site that builds a `dayOfWeek` matcher. */
const JS_DAY_OF_WEEK: readonly number[] = [1, 2, 3, 4, 5, 6, 0];

const WORKING_MODIFIER = "working" as const;
const NON_WORKING_MODIFIER = "non-working" as const;

function daysOfWeekMatching(pattern: string, working: boolean): number[] {
  return weekPatternDays(pattern)
    .map((day, index) => (day.working === working ? JS_DAY_OF_WEEK[index] : null))
    .filter((value): value is number => value !== null);
}

/**
 * The accessible name of a day cell — `labelGridcell`, the label `react-day-picker` uses when the
 * calendar carries no selection mode (this one has none: it displays, it does not pick). Read by
 * assistive technology and, deliberately, nowhere required by this screen's own tests: colour is
 * never the *only* channel a modifier is expressed through (NF-08), but the plain-text list
 * `WeekPatternList` renders beside this widget is the channel this screen's criteria are proved
 * against — see that component.
 */
function labelGridcell(date: Date, modifiers?: Modifiers): string {
  const weekday = WEEKDAY_NAMES[JS_DAY_OF_WEEK.indexOf(date.getDay())] ?? date.toDateString();
  const state = modifiers?.[WORKING_MODIFIER]
    ? WORKING_MODIFIER
    : modifiers?.[NON_WORKING_MODIFIER]
      ? NON_WORKING_MODIFIER
      : undefined;
  const base = `${weekday}, ${date.getDate()} ${date.toLocaleString("en", { month: "long" })}`;
  return state === undefined ? base : `${base} — ${state === WORKING_MODIFIER ? "Working" : "Non-working"}`;
}

/**
 * `week_pattern`'s seven weekdays, highlighted as working/non-working on a calendar grid.
 *
 * Read-only by construction: no `mode` prop and no `onDayClick`, so `react-day-picker` renders every
 * day cell as a non-interactive `<td role="gridcell">` rather than a `<button>` — there is nothing
 * here to select, only a rule to show.
 */
export function WeekPatternCalendar({ pattern }: { pattern: string }) {
  const modifiers = {
    [WORKING_MODIFIER]: { dayOfWeek: daysOfWeekMatching(pattern, true) },
    [NON_WORKING_MODIFIER]: { dayOfWeek: daysOfWeekMatching(pattern, false) },
  };
  return (
    <DayPicker
      ISOWeek
      defaultMonth={REFERENCE_MONTH}
      modifiers={modifiers}
      modifiersClassNames={{
        [WORKING_MODIFIER]: "wc-week-calendar__day--working",
        [NON_WORKING_MODIFIER]: "wc-week-calendar__day--non-working",
      }}
      labels={{ labelGridcell }}
      className="wc-week-calendar"
    />
  );
}

/**
 * The provable half of criterion K-01: the same seven weekdays, in words, in an ordinary list —
 * "Monday: Working", one per line. Assembled from the same `weekPatternDays` reading
 * `WeekPatternCalendar` uses, so the two cannot disagree about a weekday; this is the list a test
 * (and a screen reader, since it is plain text rather than an attribute) reads directly, never the
 * calendar widget's internals.
 */
export function WeekPatternList({ pattern }: { pattern: string }) {
  const days = weekPatternDays(pattern);
  return (
    <ul className="wc-week-calendar__pattern-list">
      {days.map((day) => (
        <li key={day.weekday}>{weekPatternDayLabel(day)}</li>
      ))}
    </ul>
  );
}
