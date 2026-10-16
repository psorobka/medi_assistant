# Aktywne repozytorium

Kod źródłowy: `custom_components/medi_assistant/`. Domena: `medi_assistant`.
Wydanie: 1.3.1. Wersja manifestu, pyproject i cache modułu karty jest kontrolowana przez tests/test_release.py. Procedura PR → zielone CI → merge → tag/release jest w AGENTS.md; historia wydań w CHANGELOG.md.
Testy przeglądarkowe: `playwright.config.mjs`, `tests/browser/`, opis w `docs/BROWSER_TESTS.md`; `pnpm test:browser`. Kod produktu ma wersję 1.3.1; zmiany samych testów nie wymagają wydania.

Kopia `C:/Users/Piotr/Claude/Projects/medi_assistant` z domeną `medicover`
to odrębny, stary prototyp. Nie montuj jej w lokalnym HA ani nie przenoś
z niej całych modułów auth/config flow. Właściwe repo ma własne logowanie,
podtrzymanie sesji, edytowalne szukajki i opcjonalne powiadomienia.

## Rezerwacja

- `booking.py`: losowe uchwyty terminu per konto i szukajka, 30 minut,
  odświeżenie dokładnego terminu, blokada współbieżnych zapisów i ponowień
  po nieznanym wyniku. Uchwyty nie przeżywają restartu/reloadu.
- `api.py`: `async_check_booking` (cena i dodatkowe potwierdzenia),
  `async_book_appointment` (POST v2); istniejący auth i wspólna sesja API.
- `coordinator.py`: trzeci przycisk w natywnych powiadomieniach mobile_app,
  odpowiedź po rezerwacji. Osobne powiadomienia mobile_app dla maks. 3 nowych terminów per szukajka/poll + podsumowanie bez rezerwacji; unikalne tagi i wynik zastępujący właściwy termin. Opcja konta notification_path otwiera widok HA na Androidzie/iOS, działa bez reloadu.
- `services.py` / `services.yaml`: `medi_assistant.list_booking_offers`
  oraz `medi_assistant.book_appointment` — przeglądanie terminów i rezerwacja przez akcje HA.
- `cancellation.py`: lista zaplanowanych wizyt konta i uchwyty `cancellation_id` na 30 minut; ponowna weryfikacja tożsamości wizyty, canCancel i isLateCancellation (późne odwołanie wymaga confirm_late_cancellation), blokada powtórzeń po nieznanym wyniku. Akcje `list_booked_appointments` i `cancel_appointment`; testy `test_cancellation.py` i protokołu w `test_booking_api.py`.
- `frontend.py` / `www/medi-assistant-card.js`: samodzielnie ładowana karta `custom:medi-assistant-appointments`, lista wszystkich kont bez encji/historii i bez zależności runtime JS; odwołanie z potwierdzeniem, zgoda na późne odwołanie, odświeżenie po operacji i co 5 min. `list_booked_appointments` bez entity_id zwraca accounts; ze wskazaną szukajką zachowuje wcześniejszy kontrakt appointments. Testy DOM: `tests/frontend/card.test.mjs`, `pnpm test:frontend`; Chromium komputer/telefon: `tests/browser/card.spec.mjs`, `pnpm test:browser`; Python `test_frontend.py`. Serwer Playwright ładuje produkcyjną kartę i zastępuje usługi HA danymi syntetycznymi, z blokadą ruchu zewnętrznego i sesją per test; nie testuje natywnego formularza HA.
- `search_offers.py`: `list_booking_offers` bez entity_id zwraca accounts/searches z nazwami, statusem i maks. 20 najbliższymi terminami; świeże uchwyty booking_id z aktualnych danych koordynatora, bez bookingString. `refresh: true` pobiera nowe wyniki; dotychczasowa odpowiedź pojedynczej szukajki pozostaje. Karta pokazuje sekcję Szukajki i potwierdza rezerwację konkretnego terminu, po zapisie odświeża obie listy. Moduł karty używa numeru wydania w parametrze cache. Przycisk Dodaj szukajkę przy nagłówku otwiera natywną konfigurację integracji HA; widoczny administratorom również bez istniejących szukajek.
- `sensor.py`: istniejący kontrakt atrybutów + `booking_id`; bez `bookingString`.
- Instrukcja: `docs/BOOKING.md`. Testy: `test_booking.py`, `test_booking_api.py`,
  regresje dotychczasowych sensorów/powiadomień w istniejących testach.

## Środowisko i kontrola

`requirements_test.txt` przypina PHACC 0.13.301 dla HA 2025.12.4 / Python 3.13.
Ruff 0.15.17 zgodnie z `requirements_dev.txt`. Testy uruchamiaj na Pythonie
3.13 w izolowanym kontenerze `medi-assistant-tests`; Windows ma Python 3.14.
Zależności testowe nie są instalowane w kontenerze interaktywnego HA.

`scripts/start-local-ha.ps1` uruchamia `medicover-ha-local` pod
<http://localhost:8123>, montując kod z tego repozytorium. Sprawdza źródło
istniejącego kontenera, żeby nie uruchomić innej kopii. Dane sesji wyłącznie
w ignorowanym `.local-ha/config`. Dostęp lokalny bez logowania jest oparty
na `trusted_networks`, a nie kopiowaniu sesji z produkcji.

Kontrole według zakresu: pytest (próg coverage 90%), Ruff, `pnpm test:frontend` i `pnpm test:browser`. CI obejmuje również Hassfest/HACS i job Chromium. Raportuj wyniki bieżącego uruchomienia. Testy automatyczne mają wyłącznie dane syntetyczne; rzeczywiste rezerwacje i odwołania wymagają osobnego zlecenia.

Przy commicie pomijaj zastaną zmianę `CLAUDE.md`. Stosuj procedurę PR i zielonych wymaganych kontroli z AGENTS.md. Same testy nie podbijają wersji produktu.

Stały debug integracji i HTTP auth jest ustawiany przez launcher także w istniejącej konfiguracji testowej. Komunikaty integracji pomijają nazwy kont, szukajek i parametry OIDC; regresja logowania sprawdza ukrycie kodu autoryzacyjnego. Filtr sensora w services.yaml musi wskazywać domenę medi_assistant.

Lokalny dashboard Moje wizyty: http://localhost:8123/medi-assistant/wizyty. Utworzony osobno, bez zmiany dotychczasowych widoków; karta sprawdzona w prawdziwej przeglądarce przez interfejs Playwright narzędzia: lista rezerwacji, szukajka i jej terminy, odświeżenie, zwijanie/rozwijanie, otwarcie potwierdzenia zgodnego z wybranym terminem i powrót bez rezerwacji. Przy aktualizacji v2 użyto ?v=2 w adresie dashboardu, bo przeglądarka zachowała HTML ładujący v1. Zapisane testy interakcji obejmują happy-dom i Playwright/Chromium; CI uruchamia oba zestawy. Zakres Chromium to karta z fikcyjnym HA, a nie pełna instalacja HA. Zapisy i błędy rezerwacji/odwołania w karcie zweryfikowane na danych syntetycznych.

- `search_services.py` / `search_config.py`: `get_search_filters` i `create_search`; wspólny zapis danych z config flow, walidacja filtrów i dat, blokada duplikatów per konto. Testy `test_search_services.py`; kontrakt w `docs/BOOKING.md`.

- Usuwanie szukajki z karty: delete_search (administrator, account_id + search_id), potwierdzenie nazwy/konta, usunięcie stanu powiadomień i subentry bez odwoływania wizyt. Testy Python, DOM i Chromium obejmują uprawnienia, błąd, utratę odpowiedzi i izolację kont.

- `brand/icon.png` i `brand/icon@2x.png`: oryginalna ikona kalendarza z plusem i potwierdzeniem, PNG RGBA 256/512. Lokalne brand images obsługuje HA 2026.3+; testowy HA 2025.12.4 nie wyświetla ich automatycznie. Podgląd w README.
