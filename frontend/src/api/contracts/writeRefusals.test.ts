import { describe, expect, it } from "vitest";

// The backend's own source, as text. `?raw` rather than `node:fs`, for two reasons: the project
// carries no `@types/node` (a `readFileSync` here would fail `tsc -b`, not only `pnpm test`), and an
// import is resolved at transform time — so the day this file moves, the suite fails to build
// instead of quietly asserting about an empty string. The same mechanism `src/styles/tokens.test.ts`
// uses to hold the stylesheet against the markup.
import WRITE_ERRORS_PY from "../../../../backend/app/data/write_errors.py?raw";

import {
  CONCURRENCY_MARKER_CONDITION,
  REFUSAL_SQLSTATE,
  refusalCauseOf,
} from "./writeRefusals";

/**
 * SC-2-04 (QA) — the one place where the catalogue's write contract is written down twice.
 *
 * `refusalCauseOf` reads the cause of a `409` off identifiers the backend puts in the message:
 * `condition=updated_at_marker` for a stale ADR-0007 marker, `sqlstate=23P01` for an overlap, and so
 * on. Both spellings are declared independently on the two sides — `backend/app/data/write_errors.py`
 * and `writeRefusals.ts` — and **nothing was comparing them**. Every test on either side is written
 * against its own side's constant: the backend asserts
 * `CONCURRENCY_MARKER_CONDITION in response.json()["detail"]` with the constant imported from the
 * module under test, and `CatalogWrite.test.tsx` builds its fixture refusals out of the TypeScript
 * constant. So a rename on the backend (`updated_at_marker` → anything) leaves both suites green and
 * ships a screen that answers every stale-marker conflict with "refused, and the answer did not say
 * which rule" — the `"unstated"` branch — instead of "reload the row and try again". Measured: with
 * the backend constant renamed, the delivered backend suite (328) and the delivered frontend suite
 * (105) both stayed green (QA mutation run, 2026-09-21).
 *
 * That is the whole reason this file reads the backend source rather than a fixture. A fixture would
 * be a third copy of the same guess. The two decisions it pins are the ones `refusalCauseOf` acts
 * on, and both are closed lists on the backend side too, so drift in either direction is caught:
 * a renamed identifier, and a SQLSTATE added to (or dropped from) the backend's refusal table
 * without the screen learning what it means.
 */

describe("the refusal identifiers this module reads are the ones the backend writes", () => {
  it("reads the backend module as text, so an assertion below is never about an empty string", () => {
    // Vitest stubs some imports with an empty module by default (the reason `css: true` is set in
    // `vite.config.ts`). If `?raw` on a `.py` file ever behaved the same way, every regex below
    // would simply find nothing, and three tests would need to decide what that means. They decide
    // here, once.
    expect(WRITE_ERRORS_PY).toContain("REFUSAL_BY_SQLSTATE");
    expect(WRITE_ERRORS_PY).toContain("CONCURRENCY_MARKER_CONDITION");
  });

  it("spells the concurrency-marker condition exactly as the backend does", () => {
    const declared = /^CONCURRENCY_MARKER_CONDITION = "([^"]+)"$/m.exec(WRITE_ERRORS_PY);
    expect(declared, "the backend no longer declares this constant in the shape read here").not
      .toBeNull();
    expect(CONCURRENCY_MARKER_CONDITION).toBe(declared?.[1]);
  });

  it("knows the same four refusal SQLSTATEs the backend classifies, and no others", () => {
    // The backend's list is closed by decision ("deliberately a closed list, and deliberately not
    // extended by guesswork"). A fifth code added there and not here would reach the screen as
    // `"unstated"`; a code here that the backend never answers with is a branch no response can take
    // — a rule this client invented, which is what the contracts layer exists to prevent.
    const table = /^REFUSAL_BY_SQLSTATE: dict\[str, str\] = \{([\s\S]*?)^\}/m.exec(
      WRITE_ERRORS_PY,
    );
    expect(table, "the backend's refusal table is no longer in the shape read here").not.toBeNull();
    // `[0-9A-Z]{5}`, not `\d{5}`: a SQLSTATE is five alphanumerics, and the one this whole file is
    // about — `23P01`, the `EXCLUDE` — is the one with a letter in it.
    const backendCodes = [...(table?.[1] ?? "").matchAll(/"([0-9A-Z]{5})"\s*:/g)].map(
      (match) => match[1],
    );

    expect(backendCodes.length).toBeGreaterThan(0);
    expect([...backendCodes].sort()).toEqual([...Object.values(REFUSAL_SQLSTATE)].sort());
  });

  it("reads a cause out of a refusal built the way the backend builds it, not out of one built here", () => {
    // The contrast that makes the two assertions above mean something. The message is assembled from
    // the backend's own format string and its own constants, so this is the sentence a `409` really
    // carries — and it has to come out as `"stale-marker"`, not as `"unstated"`.
    const format = /f"Writing the \{subject\} failed: \{refused\.__name__\}, ([^ ]+)=\{condition\}/
      .exec(WRITE_ERRORS_PY);
    expect(format, "the backend no longer builds the condition refusal in the shape read here").not
      .toBeNull();
    const label = format?.[1] ?? "";
    const marker = /^CONCURRENCY_MARKER_CONDITION = "([^"]+)"$/m.exec(WRITE_ERRORS_PY)?.[1] ?? "";

    const asTheBackendSendsIt =
      "Refused by the database. Writing the catalogue rate failed: " +
      `CatalogConcurrentEditConflict, ${label}=${marker}. The row changed since it was read.`;

    expect(refusalCauseOf(asTheBackendSendsIt)).toBe("stale-marker");
    // And the other direction: a refusal the backend cannot produce is not quietly classified.
    expect(refusalCauseOf("Refused by the database.")).toBe("unstated");
  });
});
