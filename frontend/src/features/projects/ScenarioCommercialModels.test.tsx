import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { App } from "../../App";
import type {
  CategoryRevenueRead,
  CommercialTermsRead,
  OutcomeTermsRead,
  RevenueAssumptionsRead,
  RevenueRead,
  ScenarioCommercialTerms,
} from "../../api/contracts/commercialTerms";
import type { ProjectListItem } from "../../api/contracts/projects";
import type { ScenarioResults } from "../../api/contracts/scenarioResults";
import { NOT_APPLICABLE } from "../../lib/money";
import { SCREEN_CRASH_MESSAGE } from "../../shell/ScreenErrorBoundary";
import {
  EXPECTED_NO_PROBABILITIES,
  NO_RATE_WINDOWS,
  OUTCOME_CATEGORY_LABELS,
  RATE_SOURCE_LABELS,
  READ_UNREADABLE,
  REVENUE_STATE_MESSAGES,
  RULE_CURRENCY_MISMATCH,
  RULE_SOURCE_LABELS,
  SAVED,
  SAVE_UNRESOLVED_UNREADABLE_ANSWER,
} from "./commercialTermsText";
import {
  ADDITIONAL_COST_STATE_MESSAGES,
  PERSONNEL_COST_STATE_MESSAGES,
  PROFITABILITY_CURRENCY_MISMATCH,
  RESULTS_FIELD_UNAVAILABLE,
  RESULTS_UNREADABLE,
} from "./scenarioResultsText";

/**
 * SC-4-07 (Issue #125) — Story Points and Outcome-based scenarios on the scenario card: both
 * sections (the commercial-terms one of SC-4-06 and the results one of SC-7-02) read them instead of
 * failing, and the frontend contract stays a closed set paired with `model_type` (ADR-0003, addendum
 * 2026-09-25 SC-4-07). One `describe` per criterion K-01..K-07 of gate 1.
 *
 * Every test runs the whole application (`<App/>`), so the render boundary `AppShell` mounts is really
 * there: a payload refused by the shape check must end as the section's named read failure, never as
 * the screen crash (ADR-0010, point 2).
 */

// --- Fixtures ------------------------------------------------------------------------------------

const OUTCOME = "aaaaaaaa-0000-0000-0000-00000000000a";
const POINTS = "aaaaaaaa-0000-0000-0000-00000000000b";
const HOURLY = "aaaaaaaa-0000-0000-0000-00000000000c";
const FIXED_PRICE = "aaaaaaaa-0000-0000-0000-00000000000d";

function scenario(id: string, name: string) {
  return { id, name, status: "Draft" as const, missing_inputs: [], ready_for_approval: false, target_margin_percent: null };
}

/** Reporting currency EUR on purpose: every revenue below is PLN, so a screen that took the
 * project's currency instead of the revenue's (or the rule's) would show it. */
const PROJECT: ProjectListItem = {
  id: "11111111-1111-1111-1111-111111111111",
  name: "Aurora migration",
  client: "Northwind",
  delivery_period: { start: "2026-01-01", end: "2026-12-31" },
  reporting_currency: "EUR",
  description: "Core platform migration.",
  status: "Active",
  scenarios: [scenario(OUTCOME, "Outcome deal"), scenario(POINTS, "Points deal"), scenario(HOURLY, "Hourly deal")],
};

/** The SC-4-03 fields of every revenue that is not a calculated Outcome-based one. */
const NOT_OUTCOME = {
  expected_state: "not_applicable" as const,
  expected_amount: "n/a",
  category_revenues: [] as CategoryRevenueRead[],
};

const TM_ASSUMPTIONS: RevenueAssumptionsRead = {
  model_type: "time_and_material",
  hours_source: "billable_hours",
  vendor_axis: "internal",
  rate_source: "live_catalog",
  rate_windows: [],
  unresolved_months: [],
  currencies: ["PLN"],
};

const SP_ASSUMPTIONS: RevenueAssumptionsRead = {
  model_type: "story_points",
  hours_source: "not_applicable",
  vendor_axis: "not_applicable",
  rate_source: "story_points_terms",
  rate_windows: [],
  unresolved_months: [],
  currencies: ["PLN"],
};

const OUTCOME_ASSUMPTIONS: RevenueAssumptionsRead = {
  model_type: "outcome_based",
  hours_source: "not_applicable",
  vendor_axis: "not_applicable",
  rate_source: "not_applicable",
  rate_windows: [],
  unresolved_months: [],
  currencies: ["PLN"],
};

const FIXED_PRICE_PROJECT: ProjectListItem = {
  ...PROJECT,
  scenarios: [...PROJECT.scenarios, scenario(FIXED_PRICE, "Fixed Price deal")],
};

const FIXED_PRICE_ASSUMPTIONS: RevenueAssumptionsRead = {
  model_type: "fixed_price",
  hours_source: "not_applicable",
  vendor_axis: "not_applicable",
  rate_source: "fixed_price_terms",
  rate_windows: [],
  unresolved_months: [],
  currencies: ["PLN"],
};

/** AC-08: fixed fee 20000 PLN, success bonus 10000 PLN (achieved and exceeded only), probabilities
 * 70 / 0 / 30 / 0 — guaranteed 20000, expected 0.7·20000 + 0.3·30000 = 23000. */
const AC_08_CATEGORIES: CategoryRevenueRead[] = [
  { category: "not_achieved", units: null, probability: "70.00", amount: "20000.00" },
  { category: "partial", units: null, probability: "0.00", amount: "20000.00" },
  { category: "achieved", units: null, probability: "30.00", amount: "30000.00" },
  { category: "exceeded", units: null, probability: "0.00", amount: "30000.00" },
];

const AC_08_TERMS: OutcomeTermsRead = {
  currency: "PLN",
  fixed_fee: "20000.0000",
  success_bonus: "10000.0000",
  unit_rate: null,
  revenue_min: null,
  revenue_max: null,
  categories: {
    not_achieved: { units: null, probability: "70.00" },
    partial: { units: null, probability: "0.00" },
    achieved: { units: null, probability: "30.00" },
    exceeded: { units: null, probability: "0.00" },
  },
};

/** The same rule without probabilities — what the backend stores behind a `no_probabilities` revenue. */
const NO_PROBABILITY_TERMS: OutcomeTermsRead = {
  ...AC_08_TERMS,
  categories: {
    not_achieved: { units: null, probability: null },
    partial: { units: null, probability: null },
    achieved: { units: null, probability: null },
    exceeded: { units: null, probability: null },
  },
};

function outcomeRevenue(overrides: Partial<Record<string, unknown>> = {}): RevenueRead {
  return {
    state: "calculated",
    amount: "20000.00",
    currency: "PLN",
    assumptions_used: OUTCOME_ASSUMPTIONS,
    expected_state: "calculated",
    expected_amount: "23000.00",
    category_revenues: AC_08_CATEGORIES,
    ...overrides,
  } as RevenueRead;
}

/** AC-09: a Story Points revenue of 25000 PLN. */
function pointsRevenue(overrides: Partial<Record<string, unknown>> = {}): RevenueRead {
  return {
    state: "calculated",
    amount: "25000.00",
    currency: "PLN",
    assumptions_used: SP_ASSUMPTIONS,
    ...NOT_OUTCOME,
    ...overrides,
  } as RevenueRead;
}

function hourlyRevenue(overrides: Partial<Record<string, unknown>> = {}): RevenueRead {
  return {
    state: "calculated",
    amount: "1000.00",
    currency: "PLN",
    assumptions_used: TM_ASSUMPTIONS,
    ...NOT_OUTCOME,
    ...overrides,
  } as RevenueRead;
}

function fixedPriceRevenue(overrides: Partial<Record<string, unknown>> = {}): RevenueRead {
  return {
    state: "calculated",
    amount: "150000.01",
    currency: "PLN",
    assumptions_used: FIXED_PRICE_ASSUMPTIONS,
    ...NOT_OUTCOME,
    ...overrides,
  } as RevenueRead;
}

function rule(modelType: string, outcomeTerms: OutcomeTermsRead | null = null): CommercialTermsRead {
  return {
    id: "dddddddd-0000-0000-0000-000000000001",
    model_type: modelType,
    updated_at: "2026-09-25T10:00:00Z",
    outcome_terms: outcomeTerms,
    ...(modelType === "fixed_price" ? { agreed_price: "150000.0050", currency: "PLN" } : {}),
  };
}

function terms(scenarioId: string, commercialTerms: CommercialTermsRead | null, revenue: RevenueRead): ScenarioCommercialTerms {
  return { scenario_id: scenarioId, scenario_status: "Draft", commercial_terms: commercialTerms, revenue };
}

const outcomeTerms = (revenue = outcomeRevenue(), parameters: OutcomeTermsRead | null = AC_08_TERMS) =>
  terms(OUTCOME, rule("outcome_based", parameters), revenue);
const pointsTerms = (revenue = pointsRevenue()) => terms(POINTS, rule("story_points"), revenue);
const hourlyTerms = (revenue = hourlyRevenue()) => terms(HOURLY, rule("time_and_material"), revenue);

function results(scenarioId: string, revenue: RevenueRead, overrides: Partial<ScenarioResults> = {}): ScenarioResults {
  return {
    scenario_id: scenarioId,
    scenario_status: "Draft",
    revenue,
    personnel_cost: { state: "calculated", amount: "15000.00", currency: "PLN", paid_absence_state: "calculated", paid_absence_amount: "0.00", paid_absence_currency: "PLN" },
    additional_cost: { state: "calculated", amount: "0.00", currency: "PLN" },
    included_cost: "15000.00",
    profit: "5000.00",
    margin: "25.00",
    markup: "33.33",
    profitability_state: "calculated",
    ...overrides,
  };
}

// --- A backend, by path and method ---------------------------------------------------------------

type Answer = { readonly status: number; readonly body?: unknown } | { readonly hang: true };

const TERMS_PATH = /^\/projects\/([^/]+)\/scenarios\/([^/]+)\/commercial-terms$/;
const RESULTS_PATH = /^\/projects\/([^/]+)\/scenarios\/([^/]+)\/results$/;

interface Backend {
  readonly projects?: ProjectListItem[];
  /** `GET …/commercial-terms` per scenario id; unspecified hangs. */
  readonly terms?: Record<string, unknown>;
  /** `GET …/results` per scenario id; unspecified hangs. */
  readonly results?: Record<string, unknown>;
  /** `POST …/commercial-terms` per scenario id, answered `201`. */
  readonly writes?: Record<string, unknown>;
}

function stubBackend(backend: Backend) {
  const fetchMock = vi.fn((url: string, init: RequestInit = {}) => {
    const path = new URL(url).pathname;
    const method = init.method ?? "GET";
    const termsMatch = TERMS_PATH.exec(path);
    const resultsMatch = RESULTS_PATH.exec(path);
    let answer: Answer = { hang: true };
    if (path === "/health") {
      answer = { status: 200, body: { status: "ok" } };
    } else if (path === "/projects") {
      answer = { status: 200, body: { projects: backend.projects ?? [PROJECT] } };
    } else if (path === "/catalog/rates") {
      answer = { status: 200, body: { rates: [], total: 0 } };
    } else if (path.startsWith("/catalog/dimensions/")) {
      answer = { status: 200, body: { entries: [] } };
    } else if (termsMatch !== null && method === "GET" && backend.terms?.[termsMatch[2]] !== undefined) {
      answer = { status: 200, body: backend.terms[termsMatch[2]] };
    } else if (termsMatch !== null && method === "POST" && backend.writes?.[termsMatch[2]] !== undefined) {
      answer = { status: 201, body: backend.writes[termsMatch[2]] };
    } else if (resultsMatch !== null && backend.results?.[resultsMatch[2]] !== undefined) {
      answer = { status: 200, body: backend.results[resultsMatch[2]] };
    }
    if ("hang" in answer) {
      return new Promise((_resolve, reject) => {
        init.signal?.addEventListener("abort", () =>
          reject(new DOMException("The operation was aborted.", "AbortError")),
        );
      });
    }
    const { status, body } = answer;
    return Promise.resolve({ ok: status >= 200 && status < 300, status, json: async () => body });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

// --- Screen helpers ------------------------------------------------------------------------------

async function openInApp() {
  render(<App />);
  const main = screen.getByRole("main");
  fireEvent.click(await within(main).findByRole("button", { name: "Aurora migration" }));
}

function card(scenarioName: string): HTMLElement {
  const item = screen.getByRole("heading", { name: scenarioName }).closest("li");
  if (item === null) {
    throw new Error(`No scenario card for ${scenarioName}`);
  }
  return item;
}

async function termsSection(scenarioName: string): Promise<HTMLElement> {
  const find = () => within(card(scenarioName)).getByRole("region", { name: "Commercial terms" });
  await waitFor(() => expect(within(find()).queryByText("Loading commercial terms.")).toBeNull());
  return find();
}

async function resultsSection(scenarioName: string): Promise<HTMLElement> {
  const find = () => within(card(scenarioName)).getByRole("region", { name: "Scenario results" });
  await waitFor(() => expect(within(find()).queryByText("Loading scenario results.")).toBeNull());
  return find();
}

function expectScreenAlive(scenarioName: string) {
  expect(screen.queryByText(SCREEN_CRASH_MESSAGE)).toBeNull();
  expect(within(card(scenarioName)).getByText("Status: Draft")).toBeVisible();
}

/** No message may be a substring of another — "distinguishable" as a property of the set. */
function expectPairwiseDistinct(messages: readonly string[]) {
  for (const [i, a] of messages.entries()) {
    expect(a.trim()).not.toBe("");
    for (const [j, b] of messages.entries()) {
      if (i !== j) {
        expect(b.includes(a), `"${a}" is contained in "${b}"`).toBe(false);
      }
    }
  }
}

const T_AND_M_LINES = [/Selling rate windows used/, /Months without a rate/, /Rates taken from/];

afterEach(() => {
  vi.unstubAllGlobals();
});

// --- K-01 -----------------------------------------------------------------------------------------

describe("K-01 — Story Points and Outcome-based scenarios are readable in both sections; values outside the contract are not", () => {
  it("renders a story points revenue instead of a section read failure, in the rule section and in the results section", async () => {
    stubBackend({
      terms: { [POINTS]: pointsTerms() },
      results: { [POINTS]: results(POINTS, pointsRevenue()) },
    });

    await openInApp();
    const rules = await termsSection("Points deal");
    const figures = await resultsSection("Points deal");

    expect(within(rules).getByText("Commercial model: Story Points")).toBeVisible();
    expect(within(rules).getByText("Revenue: 25000.00 PLN")).toBeVisible();
    expect(within(rules).getByText(RULE_SOURCE_LABELS.story_points)).toBeVisible();
    for (const line of T_AND_M_LINES) {
      expect(within(rules).queryByText(line)).toBeNull();
    }
    // A rule exists: no creation action of any model is offered (Q1 = A).
    expect(within(rules).queryAllByRole("button")).toHaveLength(0);
    expect(within(rules).queryByText(READ_UNREADABLE)).toBeNull();

    expect(within(figures).getByText("Revenue: 25000.00 PLN")).toBeVisible();
    expect(within(figures).getByText("Profit: 5000.00 PLN")).toBeVisible();
    expect(within(figures).queryByText(RESULTS_UNREADABLE)).toBeNull();
    expectScreenAlive("Points deal");
  });

  it("renders an outcome-based scenario in both sections, by the model's own name and without the Time & Material lines", async () => {
    stubBackend({
      terms: { [OUTCOME]: outcomeTerms() },
      results: { [OUTCOME]: results(OUTCOME, outcomeRevenue()) },
    });

    await openInApp();
    const rules = await termsSection("Outcome deal");
    const figures = await resultsSection("Outcome deal");

    expect(within(rules).getByText("Commercial model: Outcome-based")).toBeVisible();
    expect(within(rules).getByText(RULE_SOURCE_LABELS.outcome_based)).toBeVisible();
    for (const line of T_AND_M_LINES) {
      expect(within(rules).queryByText(line)).toBeNull();
    }
    expect(within(rules).queryAllByRole("button")).toHaveLength(0);
    expect(within(figures).getByText("Guaranteed revenue: 20000.00 PLN")).toBeVisible();
    expectScreenAlive("Outcome deal");
  });

  const refused: ReadonlyArray<readonly [string, RevenueRead]> = [
    ["a story points revenue with a rate_source sentinel outside the contract", pointsRevenue({ assumptions_used: { ...SP_ASSUMPTIONS, rate_source: "estimated_rates" } })],
    ["an outcome revenue with an hours_source sentinel outside the contract", outcomeRevenue({ assumptions_used: { ...OUTCOME_ASSUMPTIONS, hours_source: "planned_hours" } })],
    ["a time & material revenue with not_applicable hours (Q-B: pairs, not a union per field)", hourlyRevenue({ assumptions_used: { ...TM_ASSUMPTIONS, hours_source: "not_applicable" } })],
    ["a time & material revenue with the story points rate_source", hourlyRevenue({ assumptions_used: { ...TM_ASSUMPTIONS, rate_source: "story_points_terms" } })],
    ["a story points revenue with Time & Material's sources", pointsRevenue({ assumptions_used: { ...TM_ASSUMPTIONS, model_type: "story_points" } })],
    ["an outcome revenue with the story points rate_source", outcomeRevenue({ assumptions_used: { ...OUTCOME_ASSUMPTIONS, rate_source: "story_points_terms" } })],
    ["a calculated revenue of a model outside the closed set", hourlyRevenue({ assumptions_used: { ...TM_ASSUMPTIONS, model_type: "future_model" } })],
  ];

  for (const [what, revenue] of refused) {
    it(`reads ${what} as a read failure of both sections, never a render`, async () => {
      stubBackend({
        terms: { [HOURLY]: terms(HOURLY, rule("time_and_material"), revenue), [POINTS]: pointsTerms() },
        results: { [HOURLY]: results(HOURLY, revenue) },
      });

      await openInApp();
      const rules = await termsSection("Hourly deal");
      const figures = await resultsSection("Hourly deal");

      expect(within(rules).getByText(READ_UNREADABLE)).toBeVisible();
      expect(rules.textContent).not.toMatch(/revenue:/i);
      expect(within(figures).getByText(RESULTS_UNREADABLE)).toBeVisible();
      expect(figures.textContent).not.toContain("5000.00");
      expectScreenAlive("Hourly deal");
      // The contrast, on the same screen: the well-formed story points answer is read.
      expect(within(await termsSection("Points deal")).getByText("Revenue: 25000.00 PLN")).toBeVisible();
    });
  }

  it("still reads a model outside the closed set as the named unsupported state — the contrast to the calculated one above", async () => {
    stubBackend({
      terms: {
        [HOURLY]: terms(
          HOURLY,
          rule("future_model"),
          hourlyRevenue({ state: "unsupported_model_type", amount: "n/a", currency: null, assumptions_used: { ...TM_ASSUMPTIONS, model_type: "future_model" } }),
        ),
      },
    });

    await openInApp();
    const rules = await termsSection("Hourly deal");

    expect(within(rules).getByText(REVENUE_STATE_MESSAGES.unsupported_model_type)).toBeVisible();
    expect(within(rules).queryByText(READ_UNREADABLE)).toBeNull();
    // F06 (verification round 2): the status-chosen catalogue sources of an unsupported model are
    // not a rate source (ADR-0003, addendum SC-7-03, point 1) — no catalogue line is shown for it.
    expect(within(rules).queryByText(RATE_SOURCE_LABELS.live_catalog)).toBeNull();
    expect(within(rules).queryByText(NO_RATE_WINDOWS)).toBeNull();
  });

  it("still shows the catalogue lines for a time & material rule without a rate — the contrast to the unsupported model above", async () => {
    stubBackend({
      terms: {
        [HOURLY]: terms(
          HOURLY,
          rule("time_and_material"),
          hourlyRevenue({ state: "no_rate", amount: "n/a", currency: null }),
        ),
      },
    });

    await openInApp();
    const rules = await termsSection("Hourly deal");

    expect(within(rules).getByText(RATE_SOURCE_LABELS.live_catalog)).toBeVisible();
    expect(within(rules).getByText(NO_RATE_WINDOWS)).toBeVisible();
  });

  it("ends a save whose 201 carries a time & material revenue with not_applicable sources as unresolved, not as a rendered rule", async () => {
    stubBackend({
      terms: {
        [HOURLY]: terms(HOURLY, null, {
          state: "no_commercial_terms",
          amount: "n/a",
          currency: null,
          assumptions_used: { ...TM_ASSUMPTIONS, model_type: null },
          ...NOT_OUTCOME,
        }),
      },
      writes: {
        [HOURLY]: hourlyTerms(
          hourlyRevenue({ assumptions_used: { ...TM_ASSUMPTIONS, hours_source: "not_applicable", vendor_axis: "not_applicable" } }),
        ),
      },
    });

    await openInApp();
    const rules = await termsSection("Hourly deal");
    fireEvent.click(within(rules).getByRole("button", { name: "Set Time & Material for Hourly deal" }));

    expect(await within(rules).findByText(SAVE_UNRESOLVED_UNREADABLE_ANSWER)).toBeVisible();
    expect(within(rules).queryByText(SAVED)).toBeNull();
    expectScreenAlive("Hourly deal");
  });
});

// --- K-02 -----------------------------------------------------------------------------------------

describe("K-02 — the guaranteed and the expected outcome revenue are two separately labelled amounts", () => {
  it("renders the guaranteed and the expected outcome revenue as two separately labelled amounts", async () => {
    stubBackend({ terms: { [OUTCOME]: outcomeTerms() } });

    await openInApp();
    const rules = await termsSection("Outcome deal");

    expect(within(rules).getByText("Guaranteed revenue: 20000.00 PLN")).toBeVisible();
    expect(within(rules).getByText("Expected revenue: 23000.00 PLN")).toBeVisible();
    expect(within(rules).queryByText("Guaranteed revenue: 23000.00 PLN")).toBeNull();
    expect(within(rules).queryByText("Expected revenue: 20000.00 PLN")).toBeNull();
    expect(rules.querySelector('[data-expected-state="calculated"]')).not.toBeNull();
  });

  it("formats both through the shared money formatter, ROUND_HALF_UP on the decimal string", async () => {
    stubBackend({
      terms: { [OUTCOME]: outcomeTerms(outcomeRevenue({ amount: "20000.005", expected_amount: "23000.015" })) },
    });

    await openInApp();
    const rules = await termsSection("Outcome deal");

    expect(within(rules).getByText("Guaranteed revenue: 20000.01 PLN")).toBeVisible();
    expect(within(rules).getByText("Expected revenue: 23000.02 PLN")).toBeVisible();
  });

  it("renders the no_probabilities expected state as a named state, never zero and never the guaranteed amount", async () => {
    const noProbabilities = AC_08_CATEGORIES.map((category) => ({ ...category, probability: null }));
    stubBackend({
      terms: {
        // R-06 (verification round 2): the rule's own categories carry no probability either — both
        // objects come from one `outcome_terms` row, whose CHECK is "all or none".
        [OUTCOME]: outcomeTerms(
          outcomeRevenue({ expected_state: "no_probabilities", expected_amount: "n/a", category_revenues: noProbabilities }),
          NO_PROBABILITY_TERMS,
        ),
      },
    });

    await openInApp();
    const rules = await termsSection("Outcome deal");

    expect(within(rules).getByText(EXPECTED_NO_PROBABILITIES)).toBeVisible();
    expect(EXPECTED_NO_PROBABILITIES).not.toMatch(/\d/);
    expect(rules.textContent).not.toMatch(/Expected revenue: \d/);
    // The guaranteed revenue is still stated — once, under its own label.
    expect(within(rules).getByText("Guaranteed revenue: 20000.00 PLN")).toBeVisible();
    expect(rules.querySelector('[data-expected-state="no_probabilities"]')).not.toBeNull();
  });
});

// --- K-03 -----------------------------------------------------------------------------------------

describe("K-03 — pairing rules are checked before anything renders", () => {
  const refusedRevenue: ReadonlyArray<readonly [string, RevenueRead]> = [
    ["a numeric expected_amount with expected_state no_probabilities", outcomeRevenue({ expected_state: "no_probabilities", expected_amount: "23000.00" })],
    ["a numeric expected_amount with expected_state not_applicable", outcomeRevenue({ expected_state: "not_applicable", expected_amount: "23000.00" })],
    ["expected_state calculated with the n/a sentinel", outcomeRevenue({ expected_amount: "n/a" })],
    ["an expected_state outside the closed set", outcomeRevenue({ expected_state: "estimated" })],
    ["an expected revenue on a time & material revenue", hourlyRevenue({ expected_state: "calculated", expected_amount: "1.00" })],
    ["only three of the four outcome categories", outcomeRevenue({ category_revenues: AC_08_CATEGORIES.slice(0, 3) })],
    ["one outcome category twice", outcomeRevenue({ category_revenues: [...AC_08_CATEGORIES.slice(0, 3), AC_08_CATEGORIES[0]] })],
    ["a category outside the closed set", outcomeRevenue({ category_revenues: [...AC_08_CATEGORIES.slice(0, 3), { ...AC_08_CATEGORIES[3], category: "overachieved" }] })],
  ];

  for (const [what, revenue] of refusedRevenue) {
    it(`reads ${what} as this section's unreadable answer`, async () => {
      stubBackend({ terms: { [OUTCOME]: terms(OUTCOME, rule("outcome_based", AC_08_TERMS), revenue) } });

      await openInApp();
      const rules = await termsSection("Outcome deal");

      expect(within(rules).getByText(READ_UNREADABLE)).toBeVisible();
      expect(rules.textContent).not.toContain("23000");
      expectScreenAlive("Outcome deal");
    });
  }

  it("reads outcome parameters under another model's name as unreadable", async () => {
    stubBackend({ terms: { [POINTS]: terms(POINTS, rule("story_points", AC_08_TERMS), pointsRevenue()) } });

    await openInApp();
    expect(within(await termsSection("Points deal")).getByText(READ_UNREADABLE)).toBeVisible();
  });

  const refusedResults: ReadonlyArray<readonly [string, Partial<ScenarioResults>]> = [
    ["a numeric profit with profitability_state currency_mismatch", { profitability_state: "currency_mismatch", included_cost: "n/a", margin: "n/a", markup: "n/a" }],
    ["a numeric margin with profitability_state not_applicable", { profitability_state: "not_applicable", included_cost: "n/a", profit: "n/a", markup: "n/a" }],
    ["a profitability_state outside the closed set", { profitability_state: "diverged" as ScenarioResults["profitability_state"] }],
  ];

  for (const [what, overrides] of refusedResults) {
    it(`reads ${what} as the results section's unreadable answer`, async () => {
      stubBackend({ results: { [HOURLY]: results(HOURLY, hourlyRevenue(), overrides) } });

      await openInApp();
      const figures = await resultsSection("Hourly deal");

      expect(within(figures).getByText(RESULTS_UNREADABLE)).toBeVisible();
      expectScreenAlive("Hourly deal");
    });
  }

  it("reads a calculated profitability with a margin of n/a (AC-05) — the pairing is one-directional", async () => {
    stubBackend({
      results: { [HOURLY]: results(HOURLY, hourlyRevenue({ amount: "0.00" }), { margin: "n/a", profit: "-15000.00" }) },
    });

    await openInApp();
    const figures = await resultsSection("Hourly deal");

    expect(within(figures).getByText(`Margin: ${NOT_APPLICABLE}`)).toBeVisible();
    expect(within(figures).getByText("Profit: -15000.00 PLN")).toBeVisible();
    expect(within(figures).queryByText(RESULTS_UNREADABLE)).toBeNull();
    // A zero-denominator margin is not a currency mismatch.
    expect(within(figures).queryByText(PROFITABILITY_CURRENCY_MISMATCH)).toBeNull();
  });
});

// --- K-04 -----------------------------------------------------------------------------------------

describe("K-04 — the four outcome categories, labelled by their word, with units, probability and amount", () => {
  it("renders the four outcome categories with units, probability and amount", async () => {
    stubBackend({ terms: { [OUTCOME]: outcomeTerms() } });

    await openInApp();
    const rules = await termsSection("Outcome deal");

    expect(within(rules).getByText("Not achieved — units: none; probability: 70.00%; revenue: 20000.00 PLN")).toBeVisible();
    expect(within(rules).getByText("Partially achieved — units: none; probability: 0.00%; revenue: 20000.00 PLN")).toBeVisible();
    expect(within(rules).getByText("Achieved — units: none; probability: 30.00%; revenue: 30000.00 PLN")).toBeVisible();
    expect(within(rules).getByText("Exceeded — units: none; probability: 0.00%; revenue: 30000.00 PLN")).toBeVisible();
    expect(rules.querySelectorAll("[data-category]")).toHaveLength(4);
  });

  it("labels each category by its category field, not by its position, when the answer lists them out of canonical order", async () => {
    const shuffled: CategoryRevenueRead[] = [
      { category: "exceeded", units: "150.0000", probability: "10.00", amount: "30000.00" },
      { category: "achieved", units: "100.0000", probability: "20.00", amount: "30000.00" },
      { category: "not_achieved", units: "0.0000", probability: "40.00", amount: "22000.00" },
      { category: "partial", units: "50.0000", probability: "30.00", amount: "25000.00" },
    ];
    stubBackend({ terms: { [OUTCOME]: outcomeTerms(outcomeRevenue({ category_revenues: shuffled, expected_amount: "25300.00" })) } });

    await openInApp();
    const rules = await termsSection("Outcome deal");

    expect(within(rules).getByText("Exceeded — units: 150.0000; probability: 10.00%; revenue: 30000.00 PLN")).toBeVisible();
    expect(within(rules).getByText("Achieved — units: 100.0000; probability: 20.00%; revenue: 30000.00 PLN")).toBeVisible();
    expect(within(rules).getByText("Not achieved — units: 0.0000; probability: 40.00%; revenue: 22000.00 PLN")).toBeVisible();
    expect(within(rules).getByText("Partially achieved — units: 50.0000; probability: 30.00%; revenue: 25000.00 PLN")).toBeVisible();
    expect(rules.querySelector('[data-category="exceeded"]')?.textContent).toContain(OUTCOME_CATEGORY_LABELS.exceeded);
  });

  it("renders units of 0.0000 as that zero and null units as none — never one for the other", async () => {
    const mixed: CategoryRevenueRead[] = [
      { ...AC_08_CATEGORIES[0], units: "0.0000" },
      { ...AC_08_CATEGORIES[1], units: null },
      { ...AC_08_CATEGORIES[2], probability: "30.00" },
      AC_08_CATEGORIES[3],
    ];
    stubBackend({ terms: { [OUTCOME]: outcomeTerms(outcomeRevenue({ category_revenues: mixed })) } });

    await openInApp();
    const rules = await termsSection("Outcome deal");

    expect(rules.querySelector('[data-category="not_achieved"]')?.textContent).toContain("units: 0.0000;");
    expect(rules.querySelector('[data-category="partial"]')?.textContent).toContain("units: none;");
    expect(rules.querySelector('[data-category="partial"]')?.textContent).not.toContain("units: 0");
  });

  it("renders a null probability as none, never 0%", async () => {
    const noProbabilities = AC_08_CATEGORIES.map((category) => ({ ...category, probability: null }));
    stubBackend({
      terms: {
        // R-06 (verification round 2): the rule's own categories carry no probability either — both
        // objects come from one `outcome_terms` row, whose CHECK is "all or none".
        [OUTCOME]: outcomeTerms(
          outcomeRevenue({ expected_state: "no_probabilities", expected_amount: "n/a", category_revenues: noProbabilities }),
          NO_PROBABILITY_TERMS,
        ),
      },
    });

    await openInApp();
    const rules = await termsSection("Outcome deal");

    expect(within(rules).getByText("Not achieved — units: none; probability: none; revenue: 20000.00 PLN")).toBeVisible();
    expect(rules.textContent).not.toContain("probability: 0");
  });
});

// --- K-05 -----------------------------------------------------------------------------------------

describe("K-05 — the outcome rule's parameters as stored", () => {
  it("renders the outcome rule parameters as stored", async () => {
    stubBackend({ terms: { [OUTCOME]: outcomeTerms() } });

    await openInApp();
    const rules = await termsSection("Outcome deal");

    expect(within(rules).getByText("Rule currency: PLN")).toBeVisible();
    expect(within(rules).getByText("Fixed fee: 20000.0000 PLN")).toBeVisible();
    expect(within(rules).getByText("Success bonus: 10000.0000 PLN")).toBeVisible();
    expect(within(rules).getByText("Rate per unit: none")).toBeVisible();
    expect(within(rules).getByText("Minimum revenue: none")).toBeVisible();
    expect(within(rules).getByText("Maximum revenue: none")).toBeVisible();
  });

  it("renders an explicit 0.0000 maximum as zero and a stored 12.3456 unit rate with its four places", async () => {
    stubBackend({
      terms: {
        [OUTCOME]: outcomeTerms(outcomeRevenue(), {
          ...AC_08_TERMS,
          success_bonus: null,
          unit_rate: "12.3456",
          revenue_min: "0.0000",
          revenue_max: "0.0000",
        }),
      },
    });

    await openInApp();
    const rules = await termsSection("Outcome deal");

    expect(within(rules).getByText("Rate per unit: 12.3456 PLN")).toBeVisible();
    expect(within(rules).queryByText("Rate per unit: 12.35 PLN")).toBeNull();
    expect(within(rules).getByText("Maximum revenue: 0.0000 PLN")).toBeVisible();
    expect(within(rules).getByText("Minimum revenue: 0.0000 PLN")).toBeVisible();
    expect(within(rules).getByText("Success bonus: none")).toBeVisible();
  });

  it("takes every parameter's currency from the rule and names a rule-currency mismatch as such (Q6)", async () => {
    stubBackend({
      terms: {
        [OUTCOME]: outcomeTerms(
          outcomeRevenue({
            state: "currency_mismatch",
            amount: "n/a",
            currency: null,
            expected_state: "not_applicable",
            expected_amount: "n/a",
            category_revenues: [],
            assumptions_used: { ...OUTCOME_ASSUMPTIONS, currencies: ["USD"] },
          }),
          { ...AC_08_TERMS, currency: "USD" },
        ),
        [POINTS]: pointsTerms(pointsRevenue({ state: "currency_mismatch", amount: "n/a", currency: null })),
        [HOURLY]: hourlyTerms(hourlyRevenue({ state: "currency_mismatch", amount: "n/a", currency: null })),
      },
    });

    await openInApp();
    const outcome = await termsSection("Outcome deal");
    const points = await termsSection("Points deal");
    const hourly = await termsSection("Hourly deal");

    expect(within(outcome).getByText("Fixed fee: 20000.0000 USD")).toBeVisible();
    expect(within(outcome).getByText(RULE_CURRENCY_MISMATCH)).toBeVisible();
    expect(within(outcome).queryByText(REVENUE_STATE_MESSAGES.currency_mismatch)).toBeNull();
    expect(within(outcome).queryByText(/^Expected revenue/)).toBeNull();
    expect(within(points).getByText(RULE_CURRENCY_MISMATCH)).toBeVisible();
    // Time & Material keeps its catalogue sentence unchanged.
    expect(within(hourly).getByText(REVENUE_STATE_MESSAGES.currency_mismatch)).toBeVisible();
    expect(within(hourly).queryByText(RULE_CURRENCY_MISMATCH)).toBeNull();
  });
});

// --- K-06 -----------------------------------------------------------------------------------------

describe("K-06 — profitability_state currency_mismatch is its own reason, beside the personnel-cost gate", () => {
  it("renders currency_mismatch as its own reason distinct from not_applicable and from the personnel-cost gate", () => {
    expectPairwiseDistinct([
      PROFITABILITY_CURRENCY_MISMATCH,
      NOT_APPLICABLE,
      RESULTS_FIELD_UNAVAILABLE,
      REVENUE_STATE_MESSAGES.currency_mismatch,
      RULE_CURRENCY_MISMATCH,
      PERSONNEL_COST_STATE_MESSAGES.currency_mismatch,
      ADDITIONAL_COST_STATE_MESSAGES.currency_mismatch,
    ]);
  });

  it("keeps 'unavailable' on the gated fields and adds the section's currency line when the gate is closed", async () => {
    stubBackend({
      results: {
        [HOURLY]: results(HOURLY, hourlyRevenue(), {
          personnel_cost: { state: "calculated", amount: null, currency: null, paid_absence_state: "calculated", paid_absence_amount: null, paid_absence_currency: null },
          included_cost: null,
          profit: null,
          margin: null,
          markup: null,
          profitability_state: "currency_mismatch",
        }),
      },
    });

    await openInApp();
    const figures = await resultsSection("Hourly deal");

    expect(within(figures).getByText(`Profit: ${RESULTS_FIELD_UNAVAILABLE}`)).toBeVisible();
    expect(within(figures).getByText(`Margin: ${RESULTS_FIELD_UNAVAILABLE}`)).toBeVisible();
    expect(figures.querySelectorAll('[data-result-state="unavailable"]')).toHaveLength(4);
    const line = within(figures).getByText(PROFITABILITY_CURRENCY_MISMATCH);
    expect(line).toBeVisible();
    expect(line.getAttribute("data-profitability-state")).toBe("currency_mismatch");
    expect(line.textContent).not.toMatch(/\d/);
    expect(line.textContent).not.toMatch(/\b[A-Z]{3}\b/);
  });

  it("shows the same currency line when the gate is open, beside the fields' own not-applicable label", async () => {
    stubBackend({
      results: {
        [HOURLY]: results(HOURLY, hourlyRevenue(), {
          additional_cost: { state: "calculated", amount: "10.00", currency: "EUR" },
          included_cost: "n/a",
          profit: "n/a",
          margin: "n/a",
          markup: "n/a",
          profitability_state: "currency_mismatch",
        }),
      },
    });

    await openInApp();
    const figures = await resultsSection("Hourly deal");

    expect(within(figures).getByText(PROFITABILITY_CURRENCY_MISMATCH)).toBeVisible();
    expect(within(figures).getByText(`Profit: ${NOT_APPLICABLE}`)).toBeVisible();
    expect(within(figures).queryByText(`Profit: ${RESULTS_FIELD_UNAVAILABLE}`)).toBeNull();
  });

  it("shows no currency line for not_applicable or calculated — the contrast", async () => {
    stubBackend({
      results: {
        [HOURLY]: results(HOURLY, hourlyRevenue(), {
          personnel_cost: { state: "no_cost_rate", amount: "n/a", currency: null, paid_absence_state: "no_cost_rate", paid_absence_amount: "n/a", paid_absence_currency: null },
          included_cost: "n/a",
          profit: "n/a",
          margin: "n/a",
          markup: "n/a",
          profitability_state: "not_applicable",
        }),
        [POINTS]: results(POINTS, pointsRevenue()),
      },
    });

    await openInApp();
    const withheld = await resultsSection("Hourly deal");
    const calculated = await resultsSection("Points deal");

    expect(within(withheld).getByText(`Profit: ${NOT_APPLICABLE}`)).toBeVisible();
    expect(within(withheld).queryByText(PROFITABILITY_CURRENCY_MISMATCH)).toBeNull();
    expect(within(calculated).queryByText(PROFITABILITY_CURRENCY_MISMATCH)).toBeNull();
  });
});

// --- K-07 -----------------------------------------------------------------------------------------

describe("K-07 — the results section shows only the guaranteed outcome revenue; Time & Material and Story Points unchanged", () => {
  it("renders only the guaranteed revenue for an outcome scenario, with no expected revenue and no category", async () => {
    stubBackend({
      results: {
        [OUTCOME]: results(OUTCOME, outcomeRevenue()),
        [POINTS]: results(POINTS, pointsRevenue()),
        [HOURLY]: results(HOURLY, hourlyRevenue()),
      },
    });

    await openInApp();
    const outcome = await resultsSection("Outcome deal");
    const points = await resultsSection("Points deal");
    const hourly = await resultsSection("Hourly deal");

    expect(within(outcome).getByText("Guaranteed revenue: 20000.00 PLN")).toBeVisible();
    expect(outcome.textContent).not.toContain("23000");
    expect(outcome.textContent).not.toMatch(/Expected revenue/);
    for (const label of Object.values(OUTCOME_CATEGORY_LABELS)) {
      expect(outcome.textContent).not.toContain(label);
    }
    expect(within(outcome).getByText("Profit: 5000.00 PLN")).toBeVisible();

    // Time & Material and Story Points keep the plain label (addendum SC-4-07, point 6).
    expect(within(hourly).getByText("Revenue: 1000.00 PLN")).toBeVisible();
    expect(within(points).getByText("Revenue: 25000.00 PLN")).toBeVisible();
    expect(hourly.textContent).not.toMatch(/Guaranteed/);
    expect(points.textContent).not.toMatch(/Guaranteed/);
  });

  it("chooses the label by model_type, not by rate_source — an outcome revenue under a T&M rate_source is unreadable, never a relabelled amount", async () => {
    stubBackend({
      results: {
        [OUTCOME]: results(OUTCOME, outcomeRevenue({ assumptions_used: { ...OUTCOME_ASSUMPTIONS, rate_source: "live_catalog" } })),
      },
    });

    await openInApp();
    const outcome = await resultsSection("Outcome deal");

    expect(within(outcome).getByText(RESULTS_UNREADABLE)).toBeVisible();
    expect(outcome.textContent).not.toMatch(/Revenue: 20000/);
  });
});

// --- Verification round 1 (2026-09-25): R-01, R-03, R-04 -----------------------------------------

/** The results fields of a withheld revenue: none of the four is a number (R-01 of Issue #94). */
const WITHHELD_RESULTS: Partial<ScenarioResults> = {
  included_cost: "n/a",
  profit: "n/a",
  margin: "n/a",
  markup: "n/a",
  profitability_state: "not_applicable",
};

function withheldRevenue(state: string, assumptions: object): RevenueRead {
  return { state, amount: "n/a", currency: null, assumptions_used: assumptions, ...NOT_OUTCOME } as RevenueRead;
}

/** What a backend instance that cannot price the model answers for it (`revenue_of`): the rule's
 * own word as `model_type`, and the dispatcher's catalogue sources. */
function unsupportedAssumptions(modelType: string): Record<string, unknown> {
  return { ...TM_ASSUMPTIONS, model_type: modelType, currencies: [] };
}

describe("R-01 — incomplete and unsupported states of a known model are named states, and the Story Points pairing stays strict", () => {
  it("reads a story points rule without its details row (not_applicable twice, story_points_terms) as the named incomplete state in both sections", async () => {
    const revenue = withheldRevenue("incomplete_commercial_terms", SP_ASSUMPTIONS);
    stubBackend({
      terms: { [POINTS]: terms(POINTS, rule("story_points"), revenue) },
      results: { [POINTS]: results(POINTS, revenue, WITHHELD_RESULTS) },
    });

    await openInApp();
    const rules = await termsSection("Points deal");
    const figures = await resultsSection("Points deal");

    expect(within(rules).getByText(REVENUE_STATE_MESSAGES.incomplete_commercial_terms)).toBeVisible();
    expect(within(rules).getByText("Commercial model: Story Points")).toBeVisible();
    expect(within(rules).queryByText(READ_UNREADABLE)).toBeNull();
    expect(within(figures).getByText(REVENUE_STATE_MESSAGES.incomplete_commercial_terms)).toBeVisible();
    expect(within(figures).queryByText(RESULTS_UNREADABLE)).toBeNull();
    expectScreenAlive("Points deal");
  });

  it("reads an outcome-based rule without its details row (not_applicable three times, outcome_terms null) as the named incomplete state in both sections", async () => {
    const revenue = withheldRevenue("incomplete_commercial_terms", OUTCOME_ASSUMPTIONS);
    stubBackend({
      terms: { [OUTCOME]: terms(OUTCOME, rule("outcome_based", null), revenue) },
      results: { [OUTCOME]: results(OUTCOME, revenue, WITHHELD_RESULTS) },
    });

    await openInApp();
    const rules = await termsSection("Outcome deal");
    const figures = await resultsSection("Outcome deal");

    expect(within(rules).getByText(REVENUE_STATE_MESSAGES.incomplete_commercial_terms)).toBeVisible();
    expect(within(rules).getByText("Commercial model: Outcome-based")).toBeVisible();
    expect(within(rules).queryByText(READ_UNREADABLE)).toBeNull();
    expect(within(figures).getByText(REVENUE_STATE_MESSAGES.incomplete_commercial_terms)).toBeVisible();
    expect(within(figures).queryByText(RESULTS_UNREADABLE)).toBeNull();
    expectScreenAlive("Outcome deal");
  });

  it("keeps the old story points hybrid (billable_hours, internal, live_catalog) unreadable in both sections — the validator is not loosened", async () => {
    const revenue = withheldRevenue("incomplete_commercial_terms", { ...TM_ASSUMPTIONS, model_type: "story_points" });
    stubBackend({
      terms: { [POINTS]: terms(POINTS, rule("story_points"), revenue) },
      results: { [POINTS]: results(POINTS, revenue, WITHHELD_RESULTS) },
    });

    await openInApp();
    const rules = await termsSection("Points deal");
    const figures = await resultsSection("Points deal");

    expect(within(rules).getByText(READ_UNREADABLE)).toBeVisible();
    expect(within(rules).queryByText(REVENUE_STATE_MESSAGES.incomplete_commercial_terms)).toBeNull();
    expect(within(figures).getByText(RESULTS_UNREADABLE)).toBeVisible();
    expectScreenAlive("Points deal");
  });

  for (const [modelType, scenarioId, scenarioName, modelName] of [
    ["story_points", POINTS, "Points deal", "Story Points"],
    ["outcome_based", OUTCOME, "Outcome deal", "Outcome-based"],
  ] as const) {
    it(`reads unsupported_model_type for the known model ${modelType} (an older backend instance) as the named state in both sections, not as the model's lines`, async () => {
      const revenue = withheldRevenue("unsupported_model_type", unsupportedAssumptions(modelType));
      stubBackend({
        terms: { [scenarioId]: terms(scenarioId, rule(modelType), revenue) },
        results: { [scenarioId]: results(scenarioId, revenue, WITHHELD_RESULTS) },
      });

      await openInApp();
      const rules = await termsSection(scenarioName);
      const figures = await resultsSection(scenarioName);

      expect(within(rules).getByText(REVENUE_STATE_MESSAGES.unsupported_model_type)).toBeVisible();
      expect(within(rules).getByText(`Commercial model: ${modelName}`)).toBeVisible();
      expect(within(rules).queryByText(READ_UNREADABLE)).toBeNull();
      // Its sources are the dispatcher's catalogue ones: the model's own source line is not claimed.
      expect(within(rules).queryByText(RULE_SOURCE_LABELS[modelType])).toBeNull();
      expect(within(figures).getByText(REVENUE_STATE_MESSAGES.unsupported_model_type)).toBeVisible();
      expect(within(figures).queryByText(RESULTS_UNREADABLE)).toBeNull();
      expectScreenAlive(scenarioName);
    });
  }

  it("reads the same catalogue-sourced story points revenue under any other state as unreadable — only the state differs from the case above", async () => {
    const revenue = withheldRevenue("no_rate", unsupportedAssumptions("story_points"));
    stubBackend({
      terms: { [POINTS]: terms(POINTS, rule("story_points"), revenue) },
      results: { [POINTS]: results(POINTS, revenue, WITHHELD_RESULTS) },
    });

    await openInApp();
    expect(within(await termsSection("Points deal")).getByText(READ_UNREADABLE)).toBeVisible();
    expect(within(await resultsSection("Points deal")).getByText(RESULTS_UNREADABLE)).toBeVisible();
    expectScreenAlive("Points deal");
  });
});

describe("R-03 — the rule's model_type and the revenue's model_type are one model", () => {
  const mismatched: ReadonlyArray<readonly [string, string, ScenarioCommercialTerms]> = [
    ["an outcome-based rule with a story points revenue", "Points deal", terms(POINTS, rule("outcome_based", AC_08_TERMS), pointsRevenue())],
    ["a story points rule with an outcome-based revenue", "Outcome deal", terms(OUTCOME, rule("story_points"), outcomeRevenue())],
    ["a time & material rule with a story points revenue", "Points deal", terms(POINTS, rule("time_and_material"), pointsRevenue())],
    ["a story points rule with a time & material revenue", "Hourly deal", terms(HOURLY, rule("story_points"), hourlyRevenue())],
  ];

  for (const [what, scenarioName, answer] of mismatched) {
    it(`reads ${what} as unreadable, never as either model`, async () => {
      stubBackend({ terms: { [answer.scenario_id]: answer } });

      await openInApp();
      const rules = await termsSection(scenarioName);

      expect(within(rules).getByText(READ_UNREADABLE)).toBeVisible();
      expect(rules.textContent).not.toMatch(/Commercial model:/);
      expect(rules.textContent).not.toMatch(/revenue:/i);
      expectScreenAlive(scenarioName);
    });
  }

  it("reads the matching pairs of the same answers — the contrast", async () => {
    stubBackend({
      terms: { [POINTS]: pointsTerms(), [OUTCOME]: outcomeTerms(), [HOURLY]: hourlyTerms() },
    });

    await openInApp();

    expect(within(await termsSection("Points deal")).getByText("Revenue: 25000.00 PLN")).toBeVisible();
    expect(within(await termsSection("Outcome deal")).getByText("Guaranteed revenue: 20000.00 PLN")).toBeVisible();
    expect(within(await termsSection("Hourly deal")).getByText("Revenue: 1000.00 PLN")).toBeVisible();
  });

  it("never renders the outcome revenue lines under a story points revenue (QA N14: render chosen by commercial_terms.model_type)", async () => {
    // Before R-03 this answer was readable, and a render keyed on `commercial_terms.model_type` put the
    // expected-revenue line under "Commercial model: Outcome-based" beside a Story Points revenue.
    // Now the answer does not reach the render at all.
    stubBackend({ terms: { [POINTS]: terms(POINTS, rule("outcome_based", AC_08_TERMS), pointsRevenue()) } });

    await openInApp();
    const rules = await termsSection("Points deal");

    expect(within(rules).getByText(READ_UNREADABLE)).toBeVisible();
    expect(rules.textContent).not.toMatch(/Expected revenue|Guaranteed revenue|Rule parameters/);
  });
});

describe("R-04 — the outcome rule's categories are shown with its parameters, also when the revenue is withheld", () => {
  /** Keys out of canonical order on purpose: the render looks each category up by its word. */
  const STORED_CATEGORIES: OutcomeTermsRead["categories"] = {
    exceeded: { units: "150.0000", probability: "60.00" },
    partial: { units: null, probability: "0.00" },
    not_achieved: { units: "0.0000", probability: "40.00" },
    achieved: { units: "100.0000", probability: null },
  };

  it("renders the four stored categories' units and probabilities when the revenue is a currency_mismatch", async () => {
    stubBackend({
      terms: {
        [OUTCOME]: outcomeTerms(
          withheldRevenue("currency_mismatch", { ...OUTCOME_ASSUMPTIONS, currencies: ["USD"] }),
          { ...AC_08_TERMS, currency: "USD", unit_rate: "12.3456", categories: STORED_CATEGORIES },
        ),
        [POINTS]: pointsTerms(),
      },
    });

    await openInApp();
    const outcome = await termsSection("Outcome deal");

    expect(within(outcome).getByText(RULE_CURRENCY_MISMATCH)).toBeVisible();
    // No revenue per category — the revenue is withheld...
    expect(outcome.querySelectorAll("[data-category]")).toHaveLength(0);
    // ...but the rule's own categories are there, each by its word, `null` as none, zero as zero.
    expect(within(outcome).getByText("Not achieved — units set: 0.0000; probability set: 40.00%")).toBeVisible();
    expect(within(outcome).getByText("Partially achieved — units set: none; probability set: 0.00%")).toBeVisible();
    expect(within(outcome).getByText("Achieved — units set: 100.0000; probability set: none")).toBeVisible();
    expect(within(outcome).getByText("Exceeded — units set: 150.0000; probability set: 60.00%")).toBeVisible();
    expect(outcome.querySelectorAll("[data-rule-category]")).toHaveLength(4);
    expect(outcome.querySelector('[data-rule-category="partial"]')?.textContent).toContain("units set: none;");
    expect(outcome.querySelector('[data-rule-category="not_achieved"]')?.textContent).toContain("units set: 0.0000;");

    // The contrast: a rule without outcome parameters shows no category of a rule.
    const points = await termsSection("Points deal");
    expect(points.querySelectorAll("[data-rule-category]")).toHaveLength(0);
    expect(points.textContent).not.toMatch(/units set:|probability set:/);
  });

  it("renders the stored categories beside the calculated category revenues, as the rule's inputs and not as a second copy of the revenue", async () => {
    stubBackend({ terms: { [OUTCOME]: outcomeTerms() } });

    await openInApp();
    const outcome = await termsSection("Outcome deal");

    expect(outcome.querySelectorAll("[data-category]")).toHaveLength(4);
    expect(outcome.querySelectorAll("[data-rule-category]")).toHaveLength(4);
    expect(within(outcome).getByText("Not achieved — units set: none; probability set: 70.00%")).toBeVisible();
    expect(outcome.querySelector('[data-rule-category="achieved"]')?.textContent).not.toMatch(/revenue:/);
  });
});

describe("SC-4-10 K-01 — a Fixed Price card reads the rule and server-calculated revenue", () => {
  it("shows the agreed price and revenue in their own currencies, while the T&M contrast remains readable", async () => {
    stubBackend({
      projects: [FIXED_PRICE_PROJECT],
      terms: {
        [FIXED_PRICE]: terms(FIXED_PRICE, rule("fixed_price"), fixedPriceRevenue()),
        [HOURLY]: hourlyTerms(),
      },
      results: {
        [FIXED_PRICE]: results(FIXED_PRICE, fixedPriceRevenue()),
        [HOURLY]: results(HOURLY, hourlyRevenue()),
      },
    });

    await openInApp();
    const fixedPrice = await termsSection("Fixed Price deal");
    const hourly = await termsSection("Hourly deal");

    expect(within(fixedPrice).getByText("Commercial model: Fixed Price")).toBeVisible();
    expect(within(fixedPrice).getByText("Agreed price: 150000.0050 PLN")).toBeVisible();
    expect(within(fixedPrice).getByText("Revenue: 150000.01 PLN")).toBeVisible();
    expect(within(fixedPrice).queryByText(READ_UNREADABLE)).toBeNull();
    expect(within(hourly).getByText("Commercial model: Time & Material")).toBeVisible();
    expect(within(hourly).getByText("Revenue: 1000.00 PLN")).toBeVisible();
    expectScreenAlive("Fixed Price deal");
  });

  it("rejects a Fixed Price response paired with catalogue sources", async () => {
    stubBackend({
      projects: [FIXED_PRICE_PROJECT],
      terms: {
        [FIXED_PRICE]: terms(
          FIXED_PRICE,
          rule("fixed_price"),
          fixedPriceRevenue({
            assumptions_used: { ...FIXED_PRICE_ASSUMPTIONS, rate_source: "live_catalog" },
          }),
        ),
      },
    });

    await openInApp();
    const section = await termsSection("Fixed Price deal");
    expect(within(section).getByText(READ_UNREADABLE)).toBeVisible();
    expect(section.textContent).not.toContain("150000.01");
    expectScreenAlive("Fixed Price deal");
  });
});
