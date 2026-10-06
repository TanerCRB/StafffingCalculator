import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ScenarioStatus } from "../../api/contracts/projects";
import { ScenarioApprovalSection } from "./ScenarioApprovalSection";

const PROJECT_ID = "11111111-1111-1111-1111-111111111111";
const SCENARIO_ID = "aaaaaaaa-0000-0000-0000-000000000004";

function reply(body: unknown, status = 200) {
  return { ok: status >= 200 && status < 300, status, json: async () => body };
}

function approvalResult() {
  return { id: SCENARIO_ID, status: "Approved", snapshot: {
    working_calendars: 1, working_calendar_days: 5, absence_types: 0,
    absence_budgets: 0, organization_defaults: 1,
  } };
}

function renderApproval({ ready = true, status = "Draft" as ScenarioStatus, onApproved = vi.fn() } = {}) {
  return { onApproved, ...render(<ScenarioApprovalSection projectId={PROJECT_ID} scenarioId={SCENARIO_ID}
    scenarioName="Plan A" status={status} ready={ready} onApproved={onApproved} />) };
}

describe("ScenarioApprovalSection", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("approves a ready draft from the server result and shows frozen approved state", async () => {
    const fetchMock = vi.fn().mockResolvedValue(reply(approvalResult()));
    vi.stubGlobal("fetch", fetchMock);
    const { onApproved } = renderApproval();

    fireEvent.click(screen.getByRole("button", { name: "Approve scenario: Plan A" }));

    expect(await screen.findByText(/Approved\. Its saved values are frozen/)).toBeVisible();
    expect(screen.getByRole("status")).toHaveTextContent("duplicate this scenario");
    expect(onApproved).toHaveBeenCalledWith(SCENARIO_ID, approvalResult());
    expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining(`/projects/${PROJECT_ID}/scenarios/${SCENARIO_ID}/approve`),
      expect.objectContaining({ method: "POST" }));
  });

  it("does not offer approval for an incomplete draft, while readiness enables it", () => {
    const { rerender } = renderApproval({ ready: false });
    expect(screen.queryByRole("button", { name: /Approve scenario/ })).toBeNull();

    rerender(<ScenarioApprovalSection projectId={PROJECT_ID} scenarioId={SCENARIO_ID} scenarioName="Plan A"
      status="Draft" ready onApproved={vi.fn()} />);
    expect(screen.getByRole("button", { name: "Approve scenario: Plan A" })).toBeEnabled();
  });

  it("shows an approval refusal without reporting Approved", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(reply({ detail: "Forbidden" }, 403)));
    renderApproval();
    fireEvent.click(screen.getByRole("button", { name: /Approve scenario/ }));
    expect(await screen.findByText(/Approval was refused/)).toBeVisible();
    expect(screen.queryByText(/^Approved\./)).toBeNull();
  });

  it("shows a concurrent-change response without reporting Approved", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(reply({ detail: "Concurrent change" }, 409)));
    renderApproval();
    fireEvent.click(screen.getByRole("button", { name: /Approve scenario/ }));
    expect(await screen.findByText(/changed before approval/)).toBeVisible();
    expect(screen.queryByText(/^Approved\./)).toBeNull();
  });

  it("reports an unresolved outcome when the connection fails without claiming the draft stayed unapproved", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Network connection lost")));
    const { onApproved } = renderApproval();
    fireEvent.click(screen.getByRole("button", { name: /Approve scenario/ }));

    expect(await screen.findByText(/Approval outcome is unresolved/)).toBeVisible();
    expect(screen.getByRole("status")).toHaveTextContent("Refresh the project to check the scenario’s current status");
    expect(screen.queryByText(/remains unapproved/)).toBeNull();
    expect(screen.queryByText(/^Approved\./)).toBeNull();
    expect(onApproved).not.toHaveBeenCalled();
  });

  it("keeps a server-reported approved scenario frozen and directs edits to a copy", () => {
    renderApproval({ status: "Approved" });
    expect(screen.queryByRole("button", { name: /Approve scenario/ })).toBeNull();
    expect(screen.getByText(/values are frozen; duplicate it to make further changes/)).toBeVisible();
  });
});
