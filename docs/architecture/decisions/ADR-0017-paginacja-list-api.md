# ADR-0017 — Wzorzec paginacji list w API (ogólny)

**Status:** Accepted

## Kontekst

Jeden zbudowany precedens: `GET /catalog/rates` (`limit`/`offset`/`total`,
`DEFAULT_RATE_LIST_LIMIT`/`MAX_RATE_LIST_LIMIT`/`MAX_RATE_LIST_OFFSET`), przyjęty punktowo na
bramce 2 SC-2-03 (komentarz Issue #46), nigdy nie podniesiony do ADR-u. `GET /projects` (SC-1-05)
odłożyło paginację 2026-09-18, nierozstrzygnięte do dziś. SC-3-05 (`GET .../staffing-positions`)
jest trzecim niezależnym miejscem wynajdującym ten sam kształt — stąd ten ADR, żeby czwarte i
kolejne miejsca dziedziczyły gotowy wzorzec zamiast powtarzać rundę usterek, jaką reviewer
przeszedł punktowo przy SC-2-03 (nieindeksowany `count(*) OVER()`, `offset` bez górnej granicy,
pusta strona nieodróżniona od pustego katalogu).

## Decyzja (bramka 1, 2026-09-26, SC-3-05)

1. **Zasięg tego ADR — prospektywny, nie retroaktywny.** Wzorzec obowiązuje każdą NOWĄ listę API
   od tego momentu (SC-3-05 jest pierwszym konsumentem). `/catalog/rates` zostaje jako historyczny
   precedens niezmieniony — nie jest tym ADR-em migrowany wstecz; jeśli przyszłe zadanie dotknie
   ten endpoint z innego powodu, wyrównanie do tego wzorca jest wtedy naturalnym, ale osobnym,
   krokiem. `GET /projects` (SC-1-05) dziedziczy ten wzorzec, gdy zostanie podjęte.
2. **Mechanizm: `limit`/`offset`/`total`, nie kursor.** Spójne z jedynym istniejącym precedensem
   kodu. `list_positions` (i każdy przyszły konsument tego wzorca) musi sortować deterministycznie
   przez kolumnę porządkującą + `id` jako tie-break (porządek totalny) — stabilna kolejność
   między stronami jest warunkiem wstępnym, nie luksusem. "Page drift" przy współbieżnym
   wstawieniu między odczytem strony 1 i strony 2 jest **nazwanym, zaakceptowanym stanem
   prawdziwym, nie awarią** (mirror ADR-0008, aneks 2026-09-21, zastosowany tu przez analogię, nie
   przez wspólny mechanizm) — żadne zadanie przyjmujące ten wzorzec nie musi budować ochrony przed
   tym zjawiskiem, chyba że jego własne Story jawnie tego zażąda.
3. **Stałe: osobne per zasób, nazwane spójnie.** `DEFAULT_<ZASÓB>_LIST_LIMIT` /
   `MAX_<ZASÓB>_LIST_LIMIT` / `MAX_<ZASÓB>_LIST_OFFSET` — jeden wspólny limit dla całego API
   ignorowałby, że różne zasoby mają różny rozmiar wiersza i różny profil kosztu. Każdy konsument
   tego wzorca deklaruje własne stałe, jawnie.
4. **Kolejność stron:** kolumna porządkująca specyficzna dla zasobu (np. `start_date` dla
   pozycji obsady, `effective_from` dla stawek) + `id` jako tie-break — kierunek (ASC/DESC) jest
   decyzją zasobu, nie tego ADR.
5. **Kształt odpowiedzi:** `{"total": int, "<zasób>": [...]}` — `total` to liczba wierszy
   pasujących do filtra PRZED stronicowaniem, policzona w tym samym odczycie (jak dziś
   `list_rates`), nigdy osobnym zapytaniem mogącym się rozjechać z wynikiem strony.
6. **Parametry opcjonalne, domyślne zachowanie jawnie ustalone przez każde zadanie z osobna.**
   Ten ADR nie narzuca, czy brak parametrów oznacza "cała lista" czy "pierwsza strona o
   domyślnym rozmiarze" — to jest decyzja *Done when* każdego konsumenta (dla SC-3-05:
   "identyczne z dzisiejszym", bo dziś nie ma frontendowego konsumenta do złamania).
7. **Walidacja parametrów poza dozwolonym zakresem → `422` odrzucający, nigdy ciche
   przycinanie** (`min`/`max` na wartości). Nazwa pola w komunikacie, nigdy wartość wiersza
   (NF-11).
8. **Kolejność `404`/zasięg przed `422`/walidacja parametrów, na zasobach zasięgowych.**
   `/catalog/rates` nie ma tego problemu (brak zasięgu projektowego) — każdy przyszły konsument
   tego wzorca na zasobie zasięgowym (jak `staffing-positions`) musi jawnie zapewnić, że
   sprawdzenie zasięgu wykonuje się PRZED walidacją parametrów paginacji, nie polegać na
   domyślnej kolejności walidacji frameworka (Pydantic/FastAPI waliduje query params przed
   wejściem do handlera z definicji) — to wymaga świadomego kroku implementacyjnego (np.
   walidacja ręczna w handlerze po sprawdzeniu zasięgu, zamiast `Query(...)` z ograniczeniami
   wbudowanymi w sygnaturę), nie jest domyślnym zachowaniem, które "po prostu działa".

## Konsekwencje

- Każdy przyszły endpoint listujący z ryzykiem wolumenu (>~50 wierszy w typowym przypadku)
  konsultuje ten ADR zamiast wynajdywać kształt od zera.
- `docs/architecture/architecture-sensitive-paths.md`, wiersz `backend/app/api/**` i "any
  new/changed public API endpoint" — odtąd cytują też `ADR-0017` jako governing decision dla
  zmian wprowadzających/zmieniających paginację.
- SC-1-05 (`GET /projects`, odłożone 2026-09-18) ma teraz punkt odniesienia, gdy zostanie
  podjęte — nie jest tym ADR-em automatycznie zamykane.

## Rozważane alternatywy

- **Zero ADR ogólnego, punktowa decyzja per zadanie** — odrzucone: trzecia niezależna runda
  przeglądu tego samego kształtu usterek (patrz `/catalog/rates` R-01/R-02/R-03 przy SC-2-03),
  SC-1-05 pozostaje bez punktu odniesienia.
- **Aneks do ADR-0005** — odrzucone: ADR-0005 reguluje *kto widzi*, paginacja reguluje *ile na
  raz* — inny wymiar decyzji.
- **Aneks do ADR-0009** — odrzucone: wzmianka o stronicowaniu tam jest przesłanką do wniosku o
  walidacji klienta (check-then-act), nie decyzją o kształcie stronicowania samej w sobie.
- **Aneks do ADR-0008** — odrzucone: ten ADR reguluje egzekwowanie przedziałów obowiązywania
  przez `EXCLUDE`/`btree_gist`; zasoby bez takiego ograniczenia (jak `staffing_position`) nie są
  nim objęte — zaczepienie ogólnego wzorca paginacji tam byłoby doczepieniem do niepowiązanego
  mechanizmu.
- **Kursor zamiast offset/limit** — odrzucone dla tego ADR: brak precedensu w repo, więcej pracy
  bez zmierzonej potrzeby (page drift już zaakceptowany gdzie indziej przez analogię); może
  zostać zrewidowane w przyszłym, datowanym aneksie, jeśli konkretne zadanie zmierzy realny
  problem ze współbieżnym wstawieniem.

## Powiązane wymagania

NF-03 (200 pozycji × 36 miesięcy — skala odniesienia dla pierwszego konsumenta, SC-3-05).
