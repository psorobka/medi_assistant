# Aktywne repozytorium

Kod źródłowy: `custom_components/medi_assistant/`. Domena: `medi_assistant`.
Baza tej zmiany: upstream `main` e35693b (wersja manifestu 1.0.0).
Gałąź robocza: `codex/booking-from-notification`.

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
  odpowiedź po rezerwacji. Zarezerwuj dotyczy pierwszego wyświetlonego terminu.
- `services.py` / `services.yaml`: `medi_assistant.list_booking_offers`
  oraz `medi_assistant.book_appointment` — przeglądanie terminów i rezerwacja przez akcje HA.
- `cancellation.py`: lista zaplanowanych wizyt konta i uchwyty `cancellation_id` na 30 minut; ponowna weryfikacja tożsamości wizyty, canCancel i isLateCancellation (późne odwołanie wymaga confirm_late_cancellation), blokada powtórzeń po nieznanym wyniku. Akcje `list_booked_appointments` i `cancel_appointment`; testy `test_cancellation.py` i protokołu w `test_booking_api.py`.
- `frontend.py` / `www/medi-assistant-card.js`: samodzielnie ładowana karta `custom:medi-assistant-appointments`, lista wszystkich kont bez encji/historii i bez zależności runtime JS; odwołanie z potwierdzeniem, zgoda na późne odwołanie, odświeżenie po operacji i co 5 min. `list_booked_appointments` bez entity_id zwraca accounts; ze wskazaną szukajką zachowuje wcześniejszy kontrakt appointments. Testy DOM: `tests/frontend/card.test.mjs`, `pnpm test:frontend`; Python `test_frontend.py`.
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

Kontrole końcowe: 219 testów Python i 8 testów interakcji karty przeszło, pokrycie 95% (próg 90%), Ruff check i format,
walidacja lokalnej konfiguracji HA. Hassfest/HACS wymagają CI; nie twierdź,
że przeszły, jeśli nie uruchomiono workflow. Testy automatyczne nie korzystają z prawdziwego API. Rezerwacja przez akcję HA została potwierdzona na żywo na polecenie użytkownika. Odwołanie tej samej wizyty także potwierdzono na żywo na polecenie użytkownika: wynik cancelled i brak wizyty na ponownie pobranej liście Planned.

Przy commicie pomijaj zastaną zmianę `CLAUDE.md`. Nie publikuj ani nie scalaj
tej gałęzi bez polecenia użytkownika.

Stały debug integracji i HTTP auth jest ustawiany przez launcher także w istniejącej konfiguracji testowej. Komunikaty integracji pomijają nazwy kont, szukajek i parametry OIDC; regresja logowania sprawdza ukrycie kodu autoryzacyjnego. Filtr sensora w services.yaml musi wskazywać domenę medi_assistant.

Lokalny dashboard Moje wizyty: http://localhost:8123/medi-assistant/wizyty. Utworzony osobno, bez zmiany dotychczasowych widoków; karta sprawdzona w przeglądarce z prawdziwą pustą listą po odwołaniu wizyty. Potwierdzenia i błędy odwołania w karcie zweryfikowane na danych syntetycznych.
