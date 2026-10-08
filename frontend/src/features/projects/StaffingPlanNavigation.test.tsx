import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { App } from "../../App";
import type { ProjectListItem } from "../../api/contracts/projects";
import type { StaffingPositionRead } from "../../api/contracts/staffing";
import { STAFFING_EMPTY, STAFFING_FAILED, STAFFING_REFUSED } from "./staffingPlanText";

const A = "aaaaaaaa-0000-0000-0000-000000000001";
const B = "bbbbbbbb-0000-0000-0000-000000000002";
const C = "cccccccc-0000-0000-0000-000000000003";
const P1 = "11111111-1111-1111-1111-111111111111";
const P2 = "22222222-2222-2222-2222-222222222222";

function project(id: string, name: string, scenarios: { id: string; name: string }[]): ProjectListItem {
  return {
    id, name, client: "Client", description: "", status: "Active", reporting_currency: "EUR",
    delivery_period: { start: "2026-01-01", end: "2026-12-31" },
    scenarios: scenarios.map((item) => ({ ...item, status: "Draft", missing_inputs: [],
      ready_for_approval: false, target_margin_percent: null })),
  };
}

const aurora = project(P1, "Aurora", [{ id: A, name: "Baseline" }, { id: B, name: "Stretch" }]);
const helios = project(P2, "Helios", [{ id: C, name: "Recovery" }]);

function position(headcount: number): StaffingPositionRead {
  return {
    id: `dddddddd-0000-0000-0000-00000000000${headcount}`,
    role_id: "eeeeeeee-0000-0000-0000-000000000001",
    seniority_id: "eeeeeeee-0000-0000-0000-000000000002",
    location_id: "eeeeeeee-0000-0000-0000-000000000003",
    engagement_type_id: "eeeeeeee-0000-0000-0000-000000000004",
    headcount, start_date: "2026-01-01", end_date: null,
    updated_at: "2026-01-01T00:00:00.123456+00:00", allocations: [], absences: [],
  };
}

type Answer = { status: number; body: unknown; gate?: Promise<void> };
const success = (positions: StaffingPositionRead[] = []): Answer => ({ status: 200, body: { positions } });

function stubBackend(projects: ProjectListItem[], staffing: Record<string, Answer>) {
  const fetchMock = vi.fn(async (url: string, init: RequestInit = {}) => {
    const path = new URL(url).pathname;
    let answer: Answer;
    if (path === "/health") answer = { status: 200, body: { status: "ok" } };
    else if (path === "/projects") answer = { status: 200, body: { projects, total: projects.length } };
    else if (path.startsWith("/catalog/dimensions/")) answer = { status: 200, body: { entries: [] } };
    else if (path === "/catalog/absence-types") answer = { status: 200, body: { absence_types: [] } };
    else if (path.endsWith("/staffing-positions")) answer = staffing[path] ?? success();
    else return new Promise<never>((_resolve, reject) => {
      init.signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")));
    });
    if (answer.gate) await answer.gate;
    return { ok: answer.status >= 200 && answer.status < 300, status: answer.status,
      json: async () => answer.body };
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function staffingPath(projectId: string, scenarioId: string) {
  return `/projects/${projectId}/scenarios/${scenarioId}/staffing-positions`;
}

function requestedStaffing(fetchMock: ReturnType<typeof stubBackend>) {
  return fetchMock.mock.calls.map(([url]) => new URL(url as string).pathname)
    .filter((path) => path.endsWith("/staffing-positions"));
}

async function enterStaffingPlan() {
  const rail = screen.getByRole("navigation", { name: "Sections" });
  const entry = within(rail).getByRole("button", { name: "Staffing plan" });
  expect(entry).not.toHaveAttribute("aria-disabled");
  expect(entry).not.toHaveAttribute("title");
  fireEvent.click(entry);
  const heading = await within(screen.getByRole("main")).findByRole("heading", { level: 2, name: "Staffing plan" });
  expect(heading).toHaveFocus();
  expect(within(rail).getByText("Staffing plan")).toHaveAttribute("aria-current", "page");
  expect(within(screen.getByRole("navigation", { name: "Breadcrumb" })).getByText("Staffing plan"))
    .toHaveAttribute("aria-current", "page");
}

function section(scenarioName: string) {
  const card = screen.getByRole("heading", { name: scenarioName }).closest("li");
  if (!card) throw new Error(`No card for ${scenarioName}`);
  return within(card).getByRole("region", { name: "Staffing plan" });
}

describe("SC-3-10 Staffing plan navigation", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("K-01/K-02: live rail reaches project selection without reading staffing, then distinguishes an empty plan", async () => {
    const fetchMock = stubBackend([aurora], { [staffingPath(P1, A)]: success() });
    render(<App />);
    await enterStaffingPlan();
    expect(await screen.findByText("Select a project to see its scenarios.")).toBeVisible();
    expect(requestedStaffing(fetchMock)).toEqual([]);
    expect(within(screen.getByRole("navigation", { name: "Sections" }))
      .getByRole("button", { name: "Additional costs" })).toHaveAttribute("aria-disabled", "true");
    fireEvent.click(screen.getByRole("button", { name: "Aurora" }));
    expect(await within(section("Baseline")).findByText(STAFFING_EMPTY)).toBeVisible();
    expect(requestedStaffing(fetchMock)).toContain(staffingPath(P1, A));
  });

  it("K-03: two scenario cards read their own IDs and display their own staffing", async () => {
    const fetchMock = stubBackend([aurora], {
      [staffingPath(P1, A)]: success([position(2)]),
      [staffingPath(P1, B)]: success([position(3)]),
    });
    render(<App />);
    await enterStaffingPlan();
    fireEvent.click(await screen.findByRole("button", { name: "Aurora" }));
    expect(await within(section("Baseline")).findByText("Headcount: 2")).toBeVisible();
    expect(await within(section("Stretch")).findByText("Headcount: 3")).toBeVisible();
    expect(within(section("Baseline")).queryByText("Headcount: 3")).toBeNull();
    expect(within(section("Stretch")).queryByText("Headcount: 2")).toBeNull();
    expect(requestedStaffing(fetchMock).sort()).toEqual([staffingPath(P1, A), staffingPath(P1, B)].sort());
  });

  it("K-04: a late response from the previous project cannot appear under the newly selected project", async () => {
    let releaseA!: () => void;
    const gate = new Promise<void>((resolve) => { releaseA = resolve; });
    const fetchMock = stubBackend([aurora, helios], {
      [staffingPath(P1, A)]: { ...success([position(8)]), gate },
      [staffingPath(P1, B)]: success(),
      [staffingPath(P2, C)]: success([position(4)]),
    });
    render(<App />);
    await enterStaffingPlan();
    fireEvent.click(await screen.findByRole("button", { name: "Aurora" }));
    await waitFor(() => expect(requestedStaffing(fetchMock)).toContain(staffingPath(P1, A)));
    fireEvent.click(screen.getByRole("button", { name: "Helios" }));
    expect(await within(section("Recovery")).findByText("Headcount: 4")).toBeVisible();
    const abandonedRead = fetchMock.mock.calls.find(([url]) =>
      new URL(url).pathname === staffingPath(P1, A));
    expect(abandonedRead?.[1]?.signal?.aborted).toBe(true);
    await act(async () => releaseA());
    expect(screen.queryByText("Headcount: 8")).toBeNull();
    expect(screen.queryByRole("heading", { name: "Baseline" })).toBeNull();
  });

  it("K-04 contrast: the same late response displays while its project remains selected", async () => {
    let releaseA!: () => void;
    const gate = new Promise<void>((resolve) => { releaseA = resolve; });
    const fetchMock = stubBackend([aurora, helios], {
      [staffingPath(P1, A)]: { ...success([position(8)]), gate },
      [staffingPath(P1, B)]: success(),
      [staffingPath(P2, C)]: success([position(4)]),
    });
    render(<App />);
    await enterStaffingPlan();
    fireEvent.click(await screen.findByRole("button", { name: "Aurora" }));
    await waitFor(() => expect(requestedStaffing(fetchMock)).toContain(staffingPath(P1, A)));
    await act(async () => releaseA());
    expect(await within(section("Baseline")).findByText("Headcount: 8")).toBeVisible();
    const retainedRead = fetchMock.mock.calls.find(([url]) =>
      new URL(url).pathname === staffingPath(P1, A));
    expect(retainedRead?.[1]?.signal?.aborted).toBe(false);
  });

  it("K-05/K-06: loading, permission refusal, and API failure stay distinct from empty and populated plans", async () => {
    let release!: () => void;
    const gate = new Promise<void>((resolve) => { release = resolve; });
    const fetchMock = stubBackend([aurora], {
      [staffingPath(P1, A)]: { ...success(), gate },
      [staffingPath(P1, B)]: { status: 403, body: { detail: "Forbidden" } },
    });
    const mounted = render(<App />);
    await enterStaffingPlan();
    fireEvent.click(await screen.findByRole("button", { name: "Aurora" }));
    expect(within(section("Baseline")).getByText("Loading staffing plan.")).toBeVisible();
    expect(await within(section("Stretch")).findByText(STAFFING_REFUSED)).toBeVisible();
    expect(within(section("Stretch")).queryByText(/Headcount:/)).toBeNull();
    await act(async () => release());
    expect(await within(section("Baseline")).findByText(STAFFING_EMPTY)).toBeVisible();
    mounted.unmount();

    stubBackend([aurora], {
      [staffingPath(P1, A)]: { status: 500, body: { detail: "Failure" } },
      [staffingPath(P1, B)]: success([position(3)]),
    });
    render(<App />);
    await enterStaffingPlan();
    fireEvent.click(await screen.findByRole("button", { name: "Aurora" }));
    expect(await within(section("Baseline")).findByText(STAFFING_FAILED)).toBeVisible();
    expect(within(section("Baseline")).queryByText(STAFFING_EMPTY)).toBeNull();
    expect(await within(section("Stretch")).findByText("Headcount: 3")).toBeVisible();
    expect(requestedStaffing(fetchMock)).toEqual([staffingPath(P1, A), staffingPath(P1, B)]);
  });

  it("K-06: granting the staffing read changes the same scenario from refusal to its returned data", async () => {
    const path = staffingPath(P1, A);
    const refusedFetch = stubBackend([aurora], { [path]: { status: 403, body: { detail: "Forbidden" } } });
    const mounted = render(<App />);
    await enterStaffingPlan();
    fireEvent.click(await screen.findByRole("button", { name: "Aurora" }));
    expect(await within(section("Baseline")).findByText(STAFFING_REFUSED)).toBeVisible();
    expect(within(section("Baseline")).queryByText(/Headcount:/)).toBeNull();
    expect(requestedStaffing(refusedFetch)).toContain(path);
    mounted.unmount();

    const grantedFetch = stubBackend([aurora], { [path]: success([position(2)]) });
    render(<App />);
    await enterStaffingPlan();
    fireEvent.click(await screen.findByRole("button", { name: "Aurora" }));
    expect(await within(section("Baseline")).findByText("Headcount: 2")).toBeVisible();
    expect(within(section("Baseline")).queryByText(STAFFING_REFUSED)).toBeNull();
    expect(requestedStaffing(grantedFetch)).toContain(path);
  });

  it("K-07: an out-of-scope project is not selectable, while a granted project is selectable and readable", async () => {
    const deniedFetch = stubBackend([aurora], { [staffingPath(P1, A)]: success() });
    const mounted = render(<App />);
    await enterStaffingPlan();
    expect(await screen.findByRole("button", { name: "Aurora" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "Helios" })).toBeNull();
    expect(requestedStaffing(deniedFetch)).toEqual([]);
    mounted.unmount();

    const grantedFetch = stubBackend([aurora, helios], { [staffingPath(P2, C)]: success([position(4)]) });
    render(<App />);
    await enterStaffingPlan();
    fireEvent.click(await screen.findByRole("button", { name: "Helios" }));
    expect(await within(section("Recovery")).findByText("Headcount: 4")).toBeVisible();
    expect(requestedStaffing(grantedFetch)).toEqual([staffingPath(P2, C)]);
  });
});
