import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";

/**
 * Answers every read the running application makes, by path — health, the project list and the
 * six catalogue reads. A single blanket `{ status: "ok" }` would make the screens fail their
 * contract checks and render failure states, which is not what a navigation test is about.
 */
function stubRunningBackend() {
  const fetchMock = vi.fn(async (url: string) => {
    const path = new URL(url).pathname;
    const body: Record<string, unknown> = {
      "/health": { status: "ok" },
      "/projects": { projects: [], total: 0 },
      "/catalog/rates": { rates: [], total: 0 },
      "/catalog/dimensions/roles": { entries: [] },
      "/catalog/dimensions/seniorities": { entries: [] },
      "/catalog/dimensions/locations": { entries: [] },
      "/catalog/dimensions/engagement-types": { entries: [] },
      "/catalog/dimensions/vendors": { entries: [] },
      "/catalog/working-calendars": { calendars: [] },
      "/catalog/absence-budgets": { budgets: [] },
      "/organization-defaults": { target_margin_percent: "10", overload_threshold_percent: "20", updated_at: "2026-10-06T10:00:00Z" },
    }[path] ?? {};
    return { ok: true, status: 200, json: async () => body };
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function requestedPaths(fetchMock: ReturnType<typeof stubRunningBackend>): string[] {
  return fetchMock.mock.calls.map(([url]) => new URL(url as string).pathname);
}

describe("App", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows backend status ok when the health check succeeds", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, json: async () => ({ status: "ok" }) }),
    );

    render(<App />);

    await waitFor(() => expect(screen.getByTestId("backend-status")).toHaveTextContent("ok"));
  });

  it("shows backend status unreachable when the health check fails", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 500 }));

    render(<App />);

    await waitFor(() =>
      expect(screen.getByTestId("backend-status")).toHaveTextContent("unreachable"),
    );
  });

  it("frames the project list in the shell, with the backend status in the topbar", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, json: async () => ({ status: "ok" }) }),
    );

    render(<App />);

    // The indicator moved into the chrome; it is one element, in the banner, not a second copy
    // left behind on the screen itself.
    await waitFor(() =>
      expect(
        within(screen.getByRole("banner")).getByTestId("backend-status"),
      ).toHaveTextContent("ok"),
    );
    expect(screen.getAllByTestId("backend-status")).toHaveLength(1);

    // The screen the shell frames is inside the main landmark, below the rail.
    const main = screen.getByRole("main");
    expect(within(main).getByRole("heading", { name: "Projects" })).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Sections" })).toBeInTheDocument();
  });

  it("opens Overview for the selected Projects row with that project's scenarios", async () => {
    const alpha = {
      id: "aaaaaaaa-0000-0000-0000-000000000001",
      name: "Alpha forecast",
      status: "Draft",
      missing_inputs: [],
      ready_for_approval: false,
      target_margin_percent: "20.00",
    };
    const beta = { ...alpha, id: "bbbbbbbb-0000-0000-0000-000000000001", name: "Beta forecast" };
    const projects = [
      { id: "11111111-1111-1111-1111-111111111111", name: "Aurora", client: "Northwind", delivery_period: { start: "2026-01-01", end: "2026-12-31" }, reporting_currency: "PLN", description: "", status: "Active", scenarios: [alpha] },
      { id: "22222222-2222-2222-2222-222222222222", name: "Helios", client: "Contoso", delivery_period: { start: "2026-01-01", end: "2026-12-31" }, reporting_currency: "EUR", description: "", status: "Active", scenarios: [beta] },
    ];
    const fetchMock = vi.fn(async (url: string) => {
      const path = new URL(url).pathname;
      const body = path === "/health" ? { status: "ok" }
        : path === "/projects" ? { projects, total: projects.length }
          : {};
      return { ok: true, status: 200, json: async () => body };
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);
    await screen.findByRole("button", { name: "View Aurora" });
    fireEvent.click(screen.getByRole("button", { name: "View Aurora" }));

    const main = screen.getByRole("main");
    expect(await within(main).findByRole("heading", { level: 2, name: "Aurora" })).toBeVisible();
    const scenarioPicker = within(main).getByRole("combobox", { name: "Scenario" });
    expect(within(scenarioPicker).getByRole("option", { name: "Alpha forecast" })).toBeInTheDocument();
    expect(within(scenarioPicker).queryByRole("option", { name: "Beta forecast" })).toBeNull();

    fireEvent.click(within(screen.getByRole("navigation", { name: "Sections" })).getByRole("button", { name: "Projects" }));
    await screen.findByRole("button", { name: "View Helios" });
    fireEvent.click(screen.getByRole("button", { name: "View Helios" }));
    expect(await within(main).findByRole("heading", { level: 2, name: "Helios" })).toBeVisible();
    const heliosScenarioPicker = within(main).getByRole("combobox", { name: "Scenario" });
    expect(within(heliosScenarioPicker).getByRole("option", { name: "Beta forecast" })).toBeInTheDocument();
    expect(within(heliosScenarioPicker).queryByRole("option", { name: "Alpha forecast" })).toBeNull();
  });

  // --- SC-2-02, K-09 ---------------------------------------------------------------------------

  it("makes the catalogue screen reachable from the running application, not only from its own test", async () => {
    const fetchMock = stubRunningBackend();

    render(<App />);
    const main = screen.getByRole("main");

    // The contrast, and the half that a screen mounted unconditionally would fail: before the rail
    // entry is activated there is no catalogue screen at all — and, because it is not mounted, the
    // application has not read the catalogue behind the user's back either.
    expect(await within(main).findByRole("heading", { name: "Projects" })).toBeVisible();
    expect(within(main).queryByRole("heading", { name: "Roles & rates" })).toBeNull();
    expect(requestedPaths(fetchMock)).not.toContain("/catalog/rates");

    fireEvent.click(
      within(screen.getByRole("navigation", { name: "Sections" })).getByRole("button", {
        name: "Roles & rates",
      }),
    );

    // The catalogue is mounted, inside the same frame, and it read the catalogue for itself.
    expect(within(main).getByRole("heading", { name: "Roles & rates" })).toBeVisible();
    await waitFor(() =>
      expect(requestedPaths(fetchMock)).toContain("/catalog/dimensions/engagement-types"),
    );

    // And the project list is unmounted, not merely hidden behind it: the screen it was framing is
    // gone from the DOM, along with the controls that belong to it.
    expect(within(main).queryByRole("heading", { name: "Projects" })).toBeNull();
    expect(screen.queryByRole("searchbox", { name: "Search projects" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Add project" })).toBeNull();

    // The breadcrumb follows, so the chrome does not go on naming the screen that left.
    const breadcrumb = screen.getByRole("navigation", { name: "Breadcrumb" });
    expect(within(breadcrumb).getByText("Roles & rates")).toHaveAttribute("aria-current", "page");
    expect(within(breadcrumb).queryByText("Projects")).toBeNull();
  });

  // --- SC-3-06 -------------------------------------------------------------------------------------

  it("makes the working calendars screen reachable from the running application, not only from its own test", async () => {
    const fetchMock = stubRunningBackend();

    render(<App />);
    const main = screen.getByRole("main");

    expect(await within(main).findByRole("heading", { name: "Projects" })).toBeVisible();
    expect(within(main).queryByRole("heading", { name: "Working calendars" })).toBeNull();
    expect(requestedPaths(fetchMock)).not.toContain("/catalog/working-calendars");

    fireEvent.click(
      within(screen.getByRole("navigation", { name: "Sections" })).getByRole("button", {
        name: "Working calendars",
      }),
    );

    expect(within(main).getByRole("heading", { name: "Working calendars" })).toBeVisible();
    await waitFor(() =>
      expect(requestedPaths(fetchMock)).toEqual(
        expect.arrayContaining([
          "/catalog/working-calendars",
          "/catalog/absence-budgets",
          "/catalog/dimensions/engagement-types",
        ]),
      ),
    );

    // The project list is unmounted, not merely hidden behind it.
    expect(within(main).queryByRole("heading", { name: "Projects" })).toBeNull();

    const breadcrumb = screen.getByRole("navigation", { name: "Breadcrumb" });
    expect(within(breadcrumb).getByText("Working calendars")).toHaveAttribute(
      "aria-current",
      "page",
    );
  });

  it("K-04 reaches and saves organization defaults without project access", async () => {
    const requests: Array<{ path: string; init?: RequestInit }> = [];
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      const path = new URL(url).pathname;
      requests.push({ path, init });
      if (path === "/health") return { ok: true, status: 200, json: async () => ({ status: "ok" }) };
      if (path === "/projects") return { ok: false, status: 403, json: async () => ({ detail: "denied" }) };
      if (path === "/organization-defaults") {
        const body = init?.method === "PATCH"
          ? { target_margin_percent: "15", overload_threshold_percent: "20", updated_at: "2026-10-06T10:01:00Z" }
          : { target_margin_percent: "10", overload_threshold_percent: "20", updated_at: "2026-10-06T10:00:00Z" };
        return { ok: true, status: 200, json: async () => body };
      }
      return { ok: true, status: 200, json: async () => ({}) };
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);
    await screen.findByText("You do not have permission to view projects.");
    fireEvent.click(within(screen.getByRole("navigation", { name: "Sections" })).getByRole("button", { name: "Organization defaults" }));

    const main = screen.getByRole("main");
    expect(await within(main).findByRole("heading", { level: 2, name: "Organization defaults" })).toBeVisible();
    const target = await within(main).findByLabelText("Organization Target margin");
    fireEvent.change(target, { target: { value: "15" } });
    fireEvent.click(within(main).getAllByRole("button", { name: "Save override" })[0]!);
    expect(await within(main).findByRole("status")).toHaveTextContent("Saved.");
    expect(requests.filter(({ path }) => path === "/organization-defaults").map(({ init }) => init?.method ?? "GET"))
      .toEqual(["GET", "PATCH"]);
    expect(requests.find(({ path, init }) => path === "/organization-defaults" && init?.method === "PATCH")?.init?.body)
      .toBe(JSON.stringify({ updated_at: "2026-10-06T10:00:00Z", target_margin_percent: "15" }));
  });

  // --- SC-7-04 ---------------------------------------------------------------------------------

  it("makes the compare-scenarios screen reachable from the running application, not only from its own test", async () => {
    const fetchMock = stubRunningBackend();

    render(<App />);
    const main = screen.getByRole("main");

    expect(await within(main).findByRole("heading", { name: "Projects" })).toBeVisible();
    expect(within(main).queryByRole("heading", { name: "Compare scenarios" })).toBeNull();
    expect(requestedPaths(fetchMock).filter((path) => path === "/projects")).toHaveLength(1);

    fireEvent.click(
      within(screen.getByRole("navigation", { name: "Sections" })).getByRole("button", {
        name: "Compare scenarios",
      }),
    );

    expect(within(main).getByRole("heading", { name: "Compare scenarios" })).toBeVisible();
    // Its own read of `/projects` — a second call, made by the new screen, not a reuse of the
    // project list screen's own state (the two screens are unmounted from one another).
    await waitFor(() =>
      expect(requestedPaths(fetchMock).filter((path) => path === "/projects")).toHaveLength(2),
    );

    // The project list is unmounted, not merely hidden behind it.
    expect(within(main).queryByRole("heading", { name: "Projects" })).toBeNull();

    const breadcrumb = screen.getByRole("navigation", { name: "Breadcrumb" });
    expect(within(breadcrumb).getByText("Compare scenarios")).toHaveAttribute(
      "aria-current",
      "page",
    );
  });

  // --- Reviewer R-03 -----------------------------------------------------------------------------

  it("moves keyboard focus to the new screen's heading after a rail activation, in both directions", async () => {
    stubRunningBackend();

    render(<App />);
    await screen.findByRole("heading", { name: "Projects" });

    const rail = screen.getByRole("navigation", { name: "Sections" });

    fireEvent.click(within(rail).getByRole("button", { name: "Roles & rates" }));

    // The element that held focus a moment ago — the "Roles & rates" rail button — is now gone
    // (replaced by the current-screen `<span>`), so without the fix focus would have dropped to
    // `document.body` here.
    const catalogHeading = await screen.findByRole("heading", { name: "Roles & rates" });
    expect(catalogHeading).toHaveFocus();
    expect(document.body).not.toHaveFocus();

    fireEvent.click(within(rail).getByRole("button", { name: "Projects" }));

    const projectsHeading = await screen.findByRole("heading", { name: "Projects" });
    expect(projectsHeading).toHaveFocus();
    expect(document.body).not.toHaveFocus();
  });
});
