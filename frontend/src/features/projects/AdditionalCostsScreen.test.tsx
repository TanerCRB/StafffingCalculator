import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { App } from "../../App";
import type { AdditionalCostRead } from "../../api/contracts/additionalCosts";
import type { ProjectListItem } from "../../api/contracts/projects";
import { APPROVED_ADDITIONAL_COSTS_NOTE, NO_COSTS, READ_DENIED, READ_FAILED, READ_LOADING, READ_NOT_FOUND } from "./additionalCostsText";
import { COSTS_NO_SCENARIOS, COSTS_NO_SELECTION, COSTS_PROJECTS_FAILED } from "./additionalCostsScreenText";

const P = "11111111-1111-1111-1111-111111111111";
const Q = "22222222-2222-2222-2222-222222222222";
const A = "aaaaaaaa-0000-0000-0000-000000000001";
const B = "bbbbbbbb-0000-0000-0000-000000000002";
const C = "cccccccc-0000-0000-0000-000000000003";

function project(id: string, name: string, scenarios: { id: string; name: string; status?: "Draft" | "Approved" }[]): ProjectListItem {
  return {
    id, name, client: "Client", description: "", status: "Active", reporting_currency: "EUR",
    delivery_period: { start: "2026-01-01", end: "2026-12-31" },
    scenarios: scenarios.map((scenario) => ({ ...scenario, status: scenario.status ?? "Draft", missing_inputs: [],
      ready_for_approval: false, target_margin_percent: null })),
  };
}

function cost(id: string, name: string): AdditionalCostRead {
  return {
    id, category_id: "category-1", category_name: name, position_id: null, risk_id: null,
    amount: "12.5000", currency: "EUR", cost_type: "one_off", start_month: "2026-04-01",
    end_month: null, funding_source: "internal", updated_at: "2026-10-05T12:00:00Z",
  };
}

type Answer = { status: number; body?: unknown; gate?: Promise<void> };
const ok = (scenarioId: string, costs: AdditionalCostRead[] = []): Answer =>
  ({ status: 200, body: { scenario_id: scenarioId, scenario_status: "Draft", costs } });
const path = (projectId: string, scenarioId: string) =>
  `/projects/${projectId}/scenarios/${scenarioId}/additional-costs`;

function backend(projects: ProjectListItem[], answers: Record<string, Answer> = {}, projectAnswer?: Answer | ((url: URL) => Answer)) {
  const fetchMock = vi.fn(async (url: string) => {
    const requestUrl = new URL(url);
    const pathname = requestUrl.pathname;
    const answer: Answer = pathname === "/health" ? { status: 200, body: { status: "ok" } }
      : pathname === "/projects" ? (typeof projectAnswer === "function" ? projectAnswer(requestUrl) : projectAnswer)
        ?? { status: 200, body: { projects, total: projects.length } }
        : answers[pathname] ?? { status: 500, body: {} };
    if (answer.gate) await answer.gate;
    return { ok: answer.status >= 200 && answer.status < 300, status: answer.status,
      json: async () => answer.body };
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

async function enter() {
  const rail = screen.getByRole("navigation", { name: "Sections" });
  const item = within(rail).getByRole("button", { name: "Additional costs" });
  expect(item).not.toHaveAttribute("aria-disabled");
  fireEvent.click(item);
  const heading = within(screen.getByRole("main")).getByRole("heading", { level: 2, name: "Additional costs" });
  expect(heading).toHaveFocus();
  expect(within(rail).getByText("Additional costs")).toHaveAttribute("aria-current", "page");
}

async function choose(projectId: string, scenarioId: string) {
  fireEvent.change(await screen.findByRole("combobox", { name: "Project" }), { target: { value: projectId } });
  fireEvent.change(screen.getByRole("combobox", { name: "Scenario" }), { target: { value: scenarioId } });
}

describe("SC-5-14 Additional costs destination", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("K-01/K-03: opens a focused cost view; selection gates the read and a planned entry stays inert", async () => {
    const fetchMock = backend([project(P, "Aurora", [{ id: A, name: "Baseline" }])], { [path(P, A)]: ok(A) });
    render(<App />);
    await enter();
    expect(await screen.findByText(COSTS_NO_SELECTION)).toBeVisible();
    expect(fetchMock.mock.calls.map(([url]) => new URL(url).pathname)).not.toContain(path(P, A));
    fireEvent.click(within(screen.getByRole("navigation", { name: "Sections" }))
      .getByRole("button", { name: "Commercial terms" }));
    expect(screen.getByText(COSTS_NO_SELECTION)).toBeVisible();
    await choose(P, A);
    expect(await screen.findByText(NO_COSTS)).toBeVisible();
    expect(fetchMock.mock.calls.map(([url]) => new URL(url).pathname)).toContain(path(P, A));
  });

  it("K-02/K-07: only the current scenario's single saved row survives late reads across scenarios and projects", async () => {
    let releaseA!: () => void;
    const gate = new Promise<void>((resolve) => { releaseA = resolve; });
    const fetchMock = backend([
      project(P, "Aurora", [{ id: A, name: "Baseline" }, { id: B, name: "Stretch" }]),
      project(Q, "Helios", [{ id: C, name: "Recovery" }]),
    ], {
      [path(P, A)]: { ...ok(A, [cost("old", "Old cloud")]), gate },
      [path(P, B)]: ok(B, [cost("new", "New travel")]),
      [path(Q, C)]: ok(C, [cost("other", "Other licence")]),
    });
    render(<App />);
    await enter();
    await choose(P, A);
    await waitFor(() => expect(fetchMock.mock.calls.map(([url]) => new URL(url).pathname)).toContain(path(P, A)));
    fireEvent.change(screen.getByRole("combobox", { name: "Scenario" }), { target: { value: B } });
    expect(await screen.findByText("New travel")).toBeVisible();
    await act(async () => releaseA());
    expect(screen.queryByText("Old cloud")).toBeNull();
    expect(screen.getAllByText("New travel")).toHaveLength(1);
    expect(screen.queryByText(/Additional cost total/i)).toBeNull();
    await choose(Q, C);
    expect(await screen.findByText("Other licence")).toBeVisible();
    expect(screen.queryByText("New travel")).toBeNull();
  });

  it("K-02 contrast: changing only the scenario hides its previous saved row while the next read is pending", async () => {
    let releaseB!: () => void;
    const gate = new Promise<void>((resolve) => { releaseB = resolve; });
    backend([project(P, "Aurora", [{ id: A, name: "Baseline" }, { id: B, name: "Stretch" }])], {
      [path(P, A)]: ok(A, [cost("old", "Old cloud")]),
      [path(P, B)]: { ...ok(B, [cost("new", "New travel")]), gate },
    });
    render(<App />);
    await enter();
    await choose(P, A);
    expect(await screen.findByText("Old cloud")).toBeVisible();

    fireEvent.change(screen.getByRole("combobox", { name: "Scenario" }), { target: { value: B } });
    expect(screen.getByText(READ_LOADING)).toBeVisible();
    expect(screen.queryByText("Old cloud")).toBeNull();
    await act(async () => releaseB());
    expect(await screen.findByText("New travel")).toBeVisible();
    expect(screen.queryByText("Old cloud")).toBeNull();
  });

  it("K-01: a project beyond the first API page remains selectable", async () => {
    const first = Array.from({ length: 20 }, (_, index) => project(`project-${index}`, `Project ${index}`, []));
    const later = project(Q, "Helios", [{ id: C, name: "Recovery" }]);
    const fetchMock = backend([], { [path(Q, C)]: ok(C, [cost("later", "Travel")]) }, (url) => ({
      status: 200,
      body: url.searchParams.get("offset") === "0"
        ? { projects: first, total: 21 } : { projects: [later], total: 21 },
    }));
    render(<App />);
    await enter();
    expect(await screen.findByText("Page 1 of 2")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(await screen.findByText("Page 2 of 2")).toBeVisible();
    await choose(Q, C);
    expect(await screen.findByText("Travel")).toBeVisible();
    expect(fetchMock.mock.calls.map(([url]) => new URL(url).searchParams.get("offset"))).toContain("20");
  });

  it("K-05: API-listed Approved scenario retains saved rows without write controls", async () => {
    backend([project(P, "Aurora", [{ id: A, name: "Baseline", status: "Approved" }])], {
      [path(P, A)]: { ...ok(A, [cost("saved", "Cloud")]), body: {
        scenario_id: A, scenario_status: "Approved", costs: [cost("saved", "Cloud")],
      } },
    });
    render(<App />);
    await enter();
    await choose(P, A);
    expect(await screen.findByText("Cloud")).toBeVisible();
    expect(screen.getByText(APPROVED_ADDITIONAL_COSTS_NOTE)).toBeVisible();
    expect(screen.queryByRole("button", { name: "Add cost" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Edit" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Remove" })).toBeNull();
  });

  it("K-05 contrast: approval reported by the cost read overrides a stale Draft project listing", async () => {
    backend([project(P, "Aurora", [{ id: A, name: "Baseline", status: "Draft" }])], {
      [path(P, A)]: { status: 200, body: {
        scenario_id: A, scenario_status: "Approved", costs: [cost("saved", "Cloud")],
      } },
    });
    render(<App />);
    await enter();
    await choose(P, A);
    expect(await screen.findByText("Cloud")).toBeVisible();
    expect(screen.getByText(APPROVED_ADDITIONAL_COSTS_NOTE)).toBeVisible();
    expect(screen.queryByRole("button", { name: "Add cost" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Edit" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Remove" })).toBeNull();
  });

  it("K-03: no scenarios, pending, empty, denied, missing, and failed reads stay distinct", async () => {
    let release!: () => void;
    const gate = new Promise<void>((resolve) => { release = resolve; });
    const projects = [project(P, "Aurora", [
      { id: A, name: "Pending" }, { id: B, name: "Denied" }, { id: C, name: "Missing" },
    ]), project(Q, "Helios", [])];
    backend(projects, {
      [path(P, A)]: { ...ok(A), gate }, [path(P, B)]: { status: 403 }, [path(P, C)]: { status: 404 },
    });
    render(<App />);
    await enter();
    fireEvent.change(await screen.findByRole("combobox", { name: "Project" }), { target: { value: Q } });
    expect(screen.getByText(COSTS_NO_SCENARIOS)).toBeVisible();
    await choose(P, A);
    expect(screen.getByText(READ_LOADING)).toBeVisible();
    await act(async () => release());
    expect(await screen.findByText(NO_COSTS)).toBeVisible();
    fireEvent.change(screen.getByRole("combobox", { name: "Scenario" }), { target: { value: B } });
    expect(await screen.findByText(READ_DENIED)).toBeVisible();
    fireEvent.change(screen.getByRole("combobox", { name: "Scenario" }), { target: { value: C } });
    expect(await screen.findByText(READ_NOT_FOUND)).toBeVisible();
  });

  it("K-03 contrast: project-list failure and cost-read failure offer separate recovery", async () => {
    const projects = [project(P, "Aurora", [{ id: A, name: "Baseline" }])];
    backend(projects, {}, { status: 500 });
    const view = render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "Additional costs" }));
    expect(await screen.findByText(COSTS_PROJECTS_FAILED)).toBeVisible();
    view.unmount();
    backend(projects, { [path(P, A)]: { status: 500 } });
    render(<App />);
    await enter();
    await choose(P, A);
    expect(await screen.findByText(READ_FAILED)).toBeVisible();
    expect(screen.getByRole("button", { name: "Read additional costs again" })).toBeVisible();
    expect(screen.queryByText(NO_COSTS)).toBeNull();
  });
});
