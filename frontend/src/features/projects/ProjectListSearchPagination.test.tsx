import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ProjectListItem } from "../../api/contracts/projects";
import { ProjectListScreen } from "./ProjectListScreen";

function project(index: number, status: ProjectListItem["status"] = "Active"): ProjectListItem {
  return {
    id: `10000000-0000-0000-0000-${String(index).padStart(12, "0")}`,
    name: `Project ${index}`,
    client: `Client ${index}`,
    delivery_period: { start: "2026-01-01", end: "2026-12-31" },
    reporting_currency: "EUR",
    description: "Project list fixture",
    status,
    scenarios: [],
  };
}

const firstPage = Array.from({ length: 20 }, (_, index) => project(index + 1));
const secondPage = [project(21)];
const match = project(30, "Archived");

function stubListApi(options: { readonly accessibleTotal?: number } = {}) {
  const accessibleTotal = options.accessibleTotal ?? 21;
  const fetchMock = vi.fn(async (input: string | URL | Request) => {
    const url = new URL(String(input));
    const params = url.searchParams;
    let body: { projects: ProjectListItem[]; total: number };
    if (params.get("search") === "nothing") {
      body = { projects: [], total: 0 };
    } else if (params.get("search") === "Orchid" || params.get("status") === "Archived") {
      body = { projects: [match], total: 1 };
    } else if (Number(params.get("offset")) === 20) {
      body = { projects: secondPage, total: accessibleTotal };
    } else {
      body = {
        projects: accessibleTotal === 0 ? [] : firstPage.slice(0, accessibleTotal),
        total: accessibleTotal,
      };
    }
    return { ok: true, status: 200, json: async () => body };
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function requestedUrl(fetchMock: ReturnType<typeof stubListApi>, index: number): URL {
  return new URL(String(fetchMock.mock.calls[index]?.[0]));
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("project search and pagination", () => {
  it("sends server-side search, status and page parameters together, and reset clears them", async () => {
    const fetchMock = stubListApi();
    render(<ProjectListScreen />);
    await screen.findByRole("table");

    const initial = requestedUrl(fetchMock, 0).searchParams;
    expect(initial.get("limit")).toBe("20");
    expect(initial.get("offset")).toBe("0");
    expect(initial.has("search")).toBe(false);
    expect(initial.has("status")).toBe(false);

    fireEvent.change(screen.getByRole("searchbox", { name: "Search projects" }), {
      target: { value: "Orchid" },
    });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    await screen.findByRole("row", { name: /Project 30/ });
    fireEvent.change(screen.getByRole("combobox", { name: "Filter projects by status" }), {
      target: { value: "Archived" },
    });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));

    const combined = requestedUrl(fetchMock, 2).searchParams;
    expect(combined.get("search")).toBe("Orchid");
    expect(combined.get("status")).toBe("Archived");
    expect(combined.get("limit")).toBe("20");
    expect(combined.get("offset")).toBe("0");

    fireEvent.click(screen.getByRole("button", { name: "Reset filters" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(4));
    const reset = requestedUrl(fetchMock, 3).searchParams;
    expect(reset.has("search")).toBe(false);
    expect(reset.has("status")).toBe(false);
    expect(reset.get("offset")).toBe("0");
    expect(screen.getByRole("searchbox", { name: "Search projects" })).toHaveValue("");
    expect(screen.getByRole("combobox", { name: "Filter projects by status" })).toHaveValue("");
  });

  it("renders a server-returned second page and resets to offset zero when criteria change", async () => {
    const fetchMock = stubListApi();
    render(<ProjectListScreen />);
    await screen.findByText("Project 1", { selector: "button" });
    expect(within(await screen.findByRole("table")).getAllByRole("row")).toHaveLength(21);

    fireEvent.click(screen.getByRole("button", { name: "Next page" }));
    await screen.findByText("Project 21", { selector: "button" });
    expect(requestedUrl(fetchMock, 1).searchParams.get("offset")).toBe("20");
    expect(screen.getByText("Page 2 of 2")).toBeVisible();

    fireEvent.change(screen.getByRole("searchbox", { name: "Search projects" }), {
      target: { value: "Orchid" },
    });
    await screen.findByRole("row", { name: /Project 30/ });
    expect(requestedUrl(fetchMock, 2).searchParams.get("offset")).toBe("0");
    expect(screen.getByText("Page 1 of 1")).toBeVisible();
  });

  it("resets to offset zero when status changes from a later page", async () => {
    const fetchMock = stubListApi();
    render(<ProjectListScreen />);
    await screen.findByText("Project 1", { selector: "button" });

    fireEvent.click(screen.getByRole("button", { name: "Next page" }));
    await screen.findByRole("row", { name: /Project 21/ });
    expect(requestedUrl(fetchMock, 1).searchParams.get("offset")).toBe("20");

    fireEvent.change(screen.getByRole("combobox", { name: "Filter projects by status" }), {
      target: { value: "Archived" },
    });
    await screen.findByRole("row", { name: /Project 30/ });
    const changedStatus = requestedUrl(fetchMock, 2).searchParams;
    expect(changedStatus.get("status")).toBe("Archived");
    expect(changedStatus.get("offset")).toBe("0");
    expect(screen.getByText("Page 1 of 1")).toBeVisible();
  });

  it("distinguishes an empty caller-scoped list from a search with no matches", async () => {
    const noAccessibleProjects = stubListApi({ accessibleTotal: 0 });
    const firstRender = render(<ProjectListScreen />);
    expect(await screen.findByText("No projects to show.")).toBeVisible();
    expect(requestedUrl(noAccessibleProjects, 0).searchParams.has("search")).toBe(false);
    firstRender.unmount();

    const accessibleProjects = stubListApi({ accessibleTotal: 1 });
    render(<ProjectListScreen />);
    await screen.findByText("Project 1", { selector: "button" });
    fireEvent.change(screen.getByRole("searchbox", { name: "Search projects" }), {
      target: { value: "nothing" },
    });
    expect(await screen.findByText("No projects match your search and filters.")).toBeVisible();
    expect(requestedUrl(accessibleProjects, 1).searchParams.get("search")).toBe("nothing");
    expect(screen.queryByText("No projects to show.")).toBeNull();
  });

  it("keeps criteria available and retries the same query after a transient read failure", async () => {
    let failSearch = true;
    const fetchMock = vi.fn(async (input: string | URL | Request) => {
      const url = new URL(String(input));
      if (url.searchParams.get("search") === "Orchid" && failSearch) {
        failSearch = false;
        return { ok: false, status: 503, json: async () => ({}) };
      }
      const body = url.searchParams.get("search") === "Orchid"
        ? { projects: [match], total: 1 }
        : { projects: firstPage, total: 21 };
      return { ok: true, status: 200, json: async () => body };
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<ProjectListScreen />);
    await screen.findByRole("table");

    fireEvent.change(screen.getByRole("searchbox", { name: "Search projects" }), {
      target: { value: "Orchid" },
    });
    await screen.findByText("Projects could not be loaded.");
    expect(screen.getByRole("searchbox", { name: "Search projects" })).toHaveValue("Orchid");

    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    await screen.findByRole("row", { name: /Project 30/ });
    expect(requestedUrl(fetchMock, 2).searchParams.get("search")).toBe("Orchid");
  });
});
