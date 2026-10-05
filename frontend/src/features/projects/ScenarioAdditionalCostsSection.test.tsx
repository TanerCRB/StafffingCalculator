import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CALLER_ID_HEADER } from "../../api/client";
import type { AdditionalCostRead } from "../../api/contracts/additionalCosts";
import { ScenarioAdditionalCostsSection } from "./ScenarioAdditionalCostsSection";
import { ScenarioResultsSection } from "./ScenarioResultsSection";

const PROJECT_ID = "project-1";
const SCENARIO_A = "scenario-a";
const SCENARIO_B = "scenario-b";
const COST_A: AdditionalCostRead = {
  id: "cost-a",
  category_id: "category-cloud",
  category_name: "Cloud",
  position_id: null,
  risk_id: null,
  amount: "12.5000",
  currency: "EUR",
  cost_type: "recurring",
  start_month: "2026-04-01",
  end_month: "2026-06-01",
  funding_source: "rebilled_to_client",
  updated_at: "2026-10-05T12:00:00Z",
};
const COST_B: AdditionalCostRead = { ...COST_A, id: "cost-b", category_name: "Travel" };
const RESULTS_WITH_COST_GATE_CLOSED = {
  scenario_id: SCENARIO_A,
  scenario_status: "Draft",
  revenue: {
    state: "calculated",
    amount: "1000.00",
    currency: "EUR",
    assumptions_used: {
      model_type: null,
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
  },
  personnel_cost: {
    state: "calculated",
    amount: null,
    currency: null,
    assumptions_used: null,
    paid_absence_state: "calculated",
    paid_absence_amount: "0.00",
    paid_absence_currency: "EUR",
  },
  additional_cost: { state: "calculated", amount: "50.00", currency: "EUR" },
  included_cost: null,
  profit: null,
  margin: null,
  markup: null,
  profitability_state: "calculated",
};

function categoryResponse() {
  return {
    entries: [{ id: "category-cloud", name: "Cloud", updated_at: "2026-10-05T12:00:00Z" }],
  };
}

function costsResponse(scenarioId: string, costs: AdditionalCostRead[] = [], status = "Draft") {
  return { scenario_id: scenarioId, scenario_status: status, costs };
}

function response(status: number, body?: unknown) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  };
}

function mount(scenarioId = SCENARIO_A, name = "Baseline") {
  return render(
    <ScenarioAdditionalCostsSection
      projectId={PROJECT_ID}
      scenarioId={scenarioId}
      scenarioName={name}
      reportingCurrency="EUR"
    />,
  );
}

async function openAndFillCreate(overrides: Record<string, string> = {}) {
  fireEvent.click(screen.getByRole("button", { name: "Add cost" }));
  await screen.findByRole("option", { name: "Cloud" });
  fireEvent.change(screen.getByLabelText("Category"), { target: { value: "category-cloud" } });
  fireEvent.change(screen.getByLabelText("Amount"), { target: { value: "88.1250" } });
  fireEvent.change(screen.getByLabelText("Currency"), { target: { value: "PLN" } });
  fireEvent.change(screen.getByLabelText("Period type"), { target: { value: "recurring" } });
  fireEvent.change(screen.getByLabelText("Start month"), { target: { value: "2026-04" } });
  fireEvent.change(screen.getByLabelText("End month"), { target: { value: "2026-06" } });
  fireEvent.change(screen.getByLabelText("Charge basis"), { target: { value: "rebilled_to_client" } });
  for (const [label, value] of Object.entries(overrides)) {
    fireEvent.change(screen.getByLabelText(label), { target: { value } });
  }
}

function installApi(options: {
  readonly costs?: Record<string, AdditionalCostRead[]>;
  readonly post?: (init: RequestInit) => unknown | Promise<unknown>;
  readonly patch?: (init: RequestInit) => unknown | Promise<unknown>;
  readonly deleteStatus?: number;
  readonly statuses?: Record<string, string>;
  readonly results?: unknown;
} = {}) {
  const calls: { url: string; init?: RequestInit }[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input));
    calls.push({ url: url.pathname, init });
    if (url.pathname === `/projects/${PROJECT_ID}/scenarios/${SCENARIO_A}/results`) {
      return response(200, options.results ?? RESULTS_WITH_COST_GATE_CLOSED);
    }
    if (url.pathname === `/projects/${PROJECT_ID}/scenarios/${SCENARIO_A}/additional-costs` && init?.method === undefined) {
      const status = options.statuses?.[SCENARIO_A] ?? "Draft";
      return response(200, costsResponse(SCENARIO_A, options.costs?.[SCENARIO_A] ?? [], status));
    }
    if (url.pathname === `/projects/${PROJECT_ID}/scenarios/${SCENARIO_B}/additional-costs` && init?.method === undefined) {
      return response(200, costsResponse(SCENARIO_B, options.costs?.[SCENARIO_B] ?? []));
    }
    if (url.pathname === "/catalog/dimensions/cost-categories") return response(200, categoryResponse());
    if (init?.method === "POST") {
      const outcome = await options.post?.(init);
      return (outcome ?? response(201, { ...COST_A, id: "created-cost", amount: "88.1250", currency: "PLN" })) as ReturnType<typeof response>;
    }
    if (init?.method === "PATCH") {
      const outcome = await options.patch?.(init);
      return (outcome ?? response(200, {
        ...COST_A,
        id: url.pathname.split("/").at(-1),
        amount: "100.0000",
        currency: "PLN",
      })) as ReturnType<typeof response>;
    }
    if (init?.method === "DELETE") return response(options.deleteStatus ?? 204);
    throw new Error(`Unexpected request: ${init?.method ?? "GET"} ${url.pathname}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  return { calls, fetchMock };
}

afterEach(() => {
  vi.unstubAllGlobals();
  window.sessionStorage.clear();
});

describe("ScenarioAdditionalCostsSection — SC-5-13", () => {
  it("test_sc_5_13_01_lists_only_costs_of_the_selected_scenario", async () => {
    const { calls } = installApi({ costs: { [SCENARIO_A]: [COST_A], [SCENARIO_B]: [COST_B] } });
    render(
      <>
        <ScenarioAdditionalCostsSection projectId={PROJECT_ID} scenarioId={SCENARIO_A} scenarioName="Baseline" reportingCurrency="EUR" />
        <ScenarioAdditionalCostsSection projectId={PROJECT_ID} scenarioId={SCENARIO_B} scenarioName="Alternative" reportingCurrency="EUR" />
      </>,
    );
    const first = await screen.findByRole("region", { name: "Additional costs for Baseline" });
    const second = screen.getByRole("region", { name: "Additional costs for Alternative" });
    expect(within(first).getByText("Cloud")).toBeInTheDocument();
    expect(within(first).queryByText("Travel")).not.toBeInTheDocument();
    expect(within(second).getByText("Travel")).toBeInTheDocument();
    expect(within(second).queryByText("Cloud")).not.toBeInTheDocument();
    expect(calls.filter((call) => call.url.endsWith("additional-costs")).map((call) => call.url)).toEqual([
      `/projects/${PROJECT_ID}/scenarios/${SCENARIO_A}/additional-costs`,
      `/projects/${PROJECT_ID}/scenarios/${SCENARIO_B}/additional-costs`,
    ]);
  });

  it("test_sc_5_13_02_add_and_edit_show_saved_cost_fields", async () => {
    const { calls } = installApi({});
    mount();
    await screen.findByText("No additional costs are recorded for this scenario.");
    await openAndFillCreate();
    await screen.findByRole("option", { name: "Cloud" });
    fireEvent.click(screen.getByRole("button", { name: "Save cost" }));
    expect(await screen.findByText("Saved. The row below is the server's response.")).toBeInTheDocument();
    expect(screen.getByText("88.13 PLN")).toBeInTheDocument();
    expect(screen.getByText("Period: 2026-04 – 2026-06")).toBeInTheDocument();
    expect(screen.getByText("Charge basis: Rebilled to client")).toBeInTheDocument();
    expect(calls.find((call) => call.url.endsWith("additional-costs") && call.init?.method === "POST")?.init?.headers)
      .toMatchObject({ "Idempotency-Key": expect.any(String) });

    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    fireEvent.change(screen.getByLabelText("Amount"), { target: { value: "100.0000" } });
    fireEvent.click(screen.getByRole("button", { name: "Save cost" }));
    await waitFor(() => expect(screen.getByText("100.00 PLN")).toBeInTheDocument());
    expect(calls.some((call) => call.init?.method === "PATCH" && call.url.endsWith("created-cost"))).toBe(true);
  });

  it("test_sc_5_13_03_refused_write_does_not_show_saved_state", async () => {
    installApi({ post: () => response(409, { detail: "Refused by the database." }) });
    mount();
    await screen.findByText("No additional costs are recorded for this scenario.");
    await openAndFillCreate();
    await screen.findByRole("option", { name: "Cloud" });
    fireEvent.click(screen.getByRole("button", { name: "Save cost" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Not saved — the server refused this change.");
    expect(screen.queryByText("88.13 PLN")).not.toBeInTheDocument();
    expect(screen.queryByText("Saved. The row below is the server's response.")).not.toBeInTheDocument();
  });

  it("test_sc_5_13_03_refused_edit_keeps_the_existing_row_and_saved_state", async () => {
    installApi({ costs: { [SCENARIO_A]: [COST_A] }, patch: () => response(409, { detail: "Refused by the database." }) });
    mount();
    await screen.findByText("Cloud");
    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    fireEvent.change(screen.getByLabelText("Amount"), { target: { value: "100.0000" } });
    fireEvent.click(screen.getByRole("button", { name: "Save cost" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Not saved — the server refused this change.");
    expect(screen.getByText("12.50 EUR")).toBeInTheDocument();
    expect(screen.queryByText("100.00 EUR")).not.toBeInTheDocument();
    expect(screen.queryByText("Saved. The row below is the server's response.")).not.toBeInTheDocument();
  });

  it("test_sc_5_13_03_refused_delete_keeps_the_existing_row_and_saved_state", async () => {
    installApi({ costs: { [SCENARIO_A]: [COST_A] }, deleteStatus: 409 });
    mount();
    await screen.findByText("Cloud");
    fireEvent.click(screen.getByRole("button", { name: "Remove" }));
    fireEvent.click(screen.getByRole("button", { name: "Remove" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Not saved — the server refused this change.");
    expect(screen.getByText("12.50 EUR")).toBeInTheDocument();
    expect(screen.queryByText("Removed.")).not.toBeInTheDocument();
  });

  it("test_sc_5_13_02_remove_sends_the_row_marker_and_removes_only_after_success", async () => {
    const { calls } = installApi({ costs: { [SCENARIO_A]: [COST_A] } });
    mount();
    await screen.findByText("Cloud");
    fireEvent.click(screen.getByRole("button", { name: "Remove" }));
    fireEvent.click(screen.getByRole("button", { name: "Remove" }));
    await screen.findByText("Removed.");
    expect(screen.queryByText("Cloud")).not.toBeInTheDocument();
    const deletion = calls.find((call) => call.init?.method === "DELETE");
    expect(JSON.parse(String(deletion?.init?.body))).toEqual({ updated_at: COST_A.updated_at });
  });

  it("test_sc_5_13_04_approved_scenario_cost_write_is_rejected_without_change", async () => {
    installApi({ statuses: { [SCENARIO_A]: "Approved" }, post: () => response(409, { detail: "Scenario is approved." }) });
    mount();
    await screen.findByText("No additional costs are recorded for this scenario.");
    await openAndFillCreate();
    await screen.findByRole("option", { name: "Cloud" });
    fireEvent.click(screen.getByRole("button", { name: "Save cost" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Not saved — the server refused this change.");
    expect(screen.queryByText("88.13 PLN")).not.toBeInTheDocument();
  });

  it("test_sc_5_13_04_approved_scenario_edit_is_rejected_without_change", async () => {
    installApi({
      costs: { [SCENARIO_A]: [COST_A] },
      statuses: { [SCENARIO_A]: "Approved" },
      patch: () => response(409, { detail: "Scenario is approved." }),
    });
    mount();
    await screen.findByText("Cloud");
    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    fireEvent.change(screen.getByLabelText("Amount"), { target: { value: "100.0000" } });
    fireEvent.click(screen.getByRole("button", { name: "Save cost" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Not saved — the server refused this change.");
    expect(screen.getByText("12.50 EUR")).toBeInTheDocument();
    expect(screen.queryByText("100.00 EUR")).not.toBeInTheDocument();
    expect(screen.queryByText("Saved. The row below is the server's response.")).not.toBeInTheDocument();
  });

  it("test_sc_5_13_04_approved_scenario_delete_is_rejected_without_change", async () => {
    installApi({
      costs: { [SCENARIO_A]: [COST_A] },
      statuses: { [SCENARIO_A]: "Approved" },
      deleteStatus: 409,
    });
    mount();
    await screen.findByText("Cloud");
    fireEvent.click(screen.getByRole("button", { name: "Remove" }));
    fireEvent.click(screen.getByRole("button", { name: "Remove" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Not saved — the server refused this change.");
    expect(screen.getByText("12.50 EUR")).toBeInTheDocument();
    expect(screen.queryByText("Removed.")).not.toBeInTheDocument();
  });

  it("test_sc_5_13_05_keeps_additional_cost_results_visibility_contract", async () => {
    installApi({ costs: { [SCENARIO_A]: [COST_A] } });
    render(
      <>
        <ScenarioAdditionalCostsSection projectId={PROJECT_ID} scenarioId={SCENARIO_A} scenarioName="Baseline" reportingCurrency="EUR" />
        <ScenarioResultsSection projectId={PROJECT_ID} scenarioId={SCENARIO_A} scenarioName="Baseline" />
      </>,
    );
    const section = await screen.findByRole("region", { name: "Additional costs for Baseline" });
    expect(within(section).getByText("Cloud")).toBeInTheDocument();
    expect(within(section).getByText("12.50 EUR")).toBeInTheDocument();
    expect(await screen.findByText("Additional costs: 50.00 EUR")).toBeInTheDocument();
    expect(screen.getByText("Base personnel cost: Not shown on this screen.")).toBeInTheDocument();
  });

  it("test_sc_5_13_06_retry_reuses_the_same_idempotency_key", async () => {
    let first = true;
    const keys: string[] = [];
    installApi({
      post: (init) => {
        keys.push(new Headers(init.headers).get("Idempotency-Key") ?? "");
        if (first) {
          first = false;
          throw new Error("connection lost after request dispatch");
        }
        return response(201, { ...COST_A, id: "created-cost", amount: "88.1250", currency: "PLN" });
      },
    });
    const firstMount = mount();
    await screen.findByText("No additional costs are recorded for this scenario.");
    await openAndFillCreate();
    await screen.findByRole("option", { name: "Cloud" });
    fireEvent.click(screen.getByRole("button", { name: "Save cost" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Unresolved —");
    firstMount.unmount();
    mount();
    await screen.findByText("No additional costs are recorded for this scenario.");
    await openAndFillCreate();
    fireEvent.click(screen.getByRole("button", { name: "Save cost" }));
    await screen.findByText("Saved. The row below is the server's response.");
    expect(keys).toHaveLength(2);
    expect(keys[0]).not.toBe("");
    expect(keys[1]).toBe(keys[0]);
  });

  it("test_sc_5_13_07_denied_replay_response_is_not_reported_as_saved", async () => {
    let first = true;
    const keys: string[] = [];
    installApi({
      post: (init) => {
        keys.push(new Headers(init.headers).get("Idempotency-Key") ?? "");
        if (first) {
          first = false;
          throw new Error("first response was lost");
        }
        return response(403);
      },
    });
    mount();
    await screen.findByText("No additional costs are recorded for this scenario.");
    await openAndFillCreate();
    await screen.findByRole("option", { name: "Cloud" });
    fireEvent.click(screen.getByRole("button", { name: "Save cost" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Unresolved");
    fireEvent.click(screen.getByRole("button", { name: "Save cost" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Not saved — the server denied this change.");
    expect(screen.queryByText("Saved. The row below is the server's response.")).not.toBeInTheDocument();
    expect(keys).toHaveLength(2);
    expect(keys[1]).toBe(keys[0]);
  });

  it("uses CATALOG_READ for the category dictionary and STAFFING_READ for scenario costs", async () => {
    const { calls } = installApi({});
    mount();
    await screen.findByText("No additional costs are recorded for this scenario.");
    await openAndFillCreate();
    await screen.findByRole("option", { name: "Cloud" });
    const costRead = calls.find((call) => call.url.endsWith("additional-costs"));
    const categoryRead = calls.find((call) => call.url === "/catalog/dimensions/cost-categories");
    expect(new Headers(costRead?.init?.headers).has(CALLER_ID_HEADER)).toBe(true);
    expect(new Headers(categoryRead?.init?.headers).has(CALLER_ID_HEADER)).toBe(true);
  });
});
