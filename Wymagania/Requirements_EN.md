# IT Project Profitability and Staffing Planner — Requirements

Version: 1.0 — Draft for review  
Date: 2026-09-18  
Language: English

## 1. Purpose

The application will help Project Managers at an IT outsourcing company prepare staffing plans, estimate project costs and revenue, assess profitability, and present the financial impact of alternative delivery configurations.

Each calculation must be highly configurable and support alternative scenarios, additional expenses, and project risks.

## 2. Scope and assumptions

The application is a web-based planning tool supporting multiple projects and multiple independent calculation scenarios per project.

The required commercial models are:

- Time & Material (T&M).
- Fixed Price.
- Outcome-based.
- Story Points.

The initial scope covers forecasting and planning. Tracking actual hours, costs, and revenue against the baseline is a potential extension requiring confirmation.

**Unconfirmed assumption:** the Story Points model means payment per accepted point. A fixed fee per sprint with a points commitment is a possible alternative, but has not yet been confirmed.

The MVP proposal and numerical non-functional targets below are recommendations for agreement, not previously approved commitments.

## 3. Definitions

| Term | Definition |
|---|---|
| Project | A client engagement containing calculations and scenarios. |
| Scenario | An independent set of staffing, schedule, cost, revenue, and risk assumptions. |
| Staffing position | Planned demand for a role or named person over a defined period. |
| FTE | Full-time equivalent allocation; conversion into hours depends on the applicable working calendar. |
| Billable time | Time eligible for charging to the client under the commercial agreement. |
| Velocity | Forecast Story Points completed by a particular team per sprint. |
| Profit | Revenue less the costs included in the calculation. |
| Margin | Profit divided by revenue, expressed as a percentage. |
| Markup | Profit divided by cost, expressed as a percentage. |

## 4. Functional requirements

### F-01. Projects and calculations

- The system shall allow users to create, edit, copy, and archive projects.
- Each project shall include a name, client, owner, delivery period, reporting currency, and description.
- A project shall support multiple scenarios.
- Users shall be able to save incomplete calculations as drafts.
- The system shall identify missing inputs and shall not present incomplete results as ready for approval.

### F-02. Configurable assumptions

- Each scenario shall have independent start and end dates, delivery phases, working calendars, full-time working hours, currencies, exchange rates, cost rules, revenue rules, target margin, warning thresholds, escalation assumptions, and risk reserves.
- The system shall provide organization-level defaults that users can override for a project or scenario.
- The system shall identify the source of each inherited or overridden value.
- Changes to default settings shall not automatically modify saved calculations.
- Users shall be able to configure changes in rates and costs over time.

### F-03. Roles and resources

- Users shall be able to define roles, seniority levels, locations, engagement types, and default cost and selling rates.
- Staffing plans shall support anonymous roles without requiring named employees.
- Assigning a named person shall be optional.
- Rates shall support effective date ranges.

> **Addendum 2026-09-21 (SC-2-03, Issue #46) — the subcontractor is a dimension of a rate.**
> Users shall be able to define subcontractors and to record default cost and selling rates per
> subcontractor, for the same role / seniority / location / engagement type tuple and the same
> effective date range as the organisation's own rate. A rate naming no subcontractor is the
> organisation's own ("internal") rate; the two are distinct rows and neither substitutes for the
> other. Decided by the business at gate 1 of SC-2-03 and written down here because the four
> bullets above do not imply it — the original F-03 does not mention subcontractors, and they
> appear elsewhere in this document only as a cost category (F-08). Deliberately left undecided:
> contract terms per subcontractor, named people on the subcontractor's side, and any narrowing of
> who may see which subcontractor's price list (see ADR-0005, addendum 2026-09-21).

### F-04. Staffing planning

- Users shall be able to add roles or people, specify headcount, and plan allocation in hours or FTE.
- Allocation shall be editable across periods and delivery phases.
- The plan shall support partial months, onboarding, and handover periods.
- The system shall distinguish resource availability, planned allocation, and billable time.
- The system shall warn when planned allocation exceeds the configured available capacity.
- Non-billable effort shall be included in cost calculations according to the configured cost basis.

### F-05. Working calendars and absences

- Calculations shall account for working days, public holidays, leave, and other planned absences.
- The cost and revenue impact of each absence type shall be configurable.
- Paid leave may generate a cost without generating revenue.
- Different team locations shall be able to use different working calendars.

### F-06. Commercial models and revenue calculation

The system shall support all four required commercial models. A model may apply to an entire project, a delivery phase, or a workstream, allowing mixed commercial arrangements.

Personnel and additional costs shall be calculated independently of the revenue model.

#### F-06.1. Time & Material

Revenue shall be calculated from billable time and applicable selling rates.

Configuration shall include:

- Hourly or daily selling rates by role or person.
- The number of hours in a billable day.
- The proportion of planned time eligible for billing.
- Billable time and budget caps, including rules for exceeding them.
- Separate rates for overtime, on-call duties, and work outside standard hours.
- Rate changes over time.

**Base formula:** revenue = sum of billable time units multiplied by their applicable rates.

#### F-06.2. Fixed Price

Revenue shall be based on an agreed price for a defined scope.

Configuration shall include:

- The price of the entire project or individual milestones.
- Scope and acceptance criteria.
- Planned effort and schedule.
- Separately chargeable scope changes.
- Bonuses, penalties, and price adjustments.
- Rules for assigning planned revenue to reporting periods.

The system shall show the profitability impact of delays, cost increases, and effort overruns.

**Base formula:** revenue = agreed price + approved adjustments.

Increasing effort or staffing shall not automatically increase revenue.

#### F-06.3. Outcome-based

Revenue shall depend on agreed business or operational outcomes.

Configuration shall include:

- Outcome definitions, measurement units, and data sources.
- Baseline values, target values, and measurement periods.
- Evidence and acceptance conditions for confirming outcomes.
- A fixed fee component, where applicable.
- Variable compensation using fixed outcome payments, unit rates, thresholds, or a share of the measured benefit.
- Minimum and maximum compensation.
- Rules for partial achievement, bonuses, and penalties.

The system shall support scenarios for missing, partially achieving, achieving, and exceeding a target.

Users may assign probabilities to alternative outcomes and calculate expected revenue and profit. When used, probabilities shall total 100% for a complete set of mutually exclusive alternatives.

**Base formula:** revenue = fixed component + outcome compensation calculated under the configured rule.

Expected results shall be displayed separately from guaranteed compensation.

#### F-06.4. Story Points

Subject to confirmation of the assumption in Section 2, revenue shall depend on the number of Story Points meeting the agreed billing conditions.

Configuration shall include:

- Price per Story Point.
- Planned points per sprint or reporting period.
- Forecast team velocity.
- Sprint length and team composition.
- Conditions for billing points, such as client acceptance.
- Treatment of unfinished, rejected, and carried-over work.
- Rules for rework and revised estimates.
- Budget caps and optional pricing tiers.

**Base formula:** revenue = billable Story Points multiplied by their applicable prices.

Delivery costs shall come from the staffing plan and additional costs. The system shall not assume a universal conversion between Story Points and hours. Forecasts shall use velocity configured for the specific team and scenario.

#### F-06.5. Shared commercial rules

- The system shall distinguish revenue calculation, allocation of planned revenue to periods, and payment timing.
- Users shall be able to compare alternative commercial models for the same planned scope.
- Results shall expose the assumptions on which they depend.
- Mixed arrangements shall prevent the same work or outcome from being charged twice unless an explicit combined pricing rule requires it.
- Commercial rates and terms shall be versioned.

### F-07. Personnel costs

- The system shall support hourly, daily, and monthly personnel costs.
- Users shall be able to add bonuses, benefits, employer on-costs, and other personnel cost components.
- Each position shall have a configurable cost basis: worked time, allocated FTE, or a fixed amount.
- The system shall distinguish a base rate from a fully loaded cost to prevent duplicate overhead allocation.

### F-08. Additional costs

- Users shall be able to define cost categories, including recruitment, equipment, licenses, cloud services, travel, training, subcontracting, and management.
- Costs shall support one-off and recurring charges.
- Charges may be fixed or depend on headcount, FTE, hours, or a specified percentage base.
- Costs may be assigned to a project, phase, or staffing position.
- The system shall distinguish supplier-funded expenses from expenses recharged to the client.
- Each cost shall have a currency and an applicable period.
- Cost breakdowns shall make overlapping charges, overheads, and reserves visible for review.

### F-09. Scenarios and sensitivity analysis

- Users shall be able to duplicate a scenario and modify the copy independently.
- The system shall compare at least three scenarios by staffing, revenue, cost, profit, and margin.
- Sensitivity analysis shall show the impact of changes such as salary increases, reduced billable utilization, delayed starts, and exchange-rate movements.
- Risks may be represented by explicit cost events or reserves, with the selected method visible.
- The system shall make it possible to identify when the same risk is represented in both an explicit event and a reserve.

### F-10. Results and metrics

The system shall show the following for the complete project and each reporting period:

- Revenue.
- Personnel and additional costs.
- Profit.
- Margin and markup.
- Planned hours and FTE.
- Billable time as a share of planned time.
- Deviation from the target margin.

Formulas:

- Profit = revenue − included costs.
- Margin = profit / revenue × 100%.
- Markup = profit / included costs × 100%.

The system shall identify which costs are included in each profitability metric. A metric with a zero denominator shall display “Not applicable” rather than an invalid numerical value.

### F-11. Visualization and reporting

The system shall provide:

- A staffing timeline.
- Revenue, cost, and profit charts over time.
- A cost breakdown.
- Scenario comparisons.
- Indicators for negative profit and margins below the configured target.

Users shall be able to export summaries to PDF and detailed data to a spreadsheet. Reports shall identify the scenario, version, generation date, and key assumptions.

### F-12. History and reproducibility

- The system shall retain calculation versions and a change history identifying the author, time, and affected data.
- Approved versions shall be immutable; further changes shall require a new version or copy.
- Reports shall be reproducible using saved inputs, exchange rates, calendars, and calculation rule versions.

### F-13. Access control

- The system shall support administrator, calculation author, and read-only viewer roles.
- Access shall be restrictable to selected projects.
- Permission to view individual personnel costs shall be separable from permission to view aggregate results.
- Access restrictions shall also apply to exports and server interfaces.

## 5. Non-functional requirements

The numerical targets in this section are proposed acceptance targets, subject to agreement.

| ID | Area | Requirement |
|---|---|---|
| NF-01 | Calculation correctness | Monetary calculations shall use decimal arithmetic and explicit rounding rules. Results shall pass an agreed reference dataset covering all commercial models. |
| NF-02 | Explainability | Users shall be able to trace aggregate results to their components, inputs, and calculation rules. |
| NF-03 | Performance | For a scenario with 200 staffing positions and 36 months, 95% of recalculations shall complete within two seconds in an agreed test environment. |
| NF-04 | Data protection | Data shall be encrypted in transit and at rest. Authorization shall be enforced on the server. |
| NF-05 | Reliability | The application shall autosave and display save status. Concurrent edits shall not silently overwrite another user's changes. |
| NF-06 | Recovery | The proposed recovery point objective is 24 hours and recovery time objective is eight hours. Restore procedures shall be tested periodically. |
| NF-07 | Usability | Users shall be able to create their first calculation from a template without configuring the complete organization catalog. Forms shall explain input units and validation errors. |
| NF-08 | Accessibility | Core operations shall be usable with a keyboard. Color shall not be the only means of communicating status or meaning. |
| NF-09 | Browser support | The application shall work in agreed versions of Chrome, Edge, and Firefox. Desktop shall be the primary editing environment; mobile shall support viewing summaries. |
| NF-10 | Configurability | Users with appropriate permissions shall be able to add cost categories, roles, calendars, and standard rates without changing application code. |
| NF-11 | Diagnostics | Errors and failed saves shall be logged without exposing confidential rates or personal information in diagnostic logs. |

## 6. Proposed release scope

### MVP proposal

- Projects and independent scenarios.
- Anonymous roles and monthly staffing plans, including partial periods.
- All four required commercial models, with configurable standard calculation rules.
- Personnel and additional costs.
- Manually entered exchange rates.
- Basic scenario comparisons, charts, and sensitivity calculations.
- PDF and spreadsheet export.
- Saved versions and basic access control.

### Potential later extensions

- HR and time-tracking integrations.
- Baseline versus actual cost, effort, and revenue tracking.
- Cross-project capacity planning.
- Formal approval workflows.
- Automated exchange-rate feeds.
- Cash-flow forecasts based on invoice and payment schedules.

Profitability and cash flow shall be treated as separate concepts. A profitability result alone does not establish whether sufficient cash will be available at a particular time.

## 7. Acceptance criteria

| ID | Scenario | Expected result |
|---|---|---|
| AC-01 | 100 billable hours at PLN 200 per hour, personnel cost PLN 12,000, and additional cost PLN 2,000. | Revenue PLN 20,000; total cost PLN 14,000; profit PLN 6,000; margin 30%. |
| AC-02 | Staffing is changed in a duplicated scenario. | The source scenario remains unchanged. |
| AC-03 | A one-off equipment cost is assigned to a specific period. | The cost appears exactly once in that period. |
| AC-04 | An organization's default role rate changes. | An approved calculation retains its saved rate and results. |
| AC-05 | Revenue is zero. | The system displays the profit or loss amount and marks margin as not applicable. |
| AC-06 | A user lacks permission to view individual personnel costs. | The restricted information is unavailable in the UI, exports, and server interfaces. |
| AC-07 | A Fixed Price project has revenue PLN 150,000 and its cost increases from PLN 100,000 to PLN 120,000. | Revenue remains PLN 150,000 and profit falls to PLN 30,000. |
| AC-08 | An outcome-based agreement has a fixed fee of PLN 20,000 and a PLN 10,000 bonus payable only when the target is achieved. | Revenue is PLN 20,000 when the target is missed and PLN 30,000 when achieved. |
| AC-09 | The price is PLN 1,000 per accepted Story Point and 25 points are accepted. | Revenue is PLN 25,000; unaccepted points generate no revenue under this billing rule. |
| AC-10 | A report is regenerated for an approved version after defaults have changed. | Calculated values match the approved version using its saved assumptions and rules. |

## 8. Open decisions

1. Does the tool cover planning only, or also monitoring actual delivery against the plan?
2. Does Story Points pricing mean payment per accepted point, a fixed sprint fee with a points commitment, or both?
3. Which outcome-based measures and payment rules occur in the organization's contracts?
4. Is monthly planning sufficient for the first release, or is weekly or sprint-level editing required?
5. Which currencies, working calendars, user volumes, and deployment environment are required?
6. Should calculations use net amounts only, and is tax treatment or formal accounting revenue recognition in scope?

