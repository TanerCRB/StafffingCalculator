# ADR-0011 — Zasoby graficzne frontendu: ikony i referencje wizualne

**Status:** Accepted

> Dokument powstał w odpowiedzi na pytania bramki 1 architekta (Q2, Q3) dla SC-2-05 (Issue #59).
> Rozstrzygnięcie zapadło na bramce 1 (2026-09-23): ikony przez `@tabler/icons-react`, bez
> literału koloru w żadnym pliku graficznym ani atrybucie `fill`/`stroke`; referencje wizualne
> (`Wymagania/`) trafiają do repozytorium w całości jako dokumentacja, nie jako specyfikacja.

## Kontekst

Do SC-2-05 `frontend/src` nie zawierał ani jednego pliku graficznego (`.svg`, `.png` jako ikona) i
ani jednego importu takiego zasobu. Jedyny wizualny akcent — trójbelkowy „brandmark" w
`AppShell.tsx` — jest rysowany w CSS, nie plikiem. Strażnik jednego źródła koloru
(`frontend/src/styles/tokens.test.ts`) skanuje wyłącznie `**/*.css` (literały koloru) i
`style={{` w `**/*.tsx` (inline styl) — nie widzi ani pliku `.svg`, ani atrybutu `fill=`/`stroke=`
w JSX. Dopóki żaden taki plik nie istniał, luka w zasięgu strażnika była teoretyczna.

SC-2-05 wprowadza pierwsze ikony (przyciski „Add…", `Edit`/`Rename` w restylu ekranu katalogu) i
pierwsze pliki referencyjne spoza kodu produkcyjnego trafiające do repozytorium
(`Wymagania/prototyp/`, `Wymagania/UI/`, wcześniej nieśledzone w git).

## Decyzja

1. **Ikony przez `@tabler/icons-react`, nie przez ręcznie pisane `.svg`.** Biblioteka MIT,
   kompatybilna z React ≥16 (peer dependency), komponenty generowane z danych ścieżki
   (`createReactComponent`), domyślnie `stroke="currentColor"` `fill="none"` — kolor ikony
   pochodzi zawsze z CSS `color`, nigdy z atrybutu w źródle komponentu. Zweryfikowane w
   zainstalowanym pakiecie (`node_modules/@tabler/icons-react/dist/esm/icons/IconPlus.mjs`), nie
   z dokumentacji.

2. **Żaden plik pod `frontend/src/` nie niesie koloru literałem poza `tokens.css`.** Dotyczy to
   teraz też plików `.svg` (gdyby jakiś trafił do repo poza biblioteką) i atrybutów
   `fill="#…"`/`stroke="#…"` w `.tsx`. Import ikony z `@tabler/icons-react` bez nadpisania
   `color`/`stroke` spełnia to z definicji (pkt 1). Ręczne nadpisanie koloru ikony idzie przez
   token (`style={{ color: "var(--sc-color-…)" }}` albo klasę CSS), nigdy przez hex.

3. **Ikona nigdy nie zastępuje nazwanego stanu tekstem.** Ikona obok etykiety (np. plus w
   przycisku „Add role") jest dekoracją — `aria-hidden="true"`, bez własnego tekstu w drzewie
   dostępności. Stan komunikowany dziś słowem („Restricted", „Internal") zostaje słowem; ikona nie
   staje się jedynym nośnikiem znaczenia (NF-08, ADR-0010 pkt 5).

4. **Referencje wizualne w `Wymagania/` są dokumentacją, nie specyfikacją.** Commitowane w
   całości (bez archiwów `.zip`) jako materiał referencyjny — PNG i HTML pokazują wygląd, nie są
   źródłem reguł finansowych ani kontraktów API (`Wymagania/prototyp/spec/IMPLEMENTATION_HANDOFF.md`
   §1, już przyjęte tym samym zapisem dla `Wymagania/UI/` przy SC-1-06). Nie wymagają własnego
   mechanizmu publikacji ani walidatora — repo dziś żadnego nie ma dla żadnej dokumentacji.
   `Wymagania/prototyp/` i `Wymagania/UI/Proposal/` są tym samym pakietem zduplikowanym w dwóch
   miejscach (zweryfikowane `diff -rq`, brak różnic) — obie kopie zostają, żadna nie jest kasowana
   tym zadaniem.

## Konsekwencje

- Strażnik tokenów (`tokens.test.ts`) **został rozszerzony** w implementacji SC-2-05 o skan
  literału koloru w `.svg` i w atrybutach `fill=`/`stroke=`/`color=` w `.tsx` (K-29, weryfikacja
  invariant-guardiana 2026-09-23: mutacja `fill="#0066ff"` na ikonie daje czerwony wynik). Dług
  częściowo pozostaje: kolor przekazany przez nazwaną stałą (`const X = "#…"; stroke={X}`) omija
  skan, bo ten czyta tylko literał wprost w atrybucie — nazwane jako otwarte, szczątkowe ryzyko
  (invariant-guardian S-02), nie regresja względem stanu sprzed tej rundy.
- `package.json`/`pnpm-lock.yaml` zyskują pierwszą zależność biblioteczną frontendu poza
  React/Vite/Vitest — świadome odstępstwo od domyślnej ostrożności wobec nowych zależności,
  zaakceptowane wprost na bramce 1 (SC-2-05), nie milczące.
- `Wymagania/README.md:15` już dziś nazywa `UI/Proposal` pełną kopią `prototyp/` „żeby galeria była
  dostępna obok materiałów UI w projekcie" — duplikacja jest zamierzona przez autora pakietu, nie
  przypadkowa.

## Rozważane alternatywy

- **Ikony jako ręcznie pisane `.svg` w `src/assets/`** — odrzucone: wymaga natychmiastowego
  rozszerzenia strażnika tokenów (inaczej cichy wyciek koloru poza `tokens.css`), zero korzyści
  nad gotową biblioteką dla standardowego zestawu (plus, ołówek, itd.).
- **Ikony z wbudowanym kolorem marki** (np. eksport z Figmy z `fill` na sztywno) — odrzucone:
  łamie „jedno źródło koloru" bez żadnego testu, który by to zauważył.
- **Bez ikon w tej Story** — odrzucone na bramce 1: człowiek zdecydował się na `@tabler/icons-react`
  wprost, żeby domknąć restyle w całości, nie częściowo.
- **Nie commitować `Wymagania/`, cytować ścieżkę jak dotąd** — odrzucone: repo miało już wiszące
  odwołanie tej klasy (`tokens.css:9` do `Wymagania/UI/globallogic_style_guide-v3.docx`,
  nieśledzonego), commit usuwa je zamiast je powielać.

## Powiązane wymagania

NF-08 (stan nie tylko kolorem/ikoną), ADR-0010 pkt 5 (nazwany stan, nie sam kolor jako nośnik),
`tokens.css` nagłówek („ONE source of truth for colour"), precedens SC-1-06 (`Wymagania/UI/` jako
referencja, nie specyfikacja, bez testów regresji wizualnej).
