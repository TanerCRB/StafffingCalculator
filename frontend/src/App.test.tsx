import { render, screen, waitFor } from "@testing-library/react";
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
});
