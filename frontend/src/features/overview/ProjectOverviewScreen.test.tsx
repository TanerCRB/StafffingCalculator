import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ProjectListItem, ScenarioStatus } from "../../api/contracts/projects";
import type { ScenarioPeriodResults } from "../../api/contracts/scenarioResults";
import { ProjectOverviewScreen } from "./ProjectOverviewScreen";

const PROJECT_ID = "11111111-1111-1111-1111-111111111111";
const ALPHA_ID = "aaaaaaaa-0000-0000-0000-000000000001";
const BETA_ID = "aaaaaaaa-0000-0000-0000-000000000002";
const PERIOD_PATH = /^\/projects\/([^/]+)\/scenarios\/([^/]+)\/results\/periods$/;

function scenario(id: string, name: string, status: ScenarioStatus = "Draft") {
  return {
    id,
    name,
    status,
    missing_inputs: [],
    ready_for_approval: status === "Approved",
    target_margin_percent: "40.00",
  };
}

const PROJECT: ProjectListItem = {
  id: PROJECT_ID,
  name: "Aurora migration",
  client: "Northwind",
  delivery_period: { start: "2026-01-01", end: "2026-03-31" },
  reporting_currency: "PLN",
  description: "Scenario period overview fixture.",
  status: "Active",
  scenarios: [scenario(ALPHA_ID, "Alpha", "Approved"), scenario(BETA_ID, "Beta")],
};

function period(
  month: string,
  revenue: string,
  personnelCost: string | null,
  periodCost: string | null,
  profit: string | null,
  margin: string | null,
  overrides: Partial<ScenarioPeriodResults["periods"][number]> = {},
) {
  return {
    period_month: month,
    revenue,
    revenue_currency: "PLN",
    personnel_cost: personnelCost,
    personnel_cost_currency: personnelCost === null ? null : "PLN",
    additional_cost: "0.00",
    additional_cost_currency: "PLN",
    period_cost: periodCost,
    period_cost_currency: periodCost === null ? null : "PLN",
    profit,
    margin,
    profitability_state: personnelCost === null ? null : "calculated",
    below_target_margin: margin === null || margin === "n/a" ? null : Number(margin) < 40,
    negative_profit: profit === null || profit === "n/a" ? null : Number(profit) < 0,
    planned_fte: "0.5000000000000000000000000000",
    ...overrides,
  } as const;
}

function periodResults(
  scenarioId: string,
  status: ScenarioStatus = "Approved",
  changes: Partial<ScenarioPeriodResults> = {},
): ScenarioPeriodResults {
  return {
    scenario_id: scenarioId,
    scenario_status: status,
    reporting_currency: "PLN",
    target_margin_percent: "40.00",
    periods: [
      period("2026-01-01", "100.00", "20.00", "20.00", "80.00", "80.00", { below_target_margin: false }),
      period("2026-02-01", "100.00", "80.00", "80.00", "20.00", "20.00", { below_target_margin: true, planned_fte: "0.2500000000000000000000000000" }),
      period("2026-03-01", "0.00", "10.00", "10.00", "-10.00", "n/a", {
        negative_profit: true,
        below_target_margin: null,
        planned_fte: "0.0000000000000000000000000000",
      }),
    ],
    unallocated: {
      revenue: "500.00",
      revenue_currency: "PLN",
      fixed_amount_cost: "30.00",
      fixed_amount_cost_currency: "PLN",
    },
    ...changes,
  };
}

function response(status: number, body: unknown) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  };
}

interface BackendOptions {
  readonly projectsStatus?: number;
  readonly periodAnswer?: (scenarioId: string, call: number) => { status: number; body?: unknown };
}

function stubBackend(options: BackendOptions = {}) {
  let periodCalls = 0;
  const fetchMock = vi.fn((input: string | URL) => {
    const url = new URL(String(input));
    if (url.pathname === "/projects") {
      const status = options.projectsStatus ?? 200;
      return Promise.resolve(response(status, status === 200 ? { projects: [PROJECT], total: 1 } : { detail: "Denied." }));
    }
    const match = PERIOD_PATH.exec(url.pathname);
    if (match !== null) {
      periodCalls += 1;
      const scenarioId = match[2] ?? "";
      const answer = options.periodAnswer?.(scenarioId, periodCalls) ?? {
        status: 200,
        body: periodResults(scenarioId),
      };
      return Promise.resolve(response(answer.status, answer.body));
    }
    throw new Error(`Unexpected request: ${url.pathname}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function selectScenario(name = "Alpha") {
  fireEvent.change(screen.getByLabelText("Project"), { target: { value: PROJECT_ID } });
  fireEvent.change(screen.getByLabelText("Scenario"), { target: { value: name === "Alpha" ? ALPHA_ID : BETA_ID } });
}

function periodRow(month: string): HTMLElement {
  const row = document.querySelector(`[data-period-month="${month}"]`);
  if (!(row instanceof HTMLElement)) throw new Error(`Missing rendered period row ${month}`);
  return row;
}

afterEach(() => vi.unstubAllGlobals());

describe("Overview scenario selection and period results (SC-7-11)", () => {
  it("reads only the selected scenario and replaces its series when another scenario is selected", async () => {
    const fetchMock = stubBackend({
      periodAnswer: (scenarioId) => ({
        status: 200,
        body: periodResults(scenarioId, scenarioId === ALPHA_ID ? "Approved" : "Draft", {
          periods: [period("2026-01-01", scenarioId === ALPHA_ID ? "100.00" : "900.00", "20.00", "20.00", "80.00", "80.00")],
        }),
      }),
    });
    render(<ProjectOverviewScreen />);
    await screen.findByRole("option", { name: "Aurora migration" });

    selectScenario("Alpha");
    expect(await screen.findByText("100.00 PLN")).toBeVisible();
    expect(screen.getByText("Approved snapshot")).toBeVisible();

    fireEvent.change(screen.getByLabelText("Scenario"), { target: { value: BETA_ID } });
    expect(await screen.findByText("900.00 PLN")).toBeVisible();
    expect(screen.queryByText("100.00 PLN")).toBeNull();
    const periodCalls = fetchMock.mock.calls.filter(([input]) => PERIOD_PATH.test(new URL(String(input)).pathname));
    expect(periodCalls.map(([input]) => new URL(String(input)).pathname)).toEqual([
      `/projects/${PROJECT_ID}/scenarios/${ALPHA_ID}/results/periods`,
      `/projects/${PROJECT_ID}/scenarios/${BETA_ID}/results/periods`,
    ]);
  });

  it("shows numeric margins, a zero-denominator Not applicable value, and text cues without color-only meaning", async () => {
    stubBackend();
    render(<ProjectOverviewScreen />);
    await screen.findByRole("option", { name: "Aurora migration" });
    selectScenario();

    const table = await screen.findByRole("table");
    expect(within(periodRow("2026-02-01")).getByText("Below target margin")).toBeVisible();
    expect(within(periodRow("2026-02-01")).getByText("20.00%")).toBeVisible();
    const lossRow = within(periodRow("2026-03-01"));
    expect(lossRow.getByText("-10.00 PLN")).toBeVisible();
    expect(lossRow.getByText("Loss-making period")).toBeVisible();
    expect(lossRow.getByText("Not applicable")).toBeVisible();
    expect(table).toBeVisible();
  });

  it("keeps periodless revenue and fixed costs outside the temporal series as explicit unallocated totals", async () => {
    stubBackend();
    render(<ProjectOverviewScreen />);
    await screen.findByRole("option", { name: "Aurora migration" });
    selectScenario();

    const table = await screen.findByRole("table");
    expect(within(table).getAllByRole("row")).toHaveLength(4);
    expect(screen.getByRole("heading", { name: "Unallocated scenario totals" })).toBeVisible();
    expect(screen.getByText("These values have no reporting period and are kept outside the period series.")).toBeVisible();
    expect(screen.getByText("500.00 PLN")).toBeVisible();
    expect(screen.getByText("30.00 PLN")).toBeVisible();
  });

  it("renders gated personnel and dependent fields as absent values without exposing amounts", async () => {
    stubBackend({
      periodAnswer: (scenarioId) => ({
        status: 200,
        body: periodResults(scenarioId, "Draft", {
          periods: [period("2026-01-01", "100.00", null, null, null, null, {
            personnel_cost_currency: null,
            period_cost_currency: null,
            profitability_state: null,
            below_target_margin: null,
            negative_profit: null,
          })],
          unallocated: {
            revenue: "500.00",
            revenue_currency: "PLN",
            fixed_amount_cost: null,
            fixed_amount_cost_currency: null,
          },
        }),
      }),
    });
    render(<ProjectOverviewScreen />);
    await screen.findByRole("option", { name: "Aurora migration" });
    selectScenario();

    const table = await screen.findByRole("table");
    expect(within(table).getAllByText("Not shown on this screen.")).toHaveLength(4);
    expect(screen.getAllByText("Not shown on this screen.")).toHaveLength(5);
    expect(screen.queryByText("20.00 PLN")).toBeNull();
    expect(screen.queryByText("30.00 PLN")).toBeNull();
  });

  it("shows the refusal state on a denied period read and does not render a partial series", async () => {
    stubBackend({ periodAnswer: () => ({ status: 403 }) });
    render(<ProjectOverviewScreen />);
    await screen.findByRole("option", { name: "Aurora migration" });
    selectScenario();

    expect(await screen.findByText("Scenario period results not shown.")).toBeVisible();
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("allows retry after a scenario read conflict", async () => {
    stubBackend({
      periodAnswer: (scenarioId, call) => call === 1
        ? { status: 409, body: { detail: "The scenario changed." } }
        : { status: 200, body: periodResults(scenarioId) },
    });
    render(<ProjectOverviewScreen />);
    await screen.findByRole("option", { name: "Aurora migration" });
    selectScenario();

    expect(await screen.findByText("Period results could not be loaded — the scenario changed while it was being read. Read again.")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Read period results again" }));
    expect(await screen.findByRole("table")).toBeVisible();
  });

  it("does not request scenario data when project access is denied", async () => {
    const fetchMock = stubBackend({ projectsStatus: 403 });
    render(<ProjectOverviewScreen />);

    expect(await screen.findByText("You do not have permission to view projects.")).toBeVisible();
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(fetchMock.mock.calls.map(([input]) => new URL(String(input)).pathname)).toEqual(["/projects"]);
    expect(screen.queryByRole("combobox", { name: "Scenario" })).toBeNull();
  });
});
