# ADR-0003 — Model danych dla modeli komercyjnych: reguła scenariusza i Time & Material

**Status:** Accepted

> Treść przepisana i zawężona na bramce 1 SC-4-01 (Issue #8, 2026-09-23), przed pierwszą
> akceptacją — wcześniejsza wersja tego dokumentu nigdy nie miała statusu Accepted, więc jest to
> poprawka Draftu, nie aneks. Rozstrzygnięcia człowieka: P-0 (A), P-1 (A), P-2 (A), P-6, P-7.

## Kontekst

F-06 wymaga czterech modeli komercyjnych i dopuszcza reguły na poziomie projektu, fazy i
workstreamu ("mixed commercial arrangements"). F-02: "Each scenario shall have independent …
revenue rules". F-06.5: "Users shall be able to compare alternative commercial models for the same
planned scope"; "Results shall expose the assumptions on which they depend".

Poprzednia wersja tego Draftu kluczowała regułę polimorficznym `scope_ref` (projekt/faza/workstream)
i ograniczeniem `EXCLUDE` przeciw podwójnemu rozliczeniu. Trzy przeszkody, każda wystarczająca:
(1) reguła bez scenariusza przeczy F-02 — dwa scenariusze jednego projektu nie mogłyby mieć różnych
modeli; (2) encje fazy i workstreamu nie istnieją, a polimorficzna referencja nie może być kluczem
obcym (ADR-0001: integralność w bazie); (3) `EXCLUDE` nie sięga do innej tabeli, więc "chyba że
istnieje reguła łączona" jest niewyrażalne, a równość na `scope_ref` nie łapie nakładania
projekt ↔ faza (reguła 11 Strażnika).

SC-4-01 (Time & Material) jest pierwszym zadaniem bloku 4 i jedynym objętym tą decyzją.
Fundament istnieje i jest dowiedziony: stawka sprzedażowa `catalog_default_rates.default_selling_rate`
z oknem obowiązywania (ADR-0008, SC-2-01/SC-2-03), godziny fakturowalne
`staffing_position_allocation.billable_hours`, niezależne od dostępności i planu (F-04, SC-3-01).
Dziś żadne wyliczenie stawki sprzedażowej nie czyta i żadna migawka jej nie zamraża.

## Decyzja

1. **Reguła komercyjna należy do scenariusza.** Tabela `commercial_terms`: `scenario_id` — klucz
   obcy `NOT NULL`, `UNIQUE` (jedna reguła na scenariusz w MVP), bez `ON DELETE CASCADE` (wzorem
   `staffing_position`: kaskada byłaby drugą, niestrzeżoną drogą zniknięcia wiersza zatwierdzonego
   scenariusza). Zasięg wyłącznie przez `scenario_id → scenarios.project_id` i
   `project_for_caller` — bez nowego mechanizmu (ADR-0001, aneks 2026-09-19 SC-3-01). Brak kolumn
   `scope_ref`, `effective_from`/`effective_to`, `valid_period`, `combined_pricing_rule_ref` i
   ograniczenia `EXCLUDE` — patrz "Odłożone".
2. **Dyskryminator `model_type`, zamknięty do modeli, które mają tabelę szczegółów.**
   `CHECK (model_type IN ('time_and_material'))`. Każde kolejne zadanie modelu rozszerza ten `CHECK`
   w tej samej migracji, w której tworzy swoją tabelę szczegółów — wartość dyskryminatora bez tabeli
   szczegółów byłaby regułą, której wyliczenie nie ma skąd wziąć. `model_type` jest niezmienny po
   zapisie; zmiana modelu reguły jest poza zakresem MVP.
3. **Tabela szczegółów `tm_terms`, 1:1, zgodność typu egzekwowana w bazie.** Klucz główny
   `commercial_terms_id`; kolumna `model_type` z `CHECK (model_type = 'time_and_material')`; złożony
   klucz obcy `(commercial_terms_id, model_type) → commercial_terms (id, model_type)` (po stronie
   `commercial_terms`: `UNIQUE (id, model_type)`). Wiersz szczegółów innego modelu niż reguła, którą
   wskazuje, jest przez to niezapisywalny — ta sama klasa błędu, dla której katalog odrzucił jedną
   tabelę z dyskryminatorem (`app.models.catalog._CatalogDimension`: "a single table would make
   »seniority id in the location column« a valid row"). **Istnienia** wiersza szczegółów baza nie
   wymusza (wymagałoby odroczonego wyzwalacza): reguła `time_and_material` bez `tm_terms` jest
   nazwanym stanem "reguła niekompletna", nigdy przychodem `0`; ścieżka zapisu tworzy oba wiersze
   jedną instrukcją. W MVP `tm_terms` nie niesie kolumn dziedzinowych — istnieje teraz, bo mechanizm
   zgodności typu musi zostać dowiedziony w chwili powstania dyskryminatora, nie przy pierwszym
   polu T&M na tabeli z danymi.
4. **Stawka sprzedażowa: wyłącznie katalog, wyłącznie stawka wewnętrzna.** Dla każdej pozycji obsady
   stawka pochodzi z `catalog_default_rates.default_selling_rate` dla krotki czterech wymiarów
   pozycji i `vendor_id IS NULL` (pozycja nie ma dziś poddostawcy — Issue #9; `NULL` znaczy
   "wewnętrzna", nigdy "dowolna", ADR-0008 aneks 2026-09-21 pkt 6). Bez nadpisania na poziomie
   projektu ani scenariusza: brak potrzeby biznesowej w MVP, a nadpisanie jest założeniem F-02
   wymagającym łańcucha ADR-0012 i przypisania trzech poziomów do grup ADR-0004 — własny wpis przy
   pierwszym zadaniu, które go zażąda. Wyliczenie czyta **tylko** kolumnę stawki sprzedażowej, nigdy
   `default_cost_rate` (reguła 10 Strażnika).
5. **Stawka rozstrzygana per miesiąc alokacji, oknem obejmującym cały miesiąc.** Dla wiersza
   `(pozycja, period_month)` obowiązuje okno, którego `valid_period` zawiera cały miesiąc
   (`valid_period @> daterange(period_month, period_month + 1 miesiąc)`) — ten sam predykat na
   ścieżce żywej i przy zamrażaniu migawki (ADR-0004, aneks 2026-09-23 SC-4-01). Miesiąc bez takiego
   okna — luka w katalogu **albo zmiana stawki w trakcie miesiąca** — jest nazwanym stanem "brak
   stawki" dla tego miesiąca, nigdy stawką wybraną spośród dwóch okien (reguła 13, druga połowa) i
   nigdy `0`. Cena przyjęta świadomie: zmiany stawek sprzedażowych muszą przypadać na granicę
   miesiąca, inaczej miesiąc przejścia jest widocznie nieobliczalny.
6. **Godziny: `billable_hours` z alokacji, dosłownie.** Przychód T&M =
   Σ po pozycjach i miesiącach (`billable_hours` × stawka z pkt 5). `billable_hours` to wpis dla
   całej pozycji — headcount już w nim jest, więc nie mnoży się go drugi raz. Wyliczenie nie
   wyprowadza godzin fakturowalnych z planu, dostępności ani `derived_capacity_hours`, i nie czyta
   flag `absence_type.generates_revenue` — to byłaby druga derywacja tej samej wielkości obok wpisu
   planisty (capabilities: "żadna nie wyprowadzana z drugiej"). Konsumpcja flag przychodowych
   nieobecności wymaga własnego wpisu.
7. **Brak przeliczenia jednostki dnia.** Katalog wymusza `unit = 'hour'` w bazie, alokacja jest w
   godzinach — stawka dzienna w MVP nie istnieje. "Hours in a billable day" (F-06.1) nie dostaje
   pola: ani reużycia `working_calendar.standard_hours_per_day` (to podstawa zdolności kalendarza,
   F-05, nie warunek umowy — zlanie ich sprawiłoby, że zmiana kalendarza przesuwa cenę umowy), ani
   nowej kolumny na `tm_terms` (konwersja bez konsumenta). Warunek wygaśnięcia: pierwsza stawka o
   jednostce innej niż godzina.
8. **Waluta: bez przeliczenia.** Przychód jest w walucie stawek. Jeżeli rozstrzygnięte stawki
   scenariusza mają więcej niż jedną walutę albo walutę inną niż `scenarios.currency` (gdy ta jest
   ustawiona), wynik jest nazwanym stanem "waluty niezgodne" — `exchange_rates` (ADR-0006) nie
   istnieje, a kurs `1:1` byłby zmyśleniem.
9. **Dyspozytor per `model_type`, dwa kształty wyniku.** Funkcja wybierana wyłącznie po
   `model_type`, nigdy po kształcie danych. Zwraca albo wynik
   `(revenue: Decimal, currency: str, assumptions_used)`, albo nazwany stan
   `(reason, assumptions_used)` — "brak reguły", "reguła niekompletna", "brak stawki", "waluty
   niezgodne". Przychód częściowy (suma bez miesięcy bez stawki) jest zakazany: to cicho zaniżony
   przychód. `revenue` w `Decimal` bez zaokrągleń pośrednich; jedyny punkt zaokrąglenia to
   `app.core.money.round_money` na wartości końcowej (ADR-0002, reguła 2). `assumptions_used`
   (F-06.5) nazywa co najmniej: `model_type`, źródło godzin (`billable_hours`), oś poddostawcy
   ("internal"), źródło stawek (żywy katalog albo migawka) i dla każdego użytego okna jego
   identyfikator źródłowy, `effective_from`/`effective_to`, stawkę i walutę; przy nazwanym stanie —
   miesiące i pozycje, które go wywołały.
10. **Scenariusz zatwierdzony czyta stawki z migawki, godziny z własnych tabel** (ADR-0004, aneks
    2026-09-23 SC-4-01). Brak wiersza migawki dla miesiąca zostaje "brakiem stawki" na zawsze.

## Odłożone (każde wymaga własnej decyzji przy swoim zadaniu)

- `fixed_price_terms`, `outcome_terms`, `story_points_terms` — każdy model dostaje własny wpis przy
  swoim zadaniu, wzorem aneksów ADR-0004 per zadanie; open decision #2 (Story Points) nadal otwarta.
- Reguły na poziomie fazy/workstreamu, mieszane umowy, reguła łączona i ochrona przed podwójnym
  rozliczeniem (F-06, F-06.5, reguła 11) — po powstaniu encji fazy; wtedy też rozstrzygnięcie, czy
  przedział obowiązywania reguły (ADR-0008) jest potrzebny obok migawki.
- Porównanie alternatywnych modeli dla jednego zakresu (F-06.5) — dziś przez dwa scenariusze.
- Nadpisania stawek, limity godzin i budżetu, stawki nadgodzinowe/dyżurowe, udział czasu
  fakturowalnego (F-06.1) — pola `tm_terms` przy zadaniu, które ich zażąda.
- Przypisanie przychodu do okresów i termin płatności (F-06.5).
- Koszt, zysk, marża — blok 5/7; blok 4 dowodzi wyłącznie przychodu (AC-01 w części przychodowej).

## Konsekwencje

- `commercial_terms` i `tm_terms` to dane własne scenariusza (grupa 2 ADR-0004): strażnik zapisu w
  tej samej instrukcji co zapis, test wyścigu per ścieżka zapisu, jeden wpis agregatu w
  `SCENARIO_CHILD_COPIERS`. Znacznik współbieżności ADR-0007 na wierszu `commercial_terms` obejmuje
  `tm_terms`, jak znacznik pozycji obejmuje jej miesiące.
- Pierwszy konsument `default_selling_rate` w wyliczeniu i pierwsza migawka stawek — ADR-0004,
  aneks 2026-09-23 SC-4-01.
- Nowy model komercyjny = nowa tabela szczegółów + rozszerzenie `CHECK` + gałąź dyspozytora, bez
  zmiany istniejących tabel (faza expand, ADR-0001).
- ADR-0008 przestaje zaliczać `commercial_terms` do tabel wzorca — aneks 2026-09-23 SC-4-01 tam.

## Rozważane alternatywy

- **Polimorficzny `scope_ref` + `EXCLUDE`** (poprzedni Draft) — odrzucone, patrz "Kontekst".
- **Jedna szeroka tabela ze wszystkimi kolumnami modeli / JSON per model** — odrzucone jak w
  poprzednim Draftcie: kolumny `NULL` zależne od typu i brak `CHECK` na polach JSON (NF-01).
- **Stawka T&M z nadpisaniem scenariusza teraz** — odrzucone: łańcuch ADR-0012 bez potrzeby
  biznesowej; druga cena tej samej roli w systemie bez konsumenta, który ją rozróżnia.
- **Stawka z pierwszego dnia miesiąca** — odrzucone: po cichu stosuje starą stawkę do całego
  miesiąca przejścia (reguła 13).

## Powiązane wymagania

F-02, F-06, F-06.1, F-06.5, NF-01, AC-01 (część przychodowa), AC-04, AC-10; ADR-0001, ADR-0002,
ADR-0004, ADR-0005, ADR-0006, ADR-0007, ADR-0008, ADR-0012; reguły Strażnika 1, 2, 7, 10, 11, 13, 17.

## Aneksy

### 2026-09-23 — rozstrzyganie stawki: jedna cena sprzedażowa na cały miesiąc, nie jedno okno (SC-4-01, bramka 2, R-01)

Pkt 5 w brzmieniu "okno zawierające cały miesiąc" blokował przychód miesiąca, w którym zmieniła się
wyłącznie stawka kosztowa (okno katalogu niesie obie stawki). Rozstrzygnięcie człowieka na bramce 2:
miesiąc (pozycja, miesiąc) jest wyceniony, gdy wewnętrzne okna jego krotki nachodzące na miesiąc
(a) razem pokrywają każdy jego dzień i (b) mają identyczną parę (`default_selling_rate`, `currency`);
wtedy wycena tą stawką. Zmiana stawki sprzedażowej lub waluty w trakcie miesiąca albo luka = nadal
"brak stawki". Ten sam predykat na ścieżce żywej, przy zamrażaniu i w czytelniku migawki; migawka
zamraża wszystkie okna wycenionego miesiąca (ADR-0004, aneks SC-4-01 pkt 2c — "okno" czytać jako
"okna"). Dokładność sumy dni opiera się na `EXCLUDE` (okna jednej krotki się nie nakładają).
Stany nazwane pkt 9 rozszerzone o `unsupported_model_type` (nieznany dyspozytorowi `model_type` —
ochrona przed mieszanymi wersjami kodu podczas wdrożenia kolejnego modelu, ADR-0001 expand→deploy→
contract) i `no_revenue_currency` (reguła bez żadnej wycenionej pozycji i bez `scenarios.currency` —
zastępuje wcześniejsze `currency: null`, które łamało kształt wyniku z pkt 9).

### 2026-09-25 — impact map i decyzje bramki 1 dla SC-4-04 (Story Points, Issue #68)

> Rozstrzygnięcia człowieka (bramka 1, 2026-09-25): **D-1 (A), D-4 (A), D-5 (A), D-6 (A)** — wariant
> najwęższy, zgodny z rekomendacją analityka i architekta w każdym punkcie. Poniższa treść była
> przygotowana jako impact map roli architekta ze statusem "Draft — pending approval"; ten nagłówek
> zamyka ją decyzją.

Czwarty model komercyjny po T&M (SC-4-01), pierwszy wpis z "Odłożone" powyżej (`story_points_terms`).

**Rozszerza się wprost, bez nowej decyzji (mechanizm już generyczny w kodzie, sprawdzone w
`app.data.commercial_terms`):**

1. Dyskryminator i dyspozytor (pkt 2, 9; "Konsekwencje"). `MODEL_TYPES`, `REVENUE_BY_MODEL`,
   `DETAIL_TABLE_BY_MODEL` kluczują się wyłącznie po `model_type`; `_rule_of` i
   `copy_commercial_terms` czytają rejestr, nigdy `tm_terms` po nazwie (R-05, SC-4-01 bramka 2).
   Rozszerzenie `model_type_known` o `'story_points'` w tej samej migracji, która tworzy
   `story_points_terms`, plus jeden wpis w każdym z trzech rejestrów, jest dokładnie wzorcem
   zapowiedzianym w "Konsekwencje". `unsupported_model_type` i refuzja kopiowania (R-02/R-03,
   `CommercialTermsNotCopyable`) przechodzą tym zadaniem z symulacji `monkeypatch`
   (`docs/architecture/capabilities.md`, w. 163) na dowód pierwszym realnym drugim wierszem — to
   jest fundament, który SC-4-04 **ustanawia**, nie taki, na którym może się bezpiecznie oprzeć jako
   już gotowy.
2. Zasięg i uprawnienia (ADR-0005, aneks 2026-09-23 SC-4-01). Punkty 1 ("zasięg bez nowej decyzji…
   404, nigdy 403, także dla zapisu") i 2 (`COMMERCIAL_READ`/`COMMERCIAL_WRITE`) mówią o "regule
   komercyjnej", nie o T&M — obejmują Story Points bez zmian. Punkt 3 (przychód bez koniunkcji z
   `PERSONNEL_COSTS_READ`, dowód równością zbioru pól) jest warunkiem, który nowy kształt odpowiedzi
   SP musi spełnić własnym testem — nie nową decyzją dostępu.
3. Znacznik współbieżności (ADR-0007). `commercial_terms.updated_at` "obejmuje… szczegóły" i jest
   niesiony już dziś "for the first task that adds one" (`app.models.commercial_terms`, docstring) —
   bez zmian, dopóki SP nie dostaje ścieżki edycji (patrz D-5 niżej).
4. Okna obowiązywania (ADR-0008, aneks SC-4-01: `commercial_terms` poza listą tabel wzorca
   `EXCLUDE`/`valid_period`, wersjonowanie mechanizmem ADR-0004). Dotyczy reguły niezależnie od
   modelu — bez zmian, o ile D-1 (niżej) nie wprowadza wariantu wymagającego własnego okna czasowego.

**Aneks do ADR-0004 potrzebny, ale wyłącznie potwierdzający — nie nowy mechanizm.** Punkt 1b aneksu
2026-09-23 SC-4-01 nazywa wprost `tm_terms` ("kopiujący `commercial_terms` kopiuje w tej samej
funkcji `tm_terms`"), choć `copy_commercial_terms` już dziś czyta `DETAIL_TABLE_BY_MODEL[terms
.model_type]`, nigdy `tm_terms` po nazwie — kod jest generyczny, tekst decyzji nie. SC-4-04 wymaga
zdania potwierdzającego, że "grupa 2, strażnik zapisu, jeden wpis agregatu w
`SCENARIO_CHILD_COPIERS`" obowiązuje każdą tabelę szczegółów zarejestrowaną w
`DETAIL_TABLE_BY_MODEL`, nie tylko `tm_terms`, i że `story_points_terms` jest daną własną
scenariusza (strażnik zapisu, bez migawki) — tym samym uzasadnieniem, którym `app.models
.commercial_terms` (docstring, "Own data of the scenario → write guard, not snapshot") już dziś
obejmuje `tm_terms`. **Warunek, pod którym to jest prawdą:** żadna kolumna `story_points_terms` nie
pochodzi z tabeli organizacyjnej edytowalnej po zatwierdzeniu scenariusza. Jeśli D-5 to zmieni
(np. cena punktu jako wartość domyślna organizacji, nie pole reguły), potrzebna jest osobna migawka
wzorem `approved_snapshot_catalog_default_rate` — to osobna decyzja, poza tym aneksem.

**Pytania bramki 1 — rozstrzygnięte 2026-09-25, opcja A w każdym:**

- **D-1 (analityk): wariant "sprint fee" teraz czy później. WYBRANE: Opcja A.**
  - *Opcja A — poza zakresem SC-4-04 (rekomendacja analityka, wybrana).* `story_points_terms` dostaje jeden
    kształt (`price_per_point`, `currency`, pole akceptacji — patrz D-5), `model_type_known` rośnie o
    jedną wartość `'story_points'`. Najtańsza; zgodna z wzorcem "jeden model = jeden wpis CHECK +
    jedna tabela" i z zakresem K-01..K-05.
  - *Opcja B — drugi wariant jako kolumna-dyskryminator WEWNĄTRZ `story_points_terms`
    (`billing_mode`).* Odtwarza defekt, który ADR-0003 już raz odrzucił jednym poziomem wyżej
    ("Rozważane alternatywy": "Jedna szeroka tabela ze wszystkimi kolumnami modeli — odrzucone:
    kolumny `NULL` zależne od typu i brak `CHECK` na polach JSON"). Wymaga własnej decyzji o
    egzekwowaniu zgodności pól w bazie (drugi złożony klucz obcy pod jednym `model_type`, albo
    `CHECK` warunkowy) — mechanizm, którego dziś nic w repo nie ma.
  - *Opcja C — drugi wariant jako osobna wartość dyskryminatora (`'story_points_sprint_fee'`).*
    Reużywa dowiedziony mechanizm bez modyfikacji, kosztem podwojenia zakresu tego zadania (druga
    tabela szczegółów, druga gałąź dyspozytora, drugi kształt żądania) wobec zawężonych kryteriów
    K-01..K-05 analityka.
- **D-5 (architekt, nowe): gdzie żyje "warunek rozliczenia" (zaakceptowane punkty). WYBRANE: Opcja A.**
  - *Opcja A — pojedyncza wartość `accepted_points` wpisywana raz przy tworzeniu reguły, bez ścieżki
    edycji* (jak `tm_terms` — zero kolumn edytowalnych w MVP). Wybrana. Najtańsza, dosłownie realizuje AC-09
    ("cena × zaakceptowane punkty"); zmiana wymaga kopii scenariusza (wzorzec już przyjęty dla całej
    reguły). Koszt: nie odzwierciedla realnej akumulacji punktów sprint po sprincie — nienazwane w
    Issue, warte nazwania jako świadomie przyjęte ograniczenie MVP.
  - *Opcja B — pole edytowalne (nowa ścieżka `PATCH`).* Pierwsza aktywacja znacznika ADR-0007 dla tej
    tabeli i pierwszy edytowalny zapis reguły komercyjnej w repo — większy zakres niż cokolwiek
    SC-4-01 dowiodło.
  - *Opcja C — tabela zdarzeń akceptacji.* Trzeci poziom pod scenariuszem, nowy wpis kopiujący,
    mechanizm poza tym, co ADR-0003/ADR-0004 dziś przewidują — realna nowa decyzja.
- **D-6 (architekt, nowe): kształt żądania/odpowiedzi API** — pierwszy model komercyjny z polami
  dziedzinowymi (`CommercialTermsCreateRequest` dziś ma tylko `model_type`, `extra="forbid"`).
  **WYBRANE: Opcja A.**
  - *Opcja A — unia dyskryminowana po `model_type`. Wybrana.* Wymaga rozszerzenia
    `create_commercial_terms()` o wartości dziedzinowe niesione do TEJ SAMEJ jednej, strażonej
    instrukcji `INSERT … SELECT` (ADR-0004, aneks pkt 1a bez zmian co do zasady). Pierwszy realny
    test drugiej połowy K-04 (wiersz `story_points_terms` odrzucony pod regułą
    `time_and_material`, i odwrotnie).
  - *Opcja B — jeden szeroki kształt z polami opcjonalnymi per model.* Ten sam defekt co D-1/B, na
    granicy API.
  - Niezależnie od wyboru: nowy dowód równości zbioru pól bez kosztu dla wariantu SP — istniejący
    test T&M go nie pokrywa.
- **D-4 (analityk, konsekwencja architektoniczna do zapamiętania). WYBRANE: Opcja A.** Limit
  budżetowy poza zakresem SC-4-04; jeśli wróci w przyszłym zadaniu, pytanie brzmi: nowy nazwany
  stan (`budget_exceeded`) czy ciche przycięcie sumy — to drugie jest tym, co punkt 9 ADR-0003 dziś
  wprost zakazuje ("przychód częściowy jest zakazany").

D-2/D-3 analityka (prognoza z velocity, rozliczenie punktu między sprintami) nie mają konsekwencji
architektonicznej ponad to, co analityk już nazwał (brak encji zespołu/sprintu) — poprawnie usunięte
z zakresu.

**Foundation status.** Dowiedzione: cały mechanizm reguły komercyjnej dla JEDNEGO realnego modelu
(T&M) — zasięg, strażnik zapisu pod `approved`, kopiowanie agregatu, migawka stawek, odmowa
uprawnień, brak pola kosztowego — 581 testów backendowych, PR #64, dwie rundy weryfikacji
(`docs/PLAN.md` SC-4-01, `docs/architecture/capabilities.md` w. 153–163). Tylko planowane /
niedowiedzione: dyspozytor (`REVENUE_BY_MODEL`/`DETAIL_TABLE_BY_MODEL`) na DWÓCH realnych wierszach —
dziś dowiedziony wyłącznie testem z podstawionym `monkeypatch` drugim modelem ("drugi model
komercyjny jeszcze nie istnieje w bazie", capabilities.md w. 163). SC-4-04 jest zadaniem, które to
ustanawia, nie takim, które może na tym polegać jako gotowym. Żadne żądanie/odpowiedź API niosące
pole dziedzinowe modelu nigdy nie istniało w repo — `CommercialTermsCreateRequest` ma dziś dokładnie
jedno pole. ADR-0009 (kontrakt zapisu z UI), choć nieaktywny w tym zadaniu, sam ma status Draft —
pending approval, więc przyszłe zadanie frontendowe SP dziedziczy fundament również niezatwierdzony
na tym poziomie.

**Invariants to watch during implementation:**

- Reguła 10 Strażnika (niezależne wyliczenie per model, brak importu krzyżowego, brak pola
  kosztowego) — nowy moduł formuły SP nie importuje `revenue_time_and_material` ani żadnego modułu
  kosztu; `story_points_terms` nie ma kolumny kosztowej.
- Reguła 2 (jeden punkt zaokrąglenia) — `price_per_point × accepted_points` zaokrąglone wyłącznie
  przez `round_money`, bez zaokrąglenia pośredniego.
- Zakaz przychodu częściowego (ADR-0003 pkt 9) — jeśli `accepted_points`/`price_per_point` mają
  luki, to musi być nazwany stan, nie cichy `0` ani suma pominięć.
- Zgodność typu przez złożony FK (K-04) — pierwszy realny dowód, że wiersz `story_points_terms` pod
  regułą innego `model_type` jest niezapisywalny, i odwrotnie dla `tm_terms`.
- Jedna instrukcja dla obu wierszy (ADR-0004 aneks pkt 1a) — jeśli D-6/A, wartości dziedzinowe SP
  muszą wejść do TEJ SAMEJ guardowanej `INSERT … SELECT`, nie do drugiej, niestrzeżonej instrukcji.
- Kopiowanie przez `SCENARIO_CHILD_COPIERS`, jeden wpis na agregat — kolumny niekopiowane muszą
  wykluczać tylko `commercial_terms_id`/`created_at`; jeśli D-5/A, kopiowanie `accepted_points` na
  nowy DRAFT literalnie duplikuje fakt "tyle już zaakceptowano" na scenariusz, który niczego jeszcze
  nie dostarczył — warte nazwania w raporcie developera, nie ciche.
- Odpowiedź bez pola kosztowego (ADR-0005 pkt 3) — nowy, osobny dowód równością zbioru pól dla
  wariantu SP.

**Required process steps:** README indeksu decyzji (`docs/architecture/decisions/README.md`) — bez
zmian teraz, to aneks do istniejącego ADR-0003, nie nowy plik; jeśli bramka 1 wybierze D-1/C (osobny
`model_type` jako pełnoprawna decyzja projektowa), rozważyć nowy ADR zamiast aneksu, wtedy README
wymaga wiersza. `docs/architecture/architecture-sensitive-paths.md` sprawdzone — bez zmian wymaganych,
istniejące wiersze (`backend/migrations/**`, `backend/app/models/**`, `backend/app/api/**`,
`backend/app/domain/**`, "any new/changed public API endpoint, response field or event") już
pokrywają te ścieżki generycznie. D-1/D-4/D-5/D-6 rozstrzygnięte 2026-09-25 (opcja A wszędzie) —
ten aneks jest teraz wiążący dla migracji tworzącej `story_points_terms`.

### 2026-09-25 — impact mapa bramki 1 dla SC-4-05 (Issue #69, F-06.5): `scope_ref`, reguła łączona, ochrona przed podwójnym rozliczeniem — Draft, pytania otwarte

Drugi aneks tej daty w tym pliku, osobny wpis (konwencja ADR-0004: odwołanie "ADR-0003, aneks
2026-09-25" musi odtąd nazywać zadanie — SC-4-04 albo SC-4-05).

Realizuje bullet "Odłożone": "Reguły na poziomie fazy/workstreamu, mieszane umowy, reguła łączona i
ochrona przed podwójnym rozliczeniem (F-06, F-06.5, reguła 11) — po powstaniu encji fazy" — encja
(ADR-0016, SC-1-11) już istnieje. Kryteria bramki 1 analityka (Issue #69, runda 3): K-01 (rozłączność
zasięgu), K-02 (reguła łączona nierozstrzygnięta — to jest D-3 niżej), K-03 (odtwarzalność
mechanizmem, który model faktycznie używa), K-04 (złożony FK cross-scenario). **Kryteria analityka
nie są tu renegocjowane** — ten aneks wyznacza granice, w jakich muszą się zmieścić, i pytania, które
musi rozstrzygnąć człowiek.

1. **`scope_ref` żyje na `commercial_terms`, nie na tabelach szczegółów.** Zweryfikowane w kodzie
   (`app.models.commercial_terms`): `CommercialTerms` jest jedynym rodzicem `TmTerms` i
   `StoryPointsTerms` (1:1 przez złożony FK na `(id, model_type)`), dokładnie jak `model_type` —
   nowa kolumna zasięgu podąża za tym samym miejscem co dyskryminator, bez duplikowania jej na
   każdym podtypie.
2. **Kształt złożonego FK — fits bez nowej decyzji, ADR-0016 go przewidział.** "`scope_ref` na
   `commercial_terms` (SC-4-05): złożony klucz obcy `(segment_id, scenario_id) →
   scenario_delivery_segment (id, scenario_id)`, analogiczny do `TYPE_AGREEMENT_FOREIGN_KEY`"
   (ADR-0016, "Odłożone") — `commercial_terms.scenario_id` już istnieje (pkt 1 tej decyzji),
   `scenario_delivery_segment` już niesie `UNIQUE (id, scenario_id)` przygotowane dokładnie pod ten
   FK (ADR-0016 pkt 4). K-04 jest więc realizacją już zaprojektowanego kształtu, nie nowym
   projektem — `NULL` = zasięg całego scenariusza (żadnej referencji), niepusta wartość = segment
   TEGO SAMEGO scenariusza, segment innego scenariusza odrzucony przez bazę.
3. **Konflikt z pkt 1 tej decyzji, nienazwany dotąd: `UNIQUE (scenario_id)` na `commercial_terms`.**
   "jedna reguła na scenariusz w MVP" zakłada dokładnie jeden wiersz — K-01/K-02 zakładają WIELE
   wierszy (reguła projektu + reguły segmentów, albo wiele reguł segmentowych) współistniejących pod
   jednym scenariuszem. Te dwa zdania nie mogą być prawdziwe naraz. Rozwiązanie zależy od D-3
   (niżej) i nie jest tu przesądzone — ale każda opcja D-3 wymaga zastąpienia tego ograniczenia czymś
   słabszym (np. częściowym unikalnym indeksem `WHERE scope_ref IS NULL`, albo
   `UNIQUE (scenario_id, scope_ref)` — pamiętając, że PostgreSQL nie traktuje dwóch `NULL` jako
   kolizji, więc sam typ kolumny nie domyka "co najwyżej jedna reguła całego scenariusza" bez
   dodatkowego, jawnego ograniczenia).

**D-1 (analityk, Issue #69 DoD): przypisanie przychodu do okresów i termin płatności — w zakresie
SC-4-05 czy odłożone.**
- *Opcja A — odłożone (rekomendacja).* DoD Issue #69 (dwa zdania) nie wspomina okresów/płatności;
  bullet "Przypisanie przychodu do okresów i termin płatności (F-06.5)" w "Odłożone" tej decyzji
  pozostaje bez przypisanego zadania. Koszt: F-06.5 pozostaje częściowo niezrealizowane kolejny
  blok; ADR-0008 nadal nie rozstrzyga, czy `commercial_terms` (z `scope_ref` czy bez) potrzebuje
  własnego przedziału obowiązywania.
- *Opcja B — SC-4-05 bierze to teraz, bo dotyka tej samej tabeli i tego samego wymagania.* Podwaja
  zakres wobec K-01..K-04, aktywuje ADR-0008 dla `commercial_terms` (mechanizm dziś jawnie
  nieaktywowany — ADR-0008 aneks SC-4-01) jako NOWĄ decyzję dodatkową do `scope_ref`, i miesza dwa
  niezależne pytania: GDZIE reguła obowiązuje (zasięg) i KIEDY przychód jest rozpoznawany (okres) —
  reguła 11 Strażnika mówi wyłącznie o pierwszym.

**D-2 (analityk): porównanie modeli / ujawnianie założeń — już pokryte czy nowy mechanizm.**
Potwierdzone z perspektywy architektury, bez zastrzeżeń: "Porównanie alternatywnych modeli dla
jednego zakresu (F-06.5) — dziś przez dwa scenariusze" (ta decyzja, "Odłożone") — mechanizm istnieje
(F-09/duplikacja scenariusza, ADR-0004) i nie wymaga `scope_ref`. `assumptions_used` różni się już
kształtem per model (pkt 9 tej decyzji dla T&M; `StoryPointsTerms` niesie własny komplet pól) —
"ujawnianie założeń" nie potrzebuje nowego mechanizmu. **Granica, którą trzeba nazwać wprost:**
porównanie-przez-dwa-scenariusze nie daje możliwości porównania modeli WEWNĄTRZ jednego scenariusza
na różnych segmentach — to nie jest D-2, to jest dokładnie D-3 (reguła łączona), osobne pytanie.

**D-3 (architekt: kształt "reguły łączonej"; bez tego K-02 nie da się zaimplementować). GATE —
bramka 1.**
- *Opcja A — brak nowej encji; "reguła łączona" = rozłączność wartości `scope_ref` wymuszona przez
  bazę.* Scenariusz ma ALBO jedną regułę z `scope_ref IS NULL` (dzisiejszy kształt, bez zmian) ALBO
  N reguł, każda z niepustym `scope_ref`, nigdy oba naraz (nowe ograniczenie zastępujące
  `UNIQUE (scenario_id)` z pkt 3 wyżej). Dyspozytor per `model_type` (pkt 9) pozostaje bez zmian per
  wiersz; warstwa wyżej sumuje wyniki N wierszy zamiast czytać jeden — zmiana kontraktu funkcji
  odczytującej regułę(-y) scenariusza, nie dyspozytora. "Reguła łączona" nie jest encją — jest
  niezmiennikiem "żadne dwa wiersze nie mają nakładającego się zasięgu", udowadnianym w bazie.
  Najtańsza opcja, zgodna z regułą 11 Strażnika dosłownie ("dokładnie jedna ścieżka alokacji na
  jednostkę zasięgu").
- *Opcja B — nowa encja `combined_pricing_rule` wiążąca N wierszy `commercial_terms`.* Odtwarza
  dokładnie wariant, który "Kontekst" tej decyzji już raz odrzucił dla oryginalnego `scope_ref`
  ("polimorficzny `scope_ref` + `EXCLUDE`… trzy przeszkody") — tu przeszkody inne (FK nie
  polimorficzny), ale ten sam smak: koszt drugiego poziomu tabeli i własnego zestawu kryteriów
  (osobny ADR-sibling do ADR-0016) bez dzisiejszego konsumenta, który policzyłby realną "cenę
  łączoną" (np. blended rate) — K-01..K-04 tego nie wymagają.
- *Opcja C — rozłączność egzekwowana także MIĘDZY segmentami przez nakładanie w czasie.* Rozszerza
  opcję A o odrzucanie dwóch reguł na segmentach, które się nakładają definicyjnie — ale ADR-0016
  pkt 6 świadomie DOPUSZCZA nakładające się segmenty ("nie błąd do odrzucenia"), a `EXCLUDE` nie
  sięga do innej tabeli (ten sam powód, dla którego "Kontekst" tej decyzji odrzucił oryginalny
  projekt). Wymagałoby wyzwalacza albo walidacji aplikacyjnej — dokładnie klasy mutacji, przed którą
  ADR-0001 każe bronić się w bazie, gdzie to możliwe.
- **Zależność wspólna dla A/B/C, nienazwana w Issue #69: żadna opcja nie oblicza faktycznego
  przychodu per segment bez F-04.** Formuła przychodu T&M (pkt 6) sumuje WSZYSTKIE pozycje/miesiące
  scenariusza — nie zna pojęcia "pozycja należy do segmentu X" (F-04, `staffing_position`↔segment,
  jawnie poza zakresem ADR-0016 pkt 9 i SC-1-11). Rozłączność `scope_ref` (dowolna opcja) jest więc
  dowodliwa na poziomie SCHEMATU (wiersze reguł nie kolidują) już dziś — ale "dokładnie jedna ścieżka
  alokacji na jednostkę zasięgu" (reguła 11) na poziomie PRZYCHODU pozostaje niedowiedlne, bo nie
  istnieje jednostka zasięgu mniejsza niż cały scenariusz, do której przychód dałoby się przypisać.
  To nie jest przeszkoda dla K-04 (FK) ani dla połowy K-01 (odrzucenie mutacji usuwającej predykat
  rozłączności na poziomie wierszy reguł) — jest przeszkodą dla twierdzenia, że SC-4-05 "chroni przed
  podwójnym rozliczeniem" w sensie kwotowym. **Rekomendacja:** nazwać to wprost jako świadome
  ograniczenie zakresu (schema-level teraz, revenue-level po F-04) — ten sam wzorzec co "kształt
  klucza dowiedziony, siła ochronna nie" (ADR-0016, K-03, o `UNIQUE (id, scenario_id)`).

**D-4 (architekt, nowe): kolejność kopiowania w kaskadzie — `copy_commercial_terms` biegnie PRZED
`copy_scenario_delivery_segments`.** Zweryfikowane w kodzie
(`app.data.project_writes.SCENARIO_CHILD_COPIERS`): kolejność dzisiejsza to
`copy_staffing_positions, copy_commercial_terms, copy_scenario_additional_costs,
copy_scenario_delivery_segments`. Każdy kopiujący widzi wyłącznie `(session, source, copy)` — żadnego
kanału współdzielonego mapowania starych-na-nowe identyfikatory między AGREGATAMI (ADR-0004, aneks
2026-09-19 pkt 1: mapowanie żyje lokalnie w domknięciu KAŻDEGO kopiującego z osobna). Jeśli
`commercial_terms.scope_ref` ma wskazywać NOWY (skopiowany) segment, kopiujący reguły komercyjnej
musi znać mapowanie stary-segment-id → nowy-segment-id w chwili, gdy segmenty jeszcze nie istnieją
(kopiowane trzy pozycje w tuple później). K-03/K-06 (odtwarzalność przez kopiowanie) nie da się
dowieść bez rozwiązania tego.
- *Opcja A — przestawić kolejność* (`copy_scenario_delivery_segments` przed `copy_commercial_terms`),
  kopiujący reguł czyta świeżo utworzone segmenty kopii i mapuje `scope_ref` przez dopasowanie
  `(scenario_id=copy.id, name=source_segment.name)` (nazwa jest kopiowana dosłownie i unikalna per
  scenariusz, ADR-0016 pkt 5) — bez zmiany kontraktu `ScenarioChildCopier`. Najtańsza, ale wiąże
  poprawność mapowania z unikalnością nazwy, nie z identyfikatorem — krucha, jeśli kiedyś nazwa
  segmentu przestanie być unikalna.
- *Opcja B — rozszerzyć kontrakt `ScenarioChildCopier` o współdzielony rejestr mapowań id.*
  Bardziej niezawodna (mapowanie po id, nie po nazwie), ale zmienia podpis współdzielony przez
  WSZYSTKIE dzisiejsze wpisy rejestru (`Callable[[Session, Scenario, Scenario], None]` → z dodatkowym
  parametrem) — większy koszt wsteczny niż jedna zmiana kolejności.
  Żadna z opcji nie jest tu rozstrzygnięta — obie są zgodne z K-03/K-06, wybór jest kosztem
  implementacyjnym, nie rozstrzygnięciem bramki 1 wymagającym człowieka w tym samym sensie co D-1..
  D-3, ale wymaga jawnego wyboru przed napisaniem migracji, żeby nie zostać odkrytym dopiero
  czerwonym testem na bramce 2.

**Foundation status.** Dowiedzione: mechanizm reguły komercyjnej dla DWÓCH modeli (T&M, Story
Points) — zasięg, strażnik zapisu, dyspozytor, kopiowanie jednego wiersza na scenariusz (T&M: 581+
testów, bramka 3 zamknięta; Story Points: kod na `main`, bramka 3 W TOKU, brak wpisu w
`capabilities.md`). `scenario_delivery_segment` — schemat, strażnik zapisu, kopiowanie — bramka 3
ZAMKNIĘTA (migracja `b1f4e8a3c95d`), ale K-03 tamtej decyzji jest jawnie ograniczone do KSZTAŁTU
klucza, nie jego siły ochronnej ("dowiedzie dopiero SC-4-05, pierwszy konsument"). Tylko
planowane/niedowiedzione: `scope_ref` sam (nie istnieje), rozłączność zasięgów (schemat), reguła
łączona (D-3, żadna opcja wybrana), F-04 (pozycja↔segment — nie istnieje, poza zakresem tego
zadania, blokuje przychód segmentowy niezależnie od wyboru D-3).

**Invariants to watch during implementation:**
- Reguła 11 Strażnika (dokładnie jedna ścieżka alokacji na jednostkę zasięgu) — patrz ograniczenie
  nazwane w D-3: dowiedlna dziś wyłącznie na poziomie wierszy reguł, nie na poziomie przychodu.
- Reguła 13 (okno efektywności odrzuca nakładanie na zapisie) — jeśli D-1 wróci do zakresu,
  `commercial_terms` aktywuje ADR-0008 po raz pierwszy; dziś nieaktywny (ADR-0008 aneks SC-4-01).
- Reguła 17 (kopiowanie scenariusza nie zostawia współdzielonej referencji) — D-4 jest dokładnie tym
  pytaniem dla `scope_ref`.
- Pkt 3 tego aneksu (złożony FK zgodności typu, wzorzec K-04) — test musi dowieść odrzucenia
  segmentu INNEGO scenariusza, nie tylko istnienia FK (ten sam wymóg co dla `TYPE_AGREEMENT_
  FOREIGN_KEY`, pkt 3 "Decyzji").
- Pkt 1 "Decyzji" `UNIQUE (scenario_id)` — mutacja usuwająca to ograniczenie bez zastąpienia go
  czymkolwiek jest dokładnie luką, przed którą broni D-3; test musi dowieść, że NIE MOŻNA zapisać
  dwóch reguł z `scope_ref IS NULL` dla jednego scenariusza, niezależnie od wybranej opcji D-3.

**Required process steps.** Brak nowego pliku w `docs/architecture/decisions/` — to aneks do
istniejącej, zaakceptowanej decyzji (ten wpis) plus krótki aneks zamykający do
`ADR-0016-segment-dostawy-scenariusza.md`. README indeksu (`docs/architecture/decisions/README.md`)
bez zmian (żaden nowy plik, żadna zmiana statusu). `docs/architecture/architecture-sensitive-paths.md`
zaktualizowany tym samym zadaniem — `backend/app/models/**` i `backend/app/data/**` nie wymieniały
dotąd ADR-0003 ani ADR-0016 wprost, mimo że `commercial_terms.py`/`app.data.commercial_terms` i
`scenario_delivery_segment.py`/`app.data.scenario_delivery_segment` leżą dokładnie w tych ścieżkach —
poprawione tym wpisem.

### 2026-09-25 — bramka 1 SC-4-05 — decyzja człowieka

Zaakceptowano rekomendacje bez zastrzeżeń: **D-1=A, D-2=potwierdzone (bez nowego mechanizmu),
D-3=A, D-4=A**.

- D-1=A: przypisanie przychodu do okresów i termin płatności (F-06.5, pierwsza połowa) pozostaje
  odłożone, poza zakresem SC-4-05; DoD Issue #69 (dwa zdania: rozłączność zasięgu + odtwarzalność)
  jest kompletnym, testowalnym zakresem bez tego. Bullet "Przypisanie przychodu do okresów..." w
  "Odłożone" wyżej pozostaje bez przypisanego zadania.
- D-2=potwierdzone: porównanie modeli (F-09/duplikacja scenariusza) i ujawnianie założeń
  (`assumptions_used` per model) pozostają bez zmian — SC-4-05 nie dodaje tu nowego kryterium.
- D-3=A: "reguła łączona" nie jest nową encją. Rozłączność wartości `scope_ref` (co najwyżej jedna
  reguła z `scope_ref IS NULL` per scenariusz; dowolna liczba reguł z niepustym, wzajemnie różnym
  `scope_ref`) jest wymuszona ograniczeniem w bazie, zastępującym `UNIQUE (scenario_id)` z pkt 1
  "Decyzji" wyżej. Zależność wspólna nazwana wyżej (żadna opcja nie liczy przychodu per segment bez
  F-04) pozostaje świadomym ograniczeniem zakresu — SC-4-05 dowodzi rozłączności na poziomie
  SCHEMATU (K-01/K-02), nie na poziomie PRZYCHODU; to idzie wprost do "Out of scope" SC-4-05 w
  `docs/PLAN.md`.
- D-4=A: `copy_scenario_delivery_segments` przestawione przed `copy_commercial_terms` w
  `SCENARIO_CHILD_COPIERS`; kopiujący reguł mapuje `scope_ref` przez `(scenario_id=copy.id,
  name=source_segment.name)`. Kontrakt `ScenarioChildCopier` bez zmian. Notatka w kodzie przy tym
  dopasowaniu: opcja B (rejestr mapowań id) jest właściwym rozwiązaniem, gdy nazwa segmentu
  przestanie być unikalna.

Przechodzę do fazy `code`.

### 2026-09-25 — model Outcome-based: `outcome_terms`, cztery kategorie wyniku, przychód gwarantowany i oczekiwany (SC-4-03, Issue #67, bramka 1)

Rozstrzygnięcia zaakceptowane przez człowieka na bramce 1, 2026-09-25 (D-1..D-9, P-1..P-5 mapy
wpływu SC-4-03). Sekcja "Odłożone" zapowiada `outcome_terms` jako własny wpis przy własnym zadaniu —
to jest ten wpis. `fixed_price_terms` pozostaje odłożone i wiążą go elementy wspólne z pkt 10
niżej. `story_points_terms` ma własny aneks wyżej (SC-4-04, Issue #68) i został scalony do `main`
przed SC-4-03 — integrację obu modeli rozstrzyga pkt 12 (uzgodnienie po merge z `main`,
2026-09-25, decyzja człowieka). Podstawa: F-06.3, AC-08, F-06.5.

1. **Tabela szczegółów `outcome_terms` — wzorzec pkt 3 bez zmian.** Klucz główny
   `commercial_terms_id`; kolumna `model_type` z `CHECK (model_type = 'outcome_based')`; złożony
   klucz obcy `(commercial_terms_id, model_type) → commercial_terms (id, model_type)`. Wartość
   dyskryminatora `outcome_based` dochodzi do `CHECK model_type_known` w tej samej migracji, która
   tworzy tabelę (pkt 2). Istnienia wiersza szczegółów baza nie wymusza — reguła `outcome_based` bez
   `outcome_terms` jest nazwanym stanem "reguła niekompletna", nigdy przychodem `0`; ścieżka zapisu
   tworzy oba wiersze jedną instrukcją. Pierwsza tabela szczegółów z kolumnami dziedzinowymi.
2. **Wynagrodzenie w MVP (D-1): opłata stała, premia binarna warunkowa, stawka za jednostkę,
   min/max.** Opłata stała obowiązkowa; premia, stawka za jednostkę, minimum i maksimum
   opcjonalne. **Składnik opcjonalny nieobecny = `NULL`, nigdy `0`** — `0` jest wartością wpisaną
   przez użytkownika i znaczy co innego niż brak składnika (dla min/max: "ogranicz do zera" ≠ "bez
   ograniczenia"). `CHECK (min <= max)`, gdy oba ustawione; kwoty i stawka nieujemne. Premia jest
   wypłacana dla kategorii "osiągnięty" i "przekroczony", **nie** dla "częściowy" i
   "nieosiągnięty". Formuła częściowego osiągnięcia, udział w korzyści, progi wielostopniowe i kary
   — poza MVP (wymagania nie podają formuły); każde wymaga własnego wpisu.
3. **Cztery stałe kategorie wyniku jako kolumny `outcome_terms` (D-3/P-2 wariant A).**
   `not_achieved` / `partial` / `achieved` / `exceeded` — każda z liczbą osiągniętych jednostek
   (`>= 0`, wpisana ręcznie) i opcjonalnym prawdopodobieństwem. Nie osobna tabela kategorii: zbiór
   jest zamknięty, a ograniczenie sumy prawdopodobieństw musi być wyrażalne jako `CHECK` jednego
   wiersza (ADR-0001: integralność w bazie; `CHECK` nie sięga do innych wierszy).
   **Uzupełnienie po rundzie weryfikacji 1 (2026-09-25, decyzja człowieka):** liczby jednostek są
   nullowalne, gdy stawka za jednostkę jest `NULL` — reguła bez składnika jednostkowego nie wymaga
   wpisywania jednostek, a brak jednostek nie jest udawany zerem (`NULL` ≠ `0`, pkt 2).
   `CHECK` w bazie: `unit_rate IS NOT NULL` → wszystkie cztery liczby jednostek `NOT NULL`.
4. **Prawdopodobieństwa (D-2): procenty, `NUMERIC(5,2)`, suma dokładnie `100.00`.** `CHECK` w
   bazie: "wszystkie cztery `NULL` albo wszystkie `NOT NULL` i suma = 100". Bez tolerancji, bez
   normalizacji, bez uzupełniania brakującej kategorii. Wartość z trzecim miejscem po przecinku —
   `422` na granicy API, **nigdy** zaokrąglenie po cichu (zaokrąglenie zmieniałoby sumę, którą
   użytkownik uważa za sprawdzoną). Odrzucenie nie zapisuje żadnego wiersza (ani `commercial_terms`,
   ani `outcome_terms`).
   **Ograniczenie przyjęte świadomie (runda weryfikacji 1, 2026-09-25, decyzja człowieka):**
   kolumny `NUMERIC(5,2)` i `NUMERIC(14,4)` tej tabeli przy zapisie z pominięciem API po cichu
   zaokrąglają nadmiarowe miejsca po przecinku (zachowanie PostgreSQL) — nadmiar precyzji odrzuca
   (`422`) wyłącznie granica API, nie baza. Dziś nie istnieje żadna ścieżka zapisu poza API.
   **Warunek ponownego otwarcia:** pierwsza ścieżka zapisu omijająca API (import, skrypt, migracja
   danych) — wtedy odrzucenie nadmiaru precyzji musi przejść do bazy albo do tej ścieżki.
5. **Dwa przychody, dwa pola (D-4/P-1 wariant A).**
   a. **`RevenueResult.revenue` / `amount` = przychód gwarantowany**: opłata stała + zero
      składnika zmiennego, po ograniczeniu min/max (pkt 6). To jedyna wartość, od której liczą zysk
      `/results` (SC-7-01) i porównanie scenariuszy (SC-6-02) — znaczenie istniejącego pola się nie
      zmienia: "przychód, na który scenariusz może liczyć".
   b. **Przychód oczekiwany — nowe pole addytywne**, obok przychodów per kategoria (również nowe
      pola). Oczekiwany = Σ pₖ·rₖ liczone z **niezaokrąglonych** przychodów kategorii, zaokrąglone
      **raz**, na końcu, przez `app.core.money.round_money` (ADR-0002, pkt 9 tego ADR).
   c. **Brak prawdopodobieństw → nazwany stan przychodu oczekiwanego**, nigdy `0` i nigdy kopia
      gwarantowanego. Przychód gwarantowany i per kategoria są wtedy nadal podawane.
   d. Zysk/marża oczekiwana — poza zakresem (blok 7, osobne Issue). Zmiana pól `RevenueRead` jest
      addytywna (ADR-0009); żadne istniejące pole nie zmienia typu ani znaczenia — **z jednym
      nazwanym wyjątkiem** (runda weryfikacji 1, 2026-09-25, decyzja człowieka): pola
      `hours_source`, `vendor_axis` i `rate_source` w `assumptions_used` poszerzają zbiór wartości o
      `not_applicable` (pkt 8). Wartości dla T&M bez zmian; poszerzenie enumu jest zmianą kontraktu
      dla klienta, który zna zamknięty zbiór — skutek przyjęty w pkt 8.
6. **Min/max ograniczają cały przychód (D-5).** Ograniczenie stosuje się do sumy opłaty stałej i
   składnika zmiennego danej kategorii — i do przychodu gwarantowanego, który jest przez to
   `max(min, …)` nawet przy zerowym składniku zmiennym. Nie do samego składnika zmiennego.
7. **Waluta reguły (D-7/P-4 wariant A).** Kolumna `currency` na `outcome_terms` (ISO-4217, `CHECK`
   jak w katalogu: `char_length(currency) = 3`, wielkie litery). Waluta różna od
   `scenarios.currency` (gdy ta jest ustawiona) → nazwany stan `currency_mismatch` (pkt 8), nigdy
   kwota i nigdy przeliczenie — `exchange_rates` (ADR-0006) nadal nie istnieje.
   **Uzupełnienie po rundzie weryfikacji 1 (2026-09-25, decyzja człowieka):** wyniki złożone
   (`/results` SC-7-01, what-if SC-6-04, porównanie SC-6-02) wymagają równości waluty przychodu i
   waluty **każdego** wyliczonego źródła kosztu (koszt osobowy, nieobecności płatne, koszty
   dodatkowe); w przeciwnym razie `profit`/`margin`/`markup` = nazwany stan `currency_mismatch`,
   nigdy liczba z sumy różnych walut. Reguła zamyka również istniejący wcześniej przypadek koszt
   dodatkowy ≠ waluta kosztu osobowego — nie tylko nowy przypadek waluty reguły outcome. Zasada
   "nazwij źródło, nie zwijaj" (ADR-0002, aneks 2026-09-24 SC-7-01) obowiązuje bez zmian; wpis w
   ADR-0002 — aneks 2026-09-25 SC-4-03. **Kształt (decyzja człowieka 2026-09-25, runda 2):**
   stan niesie nowe, addytywne pole `profitability_state` (`calculated` | `not_applicable` |
   `currency_mismatch`) poza bramką kosztu osobowego — nie niesie liczby; pola liczbowe przy
   niezgodności mają `"n/a"`, bo walidator kształtu frontendu (`isGatedResultFieldShape`)
   przyjmuje tam tylko `null`/`"n/a"`/liczbę. Frontend do czasu Issue frontendowego pokazuje
   `"n/a"` bez przyczyny (ograniczenie pkt 8).
8. **`rate_source = not_applicable` (P-3 wariant B).** Model bez katalogu stawek nie ma źródła
   stawek, więc `assumptions_used.rate_source` dostaje nową wartość `not_applicable` zamiast
   udawania `live_catalog`/`approved_snapshot`. Obowiązek przyjęty razem z tą wartością, wzorem
   `what_if_hypothetical` (ADR-0015, SC-6-04): **jawny przegląd każdego miejsca porównującego
   `rate_source` przez równość**; w szczególności strażnik wyścigu `/results`
   (`ScenarioResultsRaceDetected`) porównuje wyłącznie źródła zależne od statusu scenariusza
   (`live_catalog`/`approved_snapshot`) — `not_applicable` (i `story_points_terms`, pkt 12) nie
   jest dowodem ani braku, ani wystąpienia wyścigu. Test wyścigu dla obu
   modeli: zatwierdzony scenariusz outcome → `200`, prawdziwy wyścig zatwierdzenia T&M → nadal
   `409`. `assumptions_used` nie może też nazywać źródła, którego wyliczenie outcome nie czyta
   (godziny `billable_hours`, oś poddostawcy) — F-06.5 wymaga założeń faktycznie użytych.
   Uściślenie (runda weryfikacji 1, 2026-09-25, decyzja człowieka): dla outcome
   `assumptions_used.hours_source`, `vendor_axis` i `rate_source` przyjmują `not_applicable`;
   wartości dla T&M bez zmian. To jest nazwany wyjątek od pkt 5d.
   **Ograniczenie przyjęte świadomie, datowane:** do zadania frontendowego (D-9, osobne Issue) ekran
   SC-4-06 pokazuje scenariusz outcome jako błąd odczytu sekcji (kontrakt frontendu zna dwie
   wartości `rate_source`) i nie pokazuje przychodu oczekiwanego — tymczasowe odstępstwo od F-06.3
   "displayed separately". **Rozszerzenie (runda weryfikacji 1, 2026-09-25, decyzja człowieka):**
   to samo ograniczenie obejmuje sekcję wyników SC-7-02 (zysk/marża/koszty) dla scenariusza
   outcome — nie tylko sekcję przychodu SC-4-06; nowe wartości `not_applicable` w założeniach nie są
   znane kontraktowi frontendu. Warunek zamknięcia dla obu sekcji: Issue frontendowe SC-4-03.
9. **Zakres zapisu (P-5 wariant C): tylko tworzenie.** Edycja i usunięcie reguły outcome — osobne
   zadanie. `model_type` pozostaje niezmienny po zapisie (pkt 2). Znacznik współbieżności ADR-0007
   na `commercial_terms` obejmuje `outcome_terms` ("Konsekwencje") — konsumowany dopiero przez
   zadanie edycji.
10. **Elementy wspólne bloku 4 — wiążące kolejne zadania modeli (SC-4-02 Fixed Price, #66).**
    Brzmienie z bramki 1 zakładało, że SC-4-03 idzie przed SC-4-04 (Story Points, #68) — SC-4-04
    zostało jednak scalone do `main` pierwsze; skutki dla punktów a i c rozstrzyga pkt 12
    (uzgodnienie po merge z `main`, 2026-09-25, decyzja człowieka). Każde kolejne zadanie modelu
    przyjmuje bez ponownego rozstrzygania:
    a. **Konwencja wartości `rate_source` dla modelu bez katalogu stawek** (brzmienie po pkt 12):
       model bez katalogu stawek używa wartości `rate_source` **spoza** źródeł zależnych od statusu
       scenariusza (`live_catalog`/`approved_snapshot`), z tym samym obowiązkiem przeglądu porównań
       i testem wyścigu `/results` dla nowego modelu; strażnik wyścigu `/results` porównuje
       wyłącznie źródła zależne od statusu. Outcome używa `not_applicable`, Story Points —
       `story_points_terms`. Model, który czyta katalog, używa istniejących wartości i nie dostaje
       nowej.
    b. **Wzorzec rozszerzania testu K-11 SC-4-01**: zbiór pól wyniku rozszerzany jawnie o nazwane
       nowe pola, zatwierdzone na bramce 1 danego zadania; **równość zbioru zostaje** (nigdy
       osłabienie do "zawiera").
    c. **Wzorzec migracji rozszerzającej `CHECK model_type_known`**: każda kolejna migracja
       odtwarza ograniczenie z **pełną listą `IN`**, łącznie z wartościami wcześniejszych modeli
       (`time_and_material`, `story_points`, `outcome_based`, …) — migracja niosąca tylko własną wartość po cichu
       unieważnia zapisane reguły innych modeli przy następnej walidacji ograniczenia. Downgrade
       odtwarza listę sprzed migracji, nie listę jednoelementową. Strażnik dryfu
       (`test_commercial_terms_schema.py::test_the_model_and_the_migration_agree_on_every_sql_expression`)
       porównuje stałą modelu z **najnowszą** migracją odtwarzającą to ograniczenie
       (`LATEST_MODEL_TYPE_CHECK_MIGRATION_PATH`) — równość zostaje; kolejny model przepina tę
       ścieżkę na swoją migrację i dokłada test, że jego downgrade odtwarza listę poprzedniej
       (wzór `test_outcome_terms_schema.py`). Decyzja człowieka 2026-09-25 (zatrzymanie na
       czerwonym teście przy SC-4-03).
       **Uzupełnienie po rundzie weryfikacji 1 (2026-09-25, decyzja człowieka):** testy danej
       migracji porównują z **własną, zamrożoną w teście listą tej migracji**, nie ze stałą modelu,
       która przesunie się przy kolejnym modelu. "Nieznany model" w testach to wartość-wartownik,
       **nigdy nazwa realnego przyszłego modelu** (dziś `fixed_price`; `story_points` jest już
       wdrożonym modelem, pkt 12), bo taki test
       zmienia znaczenie w chwili wdrożenia tego modelu. `_details_of` odmawia dla nieznanego typu
       payloadu, zamiast go przepuszczać. Istniejący test SC-4-01 używający `'fixed_price'` jako
       nieznanego modelu przepina na wartownika SC-4-02 — jawnie, na swojej bramce 1.
    d. Pkt 1 (zgodność typu złożonym kluczem obcym, "reguła niekompletna"), pkt 2 zdanie o `NULL` ≠
       `0` dla składników opcjonalnych, pkt 7 (waluta reguły, jeśli model niesie kwoty wpisane
       wprost) — ten sam kształt.
    Odstępstwo od któregokolwiek z a–d wymaga datowanego aneksu w tym ADR, nie rozstrzygnięcia w
    zadaniu.
11. **Odczyt reguły niesie jej parametry (runda weryfikacji 1, 2026-09-25, decyzja człowieka).**
    Odpowiedź `GET` reguły komercyjnej dla `outcome_based` niesie parametry wynagrodzenia: opłatę
    stałą, premię, stawkę za jednostkę, minimum, maksimum, walutę oraz per kategoria liczbę
    jednostek i prawdopodobieństwo — reguły, której parametrów nie da się odczytać, nie da się
    zweryfikować (F-06.5). Zbiór pól reguły w teście K-11 SC-4-01 rozszerzony jawnie o te pola, wg
    pkt 10b — **równość zbioru zostaje**.
12. **Uzgodnienie po merge z `main` (2026-09-25, decyzja człowieka, runda weryfikacji 2).**
    SC-4-04 (Story Points) zostało scalone do `main` przed SC-4-03, z własną wartością
    `rate_source = "story_points_terms"` i własną migracją `d2f6a91c4b58`, która rozszerza
    `CHECK model_type_known` do `('time_and_material', 'story_points')`. Pkt 10 z bramki 1 wiązał
    SC-4-04 wartością `not_applicable` — to brzmienie jest nieaktualne. Rozstrzygnięcia:
    a. **Konwencja pkt 10a w nowym brzmieniu:** model bez katalogu stawek używa wartości
       `rate_source` spoza źródeł zależnych od statusu (`live_catalog`/`approved_snapshot`);
       konkretna wartość należy do modelu. Outcome — `not_applicable`; Story Points zachowuje
       `story_points_terms` (bez zmiany kontraktu scalonego SC-4-04).
    b. **Strażnik wyścigu `/results` (`ScenarioResultsRaceDetected`) porównuje wyłącznie źródła
       zależne od statusu.** To zamyka również **istniejący na `main` defekt SC-4-04**: strażnik
       porównywał `rate_source` zwykłą równością z wartością oczekiwaną dla statusu scenariusza, więc
       każdy scenariusz Story Points (`story_points_terms` ≠ `live_catalog`/`approved_snapshot`)
       dostawał `409` na `/results`, what-if i porównaniu scenariuszy — niezależnie od tego, czy
       wyścig zaszedł. Dowód: nowe testy `backend/tests/test_story_points_scenario_results.py`
       (scenariusz Story Points szkicowy i zatwierdzony → `200`; prawdziwy wyścig zatwierdzenia
       T&M → nadal `409`).
    c. **Migracja pkt 10c:** migracja SC-4-03 `b9e3c7a1f264` jest zlinearyzowana na
       `d2f6a91c4b58` i odtwarza `CHECK model_type_known` z pełną listą `IN` trzech wartości
       (`time_and_material`, `story_points`, `outcome_based`); downgrade odtwarza listę
       `d2f6a91c4b58` (dwie wartości). `LATEST_MODEL_TYPE_CHECK_MIGRATION_PATH` wskazuje
       `b9e3c7a1f264`.
    d. **Strażnik dryfu SC-4-04 przepięty wzorem R-02** (pkt 10c, uzupełnienie rundy 1): test
       `test_story_points_terms.py` (ok. w. 511), który porównywał migrację `d2f6a91c4b58` ze stałą
       modelu, porównuje teraz migrację z **jej własną, zamrożoną listą** (dwie wartości), a zgodność
       stałej modelu z najnowszą migracją sprawdza strażnik oparty na
       `LATEST_MODEL_TYPE_CHECK_MIGRATION_PATH`. To nie jest osłabienie testu: pokryte są oba fakty
       (lista migracji SC-4-04 i lista najnowsza) — decyzja człowieka, nie rozstrzygnięcie w zadaniu.
13. **Uzgodnienie po merge z SC-4-05 (2026-09-25, decyzja człowieka).** SC-4-05 (Issue #69, PR
    #119; aneksy wyżej "impact mapa bramki 1 dla SC-4-05" i "bramka 1 SC-4-05 — decyzja
    człowieka") zostało scalone do `main` przed SC-4-03. Jego tekst powstał, gdy istniały tylko T&M
    i Story Points ("mechanizm reguły komercyjnej dla DWÓCH modeli") — nie jest przepisywany;
    uzgodnienie idzie tym punktem.
    a. **`scope_ref` dotyczy reguł `outcome_based` tak samo jak reguł pozostałych modeli.** Kolumna
       `scope_ref`, złożony klucz obcy `(scope_ref, scenario_id) → scenario_delivery_segment (id,
       scenario_id)`, dwa częściowe indeksy unikalne i przemapowanie `scope_ref` przy kopiowaniu
       (SC-4-05, D-3=A, D-4=A) żyją na `commercial_terms`, nie na tabeli szczegółów (aneks SC-4-05,
       pkt 1) — `outcome_terms` nie dostaje własnej kolumny zasięgu. Przychód outcome jest liczony z
       własnego wiersza reguły (pkt 2–4), nie z danych współdzielonych przez cały scenariusz, więc —
       jak Story Points — `outcome_based` nie należy do modeli, dla których kilka reguł jednego
       modelu w scenariuszu daje dowiedlnie tę samą odpowiedź (`app.data.commercial_terms`,
       `_MODEL_TYPES_WITH_SHARED_SCENARIO_REVENUE`: "A model joins this set only when its formula is
       checked to have the same shared-data property T&M has — not by default"). Ograniczenie SC-4-05
       "rozłączność na poziomie SCHEMATU, nie PRZYCHODU" (F-04 poza zakresem) obejmuje outcome bez
       zmian.
    b. **Migracja:** `b9e3c7a1f264` zlinearyzowana na `b7e3f19a6c52` (SC-4-05, która sama stoi na
       `d2f6a91c4b58`); historia ma jedną głowę. `b7e3f19a6c52` nie dotyka `CHECK
       model_type_known`, więc lista `IN` trzech wartości przy upgrade i downgrade do listy dwóch
       wartości ustanowionej przez `d2f6a91c4b58` (pkt 12c, kontrola O-6) obowiązują bez zmian;
       `LATEST_MODEL_TYPE_CHECK_MIGRATION_PATH` nadal wskazuje `b9e3c7a1f264`. Sformułowanie pkt 12c
       "zlinearyzowana na `d2f6a91c4b58`" opisuje stan sprzed merge z SC-4-05.
    c. **Pkt 12 obowiązuje bez zmian** (konwencja `rate_source`, strażnik wyścigu `/results`,
       przepięty strażnik dryfu SC-4-04).
    d. **Otwarte, warunek wstępny zadania wprowadzającego `scope_ref` do API** (decyzja
       człowieka 2026-09-25): pkt 5 zakłada jedną regułę na scenariusz (`amount` = przychód
       gwarantowany = podstawa zysku w `/results`). SC-4-05 dopuszcza N reguł per segment, ale
       odpowiada jedną wartością per model (`revenue_by_model_type`). Dla scenariusza z regułą
       outcome i regułą innego modelu nie jest rozstrzygnięte, co jest przychodem gwarantowanym
       w `/results` ani jak łączyć przychody oczekiwane. Dziś stan nieosiągalny (żaden schemat
       żądania nie niesie `scope_ref`, ADR-0016 pkt 8); rozstrzygnięcie należy do zadania, które
       go udostępni. To samo zadanie musi rozstrzygnąć odpowiedź zapisu (uwaga reviewera,
       runda 4): `create_commercial_terms` zatwierdza regułę z `scope_ref`, a potem odczyt
       `_view_of` → `_rule_of` rzuca `MultipleCommercialRulesNotSupported`, gdy scenariusz ma
       już inną regułę — zapis trwały, wołający dostaje błąd (zachowanie odziedziczone z SC-4-05).

Relacja do ADR-0004: `outcome_terms` — grupa 2, aneks 2026-09-25 SC-4-03 tam.

| Kontrola | Kryterium akceptacji |
|---|---|
| O-1 | `INSERT` z pominięciem API odrzucany przez bazę dla: sumy prawdopodobieństw ≠ 100.00, zestawu niepełnego (część `NULL`), `min > max`, ujemnej liczby jednostek, wiersza `outcome_terms` wskazującego regułę innego `model_type`; kontrast 33.34/33.33/33.33/0.00 zapisywalny. |
| O-2 | AC-08: opłata 20000 PLN + premia 10000 PLN → 20000 dla "nieosiągnięty" i "częściowy", 30000 dla "osiągnięty"; przychód gwarantowany 20000 niezależnie od prawdopodobieństw; oczekiwany 23000 (70/30) i 29000 (10/90); brak prawdopodobieństw → nazwany stan oczekiwanego, nie `0` i nie 20000. |
| O-3 | Opłata 20000, 100 PLN/j., min 22000, max 30000: 50 j. → 25000, 150 j. → 30000, 0 j. → 22000, gwarantowany 22000; `min > max` odrzucone (`422`, zero wierszy). |
| O-4 | Reguła w walucie innej niż `scenarios.currency` → `currency_mismatch`, bez kwoty w żadnym polu przychodu. |
| O-5 | `/results` zatwierdzonego scenariusza outcome → `200`, zysk liczony od przychodu gwarantowanego, `rate_source = not_applicable`; prawdziwy wyścig zatwierdzenia scenariusza T&M → `409` bez zmian. |
| O-6 | Migracja niesie w `CHECK model_type_known` pełną listę wartości (`time_and_material`, `story_points`, `outcome_based`), downgrade odtwarza listę `d2f6a91c4b58`; istniejące reguły T&M i Story Points przechodzą upgrade i downgrade; kopiujący reguł bez gałęzi dla `model_type` prawdziwego wiersza odmawia `unsupported_model_type`/`409`, a nie kopiuje połowy agregatu. |
| O-7 | `/results`, what-if i porównanie: waluta przychodu różna od waluty któregokolwiek wyliczonego źródła kosztu (w tym koszt dodatkowy ≠ koszt osobowy przy regule T&M) → `profit`/`margin`/`markup` = `currency_mismatch`, bez liczby. |
| O-8 | `GET` reguły outcome zwraca opłatę, premię, stawkę za jednostkę, min, max, walutę i per kategoria jednostki oraz prawdopodobieństwo; zbiór pól reguły w teście K-11 równy rozszerzonemu zbiorowi. |
| O-9 | `INSERT` z pominięciem API: `unit_rate` ustawione przy choć jednej liczbie jednostek `NULL` odrzucone przez bazę; `unit_rate = NULL` z jednostkami `NULL` zapisywalne. |
| O-10 | Scenariusz outcome: `assumptions_used.hours_source`, `vendor_axis`, `rate_source` = `not_applicable`; dla T&M wartości bez zmian. |
| O-11 | `/results`, what-if i porównanie scenariusza Story Points (szkicowego i zatwierdzonego) → `200`, `rate_source = story_points_terms`, bez `409`; prawdziwy wyścig zatwierdzenia T&M → `409` bez zmian. |
| O-12 | Test migracji `d2f6a91c4b58` porównuje ją z własną zamrożoną listą (dwie wartości); stała modelu jest równa liście migracji wskazanej przez `LATEST_MODEL_TYPE_CHECK_MIGRATION_PATH` (`b9e3c7a1f264`, trzy wartości). |

### 2026-09-25 — `rate_source` przychodu to deskryptor zależny od modelu, nie świadek statusu (SC-7-03, Issue #118, bramka 1, Q3/B)

Punkt 9 mówi o "źródle stawek (żywy katalog albo migawka)"; aneks SC-4-04 nie odnotował, że
kontrakt przychodu (`backend/app/api/schemas/commercial_terms.py`, `assumptions_used.rate_source`)
ma już trzecią wartość `story_points_terms`. Rejestr tego dokumentu rozjechał się z kontraktem, a
brak tego zapisu był przyczyną błędu #118: strażnik wyścigu porównywał `rate_source` przychodu z
`rate_source` kosztu tak, jakby oba były tym samym słownikiem.

> Uzgodnienie po merge z `main` (SC-4-03, PR #120), decyzja człowieka 2026-09-25: Q4 zmienione z A
> na B (ADR-0015, aneks SC-7-03). Pkt 1–2 niżej przepisane w tym samym dniu, przed scaleniem SC-7-03 —
> wpis nie był jeszcze częścią `main`; wcześniejsze brzmienie ("porównywanie nie może sterować
> logiką") wykluczało klasyfikację z pkt 2 i przeczyło pkt 12b wyżej.

1. `assumptions_used.rate_source` przychodu to deskryptor F-06.5 **zależny od modelu**: T&M zgłasza
   `live_catalog` / `approved_snapshot` (stawka sprzedażowa z katalogu albo migawki, pkt 4–5), Story
   Points — `story_points_terms` (dana własna reguły, bez katalogu i bez migawki), Outcome-based —
   `not_applicable` (aneks SC-4-03, pkt 8). Odpowiedzi bez modelu albo z modelem nieobsługiwanym
   (`no_commercial_terms`, `unsupported_model_type`) i niekompletna reguła T&M zgłaszają wartość
   wybraną ze statusu (`live_catalog` / `approved_snapshot`). Każdy kolejny model (Fixed Price #66)
   dopisuje tu swoją wartość w tym samym zadaniu, w którym rozszerza kontrakt — zgodnie z konwencją
   aneksu SC-4-03, pkt 10a/12a.
2. Słownik `rate_source` przychodu i słownik `rate_source` kosztu (ADR-0013, ADR-0015 pkt 4) to **dwa
   odrębne słowniki**, które dzielą dwie wartości. **Wartości z jednego nie porównuje się z
   wartościami z drugiego.** `rate_source` przychodu wolno użyć do jednej decyzji logicznej:
   klasyfikacji, czy przychód zależy od statusu scenariusza (`rate_source ∈ STATUS_DEPENDENT_SOURCES`
   — `live_catalog`, `approved_snapshot`). To jest uściślenie pkt 8, 10a i 12b aneksu SC-4-03, nie
   odstępstwo od ich semantyki: strażnik wyścigu nadal uwzględnia przychód wyłącznie wtedy, gdy jego
   źródło zależy od statusu, i nadal nie traktuje `story_points_terms` ani `not_applicable` jako
   dowodu wyścigu ani jego braku. **Zmienia się mechanizm (odstępstwo od brzmienia pkt 10a/12b, zapis
   wymagany zdaniem końcowym pkt 10):** strażnik nie porównuje `rate_source` przychodu z `rate_source`
   kosztu, lecz statusy scenariusza zamrożone przez trzy odczyty (przychód, koszt osobowy, koszt
   dodatkowy) — reguła w ADR-0015, aneks SC-7-03, pkt 2. Zdania "strażnik porównuje wyłącznie źródła
   zależne od statusu" w pkt 8, 10a i 12b czyta się odtąd jako "strażnik uwzględnia przychód
   wyłącznie, gdy jego źródło należy do źródeł zależnych od statusu". Obowiązek pkt 10a (wartość spoza
   zbioru dla modelu bez katalogu, test wyścigu `/results` dla nowego modelu) bez zmian; dochodzi
   jawna klasyfikacja każdej nowej wartości do zbioru albo poza niego (ADR-0015, aneks SC-7-03, pkt
   7).
3. Reguła 10 Strażnika bez zmian: poprawka strażnika nie może skłonić przychodu modelu niezależnego od
   katalogu do czytania statusu ani stawek katalogu tylko po to, by zgłosić wartość porównywalną z
   kosztem.
