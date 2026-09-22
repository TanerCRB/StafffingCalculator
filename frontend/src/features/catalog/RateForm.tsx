import { type FormEvent, useId, useState } from "react";

import { createCatalogRate, editCatalogRate } from "../../api/client";
import {
  CATALOG_DIMENSIONS,
  RATE_UNIT_HOUR,
  type CatalogRate,
  type CatalogRateEditRequest,
  type CatalogDimension,
  type DimensionEntry,
} from "../../api/contracts/catalog";
import { DateInput, Field, FormShell, StatedValue, TextInput } from "./CatalogFormShell";
import {
  INTERNAL_RATE,
  REQUIRED_DIMENSIONS,
  RESTRICTED_COST_RATE,
  type Dictionaries,
  type RequiredDimension,
  dimensionNameOf,
  nameLookups,
} from "./catalogRows";
import { DIMENSION_LABELS, emptyDictionaryLabel } from "./dimensionLabels";
import {
  NOTHING_CHANGED,
  describeWriteFailure,
  missingValueMessage,
} from "./writeOutcome";

/**
 * Adding a default rate window, and correcting one (SC-2-04).
 *
 * Four properties of this form are criteria rather than styling, and each one is a way the screen
 * could look completely right while being wrong:
 *
 *   * **Amounts are strings from the keystroke to the request body.** No `Number()`, no
 *     `parseFloat`, no `toFixed`, and no rounding of any kind on the way in (ADR-0002, addendum
 *     2026-09-21, points 1-2). The column is `NUMERIC(14,4)`; a value the server cannot keep is a
 *     `422` naming the field, never a value this form quietly shortened.
 *   * **The edit form is seeded from the API response, never from the table.** The cell on screen
 *     reads "150.01 EUR / hour" because `lib/money.ts` rounded it to two places for display; the
 *     stored value may be `150.0050`. Seeding from what is rendered would turn a correction of the
 *     *dates* into a silent rewrite of the *amount* (ADR-0008, addendum 2026-09-19, point 2 — a
 *     commitment made in advance, and this is the task it was made for).
 *   * **A cost rate the response did not carry is never invented and never sent back.** The field
 *     renders as the same `RESTRICTED_COST_RATE` word the table uses, and the `PATCH` body omits the
 *     key entirely — which is the whole reason the backend's edit request is partial (Issue #49,
 *     gate-1 decision Q-2).
 *   * **Nothing here checks what the database checks.** Overlapping windows are refused by
 *     `EXCLUDE` inside the write (ADR-0008); this form does not look for a collision first. It could
 *     not even do so honestly — the rate list is paginated, so the client does not hold the set it
 *     would have to check against (ADR-0009, point 4).
 */

/**
 * The value of the vendor control's first option. Deliberately not a UUID and not the empty string:
 * "this is the organisation's own rate" is a named choice that maps to `vendor_id: null`, and an
 * empty option would read as "any vendor", which is a resolution rule the backend refuses to have
 * (SC-2-03, K-03/K-04).
 */
const INTERNAL_VENDOR_CHOICE = "internal";

const AMOUNT_HINT =
  "A decimal amount, up to four decimal places. It is sent exactly as typed — nothing here rounds " +
  "it.";

const COST_RATE_NOTE =
  "The catalogue returns personnel costs only to callers permitted to read them. If yours is not, " +
  `this value will read "${RESTRICTED_COST_RATE}" after saving — to you as well.`;

const CURRENCY_HINT =
  "An ISO-4217 code in capitals, for example EUR or PLN. The server decides which codes it " +
  "accepts; this form keeps no list of its own.";

const UNIT_NOTE = `Every catalogue rate is priced per ${RATE_UNIT_HOUR}. The unit is not a choice.`;

const EFFECTIVE_FROM_HINT = "The first day this rate applies.";

const EFFECTIVE_TO_HINT =
  "The last day this rate applies, included. Leave it empty for an open-ended window.";

export function RateForm({
  rate,
  dictionaries,
  onSaved,
  onCancel,
}: {
  /** Present for a correction; absent for a new window. */
  rate?: CatalogRate;
  dictionaries: Dictionaries;
  onSaved: () => Promise<void> | void;
  onCancel: () => void;
}) {
  const names = nameLookups(dictionaries);
  const fieldId = useId();
  const id = (field: string) => `${fieldId}-${field}`;

  // --- What identifies the rate. Chosen on the way in, stated on the way back ------------------
  // The four dimension ids and the vendor are not editable once the row exists: they say *which*
  // rate this is, and moving a window onto another tuple is a re-keying nobody has decided
  // (`EDITABLE_RATE_FIELDS` on the backend is an allow-list, and the request model forbids extra
  // keys, so an attempt is a `422` rather than a silent no-op).
  const [tuple, setTuple] = useState<Readonly<Record<RequiredDimension, string>>>({
    roles: "",
    seniorities: "",
    locations: "",
    "engagement-types": "",
  });
  const [vendorChoice, setVendorChoice] = useState<string>(INTERNAL_VENDOR_CHOICE);

  // --- What can change -------------------------------------------------------------------------
  // Every one of these is seeded from the response body (`rate`), never from a rendered cell.
  const costWasSent = rate === undefined || typeof rate.default_cost_rate === "string";
  const [costRate, setCostRate] = useState(rate?.default_cost_rate ?? "");
  const [sellingRate, setSellingRate] = useState(rate?.default_selling_rate ?? "");
  const [currency, setCurrency] = useState(rate?.currency ?? "");
  const [effectiveFrom, setEffectiveFrom] = useState(rate?.effective_from ?? "");
  const [effectiveTo, setEffectiveTo] = useState(rate?.effective_to ?? "");

  const [failure, setFailure] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  function firstMissingValue(): string | null {
    if (rate === undefined) {
      for (const dimension of REQUIRED_DIMENSIONS) {
        if (tuple[dimension] === "") {
          return DIMENSION_LABELS[dimension].column;
        }
      }
    }
    if (costWasSent && costRate.trim() === "") {
      return "Default cost rate";
    }
    if (sellingRate.trim() === "") {
      return "Default selling rate";
    }
    if (currency.trim() === "") {
      return "Currency";
    }
    if (effectiveFrom === "") {
      return "Effective from";
    }
    return null;
  }

  /**
   * The changed fields, and only those (Q-2: an absent field is a field the edit does not touch).
   *
   * The comparison is against the values the *response* carried, which is why seeding from the
   * response matters twice over: it decides what is shown, and it decides what counts as a change.
   * `effective_to` is compared as "null or a date" rather than as a string, because an empty control
   * and an absent field are the same fact — an open-ended window — and the request has to say it
   * the way the API says it.
   */
  function changesFor(existing: CatalogRate): CatalogRateEditRequest {
    const changes: CatalogRateEditRequest = { updated_at: existing.updated_at };
    if (costWasSent && costRate.trim() !== existing.default_cost_rate) {
      changes.default_cost_rate = costRate.trim();
    }
    if (sellingRate.trim() !== existing.default_selling_rate) {
      changes.default_selling_rate = sellingRate.trim();
    }
    if (currency.trim() !== existing.currency) {
      changes.currency = currency.trim();
    }
    if (effectiveFrom !== existing.effective_from) {
      changes.effective_from = effectiveFrom;
    }
    const end = effectiveTo === "" ? null : effectiveTo;
    if (end !== (existing.effective_to ?? null)) {
      changes.effective_to = end;
    }
    return changes;
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

    let request: () => Promise<unknown>;
    if (rate === undefined) {
      request = () =>
        createCatalogRate({
          role_id: tuple.roles,
          seniority_id: tuple.seniorities,
          location_id: tuple.locations,
          engagement_type_id: tuple["engagement-types"],
          // The named choice becomes the API's named state. No sentinel id, ever.
          vendor_id: vendorChoice === INTERNAL_VENDOR_CHOICE ? null : vendorChoice,
          default_cost_rate: costRate.trim(),
          default_selling_rate: sellingRate.trim(),
          currency: currency.trim(),
          effective_from: effectiveFrom,
          // Empty means open-ended, said the way the API says it — never a far-future sentinel, and
          // never shifted by a day (ADR-0008, point 3).
          effective_to: effectiveTo === "" ? null : effectiveTo,
        });
    } else {
      const changes = changesFor(rate);
      if (Object.keys(changes).length === 1) {
        // Only the marker. The backend refuses such a body, and rightly: an edit that changes
        // nothing would still move `updated_at` and invalidate every other client's copy of it.
        setFailure(NOTHING_CHANGED);
        return;
      }
      request = () => editCatalogRate(rate.id, changes);
    }

    setBusy(true);
    setFailure(null);
    try {
      await request();
    } catch (error) {
      setFailure(describeWriteFailure(error));
      setBusy(false);
      return;
    }
    setBusy(false);
    await onSaved();
  }

  const tupleLabel =
    rate === undefined
      ? ""
      : CATALOG_DIMENSIONS.map((dimension) => dimensionNameOf(rate, dimension, names)).join(", ");

  return (
    <FormShell
      title={
        rate === undefined ? "Add a default rate" : `Edit the default rate for ${tupleLabel}`
      }
      submitLabel={rate === undefined ? "Save new rate" : "Save the changes"}
      busy={busy}
      failure={failure}
      onSubmit={submit}
      onCancel={onCancel}
    >
      {rate === undefined ? (
        <>
          {REQUIRED_DIMENSIONS.map((dimension) => (
            <DimensionChoice
              key={dimension}
              id={id(dimension)}
              dimension={dimension}
              entries={dictionaries[dimension]}
              value={tuple[dimension]}
              onChange={(value) => setTuple((current) => ({ ...current, [dimension]: value }))}
            />
          ))}
          <VendorChoice
            id={id("vendors")}
            entries={dictionaries.vendors}
            value={vendorChoice}
            onChange={setVendorChoice}
          />
        </>
      ) : (
        // A correction states which rate it is correcting, in the same words the table uses for the
        // same row — one join, so the two cannot disagree about a vendor or about "Internal".
        CATALOG_DIMENSIONS.map((dimension) => (
          <StatedValue
            key={dimension}
            label={DIMENSION_LABELS[dimension].column}
            value={dimensionNameOf(rate, dimension, names)}
            note={
              dimension === "engagement-types"
                ? "A rate window cannot be moved to another tuple or another vendor; add a new one " +
                  "instead."
                : undefined
            }
          />
        ))
      )}

      {costWasSent ? (
        <Field id={id("cost")} label="Default cost rate" hint={`${AMOUNT_HINT} ${COST_RATE_NOTE}`}>
          <TextInput
            id={id("cost")}
            value={costRate}
            onChange={setCostRate}
            inputMode="decimal"
            hinted
          />
        </Field>
      ) : (
        // The response did not carry this row's cost rate, so this form has no value for it and
        // will not make one up. The `PATCH` body omits the key: the stored cost is left exactly as
        // it is, by the one mechanism designed for this case.
        <StatedValue
          label="Default cost rate"
          value={RESTRICTED_COST_RATE}
          tone="withheld"
          note="This value was not sent to this browser. Saving this form leaves it unchanged."
        />
      )}

      <Field id={id("selling")} label="Default selling rate" hint={AMOUNT_HINT}>
        <TextInput
          id={id("selling")}
          value={sellingRate}
          onChange={setSellingRate}
          inputMode="decimal"
          hinted
        />
      </Field>

      <Field id={id("currency")} label="Currency" hint={CURRENCY_HINT}>
        <TextInput
          id={id("currency")}
          value={currency}
          onChange={setCurrency}
          inputMode="text"
          placeholder="EUR"
          hinted
        />
      </Field>

      <StatedValue label="Unit" value={RATE_UNIT_HOUR} note={UNIT_NOTE} />

      <Field id={id("from")} label="Effective from" hint={EFFECTIVE_FROM_HINT}>
        <DateInput id={id("from")} value={effectiveFrom} onChange={setEffectiveFrom} hinted />
      </Field>

      <Field id={id("to")} label="Effective to" hint={EFFECTIVE_TO_HINT}>
        <DateInput id={id("to")} value={effectiveTo} onChange={setEffectiveTo} hinted />
      </Field>
    </FormShell>
  );
}

/** One of the four dimensions a new rate must name. A `<select>` over the dictionary that was read
 * on mount — never a free-text id, and never a default picked by this form. */
function DimensionChoice({
  id,
  dimension,
  entries,
  value,
  onChange,
}: {
  id: string;
  dimension: CatalogDimension;
  entries: DimensionEntry[];
  value: string;
  onChange: (value: string) => void;
}) {
  const labels = DIMENSION_LABELS[dimension];
  return (
    <Field
      id={id}
      label={labels.column}
      // A dictionary with nothing in it is stated, in the same words its own panel uses. A select
      // holding one unusable option and no explanation is a dead end nobody can read.
      hint={entries.length === 0 ? emptyDictionaryLabel(dimension) : undefined}
    >
      <select
        id={id}
        className="input catalog__field-control"
        aria-describedby={entries.length === 0 ? `${id}-hint` : undefined}
        value={value}
        onChange={(event) => onChange(event.target.value)}
      >
        <option value="">{`Choose from the ${labels.inSentence} dictionary`}</option>
        {entries.map((entry) => (
          <option key={entry.id} value={entry.id}>
            {entry.name}
          </option>
        ))}
      </select>
    </Field>
  );
}

/**
 * Whose rate this is. The first option is the named state "Internal", selected by default, and it
 * is a statement rather than a gap: `vendor_id` absent would mean "any vendor" to nobody, and this
 * control is the reason a person never has to find that out.
 */
function VendorChoice({
  id,
  entries,
  value,
  onChange,
}: {
  id: string;
  entries: DimensionEntry[];
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <Field
      id={id}
      label={DIMENSION_LABELS.vendors.column}
      hint={`"${INTERNAL_RATE}" is the organisation's own rate — a choice, not an empty field.`}
    >
      <select
        id={id}
        className="input catalog__field-control"
        aria-describedby={`${id}-hint`}
        value={value}
        onChange={(event) => onChange(event.target.value)}
      >
        <option value={INTERNAL_VENDOR_CHOICE}>{INTERNAL_RATE}</option>
        {entries.map((entry) => (
          <option key={entry.id} value={entry.id}>
            {entry.name}
          </option>
        ))}
      </select>
    </Field>
  );
}
