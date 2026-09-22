// What the catalogue screen says after a save attempt — every ending in one place, so that two
// forms cannot come to word the same refusal two ways, and so that the set can be held against
// itself (no message is a substring of another) by a single test.
//
// Note: this is NOT a translation catalog — no locale mechanism exists in this repository yet (see
// frontend/README.md and dimensionLabels.ts, which says the same).
//
// ADR-0009, point 5: success, `422`, `409`, `403`, `500` and a timeout are six different, named
// endings; none of them is "something went wrong". ADR-0007's addendum of 2026-09-21, point 4, adds
// the one that is not a status code at all: on the catalogue's edit path a `409` has two unrelated
// meanings, and the two lead to two different actions — "re-read and try again, it may work" versus
// "this will never save as written". A screen that merged them would send a person into a loop.
//
// Two rules hold for every sentence below, and both are asserted rather than stated:
//
//   * None of them asserts a cause the response body did not report. The `409` whose body names no
//     mechanism gets `CONFLICT_UNSTATED` — "refused, and the answer did not say which rule" — which
//     is the true thing to say, and the only honest one.
//   * None of them carries a value from the form. The backend's refusals quote no row values
//     (NF-11), and this layer adds none back (ADR-0009, point 6): the typed values stay in the
//     fields, which is component state, not diagnostics.

import { ApiError, RequestTimeoutError } from "../../api/client";
import { refusalCauseOf } from "../../api/contracts/writeRefusals";

/** The caller may read the catalogue and may not change it. Unreachable against today's backend —
 * `PLACEHOLDER_PERMISSIONS` grants `CATALOG_WRITE` to everyone (ADR-0005, addendum 2026-09-19,
 * point 6) — and named anyway, because the branch is the server's to reach, not this screen's. */
export const SAVE_DENIED =
  "Not saved — you do not have permission to change the catalogue.";

/** ADR-0008's `EXCLUDE` constraint refused the window (SQLSTATE 23P01). The guidance is the half
 * that distinguishes this from the stale-marker conflict: retrying changes nothing, the dates do. */
export const SAVE_REFUSED_OVERLAP =
  "Not saved — this effective window overlaps one that already exists for the same tuple. " +
  "Change the dates and save again.";

/** A unique index refused the value (SQLSTATE 23505) — in practice a dictionary name that already
 * exists, compared after normalisation, so "Backend Engineer" collides with "backend engineer". */
export const SAVE_REFUSED_DUPLICATE =
  "Not saved — the catalogue already holds an entry with that name. Choose a different name.";

/** A foreign key refused the value (SQLSTATE 23503): a vendor or a dimension entry that is not
 * there any more. Reachable without anything failing — the dictionaries were read a moment ago. */
export const SAVE_REFUSED_MISSING_REFERENCE =
  "Not saved — this change names a catalogue entry that no longer exists. " +
  "Reload the catalogue and try again.";

/** A CHECK constraint refused a value (SQLSTATE 23514). Deliberately vague about *which* rule: the
 * backend reports the constraint's name, not its meaning, and inventing one here would be this
 * screen asserting a rule the database never explained to it. */
export const SAVE_REFUSED_BROKEN_RULE =
  "Not saved — the catalogue rejected one of these values by a rule it enforces.";

/**
 * ADR-0007's marker moved: the row was committed to between the read this form was filled from and
 * this save (`condition=updated_at_marker`).
 *
 * Distinct wording *and* distinct guidance from every other `409` (K-23). It carries nothing about
 * the competing change — not the field, not the value, not who made it — because the backend
 * carries nothing either: the other writer's value may well be data this caller may not see
 * (ADR-0007, NF-11).
 *
 * And it names **nobody**, which is the reason this sentence was reworded (Reviewer R-01,
 * 2026-09-22). "Somebody else changed this row" is a cause the response never reported, and two
 * reachable paths on this very screen make it false — in both, the competing writer is the person
 * reading the sentence:
 *
 *   * After `SAVED_BUT_NOT_REREAD` (G-6) the rows on screen still carry their pre-edit markers.
 *     Opening *Edit* on the row that was just saved — the obvious way to check what happened —
 *     seeds the form from the stale marker, and any submit comes back `condition=updated_at_marker`.
 *   * After `SAVE_UNRESOLVED` the write may well have committed (ADR-0009, point 2). A second
 *     attempt then conflicts with the first one, which was this caller's own.
 *
 * So the sentence states only what the backend stated (the row changed since it was read), names
 * the caller's own earlier save as a possibility rather than asserting any author, and points at
 * the action that settles it either way.
 */
export const SAVE_REFUSED_STALE_MARKER =
  "Not saved — this row changed since it was last read, possibly by an earlier save of your own. " +
  "Reload the catalogue to see its current values and make the change again.";

/** A `409` whose body named no mechanism at all. The honest ending, and a first-class one. */
export const SAVE_REFUSED_UNSTATED =
  "Not saved — the catalogue refused this change, and the answer did not say which rule refused it.";

/** A `422`: the request's shape was wrong and the body named no field. */
export const SAVE_INVALID = "Not saved — the server rejected the values in this form.";

/** A `422` that named fields (NF-07). The names are the server's; `FIELD_LABELS` below translates
 * the ones this form knows into the words it puts beside its own inputs, and leaves the rest as the
 * server spelled them — a field this screen does not have is still worth naming exactly. */
export function saveInvalidFields(fields: readonly string[]): string {
  const named = fields.map((field) => FIELD_LABELS[field] ?? field);
  return `Not saved — the server rejected these values: ${named.join(", ")}.`;
}

/** `500`, or any other **non-2xx** status this screen has no name for. Deliberately says that the
 * outcome is unknown rather than that nothing was written: a write that broke mid-statement is not
 * a write that provably did not happen.
 *
 * A 2xx does not reach this sentence any more (Reviewer R-03) — see
 * `SAVE_UNRESOLVED_UNREADABLE_ANSWER`. */
export const SAVE_FAILED =
  "Not saved as far as this screen can tell — the server failed while writing. " +
  "Reload the catalogue to see what it holds now.";

/**
 * The deadline expired (ADR-0009, point 2). Not a failure and not a success: **unresolved**.
 *
 * There is no idempotency key in this contract, so the client cannot tell "it never arrived" from
 * "it committed and the answer was lost", and it must not guess. The sentence points at the one
 * action that settles it — reading the catalogue — and warns about what a second attempt looks like
 * from the database's side, because the database will refuse the repeat as a `409` about a row this
 * very person just created, and that is a confusing thing to discover unaided.
 */
export const SAVE_UNRESOLVED =
  "Unresolved — the server did not answer in time, so this change may or may not have been saved. " +
  "Reload the catalogue before trying again; a repeat of a change that did go through comes back " +
  "as a conflict.";

/**
 * The second unresolved ending (Reviewer R-03): the server answered `2xx`, and the body was not
 * something this client could read as the contract (`api/client.ts`, `write`).
 *
 * Unresolved, not failed, and the status code is the whole argument. A `2xx` is the server saying
 * it committed the write; a body this client cannot parse says only that the two sides disagree
 * about the shape of the answer — which is what a rolling deploy looks like from the browser when
 * the frontend is a version ahead of the backend, and the new `updated_at` is simply missing from
 * the response. Calling that "the server failed while writing" asserts a failure nothing reported
 * and invites a retry — into a `409` about the row this very person just wrote, or into a duplicate
 * where no constraint catches one.
 *
 * A sentence of its own rather than `SAVE_UNRESOLVED` verbatim, deliberately: that one says the
 * server did not answer in time, and here it answered promptly. Reusing it would assert a cause the
 * response did not report — the rule at the top of this module, and the defect R-01 named one
 * constant up. The *kind* ("Unresolved") and the guidance (re-read, expect a conflict on a repeat)
 * are shared, because the person's next action is the same in both.
 */
export const SAVE_UNRESOLVED_UNREADABLE_ANSWER =
  "Unresolved — the server reported success but answered in a form this screen could not read, so " +
  "this change may or may not have been saved. Reload the catalogue before trying again; a repeat " +
  "of a change that did go through comes back as a conflict.";

/** The server's field names, in the words this form uses for them. A field the server names and
 * this map does not is passed through verbatim — a name nobody recognises beats a name nobody
 * sent. */
const FIELD_LABELS: Readonly<Record<string, string>> = {
  name: "name",
  role_id: "role",
  seniority_id: "seniority",
  location_id: "location",
  engagement_type_id: "engagement type",
  vendor_id: "vendor",
  default_cost_rate: "default cost rate",
  default_selling_rate: "default selling rate",
  currency: "currency",
  unit: "unit",
  effective_from: "effective from",
  effective_to: "effective to",
  updated_at: "concurrency marker",
};

/**
 * Turns whatever a write rejected with into the one sentence the form shows.
 *
 * The status decides first and the body decides second, and only for `409` — which is the whole
 * point: the two `409`s of the catalogue's edit path are one status code and two different answers,
 * told apart by the identifier the backend puts in the message (`condition=updated_at_marker`
 * versus a SQLSTATE and a constraint name), never by anything this screen knows about the data.
 *
 * The one `ApiError` that is not a refusal is separated out first (Reviewer R-03): `write` in
 * `api/client.ts` raises `ApiError` for a body that does not match the contract *and keeps the real
 * status*, so a `2xx` arriving here means the server committed and this client could not read the
 * answer. That is an unresolved outcome, not a failed one — routing it to `SAVE_FAILED` through the
 * `default` branch would tell somebody a stored change was not written.
 */
export function describeWriteFailure(error: unknown): string {
  if (error instanceof RequestTimeoutError) {
    return SAVE_UNRESOLVED;
  }
  if (!(error instanceof ApiError)) {
    return SAVE_FAILED;
  }
  if (error.status >= 200 && error.status < 300) {
    return SAVE_UNRESOLVED_UNREADABLE_ANSWER;
  }
  switch (error.status) {
    case 401:
    case 403:
      return SAVE_DENIED;
    case 404:
      return SAVE_REFUSED_MISSING_REFERENCE;
    case 409:
      return CONFLICT_MESSAGES[refusalCauseOf(error.detail)];
    case 422:
      return error.fields !== undefined && error.fields.length > 0
        ? saveInvalidFields(error.fields)
        : SAVE_INVALID;
    default:
      return SAVE_FAILED;
  }
}

/** One message per cause the backend's closed list can report, plus the unstated one. Exhaustive
 * over `RefusalCause`, so a cause added to the contract fails the build here rather than quietly
 * falling into a default branch that would name the wrong rule. */
const CONFLICT_MESSAGES: Readonly<Record<ReturnType<typeof refusalCauseOf>, string>> = {
  "stale-marker": SAVE_REFUSED_STALE_MARKER,
  overlap: SAVE_REFUSED_OVERLAP,
  "duplicate-value": SAVE_REFUSED_DUPLICATE,
  "missing-reference": SAVE_REFUSED_MISSING_REFERENCE,
  "broken-rule": SAVE_REFUSED_BROKEN_RULE,
  unstated: SAVE_REFUSED_UNSTATED,
};

// --- Shape checks the form does before sending anything -----------------------------------------
// ADR-0009, point 4 draws the line: the client checks the *shape* of what was typed (a required
// field is filled, a change was actually made) and never the *state* of the data (whether a window
// overlaps, whether a name is taken, whether a marker is current). The second kind is check-then-act
// against a paginated list this client does not even hold.

/** A required field was left empty. `field` is the label beside the input, so the sentence names
 * the control the person is looking at. */
export function missingValueMessage(field: string): string {
  return `Not saved — ${field} is required.`;
}

/** An edit form submitted with nothing changed. The backend refuses such a body (`422`: "An edit
 * must name at least one field to change"), and for a reason worth not provoking — an empty edit
 * still moves the concurrency marker and invalidates every other client's copy of it. */
export const NOTHING_CHANGED = "Not saved — nothing in this form was changed.";

// --- What the screen says after a save that did go through --------------------------------------

/**
 * The write was accepted and the read that replaces the screen's data has not answered yet
 * (Reviewer R-02).
 *
 * Deliberately says nothing about what the catalogue now holds — that is the read's answer, and it
 * has not arrived. It exists because the controls that open a form are disabled for the length of
 * this window: a control disabled for a reason nobody stated reads as a broken screen, and the
 * window is not always short (six reads, one of them the paginated rate list).
 *
 * It does not say "Saved" either, and that is not squeamishness: the two sentences below are the
 * screen's statements *about the save*, and both of them are only true once the read has settled
 * one way or the other. A third "Saved…" here would be a success announced twice, with the second
 * one liable to be read as the first one repeating.
 */
export const RE_READING_AFTER_SAVE =
  "The change was accepted. Re-reading the catalogue…";

/**
 * Saved, and the list below is the server's answer to a fresh read (gate-1 decision P-3a).
 *
 * It names the consequence of that decision in the same breath, because the consequence is visible
 * and confusing: `GET /catalog/rates` is paginated and ordered by `effective_from DESC`, so a window
 * with an earlier start date can be saved successfully and still not appear (Issue #49, criterion 6;
 * ADR-0008 addendum 2026-09-21, point 6). "Saved, and I cannot see it" must be a sentence the screen
 * said, not a conclusion the reader draws from a table that did not change.
 */
export const SAVED_AND_REREAD =
  "Saved. The catalogue below was re-read from the server; a rate window starting earlier than " +
  "the ones shown may be on a page this screen does not display.";

/**
 * G-6 (gate-1 decision, criterion K-19): the write succeeded and the read that was supposed to show
 * its result did not.
 *
 * A state of its own, and the reason is the whole point. The screen's existing failure state says
 * "The catalogue could not be loaded" and blanks everything; reaching it here would report a
 * successful save as a failed one, and the person would retry a change that is already stored — into
 * a `409` about the row they just wrote. So: the save is stated as done, the rows already on screen
 * stay, and they are stated to be older than the change.
 */
export const SAVED_BUT_NOT_REREAD =
  "Saved, but the catalogue could not be re-read afterwards. The rows below are from before this " +
  "change; reload the screen to see the catalogue as it is now.";
