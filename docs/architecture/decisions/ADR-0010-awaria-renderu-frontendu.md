# ADR-0010 — Awaria renderu frontendu: granica błędu i kształt odpowiedzi

**Status:** Draft — pending approval

> Dokument powstał w odpowiedzi na pytanie bramki 1 Q-1 (Issue #43, SC-1-09). Rekomendacja
> architekta: wariant A — jedna granica błędu w powłoce, wokół gniazda ekranu. Rozstrzygnięcie
> zapadło na bramce 1 (2026-09-22): wariant A, plus cztery domknięcia luk analityka tego samego
> dnia — wpisane w treść punktów niżej, nie dopisane jako osobny aneks.

## Kontekst

Dzisiejszy frontend nie ma ani jednej granicy błędu (`componentDidCatch` /
`getDerivedStateFromError` nie występują w `frontend/src/` ani razu). Wyjątek rzucony w trakcie
renderu dowolnego komponentu odmontowuje całe drzewo Reacta i zostawia pustą stronę: bez
komunikatu, bez railu, bez drogi powrotnej inaczej niż przez przeładowanie. Stan ten jest
nieodróżnialny od aplikacji, która się nie uruchomiła.

Ta klasa awarii jest w repozytorium znana i nazwana — w komentarzu, nie w decyzji.
`frontend/src/api/client.ts` uzasadnia `isCatalogRateShape` zdaniem: wiersz bez
`default_selling_rate` „would throw a `TypeError` mid render, in a codebase with no error
boundary, taking the whole screen down to a blank page". Rejestr możliwości powtarza to samo
uzasadnienie przy wpisie o walidacji kształtu („apka nie ma error boundary"). Mechanizm obronny
istnieje więc dwa razy, w dwóch funkcjach jednego pliku, a przesłanka, na której go zbudowano,
nie jest zapisana nigdzie, gdzie mogłaby zostać świadomie zmieniona.

`ProjectListScreen` nie ma żadnej z dwóch ochron: `getProjects` sprawdzał wyłącznie
`Array.isArray(payload?.projects)` i nie oglądał ani jednego pola wiersza, a `formatPercentString`
na ścieżce wyświetlania wywołuje `roundDecimalString`, który wywołuje `.trim()`. Wiersz z
`target_margin_percent` przysłanym jako liczba — zmiana serializacji po stronie backendu, proxy
przepisujące ciało — jest pustą stroną, nie komunikatem.

Czego nie rozstrzyga żadna przyjęta decyzja:

- ADR-0009 obowiązuje, własnymi słowami, „każdy przyszły formularz zapisujący" (pkt 7). SC-1-09
  nie zapisuje niczego. Jego pkt 5 (sześć rozróżnialnych, nazwanych zakończeń zapisu, „żaden nie
  jest podciągiem innego") jest wzorem dla reguły renderu, nie jej źródłem.
- ADR-0009 pkt 4 mówi o walidacji **wychodzącej** („kształt wejścia… nigdy stan danych"). O
  walidacji kształtu odpowiedzi **przychodzącej** nie mówi nic — a ta powstała dwukrotnie, na
  podstawie ustalenia reviewera (R-02, SC-2-03), i nigdy nie została zdecydowana.
- ADR-0005 zakazuje klientowi decydowania o widoczności i wprost odrzuca „Filtrowanie kosztów
  osobowych w warstwie UI". Nie mówi nic o tym, co wolno powiedzieć komunikatowi o awarii renderu
  — a wyjątek Reacta niesie w komunikacie i w stosie wartości, które właśnie renderowano.
- Żadne wymaganie nie mówi „interfejs nie może się wywracać po cichu". NF-05 dotyczy autozapisu i
  statusu zapisu, NF-07 formularzy i jednostek — obu w tym zadaniu nie ma. NF-08 jest hakiem
  przyjętym w tym repozytorium przez precedens (jawny błąd zamiast wiecznego ładowania) i
  obowiązuje wprost wobec samego komunikatu awarii. NF-11 obowiązuje twardo, po stronie
  diagnostyki. Brak wymagania jest tu powodem istnienia decyzji, nie argumentem przeciw niej.

Ten ADR istnieje, bo granica błędu jest mechanizmem przekrojowym: powstaje raz i obowiązuje każdy
ekran, także te z bloków 3-5, których jeszcze nie ma. Precedens ustanowiony w pliku jednego
komponentu zostanie skopiowany razem z tym, czego nikt nie rozważył — w szczególności razem z
punktem 4 poniżej, jedynym miejscem, w którym milczenie grozi wyniesieniem chronionej wartości,
a nie tylko niespójnym ekranem.

## Decyzja

1. **Jedna granica błędu, w powłoce, wokół gniazda ekranu.** Granica jest komponentem
   wielokrotnego użytku montowanym przez `AppShell` wokół `{children}` — nie wokół samej powłoki
   i nie osobno przez każdy ekran. Dwa powody. Wokół powłoki: awaria jednego ekranu zabrałaby
   rail, czyli jedyną drogę ucieczki, i zamieniła awarię odwracalną w nieodwracalną. Osobno w
   każdym ekranie: wartość granicy leży wyłącznie w przyczynach, których nikt nie wyliczył, więc
   zawężenie jej do ekranu już przejrzanego kupuje dokładnie tę część zakresu, którą pokrywa
   walidacja kształtu. Ekran dostaje własną, węższą granicę tylko wtedy, gdy ma po temu powód
   zapisany tu datowanym aneksem.

   **Czego ta granica NIE łapie (bramka 1, domknięcie luki 1 — zapisane wprost, nie domyślne).**
   React łapie w granicy błędu wyłącznie wyjątki **fazy renderu**. Wyjątek rzucony w handlerze
   zdarzenia, w callbacku `Promise`/`setTimeout`, w efekcie wykonywanym po commit, albo w samej
   granicy, idzie własną ścieżką do globalnego handlera przeglądarki i granica nie będzie w to
   zaangażowana. Ta decyzja nie twierdzi, że aplikacja nie może paść — twierdzi, że jedna,
   konkretna i najczęstsza klasa awarii przestaje kasować chrome.

2. **Granica jest zabezpieczeniem ostatniej instancji, nigdy zamiennikiem walidacji kształtu.**
   Każda funkcja odczytu w `frontend/src/api/client.ts` sprawdza kształt wiersza, który obiecuje
   jej typ — pole po polu, nie samo „to sparsowało się jako tablica" — i zamienia niezgodność w
   nazwany `ApiError`, czyli w istniejący, nazwany stan awarii ekranu. Kolejność jest wiążąca:
   ładunek niezgodny z kontraktem jest zatrzymywany na granicy sieciowej, a granica błędu łapie
   wyłącznie to, czego nikt nie przewidział. Osłabienie sprawdzenia kształtu z uzasadnieniem „teraz
   łapie to granica" jest odstępstwem od tej decyzji i wymaga aneksu, nie komentarza w kodzie.
   Pusta lista pozostaje wyłącznie zdaniem serwera: ładunek niezgodny z kontraktem nigdy nie
   degraduje się do listy pustej.

   Oba mechanizmy muszą być **niezależnie zabijalne** (bramka 1, Q-2): usunięcie granicy zabija
   inne testy niż osłabienie walidacji kształtu; jeśli jedna mutacja zabija oba, para nie jest
   dowiedziona jako dwa mechanizmy.

3. **Zatrzymana awaria nie przeżywa ekranu, który ją wywołał.** Granica błędu w Reakcie nie
   zeruje się sama. Stan awarii jest związany z instancją ekranu: nawigacja railem na inny ekran
   pokazuje ten ekran, nie utrwalony komunikat o awarii poprzedniego. Granica, która tego nie
   spełnia, zamienia jedną zepsutą stronę w zepsutą aplikację.

   **Domknięcie luki 3 (bramka 1, szerzej niż rekomendacja analityka):** fallback niesie też
   własną drogę wyjścia z miejsca, w którym stoi użytkownik — przycisk „spróbuj ponownie" —
   który montuje od nowa ekran, który padł, z tego samego miejsca w drzewie, dając ten sam skutek
   co nawigacja railem (ekran żyje naprawdę, jego odczyty ruszają). Re-render, który nie jest
   nawigacją ani naciśnięciem tego przycisku, fallbacku nie kasuje.

4. **Zatrzymana awaria nie wynosi wartości.** Komunikat zastępczy nie renderuje niczego
   pochodzącego z ładunku; `console.error`, jakakolwiek telemetria i jakikolwiek atrybut w DOM nie
   niosą wartości wiersza. Wyjątek Reacta niesie je w komunikacie i w stosie z natury rzeczy —
   granica jest pierwszym miejscem na froncie, gdzie chroniona stawka (`default_cost_rate`, koszt
   osobowy — AC-06, ADR-0005 aneks 2026-09-19 pkt 3) może trafić do konsoli. To jest ta sama
   reguła, którą ADR-0009 pkt 6 ustanawia dla odmowy zapisu, przeniesiona na ścieżkę renderu, i ten
   sam zakaz, który backend ma dowiedziony (`test_statement_errors_hide_parameters.py`, NF-11).

   **Domknięcie luki 2 (bramka 1), skorygowane 2026-09-22 (Reviewer R-01, SC-1-09): zakres
   ograniczony do wywołań aplikacji — ale React nie milczy w produkcji.** React sam loguje
   złapany błąd (`console.error(error)` w `logCapturedError`), i robi to **także w buildzie
   produkcyjnym**: `react-dom.production.min.js` 18.3.1 zawiera
   `function Li(a,b){try{console.error(b.value)}catch(c){…}}`, podpięte bezwarunkowo jako
   `callback` aktualizacji błędu dla każdego komponentu klasowego z `getDerivedStateFromError` —
   zweryfikowane w zainstalowanym pakiecie, nie z pamięci. Wcześniejsze zdanie „nieobecne w
   buildzie produkcyjnym" było nieprawdziwe i uzasadniało brak działania: komunikat
   `roundDecimalString` niósł surową wartość (`Not a fixed-point decimal string: "…"`), więc
   chroniona stawka trafiała do konsoli produkcyjnej mimo że sama granica nie loguje niczego.
   NF-11 obowiązuje na tej ścieżce dwojako: (a) aplikacja nie wykonuje własnych wywołań
   `console.*`/telemetrii niosących wartość (fallback, `componentDidCatch`, telemetria) — bez
   zmian; (b) **żaden wyjątek rzucany w fazie renderu nie nosi wartości wiersza w
   `Error.message`**, bo tego, co loguje sam framework, żaden komponent nie jest w stanie
   stłumić. Jedynym miejscem egzekwowalnym jest miejsce rzutu: `frontend/src/lib/money.ts` rzuca
   stałą `NOT_A_DECIMAL_STRING` bez interpolacji (dowód: `money.test.ts`).

5. **Zatrzymana awaria jest własnym, nazwanym stanem.** Nie dzieli komunikatu z `failed`,
   `denied` ani `timed-out`: te trzy mówią, co odpowiedział serwer, a ten mówi, że ekran nie dał
   się narysować — inna przyczyna, inna rada dla człowieka, inna diagnostyka. Żaden z tych
   komunikatów nie jest podciągiem innego (precedens: trzy różne braki w jednym wierszu katalogu,
   SC-2-02). Komunikat jest tekstem, osiągalnym z klawiatury, a kolor nie jest jedynym nośnikiem
   jego znaczenia (NF-08).

6. **Formater nie mięknie.** Wartość pieniężna lub procentowa w złym typie jest błędem ładunku,
   nie problemem formatowania. `frontend/src/lib/money.ts` nie dostaje `try/catch`, wartości
   zastępczej ani `Number()` w miejsce rzutu — ADR-0002 („frontend formatuje kwoty wyłącznie przez
   `frontend/src/lib/money.ts` — nigdy `toFixed()` ad hoc w komponencie") i punkt 2 powyżej
   rozstrzygają, gdzie ten błąd ma być zatrzymany, i nie jest to formater.

7. **Odczyt, którego nikt już nie chce, jest przerywany.** Każda funkcja odczytu w
   `client.ts` przyjmuje `AbortSignal`, a ekran przerywa nim każde żądanie, którego wynik
   przestał być komukolwiek potrzebny — przy odmontowaniu i przy porzuceniu odczytu z innego
   powodu. Strażnik „nie wołaj `setState` po odmontowaniu" nie jest realizacją tej reguły: nie
   zwalnia gniazda HTTP i jest niewidoczny w przeglądarce, co w tym repozytorium raz już pozwoliło
   defektowi przeżyć zielony zestaw testów (`CatalogScreen.tsx`, Reviewer R-02, 2026-09-22).

8. **Zasięg precedensu.** Ta decyzja obowiązuje każdy ekran montowany w powłoce, obecny i
   przyszły. Poza jej zakresem i wracające tu własnym, datowanym aneksem przy pierwszym zadaniu,
   które tego potrzebuje: raportowanie awarii do usługi zewnętrznej (nie istnieje i wymaga własnej
   decyzji wobec NF-11 i NF-04), ponawianie renderu bez udziału człowieka poza przyciskiem
   "spróbuj ponownie" (pkt 3), oraz granica błędu w obrębie jednego ekranu, gdyby któryś ekran
   miał jej kiedyś potrzebować.

## Konsekwencje

- Granica jest mechanizmem, którego jedynym dowodem jest wyjątek wstrzyknięty przez test — w
  działającej aplikacji nie ma dziś komponentu, o którym wiadomo, że rzuca. To nie czyni dowodu
  pustym, ale czyni go dowodem na zatrzymanie, nie na przyczynę: mutacja usuwająca granicę musi
  zabijać test, a mutacja osłabiająca walidację kształtu musi zabijać **inny** test. Jeśli oba
  mechanizmy pokrywa jeden test, punkt 2 jest niedowiedziony.
- Punkt 2 oznacza, że uzasadnienie istniejących wpisów rejestru o `isCatalogRateShape` /
  `isDimensionEntryShape` („apka nie ma error boundary") przestaje być prawdziwe co do przesłanki,
  choć pozostaje prawdziwe co do reguły. Wpisy wymagają poprawienia uzasadnienia przy najbliższej
  bramce 3 — inaczej rejestr zaczyna mówić o systemie rzecz nieaktualną.
- Punkt 4 nie jest dowiedziony przez zestaw K-03: pod mutacją przywracającą interpolację wartości
  w rzucanym wyjątku (`frontend/src/lib/money.ts`) `screenCrashContainment.test.tsx` pozostaje
  zielony — klasyfikator K-03 rozpoznaje wywołanie Reacta jako "nie aplikacji" i je pomija. Dowód
  punktu 4 leży w `money.test.ts` i dotyczy własności samego obiektu `Error` (brak wartości w
  `message`), nie zachowania granicy błędu.
- Punkt 1 kładzie na `AppShell` drugą odpowiedzialność poza chromem i przenoszeniem fokusu.
  Powłoka pozostaje bez własnych odczytów; „nie czyta niczego" i „zatrzymuje awarię tego, co
  renderuje" to dwa różne zdania i tylko pierwsze było dotąd obietnicą tego komponentu.
- Mechanizm przenoszenia fokusu w `AppShell` szuka `h1, h2` wewnątrz `main`. Po zatrzymanej awarii
  nagłówek ekranu nie istnieje; fallback dziś nie dostarcza własnego — fokus po nawigacji na
  zatrzymaną awarię nie ma gdzie usiąść. Nazwane jako otwarte ryzyko dostępności, nie zamknięte
  tym zadaniem.
- `CatalogScreen` jest objęty mechanizmem bez własnego zadania (konsekwencja wariantu A), ale nie
  jest tym dowiedziony: nic nie wprowadza realnego payloadu katalogu w wyjątek renderu. Pierwsza
  Story dotykająca tego ekranu powinna domknąć tę różnicę między „objęty" a „dowiedziony".

## Rozważane alternatywy

- **Brak ADR; SC-1-09 ustanawia precedens implicite** — odrzucone: osiem rozstrzygnięć powyżej i
  tak zapadnie w tym zadaniu, tyle że w pliku jednego komponentu. Następny ekran skopiuje je razem
  z punktem 4, jedynym, w którym milczenie kosztuje wyniesioną wartość, a nie niespójny ekran.
- **Granica wyłącznie wokół `ProjectListScreen`** — odrzucone: zabezpieczenie zawężone do kodu już
  przejrzanego kupuje tę część zakresu, którą i tak pokrywa punkt 2. Ta sama klasa awarii żyje
  dziś w `CatalogScreen`, a wariant ten zostawiłby ją otwartą bez zapisu, że została zostawiona.
  Zmierzone w implementacji: przeniesienie granicy do `ProjectListScreen` zostawia każdy test
  poziomu `<App/>` zielony i zabija wyłącznie testy poziomu powłoki.
- **Granica wokół całej powłoki (`App.tsx`)** — rozważone: łapie dodatkowo awarię samej powłoki.
  Odrzucone: awaria ekranu zabiera wtedy rail, więc jedyną drogą wyjścia z pustego stanu jest
  przeładowanie strony. Skutek gorszy przy tym samym koszcie.
- **Bez granicy; wyłącznie walidacja kształtu w SC-1-09** — rozważone i niebłahe: defekt opisany w
  Issue #43 („zniekształcony wiersz wywraca drzewo") zamyka się samą walidacją kształtu. Odrzucone,
  bo klasa awarii renderu z przyczyny niewyliczonej zostaje wtedy otwarta bez żadnego mechanizmu,
  który by ją kiedykolwiek zamknął, a zadanie, które miałoby to zrobić, nie ma wyzwalacza.
- **Fallback bez „spróbuj ponownie", z railem jako jedyną drogą wyjścia** — rekomendacja analityka
  (poza zakresem); odrzucone na bramce 1: ekran, z którego jedynym wyjściem jest pójście gdzie
  indziej, jest ekranem bez powrotu do tego, co użytkownik robił.
- **Raportowanie zatrzymanych awarii do usługi zewnętrznej** — odrzucone: projektowanie przed
  potrzebą, i to projektowanie wprost w obszarze NF-11 i NF-04. Punkt 8 zostawia to poza zakresem.

## Powiązane wymagania

NF-08 (awaria jako nazwany, osiągalny z klawiatury stan, kolor nie jako jedyny nośnik — precedens
„jawny błąd zamiast wiecznego ładowania"), NF-11 (diagnostyka bez wartości wiersza), AC-06 i NF-04
przez ADR-0005 (komunikat zastępczy nie odtwarza niczego, czego serwer nie wydał); ADR-0002
(formater nie łagodzi błędu typu), ADR-0009 (wzór rozróżnialnych, nazwanych zakończeń — pkt 5 —
i reguła „odmowa nie wzbogaca się o wartości" — pkt 6 — przeniesione na ścieżkę renderu; ADR-0009
pozostaje ograniczony do formularzy zapisujących).

**Ryzyko nazwane, nie zamknięte:** oba mechanizmy, na których to zadanie się opiera
(`AbortController` przy odmontowaniu, walidacja kształtu wiersza), są w rejestrze możliwości
oznaczone jako „test, no mutation". Wzorzec przerywania odczytu raz już zawiódł w tym repozytorium
w sposób niewidoczny dla zielonego zestawu testów (ponowny odczyt po zapisie budował
`AbortController`, nie przerywał go i nie był podpięty do żadnego sprzątania — `CatalogScreen.tsx`,
Reviewer R-02, 2026-09-22). Kopiowanie mechanizmu niedowiedzionego mutacją jest kopiowaniem
mechanizmu niedowiedzionego; SC-1-09 domyka to własnym przebiegiem mutacyjnym dla obu mechanizmów
(bramka 1, Q-3), nie dziedziczy słabszy standard po precedensie.
