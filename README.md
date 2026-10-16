# Medi Assistant — wizyty Medicover w Home Assistant

[![CI](https://github.com/psorobka/medi_assistant/actions/workflows/ci.yml/badge.svg)](https://github.com/psorobka/medi_assistant/actions/workflows/ci.yml)
[![HACS](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)

Integracja z Medicover online24 do wyszukiwania terminów, powiadamiania o nowych
wizytach oraz ich rezerwowania i odwoływania z Home Assistant. Obsługuje wiele
kont i wiele zapisanych wyszukiwań — **szukajek** — na każdym koncie.

> To nieoficjalna integracja, niezwiązana z Medicover. Korzysta z niepublicznego
> API online24, którego zmiany mogą wpłynąć na jej działanie.

## Co możesz zrobić

- Zapisać szukajkę: region → specjalizacja → opcjonalnie placówka, lekarz, język i daty.
- Otrzymywać powiadomienia o nowych terminach na jednym lub kilku urządzeniach.
- Przeglądać szukajki, dostępne terminy i zaplanowane wizyty na jednej karcie.
- Rezerwować konkretny termin i odwoływać wizyty z potwierdzeniem na karcie.
- Tworzyć szukajki oraz obsługiwać wizyty przez akcje HA, automatyzacje lub chatbota.
- Wykorzystywać sensor każdej szukajki we własnych dashboardach i automatyzacjach.

**Na skróty:** [Instalacja](#instalacja) · [Konfiguracja](#konfiguracja) ·
[Karta Moje wizyty](#karta-moje-wizyty) · [Akcje HA](#akcje-ha-serwisy) ·
[Chatbot](#tworzenie-szukajki-przez-chatbota-lub-automatyzację) ·
[Rozwiązywanie problemów](#rozwiązywanie-problemów)

## Instalacja

Wymagane są konto Medicover z dostępem do online24 i Home Assistant.
Deklarowana minimalna wersja HA to **2025.2.0**; testy backendu korzystają
z **HA 2025.12.4 / Python 3.13**.

### Przez HACS

1. Otwórz **HACS → Integracje → ⋮ → Repozytoria niestandardowe**.
2. Dodaj `https://github.com/psorobka/medi_assistant` jako **Integrację**.
3. Zainstaluj **Medi Assistant** i uruchom ponownie Home Assistant.

### Ręcznie

Skopiuj katalog `custom_components/medi_assistant` do
`config/custom_components/` w HA i uruchom ponownie Home Assistant.

## Konfiguracja

### Dodaj konto

Otwórz **Ustawienia → Urządzenia i usługi → Dodaj integrację → Medi Assistant**.
Podaj e-mail i hasło, a następnie kod weryfikacyjny SMS lub e-mail, jeśli Medicover
go wymaga. Nazwa konta zostanie ustawiona na podstawie danych pacjenta.
Dla kolejnego konta ponownie dodaj integrację.

Dane logowania są zapisywane w konfiguracji HA, aby umożliwić automatyczne
odnowienie sesji. Szczegóły: [Logowanie i prywatność](#logowanie-i-prywatność).

### Dodaj szukajkę

W konfiguracji konta Medi Assistant wybierz dodanie szukajki i przejdź trzy kroki:

1. **Region** — domyślnie region ostatnio dodanej szukajki.
2. **Specjalizacja** — lista dostępna dla wybranego regionu.
3. **Szczegóły** — opcjonalnie placówka, lekarz, język wizyty, daty i cele powiadomień.

Każda szukajka ma własny sensor. Można ją edytować i usuwać w konfiguracji
integracji; zmiany nie wymagają restartu HA. Ustawienie powiadomień jest opcjonalne.
Szukajki dodane przez akcję `create_search` działają i są edytowane tak samo.

### Częstotliwość wyszukiwania

W opcjach konta wybierz **Konfiguruj → Interwał sprawdzania**: od 5 do 120 minut,
domyślnie **10 minut**. Zmiana interwału przeładowuje dane konto.
Przycisk **Odśwież dane Medicover** pobiera ponownie listy filtrów i nazwę pacjenta.

## Karta „Moje wizyty”

Karta **Medi Assistant — Moje wizyty** łączy dane wszystkich kont w jednym widoku.
Po instalacji i restarcie HA otwórz edycję dashboardu, wybierz **Dodaj kartę**
i znajdź ją na liście. Jej moduł ładuje się automatycznie — bez osobnej instalacji
karty w HACS ani ręcznego dodawania zasobu JavaScript.

Możesz też dodać kartę przez YAML:

```yaml
type: custom:medi-assistant-appointments
```

Karta zawiera dwie części:

| Część | Zawartość i działania |
| --- | --- |
| Zaplanowane wizyty | Wizyty wszystkich kont, również zarezerwowane poza integracją. Data, godzina, lekarz, specjalność i placówka oraz przycisk **Odwołaj**. Działa także dla kont bez szukajek. |
| Szukajki | Wyszukiwania pogrupowane według konta, liczba wyników i do 20 najbliższych terminów na szukajkę. Przycisk **Zarezerwuj** przy konkretnym terminie. Listy można zwijać. |

Rezerwacja i odwołanie wymagają potwierdzenia z danymi wybranej wizyty.
Późne odwołanie wymaga dodatkowej zgody. Po operacji karta odświeża listy;
nie ponawia automatycznie operacji o nieznanym wyniku.

**Odśwież** pobiera aktualne wyniki. Automatyczne odświeżanie karty co 5 minut
odczytuje ostatnie wyniki wyszukiwania integracji — nie zastępuje interwału
sprawdzania konta. Niedostępne dane są odróżnione od braku terminów.

Przycisk **Dodaj szukajkę** otwiera natywną konfigurację integracji HA.
Jest widoczny administratorom, również gdy nie ma jeszcze szukajek.

## Akcje HA (serwisy)

Akcje znajdziesz w **Narzędzia deweloperskie → Akcje**. Możesz też wywoływać je
ze skryptów i automatyzacji. Wszystkie zwracają odpowiedź; w skrypcie odbierz ją
przez `response_variable`. Pełne nazwy mają prefiks **`medi_assistant.`**.

| Akcja | Parametry | Wynik |
| --- | --- | --- |
| `get_search_filters` | Opcjonalnie `account_id`, następnie `region_id` i `specialty_id` | Bez parametrów: załadowane konta. Po wyborze konta: regiony; po wyborze regionu: specjalizacje; po wyborze specjalizacji: placówki i lekarze. Także lista języków. |
| `create_search` | Wymagane `account_id`, `region_id`, `specialty_id`; opcjonalne filtry poniżej | Tworzy standardową szukajkę. Zwraca m.in. `search_id`, `title`, `data` i `created`. Identyczne ponowienie na tym samym koncie zwraca istniejącą szukajkę z `created: false`. |
| `list_booking_offers` | Opcjonalnie `entity_id`; bez niego można podać `refresh: true` | Szukajki wszystkich kont i do 20 najbliższych terminów na szukajkę. Z `entity_id`: terminy jednej szukajki. Każdy termin do rezerwacji zawiera `booking_id`. |
| `book_appointment` | Wymagane `booking_id` | Rezerwuje wskazany termin i zwraca `success`, `status`, `message`. |
| `list_booked_appointments` | Opcjonalnie `entity_id` szukajki | Zaplanowane wizyty wszystkich kont albo konta wskazanej szukajki. Wizyty zawierają `cancellation_id` do odwołania. |
| `cancel_appointment` | Wymagane `cancellation_id`; opcjonalnie `confirm_late_cancellation: true` | Odwołuje wskazaną wizytę i zwraca `success`, `status`, `message`. Zgoda jest wymagana przy późnym odwołaniu. |

**Rezerwacja i odwołanie wykonują rzeczywistą operację na koncie Medicover.**
Samo pobieranie filtrów, tworzenie szukajki i wyświetlanie list nie rezerwuje wizyty.

### Tworzenie szukajki przez chatbota lub automatyzację

Konto najpierw dodaj przez interfejs HA. Następnie pobierz dostępne identyfikatory
z `get_search_filters` — chatbot powinien używać wartości z odpowiedzi:

1. Bez parametrów: wybierz `account_id` z listy `accounts`.
2. Z `account_id`: wybierz `region_id` z `regions`.
3. Z `account_id` i `region_id`: wybierz `specialty_id` z `specialties`.
4. Dodaj `specialty_id`, jeśli chcesz poznać dostępne `clinics` i `doctors`.

Listy filtrów zawierają elementy `{id, value}`. Przykład pierwszego wywołania
jako kroku skryptu HA:

```yaml
action: medi_assistant.get_search_filters
data: {}
response_variable: filters
```

Po wyborze parametrów utwórz szukajkę:

```yaml
action: medi_assistant.create_search
data:
  account_id: "IDENTYFIKATOR_KONTA_Z_ODPOWIEDZI"
  region_id: 204
  specialty_id: 9
  date_from: "2026-11-01"
  date_to: "2026-11-30"
response_variable: search
```

Identyfikatory i daty są przykładowe. Opcjonalne parametry to `clinic_id`,
`doctor_id`, `language_id`, `date_from`, `date_to` i `notify_target`
(lista, np. `["notify.mobile_app_telefon"]`). Nieprawidłowe filtry i odwrócony
zakres dat są odrzucane. Nie trzeba ustawiać dat ani powiadomień.

Nowa szukajka pojawi się w konfiguracji HA i na karcie. Wyniki otrzyma przy
odświeżeniu integracji. Możesz też od razu poprosić o nowe wyniki wszystkich szukajek:

```yaml
action: medi_assistant.list_booking_offers
data:
  refresh: true
response_variable: offers
```

Akcje są podstawą do podłączenia chatbota. Po jego stronie trzeba udostępnić
narzędzia wywołujące akcje HA i odbierające ich odpowiedzi; samo zainstalowanie
integracji nie dodaje ich automatycznie do modelu. Przy REST API HA użyj
`POST /api/services/medi_assistant/<nazwa_akcji>?return_response`
z uwierzytelnieniem HA i parametrami w JSON.

### Rezerwacja i odwołanie

Do `book_appointment` przekaż `booking_id` wybranego terminu z
`list_booking_offers`. Do `cancel_appointment` przekaż `cancellation_id`
z aktualnej odpowiedzi `list_booked_appointments`.

Te identyfikatory są ważne **30 minut**, do restartu HA lub przeładowania
integracji. Przed operacją ponownie sprawdzana jest konkretna wizyta.
Rezerwacja wymagająca płatności, dodatkowych zgód lub nadrezerwacji jest
zatrzymywana przed zapisem. Po statusie `unknown` sprawdź listę wizyt przed
kolejną próbą — zapis nie jest automatycznie ponawiany.

Szczegółowy opis odpowiedzi i ograniczeń: [Obsługa wizyt i szukajek](docs/BOOKING.md).

## Powiadomienia

W szczegółach szukajki wybierz jeden lub kilka celów powiadomień. Formularz
pokazuje usługi `notify.*`; inny obsługiwany cel, w tym encję powiadomień,
możesz wpisać ręcznie.

- Nowy termin jest zgłaszany przy pierwszym wykryciu, również po dodaniu szukajki.
- Zapamiętane terminy są przechowywane na dysku, więc restart nie wysyła ich ponownie.
- Telefon (`notify.mobile_app_…`) otrzymuje osobne powiadomienie dla każdego
  z maksymalnie **3 najbliższych nowych terminów na szukajkę podczas jednego sprawdzenia**.
  Pozostałe terminy są zgłaszane zbiorczo, bez przycisku rezerwacji.
- Inne cele powiadomień otrzymują listę do 15 nowych terminów i liczbę pozostałych.
- Usługa telefonu `notify.mobile_app_…` umożliwia przyciski **Zarezerwuj**,
  **Wycisz szukajkę (24h)** i **Usuń szukajkę**. Rezerwacja dotyczy jednego terminu
  pokazanego w tej wiadomości i następuje po kliknięciu, bez dodatkowego formularza
  potwierdzenia. Wyciszenie i usunięcie dotyczą całej szukajki.
- Encje powiadomień i `notify.persistent_notification` otrzymują zwykłą wiadomość,
  bez przycisków. Ten ostatni cel pozwala wyświetlać wiadomości w panelu HA.

### Widok otwierany po kliknięciu powiadomienia

W opcjach konta (**Konfiguruj**) ustaw **Widok otwierany z powiadomienia**,
np. `/dashboard-zdrowie/medicover`. Skopiuj ścieżkę z adresu własnego dashboardu
z kartą Medi Assistant, bez nazwy serwera. Kliknięcie treści powiadomienia
mobilnego otworzy ten widok w aplikacji HA na Androidzie lub iOS; przycisk
**Zarezerwuj** nadal wykona rezerwację wskazanego terminu.

Ustawienie dotyczy wszystkich szukajek konta, także powiadomień zbiorczych
i wyników rezerwacji. Puste pole pozostawia domyślny widok aplikacji.
Zapis ścieżki nie wymaga restartu. Ścieżka wskazuje istniejący widok — nie tworzy go.

## Sensor i własne automatyzacje

Stan sensora pokazuje najbliższy termin (`data · lekarz · placówka`) albo
`Brak terminów`. Atrybuty umożliwiają własne prezentacje i automatyzacje:

| Atrybut | Znaczenie |
| --- | --- |
| `count` | Liczba wszystkich terminów pasujących do szukajki. |
| `earliest` | Data i godzina najbliższego terminu w formacie ISO. |
| `appointments` | Do 20 najbliższych terminów z danymi wizyty i `booking_id`. |
| `appointments_truncated` | `true`, gdy wyników jest więcej niż 20. |
| `search` | Zapisane parametry szukajki. |

Przykład automatyzacji — zastąp identyfikatory sensora i telefonu własnymi:

```yaml
automation:
  - alias: "Medicover — znaleziono termin"
    triggers:
      - trigger: numeric_state
        entity_id: sensor.twoja_szukajka
        attribute: count
        above: 0
    actions:
      - action: notify.mobile_app_twoj_telefon
        data:
          title: "Dostępny termin Medicover"
          message: "{{ states('sensor.twoja_szukajka') }}"
```

Ta automatyzacja reaguje na wzrost liczby terminów z zera do wartości dodatniej.
Wbudowane powiadomienia szukajki zgłaszają także nowe terminy, gdy inne nadal są dostępne.

## Logowanie i prywatność

Integracja podtrzymuje krótką sesję Medicover i próbuje automatycznie zalogować
się ponownie przy odrzuconym odświeżeniu tokena. Jeśli automatyczne odnowienie
się nie powiedzie, HA poprosi o ponowne uwierzytelnienie.

Hasło, tokeny i ciasteczka sesji są przechowywane w konfiguracji Home Assistant.
Eksport diagnostyczny usuwa z nich dane uwierzytelniające. Połączenia z usługą
Medicover korzystają z jej API. Podłączenie zewnętrznego chatbota oznacza osobną
decyzję o tym, które odpowiedzi — w tym dane wizyt — mu przekazujesz.

Lista zaplanowanych wizyt w karcie jest pobierana przez akcję, bez zapisywania
jej w osobnych encjach i historii HA. Szukajki mają sensory i ich atrybuty,
które mogą być zapisywane przez standardowy recorder HA.

## Rozwiązywanie problemów

| Problem | Co sprawdzić |
| --- | --- |
| HA wymaga ponownego logowania | Otwórz powiadomienie i zaloguj się ponownie, podając kod, jeśli jest wymagany. |
| Brakuje specjalizacji lub placówki | Filtry zależą od regionu i konta. Sprawdź wybrany region i użyj przycisku **Odśwież dane Medicover**. |
| Nie ma powiadomienia po dodaniu szukajki | Poczekaj na sprawdzenie terminów i zweryfikuj cel powiadomień. Pierwsze wykrycie dostępnego terminu powinno wysłać wiadomość. |
| Karty nie ma na liście lub po aktualizacji wygląda jak wcześniej | Uruchom ponownie HA po instalacji i odśwież stronę przeglądarki, aby załadować moduł karty. |
| Termin wygasł lub wynik operacji jest nieznany | Odśwież listy. Przy nieznanym wyniku sprawdź zaplanowane wizyty przed następną próbą. |

Aby włączyć logowanie diagnostyczne:

```yaml
logger:
  logs:
    custom_components.medi_assistant: debug
```

Przed udostępnieniem logów sprawdź, czy nie zawierają danych osobowych.

## Rozwój i testy

### Lokalny Home Assistant w Dockerze

Do ręcznego sprawdzania konfiguracji, sensorów, karty i akcji możesz uruchomić
osobną instancję HA z kodem z tego repozytorium. Wymagane są działający Docker
(na Windows: Docker Desktop z kontenerami Linux) i PowerShell.
W katalogu projektu uruchom:

```powershell
./scripts/start-local-ha.ps1
```

Skrypt uruchamia kontener `medicover-ha-local` z **HA 2025.12.4** i udostępnia
go pod [http://localhost:8123](http://localhost:8123). Przygotowuje lokalnego
użytkownika i dostęp przez zaufaną sieć; port jest przypisany wyłącznie do
tego komputera. Przy pierwszym uruchomieniu Docker pobierze obraz HA.

Kod `custom_components` jest montowany bezpośrednio z repozytorium, więc nie
trzeba kopiować integracji po każdej zmianie. Dodaj konto przez interfejs HA,
utwórz szukajkę i dodaj kartę do dashboardu. Po zmianach Pythona zrestartuj HA;
po zmianach karty odśwież również stronę przeglądarki.

```powershell
# Wczytanie zmian Pythona
docker restart medicover-ha-local

# Podgląd logów na żywo; Ctrl+C kończy podgląd
docker logs --follow --tail 100 medicover-ha-local

# Zatrzymanie środowiska
docker stop medicover-ha-local
```

Ponowne wywołanie skryptu uruchamia istniejący kontener. Konfiguracja, sesje
i logi pozostają w ignorowanym katalogu `.local-ha/config`; skrypt sprawdza,
czy istniejący kontener korzysta z właściwej kopii projektu. Nie kopiuj do niego
konfiguracji ani plików `.storage` z produkcyjnego HA i nie commituj lokalnych danych.

To działający HA: po podłączeniu konta komunikuje się z prawdziwym Medicover,
a rezerwacje i odwołania mają rzeczywisty skutek. Do automatycznych testów używaj
opisanych poniżej testów z danymi syntetycznymi. Zależności pytest instaluj
w osobnym środowisku, poza kontenerem interaktywnego HA.

### Testy automatyczne

Backend testowany jest na Pythonie **3.13**, z zależnościami z
`requirements_test.txt` i `requirements_dev.txt`. W izolowanym środowisku:

```sh
pip install -r requirements_test.txt -r requirements_dev.txt
pytest -q --cov --cov-report=term-missing --cov-fail-under=90
ruff check .
ruff format --check .
```

Testy karty wymagają Node.js **24** i pnpm **11.25.0**:

```sh
pnpm install --frozen-lockfile --ignore-scripts
pnpm test:frontend
pnpm exec playwright install chromium
pnpm test:browser
```

CI dla PR i gałęzi `main` obejmuje sześć kontroli: Ruff, pytest z progiem
pokrycia 90%, testy karty Node, Chromium na komputerze i telefonie, Hassfest i HACS.
Testy automatyczne korzystają wyłącznie z danych syntetycznych i nie wywołują
prawdziwego API Medicover. Chromium sprawdza kartę z fikcyjnymi usługami HA;
formularze konfiguracji sprawdzają testy backendu.

- [Testy przeglądarkowe — uruchomienie i zakres](docs/BROWSER_TESTS.md)
- [Lokalne środowisko HA i szczegóły obsługi wizyt](docs/BOOKING.md)
- [Historia zmian](CHANGELOG.md)
- [Procedura pracy i wydań](AGENTS.md)

## Autorzy i licencja

Logika komunikacji i logowania (PKCE, MFA, odświeżanie tokenów i wyszukiwanie
wizyt) została przeniesiona do wersji asynchronicznej na podstawie projektu
[medichaser](https://github.com/rafsaf/medichaser) autorstwa rafsaf.
Projekt rozwijany z pomocą [Claude Code](https://claude.com/claude-code) (Anthropic)
oraz [Codex](https://openai.com/codex/) (OpenAI).

Licencja **GNU General Public License v3.0** — zobacz [LICENSE](LICENSE).
Projekt dziedziczy licencję GPL-3.0 po wykorzystanej logice medichaser.
Integracja jest udostępniana bez gwarancji; nie jest afiliowana ani zatwierdzona
przez Medicover.
