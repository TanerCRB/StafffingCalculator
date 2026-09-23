# ADR-0005 — Model dostępu i uprawnień

**Status:** Accepted

## Kontekst

F-13: role administrator / calculation author / read-only viewer; dostęp ograniczany do
wybranych projektów; "Permission to view individual personnel costs shall be separable from
permission to view aggregate results"; "Access restrictions shall also apply to exports and
server interfaces." AC-06: użytkownik bez uprawnień do kosztów osobowych nie widzi tych danych
ani w UI, ani w eksporcie, ani w odpowiedzi API. NF-04: autoryzacja egzekwowana po stronie
serwera.

## Decyzja

Dwuwymiarowy model uprawnień: **rola** (`admin` | `author` | `viewer`) określa czynności
(tworzenie/edycja vs. tylko odczyt), **zasięg projektu** (`project_access` — tabela łącząca
użytkownika z projektem) określa *które* projekty użytkownik w ogóle widzi. Trzeci, niezależny
wymiar: uprawnienie `can_view_personnel_costs` (bool, per przypisanie użytkownik-projekt) —
niezależne od roli, bo administrator projektu niekoniecznie ma widzieć indywidualne stawki
kosztowe (F-13 wprost oddziela te dwa uprawnienia).

Egzekwowanie w jednym miejscu: warstwie serializacji odpowiedzi (response shaping), przez którą
przechodzi każdy endpoint zwracający dane projektu — pola kosztów osobowych są usuwane z payloadu
na tym poziomie, nie ukrywane w UI. Ta sama warstwa obsługuje eksporty PDF/spreadsheet (F-11) —
generator raportu woła te same funkcje dostępowe co API, nigdy nie czyta bazy bezpośrednio z
pominięciem warstwy uprawnień.

## Konsekwencje

- Nowy endpoint bez zadeklarowanego wymogu uprawnień kończy się odmową, nie przepuszczeniem
  (reguła Strażnika Niezmienników nr 7, analogicznie do wzorca oryginalnego frameworku).
  Test odmowy jest obowiązkowy dla każdego nowego uprawnienia (reguła nr 8).
- AC-06 staje się testem integracyjnym trzech ścieżek na raz: odpowiedź API, wygenerowany
  eksport, i (jeśli dotyczy) SSR/props przekazywane do frontendu — wszystkie trzy przechodzą
  przez tę samą funkcję filtrującą, więc jeden test pokrywa je strukturalnie, nie przypadkowo.
- Odwołanie dostępu (`project_access` usunięte) działa natychmiast przy następnym zapytaniu —
  nie czeka na wygaśnięcie tokenu sesji.

## Rozważane alternatywy

- **Uprawnienia wyłącznie po roli (RBAC bez zasięgu projektu)** — odrzucone: F-13 wprost wymaga
  ograniczenia do *wybranych* projektów, nie globalnej roli.
- **Filtrowanie kosztów osobowych w warstwie UI (frontend ukrywa pola)** — odrzucone wprost przez
  AC-06 i NF-04: dane nie mogą nawet dotrzeć do klienta, filtrowanie samym UI zostawia je w
  odpowiedzi sieciowej.

## Powiązane wymagania

F-13, NF-04, F-11 (eksporty), AC-06

## Aneksy

### 2026-09-18 — tożsamość wołającego dla SC-1-05/06 (odstępstwo czasowe)

W repozytorium nie istnieje jeszcze żaden mechanizm uwierzytelniania (brak tabeli użytkowników,
brak sesji/tokenu) — ten ADR zakłada istnienie `user`, którego dziś nic nie tworzy. Dla zadań
SC-1-05 (lista projektów) i SC-1-06 (ekran listy) przyjmuje się **jawne, czasowe odstępstwo**:
tożsamość wołającego pochodzi z ustalonego, testowego identyfikatora (np. nagłówek/zmienna
konfiguracyjna), nie z prawdziwego uwierzytelniania. Kryteria akceptacji tych zadań dowodzą
działania **filtra `project_access`**, nie samego uwierzytelniania — ten podział musi być jawnie
zapisany w sekcji "co to nie dowodzi" raportu Developera i w rejestrze możliwości (gate 3).
**Warunek zamknięcia:** osobny ADR uwierzytelniania, wymagany przed jakimkolwiek zadaniem
wystawiającym ten mechanizm poza środowisko deweloperskie/testowe.

### 2026-09-18 — placeholder tożsamości obejmuje SC-1-01 i uprawnienie zapisu (rozszerzenie odstępstwa)

Odstępstwo z aneksu powyżej nazwane było dla SC-1-05/06 i było w praktyce wyłącznie odczytowe
(`PLACEHOLDER_PERMISSIONS = {PROJECT_READ}`). SC-1-01 (utworzenie/odczyt Projektu) rozszerza je
na dwa sposoby i oba wymagają zapisu, nie domysłu:

1. **Zakres zadań.** Odstępstwo obejmuje także SC-1-01. Podstawa niezmieniona: nadal nie istnieje
   żaden mechanizm uwierzytelniania, a kryteria akceptacji SC-1-01 dowodzą filtra `project_access`
   i egzekwowania uprawnienia per endpoint — nie dowodzą uwierzytelniania.
2. **Uprawnienie akcji w zestawie placeholdera.** `PLACEHOLDER_PERMISSIONS` zawiera teraz również
   `PROJECT_CREATE`, bo inaczej `POST /projects` byłby nieosiągalny, dopóki każdy wołający jest tym
   jednym ustalonym placeholderem. To **nie jest** decyzja, że każdy może tworzyć projekty. Decyzja
   przypisuje czynności wymiarowi roli (`admin`/`author`/`viewer`), a wymiar roli nie jest jeszcze
   modelowany — do czasu ADR uwierzytelniania placeholder zwija ten wymiar i każdy wołający jest
   faktycznie `author`. Uprawnienie jest deklarowane i egzekwowane per endpoint
   (`require_permission`); nierozstrzygnięte jest wyłącznie to, kto je posiada.

Skutek uboczny wart nazwania: odstępstwo przestaje być odczytowe — nieuwierzytelniony nagłówek
tworzy teraz wiersze, nie tylko je czyta.

Granica bez zmian: `APP_ALLOW_PLACEHOLDER_IDENTITY` (domyślnie `false`) plus ograniczenie do
środowisk `development`/`test`, egzekwowane odmową startu aplikacji. Granica środowiska ogranicza
*gdzie* odstępstwo działa, nie *co* wolno w jego ramach — każde kolejne poszerzenie zestawu
uprawnień placeholdera wymaga własnego, datowanego wpisu tutaj.

**Warunek zamknięcia:** bez zmian — osobny ADR uwierzytelniania, wymagany przed jakimkolwiek
zadaniem wystawiającym ten mechanizm poza środowisko deweloperskie/testowe.

### 2026-09-18 — uprawnienia akcji zapisu na Projekcie i dostęp do kopii (SC-1-02..04)

Poprzedni aneks domyka się zdaniem: "każde kolejne poszerzenie zestawu uprawnień placeholdera
wymaga własnego, datowanego wpisu tutaj". SC-1-02 (edycja), SC-1-03 (kopiowanie) i SC-1-04
(archiwizacja) są takim poszerzeniem.

1. **Uprawnienia akcji — osobne, nie jedno wspólne.** `PROJECT_EDIT`, `PROJECT_COPY`,
   `PROJECT_ARCHIVE` — ziarnistość odpowiadająca temu, że archiwizacja bywa uprawnieniem innej
   osoby niż edycja (np. tylko administrator projektu archiwizuje, ale każdy autor edytuje).
   Podstawa niezmieniona: wymiar roli (`admin`/`author`/`viewer`) nadal nie jest modelowany, więc
   placeholder zwija go i każdy wołający jest faktycznie `author`. To nie jest decyzja, że każdy
   może edytować, kopiować i archiwizować projekty — nierozstrzygnięte pozostaje wyłącznie to, kto
   te uprawnienia posiada.
2. **`PLACEHOLDER_PERMISSIONS` rośnie** o uprawnienia z punktu 1. Granica bez zmian:
   `APP_ALLOW_PLACEHOLDER_IDENTITY` (domyślnie `false`) plus ograniczenie do środowisk
   `development`/`test`, egzekwowane odmową startu aplikacji.
3. **Odmowa na projekcie spoza zasięgu wołającego jest nieodróżnialna od nieistnienia — także dla
   zapisu.** Akcja rozwiązuje swój cel przez tę samą funkcję warstwy dostępu co odczyt (ADR-0001,
   aneks 2026-09-18), która nie zwraca powodu — endpoint zapisu nie ma z czego zbudować
   odpowiedzi "istnieje, ale nie twój". Kody odmowy specyficzne dla zapisu (konflikt
   współbieżności — ADR-0007, odmowa wynikająca z ADR-0004) nie mogą stać się ubocznym
   potwierdzeniem istnienia projektu.
4. **Dostęp do kopii (SC-1-03).** Kopia otrzymuje wiersz `project_access` wyłącznie dla
   wołającego, który ją wykonał — tak jak projekt utworzony od zera (spójne z już przetestowanym
   `create_project`). Wiersze `project_access` źródła **nie są replikowane**: nadanie dostępu jest
   osobną czynnością i nie dzieje się jako skutek uboczny kopiowania. **Konsekwencja przyjęta
   razem z tym aneksem:** kopia projektu zespołowego jest początkowo niewidoczna dla zespołu — to
   musi być powiedziane wykonującemu kopię, nie odkryte później.

### 2026-09-19 — każde uprawnienie akcji zapisu daje w praktyce odczyt całego Projektu (weryfikacja SC-1-02..04)

Security-auditor przy gate 2 zauważył, że `PATCH`, `POST .../copy` i `POST .../archive` zwracają
`ProjectDetail` (włącznie z `owner` — dane osobowe) każdemu wołającemu, który ma odpowiednio tylko
`PROJECT_EDIT`, `PROJECT_COPY` lub `PROJECT_ARCHIVE` — bez wymogu `PROJECT_READ`. Punkt 1 wyżej
rozdziela te trzy uprawnienia od siebie właśnie po to, żeby administrator archiwizujący projekt
nie musiał być tą samą osobą, co autor edytujący go — ale żadne z trzech uprawnień nie było
zamyślane jako uprawnienie odczytu, a w obecnym kształcie odpowiedzi każde z nich nim jest.

**Zaakceptowane, nienaprawione teraz:** dziś nieszkodliwe, bo placeholder daje każdemu
wołającemu wszystkie pięć uprawnień naraz (punkt 2 poprzedniego aneksu) — nie istnieje jeszcze
wołający z węższym zestawem, więc luka jest utajona, nie aktywna. Staje się aktywna dokładnie w
momencie, gdy wymiar roli (`admin`/`author`/`viewer`, wspomniany w punkcie 1) przestaje być
zwinięty przez placeholder — czyli z ADR uwierzytelniania. **Warunek zamknięcia:** to ADR musi
rozstrzygnąć jedno z dwóch, zanim rola węższa niż `author` zacznie cokolwiek wołać: (a) każde z
`PROJECT_EDIT`/`PROJECT_COPY`/`PROJECT_ARCHIVE` niesie ze sobą także `PROJECT_READ` tego
Projektu — nazwane wprost, nie domyślne; albo (b) odpowiedzi tych trzech endpointów przestają
być pełnym `ProjectDetail` dla wołającego bez `PROJECT_READ`.

### 2026-09-19 — bramka kosztów osobowych jako koniunkcja dwóch mechanizmów (SC-1-08)

Decyzja nazywa trzeci wymiar jednym mechanizmem: "`can_view_personnel_costs` (bool, per
przypisanie użytkownik-projekt)". Kod ma dwa: `Permission.PERSONNEL_COSTS_READ` w zbiorze
uprawnień tożsamości (`backend/app/core/identity.py`) oraz kolumnę
`project_access.can_view_personnel_costs`, której dziś nie czyta nic. Rejestr możliwości nazywa
tę rozbieżność otwartą luką (R-03: "kształt per-caller zamiast per-project-assignment").
SC-1-08 ją domyka — a domknięcie jest uściśleniem decyzji, nie jej odczytaniem, więc wymaga
zapisu tutaj.

1. **Koniunkcja.** Pole kosztu osobowego jest widoczne wtedy i tylko wtedy, gdy
   `caller.has(PERSONNEL_COSTS_READ)` **oraz** `project_access.can_view_personnel_costs` dla
   pary (wołający, projekt) jest prawdą. Brak wiersza `project_access` nie jest osobnym
   przypadkiem: projekt spoza zasięgu nigdy nie dociera do warstwy kształtowania (ADR-0001,
   aneks 2026-09-18). Wartość nieustalona albo nieprzekazana do warstwy kształtowania znaczy
   `false` — bramka zamyka się, a nie otwiera, gdy nie wie.
2. **Co znaczy każda z połówek.** `PERSONNEL_COSTS_READ` odpowiada na pytanie, czy wołający
   *w ogóle* może widzieć koszty osobowe; `can_view_personnel_costs` zawęża to do konkretnych
   przypisań. Zdanie decyzji "niezależne od roli" pozostaje w mocy w kierunku, w którym zostało
   napisane: rola nie nadaje kosztów. Koniunkcja jest wyłącznie zawężająca — wobec pierwotnego
   tekstu nie otwiera dostępu nikomu, komu tekst go nie dawał.
3. **`PERSONNEL_COSTS_READ` jest zastępnikiem epoki placeholdera, nie trwałym wymiarem.**
   Rozstrzygnięte (gate 1, SC-1-08): to uprawnienie nie jest niezależnym, trwałym atrybutem
   podmiotu/roli — jest tym, co placeholder ma zamiast prawdziwej tożsamości, dokładnie jak
   `PROJECT_EDIT`/`PROJECT_COPY`/`PROJECT_ARCHIVE` w aneksie z 2026-09-18. Gdy zjawi się ADR
   uwierzytelniania, `PERSONNEL_COSTS_READ` zostaje *wyprowadzone* ze zbioru wierszy
   `project_access` wołającego (np. "ma co najmniej jedno przypisanie z flagą `true`"), a nie
   utrzymywane jako osobny, ręcznie nadawany bit — inaczej dwa magazyny prawdy muszą zostać
   zsynchronizowane na zawsze. **Warunek zamknięcia:** ADR uwierzytelniania koduje to
   wyprowadzenie, zanim pojawi się pierwszy wołający z tożsamością inną niż placeholder; do tego
   czasu koniunkcja zostaje jako jest.
4. **Nikt nie nadaje dziś tej flagi.** `create_project` i `copy_project` wstawiają wiersz
   `ProjectAccess` bez tej kolumny, czyli `false` (`server_default`), i nie istnieje żadna
   ścieżka zapisu ustawiająca ją na `true`. Zostaje tak — jest to spójne z aneksem 2026-09-18
   p. 4 ("nadanie dostępu jest osobną czynnością i nie dzieje się jako skutek uboczny").
   **Konsekwencja przyjęta razem z tym aneksem:** po SC-1-08 gałąź pozytywna bramki jest w
   działającym systemie nieosiągalna — twórca projektu nie widzi jego kosztów osobowych — i daje
   się dowieść wyłącznie testem podstawiającym oba czynniki naraz. To musi być powiedziane
   wprost w rejestrze możliwości, nie odkryte później.
5. **Zbiór uprawnień placeholdera bez zmian.** `PLACEHOLDER_PERMISSIONS` nadal nie zawiera
   `PERSONNEL_COSTS_READ`, a kanarek równości zbiorów tego pilnuje. Dodanie go — choćby po to,
   by uzyskać dowód end-to-end gałęzi pozytywnej — byłoby poszerzeniem odstępstwa i wymaga
   własnego, datowanego wpisu tutaj. Ten aneks go nie obejmuje: dowód gałęzi pozytywnej idzie
   przez `app.dependency_overrides[get_caller_identity]` w teście, wzorem
   `backend/tests/test_project_detail_personnel_costs.py`.
6. **Skąd warstwa kształtowania bierze flagę.** Nie z własnego zapytania. Decyzja stawia
   egzekwowanie w warstwie serializacji i równocześnie wymaga, by generatory odpowiedzi
   "wołały te same funkcje dostępowe co API, nigdy nie czytały bazy bezpośrednio z pominięciem
   warstwy uprawnień" — flaga przychodzi więc razem z wierszem z `app.data.project_reads`,
   jednym zapytaniem na żądanie, nie jednym na wiersz listy. `response_shaping` nie otrzymuje
   `Session`. Jeśli flaga trafi do warstwy kształtowania przez relację ORM, relacja musi być
   zawężona do `caller.user_id`: wczytanie wszystkich wierszy `project_access` projektu
   ujawniłoby, kto jeszcze ma do niego dostęp, i przeczyłoby zasadzie, że zasięg jest filtrem
   bazy, a nie zbiorem noszonym w pamięci.
7. **Czego ten aneks nie domyka.** Luka z aneksu 2026-09-19 (każde uprawnienie akcji zapisu daje
   w praktyce odczyt całego `ProjectDetail`) zostaje otwarta. Koniunkcja ją zawęża — pola
   kosztowe w odpowiedzi `PATCH`/`copy`/`archive` są bramkowane podwójnie — ale nie zmienia tego,
   że `owner` wraca do wołającego bez `PROJECT_READ`. Warunek zamknięcia tamtego aneksu bez zmian.

### 2026-09-19 — pierwszy zbiór danych bez zasięgu projektu i asymetria bramki kosztowej (SC-2-01)

Katalog wymiarów roli (rola / senioritet / lokalizacja / typ zaangażowania) ze stawkami domyślnymi
(F-03, NF-10) jest pierwszymi danymi w tym systemie, które **nie należą do żadnego projektu**.
Wszystkie zdania tej decyzji o zasięgu są ograniczone do projektów ("które *projekty* użytkownik
w ogóle widzi", "każdy endpoint zwracający *dane projektu*"), więc decyzja danych organizacyjnych
nie zakazuje — ale i nie przewiduje. Rozstrzygnięcie (bramka 1, SC-2-01):

1. **Dane organizacyjne bez zasięgu — dopuszczone wprost.** Tabele katalogu nie mają wiersza
   `project_access` i nie przechodzą przez filtr zasięgu. Zasięg projektu pozostaje filtrem bazy
   dla danych projektu; brak filtra na katalogu jest decyzją, nie przeoczeniem. Kryterium jest
   twarde i wąskie: wiersz nie należy do żadnego projektu i nie ma żadnego predykatu per wołający.
   Pierwsza tabela, której wiersz da się przypisać do projektu, jednostki biznesowej albo najemcy,
   przestaje być objęta tym punktem i wymaga własnego wpisu tutaj.
2. **Uprawnienia katalogu: `CATALOG_READ` i `CATALOG_WRITE`, nowe.** `PROJECT_READ` nie zostaje
   rozciągnięte: uprawnienie o nazwie mówiącej "projekt" otwierające tabelę bez projektu byłoby
   dokładnie tą rozbieżnością nazwy i mechanizmu, którą aneks 2026-09-19 (SC-1-08) domykał.
   Rozdział odczytu od zapisu jest tu wymogiem NF-10: katalog edytuje administrator organizacji,
   czyta każdy, kto planuje staffing. Każde z nich ma obowiązkowy test odmowy.
3. **Bramka stawki kosztowej poza kontekstem projektu ma JEDEN czynnik — i to jest osłabienie,
   nazwane jako takie.** Aneks 2026-09-19 pkt 1 stawia koniunkcję jako "wtedy i tylko wtedy" i
   każe traktować brak drugiego czynnika jako `false`. Literalnie zastosowane do katalogu zamyka
   bramkę na zawsze. Ten aneks tworzy wyjątek, wąski i kierunkowy: **poza kontekstem projektu**
   stawkę kosztową katalogu strzeże samo globalne `Permission.PERSONNEL_COSTS_READ`, bo drugi
   czynnik (`project_access.can_view_personnel_costs`) nie ma tu podmiotu — nie istnieje projekt,
   względem którego mógłby być prawdą lub fałszem.
   **Asymetria, przyjęta świadomie:** ta sama nazwa uprawnienia znaczy od teraz dwie różne rzeczy.
   W odpowiedzi niosącej projekt jest *jednym z dwóch* warunków (mechanizm mocniejszy, ziarnistość
   per przypisanie). W odpowiedzi katalogu jest *całą* bramką (mechanizm słabszy, ziarnistość per
   wołający, żadnego zawężenia). Reguła kierunkowa, która z tego wynika i której nie wolno
   odwrócić: **wyjątek obowiązuje wyłącznie tam, gdzie projektu nie ma.** W chwili, gdy stawka
   katalogowa trafia do odpowiedzi opisującej projekt albo scenariusz (rozwiązana stawka pozycji
   staffingowej, blok 3-5), obowiązuje koniunkcja z aneksu 2026-09-19 bez zmian — nie da się
   odczytać stawki kosztowej projektu "przez katalog", omijając flagę przypisania.
4. **Drugie miejsce egzekwowania w warstwie kształtowania.** Decyzja stawia egzekwowanie "w jednym
   miejscu: warstwie serializacji odpowiedzi". Katalog nie ma `CallerProjectView`, więc nie
   przechodzi przez `_without_personnel_costs` ani przez asercję zgodności podmiotu widoku i
   wołającego (aneks 2026-09-19 pkt 6). Konsekwencja przyjęta razem z tym aneksem: "jedno miejsce"
   staje się dwiema funkcjami kształtującymi o różnych wejściach bramki. Wymóg pozostaje ten sam —
   pole jest usuwane z payloadu po stronie serwera, nie ukrywane w UI (AC-06, NF-04) — i ta sama
   funkcja kształtująca obsługuje eksport (F-11).
5. **Kształt odmowy: wybielenie pola, nie odmowa zasobu.** Precedens K-03 (SC-1-08) — "to odmowa
   pola, nie projektu", status 200 z polem `None`. Wiersz katalogu bez widocznej stawki kosztowej
   wraca jako wiersz z pustym polem kosztowym, nie jako 403 i nie jako brak wiersza: sam fakt
   istnienia roli, senioritetu czy lokalizacji nie jest daną chronioną. Reguła nieodróżnialności
   od nieistnienia (aneks 2026-09-18 pkt 3) **nie rozciąga się na katalog** — istniała, by nie
   potwierdzać istnienia projektu spoza zasięgu; katalog nie ma zasięgu, więc nie ma czego ukrywać.
6. **Placeholder rośnie o `CATALOG_*`, i tylko o nie.** Zgodnie z regułą "każde kolejne poszerzenie
   zestawu uprawnień placeholdera wymaga własnego, datowanego wpisu tutaj" (aneks 2026-09-18):
   `PLACEHOLDER_PERMISSIONS` obejmuje `CATALOG_READ` i `CATALOG_WRITE`. `PERSONNEL_COSTS_READ`
   nadal do niego **nie należy** (aneks 2026-09-19 pkt 5, kanarek równości zbiorów).
   **Konsekwencja przyjęta razem z tym aneksem:** gałąź pozytywna stawki kosztowej katalogu jest w
   działającym systemie nieosiągalna — żaden dzisiejszy wołający nie ma `PERSONNEL_COSTS_READ` —
   i daje się dowieść wyłącznie testem podstawiającym tożsamość (`dependency_overrides`). To musi
   być powiedziane wprost w rejestrze możliwości, nie odkryte później. Granica bez zmian:
   `APP_ALLOW_PLACEHOLDER_IDENTITY`, środowiska `development`/`test`.
7. **Warunek zamknięcia — nowy, wobec ADR uwierzytelniania.** Aneks 2026-09-19 pkt 3 zobowiązuje
   ADR uwierzytelniania do *wyprowadzenia* `PERSONNEL_COSTS_READ` ze zbioru wierszy
   `project_access` wołającego ("ma co najmniej jedno przypisanie z flagą `true`"). Po tym aneksie
   takie wyprowadzenie znaczy więcej, niż znaczyło: wgląd w koszty **jednego** projektu otwierałby
   **całą** organizacyjną tabelę stawek kosztowych. ADR uwierzytelniania musi rozstrzygnąć to
   wprost — albo (a) tak, wgląd w koszty jakiegokolwiek projektu daje wgląd w katalog stawek
   (nazwane, nie uboczne), albo (b) katalog dostaje własne uprawnienie kosztowe, niezależne od
   przypisań projektowych. Do tego czasu punkt 3 obowiązuje jako jest.

   **Druga połowa tego samego ryzyka, dopisana po weryfikacji SC-2-01 (security-auditor,
   2026-09-19):** wariant (a) nie tylko poszerza to, co pokazuje katalog — on też *odwraca*
   zawężenie, które SC-1-08 wprowadziło. Wołający z `can_view_personnel_costs=true` na jednym
   projekcie i `false` na drugim, którego pozycje staffingowe wycenia domyślna stawka z katalogu,
   dostaje z katalogu dokładnie tę stawkę bez żadnego filtra (K-01) — i może ją przyłożyć do
   pozycji drugiego projektu ręcznie, z pamięci, całkowicie omijając flagę przypisania. Punkt 3
   zamyka to wyłącznie dla stawki podróżującej *wewnątrz* odpowiedzi projektu/scenariusza; nie
   zamyka złożenia "katalog + osobny odczyt projektu w tym samym żądaniu przez tego samego
   wołającego". Wybór (a) musi to nazwać jako świadomie przyjętą konsekwencję, nie odkryć jej przy
   pierwszym zadaniu bloku 4/5.

### 2026-09-19 — pozycje obsady: zasięg dziedziczony przez scenariusz, uprawnienia STAFFING_* (SC-3-01)

Aneks 2026-09-19 (SC-2-01) pkt 1 domyka się zdaniem: "Pierwsza tabela, której wiersz da się
przypisać do projektu, jednostki biznesowej albo najemcy, przestaje być objęta tym punktem i wymaga
własnego wpisu tutaj." Pozycja obsady scenariusza i jej alokacja miesięczna (F-04, SC-3-01) są tą
tabelą. To wpis, którego tamto zdanie wymaga.

1. **Zasięg bez nowej decyzji.** Wiersz pozycji należy do projektu przez `staffing_position
   .scenario_id → scenarios.project_id` (kolumna `NOT NULL`), więc obowiązuje filtr `project_access`
   z "Decyzji" — bez nowego wymiaru i bez własnej tabeli dostępu. Wyjątek "dane organizacyjne bez
   zasięgu" (aneks 2026-09-19 pkt 1) tej tabeli **nie** obejmuje i nie wolno go tu rozciągać:
   kryterium tamtego punktu jest strukturalne ("wiersz nie należy do żadnego projektu"), a tutaj
   należy.
2. **Uprawnienia: `STAFFING_READ` i `STAFFING_WRITE`, nowe — ale z innego powodu niż `CATALOG_*`.**
   Argument katalogowy (uprawnienie mówiące "projekt" otwierające tabelę bez projektu) tu nie
   obowiązuje, bo projekt istnieje. Obowiązuje argument ziarnistości akcji z aneksu 2026-09-18
   pkt 1: planowanie obsady jest rutynowo prawem innej osoby niż edycja nagłówka projektu, a
   uprawnienia raz zlanego w jedno "zapis do projektu" nie da się później zawęzić bez złamania
   wołających. Rozdział odczytu od zapisu jak w `CATALOG_*`. Każde z dwóch ma obowiązkowy test
   odmowy ("Konsekwencje").
3. **`PLACEHOLDER_PERMISSIONS` rośnie o `STAFFING_READ`/`STAFFING_WRITE`, i tylko o nie.**
   `PERSONNEL_COSTS_READ` nadal do niego nie należy (aneks 2026-09-19 pkt 5) — kanarek równości
   zbiorów zostaje przezbrojony, nie poluzowany. Granica bez zmian:
   `APP_ALLOW_PLACEHOLDER_IDENTITY`, środowiska `development`/`test`.
4. **Nieodróżnialność obejmuje obie ścieżki.** Pozycja w scenariuszu spoza zasięgu wołającego jest
   nieodróżnialna od nieistniejącej — `404`, nigdy `403`, także dla zapisu (aneks 2026-09-18 pkt 3).
   Scenariusz należący do innego projektu niż ten w adresie jest tym samym przypadkiem. Kody odmowy
   specyficzne dla zapisu (`409` z ADR-0007, odmowa z ADR-0004, `422` walidacji) nie mogą stać się
   ubocznym potwierdzeniem, że pozycja albo scenariusz istnieje.
5. **Bramka kosztów osobowych — SC-3-01 nie niesie żadnej stawki.** Rozstrzygnięte (bramka 1):
   odpowiedź pozycji obsady zwraca krotkę wymiarów katalogu, headcount i godziny — żadnego pola
   kosztowego ani rozstrzygniętej stawki. Kierunek wyjątku z aneksu 2026-09-19 pkt 3 (koniunkcja
   obowiązuje, gdy stawka trafia do odpowiedzi opisującej scenariusz) zostaje więc **nieaktywowany
   przez to zadanie** — nazwane wprost, nie ukryte jako "gotowe": pierwsze zadanie faktycznie
   pokazujące rozwiązaną stawkę na pozycji (F-07, blok 5) musi odtworzyć koniunkcję i dowieść jej
   własnym kryterium; kierunek wyjątku pozostaje nieudowodniony (`docs/architecture/capabilities.md`).
6. **Luka z aneksu 2026-09-19 rozszerza się, nie zamyka.** `STAFFING_WRITE` zwracające pełną
   reprezentację pozycji jest w praktyce uprawnieniem odczytu tej pozycji — ta sama luka co dla
   `PROJECT_EDIT`/`COPY`/`ARCHIVE`, na nowej tabeli. Warunek zamknięcia bez zmian (ADR
   uwierzytelniania rozstrzyga (a) albo (b)); dziś utajona, bo placeholder daje wszystko naraz.

### 2026-09-21 — stawki poddostawców w katalogu bez zasięgu; bramka kosztowa obejmuje cenę kontrahenta (SC-2-03)

Aneks z 2026-09-19 (SC-2-01) pkt 1 kończy się zdaniem: "Pierwsza tabela, której wiersz da się
przypisać do projektu, jednostki biznesowej albo najemcy, przestaje być objęta tym punktem i wymaga
własnego wpisu tutaj." Wiersz stawki z kolumną poddostawcy da się przypisać do podmiotu
gospodarczego — nie do projektu i nie do wołającego, ale wystarczająco blisko, by wymagać wpisu
zamiast interpretacji.

1. **Zasięg: bez zmian.** Wiersz stawki poddostawcy nadal nie należy do żadnego projektu i żaden
   endpoint nie zawęża go po tożsamości wołającego. Poddostawca jest kontrahentem, nie podmiotem, w
   imieniu którego działa wołający (ADR-0001, aneks z tą samą datą, pkt 1). Kryterium "wołający z
   zerowym `project_access` widzi ten sam katalog co każdy inny" (SC-2-01, K-01) obowiązuje bez
   zmian i obejmuje wiersze poddostawców.
2. **Uprawnienia: bez zmian — decyzja biznesowa rozstrzygnięta wprost na bramce 1 (Issue #46,
   2026-09-21).** `CATALOG_READ`/`CATALOG_WRITE` obejmują też słownik poddostawców i stawki
   poddostawców. **Konsekwencja przyjęta świadomie, nie odkryta później:** każdy, kto planuje
   staffing, widzi cennik każdego poddostawcy — w wielu organizacjach dane objęte umową o
   poufności, chronione ostrzej niż wewnętrzna stawka kosztowa.
3. **Bramka stawki kosztowej obejmuje stawkę kosztową poddostawcy — i jest wobec niej ochroną
   nadpłaconą, nazwaną jako taka.** `CATALOG_PERSONNEL_COST_FIELDS` zostaje jednoelementowy;
   `default_cost_rate` wiersza z poddostawcą jest wybielany na tych samych zasadach co wewnętrzny.
   **To nie jest twierdzenie, że cena płacona firmie jest indywidualnym kosztem osobowym w
   rozumieniu NF-11/AC-06 — nie jest.** Jest to decyzja, żeby nie wprowadzać drugiej bramki na
   jednym polu i nie zmuszać warstwy kształtowania do klasyfikowania wiersza ("czy ten wiersz ma
   poddostawcę"). Cena tej decyzji jest nazewnicza i realna: uprawnienie o nazwie mówiącej "koszty
   osobowe" bramkuje od teraz także cenę kontrahenta — ta sama rozbieżność nazwy i mechanizmu, którą
   punkt 2 aneksu z 2026-09-19 odrzucił, odmawiając rozciągnięcia `PROJECT_READ` na katalog.
   Pierwsze zadanie, w którym ta rozbieżność zacznie przeszkadzać, rozdziela te dwie bramki i
   wymaga własnego, datowanego wpisu tutaj.
4. **Widoczność per poddostawca — jawnie poza zakresem, nie przemilczana.** Model "stawka
   poddostawcy X widoczna tylko dla ról projektu współpracujących z X" nie jest przez to zadanie
   wprowadzany. Dwa powody: (a) wymagałby tabeli dostępu dla podmiotu, który nie jest projektem —
   pierwszego predykatu per wołający na tabeli katalogu, wygaszającego zarówno wyjątek "dane
   organizacyjne bez zasięgu" (pkt 1 aneksu z 2026-09-19), jak i zwolnienie z funkcji-strażnika
   (ADR-0001, aneks 2026-09-19 pkt 2); (b) nie istnieje dziś dana, na której mógłby się oprzeć —
   żadna kolumna nie wiąże projektu z poddostawcą — ani tożsamość, na której dałoby się go dowieść.
   **Warunek otwarcia:** wymaga ADR uwierzytelniania oraz własnego, datowanego wpisu tutaj i w
   ADR-0001; nie wolno go wprowadzić jako skutek uboczny zadania o cennikach.
5. **Kształt odmowy bez zmian.** Wiersz stawki poddostawcy bez widocznej stawki kosztowej wraca jako
   `200` z pustym polem kosztowym, nie jako `403` i nie jako brak wiersza (pkt 5 aneksu z
   2026-09-19).
6. **Dane deweloperskie/testowe: wyłącznie syntetyczne cenniki poddostawców, nie realne.**
   Rozstrzygnięte na bramce 2 (audyt bezpieczeństwa, 2026-09-21): dopóki nie istnieje ADR
   uwierzytelniania (pkt 4 wyżej), placeholder identity daje `CATALOG_READ` każdemu wołającemu w
   środowisku `development`/`test` (ADR-0001, aneks 2026-09-19 pkt 6; `deps.py`,
   `assert_identity_mechanism_allowed`). Realny cennik poddostawcy objęty umową o poufności,
   załadowany do takiej bazy, jest czytelny dla każdego, kto dotrze do portu — nie tylko dla osób
   planujących staffing. Żadna baza deweloperska ani testowa (w tym kontenery efemeryczne) nie
   ładuje realnych stawek poddostawców; wyłącznie dane syntetyczne. **Warunek zamknięcia:** ADR
   uwierzytelniania zamyka placeholder identity — od tego momentu decyzja wymaga ponownego
   rozpatrzenia, nie wygasa automatycznie.

### 2026-09-21 — zapis do katalogu z przeglądarki: pole zapisywane bez prawa odczytu (SC-2-04)

Aneksy z 2026-09-19 i 2026-09-21 nazywały dotąd jeden kierunek rozbieżności: uprawnienie zapisu,
które w praktyce daje odczyt (`PROJECT_EDIT`/`COPY`/`ARCHIVE`, potem `STAFFING_WRITE`). SC-2-04
wystawia kierunek odwrotny, i to jako jedyny osiągalny stan działającego systemu:
`create_catalog_rate`/`update_rate` przepuszczają odpowiedź przez tę samą bramkę co odczyt, więc
wołający z `CATALOG_WRITE` i bez `PERSONNEL_COSTS_READ` **wpisuje stawkę kosztową i nie odczytuje
jej z powrotem**. Dotąd dotyczyło to kogoś, kto sam wysyła żądanie HTTP; od SC-2-04 jest to jedno
kliknięcie w przeglądarce, na dodawaniu i na edycji.

1. **Rozstrzygnięcie (bramka 1, P-2: wariant A): przyjęte jako nazwana konsekwencja, zero zmian w
   kodzie backendu poza tym, co wymaga Q-2.** Na edycji (Q-2: wariant A) `PATCH` jest częściowy —
   pominięcie `default_cost_rate` w żądaniu znaczy „bez zmiany"; wołający bez
   `PERSONNEL_COSTS_READ` edytuje pozostałe pola wiersza (okno, waluta, stawka sprzedaży) bez
   dotykania kosztu, którego nigdy nie widział. Odrzucony wariant „żądanie niesie cały wiersz"
   (semantyka `PUT`): albo blokowałby edycję takiemu wołającemu całkowicie, albo pozwoliłby mu
   wpisać wymyśloną wartość kosztu i cicho nadpisać koszt, którego nigdy nie odczytał — utrata
   danych wprowadzona przez formularz, nie przez wyścig. Dowiedzione:
   `backend/tests/test_catalog_edit.py::test_q_2_an_omitted_cost_rate_leaves_the_stored_one_untouched`.
2. **Granica obowiązująca niezależnie od ścieżki (dodawanie i edycja).** Ekran nie odtwarza
   wybielonego pola z pamięci formularza. Wartość wpisana przez człowieka nie wraca do tabeli jako
   stan katalogu; jedynym źródłem wiersza na ekranie jest odpowiedź serwera (ADR-0009, decyzja
   pkt 3). Rekonstrukcja po stronie klienta byłaby tym samym, co „Filtrowanie kosztów osobowych w
   warstwie UI" z „Rozważanych alternatyw" tej decyzji, tylko odwrócone — a skutek (ekran pokazuje
   kwotę, której serwer nie wydał) jest ten sam: o widoczności kosztu decyduje klient.
3. **Konsekwencja przyjęta razem z tym aneksem, nie odkryta później:** po SC-2-04 każda stawka
   kosztowa dodana lub edytowana z UI renderuje się jej autorowi jako „Restricted" — gałąź
   pozytywna bramki jest nieosiągalna, bo `PLACEHOLDER_PERMISSIONS` nie zawiera
   `PERSONNEL_COSTS_READ` (aneks 2026-09-19 pkt 5 i 6, kanarek równości zbiorów). To nie jest
   defekt ekranu i nie wolno go „naprawić" w UI.
4. **Zestaw uprawnień placeholdera bez zmian; jego zasięg praktyczny rośnie po raz drugi.** SC-2-04
   nie dodaje żadnego uprawnienia — `CATALOG_WRITE` jest w zestawie od aneksu 2026-09-19 pkt 6.
   Zmienia się co innego i wymaga nazwania: zapis do katalogu przestaje wymagać ręcznie
   zbudowanego żądania i staje się dostępny dla każdego, kto dotrze do portu środowiska
   `development`/`test` — i od SC-2-04 obejmuje też **modyfikację** istniejących danych
   organizacyjnych, nie tylko dodawanie nowych. Aneks 2026-09-21 pkt 6 (wyłącznie syntetyczne
   cenniki poddostawców w bazach dev/test) obowiązuje bez zmian i po tym zadaniu jest ważniejszy,
   nie mniej ważny. Granica bez zmian: `APP_ALLOW_PLACEHOLDER_IDENTITY`, środowiska
   `development`/`test`.
5. **Zakres słowników objętych formularzem (bramka 1, P-4: wariant A): wszystkie pięć słowników
   (role/senioritety/lokalizacje/typy zaangażowania/poddostawcy) + stawki.** Punkt 2 aneksu z
   2026-09-21 rozstrzygnął, że poddostawca jest „piątym słownikiem katalogu, nie piątym
   mechanizmem" — formularz obejmujący cztery z pięciu przywróciłby asymetrię, którą tamten punkt
   usunął, a formularz stawki i tak wymaga istniejących `id` wszystkich pięciu wymiarów.
6. **Zasięg bez zmian — pod warunkiem, że nic nie zapisuje autora wpisu ani jego zmiany.** Katalog
   nadal nie ma wiersza `project_access` i żaden endpoint nie zawęża go po tożsamości wołającego
   (pkt 1 aneksu z 2026-09-19). Dopisanie kolumny „kto dodał wpis" (audyt, F-12) byłoby pierwszą
   kolumną wiążącą wiersz katalogu z użytkownikiem, wygaszałoby wyjątek „dane organizacyjne bez
   zasięgu" oraz zwolnienie z funkcji-strażnika (ADR-0001, aneks 2026-09-19 pkt 2) — i wymaga
   własnego, datowanego wpisu tutaj oraz w ADR-0001. To samo dotyczy kolumny „kto zmienił"; znacznik
   `updated_at` wprowadzany przez ADR-0007 aneks 2026-09-21 nią nie jest (jest znacznikiem czasu, nie
   podmiotu) i wyjątku nie wygasza. Ten aneks żadnej kolumny podmiotowej **nie** wprowadza.

### 2026-09-22 — kalendarze i typy nieobecności bez zasięgu; instancja nieobecności z zasięgiem dziedziczonym (SC-3-02)

Aneks z 2026-09-19 (SC-2-01) pkt 1 domyka się zdaniem: "Pierwsza tabela, której wiersz da się
przypisać do projektu, jednostki biznesowej albo najemcy, przestaje być objęta tym punktem i wymaga
własnego wpisu tutaj." SC-3-02 tworzy tabele po **obu** stronach tego kryterium naraz i dlatego
wymaga wpisu dwukierunkowego.

1. **Kalendarz roboczy, jego dni i słownik typów nieobecności są danymi organizacyjnymi** — wyjątek
   "dane organizacyjne bez zasięgu" je obejmuje. Kryterium strukturalne spełnione: żadna kolumna nie
   wiąże wiersza z projektem, użytkownikiem, jednostką biznesową ani najemcą, i żaden endpoint nie
   zawęża ich po tożsamości wołającego.
2. **`catalog_locations.calendar_id` nie jest kolumną zasięgu.** Wskazuje inny wiersz organizacyjny,
   nie podmiot, w imieniu którego działa wołający — ta sama podstawa, którą aneks z 2026-09-21 pkt 1
   dał kolumnie `vendor_id`. Wyjątek obowiązuje dalej, także zwolnienie z funkcji-strażnika
   (ADR-0001, aneks z tą samą datą).
3. **Uprawnienia kalendarza i słownika typów: `CATALOG_READ`/`CATALOG_WRITE`, bez nowych.** Są to
   szósty i siódmy słownik katalogu, nie szósty mechanizm — precedens dosłowny z aneksu 2026-09-21
   pkt 2 ("poddostawca jest piątym słownikiem katalogu, nie piątym mechanizmem").
   **`PLACEHOLDER_PERMISSIONS` nie rośnie w tym zadaniu**; kanarek równości zbiorów zostaje bez
   przezbrajania.
4. **Instancja nieobecności NIE jest daną organizacyjną i wyjątku z pkt 1 nie wolno na nią
   rozciągać.** Wiersz należy do projektu przez `staffing_position_absence.position_id →
   staffing_position.scenario_id → scenarios.project_id` — drugi stopień pośredniości po alokacji
   miesięcznej, ten sam mechanizm. Obowiązuje filtr `project_access` z "Decyzji", adres zagnieżdżony,
   zasięg wyłącznie z `project_for_caller` (ADR-0001, aneks 2026-09-19), `404` nigdy `403`, także dla
   zapisu, i żaden kod odmowy specyficzny dla zapisu nie może stać się ubocznym potwierdzeniem
   istnienia pozycji ani scenariusza.
5. **Uprawnienia instancji: `STAFFING_READ`/`STAFFING_WRITE`, bez nowych.** Argument ziarnistości z
   aneksu 2026-09-19 pkt 2 ("planowanie obsady jest rutynowo prawem innej osoby niż edycja nagłówka
   projektu") nie oddziela planowania nieobecności od planowania obsady — to ta sama czynność.
   Osobne `ABSENCE_*` byłoby ziarnistością bez podmiotu, który miałby ją wykonywać.
6. **Granica danych osobowych, postawiona przy tworzeniu tabeli, nie po pierwszym incydencie.**
   Nieobecność wisi na **anonimowej pozycji obsady**, nigdy na osobie, a wiersz **nie ma kolumny na
   notatkę ani uzasadnienie**; typ pochodzi wyłącznie ze słownika. Bez tej granicy tabela
   nieobecności jest rejestrem, który w części przypadków niesie dane o zdrowiu — to ten sam
   argument, którym `staffing_position` odmawia kolumny na osobę (Issue #31, ADR uwierzytelniania), i
   nie wolno go osłabić dopisaniem "opcjonalnego" pola tekstowego.
7. **Bramka kosztów osobowych nieaktywowana przez to zadanie.** Żadna odpowiedź SC-3-02 nie niesie
   stawki; flagi kosztowe i przychodowe typu nieobecności są konfiguracją, nie kwotą. Kierunek
   wyjątku z aneksu 2026-09-19 pkt 3 pozostaje nieudowodniony — pierwsze zadanie pokazujące koszt
   nieobecności (F-07) musi odtworzyć koniunkcję i dowieść jej własnym kryterium.
8. **Migawka dziedziczy zasięg scenariusza.** Wiersze `approved_snapshot_*` należą do scenariusza, a
   więc do projektu, i są czytane wyłącznie tą samą ścieżką zasięgu — mimo że ich treść pochodzi z
   tabeli organizacyjnej bez zasięgu. Pochodzenie treści nie przenosi zwolnienia.
9. **Rozstrzygnięcie bramki 1 (2026-09-22, P-2): endpoint zatwierdzenia scenariusza jest zwykłą
   ścieżką zapisu projektową** — `404` nigdy `403` dla scenariusza spoza zasięgu, precedencja `404`
   nad `409` dla scenariusza spoza zasięgu już zatwierdzonego (żeby odpowiedź nie potwierdzała ani
   istnienia, ani stanu). **Kontrola roli na tym endpoincie nie istnieje** — placeholder identity nie
   niesie wymiaru roli, a `PERSONNEL_COSTS_READ`/pozostałe uprawnienia nie rozróżniają "może
   planować" od "może zatwierdzać". To nie jest przeoczenie tego aneksu — jest zamknięciem
   warunkowym na ADR uwierzytelniania, nazwanym w ADR-0004 aneks z tą samą datą, pkt 5.
10. **Nazwa typu nieobecności jest wolnym tekstem, a migawka czyni jej treść trwałą i nieusuwalną
    (security-auditor, weryfikacja SC-3-02, 2026-09-22).** Punkt 6 zamyka granicę danych osobowych
    na wierszu instancji (brak kolumny na osobę/notatkę); nie zamyka jej na `absence_type.name` —
    jedyna reguła treści tej kolumny to niepusty ciąg, a `approved_snapshot_absence_type` kopiuje ją
    wprost, bez ścieżki UPDATE/DELETE (ADR-0004, ten sam aneks, pkt 3). Dziś nieszkodliwe: SC-3-02
    nie daje żadnej ścieżki zapisu dla `absence_type` (żaden HTTP endpoint go nie tworzy), więc
    ryzyko ogranicza się do dostępu do bazy/seeda. **Warunek ponownego otwarcia:** pierwsze zadanie
    wystawiające zapis `absence_type` (formularz/endpoint) musi rozstrzygnąć wprost — ograniczenie
    treści nazwy (np. zakaz danych osobowych, walidacja wzorca) albo zasadę
    erasure/rectification dla wierszy `approved_snapshot_*`, które tę nazwę już skopiowały — jako
    warunek wstępny tamtego zadania, nie do odkrycia po fakcie.
11. **"Anonimowa pozycja obsady" (pkt 6) jest pseudonimizacją, nie anonimizacją — przy
    `headcount = 1` degraduje się do identyfikacji (security-auditor, weryfikacja SC-3-02,
    2026-09-22).** Wołający z `STAFFING_READ` + `CATALOG_READ` + wierszem `project_access` widzi
    listę nieobecności pozycji (daty + typ przez `absence_type_id`→nazwa); przy `headcount = 1`
    krotka wymiarów katalogu (rola/senioritet/lokalizacja/typ zaangażowania) w organizacji typowej
    wielkości jednoznacznie wskazuje osobę. To nie jest przekroczenie granicy zasięgu (`project_access`
    działa poprawnie) — to ujawnienie nowe wewnątrz istniejącej granicy, którego przed SC-3-02 nie
    było. **Warunek ponownego otwarcia:** przeniesienie nieobecności na osobę (Issue #31, ADR
    uwierzytelniania) albo pierwsze zadanie zależne od rozróżnienia `headcount = 1` od `headcount > 1`
    w odpowiedzi API musi tę degradację nazwać wprost i rozstrzygnąć, czy wymaga countermeasure
    (np. agregacja przy `headcount = 1`) — do tego czasu przyjęte jako nazwane, nieaktywnie
    zamknięte ryzyko, nie jako defekt do naprawy w SC-3-02.

### 2026-09-22 — budżet urlopowy jest daną organizacyjną, nie daną kosztową (SC-3-03)

Drugi aneks tej daty w tym pliku, osobny wpis, nie dopisek do poprzedniego — precedens: trzy
aneksy z 2026-09-19. **Konsekwencja nazewnicza przyjęta razem z tym wpisem:** odwołanie „ADR-0005,
aneks 2026-09-22" bez nazwy zadania — takie jak te w `backend/app/models/staffing.py`,
`backend/app/models/catalog.py` i `backend/app/data/scenario_approval.py` — znaczy aneks
**SC-3-02**; każde nowe odwołanie musi nazwać zadanie.

Aneks z 2026-09-19 (SC-2-01) pkt 1 domyka się zdaniem: „Pierwsza tabela, której wiersz da się
przypisać do projektu, jednostki biznesowej albo najemcy, przestaje być objęta tym punktem i wymaga
własnego wpisu tutaj." Budżet urlopowy (SC-3-03, F-05) tym zdaniem **nie jest wymuszony** — jego
wiersza nie da się przypisać do żadnego z tych trzech podmiotów — ale jest wymuszony czymś innym:
jest pierwszą tabelą katalogu, której wartość wchodzi wprost do wyliczenia kosztu, a bramka
kosztowa tej decyzji jest jedynym miejscem, w którym to musi być powiedziane.

1. **Zasięg: bez zmian, dane organizacyjne.** Kryterium strukturalne z aneksu 2026-09-19 (SC-2-01)
   pkt 1 spełnione: żadna kolumna nie wiąże wiersza budżetu z projektem, użytkownikiem, jednostką
   biznesową ani najemcą, i żaden endpoint nie zawęża go po tożsamości wołającego. Kolumny klucza
   (`calendar_id`, `engagement_type_id`) wskazują inne wiersze organizacyjne, nie podmiot, w imieniu
   którego działa wołający — ta sama podstawa, którą dostały `vendor_id` (2026-09-21 pkt 1) i
   `catalog_locations.calendar_id` (2026-09-22 SC-3-02 pkt 2). Zwolnienie z funkcji-strażnika
   (ADR-0001, aneks 2026-09-19 pkt 2) obowiązuje dalej; warunek wygaśnięcia bez zmian — pierwszy
   predykat **per wołający** na którejkolwiek z tych tabel kończy wyjątek.
2. **Uprawnienia: `CATALOG_READ`/`CATALOG_WRITE`, bez nowych.** Ósma tabela katalogu, nie ósmy
   mechanizm — precedens dosłowny z aneksu 2026-09-21 pkt 2 („poddostawca jest piątym słownikiem
   katalogu, nie piątym mechanizmem") i z aneksu 2026-09-22 SC-3-02 pkt 3 (szósty i siódmy). To
   samo dotyczy **nowej kolumny flagi na `absence_type`** wskazującej typ rozliczany budżetem
   (ADR-0008, aneks z tą samą datą, SC-3-03, pkt 8): jest kolejną kolumną siódmego słownika, nie
   nowym mechanizmem i nie predykatem per wołający — ustawienie organizacyjne, jednakowe dla
   każdego, kto czyta katalog. **`PLACEHOLDER_PERMISSIONS` nie rośnie w tym zadaniu**; kanarek
   równości zbiorów zostaje bez przezbrajania.
3. **Budżet NIE jest daną kosztową w rozumieniu tej decyzji** (rozstrzygnięcie bramki 1,
   2026-09-22, Q-4) — bramkuje go samo `CATALOG_READ`, nie `PERSONNEL_COSTS_READ`. Podstawa jest
   rzeczowa, nie wygodnościowa: budżet jest liczbą dni (albo FTE) — parametrem uprawnieniowym
   organizacji, nie kwotą i nie kosztem żadnej osoby. NF-11/AC-06 chronią „individual personnel
   costs"; liczba dni urlopu przysługujących w danym reżimie kalendarzowym nie ujawnia ani jednej
   kwoty, bo mnożnik — stawka kosztowa — jest już bramkowany osobno i wołający bez
   `PERSONNEL_COSTS_READ` go nie dostaje. **`CATALOG_PERSONNEL_COST_FIELDS` zostaje
   jednoelementowy.** Mutacja, którą to rozstrzygnięcie odrzuca: dopisanie kolumny budżetu do
   zbioru pól kosztowych — dałoby to uprawnieniu o nazwie „koszty osobowe" **trzecie** znaczenie (po
   dwóch, które nazywa aneks 2026-09-19 SC-2-01 pkt 3 i aneks 2026-09-21 pkt 3), a w działającym
   dziś systemie uczyniłoby całą funkcję nieosiągalną, bo `PERSONNEL_COSTS_READ` nie należy do
   zestawu placeholdera.
4. **Granica, która nie może stać się cichym omijaniem bramki kosztowej — nazwana teraz, nie po
   pierwszym zadaniu bloku 5.** Punkt 3 mówi o **budżecie jako liczbie dni**. **Wyliczony koszt
   budżetu** — koszt nieobecności płatnej w odpowiedzi opisującej projekt albo scenariusz (F-07) —
   jest polem kosztowym i podlega koniunkcji z aneksu 2026-09-19 (SC-1-08) pkt 1 bez żadnej zmiany,
   wzmocnionej regułą kierunkową z aneksu 2026-09-19 (SC-2-01) pkt 3: wyjątek jednoczynnikowy
   obowiązuje **wyłącznie tam, gdzie projektu nie ma**. Mutacja do zabicia: policzenie kosztu
   budżetu i wypuszczenie go ścieżką kształtowania katalogu (jeden czynnik) zamiast ścieżką
   projektu/scenariusza (koniunkcja).
   **Dlaczego to jest zapisane tutaj, a nie zostawione domyślności.** To jest **odwrotność**
   precedensu „ochrony nadpłaconej" z aneksu 2026-09-21 pkt 3 (Issue #46). Tam świadomie
   **przepłaciliśmy** ochroną: pole, które nie jest indywidualnym kosztem osobowym (cena
   kontrahenta), zostało objęte bramką kosztową, żeby nie mnożyć bramek na jednym polu. Tutaj ryzyko
   idzie w drugą stronę: dana **niebramkowana** (budżet) jest wejściem do liczby, która bramce
   podlega — i dokładnie tak powstaje ciche obejście, bo nikt nie klasyfikuje wyniku po pochodzeniu
   składników. Zapisane wprost, żeby pierwsze zadanie wyceniające nieobecność odtworzyło koniunkcję
   i dowiodło jej własnym kryterium, tak jak zobowiązuje je aneks 2026-09-22 SC-3-02 pkt 7.
5. **Konsekwencja przyjęta świadomie, nie odkryta później:** każdy, kto planuje staffing, widzi
   budżet urlopowy każdego reżimu kalendarzowego i każdego typu zaangażowania. W części organizacji
   wymiar urlopu jest negocjowanym elementem warunków zatrudnienia, a nie stawką z regulaminu — to
   ta sama klasa konsekwencji, którą aneks 2026-09-21 pkt 2 przyjął dla cenników poddostawców, i tak
   samo przyjęta: nazwana, nie zamilczana. Pierwsze zadanie, w którym zacznie przeszkadzać,
   rozdziela bramki i wymaga własnego, datowanego wpisu tutaj.
6. **Wolny tekst źródła budżetu uruchamia warunek z pkt 10 aneksu SC-3-02, i SC-3-03 go rozstrzyga
   wprost, bo wystawia pierwszą ścieżkę zapisu (`POST /catalog/absence-budgets`).** Wiersz budżetu
   niesie obowiązkowe, niepuste pole źródła (ADR-0008, aneks z tą samą datą, SC-3-03, pkt 6) o
   jedynej regule treści „niepusty ciąg", a migawka kopiuje je wprost, bez ścieżki UPDATE/DELETE.
   **Rozstrzygnięcie (2026-09-22): nazwane ryzyko, bez wymuszenia technicznego.** Ten sam precedens
   co `absence_type.name` i `catalog_vendors.name` (audyt 2026-09-21: „traktować jako
   nieklasyfikowane") — pole zostaje wolnym tekstem, bez detekcji nazwisk (zawodna, dałaby fałszywe
   poczucie bezpieczeństwa gorsze niż jego brak) i bez mechanizmu erasure/rectification dla wierszy
   `approved_snapshot_*` (projektowanie takiego mechanizmu teraz, dla jednej kolumny jednej tabeli,
   byłoby rozwiązywaniem problemu, którego `absence_type.name` już ma i nie rozwiązał). Kolumny
   podmiotowej („kto wpisał") ten aneks nie wprowadza i wprowadzić nie wolno — patrz pkt 6 aneksu
   2026-09-21 (SC-2-04). **Doprecyzowanie (security-auditor, weryfikacja SC-3-03, 2026-09-22):**
   akceptacja opiera się na analogii z `absence_type.name` — nazwą słownikową wpisywaną raz. Pkt 5
   tego samego aneksu przyznaje, że wymiar urlopu bywa negocjowanym elementem warunków zatrudnienia
   — dla takiego wiersza naturalną treścią `source` jest odwołanie do osoby i jej aneksu umowy, nie
   do regulaminu. Profil ekspozycji różni się od `absence_type.name`: pole opisowe wpisywane raz w
   słowniku vs. zdanie pisane przy każdym wpisie liczby, dla wiersza, który z definicji może
   dotyczyć jednej osoby. Wniosek (brak wymuszenia technicznego) zostaje w mocy — detekcja nazwisk
   byłaby zawodna i dałaby gorsze niż nic poczucie bezpieczeństwa — ale interfejs zapisu powinien
   zachęcać do wskazania dokumentu/reguły, nie osoby, tam gdzie to możliwe (treść pomocnicza pola,
   nie walidacja). **Warunek ponownego otwarcia:** ten sam co pkt 10 aneksu SC-3-02, rozszerzony —
   pierwsze zadanie projektujące mechanizm erasure/rectification dla `approved_snapshot_*`
   (jakikolwiek powód, dowolna kolumna) musi objąć nim `absence_type.name` i `absence_budget.source`
   naraz, **i** pierwsze zadanie czytające jakąkolwiek migawkę (blok 8) musi ocenić, czy odczyt
   `source` wymaga własnej bramki widoczności, nie tylko bramki zapisu.
7. **Degradacja pseudonimizacji z pkt 11 aneksu SC-3-02 rośnie, nie zmienia się — i mechanizm
   egzekwujący jest węższy niż to zdanie pierwotnie zakładało.** Tamten punkt nazwał, że przy
   `headcount = 1` krotka wymiarów katalogu wskazuje osobę, a lista nieobecności staje się
   informacją o niej. Budżet dokłada do tego wymiar uprawnieniowy: wołający z `STAFFING_READ` +
   wierszem `project_access` odczyta z wyliczenia, ile dni urlopu przysługuje tej jednej osobie —
   warunek zatrudnienia, nie plan. **Korekta (security-auditor, weryfikacja SC-3-03, 2026-09-22):**
   ścieżka obsady (`GET .../staffing-positions`) wynosi pola budżetowe pod samym `STAFFING_READ`,
   bez wymogu `CATALOG_READ` — zgodnie z precedensem SC-3-02, gdzie ta sama ścieżka już wynosi
   `calendar_name`/`standard_hours_per_day` pod samym `STAFFING_READ`. Dane organizacyjne
   dziedziczone przez odpowiedź projektową nie dostają drugiej bramki tylko dlatego, że mają też
   własny endpoint katalogowy — to byłaby bramka na polu bez podstawy w ADR-0005 (Decyzja: "warstwa
   kształtowania", nie "dwie warstwy dla tej samej wartości w dwóch odpowiedziach"). Dziś
   nieszkodliwe, bo placeholder daje oba uprawnienia naraz — rozbieżność aktywuje się dopiero z
   rozdzieleniem uprawnień. **Warunek ponownego otwarcia bez zmian** (przeniesienie nieobecności na
   osobę, Issue #31, albo pierwsze zadanie zależne od rozróżnienia `headcount = 1` od
   `headcount > 1` w odpowiedzi API); do tego czasu przyjęte jako nazwane, nieaktywnie zamknięte
   ryzyko, nie jako defekt do naprawy w SC-3-03.
8. **Bramka kosztów osobowych jest nieaktywowana dopóty, dopóki SC-3-03 nie niesie kwoty.** Jeżeli
   żadna odpowiedź tego zadania nie zawiera stawki ani kwoty, kierunek wyjątku z aneksu 2026-09-19
   (SC-2-01) pkt 3 pozostaje nieudowodniony i musi tak zostać nazwany w rejestrze możliwości —
   dokładnie jak w pkt 7 aneksu SC-3-02. Z chwilą, gdy zadanie pokaże choć jedną liczbę kosztową
   wyprowadzoną z budżetu, obowiązuje pkt 4 wyżej i koniunkcja musi zostać dowiedziona własnym
   kryterium.
