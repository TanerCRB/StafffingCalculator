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
