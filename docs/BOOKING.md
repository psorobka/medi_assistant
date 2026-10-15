# Rezerwacja wizyty

## Test lokalny bez telefonu

Uruchom `scripts/start-local-ha.ps1` i otwórz <http://localhost:8123>.
Launcher montuje `custom_components` z tego repozytorium, uruchamia oficjalny
HA 2025.12.4 / Python 3.13 i przygotowuje lokalnego testera. Dostęp z localhost
pomija logowanie dzięki `trusted_networks` (localhost i brama Dockera) oraz
`allow_bypass_login`. Port jest dostępny tylko na tym komputerze.
Konfiguracja i sesje pozostają wyłącznie w ignorowanym `.local-ha/config`.

1. W Ustawienia → Urządzenia i usługi dodaj **Medi Assistant** i konto Medicover.
2. Dodaj szukajkę. Jej sensor pokaże znalezione terminy.
3. W Narzędzia deweloperskie → Akcje uruchom
   **Medi Assistant: Pokaż terminy do rezerwacji** (`medi_assistant.list_booking_offers`).
   Wybierz sensor szukajki. Ta akcja wyłącznie wyświetla terminy.
4. Skopiuj `booking_id` wybranego terminu do akcji
   **Medi Assistant: Zarezerwuj wizytę** (`medi_assistant.book_appointment`).
   Uruchomienie jest prawdziwą rezerwacją na właściwym koncie pacjenta.
   Odpowiedź zawiera `success`, `status` i czytelny `message`.

Nie trzeba ustawiać powiadomień do powyższego testu. Zatrzymanie środowiska:
`docker stop medicover-ha-local`. Zmiany Pythona wymagają restartu kontenera.
Nie kopiuj konfiguracji `.storage` z produkcyjnego HA.

## Przycisk w powiadomieniu

W szczegółach szukajki wybierz usługę telefonu `notify.mobile_app_…`.
Powiadomienie z nowym terminem ma do trzech przycisków: **Zarezerwuj**,
**Drzemka (24h)** i **Usuń**. „Zarezerwuj” dotyczy pierwszego terminu
wyświetlonego w wiadomości; terminy są sortowane chronologicznie.
Kliknięcie rezerwuje wizytę bez otwierania aplikacji. Wynik zastępuje
powiadomienie i nie zawiera ponownego przycisku rezerwacji.

Encje `notify.*` i `notify.persistent_notification` nadal dostają zwykłe
wiadomości, bez przycisków. Integracja obsługuje powiadomienia opcjonalnie,
zgodnie z dotychczasowym działaniem upstreamu.

## Warunki i wynik

- Identyfikator jest losowy, przypisany do konta i szukajki, ważny 30 minut
  od wystawienia. Restart/przeładowanie unieważnia go.
- Przed rezerwacją ponownie wyszukiwany jest dokładnie ten sam termin,
  lekarz, placówka i specjalność. Nie ma wyboru zastępczego terminu.
- Obsługiwane są zwykłe wizyty stacjonarne. Wymagana płatność, dodatkowe
  potwierdzenia, procedury lub nadrezerwacja zatrzymują operację przed zapisem.
  Nie udało się sprawdzić warunków → rezerwacja nie jest wysyłana.
- Podwójne kliknięcie oraz ten sam termin w dwóch szukajkach nie powodują
  drugiego zapisu. Po timeout lub niejednoznacznej odpowiedzi zapis nie jest
  ponawiany. Przy statusie `unknown` sprawdź listę wizyt Medicover przed
  następną próbą; nie restartuj integracji w celu ponowienia.
- `bookingString` jest używany wyłącznie wewnątrz klienta API; nie jest
  wystawiany w stanie sensora ani przesyłany w powiadomieniu.

## Podstawa protokołu

Publiczny kod strony Medicover sprawdzony 2026-10-15:

- <https://online24.medicover.pl/assets/components-kAFt3XDg.js>:
  `POST appointments/api/v2/search-appointments/book-appointment`,
  `POST payment-gateway/api/v2/visit-prices` i komunikaty z
  `information-dispatcher/api/v2/message-groups/messages-for-context/{context}`.
- <https://online24.medicover.pl/assets/BookingStep-DTwK3zeW.js>:
  `bookingString`, warunkowe `reportingId`, `appointmentSource: Direct`,
  oddzielna ścieżka płatności i dodatkowe potwierdzenia przed zapisem.

Testy używają syntetycznych odpowiedzi i nie rezerwują prawdziwych wizyt.
Test na rzeczywistym koncie wykonuje użytkownik.

Debug integracji jest stale aktywny w tej instancji testowej, także po restarcie. Log znajduje się w ignorowanym katalogu .local-ha/config/home-assistant.log; dostępny jest też przez docker logs medicover-ha-local. Logowanie integracji pomija nazwy kont i szukajek oraz parametry adresów OIDC.
