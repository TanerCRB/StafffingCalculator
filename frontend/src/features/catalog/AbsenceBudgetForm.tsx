import { type FormEvent, useId, useState } from "react";

import { createAbsenceBudget } from "../../api/client";
import type { DimensionEntry, WorkingCalendarEntry } from "../../api/contracts/catalog";
import { DateInput, Field, FormShell, TextInput } from "./CatalogFormShell";
import { emptyDictionaryLabel } from "./dimensionLabels";
import { describeWriteFailure, missingValueMessage } from "./writeOutcome";

/**
 * Adding one leave-budget window (`POST /catalog/absence-budgets`, SC-3-03; criteria K-04..K-06 of
 * Issue #140). Mirrors the "add dictionary entry" shape `DimensionEntryForm`/`RateForm` already
 * established for this catalogue (gate-1 decision, Q-1 = Option B) — add only, no edit: there is no
 * `PATCH` for a budget to seed a correction form from (Issue #140, "Out of scope").
 *
 * Three properties are criteria rather than styling:
 *
 *   * **One submit, one `POST`, and what is on screen afterwards is a fresh read's answer** (K-04),
 *     never this form's own values and never the `201` body — `onSaved` is the screen's re-read
 *     (mirrors `DimensionEntryForm`'s `onSaved`, gate-1 decision P-3a).
 *   * **Nothing here checks the window a client cannot check honestly** (K-05, ADR-0009 point 4):
 *     alignment to whole months and overlap against other budgets are refused by the database inside
 *     the `INSERT` — ADR-0008's addendum SC-3-03, point 10 — and this form sends whatever was typed
 *     rather than predicting the refusal.
 *   * **The source field is the number's origin, never a person's name** (K-06). There is no
 *     `author`/`entered_by`/`approved_by` control anywhere in this form — not a hidden one, not a
 *     disabled one — because there is no column for one (ADR-0005, addendum 2026-09-22 SC-3-03,
 *     point 6) and `AbsenceBudgetCreateRequest` carries no field to hold it.
 */

const BUDGET_DAYS_HINT =
  "A decimal number of days, up to two decimal places. It is sent exactly as typed.";

/** K-06: "the source of this figure", never "who entered it". No mention of a person anywhere in
 * this label or its hint — see the criterion's own wording in Issue #140. */
const SOURCE_FIELD_LABEL = "Source of this figure";
const SOURCE_FIELD_HINT =
  "Where the number comes from — a policy, a regulation, a rate table (for example \"Staff " +
  "regulations §12, 2026 edition\"). There is no field for a person's name; only the source is " +
  "recorded, and none is asked for.";

const EFFECTIVE_FROM_HINT = "The first day of the first month this budget applies to.";
const EFFECTIVE_TO_HINT = "The last day of the last month this budget applies to, included.";

const NO_CALENDARS_HINT = "No working calendars are configured yet.";

export function AbsenceBudgetForm({
  calendars,
  engagementTypes,
  onSaved,
  onCancel,
}: {
  calendars: WorkingCalendarEntry[];
  engagementTypes: DimensionEntry[];
  onSaved: () => Promise<void> | void;
  onCancel: () => void;
}) {
  const fieldId = useId();
  const id = (field: string) => `${fieldId}-${field}`;

  const [calendarId, setCalendarId] = useState("");
  const [engagementTypeId, setEngagementTypeId] = useState("");
  const [budgetDays, setBudgetDays] = useState("");
  const [source, setSource] = useState("");
  const [effectiveFrom, setEffectiveFrom] = useState("");
  const [effectiveTo, setEffectiveTo] = useState("");

  const [failure, setFailure] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  function firstMissingValue(): string | null {
    if (calendarId === "") return "Calendar";
    if (engagementTypeId === "") return "Engagement type";
    if (budgetDays.trim() === "") return "Budget (days)";
    if (source.trim() === "") return SOURCE_FIELD_LABEL;
    if (effectiveFrom === "") return "Effective from";
    if (effectiveTo === "") return "Effective to";
    return null;
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) {
      return;
    }
    const missing = firstMissingValue();
    if (missing !== null) {
      setFailure(missingValueMessage(missing));
      return;
    }

    setBusy(true);
    setFailure(null);
    try {
      await createAbsenceBudget({
        calendar_id: calendarId,
        engagement_type_id: engagementTypeId,
        budget_days: budgetDays.trim(),
        source: source.trim(),
        effective_from: effectiveFrom,
        effective_to: effectiveTo,
      });
    } catch (error) {
      setFailure(describeWriteFailure(error));
      setBusy(false);
      return;
    }
    setBusy(false);
    await onSaved();
  }

  return (
    <FormShell
      title="Add a leave budget"
      submitLabel="Save new budget"
      busy={busy}
      failure={failure}
      onSubmit={submit}
      onCancel={onCancel}
    >
      <Field
        id={id("calendar")}
        label="Calendar"
        hint={calendars.length === 0 ? NO_CALENDARS_HINT : undefined}
      >
        <select
          id={id("calendar")}
          className="input catalog__field-control"
          aria-describedby={calendars.length === 0 ? `${id("calendar")}-hint` : undefined}
          value={calendarId}
          onChange={(event) => setCalendarId(event.target.value)}
        >
          <option value="">Choose a working calendar</option>
          {calendars.map((calendar) => (
            <option key={calendar.id} value={calendar.id}>
              {calendar.name}
            </option>
          ))}
        </select>
      </Field>

      <Field
        id={id("engagement-type")}
        label="Engagement type"
        hint={engagementTypes.length === 0 ? emptyDictionaryLabel("engagement-types") : undefined}
      >
        <select
          id={id("engagement-type")}
          className="input catalog__field-control"
          aria-describedby={
            engagementTypes.length === 0 ? `${id("engagement-type")}-hint` : undefined
          }
          value={engagementTypeId}
          onChange={(event) => setEngagementTypeId(event.target.value)}
        >
          <option value="">Choose from the engagement types dictionary</option>
          {engagementTypes.map((entry) => (
            <option key={entry.id} value={entry.id}>
              {entry.name}
            </option>
          ))}
        </select>
      </Field>

      <Field id={id("budget-days")} label="Budget (days)" hint={BUDGET_DAYS_HINT}>
        <TextInput
          id={id("budget-days")}
          value={budgetDays}
          onChange={setBudgetDays}
          inputMode="decimal"
          hinted
        />
      </Field>

      <Field id={id("source")} label={SOURCE_FIELD_LABEL} hint={SOURCE_FIELD_HINT}>
        <TextInput
          id={id("source")}
          value={source}
          onChange={setSource}
          inputMode="text"
          hinted
        />
      </Field>

      <Field id={id("from")} label="Effective from" hint={EFFECTIVE_FROM_HINT}>
        <DateInput id={id("from")} value={effectiveFrom} onChange={setEffectiveFrom} hinted />
      </Field>

      <Field id={id("to")} label="Effective to" hint={EFFECTIVE_TO_HINT}>
        <DateInput id={id("to")} value={effectiveTo} onChange={setEffectiveTo} hinted />
      </Field>
    </FormShell>
  );
}
