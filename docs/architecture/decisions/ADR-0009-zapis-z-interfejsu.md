# ADR-0009 — Zapis z interfejsu przeglądarki

**Status:** Accepted (human approval 2026-10-05, Issue #235 Gate 1)

> Ten dokument powstał w odpowiedzi na pytanie bramki 1 P-3 (Issue #49), rozstrzygnięte jako "ADR
> teraz". Luki `[LUKA …]` z pierwszego projektu zostały wypełnione rozstrzygnięciami bramki 1
> (2026-09-21).

## Kontekst

SC-2-04 (Issue #49) jest pierwszym zadaniem, w którym przeglądarka wysyła żądanie mutujące. Cały
dotychczasowy frontend jest odczytowy: `frontend/src/api/client.ts` ma jedną prymitywę
(`readWithDeadline`) i trzy funkcje `get*`, a `frontend/src/features/catalog/CatalogScreen.test.tsx`
dowodzi, że ekran katalogu wysyła dokładnie sześć żądań `GET`, każde bez ciała, i jest czystą
funkcją tych sześciu odpowiedzi.

Backend jest gotowy i dowiedziony niezależnie od tego zadania: `POST /catalog/dimensions/{dimension}`
i `POST /catalog/rates` (`CATALOG_WRITE`), odmowy egzekwowane w bazie (`EXCLUDE` — ADR-0008,
unikalny indeks znormalizowanej nazwy), `409` mapowane po zamkniętej liście SQLSTATE, komunikat bez
wartości wiersza (NF-11) — mutation-checked w SC-2-01 i SC-2-03. Bramka 1 tego zadania (2026-09-21)
rozstrzygnęła zakres szerzej niż rekomendacja: **dodawanie i edycja**, co dołożyło własny mechanizm
współbieżności (ADR-0007, aneks 2026-09-21) i dwa nowe endpointy `PATCH` — opisane tam, nie tutaj.

Czego nie rozstrzygała żadna wcześniej przyjęta decyzja:

- ADR-0007 odłożył drugą połowę NF-05 jednym zdaniem: „Autosave (druga część NF-05) jest poza
  zakresem tej decyzji — dotyczy interakcji frontendu… to szczegół implementacji SC-1-02, nie treść
  tego ADR." SC-1-02 dostarczyło endpoint `PATCH /projects/{id}`, ale żaden ekran go nie woła —
  odłożony szczegół nigdy nie powstał. NF-05 („display save status") i NF-07 („Forms shall explain
  input units and validation errors") nie mają dziś ani jednej realizacji.
- ADR-0005 stawia egzekwowanie po stronie serwera i wprost odrzuca filtrowanie w UI. Nie mówi nic o
  kierunku odwrotnym: o polu, które wołający zapisuje, a którego nie odczyta z powrotem.
- Żadna decyzja nie nazywa, co znaczy timeout zapisu. `REQUEST_TIMEOUT_MS` (12 s) powstał po to, by
  odczyt bez odpowiedzi kończył się nazwanym błędem zamiast wiecznym ładowaniem; dla zapisu ten sam
  mechanizm daje stan jakościowo inny — żądanie mogło się zatwierdzić.

Ten ADR istnieje, bo SC-2-04 ustanawia precedens dla każdego następnego formularza (blok 3-5).
Precedens ustanowiony milcząco jest kopiowany razem z tym, czego nikt nie rozważył.

## Decyzja

1. **Jeden punkt wejścia sieciowego, ten sam co dla odczytu.** Żądanie mutujące wychodzi z
   `frontend/src/api/client.ts`, nigdy z komponentu ekranu, i podlega temu samemu jawnemu budżetowi
   czasu (`REQUEST_TIMEOUT_MS`) oraz temu samemu rozróżnieniu wyniku (`ApiError` ze statusem vs.
   `RequestTimeoutError` bez statusu). Zapis bez budżetu czasu jest tą samą klasą defektu co odczyt
   bez budżetu, z gorszym skutkiem: wiszący przycisk „Zapisz" zachęca do powtórnego kliknięcia.

2. **Brak automatycznego ponowienia; timeout zapisu to stan nierozstrzygnięty, nie porażka.**
   Kontrakt nie ma klucza idempotencji, więc klient nie odróżni „nie doszło" od „doszło, a odpowiedź
   się zgubiła". Klient nie ponawia sam i nie nazywa tego stanu błędem zapisu — nazywa go
   nierozstrzygnięciem i kieruje do ponownego odczytu listy. Dorobienie klucza idempotencji jest
   zmianą kontraktu API i wymaga własnej decyzji; nie wolno go wprowadzić jako szczegół formularza.
   Do przeczytania razem z tym punktem: baza odrzuca powtórzony zapis sama (`EXCLUDE` dla
   identycznego okna, unikalny indeks nazwy, od aneksu 2026-09-21 do ADR-0007 także znacznik
   `updated_at` na edycji), ale odrzuca go jako `409` — osoba, której zapis „się nie udał", zobaczy
   przy powtórce komunikat o konflikcie z wierszem, który sama przed chwilą utworzyła lub zmieniła.
   To musi być w treści komunikatu, nie odkryte przez użytkownika.

3. **Stan ekranu po udanym zapisie pochodzi z serwera — rozstrzygnięcie P-3a: ponowny odczyt
   listy, nie wstawienie wiersza z odpowiedzi zapisu.** Granica twarda, niezależna od czasownika
   (`POST` czy `PATCH`): wiersza na ekranie nie wolno zbudować z tego, co człowiek wpisał w
   formularz. Dotyczy to w szczególności `default_cost_rate`: odpowiedź zapisu przechodzi przez tę
   samą bramkę kosztową co odczyt, więc dla wołającego bez `PERSONNEL_COSTS_READ` wraca pusta.
   Wstawienie w tabelę wartości z formularza odtworzyłoby po stronie klienta pole, które serwer
   usunął — ten sam kształt, który ADR-0005 odrzuca w „Rozważanych alternatywach" („Filtrowanie
   kosztów osobowych w warstwie UI — odrzucone wprost przez AC-06 i NF-04"), tylko w drugą stronę.
   Koszt przyjęty świadomie (rozstrzygnięcie G-6, bramka 1): jeśli ponowny odczyt po udanym zapisie
   zawiedzie, ekran pokazuje **osobny, nazwany stan** ("zapisano, odświeżenie listy się nie udało")
   — nigdy dzisiejszy stan zbiorczej awarii katalogu, który zamieniłby informację o sukcesie w
   informację o awarii.

4. **Klient nie waliduje reguł, które egzekwuje baza.** Nakładanie okien obowiązywania (ADR-0008,
   `EXCLUDE`), unikalność znormalizowanej nazwy i aktualność znacznika współbieżności (ADR-0007,
   aneks 2026-09-21) są sprawdzane wyłącznie przy zapisie, po stronie serwera; ekran pokazuje
   odmowę, nie uprzedza jej. Dwa powody: (a) sprawdzenie w kliencie jest check-then-act — wzorzec,
   który w tym repo przeżył dostarczone testy czterokrotnie (SC-1-02, SC-1-04, SC-2-01, SC-3-01) i
   wymagał testu wyścigu, żeby umrzeć; (b) lista stawek jest stronicowana (`limit`/`offset`,
   `total`), więc klient nie ma w pamięci zbioru, na którym mógłby to sprawdzić — byłoby to
   zdublowanie mechanizmu, i to zdublowanie fałszywe. Walidacja klienta ogranicza się do kształtu
   wejścia (wymagalność pola, format liczby i daty, jednostki — NF-07), nigdy do stanu danych.

5. **Zakończenia zapisu są rozróżnialne i nazwane.** Sukces, `422` (walidacja kształtu — komunikat
   nazywa pole), `409` (odmowa ze stanu danych — na ścieżce edycji dwuznaczne: nieaktualny znacznik
   albo nakładanie/duplikat, rozróżnialne w ciele odpowiedzi, patrz ADR-0007 aneks 2026-09-21 pkt 4),
   `403` (brak uprawnienia), timeout (punkt 2) to sześć różnych, nazwanych stanów; żaden nie jest
   podciągiem innego i żaden nie jest komunikatem ogólnym „coś poszło nie tak" (precedens
   K-01/K-02/K-03 z SC-2-02). Kolor nie jest jedynym nośnikiem żadnego z nich (NF-08).

6. **Odmowa nie wzbogaca się o wpisane wartości.** Komunikat `409` z backendu nie niesie wartości
   wiersza (NF-11, dowiedzione w SC-2-01); klient nie dokleja do niego stawki z formularza i nie
   wypisuje jej do konsoli ani telemetrii. Zachowanie wpisanych wartości w polach formularza jest
   stanem komponentu, nie diagnostyką.

7. **Zasięg precedensu.** Ten ADR obowiązuje każdy przyszły formularz zapisujący. Poza nim i
   wracające tu własnym, datowanym aneksem przy pierwszym zadaniu, które tego potrzebuje: wyłącznie
   autosave (NF-05, zdanie odłożone przez ADR-0007). Zapis modyfikujący istniejący wiersz **jest**
   w zakresie SC-2-04 (bramka 1: dodawanie i edycja) i podlega znacznikowi współbieżności ADR-0007
   wraz z jego aneksem z 2026-09-21 — to musi być powiedziane wprost w rejestrze możliwości, żeby
   „pierwszy zapis z UI" nie został przeczytany jako „zapis z UI działa dla każdej przyszłej encji
   bez własnej decyzji".

## Konsekwencje

- Ekran katalogu rozróżnia dwa znaczenia `409` w ramach jednej ścieżki zapisu (nieaktualny znacznik
  vs. odmowa ze stanu danych) — patrz ADR-0007 aneks 2026-09-21 pkt 4. Kolejny formularz edytujący
  inną encję reużywa ten sam wzorzec rozróżniania, nie wynajduje własny.
- Punkt 3 oznacza, że w dzisiejszym systemie osoba dodająca lub edytująca stawkę kosztową zobaczy
  „Restricted" w wierszu, który sama przed chwilą utworzyła lub zmieniła — bo żaden dzisiejszy
  wołający nie ma `PERSONNEL_COSTS_READ` (ADR-0005, aneks 2026-09-19 pkt 6). To nie jest defekt
  ekranu i nie wolno go „naprawić" w UI.
- Punkt 4 przenosi cały ciężar komunikatu o nakładaniu okien i o nieaktualnym znaczniku na treść
  odmowy backendu. Jeśli okaże się nieczytelna dla człowieka, poprawka należy do backendu (przy
  zachowaniu NF-11), nie do klienta odtwarzającego regułę.

## Rozważane alternatywy

- **Brak ADR; SC-2-04 ustanawia precedens implicite** — odrzucone: sześć rozstrzygnięć powyżej i
  tak zapadłoby w tym zadaniu, tyle że w kodzie jednego ekranu; następny formularz skopiowałby je
  razem z tym, czego nikt nie rozważył — w szczególności punkt 2, jedyne miejsce, gdzie milczenie
  grozi zdublowanym wierszem w danych, a nie tylko niespójnym UX-em.
- **ADR po drugim formularzu** — rozważone: tańsze, oparte na dwóch przypadkach zamiast jednego.
  Odrzucone na bramce 1 (P-3): drugi formularz powstałby w bloku 3-5 i zapisywałby dane wewnątrz
  projektu, czyli pod bramką zasięgu i pod ADR-0007 — złożenie dwóch precedensów naraz.
- **Szeroki ADR o UX zapisu (autosave, kolejkowanie, tryb offline)** — odrzucone: projektowanie
  przed potrzebą; decyzja bez zadania nie ma jak zostać dowiedziona.

## Aneks 2026-09-23 (Issue #71, SC-4-06 — bramka 1)

Drugi formularz zapisujący (pierwszy wewnątrz projektu, pod bramką zasięgu — dokładnie przypadek
nazwany w „Rozważanych alternatywach" poniżej). Bramka 1 tego zadania rozstrzygnęła dwa punkty
węższe niż litera powyższej decyzji:

- **Zawężenie pkt 3.** „Ponowny odczyt listy, nie wstawienie wiersza z odpowiedzi zapisu" dotyczyło
  wprost bramki kosztowej katalogu (odpowiedź zapisu przechodzi tę samą bramkę `PERSONNEL_COSTS_READ`
  co odczyt — ryzyko, którego reguła miała zapobiec, to odtworzenie po stronie klienta pola, które
  serwer usunął). `POST /projects/{id}/scenarios/{id}/commercial-terms` nie ma tej bramki: odpowiedź
  `201` niesie pełny, wyliczony przez serwer `ScenarioCommercialTerms` (przychód i regułę), nie echo
  formularza. SC-4-06 renderuje wprost z ciała `201`, pod warunkiem że ciało przechodzi tę samą
  walidację kształtu co odpowiedź `GET` (ADR-0010 pkt 2) — jeden zapis, jedno żądanie. Reguła pkt 3
  w brzmieniu pierwotnym („nigdy z tego, co człowiek wpisał w formularz") zostaje nienaruszona:
  wartości pochodzą z serwera, nie z pól formularza.
- **Doprecyzowanie pkt 4.** „Ekran pokazuje odmowę, nie uprzedza jej" zakazuje klientowi
  odtwarzania reguły stanu danych (check-then-act). Nie zakazuje **prezentacji** stanu, który
  serwer już podał w tej samej odpowiedzi odczytu: `GET` niesie `scenario_status`. SC-4-06 ukrywa
  lub wyłącza akcję „ustaw T&M", gdy `scenario_status = "Approved"` (`ScenarioStatusLabel`), jako
  prezentację cudzego
  ustalenia, nie jako własną walidację. Ścieżka `409` (odmowa zapisu dla scenariusza zatwierdzonego
  między odczytem a zapisem — wyścig, capabilities.md w.156) zostaje w kodzie i musi mieć własny,
  osobno dowiedziony test — nie wolno jej uznać za martwą dlatego, że kontrolka zwykle jest ukryta.

## Aneks 2026-09-24 (Issue #95, SC-6-03 — bramka 1)

Drugi przypadek zawężenia punktu 3 (pierwszy: aneks 2026-09-23, SC-4-06). `POST
/projects/{project_id}/scenarios/{scenario_id}/duplicate` (backend scalony w SC-6-01; SC-6-03
dodaje pierwszego wołającego z przeglądarki) jest formularzem zapisującym w rozumieniu tego ADR,
mimo braku ciała żądania — pytanie P-3a ("skąd pochodzi wiersz na ekranie po `201`") obowiązuje
niezależnie od tego, czy żądanie niosło jakiekolwiek pole.

- **Zawężenie pkt 3, uzasadnienie silniejsze niż w aneksie SC-4-06.** Odpowiedź `201` tego
  endpointu ma kształt `ScenarioListItem` — dokładnie ten, którym `GET /projects` już renderuje
  każdy wiersz scenariusza, zbudowany przez tę samą funkcję kształtującą (`_shape_scenario`,
  wywołaną przez `shape_duplicated_scenario`, ADR-0004 aneks SC-6-01). Ten kształt nigdy nie niósł
  pola kosztowego — bramka kosztowa, przed którą chroni reguła pkt 3, nie ma tu przedmiotu w
  ogóle, nie tylko jest nieaktywna jak w przypadku reguły komercyjnej (SC-4-06). Endpoint nie
  przyjmuje ciała żądania (ADR-0004 aneks SC-6-01 pkt 2 — "endpoint zostaje bez ciała żądania"),
  więc "echo formularza", przed którym broni reguła pkt 3 w brzmieniu pierwotnym, jest tu
  strukturalnie niemożliwe: nie ma czego echować.
- **Warunek ten sam co w aneksie SC-4-06.** Ciało `201` przechodzi tę samą walidację kształtu
  (`isScenarioListItemShape`, już istniejącą w `frontend/src/api/client.ts` dla `GET /projects`)
  co odpowiedź odczytu, zanim cokolwiek na ekranie się zmieni — jeden zapis, jedno żądanie, żadnego
  ponownego odczytu listy.
- **Miejsce wstawienia wiersza — doprecyzowanie, którego aneks SC-4-06 nie potrzebował, bo tamten
  zapis modyfikował wiersz już obecny na ekranie.** Duplikat jest NOWYM elementem tablicy
  `scenarios` projektu, którego `project_id` żądania sam nazywa (ADR-0004 aneks SC-6-01: duplikat
  zawsze we własnym projekcie źródła) — ekran wstawia go do `scenarios` dokładnie tego projektu,
  nigdy przez dopasowanie po pozycji/indeksie w drzewie stanu. Dowód wymaga kontrastu: dwa
  projekty, każdy z własnym scenariuszem, duplikacja w jednym nie rusza tablicy drugiego.
- **`409` (wyczerpanie kandydatów nazwy albo kolizja współbieżna) zostawia listę bez zmian** —
  zgodnie z pkt 6 decyzji bazowej ("odmowa nie wzbogaca się o wpisane wartości"); tu nie ma
  wartości wpisanych przez człowieka, więc odmowa nie wzbogaca się o nic — ekran pokazuje nazwany
  stan odmowy i nie wstawia żadnego wiersza, próbnego ani ostatecznego.
- **`403` i `404` rozróżnialne bez nowego mechanizmu.** Wzorzec z `getScenarioCommercialTerms`
  (ta sama zasada, SC-4-06 K-04) stosuje się wprost: `ApiError.status` niesie oba kody osobno, a
  ekran nie miesza "brak uprawnienia `SCENARIO_COPY`" z "scenariusz poza zasięgiem/nieistniejący".
  To nie jest odstępstwo od reguły nieodróżnialności ADR-0005: ta reguła zamyka wyłącznie
  przestrzeń powodów WEWNĄTRZ `404`; `403` uprawnienia jest osobną, zawsze rozróżnialną osią,
  egzekwowaną jako zależność FastAPI przed jakimkolwiek odczytem zasobu (`backend/app/api/scenarios.py`,
  docstring `duplicate`: "403 — the permission dependency, before the database").
- **Zasięg precedensu wąski, jak w aneksie SC-4-06.** Ten aneks nie rozstrzyga niczego dla
  przyszłego formularza o innym kształcie odpowiedzi — w szczególności dla żadnego przyszłego
  zapisu niosącego pole kosztowe. Pierwsze takie zadanie odtwarza pytanie punktu 3 samodzielnie i
  wraca tu własnym, datowanym wpisem, zgodnie z pkt 7 decyzji bazowej.

## Addendum 2026-09-29 (Issue #164, SC-5-09 — gate 1)

**Status:** Accepted (human decision 2026-09-29, by merging the ADR acceptance PR for SC-5-09; code merged in #173)

The catalogue form gains the cost-rate unit (`cost_rate_unit`, ADR-0005 addendum 2026-09-29, SC-5-08).
The backend added a third cause of `409` on the catalogue edit path, `condition=cost_rate_unit_precondition`
(`COST_RATE_UNIT_CONDITION` and `COST_RATE_UNIT_REASON` in `backend/app/data/catalog.py`): a caller
without `PERSONNEL_COSTS_READ` sent a unit that differs from the stored one, and nothing was written.
Point 5 of the decision says the endings of a write are distinguishable and named, and that none is a
substring of another; this addendum applies that point to the new cause and settles nothing else.

1. **The third `409` cause is a seventh named ending.** Point 5 counts six endings, with the two
   meanings of `409` on the catalogue path (ADR-0007, addendum 2026-09-21, point 4) already told
   apart. A `409` whose body carries `cost_rate_unit_precondition` is a further, separate ending — not
   a variant of the stale marker, not of "refused by the state of the data", and not `unstated`.
2. **The wording comes from the backend's own reason.** The screen text states what
   `COST_RATE_UNIT_REASON` states — the unit sent does not match the stored one, this caller may not
   change it, nothing was written — and adds no cause the backend did not establish (the rule of
   `refusalCauseOf`: a cause the answer did not name is `unstated`, never a guess). The screen does not
   reword the reason into a cause of its own, for instance a stale row.
3. **The text names no stored unit, no rate and no typed value** (decision point 6; NF-11; ADR-0005
   addendum 2026-09-29, point 7, R-02). The client does not append the unit the person chose, and does
   not derive from the refusal which unit is stored, although the status alone discloses whether the
   guess was right (the exception recorded in ADR-0005, addendum 2026-09-29, point 7).
4. **The text is not a substring of another message, and no other message is a substring of it.**
   The pair check covers every message the catalogue write path can show — the stale marker, overlap,
   duplicate value, missing reference, broken rule, unstated, and this one. The text also does not
   carry the action of the stale-marker message ("read the row again"): re-reading shows a blind
   caller nothing, so the two endings lead to different actions (precedent K-01..K-03, SC-2-02).
5. **The cause is matched by the identifier the backend puts in the message**, as the marker and the
   SQLSTATEs already are (`frontend/src/api/contracts/writeRefusals.ts`): a raw contract literal
   mirroring `COST_RATE_UNIT_CONDITION`, not a phrase of the sentence. A body carrying
   `updated_at_marker` keeps the stale-marker ending; the two identifiers never appear in one message.
6. **`RefusalCause` and `CONFLICT_MESSAGES` stay exhaustive.** The cause is a member of `RefusalCause`,
   and the message table stays a `Record` over that whole type, so a cause added to the contract and
   not worded on screen fails the build (the mechanism already in `writeOutcome.ts`).
7. **What this addendum does not decide.** It offers no blind-writer control to change a cost amount:
   a blind create supplies the amount and the unit together (explicit, required unit choice), and an
   edit of a row with the pair withheld sends neither (ADR-0005, addendum 2026-09-29, Q-B; decision
   of gate 1, SC-5-09, Q-1). The scenario cost response (`assumptions_used`) is Issue #172. The
   client still does not check the unit against stored state before the write (point 4 of the
   decision).

| Control | Acceptance criterion |
|---|---|
| A9-164-1 | A `409` whose body carries `cost_rate_unit_precondition` reaches the screen as its own named ending, distinct from the stale-marker ending and from `unstated`; removing the cause match leaves a failing test. |
| A9-164-2 | The text of that ending contains no stored unit, no rate and no value typed into the form (fixture with a distinctive typed amount and unit, neither present in the rendered text). |
| A9-164-3 | For every pair of catalogue write-refusal messages, including this one, neither text is a substring of the other. |
| A9-164-4 | A `409` carrying `updated_at_marker` keeps the stale-marker message (contrast). |
| A9-164-5 | Every member of `RefusalCause` has an entry in `CONFLICT_MESSAGES`; a member added without an entry fails the type check. |

## Addendum 2026-10-04 (Issue #228, SC-1-18 — gate 2)

**Status:** Accepted (human decision 2026-10-04, explicitly approved for Issue #228)

This addendum records the separate API decision required by point 2 of the base decision. `POST /projects`
now supports an optional `Idempotency-Key` for project creation. It is deliberately narrow:
it does not change the write contract for other endpoints.

1. **The key is optional.** A request without `Idempotency-Key` keeps the existing create behavior,
   preserving compatibility for callers that do not send the header. The Projects screen sends a
   fresh UUID for each logical create operation.
2. **Identity and payload are bound together.** A key is scoped to the authenticated caller. The
   server records a digest of the request payload and the created project reference; it does not
   retain a second copy of the submitted payload. The record remains while its project exists and
   is removed with that project. A matching caller, key, and payload replays the original project
   result. Reusing the same caller and key with a different payload returns `409` and creates no
   second project.
3. **Authorization remains current.** The caller must still pass the current project-create
   permission check before a replay can return the project. Idempotency does not grant access to a
   result after permission has been removed.
4. **Concurrent claims are atomic.** Concurrent requests using the same caller and key cannot
   create duplicate projects; the database claim and project reference are persisted atomically.
5. **Client recovery is bounded to the operation key.** The browser stores the UUID in
   `sessionStorage`, not the payload or owner data, so it can retry an unresolved create after a
   remount. A key duplicated into another tab requires an explicit recovery choice. The UI does not
   synthesize server-paginated rows or totals from the create response.
6. **Scope of this decision.** This is the task-specific exception to the base decision's
   no-idempotency rule, limited to `POST /projects`. Other write endpoints still require their own
   decision before gaining idempotency keys.

**Evidence:** `backend/tests/test_project_create_idempotency.py` covers replay, changed payload,
caller scoping, permission re-check, and concurrency. Frontend coverage in
`frontend/src/features/projects/ProjectListScreen.test.tsx` and
`ProjectListSearchPagination.test.tsx` covers unresolved-create recovery, duplicated tab state, and
paginated replay without local total inflation. Migration
`backend/migrations/versions/b2d4f6a8c0e1_add_project_create_idempotency.py` ties record lifetime to
the project with `ON DELETE CASCADE`.

## Powiązane wymagania

NF-05, NF-07, NF-08, NF-11, AC-06, F-03, NF-10; ADR-0002 (kierunek wejścia kwoty — aneks SC-2-04),
ADR-0005 (bramka kosztowa i semantyka częściowego `PATCH` — aneks SC-2-04), ADR-0007 (znacznik
współbieżności katalogu — aneks 2026-09-21, aktywowany; autosave nadal poza zakresem), ADR-0008
(okno obowiązywania wpisywane i edytowane przez człowieka — aneks SC-2-04), ADR-0003 (blokada
zapisu do scenariusza `approved` — aneks 2026-09-23 SC-4-01, powtórzona tu jako aneks 2026-09-23
SC-4-06).

**Ryzyko nazwane, nie zamknięte (Invariant Guardian, bramka 2, 2026-09-22):** edycja stawki
katalogowej jest zdarzeniem AC-04 ("An organization's default role rate changes") — dziś bez
skutku, bo nic nie czyta stawki do kalkulacji (ADR-0004, `app/models/staffing.py` — `SCENARIO_CHILD_COPIERS`
świadomie bez wpisu dla tabel katalogu). Rozszerzenie zakresu z samego dodawania na dodawanie i
edycję (bramka 1, P-1) czyni to ryzyko silniejsze niż w pierwotnej mapie architekta: edycja
nadpisuje istniejący wiersz *w miejscu*, nie dokłada nowego okna obok. Pierwsze zadanie, które
rozstrzyga stawkę do zapisanej kalkulacji, dziedziczy edytowalny domyślny bez migawki i musi
odtworzyć to pytanie samodzielnie, jeśli nie zostanie tu zapisane — co niniejszym jest zrobione.
