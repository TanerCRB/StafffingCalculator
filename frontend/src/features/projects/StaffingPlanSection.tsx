import { useEffect, useId, useState } from "react";

import {
  ApiError,
  RequestTimeoutError,
  getCatalogAbsenceTypes,
  getCatalogDimension,
  getStaffingPositions,
  createStaffingPosition,
  editStaffingPositionDetails,
  createStaffingAllocation,
  editStaffingAllocation,
} from "../../api/client";
import type {
  AbsenceBudgetState,
  CapacityState,
  StaffingAbsence,
  StaffingAllocation,
  StaffingPositionRead,
} from "../../api/contracts/staffing";
import type { ScenarioStatus } from "../../api/contracts/projects";
import { formatEffectivePeriod, formatCalendarMonth } from "../../lib/dates";
import { formatHoursString } from "../../lib/hours";
import {
  ABSENCE_BUDGET_LABEL,
  ABSENCE_BUDGET_STATE_MESSAGES,
  ABSENCES_EMPTY,
  ABSENCES_HEADING,
  ALLOCATIONS_EMPTY,
  AVAILABILITY_HOURS_LABEL,
  BILLABLE_HOURS_LABEL,
  CATALOG_NAME_UNAVAILABLE,
  CATALOG_NAME_UNKNOWN,
  DERIVED_CAPACITY_LABEL,
  DERIVED_CAPACITY_STATE_MESSAGES,
  ENGAGEMENT_TYPE_LABEL,
  HEADCOUNT_LABEL,
  LOCATION_LABEL,
  PERIOD_LABEL,
  PLANNED_ALLOCATION_HOURS_LABEL,
  ROLE_LABEL,
  SENIORITY_LABEL,
  STAFFING_EMPTY,
  STAFFING_FAILED,
  STAFFING_HEADING,
  STAFFING_LOADING,
  STAFFING_REFUSED,
  STAFFING_TIMED_OUT,
  STAFFING_UNREADABLE,
  ADD_STAFFING_POSITION, EDIT_POSITION, EDIT_MONTH, ADD_MONTH, CANCEL_EDIT, SAVE_POSITION, SAVE_MONTH,
  DIMENSIONS_UNAVAILABLE, WRITE_FORBIDDEN, WRITE_CONFLICT, WRITE_INVALID, WRITE_TIMEOUT, WRITE_FAILED,
  STAFFING_FORM_LABELS,
} from "./staffingPlanText";

/**
 * SC-3-04/SC-3-09 — the scenario's staffing plan: positions, their four catalogue dimensions,
 * headcount, period, monthly hours, derived capacity, leave budget and planned absences, with
 * position and allocation writes for draft scenarios, as a third section of the scenario card (beside `ScenarioCommercialTermsSection`/
 * `ScenarioResultsSection`).
 *
 * Staffing reads remain independently gated by STAFFING_READ. Draft writes use the backend's
 * STAFFING_WRITE dependency and return the refreshed position, including its next opaque marker.
 * This client currently has no permission preflight, so a denied write is rendered as a refusal.
 *
 * **Two independent reads, not one** (gate 1, ADR-0005 addendum 2026-09-26 SC-3-04):
 *
 *   * the staffing positions themselves, gated by `STAFFING_READ` — `403` and `404` render as one
 *     merged "unavailable" state (K-05, mirroring `ScenarioResultsSection`'s K-04), distinct from a
 *     `200` naming an empty list;
 *   * the five catalogue identifiers' names, gated by `CATALOG_READ` and *nothing else* — no scope
 *     filter, no relationship to `STAFFING_READ`. A denial of this second read never withholds the
 *     first: headcount, hours, capacity and budget states still render normally, and each of the
 *     five identifiers shows its own distinguishable "Name unavailable" instead of the raw UUID or a
 *     dropped row (K-04).
 *
 * **The pseudonymization risk this section opens is accepted, not mitigated, by this task** (ADR-0005,
 * addendum 2026-09-26 SC-3-04): a position's catalogue dimension tuple is rendered beside its
 * absences exactly as the API sends them, with no aggregation and no additional gate beyond
 * `STAFFING_READ`.
 */

// --- Reading the position list -------------------------------------------------------------------

type StaffingReadState =
  | { kind: "loading" }
  | { kind: "ready"; positions: StaffingPositionRead[] }
  | { kind: "unavailable" }
  | { kind: "timed-out" }
  | { kind: "unreadable" }
  | { kind: "failed" };

function toStaffingReadFailure(error: unknown): StaffingReadState {
  if (error instanceof RequestTimeoutError) {
    return { kind: "timed-out" };
  }
  if (error instanceof ApiError) {
    if (error.status === 401 || error.status === 403 || error.status === 404) {
      // One rendered state for both (K-05) — `ApiError.status` still carries the real number for
      // anything that needs it later; the merge happens here, not upstream.
      return { kind: "unavailable" };
    }
    if (error.status >= 200 && error.status < 300) {
      // `getStaffingPositions` keeps the real status on a shape failure: a 2xx here means the
      // server answered and the answer is not the contract (K-06).
      return { kind: "unreadable" };
    }
  }
  return { kind: "failed" };
}

const STAFFING_READ_FAILURE_MESSAGES: Readonly<
  Record<Exclude<StaffingReadState["kind"], "loading" | "ready">, string>
> = {
  unavailable: STAFFING_REFUSED,
  "timed-out": STAFFING_TIMED_OUT,
  unreadable: STAFFING_UNREADABLE,
  failed: STAFFING_FAILED,
};

// --- Resolving the five catalogue identifiers (K-04) ---------------------------------------------

interface CatalogNames {
  readonly roles: ReadonlyMap<string, string>;
  readonly seniorities: ReadonlyMap<string, string>;
  readonly locations: ReadonlyMap<string, string>;
  readonly engagementTypes: ReadonlyMap<string, string>;
  readonly absenceTypes: ReadonlyMap<string, string>;
}

type CatalogReadState = { kind: "loading" } | { kind: "ready"; names: CatalogNames } | { kind: "unavailable" };

function namesById(entries: readonly { readonly id: string; readonly name: string }[]): Map<string, string> {
  return new Map(entries.map((entry) => [entry.id, entry.name]));
}

/**
 * The five name-resolving reads, as one outcome (`Promise.all`, mirroring `CatalogScreen.tsx`'s
 * `readCatalogue`): they are issued together, and any one of them failing is read as the single
 * merged "unavailable" state (gate 1, Q6) — this section does not distinguish which of the five, or
 * why, because K-04's own criterion asks for one state, not five.
 */
async function readCatalogNames(signal: AbortSignal): Promise<CatalogNames> {
  const [roles, seniorities, locations, engagementTypes, absenceTypes] = await Promise.all([
    getCatalogDimension("roles", signal),
    getCatalogDimension("seniorities", signal),
    getCatalogDimension("locations", signal),
    getCatalogDimension("engagement-types", signal),
    getCatalogAbsenceTypes(signal),
  ]);
  return {
    roles: namesById(roles.entries),
    seniorities: namesById(seniorities.entries),
    locations: namesById(locations.entries),
    engagementTypes: namesById(engagementTypes.entries),
    absenceTypes: namesById(absenceTypes.absence_types),
  };
}

type CatalogDimensionKey = keyof CatalogNames;

const NAME_LOADING_TEXT: Readonly<Record<CatalogDimensionKey, string>> = {
  roles: "Loading role…",
  seniorities: "Loading seniority…",
  locations: "Loading location…",
  engagementTypes: "Loading engagement type…",
  absenceTypes: "Loading absence type…",
};

interface ResolvedName {
  readonly text: string;
  readonly state: "loading" | "resolved" | "unknown" | "unavailable";
}

/** What one catalogue identifier reads as, given the catalogue read's own state (K-04). Never the
 * raw UUID, in any of the four branches. */
function catalogName(catalog: CatalogReadState, dimension: CatalogDimensionKey, id: string): ResolvedName {
  if (catalog.kind === "loading") {
    return { text: NAME_LOADING_TEXT[dimension], state: "loading" };
  }
  if (catalog.kind === "unavailable") {
    return { text: CATALOG_NAME_UNAVAILABLE, state: "unavailable" };
  }
  const name = catalog.names[dimension].get(id);
  return name === undefined
    ? { text: CATALOG_NAME_UNKNOWN, state: "unknown" }
    : { text: name, state: "resolved" };
}

// --- The section ----------------------------------------------------------------------------------

export interface StaffingPlanSectionProps {
  readonly projectId: string;
  readonly scenarioId: string;
  readonly scenarioStatus: ScenarioStatus;
}

/**
 * `aria-labelledby={headingId}` gives each mounted instance its own accessible name, and the
 * section's position-specific controls use labels inside the card.
 */
export function StaffingPlanSection({ projectId, scenarioId, scenarioStatus }: StaffingPlanSectionProps) {
  const headingId = useId();
  const [staffing, setStaffing] = useState<StaffingReadState>({ kind: "loading" });
  const [catalog, setCatalog] = useState<CatalogReadState>({ kind: "loading" });
  const canEdit = scenarioStatus !== "Approved";

  function acceptPosition(updated: StaffingPositionRead) {
    setStaffing((previous) => previous.kind === "ready"
      ? { kind: "ready", positions: previous.positions.some((item) => item.id === updated.id)
        ? previous.positions.map((item) => item.id === updated.id ? updated : item)
        : [...previous.positions, updated] }
      : previous);
  }

  useEffect(() => {
    const controller = new AbortController();
    let left = false;
    getStaffingPositions(projectId, scenarioId, controller.signal)
      .then((list) => {
        if (!left) {
          setStaffing({ kind: "ready", positions: list.positions });
        }
      })
      .catch((error: unknown) => {
        if (!left) {
          setStaffing(toStaffingReadFailure(error));
        }
      });
    return () => {
      left = true;
      controller.abort();
    };
  }, [projectId, scenarioId]);

  useEffect(() => {
    // Independent of the effect above: this read is gated by `CATALOG_READ` alone, with no
    // relationship to `STAFFING_READ` or to this scenario's scope (gate 1). Its own
    // `AbortController`, so leaving the card ends this read too (ADR-0010, point 7).
    const controller = new AbortController();
    let left = false;
    readCatalogNames(controller.signal)
      .then((names) => {
        if (!left) {
          setCatalog({ kind: "ready", names });
        }
      })
      .catch(() => {
        if (!left) {
          setCatalog({ kind: "unavailable" });
        }
      });
    return () => {
      left = true;
      controller.abort();
    };
  }, []);

  let body;
  if (staffing.kind === "loading") {
    body = <p className="scenario-card__metric">{STAFFING_LOADING}</p>;
  } else if (staffing.kind !== "ready") {
    body = (
      <p role="status" className="scenario-card__gaps" data-staffing-read-failure={staffing.kind}>
        {STAFFING_READ_FAILURE_MESSAGES[staffing.kind]}
      </p>
    );
  } else if (staffing.positions.length === 0) {
    body = (
      <>
        <p className="scenario-card__gaps" data-staffing-state="empty">{STAFFING_EMPTY}</p>
        {canEdit && <NewPositionForm projectId={projectId} scenarioId={scenarioId} catalog={catalog} onSaved={acceptPosition} />}
      </>
    );
  } else {
    body = (
      <>
        {canEdit && <NewPositionForm projectId={projectId} scenarioId={scenarioId} catalog={catalog} onSaved={acceptPosition} />}
        <ul className="staffing-plan__positions">
          {staffing.positions.map((position) => (
            <StaffingPositionCard key={position.id} position={position} catalog={catalog} canEdit={canEdit}
              projectId={projectId} scenarioId={scenarioId} onSaved={acceptPosition} />
          ))}
        </ul>
      </>
    );
  }

  return (
    <section className="staffing-plan" aria-labelledby={headingId}>
      <h4 id={headingId} className="staffing-plan__title">
        {STAFFING_HEADING}
      </h4>
      {body}
    </section>
  );
}

function CatalogDimensionLine({
  label,
  dimension,
  id,
  catalog,
}: {
  label: string;
  dimension: CatalogDimensionKey;
  id: string;
  catalog: CatalogReadState;
}) {
  const resolved = catalogName(catalog, dimension, id);
  return (
    <p className="scenario-card__metric" data-name-state={resolved.state}>
      {label} {resolved.text}
    </p>
  );
}

function StaffingPositionCard({
  position,
  catalog,
  canEdit,
  projectId,
  scenarioId,
  onSaved,
}: {
  position: StaffingPositionRead;
  catalog: CatalogReadState;
  canEdit: boolean;
  projectId: string;
  scenarioId: string;
  onSaved: (position: StaffingPositionRead) => void;
}) {
  const [editing, setEditing] = useState(false);
  return (
    <li className="staffing-plan__position">
      {canEdit && <button type="button" className="button button--secondary" onClick={() => setEditing((value) => !value)}>
        {editing ? CANCEL_EDIT : EDIT_POSITION}
      </button>}
      {editing && <PositionEditForm projectId={projectId} scenarioId={scenarioId} position={position} catalog={catalog}
        onSaved={(saved) => { onSaved(saved); setEditing(false); }} />}
      <CatalogDimensionLine label={ROLE_LABEL} dimension="roles" id={position.role_id} catalog={catalog} />
      <CatalogDimensionLine
        label={SENIORITY_LABEL}
        dimension="seniorities"
        id={position.seniority_id}
        catalog={catalog}
      />
      <CatalogDimensionLine
        label={LOCATION_LABEL}
        dimension="locations"
        id={position.location_id}
        catalog={catalog}
      />
      <CatalogDimensionLine
        label={ENGAGEMENT_TYPE_LABEL}
        dimension="engagementTypes"
        id={position.engagement_type_id}
        catalog={catalog}
      />
      <p className="scenario-card__metric">
        {HEADCOUNT_LABEL} {position.headcount}
      </p>
      <p className="scenario-card__metric">
        {PERIOD_LABEL} {formatEffectivePeriod(position.start_date, position.end_date)}
      </p>
      {position.allocations.length === 0 ? (
        <p className="scenario-card__gaps" data-allocations-state="empty">
          {ALLOCATIONS_EMPTY}
        </p>
      ) : (
        <ul className="staffing-plan__allocations">
          {position.allocations.map((allocation) => (
            <AllocationLine key={allocation.id} allocation={allocation} position={position} canEdit={canEdit}
              projectId={projectId} scenarioId={scenarioId} onSaved={onSaved} />
          ))}
        </ul>
      )}
      {canEdit && <NewAllocationForm projectId={projectId} scenarioId={scenarioId} position={position} onSaved={onSaved} />}
      <p className="staffing-plan__absences-heading">{ABSENCES_HEADING}</p>
      {position.absences.length === 0 ? (
        <p className="scenario-card__gaps" data-absences-state="empty">
          {ABSENCES_EMPTY}
        </p>
      ) : (
        <ul className="staffing-plan__absences">
          {position.absences.map((absence) => (
            <AbsenceLine key={absence.id} absence={absence} catalog={catalog} />
          ))}
        </ul>
      )}
    </li>
  );
}

function AllocationLine({ allocation, position, canEdit, projectId, scenarioId, onSaved }: {
  allocation: StaffingAllocation; position: StaffingPositionRead; canEdit: boolean;
  projectId: string; scenarioId: string; onSaved: (position: StaffingPositionRead) => void;
}) {
  const [editing, setEditing] = useState(false);
  return (
    <li className="staffing-plan__allocation">
      <p className="staffing-plan__allocation-month">{formatCalendarMonth(allocation.period_month)}</p>
      {canEdit && <button type="button" className="button button--secondary" onClick={() => setEditing((value) => !value)}>
        {editing ? CANCEL_EDIT : EDIT_MONTH}
      </button>}
      {editing && <AllocationEditForm projectId={projectId} scenarioId={scenarioId} position={position}
        allocation={allocation} onSaved={(saved) => { onSaved(saved); setEditing(false); }} />}
      <p className="scenario-card__metric">
        {AVAILABILITY_HOURS_LABEL} {formatHoursString(allocation.availability_hours)}
      </p>
      <p className="scenario-card__metric">
        {PLANNED_ALLOCATION_HOURS_LABEL} {formatHoursString(allocation.planned_allocation_hours)}
      </p>
      <p className="scenario-card__metric">
        {BILLABLE_HOURS_LABEL} {formatHoursString(allocation.billable_hours)}
      </p>
      <DerivedCapacityLine allocation={allocation} />
      <AbsenceBudgetLine allocation={allocation} />
    </li>
  );
}

function StaffingWriteFeedback({ error }: { error: unknown }) {
  const message = error instanceof ApiError && error.status === 403
    ? WRITE_FORBIDDEN
    : error instanceof ApiError && error.status === 409
      ? WRITE_CONFLICT
      : error instanceof ApiError && error.status === 422
        ? WRITE_INVALID
        : error instanceof RequestTimeoutError
          ? WRITE_TIMEOUT
          : WRITE_FAILED;
  return <p role="alert" className="scenario-card__gaps">{message}</p>;
}

function NewPositionForm({ projectId, scenarioId, catalog, onSaved }: {
  projectId: string; scenarioId: string; catalog: CatalogReadState;
  onSaved: (position: StaffingPositionRead) => void;
}) {
  const [open, setOpen] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [saving, setSaving] = useState(false);
  if (catalog.kind !== "ready") return null;
  const first = (key: CatalogDimensionKey) => [...catalog.names[key].keys()][0] ?? "";
  return <div className="staffing-plan__editor">
    {!open ? <button type="button" className="button button--primary" onClick={() => setOpen(true)}>{ADD_STAFFING_POSITION}</button> :
      <form onSubmit={(event) => {
        event.preventDefault(); const data = new FormData(event.currentTarget); setSaving(true); setError(null);
        void createStaffingPosition(projectId, scenarioId, {
          role_id: String(data.get("role_id")), seniority_id: String(data.get("seniority_id")),
          location_id: String(data.get("location_id")), engagement_type_id: String(data.get("engagement_type_id")),
          headcount: Number(data.get("headcount")), start_date: String(data.get("start_date")),
          end_date: String(data.get("end_date")) || null, allocations: [],
        }).then(onSaved).then(() => setOpen(false)).catch(setError).finally(() => setSaving(false));
      }}>
        <DimensionSelect name="role_id" label={STAFFING_FORM_LABELS.role} values={catalog.names.roles} initial={first("roles")} />
        <DimensionSelect name="seniority_id" label={STAFFING_FORM_LABELS.seniority} values={catalog.names.seniorities} initial={first("seniorities")} />
        <DimensionSelect name="location_id" label={STAFFING_FORM_LABELS.location} values={catalog.names.locations} initial={first("locations")} />
        <DimensionSelect name="engagement_type_id" label={STAFFING_FORM_LABELS.engagementType} values={catalog.names.engagementTypes} initial={first("engagementTypes")} />
        <label>{STAFFING_FORM_LABELS.headcount}<input name="headcount" type="number" min="1" max="10000" required defaultValue="1" /></label>
        <label>{STAFFING_FORM_LABELS.startDate}<input name="start_date" type="date" required /></label>
        <label>{STAFFING_FORM_LABELS.endDate}<input name="end_date" type="date" /></label>
        <button className="button button--primary" disabled={saving}>{SAVE_POSITION}</button>
        {error !== null && <StaffingWriteFeedback error={error} />}
      </form>}
  </div>;
}

function DimensionSelect({ name, label, values, initial }: {
  name: string; label: string; values: ReadonlyMap<string, string>; initial: string;
}) {
  return <label>{label}<select name={name} required defaultValue={initial}>
    {!values.has(initial) && <option value={initial}>{CATALOG_NAME_UNKNOWN}</option>}
    {[...values].map(([id, text]) => <option key={id} value={id}>{text}</option>)}
  </select></label>;
}

function PositionEditForm({ projectId, scenarioId, position, catalog, onSaved }: {
  projectId: string; scenarioId: string; position: StaffingPositionRead; catalog: CatalogReadState;
  onSaved: (position: StaffingPositionRead) => void;
}) {
  const [error, setError] = useState<unknown>(null); const [saving, setSaving] = useState(false);
  if (catalog.kind !== "ready") return <p role="status">{DIMENSIONS_UNAVAILABLE}</p>;
  return <form onSubmit={(event) => {
    event.preventDefault(); const data = new FormData(event.currentTarget); setSaving(true); setError(null);
    void editStaffingPositionDetails(projectId, scenarioId, position.id, {
      updated_at: position.updated_at, role_id: String(data.get("role_id")), seniority_id: String(data.get("seniority_id")),
      location_id: String(data.get("location_id")), engagement_type_id: String(data.get("engagement_type_id")),
      headcount: Number(data.get("headcount")), start_date: String(data.get("start_date")), end_date: String(data.get("end_date")) || null,
    }).then(onSaved).catch(setError).finally(() => setSaving(false));
  }}>
    <DimensionSelect name="role_id" label={STAFFING_FORM_LABELS.role} values={catalog.names.roles} initial={position.role_id} />
    <DimensionSelect name="seniority_id" label={STAFFING_FORM_LABELS.seniority} values={catalog.names.seniorities} initial={position.seniority_id} />
    <DimensionSelect name="location_id" label={STAFFING_FORM_LABELS.location} values={catalog.names.locations} initial={position.location_id} />
    <DimensionSelect name="engagement_type_id" label={STAFFING_FORM_LABELS.engagementType} values={catalog.names.engagementTypes} initial={position.engagement_type_id} />
    <label>{STAFFING_FORM_LABELS.headcount}<input name="headcount" type="number" min="1" max="10000" required defaultValue={position.headcount} /></label>
    <label>{STAFFING_FORM_LABELS.startDate}<input name="start_date" type="date" required defaultValue={position.start_date} /></label>
    <label>{STAFFING_FORM_LABELS.endDate}<input name="end_date" type="date" defaultValue={position.end_date ?? ""} /></label>
        <button className="button button--primary" disabled={saving}>{SAVE_POSITION}</button>{error !== null && <StaffingWriteFeedback error={error} />}
  </form>;
}

function AllocationFields({ position, allocation }: { position: StaffingPositionRead; allocation?: StaffingAllocation }) {
  return <>
    {!allocation && <label>{STAFFING_FORM_LABELS.month}<input name="period_month" type="month" required min={position.start_date.slice(0, 7)} max={position.end_date?.slice(0, 7)} /></label>}
    <label>{STAFFING_FORM_LABELS.availabilityHours}<input name="availability_hours" type="number" min="0" step="0.01" required defaultValue={allocation?.availability_hours} /></label>
    <label>{STAFFING_FORM_LABELS.plannedAllocationHours}<input name="planned_allocation_hours" type="number" min="0" step="0.01" required defaultValue={allocation?.planned_allocation_hours} /></label>
    <label>{STAFFING_FORM_LABELS.billableHours}<input name="billable_hours" type="number" min="0" step="0.01" required defaultValue={allocation?.billable_hours} /></label>
  </>;
}

function NewAllocationForm({ projectId, scenarioId, position, onSaved }: {
  projectId: string; scenarioId: string; position: StaffingPositionRead; onSaved: (position: StaffingPositionRead) => void;
}) {
  const [open, setOpen] = useState(false); const [error, setError] = useState<unknown>(null); const [saving, setSaving] = useState(false);
  return <div className="staffing-plan__editor">{!open ? <button type="button" className="button button--secondary" onClick={() => setOpen(true)}>{ADD_MONTH}</button> :
    <form onSubmit={(event) => {
      event.preventDefault(); const data = new FormData(event.currentTarget); setSaving(true); setError(null);
      const month = String(data.get("period_month"));
      void createStaffingAllocation(projectId, scenarioId, position.id, { updated_at: position.updated_at,
        period_month: `${month}-01`, availability_hours: String(data.get("availability_hours")),
        planned_allocation_hours: String(data.get("planned_allocation_hours")), billable_hours: String(data.get("billable_hours")),
      }).then(onSaved).then(() => setOpen(false)).catch(setError).finally(() => setSaving(false));
    }}><AllocationFields position={position} /><button className="button button--primary" disabled={saving}>{SAVE_MONTH}</button>
      {error !== null && <StaffingWriteFeedback error={error} />}</form>}</div>;
}

function AllocationEditForm({ projectId, scenarioId, position, allocation, onSaved }: {
  projectId: string; scenarioId: string; position: StaffingPositionRead; allocation: StaffingAllocation;
  onSaved: (position: StaffingPositionRead) => void;
}) {
  const [error, setError] = useState<unknown>(null); const [saving, setSaving] = useState(false);
  return <form onSubmit={(event) => {
    event.preventDefault(); const data = new FormData(event.currentTarget); setSaving(true); setError(null);
    void editStaffingAllocation(projectId, scenarioId, position.id, allocation.period_month, {
      updated_at: position.updated_at, availability_hours: String(data.get("availability_hours")),
      planned_allocation_hours: String(data.get("planned_allocation_hours")), billable_hours: String(data.get("billable_hours")),
    }).then(onSaved).catch(setError).finally(() => setSaving(false));
  }}><AllocationFields position={position} allocation={allocation} /><button className="button button--primary" disabled={saving}>{SAVE_MONTH}</button>
    {error !== null && <StaffingWriteFeedback error={error} />}</form>;
}

/**
 * `derived_capacity_state = "resolved"` renders `derived_capacity_hours` as a number;
 * `"no_calendar"` renders its own named state — never `0`, never blank, never the cell dropped
 * (K-02). The branch is on `state`, never on the shape of `derived_capacity_hours` itself — the
 * discipline `formatHoursString` relies on its caller to keep (see that function's own docstring).
 */
function DerivedCapacityLine({ allocation }: { allocation: StaffingAllocation }) {
  const state: CapacityState = allocation.derived_capacity_state;
  if (state === "resolved") {
    return (
      <p className="scenario-card__metric" data-derived-capacity-state="resolved">
        {DERIVED_CAPACITY_LABEL} {formatHoursString(allocation.derived_capacity_hours)}
      </p>
    );
  }
  return (
    <p className="scenario-card__gaps" data-derived-capacity-state={state}>
      {DERIVED_CAPACITY_STATE_MESSAGES[state]}
    </p>
  );
}

/**
 * Each of `absence_budget_state`'s four named states gets its own message; none reads as a silent
 * `0.00` and none reuses another state's wording (K-03). `resolved` alone renders
 * `absence_budget_hours` as a number.
 */
function AbsenceBudgetLine({ allocation }: { allocation: StaffingAllocation }) {
  const state: AbsenceBudgetState = allocation.absence_budget_state;
  if (state === "resolved") {
    return (
      <p className="scenario-card__metric" data-absence-budget-state="resolved">
        {ABSENCE_BUDGET_LABEL} {formatHoursString(allocation.absence_budget_hours)}
      </p>
    );
  }
  return (
    <p className="scenario-card__gaps" data-absence-budget-state={state}>
      {ABSENCE_BUDGET_STATE_MESSAGES[state]}
    </p>
  );
}

/**
 * One planned absence — exactly its four fields (`id`/`absence_type_id`/`start_date`/`end_date`,
 * ADR-0005 addendum 2026-09-22 SC-3-02 point 6), `absence_type_id` resolved to a name through the
 * same independent catalogue read as the position's four dimensions (K-04).
 */
function AbsenceLine({ absence, catalog }: { absence: StaffingAbsence; catalog: CatalogReadState }) {
  const type = catalogName(catalog, "absenceTypes", absence.absence_type_id);
  return (
    <li className="staffing-plan__absence" data-name-state={type.state}>
      {type.text}: {formatEffectivePeriod(absence.start_date, absence.end_date)}
    </li>
  );
}
