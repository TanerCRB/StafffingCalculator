import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AbsenceBudgetEntry, WorkingCalendarEntry } from "../../api/contracts/catalog";
import { REFUSAL_SQLSTATE } from "../../api/contracts/writeRefusals";
import { WorkingCalendarsScreen } from "./WorkingCalendarsScreen";
import { SAVE_REFUSED_BROKEN_RULE, SAVE_REFUSED_OVERLAP } from "./writeOutcome";

/**
 * SC-3-06 (Issue #140), criteria K-04..K-06 — the leave-budget write form. Mirrors
 * `CatalogWrite.test.tsx`'s stubbing convention: every test reads the *request* a stub `fetch`
 * recorded, because the criteria are about what is sent and about what replaces it on screen, not
 * about a convincing render.
 */

const CALENDAR: WorkingCalendarEntry = {
  id: "c0000000-0000-0000-0000-000000000001",
  name: "Standard week",
  standard_hours_per_day: "8.00",
  week_pattern: "1111100",
  days: [],
  updated_at: "2026-01-01T00:00:00+00:00",
};

const ENGAGEMENT_TYPE_ID = "d0000000-0000-0000-0000-000000000001";
const ENGAGEMENT_TYPE_NAME = "Time & materials";

interface Recorded {
  readonly path: string;
  readonly method: string;
  readonly body: Record<string, unknown> | undefined;
}

interface WriteAnswer {
  readonly status: number;
  readonly detail?: string;
  readonly payload?: unknown;
}

function stub(options: {
  budgets?: AbsenceBudgetEntry[];
  budgetsAfterWrite?: AbsenceBudgetEntry[];
  answerWrite?: (call: Recorded) => WriteAnswer;
}) {
  const calls: Recorded[] = [];
  let written = false;

  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    const path = new URL(url).pathname;
    const method = init?.method ?? "GET";
    const raw = typeof init?.body === "string" ? init.body : undefined;
    const call: Recorded = {
      path,
      method,
      body: raw === undefined ? undefined : (JSON.parse(raw) as Record<string, unknown>),
    };
    calls.push(call);

    if (method !== "GET") {
      written = true;
      const answer = options.answerWrite?.(call) ?? {
        status: 201,
        payload: { ...options.budgets?.[0], id: "write-echo-never-rendered" },
      };
      if (answer.status >= 400) {
        return { ok: false, status: answer.status, json: async () => ({ detail: answer.detail }) };
      }
      return { ok: true, status: answer.status, json: async () => answer.payload };
    }

    if (path === "/catalog/working-calendars") {
      return { ok: true, status: 200, json: async () => ({ calendars: [CALENDAR] }) };
    }
    if (path === "/catalog/absence-budgets") {
      const budgets =
        written && options.budgetsAfterWrite !== undefined
          ? options.budgetsAfterWrite
          : (options.budgets ?? []);
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
  return calls;
}

function writes(calls: readonly Recorded[]): Recorded[] {
  return calls.filter((call) => call.method !== "GET");
}

async function mounted(): Promise<void> {
  await screen.findByRole("heading", { name: "Standard week", level: 4 });
}

async function openForm(): Promise<HTMLElement> {
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "Add leave budget" }));
  });
  return screen.getByRole("form", { name: "Add a leave budget" });
}

function set(form: HTMLElement, label: string | RegExp, value: string): void {
  fireEvent.change(within(form).getByLabelText(label), { target: { value } });
}

function fillBudgetForm(
  form: HTMLElement,
  values: {
    budgetDays?: string;
    source?: string;
    from?: string;
    to?: string;
  } = {},
): void {
  set(form, "Calendar", CALENDAR.id);
  set(form, "Engagement type", ENGAGEMENT_TYPE_ID);
  set(form, "Budget (days)", values.budgetDays ?? "20.00");
  set(form, "Source of this figure", values.source ?? "Staff regulations §12, 2026 edition");
  set(form, "Effective from", values.from ?? "2026-01-01");
  set(form, "Effective to", values.to ?? "2026-12-31");
}

async function submit(form: HTMLElement): Promise<void> {
  await act(async () => {
    fireEvent.click(within(form).getByRole("button", { name: "Save new budget" }));
  });
}

function screenText(): string {
  return document.body.textContent ?? "";
}

describe("writing a leave budget", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  // --- K-04 --------------------------------------------------------------------------------------

  it("sends exactly one POST per submit and then shows what a fresh read answered, never the write's own response body or the typed values", async () => {
    const writeEcho: AbsenceBudgetEntry = {
      id: "b0000000-0000-0000-0000-0000000000ff",
      calendar_id: CALENDAR.id,
      engagement_type_id: ENGAGEMENT_TYPE_ID,
      budget_days: "999.99",
      unit: "day",
      source: "ECHOED BY THE WRITE, NEVER RENDERED",
      effective_from: "2001-01-01",
      effective_to: "2001-12-31",
      statutory_leave_state: "no_statutory_leave_type",
      statutory_leave: null,
      generates_cost: "n/a",
      generates_revenue: "n/a",
      updated_at: "2026-09-27T08:00:00+00:00",
    };
    const afterWrite: AbsenceBudgetEntry = {
      ...writeEcho,
      id: "b0000000-0000-0000-0000-000000000002",
      budget_days: "15.50",
      source: "FROM THE FRESH READ",
      effective_from: "2026-01-01",
      effective_to: "2026-12-31",
      generates_cost: "n/a",
      generates_revenue: "n/a",
    };

    const calls = stub({
      budgets: [],
      budgetsAfterWrite: [afterWrite],
      answerWrite: () => ({ status: 201, payload: writeEcho }),
    });
    render(<WorkingCalendarsScreen />);
    await mounted();

    const form = await openForm();
    fillBudgetForm(form, { budgetDays: "12.34", source: "Typed value, never shown" });
    await submit(form);

    // Exactly one write.
    expect(writes(calls)).toHaveLength(1);
    expect(writes(calls)[0]).toMatchObject({ method: "POST", path: "/catalog/absence-budgets" });

    // What is on screen is the re-read's answer …
    expect(await screen.findByText("15.50 days")).toBeVisible();
    expect(screen.getByText("FROM THE FRESH READ")).toBeVisible();
    // … never the write's own response body …
    expect(screenText()).not.toContain("999.99");
    expect(screenText()).not.toContain("ECHOED BY THE WRITE");
    expect(screenText()).not.toContain("2001-01-01");
    // … and never what was typed into the form.
    expect(screenText()).not.toContain("12.34");
    expect(screenText()).not.toContain("Typed value, never shown");

    // The save is stated, in its own words.
    expect(screen.getByText(/Saved\./)).toBeVisible();
  });

  // --- K-05 --------------------------------------------------------------------------------------

  it("sends a misaligned window the server rejects rather than blocking it client-side, and renders the resulting refusal", async () => {
    // The detail carries the SQLSTATE the backend's CHECK constraint raises
    // (`ck_absence_budget_window_aligned_to_whole_months`, ADR-0008 addendum SC-3-03 point 10) — the
    // same identifier-carrying shape `CatalogWrite.test.tsx`'s equivalent tests stub, read through
    // this screen's own `describeWriteFailure` pipeline (`writeOutcome.ts`), not a message this
    // form invents from the raw text.
    const detail =
      "Refused by the database. Writing the absence budget failed: IntegrityError, " +
      `sqlstate=${REFUSAL_SQLSTATE.checkViolation}, ` +
      "constraint=ck_absence_budget_window_aligned_to_whole_months. It violates a check " +
      "constraint.";
    const calls = stub({
      budgets: [],
      answerWrite: () => ({ status: 409, detail }),
    });
    render(<WorkingCalendarsScreen />);
    await mounted();

    const form = await openForm();
    // A window that starts mid-month — misaligned, exactly the case the database (not this form)
    // refuses (ADR-0008, addendum SC-3-03, point 10).
    fillBudgetForm(form, { from: "2026-02-15", to: "2026-06-30" });
    await submit(form);

    // The request was sent regardless — no client-side pre-check swallowed the submit, and it
    // carries the exact (misaligned) dates that were typed, not a corrected pair.
    expect(writes(calls)).toHaveLength(1);
    expect(writes(calls)[0].body).toMatchObject({
      effective_from: "2026-02-15",
      effective_to: "2026-06-30",
    });

    // And the rendered ending is the server's refusal, read through this codebase's one refusal
    // classifier — not a generic "could not save" and not a client-invented validation message.
    expect(screen.getByRole("alert").textContent).toBe(SAVE_REFUSED_BROKEN_RULE);
  });

  it("sends an overlapping window rather than pre-checking it client-side, exactly as the equivalent catalogue-rate form does", async () => {
    const detail =
      "Refused by the database. Writing the absence budget failed: IntegrityError, " +
      `sqlstate=${REFUSAL_SQLSTATE.exclusionViolation}, ` +
      "constraint=ex_absence_budget_no_overlapping_periods. It overlaps an existing row for the " +
      "same (calendar, engagement type) pair (exclusion constraint).";
    const calls = stub({
      budgets: [],
      answerWrite: () => ({ status: 409, detail }),
    });
    render(<WorkingCalendarsScreen />);
    await mounted();

    const form = await openForm();
    fillBudgetForm(form);
    await submit(form);

    expect(writes(calls)).toHaveLength(1);
    expect(screen.getByRole("alert").textContent).toBe(SAVE_REFUSED_OVERLAP);
  });

  // --- K-06 --------------------------------------------------------------------------------------

  it("describes the source field as where the figure comes from, never as who entered it, and offers no separate author field", async () => {
    stub({ budgets: [] });
    render(<WorkingCalendarsScreen />);
    await mounted();

    const form = await openForm();

    // Exactly one control about the number's provenance.
    const sourceControl = within(form).getByLabelText("Source of this figure");
    expect(sourceControl.tagName).toBe("INPUT");

    // Neither the label nor the placeholder — nor anything else in the form — names a person.
    const forbidden = /\b(author|entered by|who|approved by)\b/i;
    expect("Source of this figure").not.toMatch(forbidden);
    expect(sourceControl.getAttribute("placeholder") ?? "").not.toMatch(forbidden);
    for (const text of Array.from(form.querySelectorAll("label")).map((label) => label.textContent ?? "")) {
      expect(text).not.toMatch(forbidden);
    }

    // No dead or hidden field claiming to record a person either.
    expect(within(form).queryByLabelText(/author/i)).toBeNull();
    expect(within(form).queryByLabelText(/entered by/i)).toBeNull();
    expect(within(form).queryByLabelText(/approved by/i)).toBeNull();
  });
});
