# Propozycja UI — Staffing planner

**Data:** 19.09.2026. **Status:** propozycja do przeglądu przez człowieka, nie zatwierdzona specyfikacja ani wdrożenie produktu.

## Od czego zacząć

1. Otwórz [galerię ekranów](gallery.html) — wszystkie grafiki i odnośniki do odpowiadających im widoków.
2. Otwórz [interaktywny prototyp](index.html) — działa lokalnie bez instalacji i bez backendu.
3. Przeczytaj [specyfikację UI](spec/UI_SPEC.md) oraz [pakiet wdrożeniowy SDD / HIL](spec/IMPLEMENTATION_HANDOFF.md).

Interfejs jest po angielsku, zgodnie z istniejącymi ekranami i wymaganiami. Dokumentacja pakietu jest po polsku, zgodnie z kontraktem repozytorium.

## Zawartość

| Artefakt | Przeznaczenie |
|---|---|
| `index.html`, `design.css`, `prototype.js` | Samodzielny prototyp 24 widoków i wzorców interakcji |
| `gallery.html` | Przegląd wszystkich makiet z przejściem do prototypu |
| `screens/01-…24-….png` | Pełne ekrany desktopowe, szerokość 1440 px |
| `screens/15-catalog-*.png` | UI-15 oraz powiększenia paneli Roles, Seniorities, Locations i Engagement types |
| `tools/render-ui15.cjs` | Eksport UI-15 i czterech grafik paneli słowników |
| `screens/mobile-*.png` | Trzy warianty mobilne, szerokość 390 px |
| `design-overview.png` | Plansza prezentująca najważniejsze widoki |
| `navigation.svg` | Edytowalna mapa architektury informacji |
| `assets/favicon.svg`, `assets/favicon-32.png`, `assets/apple-touch-icon.png` | Ikona karty przeglądarki i skrótu mobilnego |
| `spec/UI_SPEC.md` | Ekrany, zachowania, walidacja, stany, zasady prezentacji danych |
| `spec/IMPLEMENTATION_HANDOFF.md` | Powiązania z wymaganiami, propozycje zadań i bramki człowieka |
| `spec/DESIGN_DECISIONS.md` | Źródła, uzasadnienie projektu, status decyzji i pytania otwarte |
| `spec/demo-data.json` | Jeden spójny zestaw przykładowych danych dla makiet |
| `spec/screen-manifest.json` | Identyfikatory ekranów, trasy prototypu i pliki graficzne |
| `spec/verification.json` | Wynik sprawdzenia prototypu; nie jest dowodem działania produktu |
| `assets/brand-tokens.css` | Kopia tokenów istniejącego frontendu użyta do przygotowania makiet |
| `assets/fonts.css`, `Manrope-*.ttf`, `Manrope-OFL.txt` | Lokalna czcionka Manrope i licencja |

## Co można sprawdzić w prototypie

- Nawigację, wybór ekranu i otwieranie szczegółów pozycji oraz kosztu.
- Wyszukiwanie projektów, filtr Active / Archived i wybór projektu.
- Przełączanie FTE / Hours oraz okno edycji alokacji.
- Cztery odrębne formularze modeli komercyjnych.
- Zmianę symulowanej marży i zysku suwakami w analizie wrażliwości.
- Bramkę potwierdzenia przed podglądem zatwierdzonej wersji.
- Dodanie nazwy projektu w pamięci bieżącej sesji demonstracyjnej.
- Wydruk raportu do PDF przy użyciu przeglądarki i pobranie przykładowych danych CSV.

**Granica demonstracji:** formularze stawek, kosztów i założeń pokazują układ oraz przegląd zmian. Nie zapisują danych do aplikacji i nie stanowią kompletnego silnika kalkulacyjnego. Dane referencyjne są stałe. Odświeżenie resetuje stan demonstracji. Eksport CSV jest demonstracyjny; docelowy eksport XLSX/PDF wymaga implementacji.

Wybór alternatywnego scenariusza prowadzi do porównania, zamiast udawać edycję niezależnego scenariusza. Inne przykładowe projekty ilustrują listę; szczegółowy przebieg dotyczy `Commerce platform`.

UI-15 odzwierciedla aktualną implementację katalogu: jeden ekran z tabelą stawek i pięcioma panelami słowników (wraz z Vendors). Cztery dodatkowe grafiki to ujęcia szczegółowe komponentów znajdujących się na tej stronie, a nie osobne strony lub zakładki.

## Kierunek wizualny

Biały obszar pracy, typografia Manrope, jasnostalowe nagłówki tabel, niebieskie sterowanie i pomarańczowy akcent na bieli. Główną powierzchnią roboczą jest siatka obsady. Wyniki finansowe pozostają blisko założeń, ale nie mieszają się z polami alokacji.

Zrzuty są wyrenderowane z tego samego prototypu, więc napisy i układ odpowiadają plikom źródłowym. Grafiki nie są obrazami wygenerowanymi probabilistycznie. Znak słupkowy jest roboczym symbolem narzędzia, nie logotypem GlobalLogic.

## Ważne decyzje do podjęcia

- Story Points: cena za zaakceptowany punkt czy stała cena sprintu? Pokazany jest pierwszy wariant.
- Planowanie wyłącznie miesięczne czy także tygodniowe / sprintowe?
- Czy potrzebne jest plan–wykonanie? Obecna propozycja dotyczy planowania.
- Zakres ujawniania agregatów osobom bez dostępu do indywidualnych kosztów.
- Docelowe uwierzytelnianie i sposób nadawania dostępu.
- Akceptacja architektury modeli komercyjnych i przedziałów dat: ADR-0003 i ADR-0008 mają obecnie status Draft.

Otwartych decyzji nie traktować jako milcząco przyjętych. Pełny rejestr jest w dokumentacji pakietu.

## Odtworzenie grafik

`tools/render.cjs` renderuje lokalny HTML w Chromium, generuje manifest i sprawdza podstawowe interakcje. Wymaga Node.js oraz Playwright z Chromium. Ścieżkę modułu można wskazać przez `PLAYWRIGHT_MODULE`. `tools/build-gallery.py` tworzy galerię i mapę nawigacji, a `tools/render-gallery.cjs` eksportuje planszę przeglądową.

Nie zmieniono kodu produkcyjnego, rejestru PLAN ani rejestru capabilities. Ten pakiet jest materiałem wejściowym do SDD i przeglądu HIL. Nie wykonano commitów, publikacji ani wdrożenia.
