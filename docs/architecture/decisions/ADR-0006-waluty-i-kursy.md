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
