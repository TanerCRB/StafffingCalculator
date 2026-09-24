import { useEffect, useRef, useState } from "react";

import { duplicateScenario } from "../../api/client";
import type { ScenarioListItem } from "../../api/contracts/projects";
import {
  DUPLICATE_LABEL,
  DUPLICATED,
  DUPLICATING,
  describeDuplicateScenarioFailure,
} from "./duplicateScenarioText";

/**
 * SC-6-03 — a "Duplicate" control on a scenario card, consuming SC-6-01's
 * `POST /projects/{project_id}/scenarios/{scenario_id}/duplicate` (gate 1, Q1 = option A: a button
 * on the existing card, no new router, no global selected-scenario state).
 *
 * Available for a scenario in both `Draft` and `Approved` — deliberately NOT gated by
 * `scenario_status`, unlike `ScenarioCommercialTermsSection`'s "Set Time & Material" (K-02).
 * Duplication never writes to the source (ADR-0004, addendum SC-6-01, point 4: "the source is
 * read-only, never written"), so it is not subject to the approved-immutability hiding rule that
 * applies to a write that touches the source itself.
 *
 * The new row is built solely from the `201` body and handed to `onDuplicated`, which the caller
 * (`ProjectListScreen`) inserts into the project's own scenario array — never by re-fetching
 * `GET /projects` (gate 1, Q2) and never assembled from the source scenario's own client-side state
 * (K-03). `projectId` travels with every call and with the callback, so the row can only ever land
 * in the project this control's own card belongs to (K-06; ADR-0009, addendum 2026-09-24).
 */

type WriteState =
  | { kind: "idle" }
  | { kind: "saving" }
  | { kind: "duplicated" }
  | { kind: "refused"; message: string };

export interface DuplicateScenarioControlProps {
  readonly projectId: string;
  readonly scenarioId: string;
  /** Only for accessible names: several cards on screen carry the same control label. */
  readonly scenarioName: string;
  /** Called with the project this control's card belongs to, and the new row from the `201` body —
   * never assembled from anything read before the click. */
  readonly onDuplicated: (projectId: string, scenario: ScenarioListItem) => void;
}

export function DuplicateScenarioControl({
  projectId,
  scenarioId,
  scenarioName,
  onDuplicated,
}: DuplicateScenarioControlProps) {
  const [write, setWrite] = useState<WriteState>({ kind: "idle" });
  const mounted = useRef(true);
  const outcomeRef = useRef<HTMLParagraphElement>(null);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  useEffect(() => {
    // The control that was pressed stays; the outcome takes the focus so a keyboard user (or
    // anyone not looking at the card) is not left guessing whether the click did anything (same
    // convention as ScenarioCommercialTermsSection).
    if (write.kind === "duplicated" || write.kind === "refused") {
      outcomeRef.current?.focus();
    }
  }, [write.kind]);

  async function handleDuplicate() {
    setWrite({ kind: "saving" });
    try {
      const scenario = await duplicateScenario(projectId, scenarioId);
      if (!mounted.current) {
        return;
      }
      // The `201` body, which passed the same shape check `GET /projects` does, is the only source
      // of the new row — nothing assembled here, no refetch (K-01, K-03).
      onDuplicated(projectId, scenario);
      setWrite({ kind: "duplicated" });
    } catch (error: unknown) {
      if (mounted.current) {
        setWrite({ kind: "refused", message: describeDuplicateScenarioFailure(error) });
      }
    }
  }

  return (
    <div className="duplicate-scenario">
      <div className="commercial-terms__actions">
        <button
          type="button"
          className="button button--secondary"
          aria-label={`${DUPLICATE_LABEL} ${scenarioName}`}
          disabled={write.kind === "saving"}
          onClick={() => void handleDuplicate()}
        >
          {DUPLICATE_LABEL}
        </button>
      </div>
      {write.kind === "saving" && (
        <p role="status" className="scenario-card__metric">
          {DUPLICATING}
        </p>
      )}
      {write.kind === "duplicated" && (
        <p ref={outcomeRef} tabIndex={-1} role="status" className="scenario-card__metric">
          {DUPLICATED}
        </p>
      )}
      {write.kind === "refused" && (
        <p ref={outcomeRef} tabIndex={-1} role="status" className="scenario-card__gaps">
          {write.message}
        </p>
      )}
    </div>
  );
}
