import type {
  CatalogDimension,
  CatalogRate,
  CatalogRateList,
  DimensionEntry,
  DimensionEntryList,
} from "./contracts/catalog";
import type { HealthResponse } from "./contracts/health";
import type { ProjectListResponse } from "./contracts/projects";

const API_BASE_URL: string = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

// --- TEMPORARY, DATED DEVIATION (ADR-0005, addendum 2026-09-18) --------------------------------
// No authentication exists in this repository yet. The caller's identity travels in a request
// header carrying a configured test identifier, matching backend `settings.caller_id_header`.
// This is a placeholder: it exercises the server-side `project_access` filter and proves nothing
// about who the caller really is.
export const CALLER_ID_HEADER = "X-Caller-User-Id";

const CALLER_USER_ID: string = import.meta.env.VITE_CALLER_USER_ID ?? "";

/** How long a read may take before the screen stops waiting. A hung backend must end in a stated
 * failure, not in a loading state that never resolves. */
export const REQUEST_TIMEOUT_MS = 12_000;

/** A failed HTTP call, carrying the status so a screen can tell "denied" from "broken" without
 * parsing a message string. */
export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

/** The request did not complete in time. Distinct from `ApiError`: there is no status, because
 * the server never answered — the screen says that, instead of guessing a reason. */
export class RequestTimeoutError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "RequestTimeoutError";
  }
}

/**
 * Runs one read — request *and* body — under a single deadline. Every fetch wrapper in this file
 * goes through it; a read that does not, has no deadline at all.
 *
 * The deadline deliberately covers `handle`, which is where the body is consumed. Arriving
 * headers are not an answer: a backend or proxy can send `200`, then stall mid-stream (truncated
 * upstream, chunked response with no terminator, a dead connection after a rolling deploy). If
 * the timer were cleared as soon as `fetch()` resolved, `response.json()` would hang forever and
 * the screen would sit in "Loading…" — the exact state this mechanism exists to prevent.
 *
 * `signal` is a second, independent reason to give up: the caller's own — typically a screen
 * unmounting before the read finished. Without it, a read that is no longer wanted keeps running
 * to completion anyway, competing for one of the browser's six same-origin HTTP/1.1 sockets with
 * the reads the screen the user is actually on is waiting for (Reviewer R-01).
 */
async function readWithDeadline<T>(
  url: string,
  init: RequestInit,
  handle: (response: Response) => Promise<T>,
  timeoutMs: number = REQUEST_TIMEOUT_MS,
  signal?: AbortSignal,
): Promise<T> {
  const controller = new AbortController();
  let timeoutHandle: ReturnType<typeof setTimeout> | undefined;

  // The caller's signal aborts the same controller the deadline does, so `fetch` only ever sees
  // one signal no matter which of the two fires first.
  if (signal !== undefined) {
    if (signal.aborted) {
      controller.abort();
    } else {
      signal.addEventListener("abort", () => controller.abort(), { once: true });
    }
  }

  const expiry = new Promise<never>((_resolve, reject) => {
    timeoutHandle = setTimeout(() => {
      // Abort the request as well as rejecting: the abort reaches a body still being streamed,
      // not just a request still waiting for headers.
      controller.abort();
      reject(new RequestTimeoutError(`${url} did not complete within ${timeoutMs} ms`));
    }, timeoutMs);
  });

  const read = (async () => {
    const response = await fetch(url, { ...init, signal: controller.signal });
    return handle(response);
  })();

  try {
    return await Promise.race([read, expiry]);
  } finally {
    // Only now: the race has settled, so the body has either been read or been given up on.
    clearTimeout(timeoutHandle);
  }
}

export async function getHealth(): Promise<HealthResponse> {
  return readWithDeadline(`${API_BASE_URL}/health`, {}, async (response) => {
    if (!response.ok) {
      throw new Error(`GET /health failed: ${response.status}`);
    }
    return (await response.json()) as HealthResponse;
  });
}

/** The caller's project list (SC-1-05). Read only — the server decides which projects exist for
 * this caller; this function adds no filter, no sort and no default of its own (NF-04). */
export async function getProjects(): Promise<ProjectListResponse> {
  return readWithDeadline(
    `${API_BASE_URL}/projects`,
    { headers: { [CALLER_ID_HEADER]: CALLER_USER_ID } },
    async (response) => {
      if (!response.ok) {
        throw new ApiError(response.status, `GET /projects failed: ${response.status}`);
      }
      const payload = (await response.json()) as ProjectListResponse | null;
      if (!Array.isArray(payload?.projects)) {
        // A payload that does not match the contract is an error, not an empty list: an empty
        // list is a statement ("you have no projects") and may only come from the server.
        throw new ApiError(
          response.status,
          "GET /projects returned a payload without a project list",
        );
      }
      return payload;
    },
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

/** A field that is either absent, `null`, or a string — the shape `default_cost_rate` and
 * `effective_to` are contractually allowed to take (`contracts/catalog.ts`). */
function isOptionalString(value: unknown): boolean {
  return value === undefined || value === null || typeof value === "string";
}

/**
 * Whether one rate row has the shape `CatalogRate` promises, field by field — not merely "this
 * parsed as an array" (Reviewer R-02). `formatRatePerUnit` calls `roundDecimalString`, which calls
 * `.trim()` on its input: a row missing `default_selling_rate`, or carrying it as a number a
 * backend change serialised differently, would not fail here — it would throw a `TypeError` mid
 * render, in a codebase with no error boundary, taking the whole screen down to a blank page. A row
 * that fails this check becomes a named `ApiError` instead, which the screen's existing
 * `toFailureState` machinery already turns into a stated failure.
 */
function isCatalogRateShape(value: unknown): value is CatalogRate {
  if (!isRecord(value)) {
    return false;
  }
  const requiredStrings: readonly (keyof CatalogRate)[] = [
    "id",
    "role_id",
    "seniority_id",
    "location_id",
    "engagement_type_id",
    "default_selling_rate",
    "currency",
    "unit",
    "effective_from",
  ];
  return (
    requiredStrings.every((field) => typeof value[field] === "string") &&
    isOptionalString(value.default_cost_rate) &&
    isOptionalString(value.effective_to)
  );
}

function isDimensionEntryShape(value: unknown): value is DimensionEntry {
  return isRecord(value) && typeof value.id === "string" && typeof value.name === "string";
}

/**
 * Every default rate the catalogue holds (SC-2-01, `GET /catalog/rates`). Read only.
 *
 * No `on_date` parameter: the backend deliberately refused a default of "today"
 * (`list_catalog_rates`), and a client that supplied one would be inventing the resolution rule
 * the server declined to own (SC-2-02, gate-1 decision 3). The rows come back exactly as sent,
 * including a row whose `default_cost_rate` the server removed for this caller.
 *
 * `signal`, when given, ends the read early — typically because the screen that asked for it has
 * unmounted (Reviewer R-01).
 */
export async function getCatalogRates(signal?: AbortSignal): Promise<CatalogRateList> {
  return readWithDeadline(
    `${API_BASE_URL}/catalog/rates`,
    { headers: { [CALLER_ID_HEADER]: CALLER_USER_ID } },
    async (response) => {
      if (!response.ok) {
        throw new ApiError(response.status, `GET /catalog/rates failed: ${response.status}`);
      }
      const payload = (await response.json()) as CatalogRateList | null;
      if (!Array.isArray(payload?.rates) || !payload.rates.every(isCatalogRateShape)) {
        // Same rule as the project list: an empty list is a statement ("the catalogue holds no
        // rates") and may only come from the server. A payload that does not match the contract —
        // missing the array, or carrying a row of the wrong shape — is an error, never an empty
        // catalogue and never a row rendered on faith.
        throw new ApiError(
          response.status,
          "GET /catalog/rates returned a payload without a valid rate list",
        );
      }
      return payload;
    },
    REQUEST_TIMEOUT_MS,
    signal,
  );
}

/**
 * One dimension dictionary's entries (`GET /catalog/dimensions/{dimension}`). Read only.
 *
 * `dimension` is typed by `CATALOG_DIMENSIONS`, so a segment the backend answers with a `404`
 * cannot be spelled at a call site.
 *
 * `signal`, when given, ends the read early — typically because the screen that asked for it has
 * unmounted (Reviewer R-01).
 */
export async function getCatalogDimension(
  dimension: CatalogDimension,
  signal?: AbortSignal,
): Promise<DimensionEntryList> {
  const path = `/catalog/dimensions/${dimension}`;
  return readWithDeadline(
    `${API_BASE_URL}${path}`,
    { headers: { [CALLER_ID_HEADER]: CALLER_USER_ID } },
    async (response) => {
      if (!response.ok) {
        throw new ApiError(response.status, `GET ${path} failed: ${response.status}`);
      }
      const payload = (await response.json()) as DimensionEntryList | null;
      if (!Array.isArray(payload?.entries) || !payload.entries.every(isDimensionEntryShape)) {
        throw new ApiError(
          response.status,
          `GET ${path} returned a payload without a valid entry list`,
        );
      }
      return payload;
    },
    REQUEST_TIMEOUT_MS,
    signal,
  );
}
