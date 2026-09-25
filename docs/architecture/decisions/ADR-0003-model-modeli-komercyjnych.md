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
