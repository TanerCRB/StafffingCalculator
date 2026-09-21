import { useEffect, useState } from "react";

import { ApiError, RequestTimeoutError, getCatalogDimension, getCatalogRates } from "../../api/client";
import {
  CATALOG_DIMENSIONS,
  type CatalogDimension,
  type CatalogRate,
  type DimensionEntry,
} from "../../api/contracts/catalog";
import { formatEffectivePeriod } from "../../lib/dates";
import { formatRatePerUnit } from "../../lib/money";
import { handleNotYetImplemented, notImplementedHint } from "../../lib/notImplemented";
import { DIMENSION_LABELS, emptyDictionaryLabel, unknownEntryLabel } from "./dimensionLabels";
import "./CatalogScreen.css";

/**
 * SC-2-02 — the catalogue of roles, seniorities, locations, engagement types and default rates.
 * Read only: no write, no filter, no date resolution of its own.
 *
 * It is a pure function of five responses (NF-04, ADR-0005): `GET /catalog/rates` and
 * `GET /catalog/dimensions/{dimension}` for the four dictionaries. Three consequences are the point
 * of the screen rather than details of it:
 *
 *   * **It takes no cost-visibility decision.** A row whose `default_cost_rate` the server removed
 *     renders a named refusal in that cell; a row that carries one renders the amount. The only
 *     input to that difference is the response body — there is no client-side permission check, no
 *     preflight, and no configuration flag (AC-06, K-04). The column header stays in place either
 *     way, because hiding it would make "you may not see this cost" and "the catalogue has no
 *     costs" the same screen (gate-1 decision 2).
 *   * **The four dictionaries are read because a rate row carries UUIDs only.** The id → name join
 *     happens here (gate-1 decision 8), and an id that no dictionary entry matches gets a named
 *     absence rather than a blank cell or a dropped row — five requests are not one transaction.
 *   * **Any one of the five reads failing collapses the whole screen into one failure state, with
 *     zero rate rows** (gate-1 decision 9). A table of rates with a silently unnamed dimension
 *     column would look like data.
 *
 * Out of scope and deliberately absent: every write (the "Add default rate" control is rendered,
 * announced and wired to nothing), the `on_date` filter, `GET /catalog/rates/effective`, pagination,
 * and any currency conversion. See Issue #39.
 */

/** Rendered in a cost-rate cell the response did not carry. The decided literal (gate-1 decision
 * 2) — not a dash, not a zero, and not a symbol shared with any other kind of absence. */
export const RESTRICTED_COST_RATE = "Restricted";

const ADD_RATE_HINT = notImplementedHint(
  "adding a default rate is a separate task (Issue #39, out of scope 1)",
);

/** All four dictionaries, always. The record is exhaustive over `CatalogDimension`, so a fifth
 * dimension added to the contract fails the build here instead of quietly going unread. */
type Dictionaries = Readonly<Record<CatalogDimension, DimensionEntry[]>>;

interface CatalogSnapshot {
  readonly rates: CatalogRate[];
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
 * The five reads, as one outcome. `Promise.all` is the mechanism: it rejects as soon as any one of
 * them does, so there is no state in which the screen holds four answers and a gap.
 *
 * `controller` ends all five early together — the caller passes one `AbortController`, not five
 * signals, so a bounce off this screen cannot leave some of the five reads still running (Reviewer
 * R-01). It also ends them early when one of the five rejects for a real reason while the screen
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
  const [rates, roles, seniorities, locations, engagementTypes] = await Promise.all([
    getCatalogRates(signal),
    getCatalogDimension("roles", signal),
    getCatalogDimension("seniorities", signal),
    getCatalogDimension("locations", signal),
    getCatalogDimension("engagement-types", signal),
  ]).catch((error: unknown) => {
    controller.abort();
    throw error;
  });
  return {
    rates: rates.rates,
    dictionaries: {
      roles: roles.entries,
      seniorities: seniorities.entries,
      locations: locations.entries,
      "engagement-types": engagementTypes.entries,
    },
  };
}

export function CatalogScreen() {
  const [state, setState] = useState<ScreenState>({ kind: "loading" });

  useEffect(() => {
    let cancelled = false;
    // Bouncing off this screen before the five reads finish must not leave them running: each one
    // holds one of the browser's six same-origin HTTP/1.1 sockets, contended with whatever screen
    // the user bounced to (Reviewer R-01, docs/PLAN.md:281-285 — a real catalogue read is ~19.7MB).
    const controller = new AbortController();
    readCatalogue(controller)
      .then((snapshot) => {
        if (!cancelled) {
          setState({ kind: "ready", ...snapshot });
        }
      })
      .catch((error: unknown) => {
        // `cancelled` is already true whenever this rejection is the abort below firing — the
        // cleanup that aborts is the same cleanup that sets the flag — so an aborted read never
        // reaches `toFailureState` and never renders as a stated failure of a screen the user has
        // already left.
        if (!cancelled) {
          setState(toFailureState(error));
        }
      });
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, []);

  return (
    <section className="catalog" aria-labelledby="catalog-heading">
      {/* `tabIndex={-1}`: focusable by script, never by Tab. `AppShell` focuses this heading after
          a rail activation mounts this screen, so the keyboard focus the rail entry held does not
          fall through to `document.body` when that entry leaves the DOM (Reviewer R-03). */}
      <h2 id="catalog-heading" className="card__title" tabIndex={-1}>
        Roles &amp; rates
      </h2>

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

      {state.kind === "ready" && (
        <>
          <RatesPanel rates={state.rates} dictionaries={state.dictionaries} />
          <DictionaryPanels dictionaries={state.dictionaries} />
        </>
      )}
    </section>
  );
}

/** The number of rate rows the response carried, as a sentence. The count comes from the body — the
 * screen filters nothing, so it has nothing else it could count (gate-1 decisions 3 and 5). */
function rateCountLabel(count: number): string {
  return count === 1 ? "1 default rate" : `${count} default rates`;
}

function RatesPanel({ rates, dictionaries }: { rates: CatalogRate[]; dictionaries: Dictionaries }) {
  return (
    <section className="card catalog__panel" aria-labelledby="catalog-rates-heading">
      <div className="catalog__panel-header">
        <h3 id="catalog-rates-heading" className="catalog__panel-title">
          Default rates
        </h3>
        {/* Rendered, focusable, announced, wired to nothing: `POST /catalog/rates` exists, and
            mixing a write into the first read-only screen is what Issue #39 (out of scope 1)
            refuses. */}
        <button
          type="button"
          className="button button--primary"
          aria-disabled="true"
          title={ADD_RATE_HINT}
          onClick={handleNotYetImplemented}
        >
          Add default rate
        </button>
      </div>

      {rates.length === 0 ? (
        <p role="status" className="catalog__message">
          The catalogue holds no default rates.
        </p>
      ) : (
        <>
          <p className="catalog__count">{rateCountLabel(rates.length)}</p>
          <RatesTable rates={rates} dictionaries={dictionaries} />
        </>
      )}
    </section>
  );
}

function RatesTable({ rates, dictionaries }: { rates: CatalogRate[]; dictionaries: Dictionaries }) {
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
          <th scope="col">Default selling rate</th>
          {/* Always rendered, including when every row's cost rate was removed for this caller.
              A column that disappeared would say "the catalogue has no costs" (gate-1 decision 2). */}
          <th scope="col">Default cost rate</th>
        </tr>
      </thead>
      <tbody>
        {rates.map((rate) => (
          <tr key={rate.id} className="catalog__row">
            {CATALOG_DIMENSIONS.map((dimension) => (
              <td key={dimension} className="catalog__cell-name">
                {names[dimension].get(dimensionIdOf(rate, dimension)) ??
                  unknownEntryLabel(dimension)}
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
              {rate.default_cost_rate === null || rate.default_cost_rate === undefined ? (
                <span className="catalog__restricted">{RESTRICTED_COST_RATE}</span>
              ) : (
                formatRatePerUnit(rate.default_cost_rate, rate.currency, rate.unit)
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function DictionaryPanels({ dictionaries }: { dictionaries: Dictionaries }) {
  return (
    <div className="catalog__dictionaries">
      {CATALOG_DIMENSIONS.map((dimension) => {
        const entries = dictionaries[dimension];
        const headingId = `catalog-dimension-${dimension}`;
        return (
          <section key={dimension} className="card catalog__panel" aria-labelledby={headingId}>
            <h3 id={headingId} className="catalog__panel-title">
              {DIMENSION_LABELS[dimension].section}
            </h3>
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
                    {entry.name}
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

/** Which id on a rate row addresses which dictionary. An exhaustive `switch`, so a new dimension
 * cannot be forgotten silently. */
function dimensionIdOf(rate: CatalogRate, dimension: CatalogDimension): string {
  switch (dimension) {
    case "roles":
      return rate.role_id;
    case "seniorities":
      return rate.seniority_id;
    case "locations":
      return rate.location_id;
    case "engagement-types":
      return rate.engagement_type_id;
  }
}

function namesById(entries: DimensionEntry[]): ReadonlyMap<string, string> {
  return new Map(entries.map((entry) => [entry.id, entry.name]));
}

/** One id → name map per dictionary, built once per render rather than a linear scan per cell. */
function nameLookups(dictionaries: Dictionaries): Readonly<
  Record<CatalogDimension, ReadonlyMap<string, string>>
> {
  return {
    roles: namesById(dictionaries.roles),
    seniorities: namesById(dictionaries.seniorities),
    locations: namesById(dictionaries.locations),
    "engagement-types": namesById(dictionaries["engagement-types"]),
  };
}
