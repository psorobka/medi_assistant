# Medi Assistant — praca i wydania

- Odpowiadaj po polsku. Kontekst modułów i aktualne komendy kontroli są w `docs/AI_CONTEXT.md`.
- Sprawdź Git i zachowaj zastane zmiany użytkownika. Nie dodawaj danych pacjentów, tokenów, haseł ani lokalnej konfiguracji HA do logów, testów i commitów.
- Zmiany implementacyjne wykonuj na osobnym branchu `codex/<krótki-opis>`. Nie commituj ani nie wypychaj bezpośrednio na `main`.

## Procedura PR i release

Procedura odpowiada instrukcjom w repozytoriach Alerts Assistant, REST Assistant i Lists Assistant.

1. Przygotuj zmianę i adekwatne testy lokalne. Dla backendu wykonaj pełny pytest z progiem coverage 90% i Ruff; dla karty testy Node oraz test w przeglądarce odpowiedni do zakresu. Testy automatyczne nie mogą używać prawdziwego API Medicover. Rzeczywiste rezerwacje i odwołania wymagają wyraźnego polecenia użytkownika.
2. Przed merge przygotuj numer wersji zgodny z Semantic Versioning oraz `CHANGELOG.md`. Wersje w `custom_components/medi_assistant/manifest.json`, `pyproject.toml` i adresie modułu karty muszą być zgodne. Dokumentacja i testy bez zmian produktu nie wymagają bumpa.
3. Do operacji GitHub (PR, kontrole CI, merge, tagi i wydania) używaj `gh`. Na Windows można użyć zainstalowanego `gh` w WSL.
4. Otwórz PR do `main`, opisz końcowe zachowanie i wykonane kontrole. Dołącz utworzony PR do bieżącego zadania w Codex.
5. Nie scalaj PR, dopóki wymagane kontrole CI dla aktualnego commita nie przejdą. Napraw nieudane kontrole i poczekaj na nowy zielony wynik; nie omijaj kontroli ani ochrony gałęzi.
6. Merge i publikację wykonuj w zakresie autoryzowanym przez użytkownika. Zlecone wydanie obejmuje doprowadzenie procesu do GitHub Release, nie tylko merge.
7. Po zielonym CI scal PR zgodnie z ustawieniami repozytorium. Utwórz tag `vMAJOR.MINOR.PATCH` i GitHub Release wskazujące konkretny scalony commit. Nie wydawaj kodu z niescalonej gałęzi.
8. Sprawdź tag, numer wersji i opublikowany release. Podsumuj zmiany, wyniki kontroli, odnośniki do PR i wydania oraz istotne ograniczenia. Nie deklaruj wykonania niewykonanych kontroli.
