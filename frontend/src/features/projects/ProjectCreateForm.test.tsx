import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ProjectListScreen } from "./ProjectListScreen";
import type { ProjectListItem } from "../../api/contracts/projects";
function memoryStorage(): Storage {
  const values = new Map<string, string>();
  return {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => { values.set(key, value); },
    removeItem: (key) => { values.delete(key); },
    clear: () => values.clear(),
    key: (index) => [...values.keys()][index] ?? null,
    get length() { return values.size; },
  };
}
let defaultTabStorage: Storage;
let testStorages: Storage[];
beforeEach(() => {
  defaultTabStorage = memoryStorage();
  testStorages = [defaultTabStorage];
  Object.defineProperty(window, "sessionStorage", { configurable: true, value: defaultTabStorage });
});
afterEach(() => {
  vi.unstubAllGlobals();
  testStorages.forEach((storage) => storage.clear());
});
function stubApi(refusals: number[] = []) {
  let id = 0;
  const posts = [] as unknown as (Record<string, unknown>[] & { idempotencyKeys: string[] });
  posts.idempotencyKeys = [];
  const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => {
    if (init?.method === "POST") {
      const body = JSON.parse(String(init.body)) as Record<string, unknown>;
      posts.push(body);
      posts.idempotencyKeys.push(new Headers(init?.headers).get("Idempotency-Key") ?? "");
      const status = refusals.shift() ?? 201;
      if (status !== 201) return { ok: false, status, json: async () => ({ detail: status === 409
        ? "Idempotency key was already used for a different or unavailable project." : "refused" }) };
      id += 1;
      return { ok: true, status, json: async () => ({ ...body, id: "project-" + id, status: "Active", scenarios: [], updated_at: "2026-10-04T12:00:00Z", target_margin_percent: null, overload_threshold_percent: null }) };
    }
    return { ok: true, status: 200, json: async () => ({ projects: [], total: 0 }) };
  });
  vi.stubGlobal("fetch", fetchMock);
  return posts;
}
function openForm() { fireEvent.click(screen.getByRole("button", { name: "Add project" })); }
function fill(overrides: Record<string, string> = {}, container?: HTMLElement) {
  const query = container === undefined ? screen : within(container);
  const fields = {
    "Project name": "Atlas migration", Client: "Contoso", Owner: "Morgan Lee",
    "Delivery period start": "2026-01-01", "Delivery period end": "2026-12-31",
    "Reporting currency": "EUR", Description: "Move the platform.", ...overrides,
  };
  for (const [label, value] of Object.entries(fields)) fireEvent.change(query.getByLabelText(label), { target: { value } });
}
describe("project creation from Projects", () => {
  function projectRow(id: string, name: string): ProjectListItem {
    return {
      id, name, client: "Contoso", delivery_period: { start: "2026-01-01", end: "2026-12-31" },
      reporting_currency: "EUR", description: "", status: "Active", scenarios: [],
    };
  }

  it("K-01 submits all six fields; one changed value changes only that request value", async () => {
    const posts = stubApi(); render(<ProjectListScreen />);
    await screen.findByText("No projects to show.");
    openForm(); fill(); fireEvent.click(screen.getByRole("button", { name: "Create project" }));
    await screen.findByText("Scenarios of Atlas migration");
    openForm(); fill({ "Project name": "Atlas modernization" });
    fireEvent.click(screen.getByRole("button", { name: "Create project" }));
    await screen.findByText("Scenarios of Atlas modernization");
    expect([...posts]).toEqual([
      { name: "Atlas migration", client: "Contoso", owner: "Morgan Lee", delivery_period: { start: "2026-01-01", end: "2026-12-31" }, reporting_currency: "EUR", description: "Move the platform." },
      { name: "Atlas modernization", client: "Contoso", owner: "Morgan Lee", delivery_period: { start: "2026-01-01", end: "2026-12-31" }, reporting_currency: "EUR", description: "Move the platform." },
    ]);
  });
  it("K-02 validates a missing value; correcting only that value submits successfully", async () => {
    const posts = stubApi(); render(<ProjectListScreen />);
    await screen.findByText("No projects to show.");
    openForm(); fill({ Owner: "" });
    fireEvent.click(screen.getByRole("button", { name: "Create project" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Enter a name, client, owner");
    expect(posts).toHaveLength(0);
    fireEvent.change(screen.getByLabelText("Owner"), { target: { value: "Morgan Lee" } });
    fireEvent.click(screen.getByRole("button", { name: "Create project" }));
    expect(await screen.findByText("Scenarios of Atlas migration")).toBeVisible();
    expect(posts).toHaveLength(1);
    expect(posts[0].owner).toBe("Morgan Lee");
  });
  it("K-03 keeps the successful response visibly selected without inventing a list row", async () => {
    stubApi(); render(<ProjectListScreen />);
    await screen.findByText("No projects to show.");
    openForm(); fill({ "Project name": "Changed project name" });
    fireEvent.click(screen.getByRole("button", { name: "Create project" }));
    expect(await screen.findByText("Scenarios of Changed project name")).toBeVisible();
  });
  it("K-04 shows a refusal without a selected project and selects it after success", async () => {
    stubApi([409, 201]); render(<ProjectListScreen />);
    await screen.findByText("No projects to show.");
    openForm(); fill(); fireEvent.click(screen.getByRole("button", { name: "Create project" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("could not replay this request");
    expect(screen.queryByRole("button", { name: "Atlas migration" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry project creation" }));
    expect(await screen.findByText("Scenarios of Atlas migration")).toBeVisible();
    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
  });
  it("K-05 denies the same request without PROJECT_CREATE and accepts it when permission is granted", async () => {
    const posts = stubApi([403, 201]); render(<ProjectListScreen />);
    await screen.findByText("No projects to show.");
    openForm(); fill(); fireEvent.click(screen.getByRole("button", { name: "Create project" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("You do not have permission");
    expect(screen.queryByRole("button", { name: "Atlas migration" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry project creation" }));
    expect(await screen.findByText("Scenarios of Atlas migration")).toBeVisible();
    expect(posts).toHaveLength(2);
    expect(posts[1]).toEqual(posts[0]);
  });

  it("R-02 reuses the key after lost response and remount, while storing no form data", async () => {
    const requests: { body: Record<string, unknown>; key: string }[] = [];
    const expected = {
      name: "Atlas migration", client: "Contoso", owner: "Morgan Lee",
      delivery_period: { start: "2026-01-01", end: "2026-12-31" },
      reporting_currency: "EUR", description: "Move the platform.",
    };
    const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => {
      if (init?.method === "POST") {
        requests.push({
          body: JSON.parse(String(init.body)) as Record<string, unknown>,
          key: new Headers(init.headers).get("Idempotency-Key") ?? "",
        });
        if (requests.length === 1) throw new TypeError("Network connection lost");
        if (JSON.stringify(requests.at(-1)?.body) !== JSON.stringify(expected)) {
          return { ok: false, status: 409, json: async () => ({ detail: "Idempotency key was already used for a different or unavailable project." }) };
        }
        return { ok: true, status: 201, json: async () => ({ ...expected, id: "recovered-project", status: "Active", scenarios: [], updated_at: "2026-10-04T12:00:00Z", target_margin_percent: null, overload_threshold_percent: null }) };
      }
      return { ok: true, status: 200, json: async () => ({ projects: [], total: 0 }) };
    });
    vi.stubGlobal("fetch", fetchMock);
    const first = render(<ProjectListScreen />);
    await screen.findByText("No projects to show.");
    openForm();
    fill();
    fireEvent.click(screen.getByRole("button", { name: "Create project" }));

    expect(await screen.findByText(/browser keeps only a recovery key/)).toBeVisible();
    expect(await screen.findByRole("alert")).toHaveTextContent("result may be unknown");
    const key = window.sessionStorage.getItem("stafffingcalculator.pending-project-create-key");
    expect(key).toMatch(/^[0-9a-f-]{36}$/i);
    expect(window.sessionStorage.length).toBe(1);
    expect(window.sessionStorage.getItem("stafffingcalculator.pending-project-create-key")).not.toContain("Morgan Lee");
    expect(requests).toHaveLength(1);
    expect(requests[0].key).toBe(key);

    first.unmount();
    render(<ProjectListScreen />);
    await screen.findByText("No projects to show.");
    expect(screen.getByRole("heading", { name: "Create a project" })).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "Retry project creation" })).toBeEnabled();

    fill({ Description: "Changed after reload." });
    fireEvent.click(screen.getByRole("button", { name: "Retry project creation" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("could not replay this request");
    expect(requests).toHaveLength(2);
    expect(requests[1].key).toBe(key);
    expect(window.sessionStorage.getItem("stafffingcalculator.pending-project-create-key")).toBe(key);

    fireEvent.change(screen.getByLabelText("Description"), { target: { value: expected.description } });
    fireEvent.click(screen.getByRole("button", { name: "Retry project creation" }));
    expect(await screen.findByText("Scenarios of Atlas migration")).toBeVisible();
    expect(requests).toHaveLength(3);
    expect(requests.map((request) => request.key)).toEqual([key, key, key]);
    expect(requests[2].body).toEqual(expected);
    expect(window.sessionStorage.getItem("stafffingcalculator.pending-project-create-key")).toBeNull();
  });

  it("R-04 resolves a copied pending key explicitly without clearing the original tab", async () => {
    const originalTab = memoryStorage();
    const duplicatedTab = memoryStorage();
    testStorages.push(originalTab, duplicatedTab);
    const originalKey = "11111111-1111-4111-8111-111111111111";
    originalTab.setItem("stafffingcalculator.pending-project-create-key", originalKey);
    duplicatedTab.setItem("stafffingcalculator.pending-project-create-key", originalKey);
    const requests: { body: Record<string, unknown>; key: string; resolve: (value: unknown) => void }[] = [];
    const originalBody = {
      name: "Original recovery", client: "Contoso", owner: "Morgan Lee",
      delivery_period: { start: "2026-01-01", end: "2026-12-31" },
      reporting_currency: "EUR", description: "Move the platform.",
    };
    const fetchMock = vi.fn(async (_url: string, init?: RequestInit): Promise<unknown> => {
      if (init?.method === "POST") {
        const body = JSON.parse(String(init.body)) as Record<string, unknown>;
        const key = new Headers(init.headers).get("Idempotency-Key") ?? "";
        requests.push({ body, key, resolve: () => undefined });
        if (key === originalKey && JSON.stringify(body) !== JSON.stringify(originalBody)) {
          return { ok: false, status: 409, json: async () => ({
            detail: "Idempotency key was already used for a different or unavailable project.",
          }) };
        }
        return new Promise((resolve) => {
          requests[requests.length - 1].resolve = resolve;
        });
      }
      return { ok: true, status: 200, json: async () => ({ projects: [], total: 0 }) };
    });
    vi.stubGlobal("fetch", fetchMock);
    const { container } = render(
      <>
        <ProjectListScreen recoveryStorage={originalTab} />
        <ProjectListScreen recoveryStorage={duplicatedTab} />
      </>,
    );
    await waitFor(() => expect(screen.getAllByText("No projects to show.")).toHaveLength(2));
    const forms = container.querySelectorAll<HTMLFormElement>("form.project-create-form");
    expect(forms).toHaveLength(2);
    fill({ "Project name": "Original recovery" }, forms[0]);
    fill({ "Project name": "Separate project", Client: "Fabrikam", Owner: "Casey Jones" }, forms[1]);

    fireEvent.click(within(forms[1]).getByRole("button", { name: "Retry project creation" }));
    expect(await within(forms[1]).findByRole("alert")).toHaveTextContent("could not replay this request");
    expect(duplicatedTab.getItem("stafffingcalculator.pending-project-create-key")).toBe(originalKey);
    expect(originalTab.getItem("stafffingcalculator.pending-project-create-key")).toBe(originalKey);
    expect(within(forms[1]).getByRole("button", {
      name: "I checked the Projects list; start a separate project",
    })).toBeEnabled();
    expect(within(forms[0]).queryByRole("button", {
      name: "I checked the Projects list; start a separate project",
    })).not.toBeInTheDocument();

    fireEvent.click(within(forms[1]).getByRole("button", {
      name: "I checked the Projects list; start a separate project",
    }));
    expect(duplicatedTab.getItem("stafffingcalculator.pending-project-create-key")).toBeNull();
    expect(originalTab.getItem("stafffingcalculator.pending-project-create-key")).toBe(originalKey);
    expect(within(forms[1]).getByLabelText("Project name")).toHaveValue("Separate project");
    fireEvent.click(within(forms[1]).getByRole("button", { name: "Create project" }));
    await waitFor(() => expect(requests).toHaveLength(2));
    const separateKey = requests[1].key;
    expect(separateKey).toMatch(/^[0-9a-f-]{36}$/i);
    expect(separateKey).not.toBe(originalKey);
    requests[1].resolve({
      ok: true, status: 201,
      json: async () => ({ ...requests[1].body, id: "separate-project", status: "Active", scenarios: [], updated_at: "2026-10-04T12:00:00Z", target_margin_percent: null, overload_threshold_percent: null }),
    });
    expect(await screen.findByText("Scenarios of Separate project")).toBeVisible();

    fireEvent.click(within(forms[0]).getByRole("button", { name: "Retry project creation" }));
    await waitFor(() => expect(requests).toHaveLength(3));
    expect(requests.map((request) => request.key)).toEqual([originalKey, separateKey, originalKey]);
    expect(requests[2].body).toEqual(originalBody);
    requests[2].resolve({
      ok: true, status: 201,
      json: async () => ({ ...originalBody, id: "original-project", status: "Active", scenarios: [], updated_at: "2026-10-04T12:00:00Z", target_margin_percent: null, overload_threshold_percent: null }),
    });
    expect(await screen.findByText("Scenarios of Original recovery")).toBeVisible();
    expect(originalTab.getItem("stafffingcalculator.pending-project-create-key")).toBeNull();
  });

  it("R-03 isolates concurrent project creates across tab session storage", async () => {
    const firstTab = memoryStorage();
    const secondTab = memoryStorage();
    testStorages.push(firstTab, secondTab);
    const requests: { body: Record<string, unknown>; key: string; resolve: (value: unknown) => void }[] = [];
    const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => {
      if (init?.method === "POST") {
        const body = JSON.parse(String(init.body)) as Record<string, unknown>;
        return new Promise<unknown>((resolve) => {
          requests.push({
            body,
            key: new Headers(init.headers).get("Idempotency-Key") ?? "",
            resolve,
          });
        });
      }
      return { ok: true, status: 200, json: async () => ({ projects: [], total: 0 }) };
    });
    vi.stubGlobal("fetch", fetchMock);
    const { container } = render(
      <>
        <ProjectListScreen recoveryStorage={firstTab} />
        <ProjectListScreen recoveryStorage={secondTab} />
      </>,
    );
    await waitFor(() => expect(screen.getAllByText("No projects to show.")).toHaveLength(2));
    const addButtons = screen.getAllByRole("button", { name: "Add project" });
    fireEvent.click(addButtons[0]);
    fireEvent.click(addButtons[1]);
    const forms = container.querySelectorAll<HTMLFormElement>("form.project-create-form");
    expect(forms).toHaveLength(2);
    fill({ "Project name": "Northstar", Client: "Northwind", Owner: "Alex A" }, forms[0]);
    fill({ "Project name": "Southridge", Client: "Southwind", Owner: "Casey B" }, forms[1]);
    fireEvent.click(within(forms[0]).getByRole("button", { name: "Create project" }));
    fireEvent.click(within(forms[1]).getByRole("button", { name: "Create project" }));
    await waitFor(() => expect(requests).toHaveLength(2));

    const [firstRequest, secondRequest] = requests;
    expect(firstRequest.key).toMatch(/^[0-9a-f-]{36}$/i);
    expect(secondRequest.key).toMatch(/^[0-9a-f-]{36}$/i);
    expect(firstRequest.key).not.toBe(secondRequest.key);
    expect(firstTab.getItem("stafffingcalculator.pending-project-create-key")).toBe(firstRequest.key);
    expect(secondTab.getItem("stafffingcalculator.pending-project-create-key")).toBe(secondRequest.key);
    expect(firstRequest.body.name).toBe("Northstar");
    expect(secondRequest.body.name).toBe("Southridge");

    firstRequest.resolve({
      ok: true, status: 201,
      json: async () => ({ ...firstRequest.body, id: "northstar", status: "Active", scenarios: [], updated_at: "2026-10-04T12:00:00Z", target_margin_percent: null, overload_threshold_percent: null }),
    });
    secondRequest.resolve({
      ok: true, status: 201,
      json: async () => ({ ...secondRequest.body, id: "southridge", status: "Active", scenarios: [], updated_at: "2026-10-04T12:00:00Z", target_margin_percent: null, overload_threshold_percent: null }),
    });
    expect(await screen.findByText("Scenarios of Northstar")).toBeVisible();
    expect(await screen.findByText("Scenarios of Southridge")).toBeVisible();
    expect(firstTab.getItem("stafffingcalculator.pending-project-create-key")).toBeNull();
    expect(secondTab.getItem("stafffingcalculator.pending-project-create-key")).toBeNull();
  });

  it("R-01 keeps the form mounted and blocks dismissal during a pending POST", async () => {
    let resolvePost: (() => void) | undefined;
    const posts: Record<string, unknown>[] = [];
    const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => {
      if (init?.method === "POST") {
        const body = JSON.parse(String(init.body)) as Record<string, unknown>;
        posts.push(body);
        return new Promise<unknown>((resolve) => {
          resolvePost = () => resolve({
            ok: true,
            status: 201,
            json: async () => ({ ...body, id: "pending-project", status: "Active", scenarios: [], updated_at: "2026-10-04T12:00:00Z", target_margin_percent: null, overload_threshold_percent: null }),
          });
        });
      }
      return { ok: true, status: 200, json: async () => ({ projects: [], total: 0 }) };
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<ProjectListScreen />);
    await screen.findByText("No projects to show.");
    openForm();
    fill();
    fireEvent.click(screen.getByRole("button", { name: "Create project" }));
    await waitFor(() => expect(posts).toHaveLength(1));

    const close = screen.getByRole("button", { name: "Close create form" });
    const cancel = screen.getByRole("button", { name: "Cancel" });
    expect(close).toBeDisabled();
    expect(cancel).toBeDisabled();
    fireEvent.click(close);
    fireEvent.click(cancel);
    expect(screen.getByRole("heading", { name: "Create a project" })).toBeInTheDocument();
    expect(posts).toHaveLength(1);

    resolvePost?.();
    expect(await screen.findByText("Scenarios of Atlas migration")).toBeVisible();
  });

  it("keeps a created item outside a full server page, then reconciles it once on page two", async () => {
    const serverRows = Array.from({ length: 20 }, (_, index) =>
      projectRow(`project-${index}`, `Project ${String(index).padStart(2, "0")}`),
    );
    let created: ProjectListItem | null = null;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "POST") {
        const body = JSON.parse(String(init.body)) as Record<string, unknown>;
        created = projectRow("project-created", String(body.name));
        return {
          ok: true, status: 201,
          json: async () => ({ ...body, ...created, owner: body.owner, updated_at: "2026-10-04T12:00:00Z", target_margin_percent: null, overload_threshold_percent: null }),
        };
      }
      const url = new URL(String(input));
      const rows = [...serverRows, ...(created === null ? [] : [created])].sort((a, b) => a.name.localeCompare(b.name));
      const offset = Number(url.searchParams.get("offset") ?? 0);
      const limit = Number(url.searchParams.get("limit") ?? 20);
      return { ok: true, status: 200, json: async () => ({ projects: rows.slice(offset, offset + limit), total: rows.length }) };
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<ProjectListScreen />);
    await screen.findByRole("button", { name: "Project 00" });
    expect(screen.getAllByRole("row")).toHaveLength(21);
    openForm();
    fill({ "Project name": "ZZZ new project" });
    fireEvent.click(screen.getByRole("button", { name: "Create project" }));

    expect(await screen.findByText("Scenarios of ZZZ new project")).toBeVisible();
    await waitFor(() => expect(screen.getByRole("button", { name: "Next page" })).toBeEnabled());
    expect(screen.getAllByRole("row")).toHaveLength(21);
    expect(screen.queryByRole("button", { name: "ZZZ new project" })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Next page" }));
    expect(await screen.findByRole("button", { name: "ZZZ new project" })).toBeVisible();
    expect(screen.getAllByRole("row")).toHaveLength(2);
    expect(screen.getAllByRole("button", { name: "ZZZ new project" })).toHaveLength(1);
    expect(screen.getByRole("button", { name: "Next page" })).toBeDisabled();
  });

  it("does not duplicate or recount an idempotently replayed project already on the current page", async () => {
    const existing = projectRow("existing-project", "Atlas migration");
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "POST") {
        const body = JSON.parse(String(init.body)) as Record<string, unknown>;
        return {
          ok: true, status: 201,
          json: async () => ({ ...body, ...existing, owner: body.owner, updated_at: "2026-10-04T12:00:00Z", target_margin_percent: null, overload_threshold_percent: null }),
        };
      }
      return { ok: true, status: 200, json: async () => ({ projects: [existing], total: 1 }) };
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<ProjectListScreen />);
    await screen.findByRole("button", { name: "Atlas migration" });
    openForm();
    fill();
    fireEvent.click(screen.getByRole("button", { name: "Create project" }));

    expect(await screen.findByText("Scenarios of Atlas migration")).toBeVisible();
    expect(screen.getAllByRole("row")).toHaveLength(2);
    expect(screen.getAllByRole("button", { name: "Atlas migration" })).toHaveLength(1);
    expect(screen.getByRole("button", { name: "Next page" })).toBeDisabled();
  });
});
