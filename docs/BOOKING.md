# Rezerwacja wizyty

## Rezerwacja przez akcje HA

1. W Ustawienia → Urządzenia i usługi dodaj **Medi Assistant** i konto Medicover.
2. Dodaj szukajkę. Jej sensor pokaże znalezione terminy.
3. W Narzędzia deweloperskie → Akcje uruchom
   **Medi Assistant: Pokaż terminy do rezerwacji** (`medi_assistant.list_booking_offers`).
   Wybierz sensor szukajki. Bez wyboru sensora akcja zwraca `accounts` z listą
   `searches` dla każdego konta. Parametr `refresh: true` aktualizuje wyniki
   przy listowaniu wszystkich szukajek. Ta akcja wyłącznie wyświetla terminy.
4. Skopiuj `booking_id` wybranego terminu do akcji
   **Medi Assistant: Zarezerwuj wizytę** (`medi_assistant.book_appointment`).
   Uruchomienie jest prawdziwą rezerwacją na właściwym koncie pacjenta.
   Odpowiedź zawiera `success`, `status` i czytelny `message`.

Korzystanie z tych akcji nie wymaga ustawienia powiadomień.

## Karta „Moje wizyty”

Po ponownym uruchomieniu HA karta **Medi Assistant — Moje wizyty** jest
dostępna w wyborze kart dashboardu. Integracja sama udostępnia i ładuje jej
moduł; nie wymaga osobnego dodatku HACS ani zewnętrznych skryptów.
Dodaj kartę z listy w edytorze dashboardu. Konfiguracja YAML, jeśli potrzebna:

```yaml
type: custom:medi-assistant-appointments
```

Karta pobiera zaplanowane wizyty wszystkich aktywnych kont, także bez szukajek.
Przy każdej pokazuje datę, godzinę, lekarza, specjalność i placówkę.
Pod listą rezerwacji sekcja **Szukajki** grupuje znalezione terminy według
konta i nazwy szukajki. Przy każdym terminie **Zarezerwuj** otwiera potwierdzenie
z danymi wizyty. Zapis używa dokładnego `booking_id` i dotychczasowego
sprawdzenia aktualnej dostępności, ceny i wymaganych zgód. Wyświetlane jest
do 20 najbliższych terminów na szukajkę; przy większej liczbie karta pokazuje
liczbę wyświetlonych i wszystkich znalezionych terminów. Szukajki można zwijać. Przycisk **Dodaj szukajkę** obok nagłówka
otwiera konfigurację Medi Assistant, gdzie uruchomisz natywny formularz HA.
Przycisk jest widoczny administratorom, także gdy nie ma jeszcze szukajek.
**Odśwież** pobiera nowe wyniki; okresowe odświeżanie karty odczytuje wyniki
ostatniej aktualizacji integracji. Błędy szukajek są odróżnione od braku terminów.
Po próbie rezerwacji odświeżane są rezerwacje i wyniki szukajek, bez ponawiania zapisu.

**Odwołaj** otwiera potwierdzenie z danymi konkretnej wizyty. Późne odwołanie
wymaga dodatkowego zaznaczenia zgody. Po operacji lista pobierana jest ponownie;
samoczynnie odświeża się także co 5 minut oraz po powrocie do nieaktualnej karty.
Błąd jednego konta nie ukrywa wizyt pozostałych. Dane wizyt nie trafiają do
encji, historii HA, localStorage ani logów integracji.

Testy interakcji: Node 24, `pnpm install --frozen-lockfile --ignore-scripts`,
`pnpm test:frontend` (syntetyczne dane i fałszywe odpowiedzi HA).
Testy w prawdziwym Chromium: `pnpm test:browser` — komputer i telefon, z
fikcyjnymi usługami HA. [Uruchomienie i zakres](BROWSER_TESTS.md).
Karta używa [API własnych kart HA](https://developers.home-assistant.io/docs/frontend/custom-ui/custom-card/).

## Odwołanie wizyty przez akcje HA

1. Uruchom **Medi Assistant: Pokaż zarezerwowane wizyty**
   (`medi_assistant.list_booked_appointments`). Opcjonalnie wybierz dowolną szukajkę
   należącą do konta pacjenta. Bez wskazania szukajki akcja zwraca `accounts`
   ze wszystkimi aktywnymi kontami; z szukajką zachowuje odpowiedź `appointments`. Lista obejmuje zaplanowane wizyty tego konta,
   również zarezerwowane poza integracją.
2. Skopiuj `cancellation_id` konkretnej wizyty do akcji
   **Medi Assistant: Odwołaj wizytę** (`medi_assistant.cancel_appointment`).
   Uruchomienie odwołuje tę wizytę i zwraca `success`, `status`, `message`.

Identyfikator jest ważny 30 minut, do przeładowania integracji lub restartu HA.
Integracja ponownie sprawdza dokładny termin, lekarza, placówkę i specjalność.
Wymaga `canCancel`. Dla późnych odwołań (`isLateCancellation`) sprawdź
warunki w Medicover i włącz **Potwierdzam późne odwołanie**
(`confirm_late_cancellation`). Domyślnie to pole jest wyłączone. Nie powtarza operacji po nieznanym wyniku.
Żadna wizyta nie jest odwoływana podczas instalacji ani wyświetlania listy.
Protokół: lista Planned i DELETE identyfikatora z person-appointments;
referencja: [MediCony](https://github.com/bartekmp/MediCony/blob/main/src/medicover/api_client.py).

## Przycisk w powiadomieniu

W szczegółach szukajki wybierz usługę telefonu `notify.mobile_app_…`.
Dla maksymalnie trzech najbliższych nowych terminów na szukajkę podczas jednego
sprawdzenia wysyłane są osobne powiadomienia. Każde zawiera jedną wizytę oraz
przyciski **Zarezerwuj**, **Wycisz szukajkę (24h)** i **Usuń szukajkę**.
Przycisk rezerwacji pojawia się tylko dla terminu z aktualnym uchwytem rezerwacji.
Pozostałe nowe terminy są zgłaszane zbiorczo, bez przycisku rezerwacji.
Wszystkie wykryte terminy są zapamiętywane; nadmiarowe nie są wysyłane później
jako kolejna paczka powiadomień.

Kliknięcie **Zarezerwuj** rezerwuje dokładnie tę wizytę bez dodatkowego potwierdzenia.
Wynik zastępuje powiadomienie wybranego terminu i nie zawiera ponownego przycisku
rezerwacji. Powiadomienia innych terminów mają osobne identyfikatory.
Wyciszenie i usunięcie dotyczą całej szukajki.

W opcjach konta ustaw **Widok otwierany z powiadomienia** (`notification_path`),
np. `/dashboard-zdrowie/medicover`. Względna ścieżka jest przekazywana jako
`url` dla iOS i `clickAction` dla Androida. Kliknięcie treści powiadomienia,
podsumowania lub wyniku rezerwacji otwiera ten widok w aplikacji HA.
Puste pole pozostawia domyślne zachowanie aplikacji; zmiana działa bez restartu.
Widok z kartą trzeba wcześniej utworzyć we własnym dashboardzie.
[Dokumentacja Companion](https://companion.home-assistant.io/docs/notifications/notifications-basic/#opening-a-url).

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

## Lokalne środowisko deweloperskie

Uruchom `scripts/start-local-ha.ps1` i otwórz <http://localhost:8123>.
Launcher montuje `custom_components` z tego repozytorium, uruchamia oficjalny
HA 2025.12.4 / Python 3.13 i przygotowuje lokalnego testera. Dostęp z localhost
pomija logowanie dzięki `trusted_networks` (localhost i brama Dockera) oraz
`allow_bypass_login`. Port jest dostępny tylko na tym komputerze.
Konfiguracja i sesje pozostają wyłącznie w ignorowanym `.local-ha/config`.

Zatrzymanie środowiska: `docker stop medicover-ha-local`.
Zmiany Pythona wymagają restartu kontenera.
Nie kopiuj konfiguracji `.storage` z produkcyjnego HA.

## Tworzenie szukajek przez chatbot lub automatyzację

Akcje zapisują standardowe subentry konta: szukajka ma sensor, powiadomienia,
wyniki na karcie i może być edytowana oraz usuwana w konfiguracji integracji.
Logowanie do konta nadal odbywa się przez config flow.

1. Wywołaj `medi_assistant.get_search_filters` bez parametrów, aby otrzymać
   listę załadowanych kont (`accounts`, pola `account_id` i `account_name`).
2. Wywołaj tę samą akcję z `account_id`, aby otrzymać `regions`.
3. Dodaj `region_id`, aby otrzymać `specialties` dla tego regionu.
4. Dodaj `specialty_id`, aby otrzymać `clinics` i `doctors`.
   Listy mają elementy `{id, value}`. `languages` zawiera obsługiwane języki.
5. Wywołaj `medi_assistant.create_search`:

```yaml
action: medi_assistant.create_search
data:
  account_id: "IDENTYFIKATOR_Z_ODPOWIEDZI"
  region_id: 204
  specialty_id: 9
  date_from: "2026-11-01"
  date_to: "2026-11-30"
response_variable: search
```

Identyfikatory w przykładzie są ilustracyjne; użyj filtrów własnego konta.
Opcjonalne pola: `clinic_id`, `doctor_id`, `language_id`, `date_from`,
`date_to`, `notify_target` (lista celów, np. `notify.mobile_app_telefon`).
Brak dat oznacza domyślny zakres integracji. Brak powiadomień jest dozwolony.
Nieprawidłowe filtry lub zakres dat powodują błąd bez zapisania szukajki.
Odpowiedź zawiera `success`, `created`, `account_id`, `search_id`, `title`
i zapisane `data`. Identyczna szukajka na tym samym koncie jest zwracana
z `created: false`, także przy równoczesnych ponowieniach. Szukajki różnych
kont są niezależne. Wyniki pojawią się po odświeżeniu koordynatora;
można pobrać je przez `list_booking_offers` z `refresh: true`.

Obie akcje wymagają odbioru odpowiedzi. Dla REST użyj
`POST /api/services/medi_assistant/create_search?return_response` (analogicznie
`get_search_filters`) z uwierzytelnieniem HA. Samo dodanie akcji nie udostępnia
jej automatycznie modelowi: po stronie chatbota trzeba zdefiniować narzędzia
wywołujące te akcje i przekazujące odpowiedzi. Tworzenie szukajki nie rezerwuje wizyty.
