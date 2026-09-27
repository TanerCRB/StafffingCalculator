import { afterEach, describe, expect, it, vi } from "vitest";

import { getCatalogAbsenceBudgets, getProjects } from "./client";

/**
 * SC-1-09, K-05, second proof: `getProjects` does not merely *accept* an `AbortSignal`, it hands it
 * to the request.
 *
 * The screen's own test asserts that the signal `fetch` received is aborted after the screen is
 * left. That assertion holds for a `getProjects(signal)` whose parameter is accepted and dropped as
 * well — the deadline inside `requestWithDeadline` builds a controller of its own, so `fetch`
 * always gets *some* signal, and the screen's unmount aborts nothing at all while every rendered
 * outcome stays identical. This file is the layer where the two can be told apart, and the reason
 * it exists is a defect this repository has already shipped once: SC-2-04's post-save re-read built
 * an `AbortController` because the function it called wanted one, never aborted it, and was wired
 * into no cleanup — invisible to a green suite until Reviewer R-02 read the code (R-02, 2026-09-22).
 */

/** A request that never answers, so the only thing that can end it is an abort. */
function stubPendingFetch() {
  const fetchMock = vi.fn((_url: string, init: RequestInit) => {
    return new Promise<never>((_resolve, reject) => {
      const abort = () => reject(new DOMException("The operation was aborted.", "AbortError"));
      // Both branches, like the platform: `fetch` rejects straight away for a signal that was
      // already aborted when it was called, and an `abort` listener on such a signal never fires.
      if (init.signal?.aborted === true) {
        abort();
        return;
      }
      init.signal?.addEventListener("abort", abort);
    });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function signalGivenToFetch(fetchMock: {
  readonly mock: { readonly calls: readonly unknown[][] };
}): AbortSignal {
  const [, init] = (fetchMock.mock.calls[0] ?? []) as [string, RequestInit];
  const signal = init?.signal;
  if (!(signal instanceof AbortSignal)) {
    throw new Error("fetch was called without an AbortSignal");
  }
  return signal;
}

describe("getProjects and the caller's abort signal", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("ends the request when the caller's signal is aborted, rather than accepting the signal and dropping it", async () => {
    const fetchMock = stubPendingFetch();
    const caller = new AbortController();

    const read = getProjects(caller.signal);
    const signal = signalGivenToFetch(fetchMock);
    expect(signal.aborted).toBe(false);

    caller.abort();

    expect(signal.aborted).toBe(true);
    await expect(read).rejects.toThrow();
  });

  it("does not abort a request whose caller never asked it to", async () => {
    // The contrast. Without it, `requestWithDeadline(..., AbortSignal.abort())` — or any wiring
    // that aborts unconditionally — would satisfy the assertion above and break every read.
    const fetchMock = stubPendingFetch();
    const caller = new AbortController();
    const somebodyElse = new AbortController();

    void getProjects(caller.signal);
    const signal = signalGivenToFetch(fetchMock);

    somebodyElse.abort();

    expect(signal.aborted).toBe(false);
  });

  it("still issues a request when no signal is given, so the parameter is optional in fact and not only in type", async () => {
    // Every other reader of this function (and every write) calls it without a signal. A forwarding
    // that only works because a signal is always present would break all of them.
    const fetchMock = stubPendingFetch();

    void getProjects();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(signalGivenToFetch(fetchMock).aborted).toBe(false);
  });

  it("gives up immediately on a signal that was already aborted before the call", async () => {
    // The branch `requestWithDeadline` writes out separately, because `addEventListener("abort")`
    // on an already-aborted signal never fires: without it, a read started after the screen was
    // left would run to completion with nobody waiting for it.
    const fetchMock = stubPendingFetch();

    await expect(getProjects(AbortSignal.abort())).rejects.toThrow();
    expect(signalGivenToFetch(fetchMock).aborted).toBe(true);
  });
});

/**
 * SC-3-06 (Issue #140), gap flagged by the developer at handover: `isAbsenceBudgetShape`'s pairing
 * rule between `statutory_leave_state` and `statutory_leave`/`generates_cost`/`generates_revenue`
 * (criterion K-03) had no test at the `client.ts` boundary itself — only through
 * `WorkingCalendarsScreen.test.tsx`, whose fixtures are already correctly paired, which a mutation
 * removing the pairing check would not disturb. This is that missing boundary test: a payload
 * broken exactly on the pairing, never on an individual field's own type.
 */
describe("getCatalogAbsenceBudgets and the statutory-leave pairing", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  const VALID_RESOLVED_BUDGET = {
    id: "b0000000-0000-0000-0000-000000000001",
    calendar_id: "c0000000-0000-0000-0000-000000000001",
    engagement_type_id: "d0000000-0000-0000-0000-000000000001",
    budget_days: "20.00",
    unit: "day",
    source: "Staff regulations §12, 2026 edition",
    effective_from: "2026-01-01",
    effective_to: "2026-12-31",
    statutory_leave_state: "resolved",
    statutory_leave: {
      absence_type_id: "a0000000-0000-0000-0000-000000000001",
      name: "Annual leave",
      generates_cost: true,
      generates_revenue: false,
    },
    generates_cost: true,
    generates_revenue: false,
    updated_at: "2026-01-01T00:00:00+00:00",
  };

  function stubBudgets(budgets: unknown[]) {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({ ok: true, status: 200, json: async () => ({ budgets }) })),
    );
  }

  it("accepts a budget whose state and regime fields are correctly paired (control)", async () => {
    stubBudgets([VALID_RESOLVED_BUDGET]);
    const result = await getCatalogAbsenceBudgets();
    expect(result.budgets).toHaveLength(1);
  });

  it("rejects a 'resolved' state carrying no statutory-leave regime (statutory_leave: null)", async () => {
    stubBudgets([{ ...VALID_RESOLVED_BUDGET, statutory_leave: null }]);
    await expect(getCatalogAbsenceBudgets()).rejects.toThrow();
  });

  it("rejects a 'no_statutory_leave_type' state carrying a real statutory-leave regime", async () => {
    stubBudgets([
      {
        ...VALID_RESOLVED_BUDGET,
        statutory_leave_state: "no_statutory_leave_type",
        // generates_cost/generates_revenue left as real booleans, and statutory_leave left as a
        // real object — exactly the "server forgot to blank the regime out" mistake the pairing
        // rule exists to catch. A shape check that only typed each field separately would accept
        // this: every individual field is still one of its allowed types.
      },
    ]);
    await expect(getCatalogAbsenceBudgets()).rejects.toThrow();
  });

  it("rejects a 'resolved' state carrying the 'n/a' sentinel instead of real booleans", async () => {
    stubBudgets([
      { ...VALID_RESOLVED_BUDGET, generates_cost: "n/a", generates_revenue: "n/a" },
    ]);
    await expect(getCatalogAbsenceBudgets()).rejects.toThrow();
  });
});
