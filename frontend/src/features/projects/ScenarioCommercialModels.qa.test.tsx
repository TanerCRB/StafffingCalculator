import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { App } from "../../App";
import type { ProjectListItem } from "../../api/contracts/projects";
import { SCREEN_CRASH_MESSAGE } from "../../shell/ScreenErrorBoundary";
import { COMMERCIAL_MODEL_LABEL, NO_RULE, READ_UNREADABLE, REVENUE_LABEL, REVENUE_STATE_MESSAGES } from "./commercialTermsText";
import { RESULTS_UNREADABLE } from "./scenarioResultsText";

/**
 * QA, SC-4-07 (Issue #125) — contrast tests for two claims of the closed frontend contract
 * (ADR-0003, addendum 2026-09-25 SC-4-07, points 4, 5a and 11) that `ScenarioCommercialModels.test.tsx`
 * left without a mutation that dies:
 *
 *   * a stored model outside the closed set is readable **only** as the named `unsupported_model_type`
 *     state — not under any other withheld state (widening the exception to "any state but
 *     calculated" survived the developer's suite: its only negative case was a *calculated* unknown
 *     model, which the widened check still refuses);
 *   * `outcome_terms` is a **required** field, `null` included — a rule payload that omits the key is
 *     not a rule payload stating "no parameters" (`== null` instead of `=== null` survived).
 *
 * Each pair differs in exactly one element, and the result reverses.
 */

const SCENARIO = "bbbbbbbb-0000-0000-0000-00000000000a";

const PROJECT: ProjectListItem = {
  id: "22222222-2222-2222-2222-222222222222",
  name: "Borealis rollout",
  client: "Northwind",
  delivery_period: { start: "2026-01-01", end: "2026-12-31" },
  reporting_currency: "EUR",
  description: "Rollout.",
  status: "Active",
  scenarios: [
    { id: SCENARIO, name: "Unknown deal", status: "Draft", missing_inputs: [], ready_for_approval: false, target_margin_percent: null },
  ],
};

/** The catalogue sources the backend's dispatcher gives a model it cannot price (`revenue_of`). */
function unknownModelRevenue(state: string) {
  return {
    state,
    amount: "n/a",
    currency: null,
    assumptions_used: {
      model_type: "future_model",
      hours_source: "billable_hours",
      vendor_axis: "internal",
      rate_source: "live_catalog",
      rate_windows: [],
      unresolved_months: [],
      currencies: [],
    },
    expected_state: "not_applicable",
    expected_amount: "n/a",
    category_revenues: [],
  };
}

function termsBody(commercialTerms: Record<string, unknown>, revenue: Record<string, unknown>) {
  return { scenario_id: SCENARIO, scenario_status: "Draft", commercial_terms: commercialTerms, revenue };
}

function resultsBody(revenue: Record<string, unknown>) {
  return {
    scenario_id: SCENARIO,
    scenario_status: "Draft",
    revenue,
    personnel_cost: {
      state: "calculated",
      amount: "15000.00",
      currency: "PLN",
      assumptions_used: { rate_windows: [] },
      paid_absence_state: "calculated",
      paid_absence_amount: "0.00",
      paid_absence_currency: "PLN",
    },
    additional_cost: { state: "calculated", amount: "0.00", currency: "PLN" },
    included_cost: "n/a",
    profit: "n/a",
    margin: "n/a",
    markup: "n/a",
    profitability_state: "not_applicable",
  };
}

const RULE = { id: "eeeeeeee-0000-0000-0000-000000000001", updated_at: "2026-09-25T10:00:00Z" };

function stubBackend(terms: unknown, results?: unknown) {
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string, init: RequestInit = {}) => {
      const path = new URL(url).pathname;
      let body: unknown;
      if (path === "/health") {
        body = { status: "ok" };
      } else if (path === "/projects") {
        body = { projects: [PROJECT] };
      } else if (path === "/catalog/rates") {
        body = { rates: [], total: 0 };
      } else if (path.startsWith("/catalog/dimensions/")) {
        body = { entries: [] };
      } else if (path.endsWith("/commercial-terms") && (init.method ?? "GET") === "GET") {
        body = terms;
      } else if (path.endsWith("/results")) {
        body = results;
      }
      if (body === undefined) {
        return new Promise((_resolve, reject) => {
          init.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
        });
      }
      return Promise.resolve({ ok: true, status: 200, json: async () => body });
    }),
  );
}

async function openSection(name: "Commercial terms" | "Scenario results", loading: string): Promise<HTMLElement> {
  const find = () => {
    const item = screen.getByRole("heading", { name: "Unknown deal" }).closest("li");
    if (item === null) {
      throw new Error("no card");
    }
    return within(item).getByRole("region", { name });
  };
  await waitFor(() => expect(within(find()).queryByText(loading)).toBeNull());
  return find();
}

async function openProject() {
  render(<App />);
  fireEvent.click(await within(screen.getByRole("main")).findByRole("button", { name: "Borealis rollout" }));
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("QA SC-4-07 — an unknown stored model is readable only as the named unsupported_model_type state", () => {
  it("reads an unknown model under the unsupported_model_type state as that named state, in both sections", async () => {
    const revenue = unknownModelRevenue("unsupported_model_type");
    stubBackend(termsBody({ ...RULE, model_type: "future_model", outcome_terms: null }, revenue), resultsBody(revenue));

    await openProject();
    const rules = await openSection("Commercial terms", "Loading commercial terms.");
    const figures = await openSection("Scenario results", "Loading scenario results.");

    expect(within(rules).getByText(REVENUE_STATE_MESSAGES.unsupported_model_type)).toBeVisible();
    expect(within(rules).queryByText(READ_UNREADABLE)).toBeNull();
    expect(within(figures).getByText(REVENUE_STATE_MESSAGES.unsupported_model_type)).toBeVisible();
    expect(within(figures).queryByText(RESULTS_UNREADABLE)).toBeNull();
  });

  it("reads the same unknown model under a currency_mismatch state as unreadable in both sections — only the state differs", async () => {
    const revenue = unknownModelRevenue("currency_mismatch");
    stubBackend(termsBody({ ...RULE, model_type: "future_model", outcome_terms: null }, revenue), resultsBody(revenue));

    await openProject();
    const rules = await openSection("Commercial terms", "Loading commercial terms.");
    const figures = await openSection("Scenario results", "Loading scenario results.");

    expect(within(rules).getByText(READ_UNREADABLE)).toBeVisible();
    // The catalogue sentence would name selling rates of a model this client cannot read.
    expect(within(rules).queryByText(REVENUE_STATE_MESSAGES.currency_mismatch)).toBeNull();
    expect(within(figures).getByText(RESULTS_UNREADABLE)).toBeVisible();
    expect(screen.queryByText(SCREEN_CRASH_MESSAGE)).toBeNull();
  });
});

describe("QA SC-4-07 — outcome_terms is a required field: null states absence, a missing key is not a payload", () => {
  const pointsRevenue = {
    state: "calculated",
    amount: "25000.00",
    currency: "PLN",
    assumptions_used: {
      model_type: "story_points",
      hours_source: "not_applicable",
      vendor_axis: "not_applicable",
      rate_source: "story_points_terms",
      rate_windows: [],
      unresolved_months: [],
      currencies: ["PLN"],
    },
    expected_state: "not_applicable",
    expected_amount: "n/a",
    category_revenues: [],
  };

  it("reads a rule whose outcome_terms is null", async () => {
    stubBackend(termsBody({ ...RULE, model_type: "story_points", outcome_terms: null }, pointsRevenue));

    await openProject();
    const rules = await openSection("Commercial terms", "Loading commercial terms.");

    expect(within(rules).getByText("Revenue: 25000.00 PLN")).toBeVisible();
    expect(within(rules).queryByText(READ_UNREADABLE)).toBeNull();
  });

  it("reads the same rule without the outcome_terms key as unreadable — only the key differs", async () => {
    stubBackend(termsBody({ ...RULE, model_type: "story_points" }, pointsRevenue));

    await openProject();
    const rules = await openSection("Commercial terms", "Loading commercial terms.");

    expect(within(rules).getByText(READ_UNREADABLE)).toBeVisible();
    expect(rules.textContent).not.toContain("25000.00");
    expect(screen.queryByText(SCREEN_CRASH_MESSAGE)).toBeNull();
  });
});

// --- QA SC-4-07, verification round 2 (2026-09-25): R-01 and R-03 ---------------------------------
//
// Two mutations of the round-1 fixes survived the suite as it stood:
//
//   * `isRevenueSourcePairing` accepting `unsupported_model_type` with **any** sources (`return true`
//     in place of `return catalogueSources`) — every existing case of the state carried catalogue
//     sources, so nothing showed that the exception is narrow (ADR-0003, addendum SC-4-07, point 4:
//     the state carries the status values `live_catalog`/`approved_snapshot`, `billable_hours`,
//     `internal`). Accepted, the model's own sources would be cast to catalogue ones by
//     `catalogAssumptionsOf` and rendered as a rate-source line with no label.
//   * the R-03 pairing weakened to one side (`revenueModel === null || ...`, `ruleModel === null || ...`)
//     — every mismatch case had a model word on both sides, so the `null` half of the claim ("`null`
//     on both sides when there is no rule") was never forced.
//
// Each pair below differs in exactly one element, and the result reverses.

function assumptions(modelType: string | null, sources: Record<string, string>) {
  return { model_type: modelType, ...sources, rate_windows: [], unresolved_months: [], currencies: [] };
}

const CATALOGUE_SOURCES = { hours_source: "billable_hours", vendor_axis: "internal", rate_source: "live_catalog" };
const STORY_POINTS_SOURCES = { hours_source: "not_applicable", vendor_axis: "not_applicable", rate_source: "story_points_terms" };

function withheldOf(state: string, used: Record<string, unknown>) {
  return { state, amount: "n/a", currency: null, assumptions_used: used, expected_state: "not_applicable", expected_amount: "n/a", category_revenues: [] };
}

function calculatedOf(used: Record<string, unknown>) {
  return {
    state: "calculated",
    amount: "1000.00",
    currency: "PLN",
    assumptions_used: { ...used, currencies: ["PLN"] },
    expected_state: "not_applicable",
    expected_amount: "n/a",
    category_revenues: [],
  };
}

describe("QA SC-4-07 round 2 — unsupported_model_type is readable only with the status (catalogue) sources", () => {
  it("reads unsupported_model_type for story_points with the catalogue sources as the named state", async () => {
    const revenue = withheldOf("unsupported_model_type", assumptions("story_points", CATALOGUE_SOURCES));
    stubBackend(termsBody({ ...RULE, model_type: "story_points", outcome_terms: null }, revenue), resultsBody(revenue));

    await openProject();
    const rules = await openSection("Commercial terms", "Loading commercial terms.");
    const figures = await openSection("Scenario results", "Loading scenario results.");

    expect(within(rules).getByText(REVENUE_STATE_MESSAGES.unsupported_model_type)).toBeVisible();
    expect(within(rules).queryByText(READ_UNREADABLE)).toBeNull();
    expect(within(figures).getByText(REVENUE_STATE_MESSAGES.unsupported_model_type)).toBeVisible();
    expect(within(figures).queryByText(RESULTS_UNREADABLE)).toBeNull();
  });

  it("reads the same state with the model's own story points sources as unreadable in both sections — only the sources differ", async () => {
    const revenue = withheldOf("unsupported_model_type", assumptions("story_points", STORY_POINTS_SOURCES));
    stubBackend(termsBody({ ...RULE, model_type: "story_points", outcome_terms: null }, revenue), resultsBody(revenue));

    await openProject();
    const rules = await openSection("Commercial terms", "Loading commercial terms.");
    const figures = await openSection("Scenario results", "Loading scenario results.");

    expect(within(rules).getByText(READ_UNREADABLE)).toBeVisible();
    expect(within(rules).queryByText(REVENUE_STATE_MESSAGES.unsupported_model_type)).toBeNull();
    expect(within(figures).getByText(RESULTS_UNREADABLE)).toBeVisible();
    expect(within(figures).queryByText(REVENUE_STATE_MESSAGES.unsupported_model_type)).toBeNull();
    expect(screen.queryByText(SCREEN_CRASH_MESSAGE)).toBeNull();
  });
});

describe("QA SC-4-07 round 2 — R-03: null on one side and a model word on the other is not one model", () => {
  it("reads a time & material rule with a time & material revenue — the contrast", async () => {
    stubBackend(
      termsBody({ ...RULE, model_type: "time_and_material", outcome_terms: null }, calculatedOf(assumptions("time_and_material", CATALOGUE_SOURCES))),
    );

    await openProject();
    const rules = await openSection("Commercial terms", "Loading commercial terms.");

    expect(within(rules).getByText(`${REVENUE_LABEL} 1000.00 PLN`)).toBeVisible();
    expect(within(rules).queryByText(READ_UNREADABLE)).toBeNull();
  });

  it("reads the same answer with the revenue's model_type null as unreadable — only the revenue's model_type differs", async () => {
    stubBackend(
      termsBody({ ...RULE, model_type: "time_and_material", outcome_terms: null }, calculatedOf(assumptions(null, CATALOGUE_SOURCES))),
    );

    await openProject();
    const rules = await openSection("Commercial terms", "Loading commercial terms.");

    expect(within(rules).getByText(READ_UNREADABLE)).toBeVisible();
    expect(rules.textContent).not.toContain("1000.00");
    expect(rules.textContent).not.toContain(COMMERCIAL_MODEL_LABEL);
  });

  it("reads a scenario without a rule whose revenue's model_type is null — the contrast", async () => {
    stubBackend({
      scenario_id: SCENARIO,
      scenario_status: "Draft",
      commercial_terms: null,
      revenue: withheldOf("no_commercial_terms", assumptions(null, CATALOGUE_SOURCES)),
    });

    await openProject();
    const rules = await openSection("Commercial terms", "Loading commercial terms.");

    expect(within(rules).getByText(`${COMMERCIAL_MODEL_LABEL} ${NO_RULE}`)).toBeVisible();
    expect(within(rules).queryByText(READ_UNREADABLE)).toBeNull();
  });

  it("reads the same answer with the revenue naming time_and_material as unreadable — only the revenue's model_type differs", async () => {
    stubBackend({
      scenario_id: SCENARIO,
      scenario_status: "Draft",
      commercial_terms: null,
      revenue: withheldOf("no_commercial_terms", assumptions("time_and_material", CATALOGUE_SOURCES)),
    });

    await openProject();
    const rules = await openSection("Commercial terms", "Loading commercial terms.");

    expect(within(rules).getByText(READ_UNREADABLE)).toBeVisible();
    expect(rules.textContent).not.toContain(COMMERCIAL_MODEL_LABEL);
    expect(within(rules).queryByRole("button", { name: /Set Time & Material/ })).toBeNull();
  });
});
