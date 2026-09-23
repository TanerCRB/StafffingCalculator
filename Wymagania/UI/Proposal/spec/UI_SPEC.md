# Specyfikacja UI — propozycja 1.0

Data: 19.09.2026. Status: Proposed. Źródło wymagań: `Wymagania/Requirements_EN.md`.

## 1. Cel użytkownika i hierarchia

PM ma szybko odpowiedzieć: jaki zespół jest potrzebny, ile kosztuje realizacja, co generuje przychód oraz który wariant daje akceptowalną marżę.

Hierarchia: Workspace → Project → Scenario → Version → Staffing / Costs / Commercial terms / Assumptions / Results. Katalogi organizacji są niezależne od projektu. Reguła komercyjna jest przypisana do zakresu i czasu.

Główny przebieg: Projects → Create project → Assumptions → Staffing → Commercial terms → Additional costs → Overview → Compare → Versions & approval → Report.

Alternatywny przebieg: Approved version → Create draft copy → zmiana założeń → porównanie → osobne zatwierdzenie. Archiwizacja nie zastępuje zatwierdzania.

## 2. System wizualny i układ

- Wspólny punkt odniesienia: `frontend/src/styles/tokens.css`; kopia w `assets/brand-tokens.css` służy wyłącznie makietom.
- Produkcyjny frontend ma nadal jedno źródło tokenów. Nie kopiować całej warstwy demonstracyjnej jako drugiego design systemu.
- Desktop referencyjny: 1440 px, topbar 72 px, rail 216 px, margines treści 32 px, odstęp sekcji 24 px.
- Szerokość >1650 px: margines 44 px, limit obszaru treści 1800 px. To proponowane rozszerzenie obecnego limitu 1360 px, potrzebne dla siatki; do akceptacji HIL-UI-01.
- Tablet 761–1150 px: rail 184 px, drawer zamiast stałego panelu scenariuszy.
- Mobile ≤760 px: zwinięta nawigacja, metryki 2×2, układ jednokolumnowy, lokalne przewijanie tabel. Główne zastosowanie: odczyt i przegląd wyników. Nie obiecywać kompletnej edycji siatki na telefonie.
- Drawer desktop 440 px, mobile pełna szerokość. Formularz w drawerze ma stale dostępne Cancel i Save changes; w prototypie użyto jawnego Preview changes.
- Promienie: 4 px wewnętrzne kontrolki, 6–8 px grupy, 12 px dialog. Bez dekoracyjnych gradientów.
- Tekst: sentence case, zwięzłe etykiety, identyczne nazwy akcji we wszystkich miejscach.
- Liczby: tabular numerals, wyrównanie do prawej, waluta i jednostka w nagłówku lub polu. Nie mieszać `cost rate`, `selling rate`, `margin` i `markup`.
- Status nie może opierać się wyłącznie na kolorze. Wykresy mają legendę, nazwę osi/jednostkę i dostępną tabelę danych.

## 3. Wspólne komponenty

| ID | Komponent | Kontrakt zachowania |
|---|---|---|
| C-01 | AppShell | Globalna nawigacja, breadcrumb, użytkownik; brak wskaźnika stanu backendu w głównej ścieżce użytkownika |
| C-02 | ScenarioContext | Projekt, scenariusz, wersja, status, okres, waluta, stan zapisu |
| C-03 | ScenarioTabs | Zakładki zachowują kontekst ID scenariusza i wersji; deep link działa po odświeżeniu |
| C-04 | Money / Ratio | Wspólny formatter; DecimalString z API; null/restricted/n/a nie są zerem |
| C-05 | AllocationGrid | Stabilny identyfikator wiersza, miesiące w kolumnach, jednostka, suma ważona headcount |
| C-06 | DetailDrawer | Otwarcie, focus trap, Escape, powrót fokusu, ostrzeżenie o niezapisanych zmianach |
| C-07 | OriginLabel | Organization default / Project setting / Scenario override / Manual entry |
| C-08 | ValidationSummary | Lista błędów z linkami do konkretnych pól; błędy serwera przypięte do pola |
| C-09 | SaveStatus | Unsaved changes → Saving → Saved; odrębne Failed oraz Conflict |
| C-10 | ScenarioComparison | Jawny wariant odniesienia, różnice kwotowe i pp, wspólna waluta i okres |
| C-11 | ApprovalReview | Gotowość z serwera, przegląd założeń, świadoma akcja człowieka, brak auto-approval |
| C-12 | ReportPreview | Wersja, źródła, okres, waluta, zakres kosztów, klasyfikacja; identyczna polityka dostępu jak API |
| C-13 | RatePeriodEditor | Daty włączające w UI, otwarty koniec, wykrywanie luk i nakładania zakresów |
| C-14 | ErrorState | Brak danych, timeout, odmowa, niekompletność, konflikt; każda sytuacja ma własną treść |

## 4. Specyfikacja ekranów

### UI-01 — Projects

Cel: znaleźć projekt i wejść do jego scenariusza. F-01, F-13.

- Lista: nazwa, klient, okres, Active/Archived, liczba scenariuszy, menu działań.
- Wybór projektu otwiera panel jego scenariuszy; na małym ekranie przejście do szczegółów.
- Panel: nazwa scenariusza, Draft/Approved, gotowość, brakujące założenia, target margin. Target margin nie udaje obliczonej marży.
- Menu: View, Edit details, Copy project, Archive project. Delete nie zastępuje Archive.
- Domyślnie widoczne również projekty Archived. Filtr archiwum jest świadomą akcją użytkownika.
- Search, filtry i ewentualna paginacja wymagają osobnego zadania; nie są obecnie kontraktem listy API.
- Brak projektów spoza uprawnień, także jako nieaktywne wiersze. Błąd odczytu nie jest pustą listą.

### UI-02 — Overview

Cel: ocenić jeden scenariusz przed podjęciem decyzji. F-10, F-11.

- Przychód, wszystkie uwzględnione koszty, zysk, marża i odchylenie od targetu w pp.
- Wykres kosztów i przychodu miesięcznie, struktura kosztów i podsumowanie zespołu.
- Drill-down do składowych każdej wartości. Po wdrożeniu clicking metric otwiera tabelę obliczenia, nie kolejną kartę z tym samym numerem.
- Brak danych: wynik „Incomplete”, wskazane braki; nie pokazujemy zera ani nieoznaczonego częściowego wyniku.
- Nie sumować przychodów kilku alternatywnych scenariuszy projektu.

### UI-03 — Staffing plan

Cel: zaplanować ilość i okres zaangażowania. F-04, F-05, F-07.

- Wiersz: rola, seniority, location, engagement, headcount, billable share, alokacja miesięczna, koszt; przychód wiersza tylko dla reguł, które potrafią go jednoznacznie wyznaczyć.
- FTE jest wartością na osobę. Suma zespołu = Σ(headcount × FTE). Godziny = FTE × godziny właściwego kalendarza × headcount.
- Prezentacja FTE i godzin rozdzielona przełącznikiem. W docelowej implementacji ustalić jedno kanoniczne wejście i reguły konwersji; uniknąć zapętlonych przeliczeń.
- Headcount to dodatnia liczba całkowita. FTE i godziny nieujemne. Przekroczenie capacity oznaczone tekstem i ostrzeżeniem; dopuszczalność overtime wymaga reguły biznesowej.
- Komórka nieaktywna poza datami pozycji; częściowy miesiąc liczy rzeczywistą część kalendarza, bez arbitralnego dzielenia przez 30.
- Edycja klawiaturą: Tab/Shift+Tab, Enter zatwierdza lokalną wartość, Escape przywraca wartość przed edycją. Wklejanie zakresu to osobny zakres, nie założenie MVP.
- Pozycja ma jeden token współbieżności, także przy zmianie kilku miesięcy (ADR-0007 aneks SC-3-01).
- Liczby finansowe mogą być Restricted; nie pobieramy ich do UI, aby następnie ukryć CSS-em.

### UI-04 — Position details

Cel: skonfigurować wiersz bez utraty kontekstu siatki. F-03–F-05, F-07, F-08.

- Drawer: wymiary roli, anonimowo/nazwana osoba, headcount, daty, kalendarz, billable share, podstawa kosztu, koszt bazowy lub fully loaded, stawki z jednostkami i źródłem.
- Każda stawka ma okres. Gdy stawka zmienia się w trakcie projektu, tabela okresów zamiast jednego nadpisywanego pola.
- Szczegóły dodatkowych kosztów przypisanych do pozycji odwołują się do wspólnego rejestru.
- Preview impact pokazuje zmieniane składowe, następnie jawny zapis albo autozapis zgodnie z uzgodnioną regułą; nie oba mechanizmy konkurujące o tę samą zmianę.
- Nie zakładamy, że każdy posiada uprawnienie do nadpisania stawki kosztowej.

### UI-05 — Additional costs

Cel: skontrolować wszystkie koszty pozapłacowe. F-08.

- Rejestr: kategoria, opis, scope, podstawa, częstotliwość, okres, waluta źródłowa, suma w walucie scenariusza, recharged/supplier-funded.
- Filtry po kategorii i przypisaniu. Rozbicie sumy na one-off i recurring oraz harmonogram miesięczny.
- Koszty projektu, fazy i pozycji występują w tej samej agregacji dokładnie raz.
- Rezerwa i rzeczywisty wpis ryzyka z tym samym powiązaniem wymagają ostrzeżenia o możliwym podwójnym ujęciu.

### UI-06 — Cost details

Cel: skonfigurować powtarzalność i podstawę kosztu. F-08, F-02.

- Pola warunkowe: fixed amount / per person / per FTE / per hour / percentage.
- Dla percentage obowiązkowa nazwana baza kosztowa; nie dopuszczać zależności kosztu od samego siebie ani cykli.
- Dla recurring: częstotliwość, effective dates, zasada częściowego okresu. Dla one-off dokładnie jedna data wystąpienia.
- Zakres czasu musi mieścić się w regułach scenariusza; wydatki przed rozpoczęciem wymagają jawnej polityki, nie cichego pominięcia.
- Recharged koszt ma osobną regułę przychodową i ewentualny markup; nie zwiększa automatycznie przychodu bez jej wskazania.
- Widoczny podgląd liczby wystąpień i sumy. Zero jest dozwoloną świadomą wartością; pole puste to brak wejścia.

### UI-07 — Time & Material

Cel: ustawić rozliczany czas i właściwe stawki. F-06.1.

- Jednostka hour/day, godziny dnia, stawki per rola/osoba, okresy obowiązywania, limit godzin/budżetu i zasada jego przekroczenia.
- Sekcja rozszerzona: overtime, on-call, okres bez opłat, rabat i indeksacja. Nie stosować dwukrotnie udziału billable w godzinach i stawce.
- Podgląd: godziny billable × właściwe stawki, z uwzględnieniem dat i capu.

### UI-08 — Fixed Price

Cel: oddzielić uzgodnioną cenę od nakładu realizacji. F-06.2.

- Cena, scope, acceptance criteria, kamienie milowe i przypisanie przychodu do okresów.
- Zmiany zakresu i price adjustments są jawnymi pozycjami, z powodem; kary i bonusy mają regułę i limit.
- Suma kwot milestone musi odpowiadać kwocie do rozłożenia. W przeciwnym razie błąd walidacji.
- Większy staffing zwiększa koszt bez automatycznej zmiany ceny.
- Terminy faktur/płatności odrębne od planowanego przypisania przychodu; cash flow poza MVP.

### UI-09 — Outcome-based

Cel: ustalić rezultat, dowód i należne wynagrodzenie. F-06.3.

- Nazwa miernika, jednostka, baseline, target, okres pomiaru, źródło dowodu i osoba/rola akceptująca.
- Formuła wybierana spośród jawnych typów: kwota za cel, unit rate, progi, udział w korzyści; nie dowolny wykonywany kod.
- Część stała, bonus, częściowe osiągnięcie, min/max, penalties; rozróżnienie pp i wzrostu względnego.
- Tabela wariantów: rezultat, prawdopodobieństwo, przychód, koszt, zysk. Warianty kompletne i rozłączne, suma prawdopodobieństw 100%.
- Podgląd osobno: minimum gwarantowane, fee at target, expected revenue. Marża oczekiwana jako jasno opisana funkcja oczekiwanego zysku i przychodu, nie średnia procentów bez definicji.
- Makieta pokazuje prosty model all-or-nothing. Pozostałe reguły trzeba doprecyzować na umowach przed implementacją.

### UI-10 — Story Points

Cel: wycenić zaakceptowane jednostki dostawy. F-06.4.

- Cena/punkt, długość i liczba sprintów, velocity danego zespołu, forecast acceptance, cap i progi cenowe.
- Zasady akceptacji, nieukończonego zakresu, przeniesienia, rework i ponownej estymacji.
- Bez globalnego przelicznika SP → godziny. Koszt z planu zespołu, przychód z punktów kwalifikujących się do fakturowania.
- Forecast points i accepted points nie mogą udawać tego samego faktu. W makiecie wszystkie wielkości są prognozą.
- Sprint fixed fee to otwarta decyzja HIL-UI-02; nie ukryta domyślna alternatywa.

### Wspólne zachowanie UI-07–UI-10

- Zmiana modelu wymaga przeglądu wpływu; nie usuwa w tle zapisanych warunków. Umożliwić kopię scenariusza przed zmianą.
- Scope: cały projekt / faza / workstream oraz daty i waluta.
- Mixed pricing: tabela reguł per scope, zakaz niejawnego podwójnego naliczenia i osobna combined pricing rule.
- Autorytatywne obliczenie pochodzi z backendu. Przy brakującym kursie lub regule podgląd jest niekompletny.

### UI-11 — Compare scenarios

Cel: wybrać wariant na wspólnej podstawie. F-09, F-10.

- Minimum trzy kolumny, jawnie wybrany reference, wspólna waluta i okres albo blokada porównania z wyjaśnieniem.
- Revenue, cost, profit, margin, target, status i changed assumptions. Różnice marży w pp, nie procentach względnych bez etykiety.
- Porównanie nie zapisuje zmian w scenariuszach. Copy / Set reference osobnymi akcjami.
- Niekompletny scenariusz pozostaje widoczny z opisem braków, ale nie otrzymuje fikcyjnego rankingu.

### UI-12 — Sensitivity analysis

Cel: sprawdzić odporność wyniku. F-09.

- Wariant bazowy nieruchomy, suwaki zmieniają symulację; Reset przywraca bazę, Save as scenario tworzy nowy draft.
- Pierwsza plansza: koszt personelu i selling rate, z jawnym zakresem. Dalsze wymiary: utilization, FX, start delay, explicit risk event.
- Pokazać formułę/zakres zmiany, nie tylko kolor heatmapy. Nie deklarować prawdopodobieństwa na podstawie zakresu suwaka.
- Podczas przeliczenia status Calculating; poprzedni wynik wyraźnie oznaczony jako poprzedni, nie aktualny.

### UI-13 — Scenario assumptions

Cel: kontrolować pochodzenie i kompletność założeń. F-02, F-05.

- Okres, waluta, target, kalendarz, godziny, nieobecności, indeksacja, rezerwa, kursy i źródła.
- Override jest jawny i odwracalny do zapisanej wartości bazowej. Reset to default nie stosuje automatycznie najnowszej wartości bez przeglądu wpływu.
- Daty/waluta projektu są blokowane, gdy istnieje choć jeden approved scenario (ADR-0004).
- Kurs musi mieć kierunek pary i okres. Brak kursu nie oznacza 1.0; PLN → PLN może używać tożsamości bez sztucznego rekordu kursowego.

### UI-14 — Working calendars

Cel: ustalić pojemność i skutki nieobecności. F-05.

- Workweek, godziny dnia, święta, miesiące, wyjątki i okres obowiązywania.
- Widoczne dni robocze i godziny 1 FTE; osobne personal absences.
- Paid leave może zachowywać koszt przy braku revenue. Zasada zależy od cost basis i umowy.
- Kalendarz pokazany w makietach jest przykładem planistycznym do zatwierdzenia, nie zewnętrznym źródłem przepisów.

### UI-15 — Roles & rates

Cel: zarządzać słownikami i datowanymi stawkami. F-03, NF-10.

  - Jeden ekran katalogu: tabela stawek oraz pięć słowników: Roles, Seniorities, Locations, Engagement types i Vendors. Są to panele na jednej przewijanej stronie, nie osobne strony ani zakładki.
  - Tabela stawek wyświetla pięć wymiarów (w tym Vendor), okres obowiązywania, Default selling rate, Default cost rate i akcję Edit. Stawki backendu są godzinowe; prototyp pokazuje PLN/hour wyłącznie jako syntetyczny przykład.
  - Każdy słownik przechowuje nazwane wpisy i udostępnia Add oraz Rename. Formularz wspólny dla wszystkich wymiarów zawiera tylko wymagane pole `<dimension> name`, Cancel i Save; formularz otwiera się wewnątrz właściwego panelu. W danym momencie otwarty jest jeden formularz katalogu.
  - Wartości list w makietach są przykładowe, nie stanowią domyślnych danych ani zawartości API. Grafiki UI-15A–D to powiększenia czterech paneli z tego samego widoku, nie nowe trasy aplikacji.
  - Formularze zapisu są wzorem układu, nie podłączonym zapisem. Rzeczywista aplikacja zapisuje przez katalogowe API, odświeża wszystkie zbiory i może pokazać odmowę 403, konflikt 409 albo błąd walidacji.
  - Backend obecnie obsługuje godzinowe stawki katalogowe. Daily/monthly w UI są przyszłym rozszerzeniem F-07, nie istniejącą możliwością katalogu.
  - Nie tworzyć równoległych stawek dla tej samej krotki tylko dlatego, że mają różne waluty.
  - Overlap: walidacja wskazuje konfliktujące okresy bez ujawniania niedozwolonych kosztów. Gap: brak aktywnej stawki.
  - Pole kosztowe ukryte dla nieuprawnionego: Restricted, nigdy zero. Globalny katalog ma inną bramkę niż dane projektu; zob. ADR-0005 i HIL-UI-05/06.

### UI-16 — Versions & approval

Cel: przegląd wersji i świadome zatwierdzenie. F-12.

- Wersje, autor/czas, diff pól i źródło kopii. Dane historyczne wyłącznie z rzeczywistego audytu, bez odtwarzania autora na podstawie updated_at.
- Warunki zatwierdzenia: kompletność z serwera, brak konfliktów i brak oczekujących zapisów, dostęp użytkownika.
- Dwustopniowa akcja: przegląd i potwierdzenie. Margines poniżej targetu nie musi być automatycznie blokadą — polityka do ustalenia.
- Po zatwierdzeniu aplikacja otwiera read-only wersję. Błąd operacji nie może pokazać Approved.

### UI-17 — Report preview

Cel: przekazać wynik z założeniami. F-11, F-12.

- Projekt, scenariusz, wersja/status, okres, waluta, data wygenerowania, zakres kosztów, podsumowanie, wykres, założenia i ograniczenia.
- Format produkcyjny PDF oraz XLSX/dane szczegółowe. Podgląd nie ujawnia kosztów zakazanych w API.
- Approved: liczby z migawki. Zgodnie z ADR-0004 opisowe pola projektu mogą być bieżące; nie obiecywać identycznego historycznego nagłówka.
- Prototyp udostępnia browser print oraz CSV. To demonstracja, nie implementacja F-11/F-12.

### UI-18 — Project access

Cel: zrozumieć kto widzi i zmienia dane. F-13.

- Uprawnienia do read/edit/copy/archive/staffing/approval niezależne od widoczności kosztów.
- Dane projektu spoza scope dają taki sam widok jak nieistniejący projekt.
- Personnel costs wymagają globalnego uprawnienia AND przypisania z flagą. Katalog organizacyjny ma opisany w ADR wyjątek; nie przenosić go na projekt.
- Każda zmiana grantów wymaga istniejącego kontraktu i polityki autoryzacji. Ten ekran jest propozycją; nie ma w obecnym kodzie kompletnej ścieżki nadawania dostępu ani produkcyjnego identity.

### UI-19 — System states

Wzornik Empty, Loading, Incomplete, Timeout, Not found, Save conflict, zero revenue i capacity warning. NF-05, NF-07, NF-08, F-13.

- 401: sesja wymaga wznowienia. 403 na liście: brak dostępu do funkcji, bez wierszy. 404 zasobu: identycznie dla nieistnienia i braku scope.
- 422: błędy pól i zachowane wejście. 409: osobny konflikt, bez automatycznego nadpisania. 5xx/timeout: retry, bez udawania zapisania.
- Saved oznacza potwierdzenie z serwera; offline nie otrzymuje zielonego potwierdzenia.

### UI-20 — Create project

Cel: minimalny start bez konfiguracji całej organizacji. F-01, NF-07.

- Nazwa/klient/owner wymagane i niepuste po trim; daty poprawne i uporządkowane; waluta zgodna z kontraktem.
- Sekwencja projektu i pierwszego scenariusza nie oznacza jednej atomowej operacji, dopóki API jej nie gwarantuje.
- Po utworzeniu przejść do projektu/draftu; błąd zachowuje formularz; brak uprawnień usuwa możliwość akcji.
- Prototyp dodaje wyłącznie nazwę projektu do pamięci sesji — nie symuluje trwałego zapisu całego formularza.

### UI-21 — Design system

Artefakt projektowy, nie pozycja nawigacji produktu. Pokazuje paletę, typografię, statusy i kontrolki. Docelowe komponenty powinny mieć własne przykłady stanów w narzędziu zespołu, bez wymagania konkretnego nowego frameworka.

### UI-22 — Approved version

Cel: odczytać zachowany wynik. F-12.

- Zamek, numer, approver/time, status read-only. Brak aktywnych kontrolek edycji we wszystkich zakładkach tej wersji.
- Create draft copy tworzy nową niezależną wersję. Nie zmienia statusu oryginału i nie kopiuje uprawnień kosztowych.
- Prototyp pokazuje stan wizualny; jego pozostałe linki wracają do bazowego draftu demonstracji. Produkcja musi zachować ID approved version na całej nawigacji.

### UI-23 — Save conflict

Cel: ochronić wejście użytkownika bez zgadywania cudzej zmiany. NF-05, ADR-0007.

- Zachowaj lokalne pole i opisz, że zapis nie nastąpił.
- Copy my input, Reload latest version, potem świadome ponowne zastosowanie. Bez overwrite anyway i bez auto-merge.
- Odpowiedź 409 nie ujawnia autora ani treści cudzego zapisu. Dopiero uprawniony ponowny odczyt dostarcza aktualne dane.
- Token pozycji dotyczy całej jej siatki, nie poszczególnych komórek.

### UI-24 — Organization defaults

Cel: zarządzać konfiguracją używaną do nowych kalkulacji. F-02, NF-10.

- Domyślne currency/target/calendar, kategorie kosztów, kursy i odnośniki do ról/stawek/kalendarzy.
- Uprawnienie organizacyjne, nie prawo wynikające z posiadania jednego projektu.
- Zmiana wymaga walidacji effective dates i wpływa na przyszłe użycie; istniejące kalkulacje zachowują zapisane wartości.
- Sekcja zarządzania szablonami kalkulacji jest propozycją rozszerzenia; nie zakładać istniejącego kontraktu CRUD.

## 5. Wymagania interakcji, dostępności i responsywności

1. Użytkownik zawsze wie, który projekt, scenariusz i wersję edytuje.
2. Autosave po poprawnym wejściu, propozycja debounce 700 ms; walidacja lokalna nie zastępuje serwera. Potwierdzenie kończy spinner. Błąd zachowuje lokalne wejście.
3. Przed nawigacją z niezapisanym formularzem: zachowaj / odrzuć / wróć; nie usuwać zmian milcząco. Dotyczy również zmiany scenariusza.
4. Dialog ma nazwę, focus trap, Escape i powrót do inicjatora. Drawer edycyjny spełnia analogiczny kontrakt.
5. Formularze mają trwałe etykiety, opis jednostek, `aria-describedby` i `aria-invalid` dla błędów.
6. Tabele używają nagłówków semantycznych. Grid edycyjny wymaga testów klawiatury i screen reader; nie przyjmować, że każdy widget grid jest automatycznie dostępny.
7. Minimum 4.5:1 dla zwykłego tekstu i 3:1 dla istotnych granic/fokusu jako cel implementacji. Kolory statusów i disabled również czytelne; finalny audyt produkcyjny osobno.
8. Sticky row labels i header nie zakrywają aktywnej komórki. Przewijanie lokalne, nie całej strony w bok. Rozmiary 390, 768, 1024, 1440 i 1920 do sprawdzenia w implementacji.
9. Skeleton nie udaje zera; aktualizacje wyników i błędy mają kontrolowane `aria-live`, bez ogłaszania każdej cyfry podczas pisania.
10. Pola stawki przyjmują format zgodny z locale, ale przechodzą do wspólnej warstwy decimal string. Formatowanie i zaokrąglenie wyłącznie zgodnie z ADR-0002.

## 6. Dane referencyjne makiet

Projekt przykładowy `Commerce platform`, 01.09.2026–28.02.2027, PLN, T&M, target 25%.

| Miara | Wartość |
|---|---:|
| Pozycje / osoby | 5 / 6 |
| Godziny jednego pełnego etatu w okresie | 992 |
| Godziny planowane zespołu | 4 590 |
| Godziny billable | 4 242,8 |
| Koszt personelu | 747 020 PLN |
| Koszty dodatkowe | 42 000 PLN |
| Koszt łączny | 789 020 PLN |
| Przychód | 1 124 676 PLN |
| Zysk | 335 656 PLN |
| Marża | 29,84% |

Szczegóły w `demo-data.json`. Lean delivery oznacza syntetyczne obniżenie kosztu personelu o 40 000 PLN; nie deklaruje dowiedzionej optymalizacji zespołu. Downside stosuje +10% personelu i −5% przychodu. Symulacja demonstracyjna pokazuje pełne PLN; nie jest silnikiem finansowym ani wzorcem implementacji Decimal.

## 7. Zakres wierności prototypu

Grafiki i HTML są referencją kompozycji oraz przepływów. Specyfikacja opisuje także reguły niewykonywane przez prototyp: trwałość, uprawnienia, atomic save, decimal arithmetic, wersjonowanie, pełną walidację i eksport produkcyjny. Żadnej z tych możliwości nie należy uznać za wdrożoną na podstawie samej makiety.
