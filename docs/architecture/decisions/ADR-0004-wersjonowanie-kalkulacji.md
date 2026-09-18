# ADR-0004 — Wersjonowanie i niemutowalność zatwierdzonych kalkulacji

**Status:** Draft — pending approval

## Kontekst

F-12: "Approved versions shall be immutable; further changes shall require a new version or
copy." "Reports shall be reproducible using saved inputs, exchange rates, calendars, and
calculation rule versions." AC-04: zmiana domyślnej stawki organizacji nie może zmienić wyniku
już zatwierdzonej kalkulacji. AC-10: regeneracja raportu dla zatwierdzonej wersji po zmianie
domyślnych ustawień musi dać ten sam wynik co oryginał. F-02: "Changes to default settings shall
not automatically modify saved calculations."

## Decyzja

Kalkulacja (scenariusz w konkretnym stanie) ma status `draft` lub `approved`. Zatwierdzenie
(`approved`) jest operacją jednokierunkową wykonywaną przez człowieka (rola: calculation author
lub administrator, F-13) i tworzy **migawkę** (snapshot) — kopiuje w momencie zatwierdzenia
wszystkie wartości, które AC-04/AC-10 wymagają jako odtwarzalne: rozwiązane stawki, kursy walut,
kalendarze robocze, wersję reguł komercyjnych. Migawka jest osobnym zestawem wierszy (nie
referencją do "aktualnych" wartości organizacji) — zapis do tabel `approved_snapshot_*`,
odrzucany na poziomie warstwy dostępu do danych przy próbie modyfikacji (reguła Strażnika
Niezmienników: zapis do zatwierdzonej kalkulacji odrzucany w warstwie dostępu do danych, nie
tylko w UI). Dalsze zmiany wymagają nowej wersji (kopii) scenariusza ze statusem `draft`.

## Konsekwencje

- Raport wygenerowany dla wersji `approved` czyta wyłącznie z migawki, nigdy z żywych tabel
  stawek/kursów/kalendarzy — spełnia AC-10 z definicji, nie przez dodatkową logikę porównawczą.
- Historia zmian (F-12, "change history identifying the author, time, and affected data")
  wymaga osobnej tabeli `audit_log` niezależnej od migawek — migawka mówi *co* było zatwierdzone,
  audit log mówi *kto i kiedy* to zmienił.
- Duplikowanie scenariusza (F-09, AC-02) i tworzenie nowej wersji po zatwierdzeniu (F-12) to ten
  sam mechanizm kopiowania na poziomie danych — jedna funkcja, dwa punkty wejścia.

## Rozważane alternatywy

- **Event sourcing (log zdarzeń, stan odtwarzany przez replay)** — odrzucone na start: NF-03
  (95% przeliczeń < 2s dla 200 pozycji × 36 miesięcy) byłoby trudniejsze do spełnienia z pełnym
  replay przy każdym odczycie zatwierdzonej wersji; migawka daje odczyt O(1) bez replay.
- **Wersjonowanie przez "soft delete" + kolumnę `version` w tej samej tabeli** — odrzucone:
  łatwo o przypadkowy `UPDATE` trafiający też w zatwierdzony wiersz; osobny zestaw tabel
  migawkowych daje twardą granicę egzekwowaną przez uprawnienia bazy, nie tylko konwencję kodu.

## Powiązane wymagania

F-12, F-02 (brak retroaktywnej zmiany), F-09 (duplikacja scenariusza), AC-02, AC-04, AC-10
