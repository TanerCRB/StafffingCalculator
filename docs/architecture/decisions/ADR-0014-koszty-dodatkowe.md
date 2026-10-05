# ADR-0014 — Koszty dodatkowe (F-08): własność, okres, finansowanie, kształt wyniku

**Status:** Accepted (2026-09-23, bramka 1 SC-5-05)

> Projekt przygotowany przez rolę Architekta na bramkę 1 SC-5-05 (Issue #10). Kierunki Q-1..Q-8 i
> G-1 (luka analityka) zaakceptowane przez człowieka 2026-09-23 (rekomendacje architekta/analityka,
> bez zastrzeżeń) — treść poniżej już je odzwierciedla.

## Kontekst

F-08: kategorie kosztów (rekrutacja, sprzęt, licencje, chmura, podróże, szkolenia, podwykonawstwo,
zarządzanie); koszty jednorazowe i cykliczne; podstawa stała albo zależna od headcountu, FTE, godzin
lub bazy procentowej; przypisanie do projektu, fazy albo pozycji obsady; rozróżnienie kosztu
finansowanego przez dostawcę od refakturowanego na klienta; waluta i okres przy każdym koszcie;
widoczność nakładających się obciążeń, narzutów i rezerw. AC-03: koszt jednorazowy przypisany do
okresu pojawia się w tym okresie dokładnie raz.

Żaden istniejący ADR nie jest miejscem tej reguły: ADR-0013 dotyczy wyłącznie F-07 (pkt 8) i nosi
nazwę "koszt osobowy" — koszty dodatkowe nie są indywidualnym kosztem osobowym w rozumieniu F-13;
ADR-0003 opisuje wyłącznie przychód (reguła 10 Strażnika).

## Decyzja

1. **Właścicielem kosztu dodatkowego jest scenariusz.** Wiersz kosztu ma `scenario_id` `NOT NULL`,
   bez `ON DELETE CASCADE` (ADR-0003 pkt 1). Opcjonalne przypisanie do pozycji obsady wskazuje
   pozycję tego samego scenariusza — zgodność egzekwowana w bazie, nie w aplikacji (ADR-0001).
   **Q-2 = A:** "koszt projektu" z F-08 = koszt scenariusza bez pozycji, żadna osobna
   tabela projektowa. Koszt "wspólny dla całego projektu" nie istnieje jako osobny mechanizm w tym
   zadaniu — trzeba go wpisać do każdego scenariusza z osobna (kopiowanie SC-1-03 przenosi go do
   kopii scenariusza tak jak każdą inną własną daną). Faza — poza zakresem, encja nie istnieje
   (ADR-0003, "Odłożone").
2. **Kategoria jest słownikiem organizacyjnym, szóstym po `catalog_vendors`** (NF-10), dane bez
   zasięgu, `CATALOG_READ`/`CATALOG_WRITE` (ADR-0005 aneks SC-2-01 pkt 1 i 2), nieusuwalna, dopóki
   wskazuje ją jakikolwiek koszt. **Q-4 = A:** kategoria to wyłącznie etykieta, bez migawki, bez wpływu na wyliczenie;
   zmiana nazwy kategorii po zatwierdzeniu zmienia nazwę widoczną na zatwierdzonym scenariuszu —
   zaakceptowane ograniczenie, ten sam wzorzec co pola opisowe Projektu (ADR-0004 aneks 2026-09-18,
   grupa 1). Migracja nie zasiewa ośmiu kategorii z F-08 (precedent ADR-0012 pkt 3).
3. **Okres ma ziarnistość miesiąca.** Koszt jednorazowy: dokładnie jeden miesiąc. Koszt cykliczny:
   zakres miesięcy domknięty z obu stron, nigdy bezterminowy (baza odrzuca otwarty koniec). Zgodność
   rodzaju i kształtu okresu egzekwowana w bazie. **Q-3 = A:** kwota cykliczna to kwota **na każdy
   miesiąc** zakresu, bez dzielenia i bez reszty (żadna kwota "całkowita do rozłożenia" w tym
   zadaniu). Miesiąc kosztu spoza okresu dostawy projektu ani spoza miesięcy alokacji pozycji **liczy
   się do wyniku tak samo** (mirror ADR-0013 pkt 4) — brak odmowy zapisu, brak sprawdzania względem
   pól Projektu grupy 2.
4. **To nie jest konsument wzorca ADR-0008.** Nakładające się koszty tej samej kategorii są legalne;
   żadne wyszukiwanie po dacie nie wybiera jednego wiersza spośród wielu, więc reguła 13 nie ma
   zastosowania. Brak `EXCLUDE` jest rozstrzygnięciem, nie pominięciem.
   **Zakres cykliczny ma górny limit długości** (bramka 2, reviewer R-02, 2026-09-24): analogicznie
   do `MAX_ALLOCATION_MONTHS` (`backend/app/api/schemas/staffing.py`), zakres `[start, koniec]`
   ograniczony do 60 miesięcy egzekwowanych w schemacie żądania — jeden zapis nie może uczynić
   każdego późniejszego odczytu/kopii scenariusza nieograniczenie dużym. Koszt jednorazowy bez
   zmian (jeden miesiąc).
   **Nazwane ryzyko, przyjęte świadomie (reviewer R-04, 2026-09-24):** brak `EXCLUDE`/klucza
   unikalnego na wierszu kosztu oznacza, że powtórzony `POST` (np. po zerwanym połączeniu i
   ponownej próbie) tworzy drugi, identyczny wiersz — baza go nie odrzuca. ADR-0009 pkt 2 zakłada,
   że "baza odrzuca powtórzony zapis sama"; dla tej tabeli to założenie nie zachodzi. Zaakceptowane
   bez zmian w tym zadaniu: nie ma dziś ścieżki zapisu z UI (F-11 nieukończone), ryzyko dotyczy
   wyłącznie przyszłego klienta. **Warunek ponownego rozpatrzenia:** pierwsze zadanie budujące
   formularz zapisu kosztu (F-11) musi rozstrzygnąć idempotencję (klucz klienta, `EXCLUDE` na
   krotce, albo potwierdzenie po stronie UI) w tym samym zadaniu.
5. **Podstawa naliczenia w SC-5-05: wyłącznie kwota stała** (**Q-1 = A**), jednorazowa albo
   cykliczna. Zależność od headcountu, FTE, godzin lub bazy procentowej (F-08 pkt 3) — poza zakresem
   tego zadania, następne zadania (SC-5-07+, `docs/PLAN.md` SC-5-06 zajęte przez F-07 — koszt
   nieobecności płatnych, Issue #81); FTE nie ma dziś podstawy konwersji (SC-5-04 odłożone),
   a podstawa procentowa wymaga własnej definicji i — jeśli czyta koszt osobowy — dziedziczy jego
   bramkę jako czwartą dopuszczoną funkcję kształtującą, co wymaga osobnej decyzji (ADR-0005 aneks
   SC-5-01 pkt 4).
6. **Kwota.** `Decimal`, na wejściu precyzja większa niż jednostka minor (ADR-0008 pkt 6), bez
   zaokrąglenia przy zapisie, nadmiar precyzji → `422` (ADR-0002 aneks SC-2-04 pkt 2); kod ISO 4217
   przy każdym koszcie. **G-1 = A: kwota ściśle dodatnia** (`CHECK amount > 0` w bazie) — wiersz
   kosztu reprezentuje realną pozycję kosztową, zerowy wiersz nie ma po co istnieć (po prostu go nie
   tworzyć), a korekty/kredyty (kwota ujemna) to osobny, nieprojektowany w tym zadaniu mechanizm; brak
   precedensu ujemnej kwoty pieniężnej gdziekolwiek indziej w repo. Wynik: suma w `Decimal`, jedno
   zaokrąglenie na końcu przez `app.core.money.round_money` — nigdy per miesiąc ani per koszt.
7. **Dwa kształty wyniku.** `calculated` (kwota, waluta, `assumptions_used` z rozkładem na koszty i
   miesiące, kategorią i finansowaniem) albo stan nazwany: `currency_mismatch` (koszty w więcej niż
   jednej walucie albo w walucie innej niż `scenarios.currency`, jeśli jest zadeklarowana — bez
   przeliczenia, ADR-0006), `no_cost_currency` (brak kosztów i brak `scenarios.currency`). Scenariusz
   bez kosztów z zadeklarowaną walutą → `calculated`, `0.00`. Zakaz sumy częściowej.
8. **Koszt jednorazowy należy do dokładnie jednego wiersza okresu** (AC-03, reguła 16) niezależnie
   od przypisania (scenariusz/pozycja), kopiowania i ścieżki agregacji; koszt cykliczny — do każdego
   miesiąca swojego zakresu i do żadnego spoza niego.
9. **Finansowanie jest obowiązkowym atrybutem o dwóch wartościach:** koszt ponoszony przez dostawcę
   albo refakturowany na klienta. Nazwa nie używa słowa `vendor`/"poddostawca" — w tym repozytorium
   oznacza ono podwykonawcę (ADR-0008 aneks SC-2-03); proponowana nazwa pola: `funding_source`
   (`internal` / `rebilled_to_client`). **Q-5 = A:** wyłącznie atrybut przechowywany, bez wpływu na
   przychód w tym zadaniu — moduł kosztów dodatkowych i ścieżka przychodu nie importują się
   wzajemnie (reguła 10, dowód D-8/D-9). Efekt refaktury na przychód/zysk/marżę — poza zakresem,
   blok 7 (F-10) go konsumuje, nazwane wprost jako świadome niedoszacowanie zysku do czasu bloku 7.
   **Potwierdzenie (analyst G-3):** koszt `rebilled_to_client` WCHODZI do sumy kosztów dodatkowych
   scenariusza na równi z kosztem `internal` — atrybut opisuje wyłącznie kto finansuje, nie wyklucza
   kosztu z wyniku kosztowego. D-9 dowodzi braku wpływu na przychód, nie wyłączenia z kosztu.
10. **Grupa w ADR-0004:** koszty — grupa 2 (strażnik zapisu dla INSERT/UPDATE/DELETE w tej samej
    instrukcji; objęte zamknięciem wyścigu z zatwierdzeniem jak każda tabela-dziecko). **Q-6 = A:**
    koszt przypisany do pozycji kopiowany wewnątrz istniejącego kopiera agregatu pozycji obsady
    (ma już mapowanie starego-na-nowe id pozycji, SC-3-01/SC-3-02); koszt na poziomie scenariusza
    (bez pozycji) dostaje własny wpis w `SCENARIO_CHILD_COPIERS`. Kanarek kompletności musi pokryć
    obie połowy. Kategoria — grupa 1 (etykieta, Q-4).
    **Źródło kopii zablokowane na czas kopiowania** (bramka 2, reviewer R-01, 2026-09-24): koszt
    dodatkowy jest pierwszą tabelą-dzieckiem rozdzieloną między dwa niezależne kopiery (pozycji i
    scenariusza) po kolumnie edytowalnej przez użytkownika (`position_id`) — edycja przełączająca
    koszt między "przypisany do pozycji" a "poziom scenariusza" w oknie między dwoma przebiegami
    kopiowania mogłaby skopiować go podwójnie albo wcale. `copy_project`/`copy_scenario` biorą
    blokadę (`FOR UPDATE`/`FOR SHARE`) na źródłowym wierszu scenariusza na starcie kopiowania —
    ten sam mechanizm co `unapproved_scenario` dla zapisu kosztu — tak, by oba przebiegi kopiera
    czytały jeden spójny stan.
11. **Dostęp: Q-7 = B.** Koszty dodatkowe pod istniejącymi `STAFFING_READ`/`STAFFING_WRITE`, bez
    koniunkcji z `PERSONNEL_COSTS_READ`/`can_view_personnel_costs` i bez nowego uprawnienia — ten
    sam wzorzec co SC-3-02 pkt 5 ("osobne `ABSENCE_*` byłoby ziarnistością bez podmiotu"). Ryzyko
    przyjęte świadomie i nazwane: koszt rekrutacji/szkolenia na pozycji z `headcount = 1` jest
    widoczny pod `STAFFING_READ`, mimo że pośrednio wskazuje jedną osobę (ADR-0005 aneks SC-3-02
    pkt 11 — ten sam akceptowany kompromis). Koniunkcja pełna (opcja C/D) odrzucona: byłaby czwartą
    funkcją kształtującą wymagającą osobnej decyzji i nieosiągalną w produkcji (`PERSONNEL_COSTS_READ`
    poza `PLACEHOLDER_PERMISSIONS`) — nadmiarowa ochrona kosztów, które nie są danymi osobowymi.
    Zasięg projektu bez nowej decyzji (ADR-0005 aneks SC-3-01 pkt 1: `404`, nigdy `403`, także dla
    zapisu).

## Czego ten dokument nie rozstrzyga

Rezerw ryzyka (F-09); wykrywania nakładających się obciążeń (np. kategoria "zarządzanie" a narzuty
SC-5-02, kategoria "podwykonawstwo" a stawka poddostawcy na pozycji — pozycja nie ma dziś
`vendor_id`) — F-08 pkt 7, poza zakresem SC-5-05; faz; przeliczenia walut; podstawy naliczenia innej
niż kwota stała (headcount/FTE/godziny/procent — F-08 pkt 3, SC-5-07+ — SC-5-06 zajęte przez F-07,
Issue #81); całkowitego kosztu, zysku i
marży (blok 7, F-10); eksportu i ekranu (F-11); domyślnych cen organizacji dla kategorii (byłyby
konsumentem ADR-0008 i grupą 1).

## Konsekwencje

- Nowy moduł danych kosztów dodatkowych — nie importuje ścieżki przychodu ani modułu kosztu
  osobowego i nie jest przez nie importowany.
- Aneksy z datą i nazwą SC-5-05 w ADR-0004, ADR-0005, ADR-0007, ADR-0008 — napisane, zob. te pliki.

## Kontrole

| Kontrola | Kryterium akceptacji |
|---|---|
| D-1 | Koszt jednorazowy przypisany do miesiąca M pojawia się w rozkładzie dokładnie raz, w M; kontrast: ten sam koszt przypisany do pozycji — nadal dokładnie raz. |
| D-2 | Koszt cykliczny pojawia się w każdym miesiącu swojego zakresu i w żadnym spoza niego; zakres bez końca odrzucony przez bazę. |
| D-3 | Suma zaokrąglona raz na końcu; mutacja "zaokrąglenie per miesiąc" wywraca test. |
| D-4 | Koszty w dwóch walutach → `currency_mismatch`, nigdy suma częściowa ani `0`. |
| D-5 | Zapis, edycja i usunięcie kosztu pod scenariuszem `approved` odrzucone w tej samej instrukcji; wyścig z zatwierdzeniem nie zostawia wiersza. |
| D-6 | Kopia scenariusza ma koszty o nowych identyfikatorach; koszt przypisany do pozycji wskazuje skopiowaną pozycję, nie pozycję źródła. |
| D-7 | Pozycja obsady innego scenariusza jest niezapisywalna jako przypisanie kosztu (odmowa bazy). |
| D-8 | Moduł kosztów dodatkowych nie importuje ścieżki przychodu ani modułu kosztu osobowego i odwrotnie (test strukturalny). |
| D-9 | Odpowiedź przychodu nie zmienia się po dodaniu kosztu refakturowanego (jeśli Q-5 = A). |
| D-10 | Kwota ≤ 0 odrzucona przez bazę (`CHECK amount > 0`, G-1); kontrast: kwota dodatnia przyjęta. |

## Powiązane wymagania

F-02, F-08, F-10 (tylko jako granica), F-12, F-13, NF-01, NF-10, NF-11, AC-02, AC-03, AC-04;
ADR-0001, ADR-0002, ADR-0003, ADR-0004, ADR-0005, ADR-0006, ADR-0007, ADR-0008; reguły Strażnika
1, 2, 7, 10, 13, 16, 17, 19.

### 2026-09-29 - optional risk link and the pointer to ADR-0021 (SC-6-08)

The section "Czego ten dokument nie rozstrzyga" deferred risk reserves (F-09); they are decided in ADR-0021. `additional_cost` gains a nullable `risk_id` with the composite foreign key `fk_additional_cost_risk_same_scenario` `(risk_id, scenario_id)` -> `scenario_risk(id, scenario_id)` (no `ON DELETE` action). The link never enters the sum: the cost line and the point 7 total are unchanged, and detecting a double representation alters no figure. The read gains `risk_id` (the id of the linked risk, or null). The ADR-0014 R-04 risk (a retried `POST` duplicates a row) applies to reserves unchanged.

### Addendum 2026-10-05 (Issue #236, SC-5-13 — additional-cost write idempotency)

**Status:** Draft — pending approval

The first F-11 additional-cost write form resolves the duplicate-`POST` risk recorded in point 4. This addendum applies to additional-cost creates and does not change other write endpoints.

1. **Key scope and authorization.** The idempotency key is scoped to the authenticated caller. The endpoint checks the caller’s current permission to write additional costs before looking up or replaying a prior outcome. A caller without that permission is refused, even when a matching key has a stored outcome.
2. **Replay and key reuse.** For the same caller, key, and request payload, a retry reuses the original outcome and does not create another cost row. Reusing a caller’s key with a different payload returns `409 Conflict` and performs no write.
3. **Concurrent retries and atomicity.** Concurrent requests with the same caller, key, and payload converge on one persisted cost row. The cost row and idempotency outcome are committed atomically; a failed operation cannot commit only one of them.
4. **Persistence and retention.** The key outcome is persisted while the corresponding cost exists. It stores no request body. Its request fingerprint and outcome are not written to logs. Removing the cost also removes its idempotency outcome.
5. **Scope and compatibility.** Requests without an idempotency key retain existing behavior and receive no retry-idempotency guarantee. This addendum does not change edit concurrency or calculation behavior.

| Control | Acceptance criterion |
|---|---|
| D-11 | Repeating a create with the same caller, key, and payload, including concurrent retries, leaves exactly one cost row; a different payload with the same key returns `409` and writes nothing; omitting the key retains legacy behavior. |
