import { useCallback, useEffect, useRef, useState } from "react";

import {
  ApiError,
  RequestTimeoutError,
  getCatalogAbsenceBudgets,
  getCatalogDimension,
  getWorkingCalendars,
} from "../../api/client";
import type {
  AbsenceBudgetEntry,
  DimensionEntry,
  WorkingCalendarDayEntry,
  WorkingCalendarEntry,
} from "../../api/contracts/catalog";
import { formatEffectivePeriod } from "../../lib/dates";
import { formatBudgetDaysString } from "../../lib/days";
import { formatHoursString } from "../../lib/hours";
import { AbsenceBudgetForm } from "./AbsenceBudgetForm";
import { unknownEntryLabel } from "./dimensionLabels";
import { WeekPatternCalendar, WeekPatternList } from "./WeekPatternCalendar";
import {
  STATUTORY_LEAVE_LABELS,
  UNKNOWN_CALENDAR_LABEL,
  budgetRegimeFlagLabel,
  calendarDayKindLabel,
} from "./workingCalendarLabels";
import "./WorkingCalendarsScreen.css";

/**
 * SC-3-06 — the working calendars (odczyt) and the leave budget (odczyt + dodanie), the screen
 * SC-3-02/SC-3-03 deliberately left unbuilt (`AppShell.tsx`'s `RAIL_WORKSPACE`, "the screen is not
 * built"). Mounted as its own workspace-level `ScreenKey` (`"working-calendars"`), mechanically like
 * `"roles-and-rates"` — no router, no URL, no project/scenario state, because neither dictionary has
 * a `project_access` filter (ADR-0005, addenda 2026-09-22 SC-3-02 pt 3, SC-3-03 pt 2-3).
 *
 * Three properties are criteria rather than styling:
 *
 *   * **`week_pattern` is never a raw string.** Every calendar's pattern is interpreted per weekday,
 *     Monday first, in two places that cannot disagree because both read `weekPatternDays`: a
 *     colour-coded calendar widget (the library gate 1 asked for) and a plain-text list beside it
 *     (criterion K-01, NF-08 — colour is never the only channel).
 *   * **A budget's regime is three-valued, never collapsed to a boolean.** `generates_cost`/
 *     `generates_revenue` render as "Yes"/"No" when the backend resolved a statutory-leave type,
 *     and as the named "Not applicable" — never `false`, never a blank cell — when it did not
 *     (criterion K-03).
 *   * **A denied read renders one recognisable "unavailable" screen, with no data and no
 *     controls** — never a screen that renders and then hides them (ADR-0005, NF-04). Unlike
 *     `CatalogScreen`, there is a second, *un*proven half of the usual template deliberately absent
 *     here: neither dictionary has `project_access`, so there is no "out of this caller's scope"
 *     case to keep indistinguishable from "does not exist" (see Issue #140, criterion 3).
 *
 * Out of scope, deliberately: editing a calendar or a budget (no `PATCH` exists for either —
 * Issue #140); a separate "Absence types" section (the statutory-leave regime embedded in each
 * budget is the only slice of that dictionary anything reads); filtering/pagination (both `GET`s
 * return everything, unparametrised).
 */

type SaveNotice = { kind: "re-read" } | { kind: "not-re-read" };

const WC_RE_READING = "The change was accepted. Re-reading the working calendars and budgets…";
const WC_SAVED_AND_REREAD =
  "Saved. The budgets below are a fresh read from the server.";
const WC_SAVED_BUT_NOT_REREAD =
  "Saved, but the catalogue could not be re-read afterwards. The rows below are from before this " +
  "change; reload the screen to see the catalogue as it is now.";

interface ScreenSnapshot {
  readonly calendars: WorkingCalendarEntry[];
  readonly budgets: AbsenceBudgetEntry[];
  readonly engagementTypes: DimensionEntry[];
}

type ScreenState =
  | { kind: "loading" }
  | ({ kind: "ready" } & ScreenSnapshot)
  | { kind: "denied" }
  | { kind: "timed-out" }
  | { kind: "failed" };

function toFailureState(error: unknown): ScreenState {
  if (error instanceof ApiError && (error.status === 401 || error.status === 403)) {
    return { kind: "denied" };
  }
  if (error instanceof RequestTimeoutError) {
    return { kind: "timed-out" };
  }
  return { kind: "failed" };
}

/**
 * The three reads, as one outcome — mirrors `CatalogScreen.tsx`'s `readCatalogue` (Reviewer R-01,
 * R-06 of SC-2-04): `Promise.all` so a gap between "some answers arrived" and "the rest are still in
 * flight" is never rendered, and `controller.abort()` on the first rejection so the other two do not
 * keep spending a socket on a screen that already committed to a failure state.
 */
async function readScreen(controller: AbortController): Promise<ScreenSnapshot> {
  const { signal } = controller;
  const [calendarList, budgetList, engagementTypes] = await Promise.all([
    getWorkingCalendars(signal),
    getCatalogAbsenceBudgets(signal),
    getCatalogDimension("engagement-types", signal),
  ]).catch((error: unknown) => {
    controller.abort();
    throw error;
  });
  return {
    calendars: calendarList.calendars,
    budgets: budgetList.budgets,
    engagementTypes: engagementTypes.entries,
  };
}

export function WorkingCalendarsScreen() {
  const [state, setState] = useState<ScreenState>({ kind: "loading" });
  const [formOpen, setFormOpen] = useState(false);
  const [notice, setNotice] = useState<SaveNotice | null>(null);
  const [rereading, setRereading] = useState(false);

  const inFlight = useRef<Set<AbortController>>(new Set());
  const left = useRef(false);

  const readIntoScreen = useCallback(async (): Promise<ScreenSnapshot> => {
    const controller = new AbortController();
    inFlight.current.add(controller);
    try {
      return await readScreen(controller);
    } finally {
      inFlight.current.delete(controller);
    }
  }, []);

  useEffect(() => {
    const running = inFlight.current;
    left.current = false;
    readIntoScreen()
      .then((snapshot) => {
        if (!left.current) {
          setState({ kind: "ready", ...snapshot });
        }
      })
      .catch((error: unknown) => {
        if (!left.current) {
          setState(toFailureState(error));
        }
      });
    return () => {
      left.current = true;
      for (const controller of running) {
        controller.abort();
      }
      running.clear();
    };
  }, [readIntoScreen]);

  /** Mirrors `CatalogScreen.tsx`'s `afterSave` (gate-1 decision P-3a): what is on screen after a save
   * is always a fresh read's answer, never the write's own response body and never the form's typed
   * values (criterion K-04). */
  const afterSave = useCallback(async () => {
    if (left.current) {
      return;
    }
    setFormOpen(false);
    setNotice(null);
    setRereading(true);
    try {
      const snapshot = await readIntoScreen();
      if (left.current) {
        return;
      }
      setState({ kind: "ready", ...snapshot });
      setNotice({ kind: "re-read" });
    } catch {
      if (left.current) {
        return;
      }
      setNotice({ kind: "not-re-read" });
    } finally {
      if (!left.current) {
        setRereading(false);
      }
    }
  }, [readIntoScreen]);

  const openForm = useCallback(() => {
    setNotice(null);
    setFormOpen(true);
  }, []);
  const closeForm = useCallback(() => setFormOpen(false), []);

  return (
    <section className="wc" aria-labelledby="wc-heading">
      <div className="wc__intro">
        <h2 id="wc-heading" className="wc__title" tabIndex={-1}>
          Working calendars
        </h2>
        {state.kind === "ready" && (
          <p className="wc__description">
            The working calendars and the leave budgets configured for the organisation's
            catalogue.
          </p>
        )}
      </div>

      {state.kind === "loading" && (
        <p role="status" className="wc__message">
          Loading working calendars…
        </p>
      )}
      {/* A denied read renders no calendars, no budgets and no controls — never data that is hidden
          afterwards (ADR-0005). Neither dictionary has a `project_access` filter, so there is no
          second "out of scope" case to keep indistinguishable from this one (Issue #140, criterion 3). */}
      {state.kind === "denied" && (
        <p role="status" className="wc__message wc__message--attention">
          You do not have permission to view working calendars and leave budgets.
        </p>
      )}
      {state.kind === "timed-out" && (
        <p role="status" className="wc__message wc__message--attention">
          The working calendars could not be loaded — request timed out.
        </p>
      )}
      {state.kind === "failed" && (
        <p role="status" className="wc__message wc__message--attention">
          The working calendars could not be loaded.
        </p>
      )}

      {rereading && (
        <p role="status" className="wc__message wc__notice">
          {WC_RE_READING}
        </p>
      )}
      {notice !== null && (
        <p
          role="status"
          className={
            notice.kind === "re-read"
              ? "wc__message wc__notice"
              : "wc__message wc__message--attention wc__notice"
          }
        >
          {notice.kind === "re-read" ? WC_SAVED_AND_REREAD : WC_SAVED_BUT_NOT_REREAD}
        </p>
      )}

      {state.kind === "ready" && (
        <>
          <CalendarsSection calendars={state.calendars} />
          <BudgetsSection
            budgets={state.budgets}
            calendars={state.calendars}
            engagementTypes={state.engagementTypes}
            formOpen={formOpen}
            rereading={rereading}
            onOpen={openForm}
            onCancel={closeForm}
            onSaved={afterSave}
          />
        </>
      )}
    </section>
  );
}

function CalendarsSection({ calendars }: { calendars: WorkingCalendarEntry[] }) {
  return (
    <section className="card wc__panel" aria-labelledby="wc-calendars-heading">
      <h3 id="wc-calendars-heading" className="wc__panel-title">
        Calendars
      </h3>
      {calendars.length === 0 ? (
        <p role="status" className="wc__message">
          No working calendars are configured.
        </p>
      ) : (
        calendars.map((calendar) => <CalendarCard key={calendar.id} calendar={calendar} />)
      )}
    </section>
  );
}

function CalendarCard({ calendar }: { calendar: WorkingCalendarEntry }) {
  return (
    <article className="wc__calendar-card" aria-labelledby={`wc-calendar-${calendar.id}`}>
      <h4 id={`wc-calendar-${calendar.id}`} className="wc__calendar-title">
        {calendar.name}
      </h4>
      <p className="wc__standard-hours">
        Standard day: {formatHoursString(calendar.standard_hours_per_day)}
      </p>
      <WeekPatternCalendar pattern={calendar.week_pattern} />
      <WeekPatternList pattern={calendar.week_pattern} />
      <h5 className="wc__exceptions-title">Exceptional days</h5>
      <ExceptionalDaysList days={calendar.days} />
    </article>
  );
}

function ExceptionalDaysList({ days }: { days: WorkingCalendarDayEntry[] }) {
  if (days.length === 0) {
    return (
      <p role="status" className="wc__message">
        No exceptional days are configured for this calendar.
      </p>
    );
  }
  return (
    <ul className="wc__exceptions">
      {days.map((day) => (
        <li key={day.day}>
          {day.day} — {calendarDayKindLabel(day.kind)}
        </li>
      ))}
    </ul>
  );
}

function idNameMap(entries: readonly { id: string; name: string }[]): ReadonlyMap<string, string> {
  return new Map(entries.map((entry) => [entry.id, entry.name]));
}

function BudgetsSection({
  budgets,
  calendars,
  engagementTypes,
  formOpen,
  rereading,
  onOpen,
  onCancel,
  onSaved,
}: {
  budgets: AbsenceBudgetEntry[];
  calendars: WorkingCalendarEntry[];
  engagementTypes: DimensionEntry[];
  formOpen: boolean;
  rereading: boolean;
  onOpen: () => void;
  onCancel: () => void;
  onSaved: () => Promise<void> | void;
}) {
  const calendarNames = idNameMap(calendars);
  const engagementTypeNames = idNameMap(engagementTypes);

  return (
    <section className="card wc__panel" aria-labelledby="wc-budgets-heading">
      <div className="wc__panel-header">
        <h3 id="wc-budgets-heading" className="wc__panel-title">
          Leave budgets
        </h3>
        {/* Offered unconditionally — whether this caller may write is the server's answer to the
            request, not this screen's answer to a question it never asks (K-18 of SC-2-04, the same
            discipline this screen keeps). */}
        <button type="button" className="button button--primary" disabled={rereading} onClick={onOpen}>
          Add leave budget
        </button>
      </div>

      {formOpen && (
        <AbsenceBudgetForm
          calendars={calendars}
          engagementTypes={engagementTypes}
          onSaved={onSaved}
          onCancel={onCancel}
        />
      )}

      {budgets.length === 0 ? (
        <p role="status" className="wc__message">
          No leave budgets are configured.
        </p>
      ) : (
        <table className="wc__table">
          <caption className="visually-hidden">Leave budgets in the catalogue</caption>
          <thead>
            <tr>
              <th scope="col">Calendar</th>
              <th scope="col">Engagement type</th>
              <th scope="col">Budget</th>
              <th scope="col">Source</th>
              <th scope="col">Effective period</th>
              <th scope="col">Statutory-leave regime</th>
              <th scope="col">Generates cost</th>
              <th scope="col">Generates revenue</th>
            </tr>
          </thead>
          <tbody>
            {budgets.map((budget) => (
              <tr key={budget.id}>
                <td>{calendarNames.get(budget.calendar_id) ?? UNKNOWN_CALENDAR_LABEL}</td>
                <td>
                  {engagementTypeNames.get(budget.engagement_type_id) ??
                    unknownEntryLabel("engagement-types")}
                </td>
                <td>{formatBudgetDaysString(budget.budget_days)}</td>
                <td>{budget.source}</td>
                <td>{formatEffectivePeriod(budget.effective_from, budget.effective_to)}</td>
                <td>
                  {STATUTORY_LEAVE_LABELS[budget.statutory_leave_state]}
                  {budget.statutory_leave !== null ? ` — ${budget.statutory_leave.name}` : ""}
                </td>
                <td>{budgetRegimeFlagLabel(budget.generates_cost)}</td>
                <td>{budgetRegimeFlagLabel(budget.generates_revenue)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
