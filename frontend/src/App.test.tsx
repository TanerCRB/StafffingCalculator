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
      "/projects": { projects: [] },
      "/catalog/rates": { rates: [], total: 0 },
      "/catalog/dimensions/roles": { entries: [] },
      "/catalog/dimensions/seniorities": { entries: [] },
      "/catalog/dimensions/locations": { entries: [] },
      "/catalog/dimensions/engagement-types": { entries: [] },
      "/catalog/dimensions/vendors": { entries: [] },
      "/catalog/working-calendars": { calendars: [] },
      "/catalog/absence-budgets": { budgets: [] },
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
