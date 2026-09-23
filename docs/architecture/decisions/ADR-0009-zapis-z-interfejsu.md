# ADR-0009 — Zapis z interfejsu przeglądarki

**Status:** Draft — pending approval

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
