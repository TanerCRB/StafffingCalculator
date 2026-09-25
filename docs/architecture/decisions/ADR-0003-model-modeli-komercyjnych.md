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
