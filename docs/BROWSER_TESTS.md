# Automatyczne testy przeglądarkowe karty

## Uruchomienie

Wymagane: Node.js 24 i pnpm 11.25.0. W katalogu repozytorium:

```sh
pnpm install --frozen-lockfile --ignore-scripts
pnpm exec playwright install chromium
pnpm test:browser
```

Na linuksowym CI instalacja bibliotek systemowych odbywa się poleceniem
`pnpm exec playwright install --with-deps chromium`.
Tryb interaktywny lokalnie: `pnpm test:browser:ui`.
Pojedynczy projekt: `pnpm test:browser --project chromium-phone`.

## Co jest sprawdzane

Playwright uruchamia prawdziwy Chromium w dwóch konfiguracjach:
komputer 1280×900 i telefon 390×844. Serwer testowy udostępnia bezpośrednio
produkcyjny `custom_components/medi_assistant/www/medi-assistant-card.js`.
Każdy test ma odrębną sesję z fikcyjnymi kontami, wizytami i szukajkami.

- Lista wielu kont i szukajek, brak wizyt i szukajek, częściowo niedostępne dane.
- Potwierdzenie konkretnego terminu i konta oraz powrót bez zapisu.
- Rezerwowanie i odwoływanie przy podwójnym kliknięciu; weryfikacja liczby
  prób zapisu i identyfikatora przekazanego do usługi.
- Wygasły termin, nieznany wynik zapisu i ponowne pobranie obu list bez
  automatycznego ponawiania operacji.
- Zgoda na późne odwołanie, także gdy wizyta stała się późna po pobraniu listy.
- Ręczne odświeżanie, zachowanie zwiniętej szukajki i odzyskanie po błędzie.
- Skrót Dodaj szukajkę, adres docelowy konfiguracji i ukrycie skrótu
  użytkownikowi bez uprawnień administratora.
- Informacja o ograniczeniu do 20 wyników, brak poziomego przewijania
  oraz renderowanie wartości HTML jako bezpiecznego tekstu.

## Granice i izolacja

To testy warstwy karty i jej kontraktu usług. Lokalny serwer zastępuje usługi HA
kontrolowanymi odpowiedziami, w tym odpowiedzią utraconą po syntetycznym zapisie.
Natywny formularz dodawania szukajki jest poza tym zestawem; test skrótu
weryfikuje przejście pod właściwy adres HA. Testy backendu i config flow są w pytest.

Serwer wiąże się wyłącznie z `127.0.0.1:4178`, a przeglądarka blokuje żądania
poza jego origin. Zajęty port powoduje błąd zamiast użycia istniejącego serwera.
Port można zmienić przez `MEDI_BROWSER_TEST_PORT`; host zawsze pozostaje lokalny.
Testy nie odczytują konfiguracji `.local-ha`, nie korzystają z prawdziwego API
Medicover i nie uruchamiają ani nie zmieniają lokalnej instancji użytkownika.

## Pliki i CI

- `playwright.config.mjs` — projekty, start/stop serwera i izolacja wykonania.
- `tests/browser/server.mjs` — fikcyjne usługi i stan sesji.
- `tests/browser/fixture.html` — host rzeczywistej karty z interfejsem `hass.callWS`.
- `tests/browser/card.spec.mjs` — powtarzalne scenariusze Playwright.
- Job **Chromium card tests (desktop and phone)** uruchamia się dla PR i `main`.

Przy błędzie powstają zrzut ekranu i trace w ignorowanym `test-results/`.
CI zachowuje raport oraz artefakty błędów przez 7 dni; zawierają wyłącznie
dane syntetyczne. Testy DOM `pnpm test:frontend` pozostają szybką kontrolą.
