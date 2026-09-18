# ADR-0003 — Model danych dla modeli komercyjnych

**Status:** Draft — pending approval

## Kontekst

F-06 wymaga czterech modeli komercyjnych (T&M, Fixed Price, Outcome-based, Story Points), każdy
z innym zestawem pól konfiguracyjnych i inną formułą przychodu. F-06 wprost: "A model may apply
to an entire project, a delivery phase, or a workstream, allowing mixed commercial arrangements."
F-06.5: żaden zakres pracy nie może być rozliczony podwójnie, chyba że jawna reguła łączona tego
wymaga. Sekcja 2 dokumentu zaznacza niepotwierdzone założenie: model Story Points może oznaczać
płatność za punkt lub stałą opłatę za sprint z zobowiązaniem punktowym — obie interpretacje muszą
dać się wyrazić bez zmiany schematu, bo decyzja biznesowa (open decision #2) jeszcze nie zapadła.

## Decyzja

Jedna tabela `commercial_terms` (reguła komercyjna) z kolumną dyskryminującą `model_type`
(`time_and_material` | `fixed_price` | `outcome_based` | `story_points`) i wspólnymi kolumnami
(`scope_ref` — projekt/faza/workstream, `currency`, `effective_from`/`effective_to`), plus cztery
tabele szczegółów w relacji 1:1 z `commercial_terms` (`tm_terms`, `fixed_price_terms`,
`outcome_terms`, `story_points_terms`) — każda niesie tylko pola właściwe swojemu modelowi.
Wyliczanie przychodu przez jedną funkcję dyspozycyjną per `model_type`, każda zwraca ten sam
kształt wyniku (`revenue`, `assumptions_used`) — nigdy nie jest zgadywana z kształtu danych.

Reguła przeciw podwójnemu rozliczeniu: unikalne ograniczenie na (`scope_ref`, `effective_from`,
`effective_to`) z wykluczeniem nakładania się zakresów dat w obrębie tego samego `scope_ref`,
chyba że istnieje jawny wiersz `combined_pricing_rule_ref` łączący dwie reguły — wymuszane przez
ograniczenie w bazie (`EXCLUDE` z `btree_gist`), nie tylko walidację aplikacji.

## Konsekwencje

- Dodanie piątego modelu komercyjnego w przyszłości to nowa tabela szczegółów + nowa gałąź w
  dyspozytorze, bez zmiany istniejących tabel — zgodne z regułą wstecznej kompatybilności migracji.
- F-06.4 (Story Points): schemat `story_points_terms` przechowuje zarówno `price_per_point`, jak
  i opcjonalne pole `sprint_fixed_fee` — dopóki open decision #2 nie zapadnie, oba pola istnieją,
  a reguła wyliczania przychodu jawnie zwraca w `assumptions_used`, który wariant zastosowała.
- F-06.5 "Results shall expose the assumptions on which they depend" — spełnione przez
  `assumptions_used` w każdym wyniku, nie przez osobny log.

## Rozważane alternatywy

- **Jedna szeroka tabela ze wszystkimi kolumnami wszystkich modeli** — odrzucone: większość
  kolumn byłaby `NULL` dla danego wiersza, reguła Strażnika Niezmienników nr 6 (klasyfikacja z
  więcej niż jednego miejsca) sugeruje unikać rozproszonych warunków `WHERE model_type = X AND
  col_y IS NOT NULL`.
- **Dokumentowy JSON per model_type w jednej kolumnie** — odrzucone: NF-01 (arytmetyka
  dziesiętna) i ograniczenia integralności (np. min/max compensation w F-06.3) trudno wymusić
  na poziomie bazy w polu JSON; SQL `CHECK`/`EXCLUDE` działają tylko na kolumnach.

## Powiązane wymagania

F-06 (całość, w tym F-06.1–F-06.5), sekcja 2 (niepotwierdzone założenie Story Points), open
decision #2, AC-01, AC-07, AC-08, AC-09
