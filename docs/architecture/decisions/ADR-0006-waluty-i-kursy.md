# ADR-0006 — Waluty i kursy wymiany

**Status:** Accepted

## Kontekst

F-02: scenariusz ma niezależną walutę i kursy wymiany; system musi wskazywać źródło każdej
wartości dziedziczonej lub nadpisanej; zmiana ustawień domyślnych nie modyfikuje zapisanych
kalkulacji. F-06.1 (T&M): zmiany stawek w czasie. Sekcja 6 (MVP proposal): "Manually entered
exchange rates" — kursy automatyczne to potencjalne rozszerzenie, nie MVP. Open decision #5:
które waluty są w ogóle wymagane, jeszcze nie ustalone.

## Decyzja

Tabela `exchange_rates` z przedziałem dat obowiązywania (`valid_from`/`valid_to`, bez nakładania
się dla tej samej pary walut — ograniczenie `EXCLUDE`), wprowadzana ręcznie przez administratora
(MVP — brak integracji z zewnętrznym źródłem, zgodnie z sekcją 6). Każda wartość pieniężna
wyliczona z użyciem kursu przechowuje w wyniku parę (`rate_used`, `rate_source`), gdzie
`rate_source` to `"org_default"` | `"scenario_override"` | `"manual_entry"` — bezpośrednia
realizacja wymogu F-02 "identify the source of each inherited or overridden value". Domyślne
kursy organizacji żyją w osobnej tabeli od kursów przypisanych do scenariusza — scenariusz
kopiuje wartość w momencie użycia (nie referencję), więc zmiana domyślnego kursu nie propaguje
się wstecz (zgodnie z ADR-0004 dla zatwierdzonych kalkulacji, i analogicznie dla draftów w
ramach jednej sesji edycji).

## Konsekwencje

- Formularz wprowadzania kursu jest prostym CRUD-em (NF-10: użytkownicy z uprawnieniami mogą
  dodawać standardowe kursy bez zmiany kodu aplikacji) — bez zależności od zewnętrznego API,
  co upraszcza MVP i unika ryzyka niedostępności zewnętrznego dostawcy.
- Rozszerzenie o automatyczne kursy (sekcja 6, "Automated exchange-rate feeds") to nowe źródło
  zapisujące do tej samej tabeli z `rate_source = "automated_feed"` — bez zmiany schematu ani
  logiki wyliczania, tylko nowy producent wierszy.
- Waluty jako lista otwarta (kod ISO 4217 jako string, nie enum w bazie) — dopóki open decision
  #5 nie zawęzi zakresu, dodanie nowej waluty nie wymaga migracji.

## Rozważane alternatywy

- **Kursy jako pojedyncza wartość bez przedziału dat** — odrzucone: F-06.1 wymaga zmian stawek w
  czasie, a bez przedziału dat nie da się odtworzyć kursu użytego w przeszłej, zatwierdzonej
  kalkulacji (F-12/AC-10).
- **Integracja z automatycznym feedem od razu w MVP** — odrzucone: sekcja 6 dokumentu wymagań
  wprost stawia to jako rozszerzenie po MVP, nie wymaganie startowe.

## Powiązane wymagania

F-02, F-06.1, F-12 (odtwarzalność), sekcja 6 (MVP vs rozszerzenia), open decision #5

## Aneksy

### 2026-09-19 — ujednolicenie nazw kolumn przedziału dat (ADR-0008)

`ADR-0008-przedzialy-obowiazywania.md` ustanawia jeden wzorzec przedziału obowiązywania dla
wszystkich tabel, które go potrzebują (ten ADR, `commercial_terms` z ADR-0003, katalog stawek z
SC-2-01), z nazwą kolumn `effective_from`/`effective_to` — zakotwiczoną w F-03 ("effective date
ranges"). To poprawka nazewnicza wobec `valid_from`/`valid_to` użytych wyżej w tym dokumencie, nie
zmiana mechanizmu: `exchange_rates` przyjmuje tę samą nazwę przy pierwszej migracji, która tę
tabelę tworzy (jeszcze nie zaimplementowana). Semantyka (przedział, brak nakładania dla tej samej
pary walut) zostaje bez zmian.

### 2026-09-30 — Project-level overrides and ordered currency pairs (SC-1-13)

**Status:** Draft — pending approval

> Human Gate 1 decisions confirmed on 2026-09-30: convert all supported result components;
> treat a currency pair as directed and do not infer a reciprocal rate; include project-level
> overrides in the organization → project → scenario hierarchy; use the rate effective in each
> component's own period in a multi-period result.

1. **Project-level exchange-rate overrides are supported.** F-02 permits organization defaults to
   be overridden at project or scenario level. A project override is the middle level between the
   organization default and a scenario override. The most specific applicable saved value wins:
   scenario, then project, then organization. The source is carried as `project_override` in
   addition to the existing `org_default`, `scenario_override`, and `manual_entry` values.
2. **A pair is ordered.** A stored rate applies only from its configured source currency to its
   configured target currency. The system does not invert a rate or silently use a row for the
   reverse pair. A reverse conversion requires its own applicable rate.
3. **Saved scenarios remain reproducible.** When an effective rate is selected for a saved
   scenario, its value and source are copied into the scenario. Later changes to organization or
   project defaults do not alter that saved value, consistent with ADR-0004.
4. **Result completeness is preserved.** All supported revenue and cost components are converted
   to the scenario currency before profitability is calculated. If a required pair has no
   applicable rate, the result is explicitly unavailable; no partial total or implicit 1:1 rate
   is returned.

5. **Effective rates are resolved for each component period.** A multi-period result uses the rate
   effective in each revenue or cost component's own period. A rate change affects the periods it
   covers; the scenario start date does not pin one rate across the entire result.

| Control | Acceptance criterion |
|---|---|
| FX-1 | Every supported result component uses the directed rate for its own period and converts to the scenario currency before aggregation; missing required coverage produces an unavailable result, never a partial total. |
