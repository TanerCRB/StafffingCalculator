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

## Aneksy

### 2026-09-19 — ziarnistość znacznika dla siatki miesięcy pozycji obsady (SC-3-01)

"Konsekwencje" zakładają jeden wiersz, jeden znacznik, jeden `PATCH` — decyzja nie rozstrzygała
przypadku, w którym jedno żądanie edytuje wiele wierszy naraz. Edycja alokacji pozycji obsady
(F-04, SC-3-01) jest tym przypadkiem: siatka miesięcy pod jedną pozycją, edytowana zwykle razem.

**Rozstrzygnięcie:** znacznik współbieżności żyje na **pozycji** (`staffing_position.updated_at`),
nie na każdym wierszu miesiąca i nie na scenariuszu. Edycja alokacji przechodzi przez pozycję:
żądanie niesie znacznik pozycji, odczytany razem z jej wierszami miesięcznymi; zapis odrzucony
(`409`) jeśli znacznik się nie zgadza, `200` i nowy znacznik jeśli się zgadza — ten sam wzorzec co
`PATCH /projects/{id}`, reużyty, nie wynaleziony od nowa, zgodnie z "Konsekwencjami" wyżej.

**Uzasadnienie wyboru ziarnistości pośredniej (nie per wiersz, nie na scenariuszu):** znacznik per
wiersz miesiąca wymagałby N znaczników w jednym żądaniu i nowej decyzji o semantyce częściowej
odmowy (wszystko-albo-nic vs. odmowa per wiersz) — decyzji, której ten ADR nie ma. Znacznik na
scenariuszu maksymalizowałby fałszywe kolizje: edycja jednej pozycji unieważniałaby token każdej
innej pozycji tego scenariusza, dokładnie ten sam kształt problemu, który aneks ADR-0004 z
2026-09-19 (SC-1-04, archiwizacja) już odnotował jako świadomie przyjętą, ale niepożądaną
konsekwencję współdzielonego znacznika. Fałszywa kolizja między dwoma miesiącami **tej samej**
pozycji jest przyjęta świadomie — pozycja jest tu jednostką edycji, nie miesiąc.

### 2026-09-21 — edycja wiersza katalogu: tabela bez `updated_at` i odmowa, której `EXCLUDE` nie daje (SC-2-04)

„Konsekwencje" wskazują ten wzorzec przyszłym zadaniom edytującym inne encje („nie wynajdywać
własny mechanizm współbieżności per encja"), a „Rozważane alternatywy" odrzucają brak ochrony i
wymagają, by ewentualny wybór „last write wins" był zapisany jako odstępstwo z datą, nie jako brak
tematu. SC-2-04 (Issue #49, rozstrzygnięcie bramki 1: zakres obejmuje edycję, nie samo dodawanie)
jest pierwszym zapisem modyfikującym wiersz **poza** Projektem i jego dziećmi — i pierwszym, w
którym dwa założenia decyzji nie przenoszą się wprost.

1. **`EXCLUDE` nie jest substytutem znacznika — to nie jest ten sam przypadek.** Ograniczenie
   `ex_catalog_default_rates_no_overlapping_periods` jest kluczowane na czterech wymiarach,
   `COALESCE(vendor_id, sentinel)` i `valid_period` (ADR-0008 pkt 4, aneks 2026-09-21 pkt 3).
   Edycja zmieniająca wyłącznie `default_cost_rate`/`default_selling_rate`/`currency` na
   niezmienionej krotce i niezmienionym oknie **nie dotyka żadnego operatora tego klucza**: dwóch
   edytujących ten sam wiersz zapisuje się oboje, ostatni wygrywa, bez sygnału. To samo dla `name`
   wpisu słownika, dopóki nowa nazwa nie koliduje z istniejącą w indeksie znormalizowanym. Baza
   broni tu przed *nakładaniem okien* i *dwiema nazwami jednej rzeczy*, nigdy przed zgubioną
   aktualizacją — to dwa różne niezmienniki i jeden nie zastępuje drugiego.
2. **Rozstrzygnięcie (bramka 1, Q-1: wariant A): ten sam wzorzec, ale z nową kolumną.** Każda z
   sześciu tabel katalogu (pięć słowników i `catalog_default_rates`) dostaje `updated_at` z
   `onupdate`; odczyt przeznaczony do edycji niesie znacznik, żądanie edycji musi go przekazać,
   niezgodność kończy się `409` zamiast nadpisania. Założenie decyzji „znacznik (`updated_at`, już
   istniejący na wierszu — bez nowej kolumny)" jest własnością Projektu, nie wzorca: katalog miał
   dotąd wyłącznie `created_at` (`backend/app/models/catalog.py`), więc reużycie wzorca kosztowało
   tu migrację (`d5e94a1c6b73`). Kolumna jest na wszystkich sześciu tabelach, nie tylko na
   stawkach — asymetria „które tabele mają znacznik" byłaby drugą regułą do zapamiętania przy
   każdym następnym formularzu, dokładnie tą, której SC-2-03 pozbyło się zdaniem „poddostawca jest
   piątym słownikiem katalogu, nie piątym mechanizmem".
3. **Warunek dowodowy przenosi się razem z wzorcem, bo bez niego wzorzec przeżywa własne testy.**
   Znacznik musi być policzony przez bazę **w tej samej instrukcji `UPDATE`**, nie porównaniem w
   Pythonie na wierszu przed chwilą odczytanym. Podstawa nie jest teoretyczna: wariant z
   porównaniem w Pythonie przeżył cały dostarczony zestaw SC-1-02 (log mutacji 2026-09-19) i zginął
   dopiero po dopisaniu testu z commitem konkurenta w okno check-then-act. Test wyścigu dwóch
   połączeń jest obowiązkowy dla każdej edytowalnej ścieżki katalogu — dowiedziony:
   `backend/tests/test_catalog_edit.py::test_k_23_a_rate_edit_is_refused_when_a_competitor_commits_between_the_read_and_the_write`,
   `::test_k_23_a_dictionary_edit_is_refused_when_a_competitor_commits_between_read_and_write`.
4. **Dwa różne powody odmowy pod jednym kodem `409` na jednej ścieżce — rozróżnialne, nie zlane.**
   Na zapisie katalogu `409` oznacza dziś odmowę ze stanu danych (nakładanie okien, unikalność
   nazwy — SQLSTATE `23P01`/`23505`, SC-2-01 R-01). Po tym aneksie oznacza dodatkowo nieaktualny
   znacznik. Komunikaty muszą być rozróżnialne dla człowieka i żaden nie może być podciągiem
   drugiego (precedens K-01..K-03 z SC-2-02); „ktoś zmienił ten wiersz, odczytaj go ponownie" i
   „to okno nachodzi na inne" prowadzą do dwóch różnych działań. Żaden z nich nadal nie niesie
   wartości wiersza (NF-11). Dowiedzione:
   `test_the_stale_marker_conflict_and_the_overlap_conflict_are_distinguishable`,
   `test_the_stale_marker_conflict_and_the_duplicate_name_conflict_are_distinguishable`.
5. **Precedencja `404` przed `409` obowiązuje, ale z innego powodu niż na Projekcie.** „Konsekwencje"
   wiążą tę precedencję z nieodróżnialnością odmowy od nieistnienia (ADR-0005) — katalog nie ma
   zasięgu, więc tej powinności tu nie ma. Kolejność zostaje z powodu prostszego: wiersz usunięty
   nie jest wierszem w konflikcie, a `409` na nieistniejące `id` byłby odmową sugerującą stan, który
   nie istnieje (ten sam kształt błędu, który R-01 w SC-3-01 naprawiło o poziom niżej).
6. **Czego ten aneks nie wygasza.** `updated_at` jest znacznikiem czasu, nie kolumną podmiotową:
   nie wiąże wiersza z projektem, użytkownikiem, jednostką biznesową ani najemcą, więc kryterium
   strukturalne z ADR-0001 (aneks 2026-09-19 pkt 1, potwierdzone dla `vendor_id` aneksem
   2026-09-21 pkt 1) i wyjątek zasięgu z ADR-0005 obowiązują bez zmian — katalog nadal nie dostaje
   funkcji-strażnika. Kolumna „kto zmienił" (F-12) byłaby kolumną podmiotową i wygaszałaby oba
   wyjątki; ten aneks jej **nie** wprowadza i nie wolno jej dołożyć jako skutku ubocznego migracji
   znacznika.
7. **Czego ten aneks nie rozstrzyga.** Autosave (druga połowa NF-05) zostaje odłożony po raz trzeci
   — zapis jest jawny, z przycisku. Usuwanie wiersza katalogu pozostaje poza zakresem (SC-2-03,
   „Out of scope" pkt 9; FK bez `ON DELETE`), więc `409` nie zyskuje trzeciego znaczenia.

### 2026-09-22 — agregat pozycji rośnie o trzecią tabelę; znacznik się nie mnoży (SC-3-02)

Aneks z 2026-09-19 umieścił znacznik współbieżności na pozycji obsady i uzasadnił ziarnistość
pośrednią. Nieobecność pozycji (F-05, SC-3-02) jest trzecią tabelą tego samego agregatu.

1. **Nieobecność nie dostaje własnego `updated_at`.** Token zostaje `staffing_position.updated_at`.
   Każda ścieżka zapisu nieobecności przechodzi przez ten sam kształt
   `WITH guarded_position AS (UPDATE staffing_position SET updated_at = now() WHERE … AND
   updated_at = :expected AND NOT EXISTS (… approved …) …)`, który dowiodło K-07. Powód jest ten
   sam, który `backend/app/models/staffing.py` zapisał dla wiersza miesiąca: dwa tokeny na jednej
   ścieżce edycji to pytanie, na które ten ADR nie odpowiada.
2. **Fałszywa kolizja przyjęta świadomie:** dopisanie nieobecności unieważnia token edytora siatki
   miesięcy tej samej pozycji. Ta sama podstawa co dla kolizji miesiąc-kontra-miesiąc — jednostką
   edycji jest pozycja, nie pojedynczy wiersz.
3. **Nowe tabele organizacyjne dostają znacznik od chwili powstania.** `working_calendar`,
   `working_calendar_day` i `absence_type` mają `updated_at` z `onupdate` już w migracji tworzącej,
   nawet jeśli SC-3-02 nie daje im formularza: aneks 2026-09-21 pkt 2 odrzucił asymetrię "które
   tabele mają znacznik" jako drugą regułę do zapamiętania przy każdym następnym formularzu. Zdanie
   "każda z sześciu tabel katalogu" czytane jest od teraz jako "każda tabela organizacyjna".
4. **`updated_at` pozostaje znacznikiem czasu, nie kolumną podmiotową** (pkt 6 aneksu 2026-09-21) —
   wyjątki ADR-0001 i ADR-0005 dla danych organizacyjnych bez zasięgu obowiązują bez zmian.
5. **Czego ten aneks nie wprowadza: tabele `approved_snapshot_*` nie dostają znacznika
   współbieżności.** Są zapisywalne jednokrotnie, przy zatwierdzeniu — nie ma dwóch edytorów, przed
   którymi token miałby bronić (ADR-0004, aneks z tą samą datą, pkt 2).

### 2026-09-23 — koszt dodatkowy: znacznik per wiersz kosztu (SC-5-05)

Koszt dodatkowy (ADR-0014) nie jest trzecią tabelą agregatu pozycji obsady — różni się od
nieobecności (pkt 1 wyżej): może istnieć bez pozycji (koszt na poziomie scenariusza), więc nie ma
jednej wspólnej pozycji, na której mógłby siedzieć token.

1. **Koszt dodatkowy dostaje własny `updated_at`**, z `onupdate`, jak każda nowa tabela własnych
   danych scenariusza (nie organizacyjna — pkt 3 aneksu 2026-09-22 dotyczy słowników, nie danych
   scenariusza). Jednostką edycji jest pojedynczy wiersz kosztu, nie cała pozycja ani cały
   scenariusz — dwa koszty tej samej pozycji edytowane niezależnie nie kolidują ze sobą.
2. **Ten sam kształt strażnika** co pkt 1 aneksu 2026-09-19: `UPDATE ... WHERE id = :id AND
   updated_at = :expected AND NOT EXISTS (... approved ...)`, w tej samej instrukcji dla UPDATE i
   DELETE (reguła Strażnika 7).
3. **`updated_at` pozostaje znacznikiem czasu, nie kolumną podmiotową** — bez zmian wobec pkt 6
   aneksu 2026-09-21.
