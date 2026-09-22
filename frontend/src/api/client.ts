import type {
  CatalogDimension,
  CatalogRate,
  CatalogRateCreateRequest,
  CatalogRateEditRequest,
  CatalogRateList,
  DimensionEntry,
  DimensionEntryCreateRequest,
  DimensionEntryEditRequest,
  DimensionEntryList,
} from "./contracts/catalog";
import type { HealthResponse } from "./contracts/health";
import type {
  ProjectListItem,
  ProjectListResponse,
  ProjectStatus,
  ScenarioListItem,
  ScenarioStatus,
} from "./contracts/projects";

const API_BASE_URL: string = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

// --- TEMPORARY, DATED DEVIATION (ADR-0005, addendum 2026-09-18) --------------------------------
// No authentication exists in this repository yet. The caller's identity travels in a request
// header carrying a configured test identifier, matching backend `settings.caller_id_header`.
// This is a placeholder: it exercises the server-side `project_access` filter and proves nothing
// about who the caller really is.
export const CALLER_ID_HEADER = "X-Caller-User-Id";

const CALLER_USER_ID: string = import.meta.env.VITE_CALLER_USER_ID ?? "";

/** How long a request may take before the screen stops waiting. A hung backend must end in a stated
 * failure, not in a loading state that never resolves.
 *
 * One budget for reads and writes alike (ADR-0009, point 1): a write without a deadline is the same
 * class of defect as a read without one, with a worse consequence — a "Save" button that never
 * settles invites a second click, and this contract has no idempotency key. */
export const REQUEST_TIMEOUT_MS = 12_000;

/**
 * What a refused write said about itself, taken from the response body and from nothing else.
 *
 * `detail` is the backend's own sentence — built from identifiers only, never from row values
 * (NF-11, `app.data.write_errors.describe_without_values`). It is carried so that a screen can tell
 * the catalogue's two different `409`s apart (a stale ADR-0007 marker versus an overlap or a
 * duplicate name) without inventing a cause; `contracts/writeRefusals.ts` is where that reading
 * happens.
 *
 * `fields` is the other shape a FastAPI error body takes: a `422` answers with a list of locations,
 * not a sentence, and the last segment of each location is the field the request got wrong (NF-07).
 */
export interface ApiErrorReport {
  readonly detail?: string;
  readonly fields?: readonly string[];
}

/** A failed HTTP call, carrying the status so a screen can tell "denied" from "broken" without
 * parsing a message string — and, for a write, what the body said refused it. */
export class ApiError extends Error {
  readonly status: number;

  /** The refusal's own words, when the body carried a sentence. `undefined` for a body that was not
   * JSON, or carried no `detail` — which is a state a screen has to be able to render, because
   * "refused, and the answer did not say why" is a true thing to say and a guess is not. */
  readonly detail?: string;

  /** The fields a `422` named, in the request's own vocabulary (`default_cost_rate`, `currency`). */
  readonly fields?: readonly string[];

  constructor(status: number, message: string, report: ApiErrorReport = {}) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = report.detail;
    this.fields = report.fields;
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
 * Runs one request — the call *and* its body — under a single deadline. Every fetch wrapper in this
 * file goes through it; a request that does not, has no deadline at all.
 *
 * Reads and writes alike (ADR-0009, point 1): the mutating wrappers at the bottom of this file are
 * the same primitive with a method and a body, so there is one place where a deadline, an abort and
 * the `ApiError`/`RequestTimeoutError` distinction are decided. A second, write-only primitive is
 * how the two budgets start to differ without anybody deciding that they should.
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
async function requestWithDeadline<T>(
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
  return requestWithDeadline(`${API_BASE_URL}/health`, {}, async (response) => {
    if (!response.ok) {
      throw new Error(`GET /health failed: ${response.status}`);
    }
    return (await response.json()) as HealthResponse;
  });
}

/**
 * The caller's project list (SC-1-05). Read only — the server decides which projects exist for
 * this caller; this function adds no filter, no sort and no default of its own (NF-04).
 *
 * `signal`, when given, ends the read early — typically because the screen that asked for it has
 * unmounted (SC-1-09, K-05). Without it a bounced-off screen leaves the request running to
 * completion, holding one of the browser's six same-origin HTTP/1.1 sockets against the screen the
 * user actually moved to; a `cancelled` flag that only blocks `setState` does nothing about that,
 * and reads as cancellation in a review.
 */
export async function getProjects(signal?: AbortSignal): Promise<ProjectListResponse> {
  return requestWithDeadline(
    `${API_BASE_URL}/projects`,
    { headers: { [CALLER_ID_HEADER]: CALLER_USER_ID } },
    async (response) => {
      if (!response.ok) {
        throw new ApiError(response.status, `GET /projects failed: ${response.status}`);
      }
      const payload = (await response.json()) as ProjectListResponse | null;
      if (!Array.isArray(payload?.projects) || !payload.projects.every(isProjectListItemShape)) {
        // A payload that does not match the contract is an error, not an empty list: an empty
        // list is a statement ("you have no projects") and may only come from the server.
        //
        // `every`, so the *whole response* is rejected rather than the offending rows quietly
        // dropped (SC-1-09, K-01). Per-row filtering would render a shorter list that looks exactly
        // like a complete one — the error that renders correctly, and the one thing worse than the
        // blank page this check exists to prevent.
        throw new ApiError(
          response.status,
          "GET /projects returned a payload without a valid project list",
        );
      }
      return payload;
    },
    REQUEST_TIMEOUT_MS,
    signal,
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
 * A field that is present and either `null` or a string — the shape `vendor_id` takes, and a
 * deliberately stricter check than `isOptionalString` above (SC-2-03).
 *
 * `undefined` is rejected rather than folded into `null`: on this field `null` carries a meaning —
 * "this is the organisation's own rate" — and a payload that simply does not mention vendors (an
 * older backend, a proxy stripping fields) would otherwise render as a table of rates all stated to
 * be internal. That is a false statement about money, rendered without anything throwing, which is
 * exactly the failure a shape check exists to turn into a stated failure.
 */
function isRequiredNullableString(value: unknown): boolean {
  return value === null || typeof value === "string";
}

/**
 * Whether a value is one of a closed set of string labels the contract names.
 *
 * One helper rather than a comparison per field: a status the contract does not list is not a
 * "string that happens to be unfamiliar", it is a payload this client cannot read — and the
 * alternative spelling (`typeof value === "string"`) is how `"Deleted"` would reach a badge whose
 * fill is keyed by the label, rendering an unrecognised status as an unstyled word nobody planned
 * (SC-1-09, gate-1 follow-up decision 4).
 */
function isOneOf<T extends string>(value: unknown, allowed: readonly T[]): value is T {
  return typeof value === "string" && (allowed as readonly string[]).includes(value);
}

/** Exactly what `contracts/projects.ts` declares — the two labels the backend emits, and no
 * others. Kept beside the predicate that uses them so the closed set and the check cannot drift. */
const PROJECT_STATUSES: readonly ProjectStatus[] = ["Active", "Archived"];
const SCENARIO_STATUSES: readonly ScenarioStatus[] = ["Draft", "Approved"];

function isDeliveryPeriodShape(value: unknown): boolean {
  return isRecord(value) && typeof value.start === "string" && typeof value.end === "string";
}

/**
 * Whether one scenario has the shape `ScenarioListItem` promises.
 *
 * `target_margin_percent` is the field this check exists for. It is optional and nullable by
 * contract and a *fixed-point decimal string* when present (ADR-0002); a backend or proxy
 * serialising it as a JSON number instead passes `Array.isArray`, reaches `formatPercentString`,
 * and throws a `TypeError` from `.trim()` in the middle of a render. The type is checked here so
 * that a broken contract ends in the screen's own named read failure instead of in the render-phase
 * boundary — which is the ordering ADR-0010 asks for, and the reason the two mechanisms are two
 * (SC-1-09, K-01).
 *
 * What it deliberately does NOT check is the *format* of that string: `"abc"` is a string and gets
 * through. The formatter still throws on it (ADR-0002 — no try/catch softening, K-06) and the
 * render boundary catches that. A shape check is not a value check, and pretending otherwise here
 * would put the decimal grammar in two places.
 */
function isScenarioListItemShape(value: unknown): value is ScenarioListItem {
  return (
    isRecord(value) &&
    typeof value.id === "string" &&
    typeof value.name === "string" &&
    isOneOf(value.status, SCENARIO_STATUSES) &&
    Array.isArray(value.missing_inputs) &&
    value.missing_inputs.every((input) => typeof input === "string") &&
    typeof value.ready_for_approval === "boolean" &&
    isOptionalString(value.target_margin_percent)
  );
}

/**
 * Whether one project row has the shape `ProjectListItem` promises, field by field — the project
 * list's counterpart to `isCatalogRateShape`, and the first line of defence ADR-0010 puts *before*
 * the render boundary (SC-1-09, K-01).
 *
 * A row missing `delivery_period` throws on `project.delivery_period.start` mid render; a row whose
 * `status` is not one of the two labels renders a badge with no fill behind it. Both become a named
 * `ApiError` here, which the screen's existing `toFailureState` already turns into "Projects could
 * not be loaded." — an answer the screen already knows how to give.
 *
 * `owner` is absent on purpose and not checked: the backend removed it from this payload (B-02).
 * Requiring it would be this layer demanding a field the contract says must not be sent.
 */
function isProjectListItemShape(value: unknown): value is ProjectListItem {
  if (!isRecord(value)) {
    return false;
  }
  const requiredStrings: readonly (keyof ProjectListItem)[] = [
    "id",
    "name",
    "client",
    "reporting_currency",
    "description",
  ];
  return (
    requiredStrings.every((field) => typeof value[field] === "string") &&
    isOneOf(value.status, PROJECT_STATUSES) &&
    isDeliveryPeriodShape(value.delivery_period) &&
    Array.isArray(value.scenarios) &&
    value.scenarios.every(isScenarioListItemShape)
  );
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
    isRequiredNullableString(value.vendor_id) &&
    isOptionalString(value.default_cost_rate) &&
    isOptionalString(value.effective_to) &&
    isConcurrencyMarker(value.updated_at)
  );
}

/**
 * Whether a row carried the ADR-0007 concurrency marker it is contractually required to carry
 * (SC-2-04).
 *
 * Required, like `vendor_id` and unlike `default_cost_rate`: the marker is never gated, so a row
 * without one is a payload this client cannot read rather than a permission being exercised. The
 * consequence of folding it into `undefined` instead is specific and bad — the edit form would
 * offer to save a row with no marker, which is either a request the backend answers `422` (the
 * field is required) or, if a client ever "helped" by inventing one, a lost update: the marker is
 * the only thing standing between two people editing one row and the second one silently winning.
 */
function isConcurrencyMarker(value: unknown): boolean {
  return typeof value === "string" && value !== "";
}

function isDimensionEntryShape(value: unknown): value is DimensionEntry {
  return (
    isRecord(value) &&
    typeof value.id === "string" &&
    typeof value.name === "string" &&
    isConcurrencyMarker(value.updated_at)
  );
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
  return requestWithDeadline(
    `${API_BASE_URL}/catalog/rates`,
    { headers: { [CALLER_ID_HEADER]: CALLER_USER_ID } },
    async (response) => {
      if (!response.ok) {
        throw new ApiError(response.status, `GET /catalog/rates failed: ${response.status}`);
      }
      const payload = (await response.json()) as CatalogRateList | null;
      if (
        !Array.isArray(payload?.rates) ||
        !payload.rates.every(isCatalogRateShape) ||
        typeof payload.total !== "number"
      ) {
        // Same rule as the project list: an empty list is a statement ("the catalogue holds no
        // rates") and may only come from the server. A payload that does not match the contract —
        // missing the array, missing `total`, or carrying a row of the wrong shape — is an error,
        // never an empty catalogue and never a row rendered on faith. `total` is required here, not
        // merely optional-checked: a backend that omitted it would leave the screen unable to tell
        // a complete catalogue from a truncated page (K-12), silently understating its own size.
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
  return requestWithDeadline(
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

// --- Writing (SC-2-04, ADR-0009) ---------------------------------------------------------------
// Four wrappers, one primitive, no retry. ADR-0009 decides the three things they have in common:
//
//   1. They go through `requestWithDeadline`, so a write carries the same explicit time budget as a
//      read and ends in `RequestTimeoutError` rather than in a button that never settles (point 1).
//   2. Nothing here retries. The contract has no idempotency key, so a client cannot tell "it never
//      arrived" from "it committed and the answer was lost", and a silent second attempt would be
//      the one place where silence risks a duplicated row rather than an awkward screen (point 2).
//   3. Nothing here validates a business rule before sending. Overlapping windows, duplicate names
//      and a stale concurrency marker are decided by the database inside the write, and the screen
//      renders the refusal rather than anticipating it (point 4).
//
// None of them returns anything a screen is allowed to put in a table: the row on screen after a
// save comes from a fresh read, never from the write's answer (point 3, gate-1 decision P-3a). They
// return the parsed body so that a caller can `await` a real answer — not so that it can be
// rendered.

const JSON_REQUEST_HEADERS: Readonly<Record<string, string>> = {
  [CALLER_ID_HEADER]: CALLER_USER_ID,
  "Content-Type": "application/json",
};

/**
 * Builds the `ApiError` for a refused write, from the response body and from nothing else.
 *
 * FastAPI answers with `detail` in two different shapes, and both are read here because both carry
 * something a person needs: a string for the refusals this application raises itself (`403`, `404`,
 * `409` — the sentence naming the mechanism that refused), and a list of locations for a `422`
 * produced by request validation, where the last segment of each location is the field.
 *
 * A body that is not JSON, or that carries neither, leaves both fields `undefined` — and that is an
 * answer the screen renders as "refused, and the response did not say which rule refused it". The
 * alternative is a screen that picks the most plausible cause, which is the defect R-01 measured on
 * the backend a level down: a refusal with a confident face on it and nothing behind it.
 */
async function refusalOf(response: Response, what: string): Promise<ApiError> {
  let detail: string | undefined;
  let fields: string[] | undefined;
  try {
    const body: unknown = await response.json();
    const reported = isRecord(body) ? body.detail : undefined;
    if (typeof reported === "string") {
      detail = reported;
    } else if (Array.isArray(reported)) {
      fields = reported.flatMap((item) => {
        const location = isRecord(item) ? item.loc : undefined;
        if (!Array.isArray(location) || location.length === 0) {
          return [];
        }
        return [String(location[location.length - 1])];
      });
    }
  } catch {
    // A refusal whose body could not be read is still a refusal, and the status still says what
    // kind. Swallowing the parse error here is what keeps that true; rethrowing would turn a
    // stated `403` into an unstated failure.
  }
  return new ApiError(response.status, `${what} failed: ${response.status}`, { detail, fields });
}

async function write<T>(
  path: string,
  method: "POST" | "PATCH",
  body: unknown,
  isShape: (value: unknown) => boolean,
): Promise<T> {
  const what = `${method} ${path}`;
  return requestWithDeadline(
    `${API_BASE_URL}${path}`,
    { method, headers: JSON_REQUEST_HEADERS, body: JSON.stringify(body) },
    async (response) => {
      if (!response.ok) {
        throw await refusalOf(response, what);
      }
      const payload: unknown = await response.json();
      if (!isShape(payload)) {
        // The same rule as on the reads: a body that does not match the contract is an error. It
        // matters more here, not less — "the save worked" is the one claim a screen must not make
        // on faith, and a `201` carrying something this client cannot read is not evidence that a
        // row exists.
        throw new ApiError(response.status, `${what} returned a payload of the wrong shape`);
      }
      return payload as T;
    },
  );
}

/** Add one entry to one dimension dictionary (`POST /catalog/dimensions/{dimension}`).
 *
 * One function for all five dictionaries, mirroring the one endpoint that serves them: `vendors` is
 * the fifth dictionary, not a fifth mechanism (SC-2-03, K-05). */
export async function createDimensionEntry(
  dimension: CatalogDimension,
  body: DimensionEntryCreateRequest,
): Promise<DimensionEntry> {
  return write(`/catalog/dimensions/${dimension}`, "POST", body, isDimensionEntryShape);
}

/** Rename one entry of one dimension dictionary
 * (`PATCH /catalog/dimensions/{dimension}/{entry_id}`).
 *
 * `body.updated_at` is the marker that came back with the read this edit is based on, handed back
 * untouched. Nothing here compares it to anything: the comparison happens inside the backend's
 * `UPDATE` statement (ADR-0007, addendum 2026-09-21, point 3), and a client-side check would be the
 * check-then-act shape that survived a full delivered test suite once already. */
export async function editDimensionEntry(
  dimension: CatalogDimension,
  entryId: string,
  body: DimensionEntryEditRequest,
): Promise<DimensionEntry> {
  return write(
    `/catalog/dimensions/${dimension}/${entryId}`,
    "PATCH",
    body,
    isDimensionEntryShape,
  );
}

/** Add one default rate window (`POST /catalog/rates`). */
export async function createCatalogRate(body: CatalogRateCreateRequest): Promise<CatalogRate> {
  return write("/catalog/rates", "POST", body, isCatalogRateShape);
}

/**
 * Correct one default rate window (`PATCH /catalog/rates/{rate_id}`).
 *
 * `body` is partial by contract: a field it does not carry is a field the edit does not touch. That
 * is what lets a caller without `PERSONNEL_COSTS_READ` edit a selling rate or a window at all — they
 * omit `default_cost_rate`, which they were never sent, and the stored cost is left alone (Issue
 * #49, gate-1 decision Q-2). `JSON.stringify` drops `undefined` properties, so "absent from the
 * object" and "absent from the body" are the same thing; `effective_to: null`, the one meaningful
 * null, survives it.
 */
export async function editCatalogRate(
  rateId: string,
  body: CatalogRateEditRequest,
): Promise<CatalogRate> {
  return write(`/catalog/rates/${rateId}`, "PATCH", body, isCatalogRateShape);
}
