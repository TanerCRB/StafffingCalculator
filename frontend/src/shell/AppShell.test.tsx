import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AppShell } from "./AppShell";

/**
 * The shell is chrome. The claims worth pinning are the ones a screenshot cannot make: that the
 * rail says which screens exist and which do not, that it invents no entry for a screen with
 * nothing behind it, that it reads nothing from the API, and that the status indicator the App
 * test looks for is in the topbar.
 */

const SCREEN_CONTENT = "Screen placed in the shell";

function renderShell(backendStatus: "checking" | "ok" | "unreachable" = "ok") {
  return render(
    <AppShell backendStatus={backendStatus}>
      <p>{SCREEN_CONTENT}</p>
    </AppShell>,
  );
}

function rail() {
  return screen.getByRole("navigation", { name: "Sections" });
}

describe("AppShell", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("names the application and what it is for", () => {
    renderShell();

    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("StafffingCalculator");
    expect(
      screen.getByText("IT project staffing, cost, and profitability planner."),
    ).toBeInTheDocument();
  });

  it("offers the Roles & rates entry as a named control announced as not yet implemented", () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    renderShell();

    const entry = within(rail()).getByRole("button", { name: "Roles & rates" });

    // Visible and in the tab order — the shape of the product ahead of its implementation (F-13),
    // not a hidden entry.
    expect(entry).toBeVisible();
    expect(entry.tabIndex).toBe(0);
    entry.focus();
    expect(entry).toHaveFocus();
    expect(entry).toHaveAttribute("aria-disabled", "true");
    expect(entry).toHaveAttribute("title", expect.stringContaining("Not implemented yet"));

    fireEvent.click(entry);

    // Pressing it changes nothing: the screen in the frame is still the one that was there, the
    // entry is still announced as not yet implemented, and nothing was read from the API — a rail
    // entry that prefetched the screen behind it would be a read the user was never offered.
    expect(screen.getByText(SCREEN_CONTENT)).toBeInTheDocument();
    expect(entry).toHaveAttribute("aria-disabled", "true");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("marks Projects as the current screen and does not offer it as a control", () => {
    renderShell();

    const current = within(rail()).getByText("Projects");
    expect(current).toHaveAttribute("aria-current", "page");
    // The contrast with the entry above: the two states of a rail entry must not collapse onto
    // one. The screen the user is already on is not a button, and it is not announced as pending.
    expect(current).not.toHaveAttribute("aria-disabled");
    expect(within(rail()).queryByRole("button", { name: "Projects" })).toBeNull();
    expect(within(rail()).getByRole("button", { name: "Roles & rates" })).not.toHaveAttribute(
      "aria-current",
    );
  });

  it("lists only the screens this application has, not the screens the design proposal shows", () => {
    renderShell();

    // Two entries, both named. An entry for a screen with no backend behind it would be a promise
    // the product cannot keep, and — unlike a disabled entry — nothing about it would say so.
    const entries = within(rail()).getAllByRole("listitem");
    expect(entries.map((entry) => entry.textContent)).toEqual(["Projects", "Roles & rates"]);

    for (const absent of [
      "Compare scenarios",
      "Working calendars",
      "Organization defaults",
      "Staffing plan",
      "Commercial terms",
      "Versions & approval",
    ]) {
      expect(within(rail()).queryByText(absent)).toBeNull();
    }
  });

  it("puts the screen it frames inside the main landmark, reachable past the rail", () => {
    renderShell();

    const main = screen.getByRole("main");
    expect(within(main).getByText(SCREEN_CONTENT)).toBeInTheDocument();

    // The rail comes before the content in the document, so there is a way past it that does not
    // depend on tabbing through every entry.
    const skip = screen.getByRole("link", { name: "Skip to content" });
    expect(skip.getAttribute("href")).toBe(`#${main.id}`);
    expect(main.id).not.toBe("");
  });

  it("shows the backend status in the topbar, as a word rather than a colour", () => {
    renderShell("unreachable");

    const status = within(screen.getByRole("banner")).getByTestId("backend-status");
    expect(status).toHaveTextContent("Backend: unreachable");
    // `data-state` only selects the fill; the state is readable without it.
    expect(status).toHaveAttribute("data-state", "unreachable");
  });

  it("says where the user is without offering a trail of controls that navigate nowhere", () => {
    renderShell();

    const breadcrumb = screen.getByRole("navigation", { name: "Breadcrumb" });
    expect(breadcrumb).toHaveTextContent("Workspace");
    expect(within(breadcrumb).getByText("Projects")).toHaveAttribute("aria-current", "page");
    expect(within(breadcrumb).queryAllByRole("link")).toHaveLength(0);
    expect(within(breadcrumb).queryAllByRole("button")).toHaveLength(0);
  });
});
