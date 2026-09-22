# Architectural decisions — index

One file per decision, `ADR-NNNN-title.md`, numbered sequentially. A decision's status is one of:
`Draft — pending approval` (written by the Architect role, or the Human directly) or `Accepted`
(status change made only by the human — see `../../../TEAM-CONTRACT.md`, section 3, gate 1).

**A decision does not track its own implementation status.** No "Implementation status" field, no
"Done" column, no sentence in the preamble asserting the system does what the decision says.
Whether a decision is actually implemented and proven is tracked in exactly one place:
[`../capabilities.md`](../capabilities.md). This separation is deliberate — a decision means the
direction is approved, not that working code exists (see `../../../FrameworkDoc.md`, section 3,
"What not to confuse").

A later change to an accepted decision is an **addendum with a date and a rationale**, appended to
the same file — never a silent edit of the original text.

## Decisions

| ID | Title | Status |
|---|---|---|
| [ADR-0001](ADR-0001-trwalosc-danych.md) | Trwałość danych backendu (PostgreSQL/SQLAlchemy/Alembic) | Accepted |
| [ADR-0002](ADR-0002-obsluga-pieniedzy.md) | Obsługa pieniędzy: Decimal i jawne zaokrąglenia | Accepted |
| [ADR-0003](ADR-0003-model-modeli-komercyjnych.md) | Model danych dla modeli komercyjnych (F-06) | Draft — pending approval |
| [ADR-0004](ADR-0004-wersjonowanie-kalkulacji.md) | Wersjonowanie i niemutowalność zatwierdzonych kalkulacji | Accepted |
| [ADR-0005](ADR-0005-model-dostepu.md) | Model dostępu i uprawnień | Accepted |
| [ADR-0006](ADR-0006-waluty-i-kursy.md) | Waluty i kursy wymiany | Accepted |
| [ADR-0007](ADR-0007-wspolbiezna-edycja.md) | Współbieżna edycja i ochrona przed zgubioną aktualizacją | Accepted |
| [ADR-0008](ADR-0008-przedzialy-obowiazywania.md) | Przedziały obowiązywania i ich egzekwowanie w bazie | Accepted |
| [ADR-0009](ADR-0009-zapis-z-interfejsu.md) | Zapis z interfejsu przeglądarki | Draft — pending approval |
| [ADR-0010](ADR-0010-awaria-renderu-frontendu.md) | Awaria renderu frontendu: granica błędu i kształt odpowiedzi | Draft — pending approval |
