import type { FormEvent, ReactNode } from "react";

/**
 * The parts every catalogue form is built from (SC-2-04). Six forms use them — five dictionaries
 * and the rates — in two modes each, which is the reason they are here: a per-form copy of "where
 * the refusal goes" is how one of twelve ends up putting a refusal somewhere a screen reader never
 * announces, and nothing looks broken.
 *
 * Three properties hold for every form built from these parts, and each one is a decision rather
 * than a style:
 *
 *   * **The refusal is announced, not merely painted.** `role="alert"` and an association with the
 *     form, because a save that did not happen is the one thing a person must not have to notice
 *     (NF-05). Colour is an additional channel, never the only one (NF-08).
 *   * **A form in flight cannot be submitted twice.** There is no idempotency key in this contract
 *     (ADR-0009, point 2), so a second click is a second row, not a retry.
 *   * **`disabled`, not `aria-disabled`.** The convention in `lib/notImplemented.ts` is for controls
 *     that are *not built yet* and must stay reachable so the shape of the product is readable
 *     (F-13). A save button that is waiting for the server is built and momentarily unavailable —
 *     the opposite case, and borrowing the other convention would make "not implemented" and
 *     "in flight" look the same.
 */

export const SAVING_LABEL = "Saving…";
export const CANCEL_LABEL = "Cancel";

export function FormShell({
  title,
  submitLabel,
  busy,
  failure,
  onSubmit,
  onCancel,
  children,
}: {
  title: string;
  submitLabel: string;
  busy: boolean;
  /** The one sentence from `writeOutcome.ts` that names how the last attempt ended, or `null`. */
  failure: string | null;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
  onCancel: () => void;
  children: ReactNode;
}) {
  return (
    // `aria-label` gives the element the `form` role with a name, so each of the six forms is
    // addressable as itself. `noValidate`: the browser's own bubble would be a second, invisible
    // validator whose wording nothing in this repository controls and no test can read (NF-07).
    <form className="catalog__form" aria-label={title} onSubmit={onSubmit} noValidate>
      <p className="catalog__form-title">{title}</p>
      <div className="catalog__form-fields">{children}</div>
      {failure !== null && (
        <p role="alert" className="catalog__form-failure">
          {failure}
        </p>
      )}
      {/* Cancel before Save, in the DOM and on screen alike (SC-2-05, gate-1 decision G-9 — the
          one named change of order in that task). Not `flex-direction: row-reverse` over the old
          order: the tab order and the reading order would then disagree with what is seen
          (WCAG 1.3.2, 2.4.3). The pair is pushed to the right by the stylesheet. */}
      <div className="catalog__form-actions">
        <button type="button" className="button button--quiet" onClick={onCancel} disabled={busy}>
          {CANCEL_LABEL}
        </button>
        <button type="submit" className="button button--primary" disabled={busy}>
          {busy ? SAVING_LABEL : submitLabel}
        </button>
      </div>
    </form>
  );
}

/**
 * One labelled control.
 *
 * The label is a real `<label for>`, not a placeholder: a placeholder disappears the moment
 * somebody types, which is exactly when the unit or the format it was explaining is needed
 * (NF-07). `hint` is associated through `aria-describedby`, so "leave empty for an open-ended
 * window" is read out rather than seen only by whoever looks below the box.
 */
export function Field({
  id,
  label,
  hint,
  children,
}: {
  id: string;
  label: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <p className="catalog__field">
      <label className="catalog__field-label" htmlFor={id}>
        {label}
      </label>
      {children}
      {hint !== undefined && (
        <span className="catalog__field-hint" id={`${id}-hint`}>
          {hint}
        </span>
      )}
    </p>
  );
}

/**
 * A free-text control. Money and currency go through it as **strings**, all the way to the request
 * body (ADR-0002, addendum 2026-09-21, point 1).
 *
 * Deliberately `type="text"` with `inputMode="decimal"` for amounts rather than `type="number"`:
 * a number input hands JavaScript a `valueAsNumber` and, worse, lets the browser normalise what was
 * typed — which is a rounding point nobody decided on, in the one direction ADR-0008 point 6
 * forbids. The value this component holds is the characters the person typed, unchanged.
 */
export function TextInput({
  id,
  value,
  onChange,
  inputMode,
  placeholder,
  hinted,
}: {
  id: string;
  value: string;
  onChange: (value: string) => void;
  inputMode?: "decimal" | "text";
  placeholder?: string;
  hinted?: boolean;
}) {
  return (
    <input
      id={id}
      className="input catalog__field-control"
      type="text"
      inputMode={inputMode}
      placeholder={placeholder}
      aria-describedby={hinted === true ? `${id}-hint` : undefined}
      value={value}
      onChange={(event) => onChange(event.target.value)}
    />
  );
}

/**
 * A calendar-date control.
 *
 * `type="date"` produces an ISO-8601 calendar string — the same thing the API carries — so nothing
 * here parses, formats or re-bases a date. `lib/dates.ts` explains at length why a `Date` object is
 * not admissible anywhere near these values: `new Date("2026-01-01")` is UTC midnight, and west of
 * Greenwich it renders as the day before, correctly, with nothing thrown.
 */
export function DateInput({
  id,
  value,
  onChange,
  hinted,
}: {
  id: string;
  value: string;
  onChange: (value: string) => void;
  hinted?: boolean;
}) {
  return (
    <input
      id={id}
      className="input catalog__field-control"
      type="date"
      aria-describedby={hinted === true ? `${id}-hint` : undefined}
      value={value}
      onChange={(event) => onChange(event.target.value)}
    />
  );
}

/**
 * A field the form shows but does not let this caller change, with the reason in words.
 *
 * Two different uses, and it matters that they are visibly the same kind of thing: the dimension
 * tuple of a rate being edited (not editable by anyone — re-keying an existing window onto another
 * role is a decision nobody has taken, and `EDITABLE_RATE_FIELDS` on the backend is a list of what
 * may change rather than "the whole row"), and a cost rate this caller was not sent (not editable
 * by *them*, this time — the value is real and belongs to somebody else's eyes).
 *
 * It is not an input with `disabled` on it: a disabled input implies a value the form is holding
 * and could submit. This holds nothing and submits nothing.
 */
export function StatedValue({
  label,
  value,
  note,
  tone,
}: {
  label: string;
  value: string;
  note?: string;
  /** `"withheld"` paints the value the way the table paints a cost rate the response did not
   * carry, so one word in two places does not become two different-looking statements. */
  tone?: "withheld";
}) {
  return (
    <p className="catalog__field">
      <span className="catalog__field-label">{label}</span>
      <span
        className={
          tone === "withheld"
            ? "catalog__field-stated catalog__restricted"
            : "catalog__field-stated"
        }
      >
        {value}
      </span>
      {note !== undefined && <span className="catalog__field-hint">{note}</span>}
    </p>
  );
}
