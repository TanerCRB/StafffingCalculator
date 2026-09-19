import { render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";

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
});
