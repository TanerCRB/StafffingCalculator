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
