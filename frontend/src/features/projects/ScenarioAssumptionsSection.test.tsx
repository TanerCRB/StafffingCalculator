import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../../api/client";
import type { ScenarioAssumptions } from "../../api/contracts/scenarioAssumptions";
import { OrganizationDefaultsSection, ScenarioAssumptionsSection } from "./ScenarioAssumptionsSection";

const api = vi.hoisted(() => ({
  getScenarioAssumptions: vi.fn(),
  getScenarioAssumptionResetPreview: vi.fn(),
  editScenarioAssumptions: vi.fn(),
  getOrganizationDefaults: vi.fn(),
  editOrganizationDefaults: vi.fn(),
}));

vi.mock("../../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api/client")>();
  return { ...actual, ...api };
});

function assumptions(overrides: Partial<ScenarioAssumptions> = {}): ScenarioAssumptions {
  return {
    id: "scenario-1",
    status: "Draft",
    updated_at: "2026-10-06T10:00:00Z",
    target_margin_percent: { value: "10.00", state: "resolved", source: "scenario" },
    overload_threshold_percent: { value: "20.00", state: "resolved", source: "organization" },
    ...overrides,
  };
}

function mount(status: "Draft" | "Approved" = "Draft") {
  return render(<ScenarioAssumptionsSection projectId="project-1" scenarioId="scenario-1" scenarioStatus={status} />);
}

describe("SC-1-21 ScenarioAssumptionsSection", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.getScenarioAssumptions.mockReset();
    api.getScenarioAssumptions.mockResolvedValue(assumptions());
    api.getScenarioAssumptionResetPreview.mockResolvedValue({
      id: "scenario-1", status: "Draft",
      target_margin_percent: { value: "12.5", state: "resolved", source: "project" },
      overload_threshold_percent: { value: "20", state: "resolved", source: "organization" },
    });
    api.editScenarioAssumptions.mockResolvedValue({
      id: "scenario-1", status: "Draft", updated_at: "2026-10-06T10:01:00Z",
      target_margin_percent: "0", overload_threshold_percent: null,
    });
    api.getOrganizationDefaults.mockResolvedValue({ updated_at: "2026-10-06T10:00:00Z", target_margin_percent: "10", overload_threshold_percent: "20" });
    api.editOrganizationDefaults.mockResolvedValue({ updated_at: "2026-10-06T10:01:00Z", target_margin_percent: "15", overload_threshold_percent: "20" });
  });

  it("K-01 displays both resolved values and their actual source labels", async () => {
    mount();
    expect(await screen.findByText("Target margin: 10.00%" )).toBeVisible();
    expect(screen.getByText("Scenario override")).toBeVisible();
    expect(screen.getByText("Overload threshold: 20.00%")).toBeVisible();
    expect(screen.getByText("Organization default")).toBeVisible();
  });

  it("K-02 saves only the selected draft override and preserves zero as an explicit value", async () => {
    api.getScenarioAssumptions
      .mockResolvedValueOnce(assumptions())
      .mockResolvedValueOnce(assumptions({ target_margin_percent: { value: "0", state: "resolved", source: "scenario" } }));
    mount();
    const target = await screen.findByLabelText("Target margin override");
    fireEvent.change(target, { target: { value: "0" } });
    fireEvent.click(screen.getAllByRole("button", { name: "Save override" })[0]!);
    await waitFor(() => expect(api.editScenarioAssumptions).toHaveBeenCalledWith("project-1", "scenario-1", {
      updated_at: "2026-10-06T10:00:00Z", target_margin_percent: "0",
    }));
    expect(await screen.findByText("Target margin: 0.00%")).toBeVisible();
    expect(screen.getByLabelText("Overload threshold override")).toHaveValue("");
  });

  it("K-02 preserves the sibling override and renders the server's canonical saved value", async () => {
    api.getScenarioAssumptions.mockResolvedValue(assumptions({
      overload_threshold_percent: { value: "30", state: "resolved", source: "scenario" },
    }));
    api.editScenarioAssumptions.mockResolvedValue({
      id: "scenario-1", status: "Draft", updated_at: "2026-10-06T10:01:00Z",
      target_margin_percent: "14.500", overload_threshold_percent: "30",
    });
    mount();
    const target = await screen.findByLabelText("Target margin override");
    await waitFor(() => expect(screen.getByLabelText("Overload threshold override")).toHaveValue("30"));
    fireEvent.change(target, { target: { value: "14" } });
    fireEvent.click(screen.getAllByRole("button", { name: "Save override" })[0]!);
    await waitFor(() => expect(api.editScenarioAssumptions).toHaveBeenCalledWith("project-1", "scenario-1", {
      updated_at: "2026-10-06T10:00:00Z", target_margin_percent: "14",
    }));
    expect(await screen.findByText("Target margin: 14.50%")).toBeVisible();
    expect(screen.getByLabelText("Overload threshold override")).toHaveValue("30");
  });

  it("K-03 refuses to reset when preview fails and confirms reset only after showing both inherited values and sources", async () => {
    api.getScenarioAssumptionResetPreview.mockRejectedValueOnce(new ApiError(403, "denied"));
    mount();
    fireEvent.click(await screen.findByRole("button", { name: "Reset to inherited value" }));
    expect(await screen.findByText(/Nothing was reset/)).toBeVisible();
    expect(screen.queryByRole("button", { name: "Confirm reset" })).not.toBeInTheDocument();
    expect(api.editScenarioAssumptions).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Try preview again" }));
    expect(await screen.findByRole("group", { name: "Reset preview" })).toBeVisible();
    const preview = screen.getByRole("group", { name: "Reset preview" });
    expect(preview).toHaveTextContent(/Target margin:.*12\.50%/);
    expect(preview).toHaveTextContent(/Project setting/);
    expect(preview).toHaveTextContent(/Overload threshold:.*20\.00%/);
    expect(preview).toHaveTextContent(/Organization default/);
    expect(screen.getByText(/resolve the current inherited values again/)).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Confirm reset" }));
    await waitFor(() => expect(api.editScenarioAssumptions).toHaveBeenCalledWith("project-1", "scenario-1", {
      updated_at: "2026-10-06T10:00:00Z", target_margin_percent: null,
    }));
  });

  it("K-04 leaves organization-default authorization to the organization-scoped API and preserves denied input", async () => {
    api.editOrganizationDefaults.mockRejectedValueOnce(new ApiError(403, "organization grant missing"));
    const denied = render(<OrganizationDefaultsSection />);
    const input = await screen.findByLabelText("Organization Target margin");
    fireEvent.change(input, { target: { value: "14" } });
    fireEvent.click(screen.getAllByRole("button", { name: "Save override" })[0]!);
    expect(await screen.findByText(/not authorized to save/)).toBeVisible();
    expect(input).toHaveValue("14");
    expect(api.editOrganizationDefaults).toHaveBeenCalledWith({ updated_at: "2026-10-06T10:00:00Z", target_margin_percent: "14" });
    expect(screen.getAllByRole("button", { name: "Save override" })[0]).toBeDisabled();

    denied.unmount();
    render(<OrganizationDefaultsSection />);
    const authorizedInput = await screen.findByLabelText("Organization Target margin");
    fireEvent.change(authorizedInput, { target: { value: "15" } });
    fireEvent.click(screen.getAllByRole("button", { name: "Save override" })[0]!);
    await waitFor(() => expect(api.editOrganizationDefaults).toHaveBeenCalledTimes(2));
    expect(await screen.findByRole("status")).toHaveTextContent("Saved.");
  });

  it("K-04 hides default values and write controls when the organization read is denied", async () => {
    api.getOrganizationDefaults.mockRejectedValueOnce(new ApiError(403, "organization read grant missing"));
    render(<OrganizationDefaultsSection />);
    expect(await screen.findByText("You do not have access to organization defaults.")).toBeVisible();
    expect(screen.queryByLabelText("Organization Target margin")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save override" })).not.toBeInTheDocument();
  });

  it("reloads the latest marker after a conflict and preserves the user's draft for a deliberate retry", async () => {
    api.getOrganizationDefaults
      .mockResolvedValueOnce({ updated_at: "2026-10-06T10:00:00Z", target_margin_percent: "10", overload_threshold_percent: "20" })
      .mockResolvedValueOnce({ updated_at: "2026-10-06T10:02:00Z", target_margin_percent: "12", overload_threshold_percent: "25" });
    api.editOrganizationDefaults.mockRejectedValueOnce(new ApiError(409, "stale marker"));
    render(<OrganizationDefaultsSection />);
    const input = await screen.findByLabelText("Organization Target margin");
    fireEvent.change(input, { target: { value: "15" } });
    fireEvent.click(screen.getAllByRole("button", { name: "Save override" })[0]!);
    expect(await screen.findByRole("alert")).toHaveTextContent("changed elsewhere");

    fireEvent.click(screen.getByRole("button", { name: "Reload latest organization defaults" }));
    const latest = await screen.findByRole("group", { name: "Latest organization defaults" });
    expect(latest).toHaveTextContent("Target margin: 12.00%");
    expect(latest).toHaveTextContent("Overload threshold: 25.00%");
    expect(latest).toHaveTextContent(/unsaved draft remains/);
    expect(input).toHaveValue("15");

    fireEvent.click(screen.getAllByRole("button", { name: "Save override" })[0]!);
    await waitFor(() => expect(api.editOrganizationDefaults).toHaveBeenLastCalledWith({ updated_at: "2026-10-06T10:02:00Z", target_margin_percent: "15" }));
  });

  it("offers recovery after an unknown timeout without discarding either typed field", async () => {
    api.getOrganizationDefaults
      .mockResolvedValueOnce({ updated_at: "2026-10-06T10:00:00Z", target_margin_percent: "10", overload_threshold_percent: "20" })
      .mockResolvedValueOnce({ updated_at: "2026-10-06T10:03:00Z", target_margin_percent: "16", overload_threshold_percent: "24" });
    api.editOrganizationDefaults.mockRejectedValueOnce(new Error("request timed out"));
    render(<OrganizationDefaultsSection />);
    const target = await screen.findByLabelText("Organization Target margin");
    const overload = screen.getByLabelText("Organization Overload threshold");
    fireEvent.change(target, { target: { value: "17" } });
    fireEvent.change(overload, { target: { value: "28" } });
    fireEvent.click(screen.getAllByRole("button", { name: "Save override" })[0]!);
    expect(await screen.findByRole("alert")).toHaveTextContent("outcome is unknown");

    fireEvent.click(screen.getByRole("button", { name: "Reload latest organization defaults" }));
    expect(await screen.findByRole("group", { name: "Latest organization defaults" })).toHaveTextContent("Target margin: 16.00%");
    expect(target).toHaveValue("17");
    expect(overload).toHaveValue("28");
  });

  it("K-05 rereads live organization defaults for drafts while approved scenarios use their frozen response", async () => {
    api.getScenarioAssumptions.mockResolvedValueOnce(assumptions({
      target_margin_percent: { value: "10", state: "resolved", source: "organization" },
      overload_threshold_percent: { value: "20", state: "resolved", source: "organization" },
    })).mockResolvedValueOnce(assumptions({
      target_margin_percent: { value: "15", state: "resolved", source: "organization" },
      overload_threshold_percent: { value: "20", state: "resolved", source: "organization" },
    })).mockResolvedValueOnce(assumptions({
      target_margin_percent: { value: "12", state: "resolved", source: "scenario" },
      overload_threshold_percent: { value: "20", state: "resolved", source: "organization" },
    })).mockResolvedValueOnce(assumptions({
      status: "Approved",
      target_margin_percent: { value: "10", state: "resolved", source: "scenario" },
      overload_threshold_percent: { value: "20", state: "resolved", source: "scenario" },
    }));
    const draft = mount("Draft");
    expect(await screen.findByText("Target margin: 10.00%")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Reload assumptions" }));
    expect(await screen.findByText("Target margin: 15.00%")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Reload assumptions" }));
    expect(await screen.findByText("Target margin: 12.00%")).toBeVisible();
    expect(screen.getByText("Scenario override")).toBeVisible();
    draft.unmount();
    mount("Approved");
    expect(await screen.findByText("Target margin: 10.00%")).toBeVisible();
    expect(screen.getByText("Approved scenarios use their frozen assumption snapshot.")).toBeVisible();
    expect(screen.queryByLabelText("Target margin override")).not.toBeInTheDocument();
  });

  it("reloads assumptions when an open draft becomes approved", async () => {
    api.getScenarioAssumptions
      .mockResolvedValueOnce(assumptions({
        target_margin_percent: { value: "15", state: "resolved", source: "organization" },
        overload_threshold_percent: { value: "20", state: "resolved", source: "organization" },
      }))
      .mockResolvedValueOnce(assumptions({
        status: "Approved",
        target_margin_percent: { value: "10", state: "resolved", source: "scenario" },
        overload_threshold_percent: { value: "20", state: "resolved", source: "scenario" },
      }));
    const view = mount("Draft");
    expect(await screen.findByText("Target margin: 15.00%")).toBeVisible();

    view.rerender(<ScenarioAssumptionsSection projectId="project-1" scenarioId="scenario-1" scenarioStatus="Approved" />);

    expect(await screen.findByText("Target margin: 10.00%")).toBeVisible();
    expect(screen.getAllByText("Scenario override")).toHaveLength(2);
    expect(screen.getByText("Approved scenarios use their frozen assumption snapshot.")).toBeVisible();
    expect(api.getScenarioAssumptions).toHaveBeenCalledTimes(2);
    expect(screen.queryByLabelText("Target margin override")).not.toBeInTheDocument();
  });

  it.each([
    [422, "The value was rejected"],
    [403, "not authorized"],
    [409, "changed elsewhere"],
  ])("K-06 distinguishes HTTP %s and keeps the unsaved input", async (status, feedback) => {
    api.editScenarioAssumptions.mockRejectedValueOnce(new ApiError(status, "refused"));
    mount();
    const input = await screen.findByLabelText("Target margin override");
    fireEvent.change(input, { target: { value: "33" } });
    fireEvent.click(screen.getAllByRole("button", { name: "Save override" })[0]!);
    expect(await screen.findByRole("alert")).toHaveTextContent(feedback);
    expect(input).toHaveValue("33");
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    if (status === 403) {
      api.editScenarioAssumptions.mockResolvedValueOnce({
        id: "scenario-1", status: "Draft", updated_at: "2026-10-06T10:02:00Z",
        target_margin_percent: "33", overload_threshold_percent: null,
      });
      fireEvent.click(screen.getAllByRole("button", { name: "Save override" })[0]!);
      expect(await screen.findByRole("status")).toHaveTextContent("Saved.");
      expect(screen.getByText("Target margin: 33.00%")).toBeVisible();
    }
  });
});
