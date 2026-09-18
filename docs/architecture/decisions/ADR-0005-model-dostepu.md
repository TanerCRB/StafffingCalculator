# ADR-0005 — Model dostępu i uprawnień

**Status:** Draft — pending approval

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
