# ADR-0021 — Risk representation (cost event or reserve) and the double-representation signal (F-09 pt 4–5)

**Status:** Accepted

> Prepared by the Architect role for gate 1 of Issue #89 (proposed SC-6-08). Written in English
> (`TEAM-CONTRACT.md` §7); the older ADRs are Polish and are quoted, not translated. The points
> below marked **Proposed** are the Architect's recommendation for the human's answers to Q-1..Q-8;
> accepted at gate 1 on 2026-09-29: the human took every recommendation (Q-1..Q-8 = the Proposed option; G-1: the risk read returns kinds and counts only, no amounts; G-2: the additive field on the additional-cost read is the id of the linked risk, or null). Dated addenda go at the end of this file.

## Context

F-09 pt 4: "Risks may be represented by explicit cost events or reserves, with the selected method
visible." F-09 pt 5: "The system shall make it possible to identify when the same risk is
represented in both an explicit event and a reserve." Requirements §3 also names "risk reserves" as
an independent attribute of a scenario.

ADR-0014 (accepted) models additional costs as fixed-amount, one-off/recurring rows owned by a
scenario, and states in "Czego ten dokument nie rozstrzyga": *"Rezerw ryzyka (F-09); wykrywania
nakładających się obciążeń"*. `docs/PLAN.md` SC-5-05 lists "mechanizm rezerw ryzyka (F-09)" and "F-08
pkt 7 (nakładające się obciążenia/narzuty/rezerwy, F-09)" as out of scope. No reserve, risk or
risk-identity concept exists in the schema.

Two facts constrain every option:

- The additional-cost total already feeds `included_cost`, `profit`, `margin`, `markup`
  (`/results`, `/compare`, what-if) and is deliberately **not** personnel-cost-gated (ADR-0014
  pt 7 and 11; ADR-0005 addendum SC-7-01 pt 3). Any change to what enters that total changes an
  accepted, proven contract.
- "The same risk" cannot be recovered from amounts, categories or periods without guessing. Detection
  is only as good as the identity the user declares.

## Decision (draft — each point depends on the human's answer to the named question)

1. **A risk has a declared identity (Q-2).** **Proposed:** an explicit per-scenario risk entity;
   both representations may point at it; a risk is "double-represented" exactly when at least one
   cost event and at least one reserve point at the same risk. No heuristic (same category, same
   month, similar amount) ever produces the signal. Representations with no declared risk are
   legal and can never be signalled — a named limitation, not a defect.
2. **Storage (Q-1).** **Proposed:** reserves in their own table, cost events remain rows of the
   ADR-0014 additional-cost table gaining an optional link to the risk. The ADR-0014 table is not
   given a `kind` discriminator.
3. **Totals are never altered by detection (Q-3).** The signal is additive. Neither representation
   is dropped, netted or de-duplicated by the system; a scenario with a double-represented risk sums
   exactly what it summed before the signal existed. **Proposed:** the reserve total is reported
   next to, not inside, the ADR-0014 `additional_cost` total in this task; whether and how it enters
   `included_cost`/`profit` is a separate decision (see Q-3).
4. **A reserve is a fixed amount in one currency (Q-4).** Same money rules as ADR-0014 pt 6
   (`Decimal`, precision above minor unit, `> 0`, one rounding at the end via
   `app.core.money.round_money`, `currency_mismatch` never a partial sum, ADR-0006). No probability
   or percentage field (a further fixed-point class, ADR-0002 addendum) in this task.
5. **The selected method is visible (F-09 pt 4).** Per risk, the response states which
   representations exist: `cost_event`, `reserve`, `both`, or `none`.
6. **Group 2 of ADR-0004** for the risk entity and the reserve: own data of the scenario, write
   refused under `approved` in the same statement as the write, race with approval closed, no
   snapshot. The link column on a cost event is edited under the same guard.
7. **Ownership and isolation:** `scenario_id NOT NULL`, no `ON DELETE CASCADE`; the link from a cost
   event or reserve to a risk is a composite foreign key that only admits a risk of the same
   scenario (the ADR-0014 pt 1 construction). Scope, `404` never `403`, one `404` body for every
   "nothing here for you" case (ADR-0005 addendum SC-5-05 pt 4).
8. **Copy (Q-7).** The risk entity is copied by an entry in `SCENARIO_CHILD_COPIERS`; cost events
   (including position-level ones, copied inside `copy_staffing_positions`) and reserves are copied
   with their link remapped to the copy's own risk. **Proposed:** the risk entry runs before
   `copy_staffing_positions`, with a per-scenario unique risk name as the remap key (the ADR-0016
   pt 5 / ADR-0004 addendum SC-4-05 D-4 = A pattern).
9. **Access (Q-6).** **Proposed:** `STAFFING_READ`/`STAFFING_WRITE`, no new permission, no
   personnel-cost conjunction, scenario-level only (a reserve or risk carries no `position_id`), so
   the `headcount = 1` exposure named in ADR-0014 pt 11 is not widened.
10. **Concurrency:** each new row has its own ADR-0007 marker; edit and delete are `PATCH`/`DELETE`
    with it. The ADR-0014 R-04 risk (a retried `POST` creates a second identical row) applies to a
    reserve unchanged; nothing here resolves it.

## Open questions for the human (resolved before this draft can be accepted)

| Q | Question | Options and consequences | Proposed |
|---|---|---|---|
| Q-1 | Reserve storage relative to the ADR-0014 table | **A** new `risk_reserve` table: zero behaviour change for every existing additional-cost consumer (`/results`, `/compare`, what-if, copiers); costs a new table, migration, guard, copier, marker. **B** `kind` column on `additional_cost`: least new code, but every reader of the table silently starts summing reserves into `additional_cost` and `included_cost` (a change to accepted ADR-0014 pt 7 and to ADR-0005 SC-7-01 pt 3, which needs a dated addendum) and the shared CHECKs (`amount > 0`, closed period) constrain a concept they were not designed for. | A |
| Q-2 | How "the same risk" is identified | **A** declared risk entity, both representations link to it: detection is exact, costs one more entity and a link column; **B** free-text tag on both rows: no entity, but typos silently defeat detection and a false "not double" is unrecoverable; **C** heuristic on category/period/amount: guesses, produces false positives and false negatives, rejected by the requirement's own wording ("identify"). | A |
| Q-3 | Does the reserve enter `included_cost`/`profit`? | **A** not in this task, reported as its own total next to `additional_cost`: nothing existing changes, but profit omits a reserve the user chose as the representation of a risk until a follow-up (a named understatement, as ADR-0014 pt 9 did for rebilled cost); **B** enters `additional_cost`: breaks the meaning of an accepted, proven total; **C** a fourth component of `included_cost` with its own named state: correct end state, but changes ADR-0002's composite-state rule and every profitability consumer — its own decision and task. Under every option, detection never drops or nets either representation. | A now, C as follow-up |
| Q-4 | Reserve amount basis and period | **A** fixed amount, month-granular one-off/recurring like ADR-0014 (least new surface, mirrors Q-1 = A there); **B** probability × impact: needs a new decimal class and rounding rule (ADR-0002 addendum), a second decision. | A |
| Q-5 | Where the signal surfaces | **A** risk endpoint returning per risk its representations and the double flag, plus one additive field on the additional-cost read: contained; **B** also on `/results` and `/compare`: touches the public contract every result consumer and the frontend shape check (ADR-0010) depend on; **C** separate "diagnostics" endpoint only: hides the signal from anyone not looking for it. | A |
| Q-6 | Permissions | **A** reuse `STAFFING_*` (ADR-0014 pt 11 precedent); **B** new `RISK_*` permission: granularity with no subject and a placeholder-set widening (ADR-0005 addendum 2026-09-18). | A |
| Q-7 | Copy ordering | **A** risk copier first, remap by unique name; **B** a shared id-mapping channel through every copier (rejected once, ADR-0016 D-4). | A |
| Q-8 | Delete of a risk that representations still point at | **A** refuse (no `ON DELETE` action; the database refuses): safe, the user unlinks first; **B** `SET NULL`/cascade: a second, unguarded way to alter an `approved` scenario's rows (ADR-0003 pt 1). | A |

## What this document does not decide

Probability-weighted reserves; a reserve entering profit/margin (Q-3 C); a phase-level risk (no phase
entity, ADR-0003 "Odłożone"); overlap between reserves/overheads and personnel-cost surcharges or the
"management" category (F-08 pt 7 / Requirements §5 line "overlapping charges … reserves visible" —
a different overlap, not a same-risk link); a UI (F-11); idempotent creation (ADR-0014 R-04); export.

## Consequences

- New scenario-child tables: a migration (ADR-0001), guard and copier entries (ADR-0004), an entry in
  `SCENARIO_CHILD_COPIERS` before `copy_staffing_positions` if Q-7 = A.
- Dated addenda required on acceptance: ADR-0004 (group 2 for the new tables, copy ordering),
  ADR-0005 (permissions, scope), ADR-0007 (markers), ADR-0014 (the new optional link column and the
  pointer from "Czego ten dokument nie rozstrzyga"), and ADR-0002 only if Q-3 = C.

## Controls

| Control | Acceptance criterion |
|---|---|
| R-01 | A risk with at least one cost event and at least one reserve is reported double-represented; a risk with only one kind, or none, is not; no rule other than the declared link produces the signal. |
| R-02 | The additional-cost total, the reserve total and every existing `/results`/`/compare` figure of a scenario are byte-identical before and after a double-representation is created or removed by linking. |
| R-03 | A cost event or reserve cannot point at a risk of another scenario (refused by the database, not the application). |
| R-04 | Writes to a risk, a reserve, or a cost event's risk link under an `approved` scenario are refused in the same statement as the status read; a race with approval leaves no row. |
| R-05 | A copied scenario has new identifiers for risks, reserves and cost events; every link points at the copy's own risk; the completeness canary is red when either half (position-level, scenario-level) or the risk entry is missing. |
| R-06 | Every "nothing here for you" case on the new paths answers with one `404` body, before any `409`; no `403` for scope. |
| R-07 | Reserves in more than one currency, or in a currency other than the scenario's declared one, yield `currency_mismatch`, never a partial sum. |
| R-08 | The reserve module does not import the revenue path or the personnel-cost module and is not imported by them (structural test, any import depth). |
| R-09 | Deleting a risk that a cost event or reserve still points at is refused by the database. |

## Related requirements

F-08 (boundary), F-09, F-10 (boundary), F-12, NF-01, NF-10, NF-11, AC-02, AC-04; ADR-0001, ADR-0002,
ADR-0004, ADR-0005, ADR-0006, ADR-0007, ADR-0008, ADR-0014, ADR-0015, ADR-0016; Guardian rules 1, 2,
7, 10, 16, 17.
