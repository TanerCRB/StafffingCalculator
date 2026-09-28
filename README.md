# StafffingCalculator

Planer obsady i rentowności projektów IT. Aplikacja webowa dla Project Managerów w firmie
outsourcingowej: pozwala przygotować plan obsady, oszacować koszty i przychód projektu, ocenić
rentowność i porównać finansowy skutek alternatywnych wariantów realizacji.

Projekt jest jednocześnie **przykładem użycia frameworka [Ninefold](https://github.com/TanerCRB/NineFold)**
— procesu SDLC dla wytwarzania oprogramowania prowadzonego przez agentów AI (Spec-Driven
Development + Human-in-the-Loop). Cały kod w tym repozytorium powstał w tym procesie: każde
zadanie przeszło od Issue przez kryteria akceptacji, mapę wpływu na architekturę, implementację,
testy mutacyjne i niezależne przeglądy, z trzema bramkami, których agent nigdy nie przekracza.

## Co robi aplikacja

Wymagania: [`Wymagania/Requirements_EN.md`](Wymagania/Requirements_EN.md). Najważniejsze obszary:

- **Projekty i scenariusze** — wiele niezależnych scenariuszy kalkulacji na projekt, szkice,
  kopiowanie, archiwizacja, wykrywanie brakujących danych (scenariusz niekompletny nigdy nie jest
  prezentowany jako gotowy do zatwierdzenia).
- **Konfigurowalne założenia** — łańcuch nadpisań organizacja → projekt → scenariusz, ze
  wskazaniem źródła każdej wartości; kalendarze robocze, budżety urlopowe, fazy dostawy.
- **Katalog ról i stawek** — rola, senioritet, lokalizacja, typ zaangażowania, stawki kosztowe i
  sprzedażowe z przedziałami obowiązywania, stawki poddostawców, rejestr osób nazwanych.
- **Plan obsady** — pozycje obsady z miesięczną alokacją (godziny/FTE), role anonimowe lub osoby.
- **Modele komercyjne** — Time & Material, Fixed Price, Outcome-based, Story Points.
- **Koszty** — koszt osobowy (stawka bazowa, narzuty, kwota stała), koszt nieobecności płatnych,
  koszty dodatkowe.
- **Wyniki** — zysk, marża, markup; porównanie scenariuszy; analiza what-if (np. podwyżka
  wynagrodzeń) bez zapisu.
- **Kontrola dostępu** — widoczność projektów per przypisanie, osobna bramka dla pól kosztów
  osobowych, historia zmian przy zatwierdzeniu scenariusza.

Postęp prac: [`docs/PLAN.md`](docs/PLAN.md) (rejestr zadań) oraz
[`docs/architecture/capabilities.md`](docs/architecture/capabilities.md) — rejestr tego, co jest
**udowodnione testem**, oddzielony od tego, co jest tylko zdecydowane. Decyzje architektoniczne:
[`docs/architecture/decisions/`](docs/architecture/decisions/).

> **Stan:** projekt w fazie rozwoju, bez środowiska produkcyjnego. Tożsamość wołającego to nadal
> placeholder w nagłówku żądania, nie uwierzytelnianie (ADR-0005) — aplikacja odmawia startu poza
> `development`/`test` bez jawnego opt-in.

## Stos technologiczny

| Warstwa | Technologie |
|---|---|
| Backend (`backend/`) | Python 3.12+, FastAPI, Pydantic, SQLAlchemy 2.x, Alembic, PostgreSQL |
| Frontend (`frontend/`) | React 18, TypeScript, Vite, pnpm, Vitest |
| Testy | pytest + testcontainers (prawdziwy PostgreSQL w Dockerze), Vitest + Testing Library |

Kwoty pieniężne to zawsze `Decimal`, zaokrąglane wyłącznie przez `backend/app/core/money.py` /
`frontend/src/lib/money.ts` (ADR-0002). Na granicy API liczby dziesiętne są przesyłane jako
stringi, nigdy jako JSON float.

## Uruchomienie

Wymagane: Python 3.12+, Node.js z pnpm, PostgreSQL, Docker (dla testów backendu).

```bash
# Backend
cd backend
python -m venv .venv
.venv/Scripts/activate            # Windows; source .venv/bin/activate na Linux/macOS
pip install -e ".[dev]"
cp .env.example .env              # ustaw APP_DATABASE_URL
alembic upgrade head
uvicorn app.main:app --reload

# Frontend
cd frontend
pnpm install
cp .env.example .env
pnpm dev
```

Testy i lint:

```bash
( cd backend && pytest && ruff check . )
( cd frontend && pnpm test && pnpm lint && pnpm build )
```

Szczegóły: [`backend/README.md`](backend/README.md), [`frontend/README.md`](frontend/README.md).

## Proces: Ninefold

[Ninefold](https://github.com/TanerCRB/NineFold) to przenośny zestaw startowy procesu SDLC dla
agentów AI. Nazwa pochodzi od dziewięciu aktorów: ośmiu wyspecjalizowanych ról agentowych plus
człowiek. Zasada nośna: **rola, która coś wytwarza, nigdy tego nie ocenia**, a każde przekazanie
pracy między rolami to nowa para oczu.

To repozytorium jest instancją Ninefold dla monorepo. Z frameworka przeniesiono i zaadaptowano:

- **Role** ([`agents/`](agents/)) — dziewięć definicji (backend i frontend jako osobni developerzy):
  - wytwarzające: `product-owner`, `analyst`, `developer-backend`, `developer-frontend`, `qa`,
  - oceniające: `invariant-guardian`, `architect`, `reviewer`, `security-auditor`.

  Synchronizowane do `.claude/agents/` przez `node tools/sync-agents.mjs`.
- **Kontrakt zespołu** ([`TEAM-CONTRACT.md`](TEAM-CONTRACT.md)) — kto co robi, bramki, twarde
  stopy; ma pierwszeństwo przed plikami ról.
- **Maszyna stanów i etykiety** ([`process/sdlc-flow.md`](process/sdlc-flow.md),
  [`process/labels.json`](process/labels.json)), szablony Issue/PR, hook pre-push.
- **Komendy** — `/task #N` prowadzi jedno zadanie od Issue do PR, `/task-status #N` to raport
  tylko do odczytu (`.claude/commands/`).
- **Kalibracja** ([`calibration/`](calibration/)) — jak sprawdzić, że rola oceniająca faktycznie
  ocenia, zanim zacznie się jej ufać.

### Trzy bramki człowieka

1. **Zakres i architektura** — przed napisaniem kodu.
2. **Merge do `main`** — PR z kodem (`Refs #N`) przenosi Issue do `state:evidence`, nie zamyka go.
3. **Merge PR dokumentacyjnego** (`Closes #N`) — podnosi status w `docs/PLAN.md` i rejestrze
   capabilities.

Etykieta `waiting-on-human` oznacza wszystkie trzy — `is:open label:waiting-on-human` pokazuje
wszystko, co czeka na człowieka.

### Dowód zamiast deklaracji

Kryterium akceptacji ma obserwowalny nośnik, przeciwieństwo i nazwaną mutację, która musi je
zabić. QA usuwa mechanizm i zapisuje, czy test rzeczywiście upadł. Wpis w
[`capabilities.md`](docs/architecture/capabilities.md) wskazuje konkretny test i rodzaj dowodu
(`mutation-checked test`, `test, no mutation`, `no evidence`).

Uzasadnienie całego podejścia (dlaczego tak, a nie inaczej): [`FrameworkDoc.md`](FrameworkDoc.md)
i [`process/`](process/), a w wersji aktualnej — repozytorium
[TanerCRB/NineFold](https://github.com/TanerCRB/NineFold).

## Konwencje

- Język polski: commity, opisy PR/Issue, komentarze, dokumentacja.
- Język angielski: identyfikatory, komunikaty błędów i logów, powierzchnia API.
- Zadanie piszące kod pracuje we własnym `git worktree`, nie w głównym checkoucie.
- Istniejący test, który zaczyna padać po zmianie, nigdy nie jest osłabiany ani usuwany.

Pełne zasady: [`TEAM-CONTRACT.md`](TEAM-CONTRACT.md), [`CLAUDE.md`](CLAUDE.md).

## Licencja

Copyright © 2026 Mariusz Miziołek.

StafffingCalculator jest udostępniony na licencji
**[PolyForm Noncommercial 1.0.0](https://polyformproject.org/licenses/noncommercial/1.0.0)** —
pełny tekst w pliku [`LICENSE`](LICENSE).

- **Użycie niekomercyjne jest bezpłatne** — nauka, badania, projekty osobiste i hobbystyczne,
  organizacje non-profit, instytucje edukacyjne i publiczne, zgodnie z warunkami licencji.
- **Użycie komercyjne wymaga uprzedniej, pisemnej zgody autora** (osobnej licencji komercyjnej).
  Dotyczy to m.in. użycia w firmie, w usługach świadczonych klientom oraz w produktach
  sprzedawanych lub udostępnianych odpłatnie. W sprawie licencji komercyjnej skontaktuj się z
  autorem przez [GitHub](https://github.com/TanerCRB).

Przy dalszym udostępnianiu należy zachować tekst licencji oraz linie `Required Notice:` z pliku
`LICENSE`.

**Pliki pochodzące z Ninefold.** Framework [Ninefold](https://github.com/TanerCRB/NineFold) jest
udostępniony na licencji Apache 2.0. Pliki na nim oparte (`FrameworkDoc.md`, `agents/`,
`process/`, `tools/`, `calibration/`) wywodzą się z tego projektu; jeśli chcesz użyć samego
procesu — także komercyjnie — skorzystaj z oryginalnego repozytorium Ninefold na jego licencji.
