import { IconPencil, IconPlus } from "@tabler/icons-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, RequestTimeoutError, getCatalogDimension, getCatalogRates } from "../../api/client";
import {
  CATALOG_DIMENSIONS,
  type CatalogDimension,
  type CatalogRate,
  type DimensionEntry,
} from "../../api/contracts/catalog";
import { formatEffectivePeriod } from "../../lib/dates";
import { formatRatePerUnit } from "../../lib/money";
import { DimensionEntryForm } from "./DimensionEntryForm";
import { RateForm } from "./RateForm";
import {
  INTERNAL_RATE,
  RESTRICTED_COST_RATE,
  type Dictionaries,
  dimensionNameOf,
  nameLookups,
} from "./catalogRows";
import { DIMENSION_LABELS, emptyDictionaryLabel } from "./dimensionLabels";
import { RE_READING_AFTER_SAVE, SAVED_AND_REREAD, SAVED_BUT_NOT_REREAD } from "./writeOutcome";
import "./CatalogScreen.css";

/**
 * SC-2-02 — the catalogue of roles, seniorities, locations, engagement types, vendors (SC-2-03) and
 * default rates. SC-2-04 added writing: adding and correcting an entry of any of the five
 * dictionaries, and adding and correcting a default rate window.
 *
 * It is still a function of what the server answered (NF-04, ADR-0005). Six consequences are the
 * point of the screen rather than details of it:
 *
 *   * **It takes no cost-visibility decision.** A row whose `default_cost_rate` the server removed
 *     renders a named refusal in that cell; a row that carries one renders the amount. The only
 *     input to that difference is the response body — there is no client-side permission check, no
 *     preflight, and no configuration flag (AC-06, K-04). The column header stays in place either
 *     way, because hiding it would make "you may not see this cost" and "the catalogue has no
 *     costs" the same screen (gate-1 decision 2). The same holds on the way in: the edit form shows
 *     the same refusal where the response carried no cost, and sends nothing back (K-21).
 *   * **It takes no decision about who may write, either** (K-18). Every form and every control is
 *     offered to everyone; the refusal, when there is one, is the server's `403`. Nothing here asks
 *     who the caller is — not before rendering a control, not before sending a request — which is
 *     the read-only screen's proven behaviour carried across to writing (AC-06, NF-04).
 *   * **The five dictionaries are read because a rate row carries UUIDs only.** The id → name join
 *     happens in `catalogRows.ts` (gate-1 decision 8), and an id that no dictionary entry matches
 *     gets a named absence rather than a blank cell or a dropped row — six requests are not one
 *     transaction.
 *   * **It takes no vendor-visibility decision either** (SC-2-03, K-10). The vendor dictionary is
 *     read on every mount, unconditionally, and the vendor column is rendered on every row.
 *   * **Any one of the six reads failing collapses the whole screen into one failure state, with
 *     zero rate rows** (gate-1 decision 9). A table of rates with a silently unnamed dimension
 *     column would look like data.
 *   * **After a save, what is on screen is a fresh read's answer** (gate-1 decision P-3a,
 *     ADR-0009, point 3) — never the write's own response body, and never anything assembled from
 *     what was typed. When that read fails, the screen says *that*, in its own words, instead of
 *     reporting a successful save as a catalogue that could not be loaded (G-6, K-19).
 *
 * Out of scope and deliberately absent: deleting anything, the `on_date` filter,
 * `GET /catalog/rates/effective`, pagination controls, and any currency conversion. See Issue #49.
 */

/** Which form, if any, is open. At most one at a time: two open forms are two sets of unsaved
 * values and two ways to lose one of them, and there is no draft state in this repository to hold
 * the loser. */
type OpenForm =
  | { kind: "add-entry"; dimension: CatalogDimension }
  | { kind: "edit-entry"; dimension: CatalogDimension; entry: DimensionEntry }
  | { kind: "add-rate" }
  | { kind: "edit-rate"; rate: CatalogRate };

/**
 * What the screen says about the last save that went through.
 *
 * Two states, not one, and neither is a failure state (K-19): the write succeeded in both. They
 * differ in whether the read that followed it did, which is the difference between "the rows below
 * include your change" and "the rows below predate it". Collapsing them into the screen's existing
 * `failed` state would turn a save that worked into a screen saying the catalogue could not be
 * loaded — and would invite a second save of a change already stored, which the database answers
 * with a conflict about the row this very person just wrote.
 */
type SaveNotice = { kind: "re-read" } | { kind: "not-re-read" };

interface CatalogSnapshot {
  readonly rates: CatalogRate[];
  /** Every row matching the backend's filter, without the page limit applied (K-11) — may be
   * larger than `rates.length` when the catalogue does not fit in one page (K-12). */
  readonly total: number;
  readonly dictionaries: Dictionaries;
}

type ScreenState =
  | { kind: "loading" }
  | ({ kind: "ready" } & CatalogSnapshot)
  | { kind: "denied" }
  | { kind: "timed-out" }
  | { kind: "failed" };

function toFailureState(error: unknown): ScreenState {
  if (error instanceof ApiError && (error.status === 401 || error.status === 403)) {
    return { kind: "denied" };
  }
  if (error instanceof RequestTimeoutError) {
    return { kind: "timed-out" };
  }
  return { kind: "failed" };
}

/**
 * The six reads, as one outcome. `Promise.all` is the mechanism: it rejects as soon as any one of
 * them does, so there is no state in which the screen holds five answers and a gap.
 *
 * All six are issued together, unconditionally, before anything is known about the rows: the vendor
 * dictionary is not read "if a rate turns out to have a vendor", because a conditional read is a
 * decision, and this screen takes none (K-10).
 *
 * `controller` ends all six early together — the caller passes one `AbortController`, not six
 * signals, so a bounce off this screen cannot leave some of the six reads still running (Reviewer
 * R-01). It also ends them early when one of the six rejects for a real reason while the screen
 * stays mounted: `Promise.all` settles as soon as the first rejection lands, but the other reads —
 * not cancelled by anything — keep running in the background to their own completion or deadline,
 * spending transfer and a socket on a screen that already committed to a failure state (Reviewer
 * R-06). Calling `controller.abort()` here, before the rejection is rethrown, stops them without
 * changing what gets rendered: this abort is never observed by the `cancelled` guard in the effect
 * below, so the real rejection still reaches `toFailureState` exactly as it would without this
 * catch — unlike the unmount abort, which is deliberately swallowed there.
 */
async function readCatalogue(controller: AbortController): Promise<CatalogSnapshot> {
  const { signal } = controller;
  const [rates, roles, seniorities, locations, engagementTypes, vendors] = await Promise.all([
    getCatalogRates(signal),
    getCatalogDimension("roles", signal),
    getCatalogDimension("seniorities", signal),
    getCatalogDimension("locations", signal),
    getCatalogDimension("engagement-types", signal),
    getCatalogDimension("vendors", signal),
  ]).catch((error: unknown) => {
    controller.abort();
    throw error;
  });
  return {
    rates: rates.rates,
    total: rates.total,
    dictionaries: {
      roles: roles.entries,
      seniorities: seniorities.entries,
      locations: locations.entries,
      "engagement-types": engagementTypes.entries,
      vendors: vendors.entries,
    },
  };
}

export function CatalogScreen() {
  const [state, setState] = useState<ScreenState>({ kind: "loading" });
  const [openForm, setOpenForm] = useState<OpenForm | null>(null);
  const [notice, setNotice] = useState<SaveNotice | null>(null);
  /** A post-save re-read is in flight: the rows on screen are known to be superseded and are about
   * to be replaced wholesale (P-3a). See `afterSave` and `open`. */
  const [rereading, setRereading] = useState(false);

  /**
   * Every read this screen has started and not finished, and whether the screen is still here.
   *
   * One mechanism for both reads — the six on mount and the six after a save — rather than one per
   * call site (Reviewer R-02, 2026-09-22). The mount effect had this discipline and the post-save
   * re-read did not: it built an `AbortController` only because `readCatalogue` takes one, never
   * aborted it, and was wired into no cleanup. A bounce off this screen mid-re-read therefore left
   * six `GET`s running and called `setState` on a component that no longer exists — invisible in a
   * browser, and exactly the shape the mount effect's own comment was written against.
   *
   * Refs rather than state: they are read by promises that settle *after* the render that would
   * have updated state, which is the only moment either of them matters.
   */
  const inFlight = useRef<Set<AbortController>>(new Set());
  const left = useRef(false);

  /** One catalogue read, registered for the lifetime of this screen. */
  const readIntoScreen = useCallback(async (): Promise<CatalogSnapshot> => {
    const controller = new AbortController();
    inFlight.current.add(controller);
    try {
      return await readCatalogue(controller);
    } finally {
      inFlight.current.delete(controller);
    }
  }, []);

  useEffect(() => {
    // Bouncing off this screen before the six reads finish must not leave them running: each one
    // holds one of the browser's six same-origin HTTP/1.1 sockets, contended with whatever screen
    // the user bounced to (Reviewer R-01, docs/PLAN.md:281-285 — a real catalogue read is ~19.7MB).
    //
    // The set is read into a local for the cleanup rather than through `inFlight.current` there:
    // this ref holds one set for the component's whole life and is never reassigned, so the two are
    // the same object — and `react-hooks/exhaustive-deps` is right in general that a cleanup
    // reading `.current` is reading whatever the ref points at *then*, which is why it says so out
    // loud rather than being silenced.
    const running = inFlight.current;
    left.current = false;
    readIntoScreen()
      .then((snapshot) => {
        if (!left.current) {
          setState({ kind: "ready", ...snapshot });
        }
      })
      .catch((error: unknown) => {
        // `left` is already true whenever this rejection is the abort below firing — the cleanup
        // that aborts is the same cleanup that sets the flag — so an aborted read never reaches
        // `toFailureState` and never renders as a stated failure of a screen the user has already
        // left.
        if (!left.current) {
          setState(toFailureState(error));
        }
      });
    return () => {
      left.current = true;
      for (const controller of running) {
        controller.abort();
      }
      running.clear();
    };
  }, [readIntoScreen]);

  /**
   * What happens after the server accepted a write, and the only thing that does (P-3a).
   *
   * The whole catalogue is read again and the answer replaces the screen's data. Nothing from the
   * form and nothing from the write's own response reaches the table — which is what makes
   * `default_cost_rate` behave the same after a save as before one: the gate that removed it from
   * a read removes it from the write's answer too, and this screen never had another copy.
   *
   * The `catch` is the G-6 decision in code (K-19). A failed read here is *not* routed through
   * `toFailureState`: the previous snapshot stays in `state`, so the rows and the dictionary
   * sections a person was looking at are still there, and the notice says the save went through and
   * the list did not refresh. Routing it through the blanket failure state would blank the screen
   * and report a stored change as a catalogue that could not be loaded.
   *
   * The window while that read is in flight is a state of its own, and a short one that is easy to
   * mistake for instantaneous (Reviewer R-02). Two things hold inside it: the six reads are
   * registered for cancellation like any others, so leaving the screen mid-re-read ends them and
   * updates nothing afterwards; and no new form can be opened, because the rows a form would be
   * seeded from are the rows this read is replacing. Without the second, opening *Edit* during the
   * window seeds a form from a row already known to be superseded — ADR-0007's marker included, so
   * the next save is refused as a stale marker by this screen's own doing — and the notice that
   * lands when the read settles appears above a form it is not about.
   */
  const afterSave = useCallback(async () => {
    // The screen may already be gone by the time this runs (Reviewer R-03, 2026-09-22). This
    // function is the continuation of a save: it resumes after `await createCatalogRate(…)` in a
    // form, which is long enough for a rail click — or for the error boundary unmounting this
    // screen — to have run the mount effect's cleanup, setting `left` and draining `inFlight`.
    // Without this line the save's success would then start six fresh `GET`s through
    // `readIntoScreen`, registering them in a set nothing will ever drain again: reads for a screen
    // nobody is on, which no cleanup can now abort, each holding one of the browser's six
    // same-origin sockets against the screen the user actually moved to (~19.7 MB of catalogue,
    // docs/PLAN.md:281-285).
    //
    // Checked *before* the read rather than after it, which is the whole finding: the `left` guards
    // below only decide whether to call `setState` once the re-read has already been issued, and a
    // read nobody wants is the thing ADR-0010 point 7 is about — not the `setState` it would have
    // fed.
    if (left.current) {
      return;
    }
    setOpenForm(null);
    // The previous outcome goes now rather than when this one arrives: a notice standing above a
    // re-read in progress states the result of a read that is still running.
    setNotice(null);
    setRereading(true);
    try {
      const snapshot = await readIntoScreen();
      if (left.current) {
        return;
      }
      setState({ kind: "ready", ...snapshot });
      setNotice({ kind: "re-read" });
    } catch {
      if (left.current) {
        return;
      }
      setNotice({ kind: "not-re-read" });
    } finally {
      if (!left.current) {
        setRereading(false);
      }
    }
  }, [readIntoScreen]);

  const open = useCallback((form: OpenForm) => {
    // No re-read guard here, deliberately. The one mechanism that blocks a form-open during the
    // post-save re-read is `rereading` reaching the controls as `disabled` (Reviewer R-02), and
    // they are the only callers of this function: React has already re-rendered them disabled by
    // the time any click could be dispatched, because `setRereading(true)` runs in the same task as
    // the save that started the read. A second copy of the rule here would be a rule no test can
    // reach — a disabled control swallows the click before this ever runs — and an unreachable
    // guard reads as a proof that something is prevented twice.
    //
    // A new attempt clears the last one's outcome: a notice left standing above a fresh form reads
    // as this form's result before it has one.
    setNotice(null);
    setOpenForm(form);
  }, []);

  const closeForm = useCallback(() => setOpenForm(null), []);

  return (
    <section className="catalog" aria-labelledby="catalog-heading">
      {/* `tabIndex={-1}`: focusable by script, never by Tab. `AppShell` focuses this heading after
          a rail activation mounts this screen, so the keyboard focus the rail entry held does not
          fall through to `document.body` when that entry leaves the DOM (Reviewer R-03). */}
      <div className="catalog__intro">
        <h2 id="catalog-heading" className="catalog__title" tabIndex={-1}>
          Roles &amp; rates
        </h2>
        {/* SC-2-05, gate-1 decision G-2: static text from the mockup, true of the screen as built
            — a selling rate is hourly (`RATE_UNIT_HOUR`) while a cost rate carries its own unit
            (`cost_rate_unit`), and the five dictionaries are what a staffing line is keyed by. Only once the catalogue is on screen: the loading and the
            three failure states have no mockup (Issue #59, out of scope 7), so their content
            stays exactly what it was. */}
        {state.kind === "ready" && <p className="catalog__description">{SCREEN_DESCRIPTION}</p>}
      </div>

      {/* `role="status"`: the one loading state that follows every navigation to this screen, and
          — unlike the three failure states below it — the only one that was not announced to a
          screen reader (Reviewer R-03). */}
      {state.kind === "loading" && (
        <p role="status" className="catalog__message">
          Loading the catalogue…
        </p>
      )}
      {/* A denied read renders a screen with no rows, no dictionary sections and no action
          controls — never data that is hidden afterwards (ADR-0005). It says nothing about
          projects: this refusal is the catalogue's. */}
      {state.kind === "denied" && (
        <p role="status" className="catalog__message catalog__message--attention">
          You do not have permission to view the catalogue.
        </p>
      )}
      {state.kind === "timed-out" && (
        <p role="status" className="catalog__message catalog__message--attention">
          The catalogue could not be loaded — request timed out.
        </p>
      )}
      {state.kind === "failed" && (
        <p role="status" className="catalog__message catalog__message--attention">
          The catalogue could not be loaded.
        </p>
      )}

      {/* The window between "the server accepted the write" and "the fresh read answered". Named
          rather than silent (NF-05, Reviewer R-02): the controls that open a form are disabled
          throughout it, and a control that is disabled for a reason nobody stated reads as broken.
          It says nothing about what the catalogue now holds — that is what the read is for. */}
      {rereading && (
        <p role="status" className="catalog__message catalog__notice">
          {RE_READING_AFTER_SAVE}
        </p>
      )}

      {/* The outcome of a save that went through, in its own region, above the data it is about.
          Never inside a form: the form that produced it is gone by the time this appears, and a
          success announced where a refusal is announced would make the two one control's state. */}
      {notice !== null && (
        <p
          role="status"
          className={
            notice.kind === "re-read"
              ? "catalog__message catalog__notice"
              : "catalog__message catalog__message--attention catalog__notice"
          }
        >
          {notice.kind === "re-read" ? SAVED_AND_REREAD : SAVED_BUT_NOT_REREAD}
        </p>
      )}

      {state.kind === "ready" && (
        <>
          <RatesPanel
            rates={state.rates}
            total={state.total}
            dictionaries={state.dictionaries}
            openForm={openForm}
            rereading={rereading}
            onOpen={open}
            onSaved={afterSave}
            onCancel={closeForm}
          />
          <DictionaryPanels
            dictionaries={state.dictionaries}
            openForm={openForm}
            rereading={rereading}
            onOpen={open}
            onSaved={afterSave}
            onCancel={closeForm}
          />
        </>
      )}
    </section>
  );
}

/** The props every panel needs to host at most one form. Passed down rather than held in a context:
 * one screen, one level of nesting, and a context would hide which panel can open what. */
interface FormHosting {
  readonly openForm: OpenForm | null;
  /** A post-save re-read is in flight (Reviewer R-02). Every control that would open a form is
   * disabled while it is: the rows those controls would seed a form from are the rows the read is
   * about to replace. `disabled`, not `aria-disabled` — the convention `CatalogFormShell` states
   * for a control that is built and momentarily unavailable, as against one that is not built. */
  readonly rereading: boolean;
  readonly onOpen: (form: OpenForm) => void;
  readonly onSaved: () => Promise<void> | void;
  readonly onCancel: () => void;
}

/**
 * The number of rate rows the response carried, as a sentence — a *complete* count, used only when
 * `total === rates.length` (K-11, K-12). The count comes from the body — the screen filters
 * nothing, so it has nothing else it could count (gate-1 decisions 3 and 5).
 */
function rateCountLabel(count: number): string {
  return count === 1 ? "1 default rate" : `${count} default rates`;
}

/**
 * The named state for a page that is not the whole catalogue (K-12): `total` from the response
 * exceeds `rates.length`, so the backend paged (K-11, default `limit` 2000) rather than sending
 * everything. Deliberately not the same literal `rateCountLabel` produces for a complete catalogue
 * of the same `total` — "2347 default rates" would state, falsely, that every one of those rows is
 * on screen. This sentence names the cut instead of hiding it in a number that merely happens to be
 * smaller than expected.
 */
function truncatedRateCountLabel(count: number, total: number): string {
  return `Showing first ${count} of ${total} default rates`;
}

/**
 * The catalogue holds nothing at all — `total === 0`, so the absence of rows is a fact about the
 * whole catalogue and not about the page that was sent (K-11, K-12).
 */
export const EMPTY_CATALOGUE = "The catalogue holds no default rates.";

/**
 * A page that carried no rows out of a catalogue that holds some (`rates` empty, `total > 0`,
 * Reviewer R-03). Reachable without anything failing: `offset` and `on_date` are parameters of
 * `GET /catalog/rates`, so a page past the end of a non-empty result set comes back `200` with an
 * empty list and a `total` that contradicts it.
 *
 * Deliberately not `EMPTY_CATALOGUE`: "the catalogue holds no default rates" is a claim about every
 * row the filter matched, and this response says in the same breath that it matched `total` of
 * them. Stating the emptiness of a page as the emptiness of the catalogue is the error that renders
 * correctly — nothing throws, the screen looks like a calm answer, and a project manager concludes
 * the organisation has no price list.
 *
 * It states the same two numbers `truncatedRateCountLabel` does, for the same reason and in the
 * same voice — what is on screen, and how large the catalogue actually is — and, like that one,
 * offers no control of its own: this screen builds no pager (K-12).
 */
function emptyPageLabel(total: number): string {
  return `Showing 0 of ${total} default rates — this page of the catalogue is empty.`;
}

/*
 * SC-2-05, gate-1 decision G-2: the static sentences the mockup (`15-catalog.png`) adds around the
 * data. Each one is true of the screen as built — default rates, matching over the five dimensions
 * (SC-2-03), a cost column the server may withhold (ADR-0005) — and none of them states anything
 * about the data a particular response carried. The mockup's "Amounts and dates are examples." is
 * deliberately not here: it describes the prototype's sample data, not this product (Issue #59,
 * out of scope 6).
 */
const SCREEN_DESCRIPTION =
  "Manage default rates and the shared dictionaries used across staffing plans.";
const RATES_DESCRIPTION =
  "Rates are matched by role, seniority, location, engagement type and vendor.";
const DIMENSIONS_HEADING = "Dimensions";
const DIMENSIONS_DESCRIPTION =
  "Shared name dictionaries used to configure staffing and default rates.";
const RATES_FOOTNOTE = "Cost rates may be restricted by access permissions.";

/**
 * The decorative half of a control whose words are its name (ADR-0011, point 3). `aria-hidden`
 * because the label next to it already says everything — an icon that reached the accessibility
 * tree would add nothing but noise to "Add role". No `title` either: `@tabler/icons-react` turns a
 * `title` prop into an `<svg><title>`, which is exactly a name leaking into the button's.
 *
 * No colour here and none on the icon: it draws with `currentColor` by construction, so it takes
 * the colour of the button it sits in (ADR-0011, points 1–2).
 */
function PlusIcon() {
  return <IconPlus className="catalog__icon" size={16} aria-hidden="true" focusable="false" />;
}

function PencilIcon() {
  return <IconPencil className="catalog__icon" size={16} aria-hidden="true" focusable="false" />;
}

/** The two ways a rate table can have no rows, told apart by `total` and never by `rates.length`
 * alone (Reviewer R-03). */
function NoRatesMessage({ total }: { total: number }) {
  if (total === 0) {
    return (
      <p role="status" className="catalog__message">
        {EMPTY_CATALOGUE}
      </p>
    );
  }
  return (
    <p role="status" className="catalog__message catalog__message--attention">
      {emptyPageLabel(total)}
    </p>
  );
}

function RatesPanel({
  rates,
  total,
  dictionaries,
  openForm,
  rereading,
  onOpen,
  onSaved,
  onCancel,
}: {
  rates: CatalogRate[];
  total: number;
  dictionaries: Dictionaries;
} & FormHosting) {
  return (
    // SC-2-05: the section's heading and its "Add" control stand above the bordered table, as in
    // `15-catalog.png`, rather than inside one card with it. Same elements, same order — heading,
    // control, form, count, table — so nothing a screen reader meets moved (K-27).
    <section className="catalog__section" aria-labelledby="catalog-rates-heading">
      <div className="catalog__section-header">
        <div className="catalog__section-heading">
          <h3 id="catalog-rates-heading" className="catalog__section-title">
            Default rates
          </h3>
          <p className="catalog__description catalog__description--section">{RATES_DESCRIPTION}</p>
        </div>
        {/* Offered unconditionally. Whether this caller may write is the server's answer to the
            request, not this screen's answer to a question it never asks (K-18). */}
        <button
          type="button"
          className="button button--primary"
          disabled={rereading}
          onClick={() => onOpen({ kind: "add-rate" })}
        >
          <PlusIcon />
          Add default rate
        </button>
      </div>

      {openForm?.kind === "add-rate" && (
        <RateForm dictionaries={dictionaries} onSaved={onSaved} onCancel={onCancel} />
      )}
      {openForm?.kind === "edit-rate" && (
        <RateForm
          // Keyed by the row: opening the form on another rate must build a new component with that
          // row's values, not re-use the first row's state under a new label.
          key={openForm.rate.id}
          rate={openForm.rate}
          dictionaries={dictionaries}
          onSaved={onSaved}
          onCancel={onCancel}
        />
      )}

      {rates.length === 0 ? (
        <NoRatesMessage total={total} />
      ) : (
        <div className="card catalog__rates">
          {/* `total > rates.length`: the backend paged (K-11) and this is not the whole catalogue.
              A separate, named sentence and a separate class — never `rateCountLabel`'s literal
              with a bigger number silently substituted in, which would state a page's size as the
              catalogue's (K-12). */}
          <p
            className={
              total > rates.length ? "catalog__count catalog__count--truncated" : "catalog__count"
            }
          >
            {total > rates.length ? truncatedRateCountLabel(rates.length, total) : rateCountLabel(rates.length)}
          </p>
          <RatesTable
            rates={rates}
            dictionaries={dictionaries}
            rereading={rereading}
            onOpen={onOpen}
          />
          {/* G-2. Outside the `<table>`, not a `<tfoot>`: a footer row would be read as a row of
              the table, and this is a sentence about the column, not a rate. */}
          <p className="catalog__footnote">{RATES_FOOTNOTE}</p>
        </div>
      )}
    </section>
  );
}

function RatesTable({
  rates,
  dictionaries,
  rereading,
  onOpen,
}: {
  rates: CatalogRate[];
  dictionaries: Dictionaries;
  rereading: boolean;
  onOpen: (form: OpenForm) => void;
}) {
  const names = nameLookups(dictionaries);

  return (
    <table className="catalog__table">
      {/* The layout has no room for a visible caption; a screen reader still gets one. */}
      <caption className="visually-hidden">Default rates in the catalogue</caption>
      <thead>
        <tr>
          {CATALOG_DIMENSIONS.map((dimension) => (
            <th key={dimension} scope="col">
              {DIMENSION_LABELS[dimension].column}
            </th>
          ))}
          <th scope="col">Effective period</th>
          {/* SC-2-05: the two amount headers align with the amounts under them (K-30b). */}
          <th scope="col" className="catalog__head-amount">
            Default selling rate
          </th>
          {/* Always rendered, including when every row's cost rate was removed for this caller.
              A column that disappeared would say "the catalogue has no costs" (gate-1 decision 2). */}
          <th scope="col" className="catalog__head-amount">
            Default cost rate
          </th>
          {/* SC-2-04. Last, so every existing column keeps its position. */}
          <th scope="col">Actions</th>
        </tr>
      </thead>
      <tbody>
        {rates.map((rate) => (
          <tr key={rate.id} className="catalog__row">
            {CATALOG_DIMENSIONS.map((dimension) => (
              <td key={dimension} className="catalog__cell-name">
                {dimension === "vendors" && rate.vendor_id === null ? (
                  <span className="catalog__internal">{INTERNAL_RATE}</span>
                ) : (
                  dimensionNameOf(rate, dimension, names)
                )}
              </td>
            ))}
            {/* The dates are the API's calendar strings, printed as they arrived (see lib/dates.ts):
                `new Date(...)` plus a local formatter shifts a date by a day west of Greenwich. */}
            <td className="catalog__cell-period">
              {formatEffectivePeriod(rate.effective_from, rate.effective_to)}
            </td>
            {/* Both amounts stay decimal strings until the one formatter in lib/money.ts, with the
                currency and the unit taken from the row (ADR-0002, addendum 2026-09-19). */}
            <td className="catalog__cell-amount">
              {formatRatePerUnit(rate.default_selling_rate, rate.currency, rate.unit)}
            </td>
            <td className="catalog__cell-amount">
              {/* The cost is priced in `cost_rate_unit`, never in the selling rate's `unit`, and a
                  row whose cost the server withheld carries no unit either — the shape check
                  refuses a half of the pair, so there is no unit here to default. */}
              {rate.default_cost_rate === null ||
              rate.default_cost_rate === undefined ||
              rate.cost_rate_unit === null ||
              rate.cost_rate_unit === undefined ? (
                <span className="catalog__restricted">{RESTRICTED_COST_RATE}</span>
              ) : (
                formatRatePerUnit(rate.default_cost_rate, rate.currency, rate.cost_rate_unit)
              )}
            </td>
            <td className="catalog__cell-actions">
              {/* The accessible name says which row, out of the words already in it — a column of
                  identical "Edit" buttons is a list of unlabelled controls to anybody not reading
                  the table visually. It carries no identifier: the join is the only thing that
                  turns a rate row into words (K-01). */}
              <button
                type="button"
                className="button catalog__link-button"
                aria-label={`Edit the default rate for ${CATALOG_DIMENSIONS.map((dimension) =>
                  dimensionNameOf(rate, dimension, names),
                ).join(", ")}, ${formatEffectivePeriod(rate.effective_from, rate.effective_to)}`}
                disabled={rereading}
                onClick={() => onOpen({ kind: "edit-rate", rate })}
              >
                <PencilIcon />
                Edit
              </button>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function DictionaryPanels({
  dictionaries,
  openForm,
  rereading,
  onOpen,
  onSaved,
  onCancel,
}: { dictionaries: Dictionaries } & FormHosting) {
  return (
    <>
      {/* G-2: the group heading from the mockup. An `h3`, a sibling of the dictionary panels'
          own `h3`s rather than their parent: demoting those five to `h4` would change heading
          levels this task may not change (K-27; Issue #59, out of scope 11). */}
      <div className="catalog__section-header">
        <div className="catalog__section-heading">
          <h3 className="catalog__section-title">{DIMENSIONS_HEADING}</h3>
          <p className="catalog__description catalog__description--section">
            {DIMENSIONS_DESCRIPTION}
          </p>
        </div>
      </div>
      <DictionaryGrid
        dictionaries={dictionaries}
        openForm={openForm}
        rereading={rereading}
        onOpen={onOpen}
        onSaved={onSaved}
        onCancel={onCancel}
      />
    </>
  );
}

function DictionaryGrid({
  dictionaries,
  openForm,
  rereading,
  onOpen,
  onSaved,
  onCancel,
}: { dictionaries: Dictionaries } & FormHosting) {
  return (
    <div className="catalog__dictionaries">
      {CATALOG_DIMENSIONS.map((dimension) => {
        const entries = dictionaries[dimension];
        const headingId = `catalog-dimension-${dimension}`;
        const labels = DIMENSION_LABELS[dimension];
        return (
          <section key={dimension} className="card catalog__panel" aria-labelledby={headingId}>
            <div className="catalog__panel-header">
              <h3 id={headingId} className="catalog__panel-title">
                {labels.section}
              </h3>
              {/* Primary, as in the mockup (K-30d) — in the product's orange, not the mockup's
                  blue (gate-1 decision G-8: one primary colour on every screen). */}
              <button
                type="button"
                className="button button--primary"
                disabled={rereading}
                onClick={() => onOpen({ kind: "add-entry", dimension })}
              >
                <PlusIcon />
                {`Add ${labels.column.toLowerCase()}`}
              </button>
            </div>

            {openForm?.kind === "add-entry" && openForm.dimension === dimension && (
              <DimensionEntryForm dimension={dimension} onSaved={onSaved} onCancel={onCancel} />
            )}
            {openForm?.kind === "edit-entry" && openForm.dimension === dimension && (
              <DimensionEntryForm
                key={openForm.entry.id}
                dimension={dimension}
                entry={openForm.entry}
                onSaved={onSaved}
                onCancel={onCancel}
              />
            )}

            {/* A dictionary the API returned empty is named as empty. The section stays: a
                vanishing section would be indistinguishable from a dimension that does not
                exist (K-06). */}
            {entries.length === 0 ? (
              <p role="status" className="catalog__message">
                {emptyDictionaryLabel(dimension)}
              </p>
            ) : (
              <ul className="catalog__entries">
                {entries.map((entry) => (
                  <li key={entry.id} className="catalog__entry">
                    <span className="catalog__entry-name">{entry.name}</span>
                    <button
                      type="button"
                      className="button catalog__link-button catalog__entry-action"
                      aria-label={`Rename ${entry.name} in the ${labels.inSentence} dictionary`}
                      disabled={rereading}
                      onClick={() => onOpen({ kind: "edit-entry", dimension, entry })}
                    >
                      <PencilIcon />
                      Rename
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </section>
        );
      })}
    </div>
  );
}
