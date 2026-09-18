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
 */
async function readWithDeadline<T>(
  url: string,
  init: RequestInit,
  handle: (response: Response) => Promise<T>,
  timeoutMs: number = REQUEST_TIMEOUT_MS,
): Promise<T> {
  const controller = new AbortController();
  let timeoutHandle: ReturnType<typeof setTimeout> | undefined;

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
