# Przekazanie do Agentic AI / SDD / HIL

Status: propozycja. Ten dokument nie nadaje statusu Accepted żadnemu ADR i nie podnosi statusów w PLAN/capabilities.

## 1. Kolejność źródeł

1. Instrukcja człowieka i zatwierdzony zakres Story.
2. `TEAM-CONTRACT.md` oraz zaakceptowane ADR, z aneksami.
3. `Wymagania/Requirements_EN.md`.
4. Zatwierdzona część niniejszego pakietu: `UI_SPEC.md`, kryteria konkretnej Story, ustalenia HIL.
5. PNG i HTML jako referencja wyglądu, nie źródło reguł finansowych.

W razie konfliktu wskazać dokładne źródła. Nie wnioskować o akceptacji z samego istnienia grafiki lub kodu prototypu.

## 2. Odczyt istniejącego produktu

Stan odczytany 19.09.2026 przy HEAD `df1ab1c`:

| Obszar | Zaobserwowany stan | Konsekwencja dla implementacji |
|---|---|---|
| Frontend | React / TypeScript / Vite, `App.tsx` i `ProjectListScreen.tsx` | Rozszerzać istniejącą aplikację, nie zastępować samodzielnym HTML |
| Lista projektów | Odczyt, wybór projektu, lista scenariuszy, stany błędów; część akcji jest aria-disabled | Nie utożsamiać narysowanego przycisku z gotowym handlerem |
| Tokeny | Jeden plik `frontend/src/styles/tokens.css`, test literalnych kolorów | Dopisywać tokeny w jednym miejscu; nie przenosić inline styli prototypu |
| API projektów | GET/POST `/projects`, GET/PATCH `/projects/{id}`, POST `/projects/{id}/copy`, POST `/projects/{id}/archive` | Można projektować integrację na rzeczywistych kontraktach; odczytać aktualne schematy przed edycją |
| Katalog | Słowniki i stawki koszt/selling, jednostka hour, effective dates | UI katalogu rozszerza istniejący backend; daily/monthly to osobna zmiana |
| Staffing | SC-3-01 w PLAN pozostaje niezaznaczone, brak modułu staffing w odczytanym drzewie `backend/app` | Nie deklarować gotowego endpointu siatki |
| Money | Wspólny formatter i fixed-point strings | Nie używać Number/parseFloat do pieniędzy API |
| Identity / access | Placeholder ma ograniczony zakres; brak produkcyjnego loginu i pełnej ścieżki grantów | UI-18 nie uprawnia do rozszerzenia placeholdera |
| Catalog UI-15 | `CatalogScreen` renderuje tabelę stawek i pięć słowników na jednej stronie; wspólny formularz słownika zapisuje tylko nazwę | UI-15A–D to grafiki szczegółowe paneli, nie cztery produkcyjne trasy; zachować Vendors jako piąty wymiar |
| Commercial / period ADR | ADR-0003 i ADR-0008 mają nagłówek Draft | Wyjaśnić status na bramce 1 przed opieraniem nowych kontraktów na tych decyzjach |
| Historia / snapshot | Projekt zakłada mechanizmy, część historii jest odłożona do bloku 8 | Makieta timeline nie jest dowodem istnienia audit log |

To odczyt kodu i dokumentów, nie ponowna certyfikacja testów. Zmiany innego zespołu po tej dacie wymagają ponownego porównania.

## 3. Macierz śledzenia wymagań

| Wymaganie | Ekrany | Kluczowy dowód wymagany przy wdrożeniu |
|---|---|---|
| F-01 | UI-01, UI-20, menu projektów | Utworzenie, edycja, kopia i archiwizacja zgodnie z kontraktem; draft nie udaje gotowej kalkulacji |
| F-02 | UI-13, UI-24, C-07 | Nadpisanie jednego scenariusza nie zmienia innego; aktualizacja defaultu nie przepisuje zapisanych wartości |
| F-03 | UI-04, UI-15 | Poprawna stawka w dniu granicznym i brak stawki w luce |
| F-04 | UI-03, UI-04 | Dwie osoby po 0.5 FTE = 1 FTE, zmiana miesięczna i partial month |
| F-05 | UI-13, UI-14 | Absence oddzielnie zmienia capacity, cost i billable time |
| F-06.1 | UI-07 | AC-01 i cap, zmiana stawki w czasie |
| F-06.2 | UI-08 | AC-07: wzrost cost bez wzrostu agreed price |
| F-06.3 | UI-09 | AC-08, gwarantowane ≠ oczekiwane, probabilities = 100% |
| F-06.4 | UI-10 | AC-09, odrzucone punkty nie są billable, brak stałego SP/h |
| F-06.5 | UI-07–UI-10, mixed pricing dialog | Brak duplikacji przychodu per scope/period bez explicit combined rule |
| F-07 | UI-03, UI-04 | Cost basis i overhead nie są naliczane podwójnie |
| F-08 | UI-05, UI-06 | AC-03, one-off raz, recurring we właściwych okresach |
| F-09 | UI-11, UI-12 | AC-02, kopia niezależna, symulacja nie zapisuje bazy |
| F-10 | UI-02, UI-11, UI-12 | AC-01/05, margin ≠ markup, brak sumowania alternatywnych scenariuszy |
| F-11 | UI-02, UI-17 | Zgodność wartości podglądu z eksportem, wersja i założenia widoczne |
| F-12 | UI-16, UI-22, UI-17 | AC-04/10, snapshot i audit, odmowa zapisu approved po stronie danych |
| F-13 | UI-01, UI-15, UI-18, UI-19 | AC-06, brak danych kosztowych w payloadzie/eksporcie; out-of-scope ≡ not found |
| NF-01/02 | Wszystkie widoki liczbowe | DecimalString, jednolite formatowanie, drill-down do składników |
| NF-03 | UI-03, UI-12 | Pomiar na 200 pozycjach × 36 miesięcy, cel 95% ≤2 s w uzgodnionym środowisku |
| NF-04 | UI-18, UI-17 | Serwerowa autoryzacja i ochrona danych; nie dowodzi tego prototyp |
| NF-05 | C-09, UI-23 | Saved dopiero po odpowiedzi; 409 nie gubi lokalnego wejścia |
| NF-06 | Poza UI | Test odtworzenia kopii zapasowej; ekrany nie są dowodem spełnienia |
| NF-07/08/09 | Cały shell i formularze | Klawiatura, focus, czytelne błędy, lokalne przewijanie, przeglądarki i mobile |
| NF-10 | UI-15, UI-24 | Uprawniona zmiana katalogu bez zmiany kodu |
| NF-11 | C-14 | Diagnostyka bez poufnych stawek i danych osobowych |

## 4. Proponowane pakiety Story

Identyfikatory `UX-Pxx` są lokalnymi etykietami tej propozycji, nie nowymi numerami w `docs/PLAN.md`. Product Owner przypisuje właściwe SC-ID po uzgodnieniu zakresu.

| Pakiet | Zakres | Warunek zakończenia | Zależności |
|---|---|---|---|
| UX-P01 | Shell, nawigacja i tokeny | Kontekst projektu/scenariusza zachowany; 390–1920 px bez overflow dokumentu; keyboard navigation | HIL-UI-01 |
| UX-P02 | Lista i formularze projektu | Istniejące akcje API podłączone; archive nie ukrywa/usunie projektu; obsłużone 404/409/422 | UX-P01, obecne API |
| UX-P03 | Defaults i katalog | Rate periods, źródła i uprawnienia; brak możliwości konfliktujących okresów | HIL statusu ADR-0008 |
| UX-P04 | Staffing i kalendarze | Kanoniczna alokacja, headcount, partial months i konflikt tokenu pozycji | Kontrakt SC-3-01, reguły kalendarza |
| UX-P05 | Koszty | One-off/recurring i powiązanie pozycji bez duplikacji; nazwana baza procentowa | UX-P04, F-07/F-08 |
| UX-P06a | T&M i Fixed Price | Rozdzielone formy, cap, milestone, porównywalny response kalkulacji | HIL-UI-09, zaakceptowany ADR-0003 |
| UX-P06b | Outcome-based i SP | Testowane reguły zatwierdzone na konkretnych przykładach umów | HIL-UI-02/04 |
| UX-P07 | Overview, compare, sensitivity | Dane autorytatywne, 3 scenariusze, jawny reference, isolated simulations | Kalkulator wszystkich modeli |
| UX-P08 | Wersje i raporty | Approval człowieka, immutable snapshot, zgodny eksport bez wycieku | Blok 8, HIL-UI-05/10 |
| UX-P09 | Access | Real identity, grant workflow, oddzielne prawa akcji i kosztów | HIL-UI-06, właściwy ADR |

Ekrany stanów wyjątkowych wdrażane w każdej Story, nie jako końcowe kosmetyczne zadanie.

## 5. Przykładowe kryteria kontrastowe

### UI-03 / Staffing

- Given dwie osoby, 176 h pojemności na osobę i 0.50 FTE per person, Then 176 h zespołu; nie 88 h i nie 352 h.
- Given 50% billable, Then billing bierze połowę planned hours, ale koszt przy godzinowym cost basis obejmuje wszystkie planned hours.
- Given brak stawki kosztowej, Then stan incomplete/restricted zgodnie z przyczyną; nie PLN 0.
- Given token T0 i równoległy zapis T1, When save z T0, Then 409 i zachowane wejście; drugi wiersz z własnym poprawnym tokenem nie jest automatycznie konfliktem.
- Proponowana mutacja QA: usuń mnożnik headcount lub zignoruj token pozycji. Test ma zabić każdą mutację niezależnie.

### UI-05 / Additional costs

- Given one-off PLN 12,000 we wrześniu, Then 12,000 w okresie i 0 w kolejnych miesiącach.
- Given PLN 3,000 monthly przez 6 miesięcy, Then PLN 18,000.
- Given ten sam koszt widoczny w drawerze i rejestrze, Then suma obejmuje go raz.
- Mutacja QA: agreguj koszt drugi raz przez relację pozycji; test ma wykryć zawyżoną sumę.

### UI-07–UI-10 / Commercial

- Fixed price: koszt +20,000 zmniejsza profit o 20,000 przy tej samej cenie.
- Outcome: brak celu daje tylko fee gwarantowane; expected fee nie jest etykietowane jako guaranteed.
- SP: 25 accepted i 5 rejected przy 1,000/point daje 25,000, nie 30,000.
- Mixed: nakładające się scope/period bez explicit combined rule są odrzucone.
- Mutacje QA: naliczaj rejected points, traktuj bonus jako bezwarunkowy, zwiększaj fixed price wraz z effort.

### UI-16 / Approval

- Given missing inputs, Then brak możliwości approval, nawet jeśli frontend wysłał taką akcję.
- Given approved, Then serwer odrzuca zapis; przycisk disabled nie jest dowodem ochrony.
- Given zmiana defaultów po approval, Then wartości liczbowe raportu pozostają zgodne z migawką.
- Mutacja QA: raport czyta aktualne kursy zamiast snapshotu; test ma wykryć zmianę.

### UI-18 / Costs

- Global permission true + project flag false → restricted.
- Global permission false + project flag true → restricted.
- Oba true → odczyt dozwolony; istniejące granty innego użytkownika nie wpływają na bieżącego.
- Te same przypadki dla API, listy, raportu i eksportu. Test nie może ograniczać się do ukrytej kolumny.

## 6. Kontrakty integracji — co zachować

- `ProjectStatus`: Active / Archived; `ScenarioStatus`: Draft / Approved.
- `missing_inputs` i `ready_for_approval` przychodzą z serwera. UI tłumaczy nazwy pól na etykiety, nie zgaduje gotowości z kolorów.
- Pieniądze API jako fixed-point strings. Reużyć `frontend/src/lib/money.ts` i backendowe reguły; przykładowe JS w prototypie nie jest produkcyjną biblioteką obliczeń.
- Daty domenowe jako daty ISO, bez przypadkowego przesunięcia strefą czasową. effective_to w UI jest dniem włączającym; brak końca ma jawne znaczenie.
- Projekty: używać aktualnego `updated_at` przy zapisie. Dla staffing reużyć zaakceptowaną ziarnistość tokenu pozycji, gdy powstanie kontrakt.
- Nie dopisywać nowych pól modelu komercyjnego do `ProjectListItem` tylko dlatego, że są na inspiracji. Najpierw Story kontraktu i backend.
- Nowe route patterns w aplikacji powinny obejmować projectId/scenarioId/versionId. Hash routes w prototypie służą katalogowi ekranów, nie są przyjętą architekturą routingu.
- API dla scenariuszy, approval, kosztów, symulacji i eksportów w tym pakiecie nie zostało ustanowione. Ich dokładne payloady należą do zatwierdzonego zakresu implementacji.

## 7. Bramki Human-in-the-Loop

### Bramka 1 — przed kodem produktu

Do przeglądu: zakres Story, screen IDs, acceptance criteria z kontrastem, impact map zaakceptowanych ADR, otwarte decyzje i Out of scope. Człowiek akceptuje zakres i architekturę zgodnie z `TEAM-CONTRACT.md` §3. Samo zaakceptowanie koloru lub makiety nie akceptuje nieustalonych formuł komercyjnych.

### Bramka 2 — przed merge

Do przeglądu: diff, ekran przed/po, wynik testów, raport Guardian, niezależny review, QA z wykonaną mutacją; security audit zgodnie z triggerami projektu. Autor nie ocenia sam własnego wyniku. Bez merge/commit/push na podstawie tego pakietu.

### Bramka 3 — przed statusem

QA proponuje wpis do PLAN/capabilities z dowodem, ale człowiek zatwierdza i podnosi status. Zrzut ekranu dowodzi wyglądu; nie dowodzi trwałości, autoryzacji ani poprawności silnika finansowego.

## 8. Gotowy brief dla agenta implementującego

> Zrealizuj wyłącznie zatwierdzoną Story [SC-ID] dla ekranów [UI-IDs]. Najpierw przeczytaj TEAM-CONTRACT.md, aktualne ADR i dokumentację UI w Wymagania/prototyp/spec. Wskaż istniejące komponenty i kontrakty do ponownego użycia. Nie przenoś demo-data ani silnika prototype.js do produktu. Nie rozszerzaj zakresu na niezaakceptowane modele, identity lub eksport. Zachowaj DecimalString, serwerowe uprawnienia i tokeny współbieżności. Przed implementacją przedstaw acceptance criteria oraz impact map, jeśli nie są zatwierdzone w Story. Implementuj w odrębnym worktree zgodnie z kontraktem. Po zmianie wykonaj właściwe testy i dołącz dowody wizualne. Nie zmieniaj samodzielnie statusów PLAN/capabilities i nie wykonuj commit/push/merge bez wyraźnego polecenia. Zakończ raportem zmian, walidacji i otwartych ograniczeń.

## 9. Szablon akceptacji makiet

Do wypełnienia przez człowieka podczas przeglądu:

- Przeglądający / data: …
- Ekrany: …
- Decyzja: Accepted / Accepted with changes / Revise.
- Zatwierdzony zakres zachowania: …
- Wymagane zmiany: …
- Decyzje HIL-UI zamknięte tym przeglądem: …
- Nadal otwarte i blokujące implementację: …

Ten szablon nie jest już wypełnioną akceptacją.
