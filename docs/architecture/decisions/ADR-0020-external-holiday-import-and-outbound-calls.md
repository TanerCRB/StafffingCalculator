# ADR-0020 — External holiday import and the outbound-call boundary (F-05, SC-3-08)

**Status:** Draft — pending approval

> Prepared by the Architect role for gate 1 of Issue #165 (SC-3-08, public-holiday import from
> Nager.Date). Human answers Q8–Q18 of that Issue are recorded there and summarised in "Decision
> basis" below. Number 0020, not 0018: ADR-0018 is reserved for the authentication ADR (SC-1-12) and
> ADR-0019 exists. Written in English (`TEAM-CONTRACT.md` §7); the older ADRs are Polish and are
> quoted, not translated. The provenance columns on the approval snapshot are recorded as a dated
> addendum in ADR-0004 (2026-09-29), not here. Dated addenda go at the end of this file.

## Context

F-05 (Requirements, "Calculations shall account for working days, public holidays, leave, and other
planned absences") is served today by `working_calendar_day` rows: "which days are holidays is a set
of rows, not a Python list and not a library of national calendars" (`app.models.catalog.
WorkingCalendarDay`, NF-10). Every row is entered by hand. SC-3-08 loads public holidays for a
country and year from an external service, Nager.Date, into those same rows.

This is **the first outbound network call this system would make.** At the time of writing no module
under `backend/` imports an HTTP client (`httpx` is declared only in the `dev` extra of
`backend/pyproject.toml`, for the test client), and `.github/workflows/backend.yml` runs `ruff` and
`pytest` only. Three things follow from that and are decided here rather than left to the task:

- the boundary between a calculation and an external source (a calculation that depends on a third
  party being reachable is not reproducible — F-12, AC-04, AC-10);
- what the system may send, to whom, and what it may believe of what comes back (egress policy,
  untrusted input);
- how imported rows are told apart from hand-entered ones, in the live table and in the approval
  snapshot (ADR-0004).

No personal data is sent or stored: the request carries a country code and a year, the response is
public holiday names. `TEAM-CONTRACT.md` §4 hard stop 6 is therefore not triggered.

## Decision basis (human answers, Issue #165, gate 1)

Q8 = A persisted, calculations read only the database. Q9 = C operator-run script / service function
only. Q10 = B country only on the imported day row. Q11 = A skip regional holidays and report.
Q12 = A only type `Public`. Q13 = A insert-if-absent, manual wins. Q14 = A strict validation.
Q15 = A `httpx` moves to runtime dependencies. Q16 = A hosted API, on the stated condition below.
Q17 = A provenance (source, name, country, year) on the day row and in the snapshot, no default on
the snapshot table. Q18 = A one year per call, the range not hard-coded.

## Decision

1. **A calculation never calls out (Q8 = A).** Imported holidays are `working_calendar_day` rows
   like any other, and every read that answers "is this a working day?" — the capacity grid, the
   FTE conversion (ADR-0008, addendum 2026-09-29), the paid-absence cost, the approval snapshot —
   reads the database and nothing else. No outbound network access exists on a request path, in the
   approval transaction, in `app.domain.**`, or in any read function of `app.data.**`. Consequence
   named: when the external source is unreachable, changes terms or disappears, *future imports*
   stop; no calculation, no draft and no approved scenario is affected.

2. **The producer is separate from every consumer and is started by a human (Q9 = C).** The import
   is one service function plus one operator-run script under `backend/scripts/`. It has **no HTTP
   endpoint, no scheduler, no startup hook, no CI job and no workflow that runs it.** Any of those is
   a new decision: an endpoint brings a permission (ADR-0005) and the write-from-UI contract
   (ADR-0009); a scheduler and a workflow bring an unattended outbound call and its failure
   handling. Consequences accepted with this choice:
   - there is no in-application authorisation on the import: authority is access to the database and
     a shell, exactly as for `backend/scripts/seed_dev_data.py`;
   - there is no `audit_log` row (ADR-0004, addendum 2026-09-27 SC-8-01: closed `action_type`
     vocabulary, approval only) — the trace of an import is the provenance columns and `created_at`
     on the rows it wrote, plus the report the script prints;
   - the import only ever *inserts*, so it never moves an existing row's `updated_at` and cannot
     invalidate an ADR-0007 marker held by someone editing a calendar day.

3. **Provenance is a closed set enforced by the database (Q17 = A).** `working_calendar_day` gains
   four provenance facts: `source` (closed set `manual`, `nager_date`), the holiday's `name`, the
   `country_code` and the `year`. The closed set and its coherence are `CHECK` constraints, not
   validation in the service function: a fixture, a seed script or a later import must not be able to
   write an unnamed origin. Rows that exist before the migration are `manual` — true by construction,
   since no other writer existed. Adding a value to the set is a migration and a dated entry here.
   The year is derivable from `day`; if it is stored, the database keeps the two spellings from
   disagreeing (see "Additional proposals", (h)). The snapshot side is ADR-0004, addendum 2026-09-29.

4. **Insert-if-absent, manual wins (Q13 = A).** The write is a single
   `INSERT … ON CONFLICT (calendar_id, day) DO NOTHING` against the existing
   `UNIQUE (calendar_id, day)` — not a `SELECT` followed by an `INSERT` (check-then-act, which the
   repository records as having survived delivered tests three times), and never `DO UPDATE`. A day a
   human already named — in either direction, `non_working` or a `working` Saturday — is never
   touched by an import, and neither is a day a previous import wrote. Two concurrent imports of the
   same unit leave one row per day and no error. Consequence: an upstream correction (a date moved, a
   holiday withdrawn) does not propagate; it is *reported* (`stale_import_rows`, below) and left to a
   human.

5. **Mapping scope (Q11 = A, Q12 = A).** A received row becomes a `working_calendar_day` only when it
   is of type `Public` **and** global. It is always written as `non_working`; an import never writes
   `working`, and never a partial day (ADR-0008, addendum 2026-09-22, point 6). Rows of any other
   type are skipped and counted; regional (non-global) rows are skipped and counted, not attributed
   to a calendar. Consequence named: a position whose calendar stands for a region with a regional
   holiday over-states its capacity until a human adds the day; the report is the only signal.

6. **One unit of work is one (calendar, country, year), all or nothing (Q18 = A).** A single
   transaction: any validation failure, transport failure or database error leaves zero rows written
   for that unit. There is no partial year. The service function takes one year; the allowed range of
   years is **not** a constant in the code — the source decides what it can answer, and a refusal by
   the source is a named failure (below). A caller wanting several years calls the function several
   times; those units are independent, so a failure in unit *n* leaves units 1..*n*−1 committed.

7. **The response is untrusted input (Q14 = A).** Every field the importer reads is validated on
   every received row before anything is written; a single invalid row rejects the whole unit
   (decision 6). Validated at least: the payload is a list; `date` is an ISO calendar date whose year
   equals the requested year; `countryCode` equals the requested country; `types` is a list of known
   strings; `global` is a boolean; the retained rows' `name` is non-empty, of bounded length (no
   wider than the 200-character names the snapshot already freezes) and free of control characters.
   Values are bound as parameters, never assembled into SQL, and are stored as data, never
   interpreted. The response body is never written to a log; the report carries counts and named
   failures only.

8. **Egress policy.** The rules below are the whole of what the import may do on the network:
   - **One fixed host, from configuration.** The host is HTTPS, taken from configuration (not from
     an argument, a payload or a redirect). No second host, no URL built from received data.
   - **Inputs validated before the URL is built.** `country_code` is exactly two uppercase ASCII
     letters (no normalisation, no trimming); `year` is an integer of four digits. A value failing
     either check never reaches the URL and no request is issued.
   - **Bounded.** Explicit finite connect and read timeouts and a total deadline; a cap on response
     bytes (enforced while reading, not after) and a cap on the number of rows. A year of public
     holidays is tens of rows, so the caps sit far above legitimate use.
   - **No redirects followed.** Any 3xx is a named failure.
   - **TLS verification on, never disabled** — no `verify=False`, no environment-variable escape.
   - **Nothing is sent** beyond the path (no body, no credentials, no identifier from the database,
     no data about a project, scenario or person).
   - **CI never calls the live API.** Tests use an in-process fake transport or a local fake; a guard
     fails the suite if a test opens a connection to a non-loopback address. No workflow file runs
     the import (decision 2).
   The concrete numeric caps and the host value are the Developer's, fixed in configuration and
   asserted by tests; the *existence* of each rule is the decision.

9. **Country is attributed on the imported row only (Q10 = B).** The calendar does not know its
   country, and a location (`catalog_locations`) has none. Consequence accepted: nothing prevents
   importing `DE` holidays into a calendar that already holds `PL` ones, or a country typo that is
   still a valid code. The mitigation is reporting, not refusal (proposal (e) below).

10. **Snapshot (ADR-0004, addendum 2026-09-29).** The provenance columns are copied to
    `approved_snapshot_working_calendar_day` by the existing approval statement, without a default on
    the snapshot table. The set of snapshot tables does not change.

11. **Dependency (Q15 = A).** `httpx` moves from the `dev` extra to `dependencies` in
    `backend/pyproject.toml`; no other package is added, but the runtime image now also carries its
    transitive TLS trust store and HTTP stack. This is a supply-chain change and a Security Auditor
    review item (`architecture-sensitive-paths.md`, "any new or upgraded dependency"). The import
    module is the only importer of the HTTP client (control E-02).

12. **The source and its terms (Q16 = A).** The hosted Nager.Date API is used **on the basis that this
    project is used privately and non-commercially.** As recorded by the human at gate 1 (the
    Architect did not read the document), the Terms of Service
    (https://nagerholidays.com/legal/termsofservice, version of 2023-09-15) require active sponsorship
    for commercial use. **Condition of this decision: if the use of this system becomes commercial,
    sponsorship is arranged first — or Q16 is reopened** (a self-hosted instance or another source is
    then a new dated entry here). Nothing in the system detects a change of use; the condition is
    carried by this text and by the script's help output (E-14).
    Risks accepted with the hosted API, as stated in the terms as recorded: no warranty of
    correctness or availability, terms that can change, and termination at any time. All three are
    mitigated by decision 1 (Q8 = A): the import can stop working, the data already imported and every
    approved scenario cannot. Correctness of the imported data is not vouched for by the source: the
    report and the operator's review are the control.

## Gaps closed by proposal (Analyst gaps of #165), pending human confirmation

These answer the gaps the Analyst raised on Issue #165 and are proposals, not decisions, until the
human confirms them at gate 1.

**(a) The import report.** The service function returns a structured result (not text); the script
prints it and, on failure, exits non-zero with a named failure. The result carries: the target
(calendar, country, year); `received_rows`; and the counters `written`, `type_skipped`,
`regional_skipped`, `collapsed_duplicates`, `conflict_skipped`; `stale_import_rows`; the days added
per calendar month; and `mixed_countries` (see (e)). The named failures are a closed set the script
prints by name: `invalid_argument`, `unknown_calendar`, `unreachable`, `timeout`,
`unexpected_status`, `redirect_refused`, `payload_too_large`, `payload_invalid`, `database_error`.
`stale_import_rows` are rows already in the calendar with `source = nager_date` for the same
(country, year) whose day is absent from the received payload — reported, never deleted.

**(b) Empty result.** An empty list, or HTTP 204, is a success that writes zero rows and is reported
as such (`received_rows = 0`, with an explicit "nothing received" line) — not a failure, not silence.
`stale_import_rows` is still computed, which is what makes an unexpectedly empty year visible.

**(c) `country_code`.** Exactly two uppercase ASCII letters; `pl`, ` PL`, `POL` and `P1` are refused
with `invalid_argument` before any URL exists. No case normalisation: the repository already refuses
`"eur"` rather than upper-casing it (`Iso4217Code`, `FIRST_DAY_OF_MONTH_EXPRESSION`), and a silent
correction is a decision nobody took.

**(d) Disjoint counters.** The filters run in a fixed order and each received row lands in exactly
one counter: (1) type filter → `type_skipped`; (2) of the rest, the global filter →
`regional_skipped`; (3) of the rest, rows sharing a `date` are collapsed to one →
`collapsed_duplicates`; (4) the remaining days are inserted → `written`, and days that hit the
existing row → `conflict_skipped`. Improvement on the brief: the counters are a **partition** of the
received rows, so `received_rows = type_skipped + regional_skipped + collapsed_duplicates + written +
conflict_skipped` always holds, and a test asserts that equality (E-07) — a counter that silently
double-counts or drops a row breaks it. The collapse is deterministic (rows ordered by date then name
before the first is kept), so two runs against one payload write the same name.

**(e) Country/calendar mismatch.** Not enforced under Q10 = B. The report carries `mixed_countries`:
the distinct `country_code` values among **all** `nager_date` rows now in the target calendar, listed
whenever there is more than one. The operator sees it; nothing refuses it.

### Additional proposals by the Architect (not in the Analyst's list)

**(f)** A row is retained when its `types` list *contains* `Public` (a holiday can carry several
types); a row without `Public` in the list is `type_skipped`. Q12 = A says "only `Public`"; this is
the reading of it, and needs confirming.
**(g)** A structurally invalid row (missing field, wrong type) rejects the unit even when the row
would have been skipped for type or region; content rules (name length, control characters) are
applied to retained rows only. Strictness where the importer reads the field, leniency where it never
uses it.
**(h)** If the year is stored on the row, a `CHECK` ties it to the year of `day`, so the two cannot
disagree. Option: not store the year at all (derive from `day`). Q17 = A names it as provenance, so
the proposal keeps it and guards it.
**(i)** An import writes into the *live* calendar. A draft scenario that reads that calendar changes
its working-day count the moment rows are added; that is expected. What the import also changes,
and what is **not** repaired here, is the capacity grid of an **approved** scenario, which still
reads the live calendar (ADR-0004, addendum 2026-09-23 SC-5-06, point 4: named, not repaired). Until
that is repaired, an import can move a figure shown for an approved calculation — see the human
decision listed in the Architect's impact map for SC-3-08.

## Not decided here

- Who may run the import, beyond "whoever has database and shell access" (decision 2) — the
  authentication ADR (ADR-0018, reserved).
- Regional holidays, and country/region on a location or calendar (Q10 = B, Q11 = A leave both out).
- An endpoint, a schedule or a UI for the import (decision 2).
- Repairing the live read of the approved-scenario capacity grid (proposal (i)).
- Any consumer that treats the provenance as more than a label (e.g. refusing a calendar mixing
  countries).

## Controls

| Control | Acceptance criterion |
|---|---|
| E-01 | With every outbound socket refused, the capacity grid, the FTE conversion, the paid-absence cost and the scenario approval all run to completion. |
| E-02 | The HTTP client is imported by the holiday-import module and by no other module of `backend/app` or `backend/scripts` (an import-graph test). |
| E-03 | No HTTP route, scheduler, startup hook or workflow file starts the import. |
| E-04 | The database refuses a `source` outside the closed set, and refuses a `nager_date` row lacking name, country or year, without the service function being involved. |
| E-05 | An import does not change any column of an existing row for the same (calendar, day) — a manual `working` and a manual `non_working` row survive byte for byte, including `updated_at`; two concurrent imports of one unit leave one row per day and raise nothing; the write is one `ON CONFLICT DO NOTHING` statement. |
| E-06 | Only rows whose types contain `Public` and which are global are written, always as `non_working`; every other received row is counted in exactly one skip counter. |
| E-07 | For every import, `received_rows` equals the sum of the disjoint counters. |
| E-08 | An invalid row anywhere in the payload, or a failure after the first insert, leaves zero rows written for the unit. |
| E-09 | The payload validator refuses: a date outside the requested year, a different `countryCode`, a non-ISO date, a missing field, a wrong type, an over-long name, a control character, a body over the byte cap, more rows than the cap. |
| E-10 | `country_code` other than two uppercase ASCII letters and a `year` other than four digits issue no request; the configured host is the only host contacted; timeouts are set; a redirect is refused; TLS verification is enabled; the request carries no body and no value read from the database. |
| E-11 | The test suite fails when any test opens a connection to a non-loopback address. |
| E-12 | The report has the fields listed in proposal (a); an empty payload is a success with `received_rows = 0`; stale import rows are reported and none is deleted; two countries in one calendar are flagged in `mixed_countries`; every named failure makes the script exit non-zero and print its name. |
| E-13 | `httpx` is in `dependencies`; a clean install without the `dev` extra can import the import module. |
| E-14 | The script's help output names the non-commercial condition and this decision. |

The snapshot controls are in ADR-0004, addendum 2026-09-29.

## Related requirements

F-05 (working days and public holidays in calculations), NF-10 (calendars are data), F-12 / AC-04 /
AC-10 (reproducibility of approved calculations — ADR-0004), NF-04 (nothing about a project, scenario
or person leaves the system).

## Addenda

None yet.
