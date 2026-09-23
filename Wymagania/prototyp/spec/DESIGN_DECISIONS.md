# Źródła i decyzje projektowe

Status: Proposed / do przeglądu. Data: 19.09.2026. Odczyt repozytorium: `df1ab1c`; materiały UI były nieśledzone przez Git. Stan aplikacji oceniono na podstawie plików, bez uruchamiania testów backendu.

## Podstawa projektu

| Źródło | Wykorzystanie |
|---|---|
| `Wymagania/Requirements_EN.md` | F-01–F-13, NF-01–NF-11, AC-01–AC-10 i pytania otwarte |
| `Wymagania/UI/Project List.jpeg` | Lista projektów i kontekstowy panel scenariuszy |
| `Wymagania/UI/Team_Planner.jpeg` | Tabela obsady, czas w kolumnach, wynik pozycji i koszty dodatkowe |
| `Wymagania/UI/Project Other Costs.jpeg` | Rejestr kosztów i przypisanie wydatku do zakresu |
| `Wymagania/UI/globallogic_style_guide-v3.docx` | Manrope, role kolorów, czytelność, sentence case, paleta danych |
| `frontend/src/styles/tokens.css` | Konkretne wartości kolorów, skala odstępów, promienie i typografia |
| `frontend/src/features/projects/ProjectListScreen.tsx` | Działająca koncepcja listy i wyboru projektu; stany odczytu |
| `frontend/src/api/contracts/projects.ts` | Active/Archived; Draft/Approved; missing_inputs; ready_for_approval |
| `backend/app/api/projects.py` | Istniejące operacje create/read/edit/copy/archive |
| `backend/app/api/schemas/catalog.py` | Wymiary katalogu, stawki, daty obowiązywania, pole kosztowe ograniczone uprawnieniami |
| `docs/architecture/decisions/ADR-0004…0007` | Zatwierdzanie, widoczność kosztów, kursy walut i konflikt zapisu |
| `docs/architecture/decisions/ADR-0003…` i `ADR-0008…` | Proponowane rozwiązania komercyjne i daty; pliki mają status Draft |
| `TEAM-CONTRACT.md`, `process/sdlc-flow.md` | SDD, odrębna ewaluacja, trzy bramki człowieka |
| `docs/PLAN.md`, `docs/architecture/capabilities.md` | Oddzielenie istniejących możliwości od propozycji |

## Plan stylistyczny przed wykonaniem

- Kolory bazowe: white `#FFFFFF`, black `#000000`, light steel `#F0F2F5`, steel gray `#3B4252`, blue `#0066FF`, orange `#FF5500`.
- Manrope: 28 px / 600 dla tytułu, 18 px / 700 dla sekcji, 14 px dla tekstu, 12 px dla tabel. Wartości finansowe z równą szerokością cyfr.
- Układ: stała nawigacja 216 px, kontekst scenariusza nad treścią, szeroki obszar tabeli; panel szczegółów po prawej zamiast wielu nakładających się okien.
- Wyróżnik: arkusz planowania, którego wiersze prowadzą do kosztu i przychodu. Wykresy podsumowują skutek decyzji.
- Teksty i liczby wyrównane odpowiednio do lewej i prawej. Jednostka widoczna przy każdym polu liczbowym.

Przegląd planu: ograniczono powtarzalne karty KPI do widoków wynikowych. Edycja obsady i kosztów jest tabelą. Usunięto wielkie litery i ozdobne narożniki ze slajdów, zachowując ich strukturę informacyjną. Nie użyto gradientów, ilustracji dekoracyjnych ani ciemnego panelu nawigacji, bo nie wspierają pracy nad kalkulacją i nie wynikają z przewodnika.

## Rozstrzygnięcia projektu UI

1. **Scenariusz jest kontekstem kalkulacji.** Model komercyjny nie jest pojedynczą obowiązkową właściwością projektu: różne scenariusze i zakresy mogą mieć różne reguły.
2. **Dwa niezależne statusy.** Projekt Active/Archived, scenariusz Draft/Approved. Gotowość do zatwierdzenia jest osobną oceną, nie trzecim statusem.
3. **Archiwizacja zachowuje dane.** Nie wprowadza dodatkowej blokady edycji draftów. Brak przycisku przywracania do czasu osobnego zakresu.
4. **Alokacja ma jednoznaczne jednostki.** Nie kopiujemy nieopisanej pary `1.0 / 0.5` z inspiracji. FTE, liczba osób i udział czasu billable są oddzielne.
5. **Suma FTE jest ważona headcount.** Dwie osoby po 1.0 FTE to 2.0 FTE zespołu. Średnia FTE jest ważona godzinami kalendarza, nie liczbą miesięcy.
6. **Nie przypisujemy fikcyjnego przychodu wierszom.** Przychód pozycji w siatce ma sens dla T&M. Przy Fixed Price i Outcome-based pokazujemy koszt pozycji; przychód pozostaje na poziomie reguły komercyjnej, chyba że człowiek zatwierdzi jawną metodę alokacji.
7. **Koszt wskazany przy roli nie tworzy drugiej kopii.** Rejestr kosztów i drawer pozycji pokazują ten sam element, identyfikowany wspólnym ID.
8. **Orange na bieli, blue na light steel.** Biały tekst na brand orange nie jest domyślnym stylem przycisku. Pomarańczowy przycisk ma czarny tekst; typowe akcje są niebieskie.
9. **Poufność raportu wymaga potwierdzenia.** Przewodnik prezentacji zawiera niejednoznaczne mapowanie Internal → Confidential i kategorię Secret dla danych finansowych. `Confidential` w makiecie raportu jest przykładem, nie decyzją o klasyfikacji. Eksport produkcyjny musi stosować zatwierdzoną politykę firmy.
10. **Przewodnik jest materiałem prezentacyjnym.** Jego 16:9, stopka slajdu i numeracja rozdziałów nie są bezpośrednio narzucane aplikacji. Zachowane są stałe marki i czytelność. Tekstowe reguły mają pierwszeństwo przed sprzecznymi grafikami (np. zakaz ALL CAPS).
11. **Manrope lokalnie.** Pakiet zawiera font i OFL, działa bez CDN. Produkcyjny sposób dystrybucji fontu wymaga osobnej decyzji technicznej.
12. **SSD interpretujemy jako SDD.** Repozytorium opisuje Spec-Driven Development i Human-in-the-Loop. Pakiet używa tego nazewnictwa i obecnych bramek Ninefold; nie ustanawia nowego procesu.

## Otwarty rejestr decyzji HIL

| ID | Decyzja | Propozycja / wpływ | Kiedy wymagana |
|---|---|---|---|
| HIL-UI-01 | Akceptacja kierunku, nawigacji i gęstości tabel | Jeden kierunek oparty na dostarczonej marce | Przed zmianą produkcyjnego shellu |
| HIL-UI-02 | Story Points | Cena za zaakceptowany punkt; sprint fee nie jest przyjęty | Przed implementacją F-06.4 |
| HIL-UI-03 | Granularność | Miesiące na start; sprinty dla SP, tygodnie później | Przed planowaniem siatki i kontraktów |
| HIL-UI-04 | Outcome-based | Progi, jednostka wyniku i sposób akceptacji zgodnie z realnymi umowami | Przed regułami przychodowymi |
| HIL-UI-05 | Widoczność agregatów | Ocena możliwości wywnioskowania indywidualnego kosztu | Przed F-10 i eksportem |
| HIL-UI-06 | Uwierzytelnianie i nadawanie uprawnień | Nie rozszerzać placeholder identity na produkcję | Przed ekranem dostępu |
| HIL-UI-07 | Plan–wykonanie | Poza obecnym pakietem | Przed rozszerzaniem zakresu |
| HIL-UI-08 | Net/gross, podatki, rozpoznanie przychodu | Makiety operują planistycznymi kwotami bez podatku | Przed specyfikacją finansową |
| HIL-UI-09 | ADR-0003 / ADR-0008 | Status Draft wymaga wyjaśnienia, mimo odwołań z innych ADR | Przed poleganiem na tych decyzjach |
| HIL-UI-10 | Klasyfikacja eksportów i znak firmowy | Użyć zatwierdzonych zasobów i oznaczeń, nie roboczego znaku | Przed udostępnianiem raportów |
| HIL-UI-11 | Język produktu | English jako kontynuacja obecnego kodu; lokalizacja PL opcjonalna | Przed finalnymi tekstami |

Nie zmieniono statusu żadnego ADR, zadania ani capability. Brak pytania w trakcie przygotowania makiet nie oznacza zatwierdzenia powyższych decyzji.
