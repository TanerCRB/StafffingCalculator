# ADR-0019 — Dane osobowe: rejestr osób nazwanych i przypisanie osoby do pozycji obsady (F-03)

**Status:** Accepted (bramka 1 SC-2-06, decyzja człowieka 2026-09-27, Issue #31)

> Szkic przygotowany przez rolę Architekta na bramkę 1 SC-2-06 (Issue #31) jako „wskazana decyzja
> architektoniczna" wymagana przez hard stop 6 (`TEAM-CONTRACT.md` §4) przed zmianą jakiegokolwiek
> pliku dotyczącego danych osobowych. Zawiera ocenę wpływu na dane osobowe. Numer 0019, nie 0018:
> ADR-0018 jest roboczo zarezerwowany dla ADR uwierzytelniania (SC-1-12), który nie ma jeszcze
> Issue. Pytania PD-1..PD-5 na końcu wymagają decyzji człowieka; do czasu ich rozstrzygnięcia
> treść pod nimi jest rekomendacją, nie decyzją.

## Kontekst

F-03: "Assigning a named person shall be optional." — możliwość musi istnieć, z opcjonalnością
dla planisty. Słownik wymagań (§2): "Staffing position — Planned demand for a role or named
person over a defined period." NF-11: "Errors and failed saves shall be logged without exposing
confidential rates or personal information in diagnostic logs." NF-04: "Data shall be encrypted in
transit and at rest. Authorization shall be enforced on the server." F-13/AC-06: indywidualne
koszty osobowe oddzielone od wyników zagregowanych.

Do dziś system nie przechowuje żadnego rejestru osób fizycznych. Jedyne dane osobowe to pole
opisowe `projects.owner` (ADR-0005, aneks 2026-09-19) oraz wolne teksty, których treść bywa
osobowa (`absence_type.name`, `absence_budget.source` — ADR-0005 aneksy SC-3-02 pkt 10, SC-3-03
pkt 6). `staffing_position` jest z założenia anonimowa, a jej docstring i aneksy ADR-0005
(SC-3-02 pkt 6, 11; SC-3-03 pkt 7; SC-5-05 pkt 2, 6; SC-3-04 pkt 2) wiązały pojawienie się osoby
przy pozycji z "Issue #31, ADR uwierzytelniania".

Decyzja człowieka P-2 = (a) z 2026-09-27 zmienia tę przesłankę: **osoba nazwana jest osobnym
rekordem osoby/pracownika, nie kontem użytkownika systemu** — bez powiązania z
`project_access.user_id` ani z tożsamością wołającego. Pozostałe decyzje człowieka tej samej daty,
na których ten dokument stoi: Q-1 = (a) (bez stawki indywidualnej), Q-3 = (a) (nowe
`PEOPLE_READ`/`PEOPLE_WRITE`, placeholder ich nie nadaje), Q-4 = (c) (ekspozycja nieobecności i
kosztów dodatkowych pozycji z osobą — osobna Story przed ekranem osób), Q-5 = (a) (bez lokalizacji
osoby), Q-6 = (a) (usuwanie osobną Story przed danymi rzeczywistymi; kierunek: anonimizacja w
miejscu), Q-7 = (a) (osoba tylko przy `headcount = 1`).

## Ocena wpływu na dane osobowe

### 1. Cel i podstawa przetwarzania

- **Cel, jedyny:** planowanie obsady projektu IT i jego rentowności — odpowiedź na pytanie "kto
  konkretnie ma obsadzić tę pozycję". **Poza celem:** ocena pracownika, decyzje kadrowe,
  wynagrodzeniowe, dyscyplinarne, profilowanie wydajności, ewidencja czasu pracy. Każda przyszła
  funkcja, która wyprowadza z rejestru osób wniosek o osobie (np. "wykorzystanie osoby", ranking),
  wychodzi poza ten cel i wymaga własnej decyzji z oceną wpływu.
- **Podstawa prawna — do potwierdzenia przez administratora danych (pytanie PD-1).** Rekomendacja:
  art. 6 ust. 1 lit. f RODO (prawnie uzasadniony interes administratora — planowanie zasobów
  projektu) dla pracowników i współpracowników B2B. Architekt nie jest właściwy do rozstrzygnięcia
  podstawy prawnej; dokument zapisuje wyłącznie, że **zgoda (lit. a) nie jest rekomendowana** —
  w relacji podporządkowania nie jest swobodna, a jej wycofanie wymuszałoby usuwanie, którego
  system jeszcze nie ma (pkt 8).

### 2. Kategorie osób i danych

- **Osoby, których dane dotyczą:** pracownicy i współpracownicy organizacji (także osoby fizyczne
  na kontraktach B2B). **Poza zakresem:** osoby po stronie poddostawcy (F-03 aneks 2026-09-21,
  nierozstrzygnięte przez biznes) — Issue #31, Out of scope pkt 10.
- **Kategorie danych:** imię i nazwisko (dana zwykła). **Brak danych szczególnych kategorii (art. 9)
  w rejestrze.** Uwaga, która czyni to zdanie warunkowym: nieobecności pozycji (typ z słownika,
  część typów implikuje dane o zdrowiu — ADR-0005 aneks SC-3-02 pkt 6) wiszą na pozycji, a pozycja
  z przypisaną osobą **wskazuje ją wprost**. W chwili, gdy ktokolwiek w działającym systemie ma
  `STAFFING_READ` ∧ `PEOPLE_READ`, lista nieobecności pozycji z osobą jest listą nieobecności tej
  osoby. Dziś nieosiągalne (pkt 4); rozstrzygnięcie należy do Story Q-4 = (c).

### 3. Minimalizacja — zatwierdzany zbiór kolumn

**Wiersz osoby (tabela, nazwa robocza `person`) ma dokładnie te kolumny — równość zbioru, nie
`not in`:**

| Kolumna | Typ | Po co |
|---|---|---|
| `id` | UUID, PK | identyfikator techniczny, jedyne odniesienie z pozycji obsady |
| `full_name` | `VARCHAR(200) NOT NULL` | imię i nazwisko — jedna kolumna, nie dwie (pkt niżej) |
| `created_at` | `timestamptz NOT NULL`, czas bazy | metadane rekordu; wejście przyszłej reguły retencji |
| `updated_at` | `timestamptz NOT NULL`, czas bazy | znacznik współbieżności ADR-0007 dla sprostowania |

**Czego wiersz nie ma i nie może mieć bez nowej decyzji:** e-mail, telefon, notatka/komentarz,
stanowisko, dział, przełożony, stawka (sprzedażowa i kosztowa — Q-1/Q-2), lokalizacja (Q-5),
identyfikator kadrowy/numer pracownika, powiązanie z kontem użytkownika (P-2), flaga aktywności.
Każda z nich poszerza cel lub zakres i wymaga datowanego aneksu do tego dokumentu (oraz ponownego
uzbrojenia testu równości zbioru — nie jego poluzowania).

- **Jedna kolumna `full_name`, nie `first_name` + `last_name`:** mniej założeń kulturowych, jedno
  pole do sprostowania i anonimizacji, żadne wyliczenie nie potrzebuje rozbicia.
- **Brak unikalności na `full_name`:** dwie osoby o tym samym imieniu i nazwisku są legalne.
  Konsekwencja nazwana: w rejestrze i przy pozycji są rozróżnialne wyłącznie po `id` — pytanie
  PD-3 (czy dopuścić rozróżniającą etykietę) należy do Story ekranu, nie do tej.
- **Ograniczenie serwerowe na wierszu z imieniem (warunek kryterium K-09):** `CHECK` nazwany, np.
  `person_full_name_canonical`: `full_name = btrim(full_name) AND char_length(full_name) > 0`.
  Jest to reguła danych (postać kanoniczna — brak pustego i brak białych znaków na brzegach), nie
  sztuczka testowa — i zarazem **jedyne ograniczenie, które pada na wierszu niosącym rzeczywiste
  imię** (np. `' Jan Kowalski'`), więc PostgreSQL w `DETAIL: Failing row contains (…)` wyniósłby
  to imię. Bez niego NF-11 kanał 2 dla tej tabeli byłby twierdzeniem bez kontrastu. Przekroczenie
  `VARCHAR(200)` i `NOT NULL` nie niosą wartości w komunikacie, więc kontrastu nie dają.
  Unikalność na `full_name` odrzucona jako nośnik kontrastu: dałaby kontrast (`Key (full_name)=(…)`),
  ale jest fałszywą regułą biznesową (punkt wyżej).

### 4. Kto widzi imię

- **Rejestr:** odczyt wyłącznie z `PEOPLE_READ`, zapis (utworzenie, sprostowanie) wyłącznie z
  `PEOPLE_WRITE`. Odmowa jest **odmową zasobu** (`403`, ciało bez żadnego imienia), nie
  wybieleniem pola — odwrotnie niż katalog (ADR-0005 aneks SC-2-01 pkt 5, "sam fakt istnienia roli
  … nie jest daną chronioną"): **fakt istnienia osoby w rejestrze jest daną osobową**.
- **Przy pozycji obsady:** pole osoby istnieje w odpowiedzi wyłącznie dla wołającego z
  `STAFFING_READ` ∧ `PEOPLE_READ`. Dla wołającego bez `PEOPLE_READ` pozycja z przypisaną osobą jest
  **nieodróżnialna od anonimowej** — brak klucza (nie `null`), brak identyfikatora osoby, brak flagi
  "przypisano" — na każdej ścieżce zwracającej pozycję. Egzekwowane w warstwie kształtowania
  odpowiedzi (ADR-0005 "Decyzja"), nie w UI.
- **Placeholder tożsamości nie nadaje `PEOPLE_READ` ani `PEOPLE_WRITE`** (Q-3 = a). Konsekwencja
  przyjęta razem z tym dokumentem: w działającym systemie (dev/test) **nikt nie widzi i nikt nie
  wpisuje imienia przez API**; gałąź pozytywna jest dowodzona wyłącznie przez
  `dependency_overrides`, a rejestr możliwości musi to nazwać wprost. Jedyną drogą wprowadzenia
  osoby do bazy w dev/test jest zapis z pominięciem API (fixture, seed, SQL) — i do niej odnosi się
  zakaz z pkt 8.
- **`PEOPLE_READ` jest globalne, nie per projekt** (Q-3 = a). ADR uwierzytelniania/ról (SC-1-12,
  SC-1-13) musi rozstrzygnąć, czy ma pozostać globalne, czy być wyprowadzane z przypisań
  projektowych — wzorem zobowiązania dla `PERSONNEL_COSTS_READ` (ADR-0005 aneks 2026-09-19 pkt 3).

### 5. Przyszła stawka indywidualna (poza zakresem SC-2-06, Q-1/Q-2) — granice z góry

Stawka przypisana do osoby nazwanej jest daną osobową niezależnie od tego, czy jest sprzedażowa
czy kosztowa. Story stawki indywidualnej musi co najmniej:

1. **Stawka kosztowa osoby = indywidualny koszt osobowy w rozumieniu F-13/AC-06/NF-11** — w
   kontekście projektu/scenariusza koniunkcja `PERSONNEL_COSTS_READ` ∧
   `project_access.can_view_personnel_costs` (ADR-0005 aneks 2026-09-19) **oraz** `PEOPLE_READ`
   dla samej tożsamości. **Wyjątek jednoczynnikowy katalogu (ADR-0005 aneks SC-2-01 pkt 3) nie
   rozciąga się na rejestr osób** — tamten wyjątek chroni stawkę roli, nie człowieka; stawka
   kosztowa osoby wystawiona w rejestrze pod samym `PERSONNEL_COSTS_READ` byłaby dokładnie tym, co
   F-13 oddziela.
2. Stawka sprzedażowa osoby: nie jest kosztem (precedens `default_selling_rate`), ale razem z
   imieniem jest daną osobową → co najmniej `PEOPLE_READ`.
3. Rozstrzygnąć wejście do migawki (ADR-0004): stawka jest wartością dziedziczoną spoza scenariusza
   i **wchodzi** do migawki (w przeciwieństwie do imienia, pkt 7) — co oznacza, że anonimizacja
   osoby nie usunie zamrożonej stawki zatwierdzonego scenariusza. To trzeba rozstrzygnąć razem z
   Story usuwania, nie po niej.

### 6. Logi i diagnostyka (NF-11)

- Kanał 1 (echo parametrów SQLAlchemy) i kanał 2 (`DETAIL: Failing row contains`) — ten sam
  mechanizm co dziś (`hide_parameters=True`, `app.data.write_errors`), dowiedziony dla projektu
  (`test_statement_errors_hide_parameters.py`); dla tabeli osoby dowodzony osobno, z kontrastem na
  ograniczeniu z pkt 3.
- **Imię nigdy w URL** — ani w ścieżce, ani w parametrach zapytania (logi dostępowe serwera
  zapisują URL). Wyszukiwanie/filtrowanie rejestru po imieniu jest poza zakresem SC-2-06; pierwsza
  Story, która je wprowadzi, przenosi kryterium do ciała żądania albo rozstrzyga logi dostępowe
  wprost.
- **Imię nigdy w komunikacie odmowy ani wyjątku** (`404`/`409`/`422` generowane przez aplikację,
  wiadomości logów) — komunikaty nazywają ograniczenie i identyfikator, nie wartość.

### 7. Sprostowanie (art. 16) i usuwanie (art. 17) wobec migawki zatwierdzonego scenariusza

- **Imię nie wchodzi do migawki** — ani `approved_snapshot_*` nie rośnie o tabelę osoby, ani żadna
  tabela migawkowa nie dostaje kolumny z imieniem lub identyfikatorem osoby. Zbiór tabel
  `approved_snapshot_*` bez zmian. Jest to świadome odstępstwo od kryterium ADR-0004 (aneks
  2026-09-19 SC-3-01 pkt 1: "wartość dziedziczona spoza scenariusza → migawka"), zapisane w
  ADR-0004 aneksem 2026-09-27 (SC-2-06) — uzasadnienie: imię nie wchodzi do żadnego wyliczenia, a
  migawka "nie ma ścieżki UPDATE" (ADR-0004 aneks SC-3-02 pkt 3), więc imię w migawce byłoby daną
  osobową, której **nie da się sprostować ani usunąć**.
- **Przypisanie (`person_id` na pozycji) jest daną własną scenariusza (grupa 2)** — chronione
  strażnikiem zapisu `approved`, nie migawką. Zatwierdzony scenariusz wskazuje osobę przez
  identyfikator; jej imię jest czytane bieżąco. **Konsekwencja przyjęta:** raport zatwierdzonego
  scenariusza pokazuje imię w brzmieniu bieżącym (po sprostowaniu) albo znacznik anonimizacji (po
  Story usuwania) — ograniczenie odtwarzalności nagłówka, nie wyliczenia (precedens: pola opisowe
  Projektu, ADR-0004 aneks 2026-09-18 grupa 1; etykieta kategorii kosztu, aneks SC-5-05 pkt 2).
- **Sprostowanie nie jest zapisem do zatwierdzonej kalkulacji** — wiersz osoby nie jest dzieckiem
  scenariusza i strażnik `approved` go nie obejmuje. Dodanie takiego strażnika byłoby błędem, nie
  ostrożnością: blokowałoby art. 16 dla każdej osoby przypisanej kiedykolwiek do zatwierdzonego
  scenariusza.
- **Kierunek usuwania (Q-6 = a): anonimizacja w miejscu** — `full_name` zastąpione znacznikiem,
  `id` i przypisania nietknięte, scenariusze `approved` nietknięte. Klucz obcy
  `staffing_position.person_id → person.id` bez `ON DELETE` (czyli `NO ACTION`), zgodnie z
  konwencją `app.models.staffing`: fizyczne `DELETE` osoby przypisanej do pozycji zostaje odmówione
  przez bazę, co jest zgodne z kierunkiem anonimizacji. Story usuwania może potrzebować kolumny
  (np. znacznik czasu anonimizacji) — to jej aneks i jej ponowne uzbrojenie testu równości zbioru.

### 8. Retencja i zakaz danych rzeczywistych

- **Do czasu scalenia Story usuwania/anonimizacji (Q-6) wyłącznie dane fikcyjne** — w każdej bazie:
  deweloperskiej, testowej, efemerycznej, w fixture'ach, w `seed_dev_data.py`, w przykładach w
  dokumentacji i Issue. Rozszerza wprost zakaz z ADR-0005 aneksu 2026-09-21 pkt 6 (syntetyczne
  cenniki poddostawców).
- **Warunki wpuszczenia pierwszych danych rzeczywistych — wszystkie naraz, nie którykolwiek:**
  (a) Story usuwania/anonimizacji scalona i w rejestrze możliwości; (b) ADR uwierzytelniania
  (SC-1-12; źródło tożsamości przesądzone przez człowieka 2026-09-27: Keycloak self-hosted, OIDC)
  zamyka placeholder tożsamości i ta zdolność jest w rejestrze możliwości (inaczej `PEOPLE_READ`
  nadawane jest "komuś z nagłówka");
  (c) Story Q-4 = (c) (ekspozycja nieobecności i kosztów dodatkowych pozycji z osobą) scalona;
  (d) szyfrowanie w spoczynku środowiska docelowego udokumentowane dowodem w rejestrze możliwości
  (pkt 9); (e) reguła retencji ustalona (pytanie PD-2) i opis postępowania z kopiami zapasowymi
  (pkt 10).
- **Reguła retencji — do Story usuwania (pytanie PD-2).** Architekt nie proponuje okresu; wskazuje,
  że `created_at` jest jedyną kolumną, na której reguła czasowa może dziś stanąć, a "osoba nie jest
  przypisana do żadnej pozycji nie-zarchiwizowanego projektu" wymaga zapytania odwrotnego, którego
  SC-2-06 nie buduje.

### 9. Szyfrowanie w spoczynku (NF-04)

Rejestr możliwości nie ma dziś żadnego wpisu o szyfrowaniu w spoczynku — nie ma też środowiska
poza dev/test. **Rozstrzygnięcie: bez szyfrowania na poziomie kolumny (`pgcrypto` itp.) w SC-2-06**:
bez środowiska docelowego nie ma gdzie trzymać klucza, a szyfrowanie kolumny z kluczem w tej samej
aplikacji chroni przed mniej niż się wydaje (wyciek przez API przechodzi przez klucz). NF-04 dla
danych osobowych jest spełniane szyfrowaniem magazynu/wolumenu i kopii zapasowych środowiska
docelowego — warunek (d) pkt 8, sprawdzany przed danymi rzeczywistymi, nie przez test w repo.
**Warunek ponownego rozpatrzenia:** pierwsza dana szczególnej kategorii w rejestrze osób albo
wymóg administratora danych.

### 10. Kopie zapasowe (NF-06)

RPO 24 h oznacza kopie zawierające imiona. Anonimizacja w miejscu nie sięga do kopii. Story
usuwania musi nazwać postępowanie (retencja kopii, ponowna anonimizacja po odtworzeniu) — tu
nazwane jako warunek (e) pkt 8.

### 11. Historia zmian (F-12, SC-8-01)

Przyszły `audit_log` (ADR-0004 "Konsekwencje", SC-8-01 zarezerwowane) **nie zapisuje imienia jako
wartości** — przy zdarzeniach przypisania i sprostowania zapisuje identyfikator osoby. Inaczej
sprostowanie i anonimizacja musiałyby sięgać do append-only historii. Zapisane tu jako ograniczenie
wiążące SC-8-01, nie odkrywane w nim.

### 12. Eksport (F-11) i frontend

Eksport przechodzi przez tę samą funkcję kształtującą co API (ADR-0005 "Decyzja") — bramka
`PEOPLE_READ` obowiązuje go bez osobnej decyzji. Ekran rejestru i osoby przy pozycji są osobną
Story FE (Out of scope pkt 3), która nie może rekonstruować imienia po stronie klienta z pamięci
formularza (ADR-0005 aneks 2026-09-21 SC-2-04 pkt 2, analogicznie).

## Decyzja

1. **Rejestr osób nazwanych jest danymi organizacyjnymi bez zasięgu projektu**, z własną parą
   uprawnień `PEOPLE_READ`/`PEOPLE_WRITE`, odmową zasobu (`403`) i bez funkcji-strażnika zasięgu
   (ADR-0001 aneks 2026-09-19 SC-2-01 — kryterium strukturalne spełnione: wiersz osoby nie ma
   kolumny wiążącej go z projektem, użytkownikiem, jednostką biznesową ani najemcą; P-2 wyklucza
   powiązanie z użytkownikiem). Szczegóły uprawnień: ADR-0005 aneks 2026-09-27 (SC-2-06).
2. **Zbiór kolumn wiersza osoby** — dokładnie pkt 3 oceny wpływu, z ograniczeniem postaci
   kanonicznej `full_name` w bazie.
3. **Rejestr zwraca wyłącznie wiersze osób** — nigdy przypisań, pozycji, scenariuszy ani projektów,
   do których osoba jest przypisana. Pierwsze zapytanie odwrotne (osoba → przypisania) przechodzi
   przez `project_for_caller` per projekt i wymaga własnego aneksu tutaj i w ADR-0001.
4. **Imię poza migawką, przypisanie w grupie 2** — pkt 7 oceny wpływu; ADR-0004 aneks 2026-09-27.
5. **Wyłącznie dane fikcyjne do spełnienia warunków (a)–(e) z pkt 8.**
6. **NF-11:** imię nigdy w logu, URL ani komunikacie odmowy — pkt 6.
7. **Stawka indywidualna, nieobecności na osobie, osoby poddostawcy, powiązanie z kontem
   użytkownika** — poza tą decyzją; każde wymaga aneksu tutaj przed kodem (granice z pkt 5).

## Konsekwencje

- Pierwszy rejestr danych osobowych w systemie; każda przyszła kolumna na wierszu osoby i każda
  nowa ścieżka zwracająca osobę lub przypisanie jest zmianą tego dokumentu (aneks z datą), nie
  szczegółem implementacji.
- Do czasu ADR uwierzytelniania funkcja jest w działającym systemie nieosiągalna od strony
  odczytu imienia — dowód gałęzi pozytywnej wyłącznie przez podstawienie tożsamości w teście.
- Pola, które dotąd "pośrednio wskazywały osobę przy `headcount = 1`" (nieobecności, budżet
  urlopowy, koszty dodatkowe przy pozycji), wskazują ją **wprost** dla wołającego z `PEOPLE_READ`
  — rozstrzygnięcie w Story Q-4 = (c), obowiązkowo przed nadaniem `PEOPLE_READ` komukolwiek w
  działającym systemie.

## Rozważane alternatywy

- **Osoba jako konto użytkownika (`project_access.user_id`)** — odrzucone decyzją P-2: wiązałoby
  planowanie obsady z uwierzytelnianiem, którego nie ma, i czyniłoby każdą osobę planowaną
  potencjalnym podmiotem logowania.
- **Imię jako wolny tekst na `staffing_position`** (bez rejestru) — odrzucone: sprostowanie
  wymagałoby edycji każdej pozycji w każdym scenariuszu, w tym `approved` (zablokowanych
  strażnikiem), a anonimizacja — przeszukiwania wolnego tekstu.
- **Imię w migawce zatwierdzonego scenariusza** — odrzucone (pkt 7): nieusuwalna dana osobowa.
- **Szyfrowanie kolumny `full_name`** — odrzucone na teraz (pkt 9).

## Pytania do człowieka

- **PD-1 — podstawa prawna.** (a) art. 6 ust. 1 lit. f (rekomendacja); (b) lit. b/c dla
  pracowników; (c) zgoda — nierekomendowana. Rozstrzyga administrator danych/IOD, nie Architekt.
- **PD-2 — reguła retencji** — do Story usuwania; tu wyłącznie potwierdzenie, że nie blokuje
  SC-2-06 przy zakazie danych rzeczywistych.
- **PD-3 — homonimy.** (a) tylko `full_name`, rozróżnienie po `id` (rekomendacja dla SC-2-06);
  (b) dodatkowa etykieta rozróżniająca — więcej danych, nowa kolumna, aneks.
- **PD-4 — kształt pola osoby przy pozycji dla wołającego z `PEOPLE_READ`** — patrz mapa wpływu
  SC-2-06, L-2 (sam identyfikator vs identyfikator i imię).
- **PD-5 — uprawnienie zapisu przypisania** — patrz ADR-0005 aneks 2026-09-27 pkt 5 (L-1b).

## Kontrole

| Kontrola | Kryterium akceptacji |
|---|---|
| PD-K1 | Zbiór kolumn wiersza osoby odczytany z `information_schema.columns` jest równy zbiorowi z pkt 3 oceny wpływu; mutacja "dodana kolumna `note`" pada. |
| PD-K2 | Wołający z każdym uprawnieniem poza `PEOPLE_READ` dostaje odmowę odczytu rejestru, a ciało odpowiedzi nie zawiera imienia; z `PEOPLE_READ` — imię widoczne (kontrast). |
| PD-K3 | Pozycja z przypisaną osobą, odczytana bez `PEOPLE_READ` na każdej ścieżce zwracającej pozycję, ma ten sam zbiór pól i te same wartości (poza `id`/`updated_at`) co pozycja anonimowa. |
| PD-K4 | Nieudane utworzenie i nieudane sprostowanie osoby na ograniczeniu postaci kanonicznej nie wynosi imienia do logu; ten sam zapis z pominięciem ochrony wynosi je (kontrast). |
| PD-K5 | Sprostowanie imienia osoby przypisanej do pozycji scenariusza `approved` się udaje, jest widoczne dla `PEOPLE_READ`, a liczba wierszy każdej tabeli `approved_snapshot_*` i ich zbiór nie zmieniają się. |
| PD-K6 | `PLACEHOLDER_PERMISSIONS` nie zawiera `PEOPLE_READ` ani `PEOPLE_WRITE` (kanarek równości zbioru bez zmian). |

## Powiązane wymagania

F-03, F-11, F-12, F-13, AC-02, AC-06, NF-04, NF-06, NF-11; decyzje człowieka 2026-09-27 (Issue #31):
P-2, Q-1..Q-7.
