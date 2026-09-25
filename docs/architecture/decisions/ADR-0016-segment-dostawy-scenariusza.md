# ADR-0016 — Segment dostawy scenariusza: encja fazy/workstreamu (F-02, F-06)

**Status:** Draft — pending approval

> Projekt przygotowany przez rolę Architekta na bramkę 1 SC-1-11 (Issue #65). Kierunki Q1 (F-04
> poza zakresem), Q2 (bez API HTTP), Q3 (jedna tabela, bez dyskryminatora) i Q4 (`SCENARIO_CHILD_
> COPIERS` + strażnik zapisu `approved` w tym zadaniu) zaakceptowane przez człowieka 2026-09-25
> (rekomendacje architekta z impact-mapy SC-1-11, bez zastrzeżeń) — treść poniżej już je
> odzwierciedla. Q5 (nowa, dedykowana decyzja zamiast aneksów rozproszonych) jest tym, czego ten
> dokument jest realizacją.

## Kontekst

F-02: "Each scenario shall have independent … delivery phases …". F-06: "The system shall support
all four required commercial models. A model may apply to an entire project, a delivery phase, or
a workstream, allowing mixed commercial arrangements." F-06.5: "Users shall be able to compare
alternative commercial models for the same planned scope."

`ADR-0003-model-modeli-komercyjnych.md` (Accepted, SC-4-01) rozstrzygnął regułę komercyjną T&M
wyłącznie na poziomie scenariusza i jawnie odłożył wszystko poniżej tego poziomu do chwili, gdy
"encja fazy/workstreamu" zacznie istnieć: "Reguły na poziomie fazy/workstreamu, mieszane umowy,
reguła łączona i ochrona przed podwójnym rozliczeniem (F-06, F-06.5, reguła 11) — po powstaniu
encji fazy; wtedy też rozstrzygnięcie, czy przedział obowiązywania reguły (ADR-0008) jest potrzebny
obok migawki" (ADR-0003, "Odłożone"). Ten sam dokument nazwał już raz i odrzucił próbę zbudowania
tej encji jako polimorficznej referencji: "reguła bez scenariusza przeczy F-02 … encje fazy i
workstreamu nie istnieją, a polimorficzna referencja nie może być kluczem obcym (ADR-0001:
integralność w bazie) … `EXCLUDE` nie sięga do innej tabeli" (ADR-0003, "Kontekst").

SC-1-11 (Issue #65) jest zadaniem, które wprowadza tę encję — i tylko ją. Logika mieszanych modeli
komercyjnych, ochrona przed podwójnym rozliczeniem (SC-4-05) i alokacja obsady per faza (F-04) są
jawnie poza zakresem (Issue #65, "Out of scope (explicit)"; impact-mapa SC-1-11, Q1).

## Decyzja

1. **Jedna tabela, `scenario_delivery_segment`, bez dyskryminatora (Q3 = A).** "Delivery phase" i
   "workstream" nie mają w `Requirements_EN.md` odrębnego zachowania — F-06 wymienia je jako
   równoważne alternatywy tego samego zasięgu ("a delivery phase, or a workstream"), a F-02, F-04 i
   F-08 posługują się wyłącznie terminem "delivery phase". Nazwa tabeli jest neutralna wobec obu
   słów z wymagań i spójna z istniejącym słownictwem domeny (`Project.delivery_period_start/end`).
   Nie ma dziś ani jednego miejsca w kodzie, które miałoby rozgałęzić się po tym, czy wiersz jest
   "fazą" czy "workstreamem" — dodanie kolumny-dyskryminatora bez konsumenta byłoby tym samym
   ryzykiem spekulacyjnej konstrukcji, przed którym broni się `ADR-0003` pkt 2 dla `model_type`
   ("wartość dyskryminatora bez tabeli szczegółów byłaby regułą, której wyliczenie nie ma skąd
   wziąć"). Rozróżnienie, jeśli kiedyś będzie potrzebne semantycznie (nie tylko etykietą), jest
   migracją fazy expand dodającą kolumnę do istniejącej tabeli — nie wymaga przebudowy referencji.
2. **Miejsce w hierarchii: dziecko scenariusza, `scenario_id` `NOT NULL`, jeden poziom pod
   scenariuszem.** Kolumna `scenario_id` (`UUID`, klucz obcy `fk_scenario_delivery_segment_
   scenario_id → scenarios.id`, bez `ON DELETE CASCADE` — ten sam powód co `staffing_position` i
   `commercial_terms`: kaskada byłaby drugą, niestrzeżoną drogą zniknięcia wiersza zatwierdzonego
   scenariusza). Przynależność segmentu do scenariusza jest kolumną na wierszu segmentu, nie tabelą
   asocjacyjną — jeden scenariusz ma wiele segmentów (1:N), jeden segment ma dokładnie jednego
   rodzica (kryterium K-01/K-02 analityka, Issue #65).
3. **Kolumny: `id`, `scenario_id`, `name`, `created_at`, `updated_at` — nic ponadto.**
   - `id`: `UUID`, klucz główny.
   - `name`: `VARCHAR(200) NOT NULL`, z `CHECK (name ~ '[^[:space:]]')` nazwanym
     `scenario_delivery_segment_name_not_blank` — ten sam wzorzec co `projects.name`/`client`/
     `owner` (`ADR-0001`, migracja `4f0a9c1b7d62`): `NOT NULL` samo w sobie nie odrzuca `''` ani
     samych spacji.
   - `created_at`, `updated_at`: `TIMESTAMPTZ NOT NULL`, `updated_at` z `onupdate=func.now()`.
     **Własny token współbieżności (ADR-0007)**, nie pożyczony od scenariusza: segment jest
     jednostką własnej edycji (utworzenie, zmiana nazwy, usunięcie), tak jak `commercial_terms` ma
     własny token, a nie token scenariusza — wzorzec generyczny ADR-0007 ("token współbieżności
     należy do wiersza, który jest jednostką jednej edycji"), zastosowany tu wprost, bez nowego
     aneksu.
   - **Świadomie nieobecne:** kolumna kolejności/numeru segmentu (nazwa użytkownika niesie
     porządek, jeśli go potrzebuje — "Faza 1", "Faza 2" — bez wymuszania go w schemacie);
     `effective_from`/`effective_to` lub jakikolwiek przedział dat (ADR-0008 nieaktywowany — patrz
     pkt 6); jakakolwiek kolumna kwotowa, walutowa, godzinowa lub alokacyjna (K-05 analityka);
     `position_id` lub inne powiązanie z pozycją obsady (Q1 = A, F-04 poza zakresem).
4. **`UNIQUE (id, scenario_id)`, nazwany `uq_scenario_delivery_segment_id_scenario_id` (K-03).**
   Redundantne jako twierdzenie o unikalności (`id` samo jest kluczem głównym) i wymagane mimo to:
   PostgreSQL akceptuje złożony klucz obcy tylko przeciw ograniczeniu unikalności na dokładnie tych
   kolumnach, które referencjonuje. Ten sam wzorzec co `uq_commercial_terms_id_model_type`
   (ADR-0003 pkt 3) i `uq_staffing_position_id_scenario_id` (ADR-0014 pkt 1, SC-5-05) — przygotowuje
   grunt pod przyszły złożony klucz obcy `(segment_id, scenario_id) → scenario_delivery_segment (id,
   scenario_id)` z tabeli, która dostanie `scope_ref` (SC-4-05). Kryterium dowodzi wyłącznie
   *kształtu* klucza, nie jego siły ochronnej przeciw referencji cross-scenario — to udowodni
   dopiero SC-4-05 jako pierwszy konsument, dokładnie jak dla `staffing_position` (ADR-0014,
   docstring `app.models.staffing`).
5. **`UNIQUE (scenario_id, name)`, nazwany `uq_scenario_delivery_segment_scenario_id_name`.** Dwa
   segmenty tego samego scenariusza o identycznej nazwie są tym samym rodzajem błędu, przed którym
   broni `UniqueConstraint("project_id", "name")` na `scenarios` — "dwa scenariusze jednego
   projektu są dwoma niezależnymi wierszami, nie dwoma widokami jednego wiersza"; tu: dwa segmenty
   jednego scenariusza są dwoma niezależnymi wierszami, a powtórzona nazwa nie ma dziś żadnego
   znaczenia różnicującego, które commercial_terms mogłoby rozstrzygnąć jednoznacznie przy wskazaniu
   `scope_ref`. Ograniczenie jest zawężone do `scenario_id` — kopiowanie scenariusza (pkt 8) nadaje
   każdej kopii nowy `scenario_id`, więc nie koliduje nigdy z wierszami źródła.
6. **ADR-0008 nieaktywowany — bez przedziału obowiązywania.** Wzorzec `EXCLUDE`/`valid_period`
   istnieje w tym repozytorium dla stawek, kursów i reguł komercyjnych, których wyszukiwanie musi
   rozstrzygać jeden wiersz spośród wielu w danym momencie (reguła 13 Strażnika). Segment nie jest
   takim wyszukiwaniem: nakładające się w czasie segmenty (dwie równoległe fazy, workstream biegnący
   przez cały okres dostawy) są stanem, który przyszła reguła komercyjna może zechcieć wyrazić, nie
   błędem do odrzucenia — ten sam wniosek co dla okresu `staffing_position` (ADR-0004, aneks
   2026-09-19 SC-3-01: "nakładające się pozycje tej samej roli w tym samym okresie są legalne …
   brak `EXCLUDE` … jest poprawnością, nie pominięciem"). Czy segment w ogóle potrzebuje własnego
   okresu dat jest pytaniem odłożonym do pierwszego konsumenta, który go zażąda — patrz "Odłożone".
7. **Grupa 2 tabel-dzieci scenariusza (ADR-0004), aneks towarzyszący w tym samym dniu.** Segment
   jest strukturalną, własną daną scenariusza — nic spoza scenariusza jej nie zmienia — więc
   kwalifikuje się do grupy 2 ("własna → strażnik zapisu, nie migawka") tak samo jednoznacznie jak
   `staffing_position`, `commercial_terms` i `additional_cost`. Pełna treść przypisania (Q4 = A: w
   tym samym zadaniu, jeden wpis w `SCENARIO_CHILD_COPIERS`, bez wpisu w `SNAPSHOT_TABLES`) jest
   spisana jako datowany aneks do `ADR-0004-wersjonowanie-kalkulacji.md`, nie tutaj — ta decyzja
   ustanawia encję i jej kształt, ADR-0004 pozostaje jedynym miejscem klasyfikacji tabel-dzieci
   scenariusza.
8. **Bez API w tym zadaniu (Q2 = A).** SC-1-11 tworzy schemat, model ORM i minimalną funkcję
   warstwy danych potrzebną do spełnienia obowiązku ADR-0004 (kopiowanie, strażnik zapisu) — żaden
   endpoint HTTP nie wystawia segmentu na zewnątrz. Nazwa i zakres uprawnień odczytu/zapisu
   (`ADR-0005`) pozostają nierozstrzygnięte do zadania, które doda pierwszy endpoint (prawdopodobnie
   SC-4-05 albo dedykowany ekran zarządzania fazami) — ten sam wzorzec co komentarz przy `tm_terms`:
   "istnieje teraz, bo mechanizm … musi zostać dowiedziony w chwili powstania …, nie przy pierwszym
   polu … na tabeli z danymi", odwrócony: tu istnieje schemat, zanim istnieje jego pierwszy
   zewnętrzny czytelnik.
9. **F-04 (alokacja obsady per faza) jawnie poza zakresem (Q1 = A).** `staffing_position` i
   `staffing_position_allocation` pozostają nietknięte przez tę decyzję i przez SC-1-11. Komentarz
   w `app.models.staffing` ("delivery phases as a dimension need an entity that does not exist yet")
   jest tylko częściowo rozwiązany: encja teraz istnieje, ale jej powiązanie z siatką alokacji jest
   osobną decyzją, odłożoną do zadania, które odtworzy i rozstrzygnie gate-1 decyzję z Issue #4.

## Odłożone (każde wymaga własnej decyzji przy swoim zadaniu)

- **`scope_ref` na `commercial_terms`** (SC-4-05): złożony klucz obcy `(segment_id, scenario_id) →
  scenario_delivery_segment (id, scenario_id)`, analogiczny do `TYPE_AGREEMENT_FOREIGN_KEY`
  (ADR-0003 pkt 3), plus rozstrzygnięcie, czy reguła "cały scenariusz" i reguła "jeden segment" mogą
  współistnieć i jak wygląda "reguła łączona" (reguła 11 Strażnika).
- **Ochrona przed podwójnym rozliczeniem** przy mieszanych umowach (F-06.5, reguła 11 Strażnika) —
  ta decyzja nie dowodzi tej reguły; SC-4-05 musi ją udowodnić własnym kryterium.
- **F-04: alokacja obsady per faza** — czy `staffing_position`/`allocation` dostają `segment_id`,
  czy alokacja może rozpościerać się na więcej niż jeden segment w danym miesiącu, i jak to wpływa
  na K-05 (`app.models.staffing` — tabela allokacji nadal nie niesie kolumn kwotowych/godzinowych
  poza tym, co już ma; `segment_id` byłby wymiarem, nie kwotą).
- **Uprawnienia (ADR-0005)** — nazwa i ziarnistość `READ`/`WRITE` dla segmentu, decyzja odłożona do
  pierwszego zadania wystawiającego endpoint (Q2, pkt 8 wyżej).
- **Przedział obowiązywania segmentu (ADR-0008)** — czy segment potrzebuje własnych dat, i czy
  wymaga to `EXCLUDE`, rozstrzyga pierwszy konsument, który faktycznie czyta czas segmentu (nie ta
  decyzja — pkt 6 wyżej).
- **Kolejność/numeracja segmentów w UI** — jeśli kiedyś potrzebna jako dana, nie tylko konwencja
  nazewnicza użytkownika.
- **Usunięcie segmentu wskazywanego przez regułę komercyjną** — nie istnieje dziś żaden wskaźnik
  (`scope_ref` odłożony, pkt wyżej), więc pytanie nie ma dziś przedmiotu; rozstrzyga SC-4-05.

## Konsekwencje

- Nowa tabela wchodzi do grupy 2 ADR-0004 aneksem towarzyszącym tej decyzji, datowanym tego samego
  dnia — patrz `ADR-0004-wersjonowanie-kalkulacji.md`.
- `docs/architecture/architecture-sensitive-paths.md` już kieruje `backend/migrations/**` i
  `backend/app/models/**` do Architekta (ADR-0001/ADR-0008 i ADR-0004/ADR-0007/ADR-0009
  odpowiednio) — SC-1-11 nie wymaga zmiany tego pliku (sprawdzone w impact-mapie SC-1-11).
  Pierwsze zadanie dodające endpoint nad tą tabelą aktywuje już-wrażliwe wiersze
  `backend/app/api/**`/`frontend/src/api/contracts/**` (ADR-0005/ADR-0009) — bez nowej luki.
- Dodanie kolumny-dyskryminatora później (jeśli któryś przyszły konsument faktycznie rozróżni
  zachowanie fazy od workstreamu) jest migracją fazy expand dotykającą tej tabeli, udokumentowaną
  jako aneks do tej decyzji — nie nową decyzją i nie przebudową referencji `scope_ref`, o ile ten
  ostatni istnieje już wtedy jako prosty FK na `id`.

## Rozważane alternatywy

- **Dwie osobne tabele (`delivery_phase`, `workstream`)** — odrzucone. Odtwarzałoby dosłownie
  przeszkodę nr 2 z Kontekstu ADR-0003 ("encje fazy i workstreamu nie istnieją, a polimorficzna
  referencja nie może być kluczem obcym") w chwili, gdy SC-4-05 zechce zbudować jeden `scope_ref`
  wskazujący "ten segment, którymkolwiek by nie był" — zmuszałoby SC-4-05 albo do podwójnego wzorca
  zgodności typu (jak `tm_terms`, dwa razy, dla identycznie wyglądających tabel), albo do referencji
  polimorficznej, której ADR-0003 świadomie unika. Żadna różnica kolumn między "fazą" a
  "workstreamem" nie uzasadnia dziś dwóch tabel.
- **Jedna tabela z dyskryminatorem `kind` (`phase`/`workstream`)** — odrzucone na razie, nie na
  stałe. Nazwałoby rozróżnienie, którego dziś żaden kod nie konsumuje — ten sam smak ryzyka
  spekulacyjnej konstrukcji, przed którym ADR-0003 pkt 2 broni `model_type` (tam uzasadnione, bo
  dyskryminator od razu dostaje tabelę szczegółów i gałąź dyspozytora; tu nie miałby żadnej z
  dwóch). Migracja dodająca kolumnę później jest tania (pkt "Konsekwencje"); utrzymywanie
  nieużywanej kolumny od dziś nie jest.
- **Segment z własnym przedziałem dat już teraz** — odrzucone na razie. F-02 wymienia "delivery
  phases" jako jeden z niezależnych atrybutów scenariusza obok dat scenariusza, kalendarzy i walut,
  ale żadne kryterium akceptacji SC-1-11 (Issue #65) nie wymaga, by segment sam niósł okres — DoD
  mówi wyłącznie o miejscu w hierarchii i gotowości do wskazania przez regułę komercyjną. Dodanie
  dat bez konsumenta powtórzyłoby wzorzec "kolumna bez odbiorcy", tym razem otwierając pytanie o
  `EXCLUDE` (ADR-0008) przedwcześnie.
- **Rozszerzenie `staffing_position` o kolumnę fazy zamiast nowej tabeli** — odrzucone: nie
  rozwiązuje DoD ("gotowa do wskazania przez regułę komercyjną jako alternatywa dla zasięgu »cały
  scenariusz«") i miesza F-04 (poza zakresem, Q1) z F-06 (przedmiot tego zadania) w jednej zmianie
  schematu.

## Kontrole

| Kontrola | Kryterium akceptacji |
|---|---|
| K-01 | Segment ma dokładnie jednego rodzica: `scenario_id NOT NULL`, FK do `scenarios.id`, wiersz wskazujący nieistniejący/inny scenariusz odrzucony przez bazę. |
| K-02 | Jeden scenariusz ma wiele segmentów (1:N) — przynależność jest kolumną na wierszu segmentu, nie tabelą asocjacyjną. |
| K-03 | `UNIQUE (id, scenario_id)` istnieje w bazie i w modelu ORM zgodnie (drift-guard R-02, wzorem `test_commercial_terms_schema.py`/`test_staffing_schema_constraints.py`). |
| K-04 | Migracja i model ORM zgadzają się co do wszystkich ograniczeń tej tabeli, dowiedzione przeciw realnie zmigrowanej bazie. |
| K-05 | Zbiór kolumn tabeli równy dokładnie `{id, scenario_id, name, created_at, updated_at}` — dowód równością zbioru kolumn (`information_schema.columns`), nie asercją `not in`; żadna kolumna kwotowa, walutowa, godzinowa, alokacyjna ani `position_id`. |
| K-06 | Kopia scenariusza zawierającego segmenty ma segmenty o nowych identyfikatorach i tej samej `name`, wskazujące `scenario_id` kopii — jeden wpis w `SCENARIO_CHILD_COPIERS`; kanarek kompletności kaskady pokrywa tę tabelę. |
| K-07 | Zapis (co najmniej INSERT; UPDATE/DELETE, jeśli funkcje te powstają w tym zadaniu) pod scenariuszem `approved` odrzucony w tej samej instrukcji co odczyt statusu; wyścig z zatwierdzeniem na dwóch połączeniach nie zostawia wiersza. |
| K-08 | Żaden endpoint HTTP nie wystawia segmentu (test strukturalny/brak trasy) — zgodnie z Q2 = A. |
| K-09 | `UNIQUE (scenario_id, name)` odrzuca drugi segment tej samej nazwy w tym samym scenariuszu; kontrast: ta sama nazwa w innym scenariuszu (w tym w kopii) jest przyjmowana. |

## Powiązane wymagania

F-02, F-04 (tylko jako granica), F-06, F-06.5, NF-01; AC-02; ADR-0001, ADR-0003, ADR-0004,
ADR-0005 (tylko jako granica), ADR-0007, ADR-0008; reguły Strażnika Niezmienników 1, 11, 13, 17.
