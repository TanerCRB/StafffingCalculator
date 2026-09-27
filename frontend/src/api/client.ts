import {
  BUDGET_REGIME_NOT_APPLICABLE,
  STATUTORY_LEAVE_STATES,
  type AbsenceBudgetCreateRequest,
  type AbsenceBudgetEntry,
  type AbsenceBudgetList,
  type CatalogAbsenceTypeEntry,
  type CatalogAbsenceTypeList,
  type CatalogDimension,
  type CatalogRate,
  type CatalogRateCreateRequest,
  type CatalogRateEditRequest,
  type CatalogRateList,
  type DimensionEntry,
  type DimensionEntryCreateRequest,
  type DimensionEntryEditRequest,
  type DimensionEntryList,
  type StatutoryLeaveRegime,
  type WorkingCalendarDayEntry,
  type WorkingCalendarEntry,
  type WorkingCalendarList,
} from "./contracts/catalog";
import {
  EXPECTED_REVENUE_STATES,
  OUTCOME_BASED,
  OUTCOME_CATEGORIES,
  RATE_SOURCES,
  REVENUE_NOT_APPLICABLE,
  REVENUE_STATES,
  SOURCE_NOT_APPLICABLE,
  STORY_POINTS,
  STORY_POINTS_RATE_SOURCE,
  TIME_AND_MATERIAL,
  type CommercialTermsCreateRequest,
  type ExpectedRevenueState,
  type ScenarioCommercialTerms,
} from "./contracts/commercialTerms";
import { isDecimalString } from "../lib/money";
import type { HealthResponse } from "./contracts/health";
import type {
  ProjectListItem,
  ProjectListResponse,
  ProjectStatus,
  ScenarioListItem,
  ScenarioStatus,
} from "./contracts/projects";
import {
  ADDITIONAL_COST_STATES,
  PERSONNEL_COST_STATES,
  PROFITABILITY_STATES,
  RESULTS_NOT_APPLICABLE,
  type AdditionalCostSource,
  type PersonnelCostSource,
  type ScenarioResults,
} from "./contracts/scenarioResults";
import {
  ABSENCE_BUDGET_STATES,
  CAPACITY_STATES,
  HOURS_NOT_APPLICABLE,
  type StaffingAbsence,
  type StaffingAllocation,
  type StaffingPositionList,
  type StaffingPositionRead,
} from "./contracts/staffing";

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

// --- Concurrent-request coalescing (Reviewer R-01, gate 2 of SC-3-04/Issue #135) -----------------
//
// Not a cache of data — nothing here answers a caller with a value that might already be stale.
// An entry exists only from the moment one `GET` starts to the moment it settles (or the moment
// nobody is left waiting on it, whichever comes first — see `leave` below), and is dropped from
// the map at exactly that point. A later, non-concurrent call to the same key always makes its own
// fresh request; the only thing this removes is the fan-out of *identical, concurrent* requests.
//
// That fan-out is real, not hypothetical: `StaffingPlanSection` mounts one instance per scenario
// card and every instance calls the same five name-resolving reads on mount
// (`readCatalogNames`) — a project with N scenarios turned five dictionaries, none of which
// depend on `scenarioId` or `projectId`, into N×5 identical `GET`s in flight together. This sits
// under `getCatalogDimension`/`getCatalogAbsenceTypes`'s existing signatures rather than as a
// second mechanism: `CatalogScreen`'s own five-dimension read (`readCatalogue`, including
// `vendors`) goes through the very same function and gets the same deduplication with no change
// to its call site.
//
// Cancellation stays real, not merely cosmetic, once more than one caller shares a request: each
// caller's own `AbortSignal`, when given, is one vote to keep the shared request alive, not a
// unilateral cancellation of a read others are still waiting on — one `StaffingPlanSection`
// instance unmounting must not cancel the read fourteen still-mounted siblings need. Only once
// every caller who asked for this key has left does the underlying request actually abort. When
// that happens, the entry is evicted from the map in the same tick — not on the promise's own
// eventual settling — so an immediate next call (React 18 `StrictMode`'s mount → cleanup → mount,
// in particular) starts a fresh request of its own rather than joining one already given up on.
interface CoalescedRead<T> {
  readonly promise: Promise<T>;
  readonly controller: AbortController;
  subscribers: number;
}

const pendingReads = new Map<string, CoalescedRead<unknown>>();

function evict(key: string, entry: CoalescedRead<unknown>): void {
  // Guarded: by the time this runs, a fresher entry may already occupy `key` (the immediate
  // eviction in `leave` below can race the promise's own `.finally`), and it must not be the one
  // that gets deleted.
  if (pendingReads.get(key) === entry) {
    pendingReads.delete(key);
  }
}

/**
 * Runs `start` at most once per `key` among callers whose calls overlap in time, and hands every
 * caller who asked while it was in flight the same, single settling of it.
 *
 * `signal`, when given, is this caller's own vote to keep the shared request alive (see the
 * section docstring above) — it does not hand this caller a private cancellation of a request
 * others still want.
 */
function coalesced<T>(
  key: string,
  signal: AbortSignal | undefined,
  start: (sharedSignal: AbortSignal) => Promise<T>,
): Promise<T> {
  let entry = pendingReads.get(key) as CoalescedRead<T> | undefined;
  if (entry === undefined) {
    const controller = new AbortController();
    const promise = start(controller.signal);
    entry = { controller, subscribers: 0, promise };
    pendingReads.set(key, entry as CoalescedRead<unknown>);
    const settledEntry = entry;
    // A second observer, deliberately not the one returned to callers: settling — success or
    // failure alike — is also a reason to stop coalescing new callers onto this request, because a
    // later, non-concurrent call must make its own. The `.catch` here only exists to keep this
    // second chain from becoming an unhandled rejection of its own; the real rejection is still
    // `promise` itself, already returned below to whoever calls this.
    promise
      .finally(() => evict(key, settledEntry as CoalescedRead<unknown>))
      .catch(() => {
        /* observed above only to run `evict`; the caller's own `promise` still carries it. */
      });
  }
  const live = entry;
  live.subscribers += 1;
  if (signal !== undefined) {
    const leave = () => {
      live.subscribers -= 1;
      if (live.subscribers <= 0) {
        evict(key, live as CoalescedRead<unknown>);
        live.controller.abort();
      }
    };
    if (signal.aborted) {
      leave();
    } else {
      signal.addEventListener("abort", leave, { once: true });
    }
  }
  return live.promise;
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
 * unmounted (Reviewer R-01) — but only once every other concurrent caller of this same dimension
 * has too: this function is coalesced (Reviewer R-01, gate 2 of SC-3-04, see `coalesced` above),
 * so `CatalogScreen`'s own five-dimension read and `StaffingPlanSection`'s four-dimension read
 * share one network request per dimension whenever they overlap in time, with no change to either
 * call site.
 */
export async function getCatalogDimension(
  dimension: CatalogDimension,
  signal?: AbortSignal,
): Promise<DimensionEntryList> {
  const path = `/catalog/dimensions/${dimension}`;
  return coalesced(path, signal, (sharedSignal) =>
    requestWithDeadline(
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
      sharedSignal,
    ),
  );
}

/** Whether one row has the shape `CatalogAbsenceTypeEntry` promises — only the two fields this
 * client reads (`contracts/catalog.ts`: this endpoint's row carries more, unread by this client). */
function isCatalogAbsenceTypeShape(value: unknown): value is CatalogAbsenceTypeEntry {
  return isRecord(value) && typeof value.id === "string" && typeof value.name === "string";
}

/**
 * Every absence type the catalogue holds (`GET /catalog/absence-types`, SC-3-02). Read only.
 *
 * Its own route, not `/catalog/dimensions/{dimension}` (SC-3-04, `contracts/catalog.ts`) — consumed
 * by SC-3-04 to resolve a staffing absence's `absence_type_id` to a name, the sixth name-resolving
 * read this client performs alongside the four dimension dictionaries `getCatalogDimension` serves
 * (`CATALOG_READ`, independently of `STAFFING_READ` — gate 1, Q4/Q6).
 *
 * `signal`, when given, ends the read early — the card that asked for it has unmounted (Reviewer
 * R-01) — but only once every other concurrent caller has too: this function is coalesced
 * (Reviewer R-01, gate 2 of SC-3-04, see `coalesced` above), so N scenario cards mounting
 * `StaffingPlanSection` together share one `GET /catalog/absence-types`, not N of them.
 */
export async function getCatalogAbsenceTypes(signal?: AbortSignal): Promise<CatalogAbsenceTypeList> {
  const path = "/catalog/absence-types";
  return coalesced(path, signal, (sharedSignal) =>
    requestWithDeadline(
      `${API_BASE_URL}${path}`,
      { headers: { [CALLER_ID_HEADER]: CALLER_USER_ID } },
      async (response) => {
        if (!response.ok) {
          throw new ApiError(response.status, `GET ${path} failed: ${response.status}`);
        }
        const payload = (await response.json()) as CatalogAbsenceTypeList | null;
        if (
          !Array.isArray(payload?.absence_types) ||
          !payload.absence_types.every(isCatalogAbsenceTypeShape)
        ) {
          throw new ApiError(
            response.status,
            `GET ${path} returned a payload without a valid absence-type list`,
          );
        }
        return payload;
      },
      REQUEST_TIMEOUT_MS,
      sharedSignal,
    ),
  );
}

/** Whether one exceptional day has the shape `WorkingCalendarDayEntry` promises. */
function isWorkingCalendarDayShape(value: unknown): value is WorkingCalendarDayEntry {
  return isRecord(value) && typeof value.day === "string" && typeof value.kind === "string";
}

/**
 * Whether one calendar has the shape `WorkingCalendarEntry` promises, including `week_pattern`'s own
 * grammar (SC-3-06, criterion K-01): seven characters, so a malformed pattern ends in this screen's
 * named read failure rather than in `weekPatternDays` throwing mid render. `standard_hours_per_day`
 * is checked against the decimal grammar for the same reason `isCatalogRateShape` checks its
 * amounts — `formatHoursString` calls `roundDecimalString`, which throws on anything else.
 */
function isWorkingCalendarShape(value: unknown): value is WorkingCalendarEntry {
  return (
    isRecord(value) &&
    typeof value.id === "string" &&
    typeof value.name === "string" &&
    isDecimalString(value.standard_hours_per_day) &&
    typeof value.week_pattern === "string" &&
    value.week_pattern.length === 7 &&
    Array.isArray(value.days) &&
    value.days.every(isWorkingCalendarDayShape) &&
    isConcurrencyMarker(value.updated_at)
  );
}

function isWorkingCalendarListShape(value: unknown): value is WorkingCalendarList {
  return (
    isRecord(value) && Array.isArray(value.calendars) && value.calendars.every(isWorkingCalendarShape)
  );
}

/**
 * Every working calendar the catalogue holds (`GET /catalog/working-calendars`, SC-3-02, consumed by
 * SC-3-06). Read only — no edit endpoint exists for a calendar, its basis or its days.
 *
 * `signal`, when given, ends the read early — the screen that asked for it has unmounted (Reviewer
 * R-01).
 */
export async function getWorkingCalendars(signal?: AbortSignal): Promise<WorkingCalendarList> {
  const path = "/catalog/working-calendars";
  return requestWithDeadline(
    `${API_BASE_URL}${path}`,
    { headers: { [CALLER_ID_HEADER]: CALLER_USER_ID } },
    async (response) => {
      if (!response.ok) {
        throw new ApiError(response.status, `GET ${path} failed: ${response.status}`);
      }
      const payload: unknown = await response.json();
      if (!isWorkingCalendarListShape(payload)) {
        throw new ApiError(
          response.status,
          `GET ${path} returned a payload without a valid calendar list`,
        );
      }
      return payload;
    },
    REQUEST_TIMEOUT_MS,
    signal,
  );
}

/** Whether a value has the shape `StatutoryLeaveRegime` promises. */
function isStatutoryLeaveRegimeShape(value: unknown): value is StatutoryLeaveRegime {
  return (
    isRecord(value) &&
    typeof value.absence_type_id === "string" &&
    typeof value.name === "string" &&
    typeof value.generates_cost === "boolean" &&
    typeof value.generates_revenue === "boolean"
  );
}

/** Whether a value is the three-valued shape `generates_cost`/`generates_revenue` promise: a real
 * boolean, or the `"n/a"` sentinel — never anything else, and never read as falsy on faith
 * (criterion K-03; the same discipline `isPersonnelCostSourceShape` already applies to its own
 * gated fields). */
function isBudgetRegimeFlagShape(
  value: unknown,
): value is boolean | typeof BUDGET_REGIME_NOT_APPLICABLE {
  return typeof value === "boolean" || value === BUDGET_REGIME_NOT_APPLICABLE;
}

/**
 * Whether one budget has the shape `AbsenceBudgetEntry` promises, including the pairing the
 * backend's schema docstring states in prose: `statutory_leave`/`generates_cost`/`generates_revenue`
 * carry a real regime **exactly when** `statutory_leave_state === "resolved"`, and `null`/`"n/a"`
 * **exactly otherwise** — never a real regime beside `"no_statutory_leave_type"`, and never the
 * sentinel beside `"resolved"` (criterion K-03, the same discipline `isStaffingAllocationShape`
 * already applies to `derived_capacity_hours`/`absence_budget_hours`).
 */
function isAbsenceBudgetShape(value: unknown): value is AbsenceBudgetEntry {
  if (
    !isRecord(value) ||
    typeof value.id !== "string" ||
    typeof value.calendar_id !== "string" ||
    typeof value.engagement_type_id !== "string" ||
    !isDecimalString(value.budget_days) ||
    typeof value.unit !== "string" ||
    typeof value.source !== "string" ||
    typeof value.effective_from !== "string" ||
    typeof value.effective_to !== "string" ||
    !isOneOf(value.statutory_leave_state, STATUTORY_LEAVE_STATES) ||
    !(value.statutory_leave === null || isStatutoryLeaveRegimeShape(value.statutory_leave)) ||
    !isBudgetRegimeFlagShape(value.generates_cost) ||
    !isBudgetRegimeFlagShape(value.generates_revenue) ||
    !isConcurrencyMarker(value.updated_at)
  ) {
    return false;
  }
  const resolved = value.statutory_leave_state === "resolved";
  const statutoryLeavePaired = resolved
    ? value.statutory_leave !== null
    : value.statutory_leave === null;
  const costPaired = resolved
    ? typeof value.generates_cost === "boolean"
    : value.generates_cost === BUDGET_REGIME_NOT_APPLICABLE;
  const revenuePaired = resolved
    ? typeof value.generates_revenue === "boolean"
    : value.generates_revenue === BUDGET_REGIME_NOT_APPLICABLE;
  return statutoryLeavePaired && costPaired && revenuePaired;
}

function isAbsenceBudgetListShape(value: unknown): value is AbsenceBudgetList {
  return isRecord(value) && Array.isArray(value.budgets) && value.budgets.every(isAbsenceBudgetShape);
}

/**
 * Every leave budget the catalogue holds (`GET /catalog/absence-budgets`, SC-3-03, consumed by
 * SC-3-06). Read only.
 *
 * `signal`, when given, ends the read early — the screen that asked for it has unmounted (Reviewer
 * R-01).
 */
export async function getCatalogAbsenceBudgets(signal?: AbortSignal): Promise<AbsenceBudgetList> {
  const path = "/catalog/absence-budgets";
  return requestWithDeadline(
    `${API_BASE_URL}${path}`,
    { headers: { [CALLER_ID_HEADER]: CALLER_USER_ID } },
    async (response) => {
      if (!response.ok) {
        throw new ApiError(response.status, `GET ${path} failed: ${response.status}`);
      }
      const payload: unknown = await response.json();
      if (!isAbsenceBudgetListShape(payload)) {
        throw new ApiError(
          response.status,
          `GET ${path} returned a payload without a valid budget list`,
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
// rendered. One dated exception, and only one: `createScenarioCommercialTerms` below, whose `201`
// is the server-computed rule and revenue rather than an echo of the form (ADR-0009, addendum
// 2026-09-23, narrowing point 3 — see that function).

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

// --- A scenario's commercial rule and revenue (SC-4-06, consuming SC-4-01) ----------------------

function isRateWindowShape(value: unknown): boolean {
  return (
    isRecord(value) &&
    typeof value.source_rate_id === "string" &&
    typeof value.effective_from === "string" &&
    isRequiredNullableString(value.effective_to) &&
    // The grammar, not only the type: the section renders this rate through `formatRatePerUnit`,
    // and a rate that is a string but not a decimal would throw there mid render (R-01, SC-4-06).
    isDecimalString(value.default_selling_rate) &&
    typeof value.currency === "string"
  );
}

function isUnresolvedMonthShape(value: unknown): boolean {
  return (
    isRecord(value) &&
    typeof value.position_id === "string" &&
    typeof value.period_month === "string"
  );
}

/**
 * Whether the three source fields are the ones the backend emits **for this `model_type`** — a pair,
 * never a per-field union of values (ADR-0003, addendum 2026-09-25 SC-4-07, point 5a; Q-B = B). A
 * union would let a Time & Material revenue through with `not_applicable`, or an Outcome-based one
 * with a catalogue source, and the screen would then render one model's lines under another's name.
 *
 *   * `story_points` — `not_applicable`, `not_applicable`, `story_points_terms` (SC-4-04), and no
 *     rate window or unresolved month: the model reads neither.
 *   * `outcome_based` — `not_applicable` ×3 (SC-4-03, point 8), and likewise no window or month.
 *   * `time_and_material`, and `null` (the scenario without a rule) — the catalogue sources.
 *   * any other stored model — only as the named `unsupported_model_type` state, with the catalogue
 *     sources the backend's dispatcher gives it (`revenue_of`). The response side of `model_type` is
 *     open (SC-4-01, R-02); the closed set of point 4 is what this client can *render as a model*,
 *     and an unknown model is rendered as that state, never as a model.
 *
 * The `unsupported_model_type` state is checked **first**, before the per-model pairing (SC-4-07,
 * verification R-01): in the mixed-version window (ADR-0001) an older backend instance can answer
 * that state for a model this client *does* know (`story_points`, `outcome_based`) — and it answers
 * it with the dispatcher's catalogue sources, not the model's own. Checking the model first would
 * refuse that named state as a broken payload. The render follows the same order
 * (`revenueModelKind`): an unsupported revenue is shown as the state, never as the model's lines.
 *
 * Each model adds only its own values; no Fixed Price value is admitted here (#113, Q3 = A).
 */
function isRevenueSourcePairing(value: Record<string, unknown>, revenueState: unknown): boolean {
  const model = value.model_type;
  const catalogueSources =
    value.hours_source === "billable_hours" &&
    value.vendor_axis === "internal" &&
    isOneOf(value.rate_source, RATE_SOURCES);
  if (revenueState === "unsupported_model_type") {
    // The same sources this state was accepted with before SC-4-07 verification — the catalogue
    // ones — for any stored model, a known one included. `model_type` itself is typed by the caller.
    return catalogueSources;
  }
  const noWindowsOrMonths =
    Array.isArray(value.rate_windows) &&
    value.rate_windows.length === 0 &&
    Array.isArray(value.unresolved_months) &&
    value.unresolved_months.length === 0;
  if (model === STORY_POINTS) {
    return (
      value.hours_source === SOURCE_NOT_APPLICABLE &&
      value.vendor_axis === SOURCE_NOT_APPLICABLE &&
      value.rate_source === STORY_POINTS_RATE_SOURCE &&
      noWindowsOrMonths
    );
  }
  if (model === OUTCOME_BASED) {
    return (
      value.hours_source === SOURCE_NOT_APPLICABLE &&
      value.vendor_axis === SOURCE_NOT_APPLICABLE &&
      value.rate_source === SOURCE_NOT_APPLICABLE &&
      noWindowsOrMonths
    );
  }
  return (model === null || model === TIME_AND_MATERIAL) && catalogueSources;
}

function isRevenueAssumptionsShape(value: unknown, revenueState: unknown): boolean {
  return (
    isRecord(value) &&
    isRequiredNullableString(value.model_type) &&
    isRevenueSourcePairing(value, revenueState) &&
    Array.isArray(value.rate_windows) &&
    value.rate_windows.every(isRateWindowShape) &&
    Array.isArray(value.unresolved_months) &&
    value.unresolved_months.every(isUnresolvedMonthShape) &&
    Array.isArray(value.currencies) &&
    value.currencies.every((currency) => typeof currency === "string")
  );
}

/** A field that is present and either `null` or a fixed-point decimal string — an optional rule
 * component, a category's units or probability. `undefined` is not `null` here: a payload that does
 * not mention the field is not a payload stating its absence. */
function isNullableDecimalString(value: unknown): boolean {
  return value === null || isDecimalString(value);
}

function isCategoryRevenueShape(value: unknown): boolean {
  return (
    isRecord(value) &&
    isOneOf(value.category, OUTCOME_CATEGORIES) &&
    isNullableDecimalString(value.units) &&
    isNullableDecimalString(value.probability) &&
    isDecimalString(value.amount)
  );
}

/**
 * The SC-4-03 fields of a revenue, paired with its `state` and its model (ADR-0003, addendum SC-4-07,
 * point 5b):
 *
 *   * `expected_amount` is a decimal **only** with `expected_state = "calculated"`, and `"n/a"` with
 *     every other state — a `no_probabilities` state carrying a number would put a figure the server
 *     says it does not have under the "expected" label;
 *   * an expected revenue (`calculated` or `no_probabilities`) and the category revenues exist only
 *     for a **calculated Outcome-based** revenue, and that revenue always has them — the categories as
 *     exactly the four fixed words, each once (they are rendered by `category`, never by position,
 *     so a missing or doubled one would be a row silently absent or shown twice). Any other revenue —
 *     another model, or a withheld one — carries `not_applicable`, `"n/a"` and no categories. An
 *     amount formatted without the revenue's own currency is the thing this rules out.
 */
function isExpectedRevenuePairing(value: Record<string, unknown>, outcomeCalculated: boolean): boolean {
  const allowedStates: readonly ExpectedRevenueState[] = outcomeCalculated
    ? ["calculated", "no_probabilities"]
    : ["not_applicable"];
  if (!isOneOf(value.expected_state, EXPECTED_REVENUE_STATES) || !isOneOf(value.expected_state, allowedStates)) {
    return false;
  }
  const expectedAmountPaired =
    value.expected_state === "calculated"
      ? value.expected_amount !== REVENUE_NOT_APPLICABLE && isDecimalString(value.expected_amount)
      : value.expected_amount === REVENUE_NOT_APPLICABLE;
  if (!expectedAmountPaired || !Array.isArray(value.category_revenues)) {
    return false;
  }
  const categories: unknown[] = value.category_revenues;
  if (!outcomeCalculated) {
    return categories.length === 0;
  }
  if (categories.length !== OUTCOME_CATEGORIES.length || !categories.every(isCategoryRevenueShape)) {
    return false;
  }
  const named = new Set(categories.map((category) => (category as { category: string }).category));
  return named.size === OUTCOME_CATEGORIES.length;
}

/**
 * Whether a revenue has the shape `RevenueRead` promises — including the two pairings the schema
 * states in prose, because each of them is a render that would otherwise lie without throwing:
 *
 *   * `state` is one of the **seven** labels the backend's `RevenueState` lists. An unlisted one —
 *     a later backend's eighth state, a typo — is not "an unfamiliar withheld state" to be shown as
 *     some generic message: it is a payload this client cannot read, and ends in the named read
 *     failure (ADR-0010, point 2; SC-4-06 K-01).
 *   * `"calculated"` carries a currency and an amount that is not the `"n/a"` sentinel; every other
 *     state carries `"n/a"`. A calculated revenue with `"n/a"` would reach `formatMoneyString` and
 *     throw mid render; a withheld state carrying a number is a revenue the server says it does not
 *     have, and the screen must not be the one to decide which half to believe.
 *
 *   * a calculated amount — and every window's `default_selling_rate` — is a fixed-point decimal
 *     string by the grammar `lib/money.ts` rounds with (`isDecimalString`), not merely a string.
 *   * the source fields of `assumptions_used` are the ones of its `model_type`
 *     (`isRevenueSourcePairing`), and the SC-4-03 fields are paired with `state` and the model
 *     (`isExpectedRevenuePairing`) — SC-4-07.
 *
 * The grammar is checked here, unlike `isScenarioListItemShape`'s `target_margin_percent`, because
 * of where a violation would otherwise land (Reviewer R-01, SC-4-06). `"abc"` or `"1,00"` passing as
 * a calculated amount reaches `formatMoneyString` and throws mid render; the render boundary then
 * takes down the whole `ProjectListScreen` — every card of every scenario, and after a `201` a save
 * the server has already committed. ADR-0010 point 2 puts a payload that breaks the contract on
 * the network boundary, as this section's named `unreadable` state (and, for the `201`, as the
 * unresolved save), not on the render boundary. The grammar itself still lives in one place
 * (ADR-0002): `isDecimalString` and `roundDecimalString` read it through the same function, and the
 * formatter is not softened (ADR-0010, point 6).
 */
function isRevenueShape(value: unknown): boolean {
  if (!isRecord(value) || !isOneOf(value.state, REVENUE_STATES)) {
    return false;
  }
  const assumptions = value.assumptions_used;
  if (typeof value.amount !== "string" || !isRevenueAssumptionsShape(assumptions, value.state)) {
    return false;
  }
  const outcomeCalculated =
    value.state === "calculated" && isRecord(assumptions) && assumptions.model_type === OUTCOME_BASED;
  if (!isExpectedRevenuePairing(value, outcomeCalculated)) {
    return false;
  }
  if (value.state === "calculated") {
    return (
      value.amount !== REVENUE_NOT_APPLICABLE &&
      isDecimalString(value.amount) &&
      typeof value.currency === "string"
    );
  }
  return value.amount === REVENUE_NOT_APPLICABLE && isRequiredNullableString(value.currency);
}

function isOutcomeCategoryTermsShape(value: unknown): boolean {
  return isRecord(value) && isNullableDecimalString(value.units) && isNullableDecimalString(value.probability);
}

/** `OutcomeTermsRead` — the rule's parameters as stored. Every optional component is present as
 * `null` or a decimal string, and all four categories are there by name (SC-4-03 R-04). */
function isOutcomeTermsShape(value: unknown): boolean {
  if (!isRecord(value) || !isRecord(value.categories)) {
    return false;
  }
  const categories = value.categories;
  return (
    typeof value.currency === "string" &&
    isDecimalString(value.fixed_fee) &&
    isNullableDecimalString(value.success_bonus) &&
    isNullableDecimalString(value.unit_rate) &&
    isNullableDecimalString(value.revenue_min) &&
    isNullableDecimalString(value.revenue_max) &&
    OUTCOME_CATEGORIES.every((category) => isOutcomeCategoryTermsShape(categories[category]))
  );
}

function isCommercialTermsShape(value: unknown): boolean {
  if (
    !isRecord(value) ||
    typeof value.id !== "string" ||
    // Any string: the response side of `model_type` is open (`StoredModelType`, R-02 of SC-4-01).
    // A model this version cannot price is the named `unsupported_model_type` state, not a
    // payload this client cannot read (SC-4-06, K-03).
    typeof value.model_type !== "string" ||
    !isConcurrencyMarker(value.updated_at)
  ) {
    return false;
  }
  // Required, `null` included (ADR-0003, addendum SC-4-07, point 11): `null` for every model but
  // Outcome-based, and for an Outcome-based rule without its details row. Parameters under any other
  // model's name would be shown as that model's — so they are not a payload this client reads.
  if (value.outcome_terms === null) {
    return true;
  }
  return value.model_type === OUTCOME_BASED && isOutcomeTermsShape(value.outcome_terms);
}

/**
 * Whether a response is the `ScenarioCommercialTerms` *of the scenario that was asked about*.
 *
 * `scenario_id` is compared, not only typed: this fragment renders one answer per scenario card, and
 * an answer about another scenario is not an answer to this request, however well-formed it is
 * (SC-4-06, K-07). And `commercial_terms` must be `null` exactly when the revenue says there is no
 * rule — otherwise the card would offer "Set Time & Material" beside an amount, or name a model
 * beside "no rule is set". And `commercial_terms.model_type` must be `revenue.assumptions_used.model_type`
 * (`null` on both sides when there is no rule) — SC-4-07, verification R-03.
 *
 * One predicate for the `GET` and for the `201` alike (ADR-0009, addendum 2026-09-23, narrowing
 * point 3): the body of a successful save is rendered as it stands, so it passes the very same check
 * a read does before anything on screen changes.
 */
function isScenarioCommercialTermsShape(
  value: unknown,
  scenarioId: string,
): value is ScenarioCommercialTerms {
  if (!isRecord(value)) {
    return false;
  }
  const revenue = value.revenue;
  const terms = value.commercial_terms;
  if (
    value.scenario_id !== scenarioId ||
    !isOneOf(value.scenario_status, SCENARIO_STATUSES) ||
    !isRevenueShape(revenue) ||
    !(terms === null || isCommercialTermsShape(terms))
  ) {
    return false;
  }
  const saysNoRule = isRecord(revenue) && revenue.state === "no_commercial_terms";
  if ((terms === null) !== saysNoRule) {
    return false;
  }
  // The rule's model and the revenue's model are one model (SC-4-07, verification R-03). The backend
  // copies `assumptions_used.model_type` from the stored rule (`revenue_of`: `None` without a rule,
  // the rule's own word otherwise — an unsupported one included), so a pair that differs is not an
  // answer this client can read: the card would name one model in "Commercial model:" and render
  // another model's revenue lines and parameters beneath it.
  const assumptions = isRecord(revenue) ? revenue.assumptions_used : undefined;
  const revenueModel = isRecord(assumptions) ? assumptions.model_type : undefined;
  const ruleModel = isRecord(terms) ? terms.model_type : null;
  return ruleModel === revenueModel;
}

function commercialTermsPath(projectId: string, scenarioId: string): string {
  return `/projects/${projectId}/scenarios/${scenarioId}/commercial-terms`;
}

/**
 * A scenario's commercial rule and the revenue derived from it
 * (`GET /projects/{project_id}/scenarios/{scenario_id}/commercial-terms`, SC-4-01). Read only.
 *
 * `403` and `404` stay distinguishable statuses on the `ApiError`: the backend answers every "this
 * scenario is not yours" with one `404`, and a screen that mapped it onto "no rule yet" would offer a
 * write on a scenario that is not the caller's (SC-4-06, K-04). A scenario that simply has no rule is
 * a `200` with `commercial_terms: null`, never an error.
 *
 * `signal`, when given, ends the read early — the card that asked for it has unmounted, or asked
 * again (ADR-0010, point 7).
 */
export async function getScenarioCommercialTerms(
  projectId: string,
  scenarioId: string,
  signal?: AbortSignal,
): Promise<ScenarioCommercialTerms> {
  const path = commercialTermsPath(projectId, scenarioId);
  return requestWithDeadline(
    `${API_BASE_URL}${path}`,
    { headers: { [CALLER_ID_HEADER]: CALLER_USER_ID } },
    async (response) => {
      if (!response.ok) {
        throw new ApiError(response.status, `GET ${path} failed: ${response.status}`);
      }
      const payload: unknown = await response.json();
      if (!isScenarioCommercialTermsShape(payload, scenarioId)) {
        throw new ApiError(response.status, `GET ${path} returned a payload of the wrong shape`);
      }
      return payload;
    },
    REQUEST_TIMEOUT_MS,
    signal,
  );
}

/**
 * Set a scenario's commercial rule (`POST …/commercial-terms`, SC-4-01) — today only Time & Material.
 *
 * Unlike the catalogue's writes, the answer **is** what the screen renders afterwards (ADR-0009,
 * addendum 2026-09-23, narrowing point 3): the `201` carries the full, server-computed
 * `ScenarioCommercialTerms` — rule and revenue — not an echo of the form, and this payload has no
 * cost gate a client could undo. The body goes through the same shape check as the `GET`
 * (`isScenarioCommercialTermsShape`) inside `write`, so a `2xx` this client cannot read ends in an
 * `ApiError` with its real `2xx` status — an unresolved outcome, never a rendered guess.
 */
export async function createScenarioCommercialTerms(
  projectId: string,
  scenarioId: string,
  body: CommercialTermsCreateRequest,
): Promise<ScenarioCommercialTerms> {
  return write(commercialTermsPath(projectId, scenarioId), "POST", body, (value) =>
    isScenarioCommercialTermsShape(value, scenarioId),
  );
}

/** Add one default rate window (`POST /catalog/rates`). */
export async function createCatalogRate(body: CatalogRateCreateRequest): Promise<CatalogRate> {
  return write("/catalog/rates", "POST", body, isCatalogRateShape);
}

/**
 * Add one leave-budget window (`POST /catalog/absence-budgets`, SC-3-03, consumed by SC-3-06).
 *
 * Mirrors `createCatalogRate`/`createDimensionEntry`: the body goes straight to the server, and
 * nothing here checks the window for an overlap first (ADR-0009, point 4; criterion K-05) — the
 * `EXCLUDE` constraint inside the `INSERT` is what refuses one, and the caller renders whatever
 * `describeWriteFailure` makes of the resulting `409`. There is no `unit` choice to omit the way
 * `createCatalogRate` omits one for `RATE_UNIT_HOUR` — `AbsenceBudgetCreateRequest.unit` is optional
 * and defaults on the backend to `BUDGET_UNIT_DAY`, so this function does not need to send it either.
 */
export async function createAbsenceBudget(
  body: AbsenceBudgetCreateRequest,
): Promise<AbsenceBudgetEntry> {
  return write("/catalog/absence-budgets", "POST", body, isAbsenceBudgetShape);
}

// --- Duplicating a scenario (SC-6-03, consuming SC-6-01) ----------------------------------------

function scenarioDuplicatePath(projectId: string, scenarioId: string): string {
  return `/projects/${projectId}/scenarios/${scenarioId}/duplicate`;
}

/**
 * Duplicate one scenario within its own project
 * (`POST /projects/{project_id}/scenarios/{scenario_id}/duplicate`, SC-6-01). No request body.
 *
 * The `201` body is a `ScenarioListItem` — the identical shape `GET /projects` already sends per
 * scenario row, built by the same server-side shaping function (ADR-0009, addendum 2026-09-24).
 * `isScenarioListItemShape` is reused unchanged rather than written a second time, and the body is
 * what the caller renders: no refetch of `GET /projects` after a successful duplicate (ADR-0009,
 * addendum 2026-09-24, narrowing point 3). This endpoint takes no request body, so there is no form
 * to echo, and the source scenario is read-only on the server (`FOR SHARE`) — nothing here writes
 * to it.
 *
 * `error.status` on a thrown `ApiError` stays `403` (missing `SCENARIO_COPY`, enforced before the
 * database) or `404` (out of the caller's scope or nonexistent — one answer for both, ADR-0005)
 * without merging the two, the same distinction `getScenarioCommercialTerms` already keeps for its
 * sibling read. A `409` (no free name left among the candidate suffixes, or a lost naming race)
 * writes nothing; the caller decides what that means, this function only carries the status.
 */
export async function duplicateScenario(
  projectId: string,
  scenarioId: string,
): Promise<ScenarioListItem> {
  return write(
    scenarioDuplicatePath(projectId, scenarioId),
    "POST",
    undefined,
    isScenarioListItemShape,
  );
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

// --- A scenario's whole-life profit, margin and markup (SC-7-02, consuming SC-7-01) ------------

/**
 * Whether a value is one of the shapes `GatedResultField` promises: a fixed-point decimal string, the
 * literal `"n/a"`, or `null` — and nothing else. `null` (the personnel-cost gate) and `"n/a"` (not
 * computable) are read as the two different values they are; neither is folded into the other here,
 * and a payload that says something else again (a JSON number, an empty string) is not admissible —
 * it would reach `formatMoneyString`/`formatPercentString` and throw mid render (ADR-0010, point 2;
 * Issue #94, K-01/K-02).
 */
function isGatedResultFieldShape(value: unknown): boolean {
  if (value === null) {
    return true;
  }
  if (typeof value !== "string") {
    return false;
  }
  if (value === RESULTS_NOT_APPLICABLE) {
    return true;
  }
  return isDecimalString(value);
}

/**
 * Whether a value has the shape `PersonnelCostSource` promises.
 *
 * `amount`/`currency` are read independently of `state` (Issue #94, Architect's impact map): the
 * personnel-cost gate can null the amount while `state` still says `"calculated"`, and a `state` that
 * names a cause can sit beside a gate that is open (`amount: "n/a"`). Both combinations are legal;
 * what is not legal is a `"calculated"` state paired with the `"n/a"` sentinel, or any other state
 * paired with a real decimal amount — the same pairing rule `isRevenueShape` already enforces for
 * revenue, applied here to its cost counterpart.
 */
function isPersonnelCostSourceShape(value: unknown): value is PersonnelCostSource {
  if (!isRecord(value) || !isOneOf(value.state, PERSONNEL_COST_STATES)) {
    return false;
  }
  const amount = value.amount;
  if (amount === null) {
    return isRequiredNullableString(value.currency);
  }
  if (typeof amount !== "string") {
    return false;
  }
  if (amount === RESULTS_NOT_APPLICABLE) {
    return value.state !== "calculated" && isRequiredNullableString(value.currency);
  }
  return value.state === "calculated" && isDecimalString(amount) && typeof value.currency === "string";
}

/**
 * Whether a value has the shape `AdditionalCostSource` promises — the same pairing rule as
 * `isPersonnelCostSourceShape`, without the gate: `additional_cost` is never gated (ADR-0014,
 * point 11), so `amount` is never `null`.
 */
function isAdditionalCostSourceShape(value: unknown): value is AdditionalCostSource {
  if (!isRecord(value) || !isOneOf(value.state, ADDITIONAL_COST_STATES)) {
    return false;
  }
  const amount = value.amount;
  if (typeof amount !== "string") {
    return false;
  }
  if (amount === RESULTS_NOT_APPLICABLE) {
    return value.state !== "calculated" && isRequiredNullableString(value.currency);
  }
  return value.state === "calculated" && isDecimalString(amount) && typeof value.currency === "string";
}

/**
 * Whether a `GatedResultField` value is a real number — neither the gate's `null` nor the `"n/a"`
 * sentinel. Used only to enforce the pairing rule below; `isGatedResultFieldShape` above still owns
 * the field's own shape.
 */
function isRealGatedResultValue(value: unknown): boolean {
  return typeof value === "string" && value !== RESULTS_NOT_APPLICABLE;
}

/**
 * Whether a response is the `ScenarioResults` *of the scenario that was asked about* — the same
 * `scenario_id` discipline `isScenarioCommercialTermsShape` already applies (SC-4-06, K-07).
 *
 * One more pairing rule, on top of each field's own shape: the backend's documented invariant
 * (`backend/app/api/schemas/scenario_results.py`, not enforced by any schema validator on that side
 * either) is that `included_cost`/`profit`/`margin`/`markup` can only be real numbers when
 * `revenue.state === "calculated"` — `isRevenueShape` already guarantees a non-`"calculated"` revenue
 * always carries the `"n/a"` sentinel, so this reads `revenue.state` straight off the now-checked
 * `value.revenue`. A payload that pairs a real value in any of the four with a withheld revenue is
 * not admissible (Reviewer R-01, Issue #94): treated as shape-invalid, same as any other malformed
 * payload, so `getScenarioResults` throws before `ScenarioResultsSection` ever derives a currency
 * from a `null` revenue and hands a real number to `GatedMoneyLine` with no currency to show it in.
 */
function isScenarioResultsShape(value: unknown, scenarioId: string): value is ScenarioResults {
  if (
    !isRecord(value) ||
    value.scenario_id !== scenarioId ||
    !isOneOf(value.scenario_status, SCENARIO_STATUSES) ||
    !isRevenueShape(value.revenue) ||
    !isPersonnelCostSourceShape(value.personnel_cost) ||
    !isAdditionalCostSourceShape(value.additional_cost) ||
    !isGatedResultFieldShape(value.included_cost) ||
    !isGatedResultFieldShape(value.profit) ||
    !isGatedResultFieldShape(value.margin) ||
    !isGatedResultFieldShape(value.markup) ||
    !isOneOf(value.profitability_state, PROFITABILITY_STATES)
  ) {
    return false;
  }
  const revenue = value.revenue;
  // Two pairings, both one-directional: a withheld revenue (R-01 of Issue #94), and a
  // `profitability_state` naming why the aggregate is withheld (ADR-0003, addendum SC-4-07, point 5c),
  // each exclude a real number in the four. `calculated` does not force one — `margin = "n/a"` beside
  // it (AC-05, a zero revenue) is a valid payload, not a contradiction.
  if ((isRecord(revenue) && revenue.state !== "calculated") || value.profitability_state !== "calculated") {
    return (
      !isRealGatedResultValue(value.included_cost) &&
      !isRealGatedResultValue(value.profit) &&
      !isRealGatedResultValue(value.margin) &&
      !isRealGatedResultValue(value.markup)
    );
  }
  return true;
}

function scenarioResultsPath(projectId: string, scenarioId: string): string {
  return `/projects/${projectId}/scenarios/${scenarioId}/results`;
}

/**
 * A scenario's whole-life profit, margin and markup, next to the revenue, personnel cost and
 * additional cost they are built from (`GET …/scenarios/{scenario_id}/results`, SC-7-01). Read only.
 *
 * `403` and `404` both stay on the `ApiError`'s `status` — this screen renders them identically
 * (Issue #94, K-04: unlike `getScenarioCommercialTerms`, which distinguishes them), so the mapping to
 * one rendered state happens in the screen, not by collapsing the status here. `409` (the rate-source
 * race, SC-7-01) stays distinguishable from every other failure for the same reason.
 *
 * `signal`, when given, ends the read early — the card that asked for it has unmounted, or asked
 * again (ADR-0010, point 7).
 */
export async function getScenarioResults(
  projectId: string,
  scenarioId: string,
  signal?: AbortSignal,
): Promise<ScenarioResults> {
  const path = scenarioResultsPath(projectId, scenarioId);
  return requestWithDeadline(
    `${API_BASE_URL}${path}`,
    { headers: { [CALLER_ID_HEADER]: CALLER_USER_ID } },
    async (response) => {
      if (!response.ok) {
        throw new ApiError(response.status, `GET ${path} failed: ${response.status}`);
      }
      const payload: unknown = await response.json();
      if (!isScenarioResultsShape(payload, scenarioId)) {
        throw new ApiError(response.status, `GET ${path} returned a payload of the wrong shape`);
      }
      return payload;
    },
    REQUEST_TIMEOUT_MS,
    signal,
  );
}

// --- A scenario's staffing plan (SC-3-04, Issue #135, consuming SC-3-01/SC-3-02/SC-3-03) --------
// Read only: no function here, and nothing that calls one, reaches a write endpoint of this router
// (`PATCH`/`POST`/`DELETE` under `.../staffing-positions`) — out of scope by construction, not by a
// check this file performs (see Issue #135, "Out of scope").

/** Whether a value is a fixed-point decimal string an hours field is allowed to carry — the grammar
 * `lib/hours.ts` rounds with, read through the same `isDecimalString` money.ts owns (ADR-0002,
 * addendum 2026-09-26 SC-3-04: one grammar, not a second reading of it for hours). */
function isStaffingHoursShape(value: unknown): boolean {
  return typeof value === "string" && isDecimalString(value);
}

/**
 * Whether one allocation month has the shape `StaffingAllocation` promises — including the two
 * pairings the backend's schema docstrings state in prose (`backend/app/api/schemas/staffing.py`):
 * `derived_capacity_hours`/`absence_budget_hours` are decimal strings **exactly when** their own
 * state is `"resolved"`, and the sentinel `"n/a"` **exactly otherwise** — never a real number beside
 * a non-resolved state, and never `"n/a"` beside `"resolved"`. A row that breaks either pairing is
 * not admissible: it would reach `formatHoursString` and throw mid render, or would let a
 * `"no_calendar"` row carry a plausible-looking number (SC-3-04, K-02/K-03; the same discipline
 * `isPersonnelCostSourceShape` already applies to its own gated fields).
 */
function isStaffingAllocationShape(value: unknown): value is StaffingAllocation {
  if (
    !isRecord(value) ||
    typeof value.id !== "string" ||
    typeof value.period_month !== "string" ||
    !isStaffingHoursShape(value.availability_hours) ||
    !isStaffingHoursShape(value.planned_allocation_hours) ||
    !isStaffingHoursShape(value.billable_hours) ||
    !isOneOf(value.derived_capacity_state, CAPACITY_STATES) ||
    !isOneOf(value.absence_budget_state, ABSENCE_BUDGET_STATES)
  ) {
    return false;
  }
  const capacityPaired =
    value.derived_capacity_state === "resolved"
      ? isStaffingHoursShape(value.derived_capacity_hours)
      : value.derived_capacity_hours === HOURS_NOT_APPLICABLE;
  const budgetPaired =
    value.absence_budget_state === "resolved"
      ? isStaffingHoursShape(value.absence_budget_hours)
      : value.absence_budget_hours === HOURS_NOT_APPLICABLE;
  return capacityPaired && budgetPaired;
}

/** Whether one absence has the shape `StaffingAbsence` promises — exactly its four fields
 * (ADR-0005, addendum 2026-09-22 SC-3-02, point 6; this check does not forbid a fifth field being
 * *present*, since `response.json()` cannot un-send one, but this client declares and reads only
 * these four regardless of what else a row might carry). */
function isStaffingAbsenceShape(value: unknown): value is StaffingAbsence {
  return (
    isRecord(value) &&
    typeof value.id === "string" &&
    typeof value.absence_type_id === "string" &&
    typeof value.start_date === "string" &&
    typeof value.end_date === "string"
  );
}

/**
 * Whether one position has the shape `StaffingPositionRead` promises, field by field, including
 * both nested lists (`allocations`, `absences`) — checked one level deeper than any predicate in
 * this file has had to go before (Architect's impact map, SC-3-04): a position whose `allocations`
 * array is `[]` is a legal draft with no months planned yet, exactly as a position list that is
 * itself `[]` is a legal scenario with no positions — neither empty array may be treated as, or
 * produced by, a shape failure (K-05's contrast, generalised one level down).
 */
function isStaffingPositionShape(value: unknown): value is StaffingPositionRead {
  return (
    isRecord(value) &&
    typeof value.id === "string" &&
    typeof value.role_id === "string" &&
    typeof value.seniority_id === "string" &&
    typeof value.location_id === "string" &&
    typeof value.engagement_type_id === "string" &&
    typeof value.headcount === "number" &&
    typeof value.start_date === "string" &&
    isRequiredNullableString(value.end_date) &&
    typeof value.updated_at === "string" &&
    Array.isArray(value.allocations) &&
    value.allocations.every(isStaffingAllocationShape) &&
    Array.isArray(value.absences) &&
    value.absences.every(isStaffingAbsenceShape)
  );
}

function isStaffingPositionListShape(value: unknown): value is StaffingPositionList {
  return (
    isRecord(value) && Array.isArray(value.positions) && value.positions.every(isStaffingPositionShape)
  );
}

function staffingPositionsPath(projectId: string, scenarioId: string): string {
  return `/projects/${projectId}/scenarios/${scenarioId}/staffing-positions`;
}

/**
 * A scenario's whole staffing grid — its positions, their monthly allocations and their planned
 * absences (`GET …/staffing-positions`, SC-3-01). Read only (Issue #135: no write path is reachable
 * from this function or from anything that calls it).
 *
 * `403` (the caller lacks `STAFFING_READ`) and `404` (the scenario is out of the caller's scope, or
 * does not exist) both stay on the `ApiError`'s `status`, exactly as `getScenarioResults` keeps them
 * — the merge into one rendered "unavailable" state (gate 1, Q3 = option A, mirroring K-04 of
 * SC-7-02) happens in `StaffingPlanSection`, not here.
 *
 * `signal`, when given, ends the read early — the card that asked for it has unmounted (ADR-0010,
 * point 7).
 */
export async function getStaffingPositions(
  projectId: string,
  scenarioId: string,
  signal?: AbortSignal,
): Promise<StaffingPositionList> {
  const path = staffingPositionsPath(projectId, scenarioId);
  return requestWithDeadline(
    `${API_BASE_URL}${path}`,
    { headers: { [CALLER_ID_HEADER]: CALLER_USER_ID } },
    async (response) => {
      if (!response.ok) {
        throw new ApiError(response.status, `GET ${path} failed: ${response.status}`);
      }
      const payload: unknown = await response.json();
      if (!isStaffingPositionListShape(payload)) {
        throw new ApiError(
          response.status,
          `GET ${path} returned a payload without a valid position list`,
        );
      }
      return payload;
    },
    REQUEST_TIMEOUT_MS,
    signal,
  );
}
