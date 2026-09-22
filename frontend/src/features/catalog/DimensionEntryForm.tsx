import { type FormEvent, useId, useState } from "react";

import { createDimensionEntry, editDimensionEntry } from "../../api/client";
import type { CatalogDimension, DimensionEntry } from "../../api/contracts/catalog";
import { Field, FormShell, TextInput } from "./CatalogFormShell";
import { DIMENSION_LABELS } from "./dimensionLabels";
import {
  NOTHING_CHANGED,
  describeWriteFailure,
  missingValueMessage,
} from "./writeOutcome";

/**
 * Adding an entry to one dimension dictionary, and renaming one (SC-2-04, gate-1 decision P-4:
 * all five dictionaries).
 *
 * **One component for five dictionaries and for both verbs**, mirroring the one pair of endpoints
 * that serves them. SC-2-03 settled the same question a level down — "a vendor is the fifth
 * dictionary, not a fifth mechanism" — and five form components would be five places for the fifth
 * one to be the one that forgets the concurrency marker, with nothing failing to say so. The
 * dimension is a parameter here exactly as it is a path segment there.
 *
 * `entry` is what tells the two verbs apart, and it is not a flag: the edit path needs the row's
 * id and its concurrency marker, so "which mode" and "what to send" are the same fact
 * (ADR-0007, addendum 2026-09-21).
 */
export function DimensionEntryForm({
  dimension,
  entry,
  onSaved,
  onCancel,
}: {
  dimension: CatalogDimension;
  /** Present for a rename; absent for a new entry. */
  entry?: DimensionEntry;
  /** Called after the server accepted the write, and only then. What happens next — re-reading the
   * catalogue — belongs to the screen, because the answer replaces the whole screen's data and not
   * this form's (gate-1 decision P-3a). */
  onSaved: () => Promise<void> | void;
  onCancel: () => void;
}) {
  const labels = DIMENSION_LABELS[dimension];
  const nameFieldId = useId();

  // The edit form starts from the value the *API* sent for this row. There is no rounding or
  // formatting on a dictionary name, so nothing is lost here — but the rule is the same one the
  // rate form follows for an amount (ADR-0008, addendum 2026-09-19, point 2), and it is written the
  // same way on purpose: the source is the response, never the rendered list item.
  const [name, setName] = useState(entry?.name ?? "");
  const [failure, setFailure] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) {
      return;
    }
    const value = name.trim();

    // Shape only, never state (ADR-0009, point 4). "Is this field filled" and "did anything change"
    // are facts about the form; "is this name already taken" is a fact about the catalogue, and the
    // unique index on the normalised name is the only thing entitled to answer it.
    if (value === "") {
      setFailure(missingValueMessage(`${labels.column} name`));
      return;
    }
    if (entry !== undefined && value === entry.name) {
      setFailure(NOTHING_CHANGED);
      return;
    }

    setBusy(true);
    setFailure(null);
    try {
      if (entry === undefined) {
        await createDimensionEntry(dimension, { name: value });
      } else {
        // The marker goes back exactly as it arrived. Nothing compares it here: the comparison runs
        // inside the backend's `UPDATE`, and a client-side "is it still current?" would be
        // check-then-act against a row another connection may be writing.
        await editDimensionEntry(dimension, entry.id, {
          updated_at: entry.updated_at,
          name: value,
        });
      }
    } catch (error) {
      // The typed value stays in the field: a refusal is not a reason to make somebody type it
      // again, and this form adds nothing of its own to the server's sentence (ADR-0009, point 6).
      setFailure(describeWriteFailure(error));
      setBusy(false);
      return;
    }
    setBusy(false);
    await onSaved();
  }

  return (
    <FormShell
      title={
        entry === undefined
          ? `Add an entry to the ${labels.inSentence} dictionary`
          : `Rename an entry of the ${labels.inSentence} dictionary`
      }
      submitLabel={entry === undefined ? "Save new entry" : "Save the new name"}
      busy={busy}
      failure={failure}
      onSubmit={submit}
      onCancel={onCancel}
    >
      <Field id={nameFieldId} label={`${labels.column} name`}>
        <TextInput id={nameFieldId} value={name} onChange={setName} inputMode="text" />
      </Field>
    </FormShell>
  );
}
