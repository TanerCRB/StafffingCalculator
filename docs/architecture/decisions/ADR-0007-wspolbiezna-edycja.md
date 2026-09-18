# ADR-0007 — Współbieżna edycja i ochrona przed zgubioną aktualizacją

**Status:** Accepted

## Kontekst

NF-05: "The application shall autosave and display save status. Concurrent edits shall not
silently overwrite another user's changes." Do tej pory w systemie istniała jedna ścieżka zapisu
(utworzenie projektu), która nie może zgubić cudzej zmiany — nie modyfikuje istniejącego wiersza.
SC-1-02 (edycja Projektu) jest pierwszym zapisem modyfikującym istniejący wiersz, więc pierwszym,
w którym dwóch wołających może nadpisać się wzajemnie bez żadnego sygnału. `Project` ma
`updated_at` z `onupdate`, ale nic go nie porównuje — domyślnym zachowaniem byłoby "last write
wins", czyli cicha utrata danych.

Rozróżnienie konieczne, żeby nie odczytać tego ADR jako sprzecznego z ADR-0004: ADR-0004 odrzucił
kolumnę `version` **jako mechanizm wersjonowania kalkulacji** ("łatwo o przypadkowy `UPDATE`
trafiający też w zatwierdzony wiersz"). Znacznik współbieżności z tego ADR rozwiązuje inny
problem — kolizję dwóch równoległych zapisów tego samego wiersza — i nie jest substytutem wersji
kalkulacji ani migawki z ADR-0004.

## Decyzja

**Kontrola optymistyczna.** Każdy odczyt Projektu przeznaczony do edycji (`GET /projects/{id}`)
niesie znacznik współbieżności (`updated_at`, już istniejący na wierszu — bez nowej kolumny).
Żądanie edycji (`PATCH`/`PUT /projects/{id}`) musi przekazać ten sam znacznik. Jeśli znacznik nie
zgadza się z aktualnym stanem wiersza w chwili zapisu, żądanie kończy się `409 Conflict` zamiast
nadpisania — bez informacji o treści cudzej zmiany (to nie jest mechanizm scalania, tylko
odmowa). Warunek sprawdzany i egzekwowany w tej samej warstwie dostępu do danych, która egzekwuje
regułę 7 (zapis do zatwierdzonej kalkulacji) — jedno miejsce, dwa niezależne powody odmowy.

Autosave (druga część NF-05) jest poza zakresem tej decyzji — dotyczy interakcji frontendu, nie
mechanizmu ochrony przed nadpisaniem. Frontend musi wyświetlić stan zapisu i obsłużyć `409` jako
odrębny od błędu walidacji, ale to szczegół implementacji SC-1-02, nie treść tego ADR.

## Konsekwencje

- Kontrakt API endpointu edycji niesie znacznik współbieżności w obie strony (odczyt zwraca go,
  zapis go wymaga) — pierwszy przypadek w tym systemie, gdzie klient musi przechować wartość
  wyłącznie po to, by odesłać ją przy następnym zapisie.
- `409` jest nowym kodem odmowy na ścieżce Projektu — musi być odróżnialny od `404`
  (nieodróżnialność z ADR-0005) i nie może stać się ubocznym sposobem sprawdzenia, czy ktoś inny
  właśnie edytował wiersz, którego wołający nie widzi (`404` ma pierwszeństwo przed `409`, jeśli
  oba warunki zachodzą naraz).
- Ten sam wzorzec (znacznik `updated_at`, `409` przy niezgodności) jest tym, co przyszłe zadania
  edytujące inne encje (scenariusz, koszty) powinny reużyć — nie wynajdywać własny mechanizm
  współbieżności per encja.

## Rozważane alternatywy

- **Brak ochrony (last write wins)** — odrzucone: jawna, nienazwana sprzeczność z NF-05 ("shall
  not silently overwrite"); gdyby to był wybór, musiałby być zapisany jako odstępstwo z datą i
  uzasadnieniem, nie jako brak tematu.
- **Blokada pesymistyczna na wierszu** — odrzucone: blokada trzymana przez czas pracy człowieka
  nie ma właściciela w bezstanowym HTTP i przeżywa porzuconą zakładkę; wymagałaby osobnego
  mechanizmu wygasania, którego żadna inna część systemu nie ma.

## Powiązane wymagania

NF-05, F-01 (edycja projektu), F-12 (historia zmian — kto i kiedy, odłożone do bloku 8, patrz
aneks ADR-0004)
