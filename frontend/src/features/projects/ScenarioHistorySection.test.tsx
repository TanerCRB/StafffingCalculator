import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CALLER_ID_HEADER } from "../../api/client";
import type { ScenarioHistory } from "../../api/contracts/scenarioHistory";
import { ScenarioHistorySection } from "./ScenarioHistorySection";

const PROJECT_ID = "11111111-1111-1111-1111-111111111111";
const APPROVED_ID = "aaaaaaaa-0000-0000-0000-000000000003";
const DRAFT_ID = "aaaaaaaa-0000-0000-0000-000000000004";
const SYNTHETIC_ACTOR_ID = "test-actor-001";

function page<T>(items: T[], total = items.length) {
  return { items, total, limit: 100, offset: 0 };
}

function approvedHistory(overrides: Partial<ScenarioHistory> = {}): ScenarioHistory {
  return {
    scenario_id: APPROVED_ID,
    name: "Signed plan",
    status: "Approved",
    updated_at: "2026-08-12T10:12:00Z",
    inputs: {
      start_date: "2026-07-01",
      end_date: "2026-12-31",
      working_calendar: "calendar-live-001",
      full_time_hours_per_week: "40.00",
      currency: "EUR",
      target_margin_percent: "15.005",
      overload_threshold_percent: "110.000",
    },
    approval_event: {
      action_type: "scenario_approved",
      performed_by: SYNTHETIC_ACTOR_ID,
      performed_by_verified: false,
      created_at: "2026-08-12T10:12:00Z",
    },
    approved_snapshot: {
      organization_defaults: {
        target_margin_percent: "12.500",
        overload_threshold_percent: "105.000",
      },
      working_calendars: page([]),
      working_calendar_days: page([]),
      absence_types: page([]),
      absence_budgets: page([]),
      catalog_rates: page([{
        source_rate_id: "b1000000-0000-0000-0000-000000000001",
        source_role_id: "b2000000-0000-0000-0000-000000000001",
        source_seniority_id: "b3000000-0000-0000-0000-000000000001",
        source_location_id: "b4000000-0000-0000-0000-000000000001",
        source_engagement_type_id: "b5000000-0000-0000-0000-000000000001",
        source_vendor_id: null,
        default_selling_rate: "10.505",
        currency: "EUR",
        unit: "hour",
        effective_from: "2026-01-01",
        effective_to: null,
        surcharge_percent: "5.000",
        includes_surcharge: false,
      }]),
      exchange_rates: page([]),
    },
    ...overrides,
  };
}

function responseFor(body: unknown, status = 200) {
  return { ok: status >= 200 && status < 300, status, json: async () => body };
}

function renderSection(scenarioId = APPROVED_ID, scenarioName = "Signed plan") {
  return render(
    <ScenarioHistorySection projectId={PROJECT_ID} scenarioId={scenarioId} scenarioName={scenarioName} />,
  );
}

describe("ScenarioHistorySection", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("shows the approval time and synthetic actor only as an unverified placeholder", async () => {
    const fetchMock = vi.fn().mockResolvedValue(responseFor(approvedHistory()));
    vi.stubGlobal("fetch", fetchMock);

    renderSection();
    expect(fetchMock).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "View approval history for Signed plan" }));

    expect(await screen.findByRole("region", { name: "Approval event" })).toBeVisible();
    expect(screen.getByRole("region", { name: "Approval event" }).querySelector("time"))
      .toHaveAttribute("datetime", "2026-08-12T10:12:00Z");
    expect(screen.getByText(SYNTHETIC_ACTOR_ID)).toBeVisible();
    expect(screen.getByText(/Unverified development\/test placeholder/)).toBeVisible();
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining(`/projects/${PROJECT_ID}/scenarios/${APPROVED_ID}/history`),
      expect.objectContaining({ headers: { [CALLER_ID_HEADER]: expect.any(String) } }),
    );
  });

  it("renders this scenario's current inputs separately from its saved approval snapshot", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(responseFor(approvedHistory())));
    renderSection();
    fireEvent.click(screen.getByRole("button", { name: "View approval history for Signed plan" }));

    const current = await screen.findByRole("region", { name: "This scenario’s saved inputs" });
    const snapshot = screen.getByRole("region", { name: "Saved approval snapshot inputs" });
    expect(current).toHaveTextContent("15.01%");
    expect(snapshot).toHaveTextContent("12.50%");
    expect(current).not.toHaveTextContent("12.50%");
    expect(snapshot).not.toHaveTextContent("15.01%");
    expect(snapshot).toHaveTextContent("10.51 EUR");
    expect(snapshot).not.toHaveTextContent("Cost rate");
    expect(screen.queryByText(/compare|compared|difference/i)).toBeNull();
  });

  it("renders a draft's own inputs without inventing approval or snapshot data", async () => {
    const draft: ScenarioHistory = {
      ...approvedHistory(),
      scenario_id: DRAFT_ID,
      name: "Working draft",
      status: "Draft",
      inputs: {
        start_date: "2027-02-01",
        end_date: null,
        working_calendar: "draft-calendar-002",
        full_time_hours_per_week: "36.00",
        currency: "PLN",
        target_margin_percent: null,
        overload_threshold_percent: null,
      },
      approval_event: null,
      approved_snapshot: null,
    };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(responseFor(draft)));
    renderSection(DRAFT_ID, "Working draft");
    fireEvent.click(screen.getByRole("button", { name: "View approval history for Working draft" }));

    expect(await screen.findByText("No approval event is recorded for this scenario.")).toBeVisible();
    expect(screen.getByRole("region", { name: "This scenario’s saved inputs" })).toHaveTextContent("draft-calendar-002");
    expect(screen.queryByRole("region", { name: "Saved approval snapshot inputs" })).toBeNull();
    expect(screen.queryByText(SYNTHETIC_ACTOR_ID)).toBeNull();
  });

  it("renders an approved snapshot when no approval audit event is available", async () => {
    const legacyApproved = { ...approvedHistory(), approval_event: null };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(responseFor(legacyApproved)));
    renderSection();
    fireEvent.click(screen.getByRole("button", { name: "View approval history for Signed plan" }));

    expect(await screen.findByText("No approval event is recorded for this scenario.")).toBeVisible();
    expect(screen.getByRole("region", { name: "Saved approval snapshot inputs" })).toHaveTextContent("10.51 EUR");
  });

  it("shows a server denial without rendering history values or offering retry actions", async () => {
    const fetchMock = vi.fn().mockResolvedValue(responseFor({ detail: "Forbidden" }, 403));
    vi.stubGlobal("fetch", fetchMock);
    renderSection();
    fireEvent.click(screen.getByRole("button", { name: "View approval history for Signed plan" }));

    expect(await screen.findByText("Approval history is unavailable for this scenario.")).toBeVisible();
    expect(screen.queryByText(SYNTHETIC_ACTOR_ID)).toBeNull();
    expect(screen.queryByRole("button")).toBeNull();
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
  });

  it("loads the next bounded snapshot page and appends its rows", async () => {
    const firstPage = approvedHistory();
    firstPage.approved_snapshot!.catalog_rates.total = 101;
    const nextPage = approvedHistory();
    nextPage.approved_snapshot!.catalog_rates = {
      items: [{
        ...firstPage.approved_snapshot!.catalog_rates.items[0],
        source_rate_id: "b1000000-0000-0000-0000-000000000002",
        default_selling_rate: "20.505",
      }],
      total: 101,
      limit: 100,
      offset: 100,
    };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(responseFor(firstPage))
      .mockResolvedValueOnce(responseFor(nextPage));
    vi.stubGlobal("fetch", fetchMock);

    renderSection();
    fireEvent.click(screen.getByRole("button", { name: "View approval history for Signed plan" }));
    fireEvent.click(await screen.findByRole("button", { name: "Load more snapshot inputs" }));

    expect(await screen.findByText("20.51 EUR")).toBeVisible();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls[1][0]).toContain("?catalog_rates_limit=100&catalog_rates_offset=100");
    expect(screen.getAllByText("Catalog rates")).toHaveLength(1);
  });
});
