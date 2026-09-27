import { render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AbsenceBudgetEntry, WorkingCalendarEntry } from "../../api/contracts/catalog";
import { WorkingCalendarsScreen } from "./WorkingCalendarsScreen";

/**
 * SC-3-06 (Issue #140), criteria K-01..K-03 and the 403 criterion — the Working calendars screen's
 * read side. K-04..K-06 (the write form) are in `WorkingCalendarsWrite.test.tsx`; reachability from
 * the running application is in `App.test.tsx`.
 */

const ENGAGEMENT_TYPE_ID = "d0000000-0000-0000-0000-000000000001";
const ENGAGEMENT_TYPE_NAME = "Time & materials";

/** K-01's own fixture pair: identical in every field except `week_pattern`, which inverts exactly
 * Monday and Saturday and leaves the other five weekdays unchanged. */
const CALENDAR_STANDARD: WorkingCalendarEntry = {
  id: "c0000000-0000-0000-0000-000000000001",
  name: "Standard week",
  standard_hours_per_day: "8.00",
  week_pattern: "1111100",
  days: [
    { day: "2026-12-24", kind: "non_working" },
    { day: "2026-01-02", kind: "working" },
  ],
  updated_at: "2026-01-01T00:00:00+00:00",
};

const CALENDAR_SHIFTED: WorkingCalendarEntry = {
  id: "c0000000-0000-0000-0000-000000000002",
  name: "Shifted week",
  standard_hours_per_day: "7.50",
  week_pattern: "0111110",
  days: [],
  updated_at: "2026-01-01T00:00:00+00:00",
};

const BUDGET_RESOLVED: AbsenceBudgetEntry = {
  id: "b0000000-0000-0000-0000-000000000001",
  calendar_id: CALENDAR_STANDARD.id,
  engagement_type_id: ENGAGEMENT_TYPE_ID,
  budget_days: "20.00",
  unit: "day",
  source: "Staff regulations §12, 2026 edition",
  effective_from: "2026-01-01",
  effective_to: "2026-12-31",
  statutory_leave_state: "resolved",
  statutory_leave: {
    absence_type_id: "a0000000-0000-0000-0000-000000000001",
    name: "Annual leave",
    generates_cost: true,
    generates_revenue: false,
  },
  generates_cost: true,
  generates_revenue: false,
  updated_at: "2026-01-01T00:00:00+00:00",
};

const BUDGET_NO_STATUTORY_TYPE: AbsenceBudgetEntry = {
  id: "b0000000-0000-0000-0000-000000000002",
  calendar_id: CALENDAR_SHIFTED.id,
  engagement_type_id: ENGAGEMENT_TYPE_ID,
  budget_days: "10.00",
  unit: "day",
  source: "Provisional, no statutory type flagged yet",
  effective_from: "2026-01-01",
  effective_to: "2026-06-30",
  statutory_leave_state: "no_statutory_leave_type",
  statutory_leave: null,
  generates_cost: "n/a",
  generates_revenue: "n/a",
  updated_at: "2026-01-01T00:00:00+00:00",
};

function stubReads(options: {
  calendars?: WorkingCalendarEntry[];
  budgets?: AbsenceBudgetEntry[];
  status?: number;
}) {
  const { calendars = [], budgets = [], status } = options;
  const fetchMock = vi.fn(async (url: string) => {
    if (status !== undefined) {
      return { ok: false, status };
    }
    const path = new URL(url).pathname;
    if (path === "/catalog/working-calendars") {
      return { ok: true, status: 200, json: async () => ({ calendars }) };
    }
    if (path === "/catalog/absence-budgets") {
      return { ok: true, status: 200, json: async () => ({ budgets }) };
    }
    if (path === "/catalog/dimensions/engagement-types") {
      return {
        ok: true,
        status: 200,
        json: async () => ({
          entries: [
            {
              id: ENGAGEMENT_TYPE_ID,
              name: ENGAGEMENT_TYPE_NAME,
              updated_at: "2026-01-01T00:00:00+00:00",
            },
          ],
        }),
      };
    }
    throw new Error(`unexpected path: ${path}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

async function mounted(): Promise<void> {
  await screen.findByRole("heading", { name: "Standard week", level: 4 });
}

function screenText(): string {
  return document.body.textContent ?? "";
}

describe("WorkingCalendarsScreen", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  // --- K-01 --------------------------------------------------------------------------------------

  it("renders week_pattern as named working/non-working weekdays, Monday first, never as the raw string", async () => {
    stubReads({ calendars: [CALENDAR_STANDARD, CALENDAR_SHIFTED] });
    render(<WorkingCalendarsScreen />);
    await mounted();

    const standardCard = screen.getByRole("heading", { name: "Standard week" }).closest("article");
    const shiftedCard = screen.getByRole("heading", { name: "Shifted week" }).closest("article");
    expect(standardCard).not.toBeNull();
    expect(shiftedCard).not.toBeNull();

    // Standard week: "1111100" — Monday through Friday working, Saturday and Sunday not.
    for (const weekday of ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]) {
      expect(within(standardCard as HTMLElement).getByText(`${weekday}: Working`)).toBeVisible();
    }
    for (const weekday of ["Saturday", "Sunday"]) {
      expect(within(standardCard as HTMLElement).getByText(`${weekday}: Non-working`)).toBeVisible();
    }

    // Shifted week: "0111110" — the contrast. Exactly Monday and Saturday invert; the rest do not.
    expect(within(shiftedCard as HTMLElement).getByText("Monday: Non-working")).toBeVisible();
    for (const weekday of ["Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]) {
      expect(within(shiftedCard as HTMLElement).getByText(`${weekday}: Working`)).toBeVisible();
    }
    expect(within(shiftedCard as HTMLElement).getByText("Sunday: Non-working")).toBeVisible();

    // Never the raw pattern string, on either card.
    expect(screenText()).not.toContain("1111100");
    expect(screenText()).not.toContain("0111110");
  });

  // --- K-02 --------------------------------------------------------------------------------------

  it("renders each exceptional day's kind as its own text, pinned to its own date", async () => {
    stubReads({ calendars: [CALENDAR_STANDARD] });
    render(<WorkingCalendarsScreen />);
    await mounted();

    expect(screen.getByText(/2026-12-24 — Non-working \(exception\)/)).toBeVisible();
    expect(screen.getByText(/2026-01-02 — Working \(exception\)/)).toBeVisible();
    // Never the raw enum values.
    expect(screenText()).not.toMatch(/\bnon_working\b/);
    // "working" alone (not "Working (exception)"/"Working:") would also be the raw enum leaking —
    // checked as the exact word, not as a substring of a label this screen does intend to show.
    expect(screenText()).not.toMatch(/[^-\s]working\b(?! \(exception\)|:)/);
  });

  it("names a calendar with no exceptional days as having none, rather than an empty list", async () => {
    stubReads({ calendars: [CALENDAR_SHIFTED] });
    render(<WorkingCalendarsScreen />);
    await screen.findByRole("heading", { name: "Shifted week", level: 4 });

    expect(
      screen.getByText("No exceptional days are configured for this calendar."),
    ).toBeVisible();
  });

  // --- K-03 --------------------------------------------------------------------------------------

  it("renders a resolved statutory-leave regime as Yes/No, and no_statutory_leave_type as Not applicable — never false or a blank cell", async () => {
    stubReads({
      calendars: [CALENDAR_STANDARD, CALENDAR_SHIFTED],
      budgets: [BUDGET_RESOLVED, BUDGET_NO_STATUTORY_TYPE],
    });
    render(<WorkingCalendarsScreen />);
    await mounted();

    const table = await screen.findByRole("table", { name: /leave budgets/i });
    const rows = within(table).getAllByRole("row");
    // Header row + two budget rows.
    expect(rows).toHaveLength(3);

    const resolvedRow = rows[1];
    const cells = within(resolvedRow).getAllByRole("cell");
    // Statutory-leave regime, generates cost, generates revenue are the last three columns.
    expect(cells[5].textContent).toContain("Resolved against a named statutory-leave type");
    expect(cells[5].textContent).toContain("Annual leave");
    expect(cells[6].textContent).toBe("Yes");
    expect(cells[7].textContent).toBe("No");

    const unresolvedRow = rows[2];
    const unresolvedCells = within(unresolvedRow).getAllByRole("cell");
    expect(unresolvedCells[5].textContent).toBe(
      "No absence type in the catalogue is flagged as statutory leave",
    );
    // Never false, never a boolean-ish word, never blank — the sentinel is a named phrase.
    expect(unresolvedCells[6].textContent).toBe("Not applicable");
    expect(unresolvedCells[7].textContent).toBe("Not applicable");
    for (const cell of [unresolvedCells[6], unresolvedCells[7]]) {
      expect(cell.textContent).not.toBe("");
      expect(cell.textContent?.toLowerCase()).not.toBe("false");
      expect(cell.textContent?.toLowerCase()).not.toBe("no");
      expect(cell.textContent?.toLowerCase()).not.toBe("yes");
      expect(cell.textContent).not.toBe("n/a");
    }
  });

  // --- Criterion 3 (Issue #140) --------------------------------------------------------------------

  it("renders one recognisable 'unavailable' state on a denied read, with no data and no controls", async () => {
    stubReads({ status: 403 });
    render(<WorkingCalendarsScreen />);

    expect(
      await screen.findByText("You do not have permission to view working calendars and leave budgets."),
    ).toBeVisible();
    expect(screen.queryByRole("heading", { name: "Calendars" })).toBeNull();
    expect(screen.queryByRole("heading", { name: "Leave budgets" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Add leave budget" })).toBeNull();
    expect(screen.queryByRole("table")).toBeNull();
  });

  // --- Reviewer R-01 of SC-3-06 (originally R-01/R-06 of SC-2-04) --------------------------------
  // `readScreen`'s own comment claims it "mirrors `CatalogScreen.tsx`'s `readCatalogue`" for exactly
  // this reason: `Promise.all` plus `controller.abort()` on the first rejection. Nothing here
  // exercised either half before this fix — both tests below are adapted from
  // `CatalogScreen.test.tsx`'s "aborts all six reads when the screen unmounts before they settle"
  // and "aborts the other five reads as soon as one of the six rejects", narrowed from six reads to
  // this screen's three (`getWorkingCalendars`, `getCatalogAbsenceBudgets`, the coalesced
  // `getCatalogDimension("engagement-types")`).

  it("aborts all three reads when the screen unmounts before they settle, and never sets state afterwards", async () => {
    // A `fetch` that only ever resolves once its own signal aborts — the shape a hung or slow read
    // has in the running application, and the one case that shows whether the abort actually
    // reaches the network layer rather than only the component's own `left` flag.
    const fetchMock = vi.fn((_url: string, init?: RequestInit) => {
      return new Promise<never>((_resolve, reject) => {
        init?.signal?.addEventListener("abort", () => {
          const error = new Error("aborted");
          error.name = "AbortError";
          reject(error);
        });
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    const { container, unmount } = render(<WorkingCalendarsScreen />);
    expect(fetchMock).toHaveBeenCalledTimes(3);
    const signals = fetchMock.mock.calls.map(
      ([, init]) => (init as RequestInit | undefined)?.signal as AbortSignal,
    );
    expect(signals.every((signal) => signal.aborted)).toBe(false);

    unmount();

    // Every one of the three reads carried a signal, and every one of them is now aborted — a
    // screen bounced away from cannot leave any of its three requests still occupying a socket.
    expect(signals).toHaveLength(3);
    expect(signals.every((signal) => signal.aborted)).toBe(true);

    // Let the abort's rejection settle, then check that nothing this screen would have rendered
    // reappears — not the component's own container (already removed by `unmount`), and no failure
    // or loading text leaked past it either. `render`/`unmount` here run inside Testing Library's own
    // `act`, so a `setState` on the unmounted component would have surfaced as a React warning; the
    // absence of any rendered content is the observable half of that same guarantee.
    await Promise.resolve();
    await Promise.resolve();
    expect(container.textContent).toBe("");
    expect(screen.queryByText("The working calendars could not be loaded.")).toBeNull();
    expect(screen.queryByText("Loading working calendars…")).toBeNull();
  });

  it("aborts the other two reads as soon as one of the three rejects, without waiting for the screen to unmount", async () => {
    // The absence-budgets read fails fast (a real 500, not an abort); the other two hang until
    // their own signal aborts — the same shape as the fixture above, but nothing here ever unmounts
    // the screen. If the fix regresses to "only the cleanup aborts", this test hangs instead of
    // failing false-green, because nothing else would ever settle these two promises.
    const pendingSignals: AbortSignal[] = [];
    const fetchMock = vi.fn((url: string, init?: RequestInit) => {
      const path = new URL(url).pathname;
      if (path === "/catalog/absence-budgets") {
        return Promise.resolve({ ok: false, status: 500 });
      }
      const signal = init?.signal as AbortSignal;
      pendingSignals.push(signal);
      return new Promise<never>((_resolve, reject) => {
        signal?.addEventListener("abort", () => {
          const error = new Error("aborted");
          error.name = "AbortError";
          reject(error);
        });
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<WorkingCalendarsScreen />);

    // The genuine failure is still what renders — aborting the rest must not turn into a second,
    // different outcome (and, in particular, never the "denied" branch: `toFailureState` never sees
    // an `AbortError` for the rejection that actually decided this), and the screen never shows
    // partial data from the two reads that did not fail.
    expect(await screen.findByText("The working calendars could not be loaded.")).toBeVisible();
    expect(
      screen.queryByText("You do not have permission to view working calendars and leave budgets."),
    ).toBeNull();
    expect(screen.queryByRole("table")).toBeNull();
    expect(screen.queryByRole("heading", { name: "Calendars" })).toBeNull();

    // The screen is still mounted — nothing here ever called `unmount` — and yet both of the other
    // reads have their signal aborted, because the rejection of the third stopped them on its own.
    expect(pendingSignals).toHaveLength(2);
    expect(pendingSignals.every((signal) => signal.aborted)).toBe(true);
  });
});
