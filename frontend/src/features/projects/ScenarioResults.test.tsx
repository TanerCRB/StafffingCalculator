import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type {
  RevenueAssumptionsRead,
  RevenueRead,
  WithheldRevenueState,
} from "../../api/contracts/commercialTerms";
import type { ProjectListItem, ScenarioStatus } from "../../api/contracts/projects";
import type {
  AdditionalCostSource,
  AdditionalCostState,
  PersonnelCostSource,
  PersonnelCostState,
  ScenarioResults,
} from "../../api/contracts/scenarioResults";
import { NOT_APPLICABLE } from "../../lib/money";
import { App } from "../../App";
import { SCREEN_CRASH_MESSAGE } from "../../shell/ScreenErrorBoundary";
import { ProjectListScreen } from "./ProjectListScreen";
import {
  ADDITIONAL_COST_STATE_MESSAGES,
  NEGATIVE_PROFIT_INDICATOR,
  PAID_ABSENCE_COST_STATE_MESSAGES,
  PERSONNEL_COST_STATE_MESSAGES,
  RESULTS_CONFLICT,
  RESULTS_FAILED,
  RESULTS_FIELD_UNAVAILABLE,
  RESULTS_REFUSED,
  RESULTS_TIMED_OUT,
  RESULTS_UNREADABLE,
  REVENUE_STATE_MESSAGES,
} from "./scenarioResultsText";

/**
 * SC-7-02 — a scenario's profit, margin, markup and cost, as a second section of its card on the
 * project list (Issue #94, gate 1: Q1 = option A, Q2 = option b). One `describe` per criterion.
 *
 * The commercial-terms section mounted next to this one is not under test here: its read is always
 * stubbed to hang, so it never resolves into a state a test would otherwise have to account for.
 */

// --- Fixtures --------------------------------------------------------------------------------------

const BASELINE = "aaaaaaaa-0000-0000-0000-000000000001";
const STRETCH = "aaaaaaaa-0000-0000-0000-000000000002";

function scenario(id: string, name: string, status: ScenarioStatus = "Draft") {
  return {
    id,
    name,
    status,
    missing_inputs: [],
    ready_for_approval: status === "Approved",
    target_margin_percent: null,
  };
}

const PROJECT: ProjectListItem = {
  id: "11111111-1111-1111-1111-111111111111",
  name: "Aurora migration",
  client: "Northwind",
  delivery_period: { start: "2026-01-01", end: "2026-12-31" },
  reporting_currency: "EUR",
  description: "Core platform migration.",
  status: "Active",
  scenarios: [scenario(BASELINE, "Baseline"), scenario(STRETCH, "Stretch")],
};

const NO_ASSUMPTIONS: RevenueAssumptionsRead = {
  model_type: null,
  hours_source: "billable_hours",
  vendor_axis: "internal",
  rate_source: "live_catalog",
  rate_windows: [],
  unresolved_months: [],
  currencies: [],
};

function revenueCalculated(amount: string, currency: string): RevenueRead {
  return { state: "calculated", amount, currency, assumptions_used: NO_ASSUMPTIONS, expected_state: "not_applicable", expected_amount: "n/a", category_revenues: [] };
}

function revenueWithheld(state: WithheldRevenueState): RevenueRead {
  return { state, amount: "n/a", currency: null, assumptions_used: NO_ASSUMPTIONS, expected_state: "not_applicable", expected_amount: "n/a", category_revenues: [] };
}

function personnelCostCalculated(amount: string, currency: string): PersonnelCostSource {
  return { state: "calculated", amount, currency, assumptions_used: { rate_windows: [] }, paid_absence_state: "calculated", paid_absence_amount: "25.00", paid_absence_currency: currency };
}

function personnelCostWithheld(state: Exclude<PersonnelCostState, "calculated">): PersonnelCostSource {
  return { state, amount: "n/a", currency: null, assumptions_used: { rate_windows: [] }, paid_absence_state: state, paid_absence_amount: "n/a", paid_absence_currency: null };
}

function additionalCostCalculated(amount: string, currency: string): AdditionalCostSource {
  return { state: "calculated", amount, currency };
}

function additionalCostWithheld(
  state: Exclude<AdditionalCostState, "calculated">,
): AdditionalCostSource {
  return { state, amount: "n/a", currency: null };
}

/** Every field calculated and gate open — the baseline every test below narrows from. */
function baseResults(id: string, overrides: Partial<ScenarioResults> = {}): ScenarioResults {
  return {
    scenario_id: id,
    scenario_status: "Draft",
    revenue: revenueCalculated("1000.00", "PLN"),
    personnel_cost: personnelCostCalculated("400.00", "PLN"),
    additional_cost: additionalCostCalculated("50.00", "PLN"),
    included_cost: "450.00",
    profit: "550.00",
    margin: "55.00",
    markup: "122.22",
    profitability_state: "calculated",
    ...overrides,
  };
}

// --- A backend, by path and method -------------------------------------------------------------

type Answer =
  | { readonly status: number; readonly body?: unknown }
  | { readonly hang: true };

const TERMS_PATH = /^\/projects\/([^/]+)\/scenarios\/([^/]+)\/commercial-terms$/;
const RESULTS_PATH = /^\/projects\/([^/]+)\/scenarios\/([^/]+)\/results$/;

interface Backend {
  readonly projects?: ProjectListItem[];
  /** The `GET …/results` answer per scenario id — a function to answer the n-th read differently.
   * A scenario with none configured hangs, so an unrelated card on the same project never needs an
   * answer this test does not care about. */
  readonly results?: Record<string, Answer | ((call: number) => Answer)>;
}

function response(status: number, body: unknown) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => {
      if (body === undefined) {
        throw new SyntaxError("Unexpected end of JSON input");
      }
      return body;
    },
  };
}

function stubBackend(backend: Backend) {
  const readCounts = new Map<string, number>();
  const fetchMock = vi.fn((url: string, init: RequestInit = {}) => {
    const path = new URL(url).pathname;
    const method = init.method ?? "GET";
    let answer: Answer | undefined;
    const resultsMatch = RESULTS_PATH.exec(path);
    if (path === "/health") {
      answer = { status: 200, body: { status: "ok" } };
    } else if (path === "/projects") {
      answer = { status: 200, body: { projects: backend.projects ?? [PROJECT] } };
    } else if (path === "/catalog/rates") {
      answer = { status: 200, body: { rates: [], total: 0 } };
    } else if (path.startsWith("/catalog/dimensions/")) {
      answer = { status: 200, body: { entries: [] } };
    } else if (TERMS_PATH.test(path)) {
      // The commercial-terms section beside this one is not under test — left open forever.
      answer = { hang: true };
    } else if (resultsMatch !== null && method === "GET") {
      const scenarioId = resultsMatch[2];
      const count = (readCounts.get(scenarioId) ?? 0) + 1;
      readCounts.set(scenarioId, count);
      const configured = backend.results?.[scenarioId];
      answer = typeof configured === "function" ? configured(count) : (configured ?? { hang: true });
    }
    if (answer === undefined) {
      throw new Error(`No answer stubbed for ${method} ${path}`);
    }
    if ("hang" in answer) {
      return new Promise((_resolve, reject) => {
        init.signal?.addEventListener("abort", () =>
          reject(new DOMException("The operation was aborted.", "AbortError")),
        );
      });
    }
    return Promise.resolve(response(answer.status, answer.body));
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

type FetchMock = ReturnType<typeof stubBackend>;

function resultsCalls(fetchMock: FetchMock, scenarioId: string) {
  return fetchMock.mock.calls.filter(([url]) => {
    const match = RESULTS_PATH.exec(new URL(url).pathname);
    return match?.[2] === scenarioId;
  });
}

// --- Screen helpers ------------------------------------------------------------------------------

async function openProject() {
  fireEvent.click(await screen.findByRole("button", { name: "Aurora migration" }));
}

function card(scenarioName: string): HTMLElement {
  const item = screen.getByRole("heading", { name: scenarioName }).closest("li");
  if (item === null) {
    throw new Error(`No scenario card for ${scenarioName}`);
  }
  return item;
}

function section(scenarioName: string): HTMLElement {
  return within(card(scenarioName)).getByRole("region", { name: "Scenario results" });
}

/** Waits until the section has left its loading state, whatever it then says. */
async function settledSection(scenarioName: string): Promise<HTMLElement> {
  await waitFor(() =>
    expect(within(section(scenarioName)).queryByText("Loading scenario results.")).toBeNull(),
  );
  return section(scenarioName);
}

afterEach(() => {
  vi.unstubAllGlobals();
});

// --- K-01 -----------------------------------------------------------------------------------------

describe("K-01 — money and percentage figures render only through the shared formatter", () => {
  it("rounds ROUND_HALF_UP on the .005 boundary, matching the backend, for money and for a percentage", async () => {
    stubBackend({
      results: {
        [BASELINE]: {
          status: 200,
          body: baseResults(BASELINE, {
            included_cost: "100.005",
            profit: "100.005",
            margin: "12.505",
            markup: "12.505",
          }),
        },
      },
    });

    render(<ProjectListScreen />);
    await openProject();
    const results = await settledSection("Baseline");

    // `Number("100.005").toFixed(2)` is "100.00" — a call-site `toFixed()` would fail this.
    expect(within(results).getByText("Scenario cost: 100.01 PLN")).toBeVisible();
    expect(within(results).getByText("Profit: 100.01 PLN")).toBeVisible();
    expect(within(results).getByText("Margin: 12.51%")).toBeVisible();
    expect(within(results).getByText("Markup: 12.51%")).toBeVisible();
  });

  it("renders an exact 0.00 margin as 0.00%, never the n/a sentinel", async () => {
    // The contrast: a real number that rounds to zero is not the backend's "n/a" sentinel, and must
    // not be confused with it (revenue is non-zero here).
    stubBackend({
      results: { [BASELINE]: { status: 200, body: baseResults(BASELINE, { margin: "0.00" }) } },
    });

    render(<ProjectListScreen />);
    await openProject();
    const results = await settledSection("Baseline");

    expect(within(results).getByText("Margin: 0.00%")).toBeVisible();
    expect(within(results).queryByText(`Margin: ${NOT_APPLICABLE}`)).toBeNull();
    expect(within(results).queryByText(`Margin: ${RESULTS_FIELD_UNAVAILABLE}`)).toBeNull();
  });
});

describe("SC-7-05 - negative-profit indicator", () => {
  it("shows the indicator when profit is negative", async () => {
    stubBackend({
      results: {
        [BASELINE]: { status: 200, body: baseResults(BASELINE, { profit: "-0.01" }) },
      },
    });

    render(<ProjectListScreen />);
    await openProject();
    const results = await settledSection("Baseline");

    expect(within(results).getByText(NEGATIVE_PROFIT_INDICATOR)).toBeVisible();
  });

  it.each(["0.00", "-0.00"])("does not show the indicator when profit is zero (%s)", async (profit) => {
    stubBackend({
      results: {
        [BASELINE]: { status: 200, body: baseResults(BASELINE, { profit }) },
      },
    });

    render(<ProjectListScreen />);
    await openProject();
    const results = await settledSection("Baseline");

    expect(within(results).queryByText(NEGATIVE_PROFIT_INDICATOR)).toBeNull();
  });

  it("does not show the indicator when profit is positive", async () => {
    stubBackend({
      results: {
        [BASELINE]: { status: 200, body: baseResults(BASELINE, { profit: "0.01" }) },
      },
    });

    render(<ProjectListScreen />);
    await openProject();
    const results = await settledSection("Baseline");

    expect(within(results).queryByText(NEGATIVE_PROFIT_INDICATOR)).toBeNull();
  });

  it("renders separate indicator states for otherwise identical scenarios with opposite profit signs", async () => {
    stubBackend({
      results: {
        [BASELINE]: { status: 200, body: baseResults(BASELINE, { profit: "-1.00" }) },
        [STRETCH]: { status: 200, body: baseResults(STRETCH, { profit: "1.00" }) },
      },
    });

    render(<ProjectListScreen />);
    await openProject();
    const baseline = await settledSection("Baseline");
    const stretch = await settledSection("Stretch");

    expect(within(baseline).getByText(NEGATIVE_PROFIT_INDICATOR)).toBeVisible();
    expect(within(stretch).queryByText(NEGATIVE_PROFIT_INDICATOR)).toBeNull();
  });
});
// --- SC-7-07 --------------------------------------------------------------------------------------

describe("SC-7-07 — scenario cost components are independent", () => {
  it("renders distinct base and paid-absence amounts using the scenario currency", async () => {
    stubBackend({ results: { [BASELINE]: { status: 200, body: baseResults(BASELINE, {
      personnel_cost: { state: "calculated", amount: "400.00", currency: "PLN", paid_absence_state: "calculated", paid_absence_amount: "25.00", paid_absence_currency: "PLN" },
    }) } } });
    render(<ProjectListScreen />); await openProject();
    const results = await settledSection("Baseline");
    expect(within(results).getByText("Base personnel cost: 400.00 PLN")).toBeVisible();
    expect(within(results).getByText("Paid absence cost: 25.00 PLN")).toBeVisible();
  });

  it("shows paid absence as zero without changing the base amount", async () => {
    stubBackend({ results: { [BASELINE]: { status: 200, body: baseResults(BASELINE, {
      personnel_cost: { state: "calculated", amount: "400.00", currency: "PLN", paid_absence_state: "calculated", paid_absence_amount: "0.00", paid_absence_currency: "PLN" },
    }) } } });
    render(<ProjectListScreen />); await openProject();
    const results = await settledSection("Baseline");
    expect(within(results).getByText("Base personnel cost: 400.00 PLN")).toBeVisible();
    expect(within(results).getByText("Paid absence cost: 0.00 PLN")).toBeVisible();
  });

  it("keeps additional costs independent from personnel components", async () => {
    stubBackend({ results: { [BASELINE]: { status: 200, body: baseResults(BASELINE, {
      personnel_cost: { state: "calculated", amount: "400.00", currency: "PLN", paid_absence_state: "calculated", paid_absence_amount: "25.00", paid_absence_currency: "PLN" },
      additional_cost: additionalCostCalculated("75.00", "PLN"),
    }) } } });
    render(<ProjectListScreen />); await openProject();
    const results = await settledSection("Baseline");
    expect(within(results).getByText("Base personnel cost: 400.00 PLN")).toBeVisible();
    expect(within(results).getByText("Paid absence cost: 25.00 PLN")).toBeVisible();
    expect(within(results).getByText("Additional costs: 75.00 PLN")).toBeVisible();
  });

  it("shows named unavailable components distinctly from zero while retaining additional costs", async () => {
    stubBackend({ results: { [BASELINE]: { status: 200, body: baseResults(BASELINE, {
      personnel_cost: { state: "calculated", amount: "400.00", currency: "PLN", paid_absence_state: "no_budget", paid_absence_amount: "n/a", paid_absence_currency: null },
      additional_cost: additionalCostCalculated("50.00", "PLN"),
    }) } } });
    render(<ProjectListScreen />); await openProject();
    const results = await settledSection("Baseline");
    expect(within(results).getByText("Base personnel cost: 400.00 PLN")).toBeVisible();
    expect(within(results).getByText(PAID_ABSENCE_COST_STATE_MESSAGES.no_budget)).toBeVisible();
    expect(within(results).getByText("Additional costs: 50.00 PLN")).toBeVisible();
  });

  it("withholds both personnel components while additional costs remain visible", async () => {
    stubBackend({ results: { [BASELINE]: { status: 200, body: baseResults(BASELINE, {
      personnel_cost: { state: "calculated", amount: null, currency: null, paid_absence_state: "calculated", paid_absence_amount: null, paid_absence_currency: null },
    }) } } });
    render(<ProjectListScreen />); await openProject();
    const results = await settledSection("Baseline");
    expect(within(results).getByText(`Base personnel cost: ${RESULTS_FIELD_UNAVAILABLE}`)).toBeVisible();
    expect(within(results).getByText(`Paid absence cost: ${RESULTS_FIELD_UNAVAILABLE}`)).toBeVisible();
    expect(within(results).getByText("Additional costs: 50.00 PLN")).toBeVisible();
  });
});

// --- K-02 -----------------------------------------------------------------------------------------

describe("K-02 — the personnel-cost gate's null and a component's own n/a never render as the same thing", () => {
  it("renders the generic unavailable message on all four gated fields, and leaves revenue/additional cost visible", async () => {
    stubBackend({
      results: {
        [BASELINE]: {
          status: 200,
          body: baseResults(BASELINE, {
            included_cost: null,
            profit: null,
            margin: null,
            markup: null,
          }),
        },
      },
    });

    render(<ProjectListScreen />);
    await openProject();
    const results = await settledSection("Baseline");

    expect(within(results).getByText(`Scenario cost: ${RESULTS_FIELD_UNAVAILABLE}`)).toBeVisible();
    expect(within(results).getByText(`Profit: ${RESULTS_FIELD_UNAVAILABLE}`)).toBeVisible();
    expect(within(results).queryByText(NEGATIVE_PROFIT_INDICATOR)).toBeNull();
    expect(within(results).getByText(`Margin: ${RESULTS_FIELD_UNAVAILABLE}`)).toBeVisible();
    expect(within(results).getByText(`Markup: ${RESULTS_FIELD_UNAVAILABLE}`)).toBeVisible();
    expect(results.querySelectorAll('[data-result-state="unavailable"]')).toHaveLength(4);
    // Never gated — still their own real numbers.
    expect(within(results).getByText("Revenue: 1000.00 PLN")).toBeVisible();
    expect(within(results).getByText("Additional costs: 50.00 PLN")).toBeVisible();
  });

  it("answers 'unavailable' when the gate is closed, and 'n/a' once it opens on the same underlying field", async () => {
    stubBackend({
      results: {
        [BASELINE]: { status: 200, body: baseResults(BASELINE, { margin: null }) },
        [STRETCH]: { status: 200, body: baseResults(STRETCH, { margin: "n/a" }) },
      },
    });

    render(<ProjectListScreen />);
    await openProject();

    const closed = await settledSection("Baseline");
    expect(within(closed).getByText(`Margin: ${RESULTS_FIELD_UNAVAILABLE}`)).toBeVisible();
    expect(within(closed).queryByText(`Margin: ${NOT_APPLICABLE}`)).toBeNull();

    const open = await settledSection("Stretch");
    expect(within(open).getByText(`Margin: ${NOT_APPLICABLE}`)).toBeVisible();
    expect(within(open).queryByText(`Margin: ${RESULTS_FIELD_UNAVAILABLE}`)).toBeNull();
  });

  it("renders the same generic message for personnel_cost's own amount when its gate is closed, regardless of its state", async () => {
    // Architect's impact map: `personnel_cost.state` can say "calculated" while the very same gate
    // that nulls the four aggregate fields nulls this source's own amount too.
    stubBackend({
      results: {
        [BASELINE]: {
          status: 200,
          body: baseResults(BASELINE, { personnel_cost: { state: "calculated", amount: null, currency: null, assumptions_used: null, paid_absence_state: "calculated", paid_absence_amount: null, paid_absence_currency: null } }),
        },
      },
    });

    render(<ProjectListScreen />);
    await openProject();
    const results = await settledSection("Baseline");

    expect(within(results).getByText(`Base personnel cost: ${RESULTS_FIELD_UNAVAILABLE}`)).toBeVisible();
    expect(within(results).getByText(`Paid absence cost: ${RESULTS_FIELD_UNAVAILABLE}`)).toBeVisible();
    expect(within(results).queryByText(/^Base personnel cost: 400\.00/)).toBeNull();
  });

  it("renders 'unavailable', never the state's own message, when the gate is closed AND the state names a cause at once", async () => {
    // The real precedence case (QA addendum): unlike the test above (state "calculated" + amount
    // null, which never even reaches a state-message branch), here `state` itself names a
    // non-computable cause ("no_cost_rate") *and* the gate has independently nulled `amount` — both
    // conditions are true on the same field at once. Whichever check a component runs first must
    // still answer "unavailable": a component that checks `state !== "calculated"` before checking
    // `amount === null` would show the state's own wording instead, and that is exactly the
    // regression this test exists to catch.
    stubBackend({
      results: {
        [BASELINE]: {
          status: 200,
          body: baseResults(BASELINE, {
            personnel_cost: { state: "no_cost_rate", amount: null, currency: null, assumptions_used: null, paid_absence_state: "no_cost_rate", paid_absence_amount: null, paid_absence_currency: null },
          }),
        },
      },
    });

    render(<ProjectListScreen />);
    await openProject();
    const results = await settledSection("Baseline");

    expect(within(results).getByText(`Base personnel cost: ${RESULTS_FIELD_UNAVAILABLE}`)).toBeVisible();
    expect(within(results).getByText(`Paid absence cost: ${RESULTS_FIELD_UNAVAILABLE}`)).toBeVisible();
    expect(within(results).queryByText(PERSONNEL_COST_STATE_MESSAGES.no_cost_rate)).toBeNull();
    expect(results.querySelector('[data-personnel-cost-state="unavailable"]')).not.toBeNull();
  });
});

// --- K-03 -----------------------------------------------------------------------------------------

describe("K-03 — each composed source keeps its own named non-computable state", () => {
  it("renders a different message for each of three sources put into three different non-computable states", async () => {
    stubBackend({
      results: {
        [BASELINE]: {
          status: 200,
          body: baseResults(BASELINE, {
            revenue: revenueWithheld("no_commercial_terms"),
            personnel_cost: personnelCostWithheld("no_cost_rate"),
            additional_cost: additionalCostWithheld("currency_mismatch"),
            included_cost: "n/a",
            profit: "n/a",
            margin: "n/a",
            markup: "n/a",
          }),
        },
      },
    });

    render(<ProjectListScreen />);
    await openProject();
    const results = await settledSection("Baseline");

    const revenueMessage = within(results).getByText(REVENUE_STATE_MESSAGES.no_commercial_terms);
    const costMessages = within(results).getAllByText(PERSONNEL_COST_STATE_MESSAGES.no_cost_rate);
    const additionalMessage = within(results).getByText(
      ADDITIONAL_COST_STATE_MESSAGES.currency_mismatch,
    );
    expect(revenueMessage).toBeVisible();
    expect(costMessages).toHaveLength(2);
    costMessages.forEach((message) => expect(message).toBeVisible());
    expect(results.querySelector('[data-cost-component="base"][data-personnel-cost-state="no_cost_rate"]')).not.toBeNull();
    expect(results.querySelector('[data-cost-component="paid_absence"][data-personnel-cost-state="no_cost_rate"]')).not.toBeNull();
    expect(additionalMessage).toBeVisible();
    const texts = [revenueMessage.textContent, costMessages[0].textContent, additionalMessage.textContent];
    expect(new Set(texts).size).toBe(3);
    // The four gated fields, open but not computable — their own sentinel, not a state message.
    expect(results.querySelectorAll('[data-result-state="not-applicable"]')).toHaveLength(4);
  });

  it("renders real numbers everywhere when every source is calculated — the contrast", async () => {
    stubBackend({ results: { [BASELINE]: { status: 200, body: baseResults(BASELINE) } } });

    render(<ProjectListScreen />);
    await openProject();
    const results = await settledSection("Baseline");

    expect(within(results).getByText("Revenue: 1000.00 PLN")).toBeVisible();
    expect(within(results).getByText("Base personnel cost: 400.00 PLN")).toBeVisible();
    expect(within(results).getByText("Paid absence cost: 25.00 PLN")).toBeVisible();
    expect(within(results).getByText("Additional costs: 50.00 PLN")).toBeVisible();
    expect(within(results).getByText("Scenario cost: 450.00 PLN")).toBeVisible();
    expect(within(results).getByText("Profit: 550.00 PLN")).toBeVisible();
    expect(within(results).getByText("Margin: 55.00%")).toBeVisible();
    expect(within(results).getByText("Markup: 122.22%")).toBeVisible();
    expect(results.querySelector('[data-read-failure]')).toBeNull();
  });

  it("words the additional-cost source's own states differently from the personnel-cost source's", () => {
    // Held against each other directly, since the mutation this guards against ("one shared
    // 'cannot be shown' string across all three sources") would make these equal.
    expect(ADDITIONAL_COST_STATE_MESSAGES.currency_mismatch).not.toBe(
      PERSONNEL_COST_STATE_MESSAGES.currency_mismatch,
    );
    expect(ADDITIONAL_COST_STATE_MESSAGES.no_cost_currency).not.toBe(
      PERSONNEL_COST_STATE_MESSAGES.no_cost_currency,
    );
  });
});

// --- K-04 -----------------------------------------------------------------------------------------

describe("K-04 — 403 and 404 render identically, unlike the sibling commercial-terms section", () => {
  it("renders the same message text and the same state marker for a 403 and a 404", async () => {
    stubBackend({
      results: {
        [BASELINE]: { status: 403, body: { detail: "Forbidden" } },
        [STRETCH]: { status: 404, body: { detail: "Scenario not found." } },
      },
    });

    render(<ProjectListScreen />);
    await openProject();

    const forbidden = await settledSection("Baseline");
    const notFound = await settledSection("Stretch");

    expect(within(forbidden).getByText(RESULTS_REFUSED)).toBeVisible();
    expect(within(notFound).getByText(RESULTS_REFUSED)).toBeVisible();
    expect(forbidden.querySelector("[data-read-failure]")?.getAttribute("data-read-failure")).toBe(
      "refused",
    );
    expect(notFound.querySelector("[data-read-failure]")?.getAttribute("data-read-failure")).toBe(
      "refused",
    );
    // A denied read offers nothing to do.
    expect(within(forbidden).queryAllByRole("button")).toHaveLength(0);
    expect(within(notFound).queryAllByRole("button")).toHaveLength(0);
  });

  it("renders real content instead, for a scenario the caller holds RESULTS_READ and scope for", async () => {
    stubBackend({ results: { [BASELINE]: { status: 200, body: baseResults(BASELINE) } } });

    render(<ProjectListScreen />);
    await openProject();
    const results = await settledSection("Baseline");

    expect(within(results).getByText("Revenue: 1000.00 PLN")).toBeVisible();
    expect(within(results).queryByText(RESULTS_REFUSED)).toBeNull();
  });
});

// --- K-05 -----------------------------------------------------------------------------------------

describe("K-05 — 409 (the rate-source race) is its own named, retryable state", () => {
  it("renders a message distinct from a 500, and only the 409 names retry", async () => {
    stubBackend({ results: { [BASELINE]: { status: 409, body: { detail: "Conflict." } } } });

    render(<ProjectListScreen />);
    await openProject();
    const results = await settledSection("Baseline");

    expect(within(results).getByText(RESULTS_CONFLICT)).toBeVisible();
    expect(within(results).queryByText(RESULTS_FAILED)).toBeNull();
    expect(within(results).queryByText(RESULTS_TIMED_OUT)).toBeNull();
    expect(RESULTS_CONFLICT).not.toBe(RESULTS_FAILED);
    expect(RESULTS_CONFLICT.toLowerCase()).toContain("read again");
    expect(RESULTS_FAILED.toLowerCase()).not.toContain("read again");
  });

  it("renders real content on retry after a 409, not a repeated 409 baked into component state", async () => {
    const fetchMock = stubBackend({
      results: {
        [BASELINE]: (call) =>
          call === 1 ? { status: 409, body: { detail: "Conflict." } } : { status: 200, body: baseResults(BASELINE) },
      },
    });

    render(<ProjectListScreen />);
    await openProject();
    const first = await settledSection("Baseline");
    expect(within(first).getByText(RESULTS_CONFLICT)).toBeVisible();

    fireEvent.click(
      within(first).getByRole("button", { name: "Read scenario results again for Baseline" }),
    );

    const retried = await settledSection("Baseline");
    expect(within(retried).getByText("Revenue: 1000.00 PLN")).toBeVisible();
    expect(within(retried).queryByText(RESULTS_CONFLICT)).toBeNull();
    expect(resultsCalls(fetchMock, BASELINE)).toHaveLength(2);
  });
});

// --- K-06 -----------------------------------------------------------------------------------------

describe("K-06 — the section mounts on its own, and a failure on it never removes the card's name/status", () => {
  it("fetches on mount with no click, and a 403 leaves the card's name and status in place", async () => {
    const fetchMock = stubBackend({ results: { [BASELINE]: { status: 403, body: { detail: "Forbidden" } } } });

    render(<ProjectListScreen />);
    await openProject();
    const results = await settledSection("Baseline");

    expect(within(results).getByText(RESULTS_REFUSED)).toBeVisible();
    expect(resultsCalls(fetchMock, BASELINE)).toHaveLength(1);
    expect(within(card("Baseline")).getByRole("heading", { name: "Baseline" })).toBeVisible();
    expect(within(card("Baseline")).getByText("Status: Draft")).toBeVisible();
    expect(screen.getByRole("table")).toBeVisible();
  });

  it("renders both the card's name/status and the results content together on success", async () => {
    stubBackend({ results: { [BASELINE]: { status: 200, body: baseResults(BASELINE) } } });

    render(<ProjectListScreen />);
    await openProject();
    const results = await settledSection("Baseline");

    expect(within(card("Baseline")).getByRole("heading", { name: "Baseline" })).toBeVisible();
    expect(within(card("Baseline")).getByText("Status: Draft")).toBeVisible();
    expect(within(results).getByText("Revenue: 1000.00 PLN")).toBeVisible();
  });

  it("reads a gated field the shape check refuses as this section's unreadable answer, not a render crash", async () => {
    // Through the running application, so the render boundary is really there: a malformed value
    // that slipped past `isGatedResultFieldShape` would reach `formatMoneyString` and throw mid
    // render, and `ScreenErrorBoundary` would replace the whole project list — every card, not this
    // section (ADR-0010, point 2). This is the mutation K-06 names: a money formatter called on a
    // value that was never shape-checked must not be able to take the card down.
    stubBackend({
      results: {
        [BASELINE]: { status: 200, body: baseResults(BASELINE, { included_cost: "abc" }) },
        [STRETCH]: { status: 200, body: baseResults(STRETCH) },
      },
    });

    render(<App />);
    const main = screen.getByRole("main");
    fireEvent.click(await within(main).findByRole("button", { name: "Aurora migration" }));

    const results = await settledSection("Baseline");
    expect(within(results).getByText(RESULTS_UNREADABLE)).toBeVisible();
    expect(screen.queryByText(SCREEN_CRASH_MESSAGE)).toBeNull();
    expect(within(card("Baseline")).getByRole("heading", { name: "Baseline" })).toBeVisible();
    expect(within(card("Baseline")).getByText("Status: Draft")).toBeVisible();
    expect(within(await settledSection("Stretch")).getByText("Revenue: 1000.00 PLN")).toBeVisible();
  });
});

// --- SC-5-10 --------------------------------------------------------------------------------------

describe("SC-5-10 — each resolved cost-rate window shows only its own unit", () => {
  it("pairs distinct units with their corresponding rates, and follows changed units per window", async () => {
    const withWindows = (id: string, windows: PersonnelCostSource["assumptions_used"]) =>
      baseResults(id, {
        personnel_cost: {
          state: "calculated",
          amount: "400.00",
          currency: "PLN",
          assumptions_used: windows,
        },
      });
    stubBackend({
      results: {
        [BASELINE]: {
          status: 200,
          body: withWindows(BASELINE, {
            rate_windows: [
              { default_cost_rate: "120.00", currency: "PLN", cost_rate_unit: "hour" },
              { default_cost_rate: "900.00", currency: "PLN", cost_rate_unit: "day" },
            ],
          }),
        },
        [STRETCH]: {
          status: 200,
          body: withWindows(STRETCH, {
            rate_windows: [
              { default_cost_rate: "120.00", currency: "PLN", cost_rate_unit: "month" },
              { default_cost_rate: "900.00", currency: "PLN", cost_rate_unit: "hour" },
            ],
          }),
        },
      },
    });

    render(<ProjectListScreen />);
    await openProject();
    const baseline = await settledSection("Baseline");
    expect(within(baseline).getByText("Cost rate: 120.00 PLN per hour")).toBeVisible();
    expect(within(baseline).getByText("Cost rate: 900.00 PLN per day")).toBeVisible();

    const stretch = await settledSection("Stretch");
    expect(within(stretch).getByText("Cost rate: 120.00 PLN per month")).toBeVisible();
    expect(within(stretch).getByText("Cost rate: 900.00 PLN per hour")).toBeVisible();
  });

  it("omits only a missing window unit and restores only that label when supplied", async () => {
    const withWindows = (id: string, windows: PersonnelCostSource["assumptions_used"]) =>
      baseResults(id, {
        personnel_cost: {
          state: "calculated",
          amount: "400.00",
          currency: "PLN",
          assumptions_used: windows,
        },
      });
    stubBackend({
      results: {
        [BASELINE]: {
          status: 200,
          body: withWindows(BASELINE, {
            rate_windows: [
              { default_cost_rate: "120.00", currency: "PLN" },
              { default_cost_rate: "900.00", currency: "PLN", cost_rate_unit: "day" },
            ],
          }),
        },
        [STRETCH]: {
          status: 200,
          body: withWindows(STRETCH, {
            rate_windows: [
              { default_cost_rate: "120.00", currency: "PLN", cost_rate_unit: "hour" },
              { default_cost_rate: "900.00", currency: "PLN", cost_rate_unit: "day" },
            ],
          }),
        },
      },
    });

    render(<ProjectListScreen />);
    await openProject();
    const baseline = await settledSection("Baseline");
    expect(within(baseline).getByText("Cost rate: 120.00 PLN")).toBeVisible();
    expect(within(baseline).queryByText("Cost rate: 120.00 PLN per hour")).toBeNull();
    expect(within(baseline).getByText("Cost rate: 900.00 PLN per day")).toBeVisible();

    const stretch = await settledSection("Stretch");
    expect(within(stretch).getByText("Cost rate: 120.00 PLN per hour")).toBeVisible();
    expect(within(stretch).getByText("Cost rate: 900.00 PLN per day")).toBeVisible();
  });
});

// --- Reviewer R-01 (Issue #94) ---------------------------------------------------------------------

describe("R-01 — a gated field carrying a real number without a calculated revenue is this section's unreadable answer, not a blank currency", () => {
  it("rejects a real profit paired with a withheld revenue, and renders no number for it at all", async () => {
    // The backend's documented invariant (`backend/app/api/schemas/scenario_results.py`, convention
    // only, not enforced by a schema validator on either side): `profit`/`included_cost`/`margin`/
    // `markup` are only ever real numbers when `revenue.state === "calculated"`. A payload that
    // breaks this pairing must not reach `GatedMoneyLine`, which would render the real number with
    // `currency ?? ""` — "550.00 " with no currency code, not an explicit failure.
    stubBackend({
      results: {
        [BASELINE]: {
          status: 200,
          body: baseResults(BASELINE, {
            revenue: revenueWithheld("no_commercial_terms"),
            profit: "550.00",
          }),
        },
      },
    });

    render(<ProjectListScreen />);
    await openProject();
    const results = await settledSection("Baseline");

    expect(within(results).getByText(RESULTS_UNREADABLE)).toBeVisible();
    expect(results.textContent).not.toContain("550.00");
  });
});
