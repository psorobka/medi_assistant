# Plan: Custom Component „Medicover" dla Home Assistant

Integracja HA odpytująca API Medicover (na bazie `medichaser.py`) o wolne terminy wizyt.
Obsługuje **wiele kont Medicover** oraz wiele **szukajek** (region / klinika / specjalista / lekarz / data) i wystawia każdą szukajkę jako **sensor**, na którym można budować automatyzacje i notyfikacje.

> **Zasada przewodnia: dodawanie szukajek robią osoby nietechniczne.** Cały interfejs ma być prosty jak formularz w aplikacji: same listy rozwijane z czytelnymi nazwami (żadnych ID, żadnego YAML, żadnego JSON-a). Listy regionów/specjalności/klinik/lekarzy pobierają się automatycznie po zalogowaniu i są zapisane w storage, więc dropdowny są natychmiastowe. Wpis konta nazywa się imieniem i nazwiskiem pacjenta, żeby od razu było wiadomo „czyje" to konto.

---

## 1. Decyzje architektoniczne (najnowsze techniki HA)

| Obszar | Wybór | Uzasadnienie |
|---|---|---|
| Konto Medicover | **Config Entry** (UI config flow) | Jeden wpis = jedno logowanie/token. Wiele kont = wiele wpisów. **Nazwa wpisu = imię i nazwisko pacjenta** (z `/personal-data/api/personal`). |
| Szukajka | **Config Subentry** (`config_subentries`, luty 2025) | Natywne dodawanie/edycja/usuwanie wielu elementów w UI, bez YAML. Każda szukajka ma własny, prosty kreator. |
| Endpointy OIDC | **OIDC discovery** (`/.well-known/openid-configuration`) | Pobieramy `authorization_endpoint`, `token_endpoint`, `userinfo_endpoint`, `revocation_endpoint`, `device_authorization_endpoint` zamiast zaszywać URL-e na sztywno. |
| Logowanie | **Login + hasło + MFA w config flow** (jak `medichaser.py`) | Najmniej kroków dla laika — wszystko w jednym formularzu HA. Device code flow rozważony w „Future improvements". |
| Listy filtrów (regiony/specjalności/kliniki/lekarze) | **Pobierane po auth + cache w `Store`** | Dropdowny natychmiastowe i offline. **Przycisk „Odśwież dane Medicover"** aktualizuje listy + dane pacjenta. |
| MFA / kod SMS | **Krok w config flow + reauth flow** | Jeśli używamy ścieżki login/hasło — pole na 6-cyfrowy kod; `async_step_reauth` gdy token wygaśnie. |
| Pobieranie terminów | **`DataUpdateCoordinator`** (jeden na konto) | Współdzieli token i sesję, jeden cykl obsługuje wszystkie szukajki konta → mniej żądań. |
| Odświeżanie list/danych konta | **`ButtonEntity`** „Odśwież dane Medicover" + serwis | Laik klika jeden przycisk w UI; aktualizuje cache filtrów i nazwę pacjenta. |
| Przechowywanie stanu | **`entry.runtime_data`** (typowany `ConfigEntry`) | Aktualny wzorzec (2024.6+) zamiast `hass.data[DOMAIN]`. |
| Trwały cache | **`Store`** (`homeassistant.helpers.storage`) | Token, device_id, dane pacjenta, listy filtrów — w storage, nie w plikach jak w CLI. |
| Encje szukajek | **`SensorEntity`** per szukajka, powiązana z subentry | `config_subentry_id` przy `async_add_entities`. |
| Klient HTTP | **`aiohttp`** przez `async_get_clientsession(hass)` | HA jest async — port logiki z `requests` na async. |

### Mapowanie `medichaser.py` → integracja HA

| `medichaser.py` | Odpowiednik w integracji |
|---|---|
| `Authenticator` (login/MFA/refresh, PKCE) | `api.py` → `MedicoverAuth` (async, aiohttp, endpointy z discovery) |
| `AppointmentFinder.find_appointments` | `MedicoverClient.find_appointments` |
| `AppointmentFinder.find_filters` | `MedicoverClient.find_filters` → **cache w `Store`**, zasila dropdowny |
| *(brak)* | `MedicoverClient.get_personal_data` → `/personal-data/api/personal` (imię/nazwisko) |
| `Notifier` (pushbullet/telegram/...) | **Usunięte** — notyfikacje robi user automatyzacją HA |
| pętla `while True` + `NextRun(interval)` | `DataUpdateCoordinator` z `update_interval` |
| `previous_appointments` / „new" | Stan sensora + automatyzacja HA |
| pliki tokenów `data/*.json` | `Store` + `entry.data` |
| czytanie kodu MFA ze `stdin` | krok `async_step_mfa` w config flow (lub device flow w ogóle bez MFA w HA) |

---

## 2. UX dla osób nietechnicznych (priorytet)

Dodanie szukajki ma wyglądać jak prosty kreator „klikam-dalej":

1. **Listy zamiast ID.** Każde pole to `SelectSelector` pokazujący czytelne nazwy („Warszawa", „Kardiolog", „dr Jan Kowalski"). ID są ukryte — zapisywane w tle. (W medichaserze user musiał ręcznie wywoływać `list-filters` i przepisywać numery — to eliminujemy.)
2. **Listy gotowe od razu.** Pobrane po zalogowaniu i trzymane w `Store`, więc kreator nie czeka na API.
3. **Kaskadowe zawężanie.** Po wyborze regionu + specjalności listy klinik i lekarzy filtrują się same do pasujących.
4. **Minimum wymaganych pól.** Wymagane tylko **region** i **specjalność**. Klinika, lekarz, język, data — opcjonalne (puste = „wszystko / od dziś").
5. **Wyszukiwarka w dropdownie.** `SelectSelector` z `mode: dropdown` i filtrowaniem tekstem — ważne przy długich listach lekarzy.
6. **Czytelne, automatyczne nazwy.** Szukajka dostaje tytuł typu „Kardiolog · Warszawa · od 1 lip" bez proszenia usera o nazwę.
7. **Nazwa konta = pacjent.** Wpis nazywa się „Jan Kowalski", więc przy wielu kontach (np. cała rodzina) od razu widać, czyje jest które.
8. **Jeden przycisk odświeżania.** Encja-przycisk „Odśwież dane Medicover" — gdy Medicover doda nową klinikę/specjalność albo zmieni się nazwisko, user klika i gotowe.
9. **Polskie tłumaczenia UI** (`translations/pl.json`) — wszystkie etykiety i komunikaty po polsku.
10. **Czytelne błędy.** Zamiast stacktrace: „Nie udało się zalogować — sprawdź dane" / „Kod SMS niepoprawny".

---

## 3. Struktura plików

```
custom_components/medicover/
├── __init__.py            # setup/unload entry, koordynator, runtime_data, ustawienie nazwy = pacjent
├── manifest.json
├── const.py               # DOMAIN, klucze configu, mapy języków, klucze Store
├── api.py                 # MedicoverAuth + MedicoverClient (async port medichasera + discovery + personal-data)
├── coordinator.py         # MedicoverCoordinator(DataUpdateCoordinator) — terminy
├── store.py               # FiltersStore / cache filtrów + danych pacjenta w Store
├── config_flow.py         # ConfigFlow (konto + device/MFA + reauth) + SubentryFlow (szukajki)
├── sensor.py              # MedicoverSearchSensor per szukajka
├── button.py              # „Odśwież dane Medicover" (ButtonEntity)
├── exceptions.py          # AuthError, MfaRequired, InvalidGrant, ApiError
├── strings.json
├── translations/
│   ├── en.json
│   └── pl.json
└── diagnostics.py
```

`hacs.json` + repo na GitHub → instalacja przez HACS jako custom repository.

---

## 4. Warstwa API (`api.py`)

Port `Authenticator.login_requests` + `AppointmentFinder` na async/aiohttp, z endpointami z **OIDC discovery**.

### Discovery
- Na starcie pobierz `https://login-online24.medicover.pl/.well-known/openid-configuration` (cache w `Store`).
- Stąd: `authorization_endpoint`, `token_endpoint`, `userinfo_endpoint`, `revocation_endpoint` (`device_authorization_endpoint` — patrz „Future improvements").
- Scope: `openid offline_access profile` jak w `medichaser.py` (discovery wymienia też `rdol_api` — ewentualnie potrzebny do API terminów; do weryfikacji).

### `MedicoverAuth` — login/hasło + MFA (jak `medichaser.py`)
Port `Authenticator.login_requests` na async. PKCE flow:
1. `GET authorize` (PKCE: `code_verifier`, `code_challenge`, `state`, `device_id`),
2. pobranie strony logowania + CSRF (`__RequestVerificationToken`),
3. `POST /Account/Login` z loginem/hasłem,
4. obsługa redirectów; przy URL z `mfa` → **rzuć `MfaRequired`** z kontekstem (`mfa_code_id`, `csrf`, `return_url`, cookies) zamiast czytać `stdin`,
5. `async_submit_mfa(code)` → `POST /Account/Mfa` → `code` → `_exchange_code_for_token`.

> **Kluczowa różnica vs CLI:** medichaser czyta kod MFA ze `stdin` (`select.select`). W HA przerywamy login wyjątkiem `MfaRequired`, a config flow pokazuje krok z polem na kod SMS. Kontekst MFA (cookies, `mfa_code_id`, `csrf`, `return_url`) trzymamy między krokami flow.
- `async_refresh_token()` — grant `refresh_token`; przy `invalid_grant` → `InvalidGrant` → reauth.
- `async_logout()` — wywołanie `revocation_endpoint` przy usuwaniu wpisu (czyste odpięcie).
- `device_id` / `User-Agent` — generowane raz, w `Store`/`entry.data` (jak `device_id.json`/`device_ua.json` w CLI; zachować nagłówki `Sec-*`/UA dla zaufania urządzenia).

### `MedicoverClient`
- `async_find_appointments(...)` → `/appointments/api/search-appointments/slots` (jak w CLI; filtr `end_date` po stronie klienta).
- `async_find_filters(region=None, specialty=None)` → `/appointments/api/search-appointments/filters` → `regions`/`specialties`/`clinics`/`doctors` (pary `id`/`value`).
- `async_get_personal_data()` → **`/personal-data/api/personal`** → imię i nazwisko pacjenta (do nazwy wpisu).
- Przy 401/403 → `async_refresh_token()` i ponów (odpowiednik `ExpiredToken` + retry).

### `const.py`
- `DOMAIN = "medicover"`, `LANGUAGES = {4:"Polski",6:"Angielski",60:"Ukraiński"}`, `DEFAULT_SCAN_INTERVAL` (np. 10 min), klucze `CONF_*`, klucze `Store`.

---

## 5. Cache filtrów i danych pacjenta + przycisk odświeżania (`store.py`, `button.py`)

Realizacja Twojej uwagi: listy pobierają się po auth, lądują w storage, jest guzik do odświeżenia.

- **Po zalogowaniu** (`async_setup_entry`, jeśli brak cache lub stary):
  1. `async_get_personal_data()` → zapisz imię/nazwisko; **ustaw tytuł config entry** = `„Jan Kowalski"` (`hass.config_entries.async_update_entry(entry, title=...)`),
  2. `async_find_filters()` → zapisz `regions`/`specialties` (+ kliniki/lekarze pobierane kaskadowo przy wyborze) do `Store`.
- **`FiltersStore`** (`Store` z wersją): klucz per konto, trzyma `personal`, `regions`, `specialties`, ewentualnie cache `clinics`/`doctors`, oraz `last_refreshed`.
- **`ButtonEntity` „Odśwież dane Medicover"** (per konto): `async_press()` → ponownie pobiera personal-data + filtry, aktualizuje `Store` i tytuł wpisu, czyści zależne cache. Dla laika: jedno kliknięcie w UI.
- Subentry flow **czyta dropdowny z `Store`** (natychmiast); jeśli cache pusty/stary — dociąga z API w tle.

---

## 6. Config Flow — konto (`config_flow.py`)

```
async_step_user        # login + hasło
   ↓ (MfaRequired)
async_step_mfa         # pole na 6-cyfrowy kod SMS
   ↓
[pobierz personal-data]  # imię i nazwisko
   ↓
async_create_entry(title="Jan Kowalski", data={token, device_id, ua})
```

- `async_step_user`: formularz `username` + `password` → `async_login()`. Sukces bez MFA → entry; `MfaRequired` → `async_step_mfa`; `AuthError` → `errors={"base":"invalid_auth"}`.
- `async_step_mfa`: pole `mfa_code` (walidacja 6 cyfr) → `async_submit_mfa()`; błąd → `errors={"base":"invalid_mfa"}`.

- `unique_id` = identyfikator pacjenta z personal-data (lub username) → `_abort_if_unique_id_configured()` (brak duplikatów konta).
- **Reauth** (`async_step_reauth` / `_confirm`): wyzwalany `ConfigEntryAuthFailed` z koordynatora przy `InvalidGrant`; ponowne logowanie → `async_update_reload_and_abort`.
- **Options flow** (per konto): `scan_interval`.
- **Wiele kont:** każde wywołanie flow = osobny entry (inny `unique_id` = inny pacjent), własny koordynator i token.

---

## 7. Subentry Flow — szukajki (`config_flow.py`)

`async_get_supported_subentry_types` → `{"search": SearchSubentryFlowHandler}`.

`SearchSubentryFlowHandler(ConfigSubentryFlow)`:
- `async_step_user` — prosty kreator z dropdownami **zasilanymi z `Store`**:
  1. **Region** (`SelectSelector`, wymagane),
  2. **Specjalność** (`SelectSelector`, wymagane),
  3. po wyborze → **Klinika** (opcj.) i **Lekarz** (opcj.) zawężone kaskadowo,
  4. **Język** (opcj.), **Data od / Data do** (opcj., `DateSelector`),
  5. `slot_search_type` (domyślnie 0; raczej ukryte/„zaawansowane").
- `async_step_reconfigure` — edycja szukajki.
- Auto-tytuł: „Kardiolog · Warszawa · od 1 lip".
- Walidacja minimalna: `region` + `specialty`.
- **Pod maską:** dropdowny pokazują nazwy, zapisują ID — user nie widzi numerów.

---

## 8. Koordynator (`coordinator.py`)

`MedicoverCoordinator(DataUpdateCoordinator[dict[str, list[Slot]]])` — jeden na konto:
- `update_interval` z options,
- `_async_update_data()`: refresh token (→ `ConfigEntryAuthFailed` przy `InvalidGrant`) → dla każdej szukajki `async_find_appointments(...)` → `dict[subentry_id, sloty]`; błędy API → `UpdateFailed`.
- Współdzielenie tokenu/sesji = mniej logowań (rozwiązuje problem `FileLock` z CLI).

---

## 9. Sensory (`sensor.py`)

`async_setup_entry`: dla każdego subentry → `MedicoverSearchSensor` z `config_subentry_id=subentry.subentry_id`.

`MedicoverSearchSensor(CoordinatorEntity, SensorEntity)`:
- **stan** = liczba wolnych slotów (`state_class=measurement`) — łatwa automatyzacja „> 0".
- **atrybuty**: `appointments` (data/klinika/lekarz/specjalność/języki — format jak `Notifier.format_appointments`), `earliest`, `search` (parametry), `last_update`.
- `unique_id = f"{entry_id}_{subentry_id}"`; `available` wg stanu koordynatora.

### Notyfikacje — po stronie usera (automatyzacja HA)
```yaml
automation:
  - alias: "Medicover - wolny termin kardiolog"
    trigger:
      - platform: numeric_state
        entity_id: sensor.medicover_kardiolog_warszawa
        above: 0
    action:
      - service: notify.mobile_app_telefon
        data:
          title: "Wolny termin Medicover!"
          message: >
            Najwcześniej: {{ state_attr('sensor.medicover_kardiolog_warszawa','earliest') }}
```

---

## 10. `__init__.py` (szkielet)

```python
type MedicoverConfigEntry = ConfigEntry[MedicoverRuntimeData]

async def async_setup_entry(hass, entry: MedicoverConfigEntry) -> bool:
    session = async_get_clientsession(hass)
    auth = MedicoverAuth(session, entry.data, store=...)         # endpointy z discovery
    client = MedicoverClient(auth)

    # dane pacjenta -> nazwa wpisu, jeśli brak/odświeżenie
    personal = await client.async_get_personal_data()
    if entry.title != personal.full_name:
        hass.config_entries.async_update_entry(entry, title=personal.full_name)

    await filters_store.async_ensure(client)                    # cache regionów/specjalności

    coordinator = MedicoverCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()        # ConfigEntryAuthFailed -> reauth
    entry.runtime_data = MedicoverRuntimeData(auth, client, coordinator, filters_store)
    await hass.config_entries.async_forward_entry_setups(entry, [Platform.SENSOR, Platform.BUTTON])
    return True

async def async_unload_entry(hass, entry):
    return await hass.config_entries.async_unload_platforms(entry, [Platform.SENSOR, Platform.BUTTON])
```

Listener na zmianę subentries (dodanie/usunięcie szukajki) → reload entry. Przy `async_remove_entry` → `auth.async_logout()` (revocation).

---

## 11. Etapy implementacji (kolejność)

1. **Szkielet** — `manifest.json`, `const.py`, `__init__.py`, HACS; pusty config flow.
2. **Discovery + auth** — pobranie `.well-known`, PKCE login (login/hasło); `async_refresh_token`.
3. **Personal-data + nazwa wpisu** — `/personal-data/api/personal` → tytuł = imię i nazwisko; `unique_id` z personal-data.
4. **MFA / reauth** — `async_step_mfa` (kod SMS); reauth na `invalid_grant`.
5. **`MedicoverClient` + cache filtrów** — `find_filters` → `Store`; `find_appointments`.
6. **Przycisk „Odśwież dane Medicover"** — `ButtonEntity` aktualizujący filtry + personal-data.
7. **Subentry flow** — prosty kreator z dropdownami z cache, kaskadowe zawężanie.
8. **Koordynator** — odpytywanie wszystkich szukajek konta.
9. **Sensory** — encja per szukajka + atrybuty.
10. **Tłumaczenia** — `pl.json` (priorytet), `en.json`, czytelne komunikaty błędów.
11. **Wiele kont** — test 2+ pacjentów równolegle (osobne tokeny/koordynatory/cache).
12. **Diagnostyka + dopracowanie** — `diagnostics.py` (redakcja tokenów/danych), retry/backoff, logi.

---

## 12. Ryzyka i uwagi

- **Scope `rdol_api`:** discovery sugeruje, że API może wymagać tego scope’u (CLI używa tylko `openid offline_access profile`). Do przetestowania przy realnym żądaniu o terminy.
- **Trusted device / MFA:** stałe `device_id` pozwala pomijać MFA przy kolejnych logowaniach (jak `IsTrustedDevice=true` w CLI). Reauth z MFA na zapas.
- **Aktualność cache filtrów:** Medicover czasem dodaje kliniki/lekarzy → przycisk odświeżania + ewentualnie auto-refresh raz na dobę.
- **Rate limiting:** rozsądny `scan_interval` (min. 5–10 min) + retry/backoff (jak `Retry` w CLI); zachować nagłówki UA/`Sec-*`.
- **Nieoficjalne API:** flow logowania i endpointy mogą się zmienić — `api.py` izolowany i łatwy do aktualizacji; endpointy z discovery zmniejszają ryzyko zaszytych URL-i.
- **Selenium pominięte** — tylko ścieżka HTTP/PKCE (`login_requests`); Selenium nieodpowiedni w HA.
- **Bezpieczeństwo:** hasło tylko w config flow (ścieżka B) → wymieniane na token; w `entry.data` refresh token; `diagnostics.py` redaguje tokeny/dane osobowe; przy usunięciu wpisu — `revocation`.
- **Wiele kont rodziny:** nazwy = pacjenci rozwiązują problem rozróżnienia; rozważyć `DeviceInfo` per konto, by grupować encje pod „urządzeniem" pacjenta w UI.

---

## 13. Testy

Logika integracji jest w pełni odizolowana od sieci (cała komunikacja z Medicover idzie przez `aiohttp` w `api.py`), więc nadaje się do testów automatycznych bez ręcznego klikania w HA i bez odpytywania prawdziwego API.

### Framework

Standardem jest **`pytest-homeassistant-custom-component`** (PHACC) — ten sam zestaw fixtures co w rdzeniu Home Assistant, aktualizowany codziennie do najnowszego wydania (także beta). Daje:

- `hass` — zamockowaną instancję HA w pamięci, w której odpalamy config flow, setup wpisu, koordynator i encje,
- `enable_custom_integrations` — fixture wymagany, by HA „widziało" `custom_components/medicover`,
- helpery z core: `MockConfigEntry`, `async_setup_component`, snapshoty stanów encji.

Wersję PHACC pinujemy do wydania HA, pod które celujemy.

### Zasada: zero ruchu do prawdziwego Medicover

Warstwę HTTP mockujemy (`aioresponses` lub wstrzyknięty fałszywy `MedicoverClient`) i odgrywamy zapisane odpowiedzi: stronę logowania z CSRF, redirect na MFA, wymianę kodu na token, `personal-data`, `filters`, `slots`. Realne odpowiedzi API zapisujemy raz jako pliki w `tests/fixtures/` (po redakcji danych osobowych) i z nich korzystamy.

### Zakres testów

| Obszar | Przypadki |
|---|---|
| **Config flow** | happy path (login → token → wpis o tytule „Jan Kowalski"); ścieżka MFA (`MfaRequired` → `async_step_mfa` → sukces); błędne hasło (`invalid_auth`); błędny kod SMS (`invalid_mfa`); duplikat konta (`already_configured`); reauth przy `invalid_grant` |
| **Subentry flow** | dodanie szukajki z dropdownów zasilanych z cache; walidacja że region + specjalność są wymagane; poprawny auto-tytuł szukajki |
| **Coordinator** | `_async_update_data` zwraca poprawny `dict[subentry_id, sloty]`; `InvalidGrant` → `ConfigEntryAuthFailed`; błąd API → `UpdateFailed` |
| **Sensor** | stan = liczba slotów; atrybuty (`earliest`, `appointments`, `search`); aktualizacja po odświeżeniu koordynatora |
| **Button** | `async_press` odświeża cache filtrów i tytuł wpisu |
| **Cache (`Store`)** | zapis/odczyt filtrów i danych pacjenta; zachowanie przy pustym/starym cache |

### Struktura

```
tests/
├── conftest.py          # auto_enable_custom_integrations, fixtury z mockami API
├── test_config_flow.py  # login, MFA, reauth, duplikaty
├── test_init.py         # setup/unload entry, nazwa = pacjent
├── test_coordinator.py  # cykl odpytania, obsługa błędów/tokenu
├── test_sensor.py       # stan i atrybuty encji
├── test_button.py       # odświeżanie danych
└── fixtures/            # zapisane JSON-y odpowiedzi (slots, filters, personal)
```

### Uruchamianie i CI

Lokalnie: `pytest`. W repo (HACS) standardem jest **GitHub Actions** z matrycą po wersjach HA — testy odpalają się przy każdym pushu/PR. Dodatkowo warto włączyć `hassfest` i walidację HACS jako osobne kroki CI.

### Smoke-test na żywo (uzupełnienie, nie zamiennik)

Przed wrzuceniem do własnego HA — kontener `ghcr.io/home-assistant/home-assistant:stable` z podmontowanym `custom_components/medicover`. Szybsza pętla niż reinstalacja w produkcyjnym HA; służy do ręcznej weryfikacji realnego logowania/MFA, których nie obejmują mocki.

---

## 14. Future improvements (do zbadania później)

- **Device code flow zamiast login/hasło w HA.** Discovery wystawia `device_authorization_endpoint` i grant `urn:ietf:params:oauth:grant-type:device_code`. Zaleta: HA nigdy nie widzi hasła ani kodu SMS — user loguje się na stronie Medicover (w przeglądarce), a HA tylko odpytuje token. Do zbadania:
  - czy client `web` faktycznie akceptuje ten grant (czy nie wymaga osobnego client_id),
  - jak wygląda UX kroku (pokazanie `user_code` + `verification_uri` w config flow, polling z `interval`/`expires_in`),
  - czy istniejąca sesja w przeglądarce skraca to do jednego „potwierdź".
  - **Uwaga z rozmowy:** device flow i tak wymaga pełnego logowania (username + hasło + MFA) — przenosi je tylko poza HA, nie usuwa. Korzyść jest czysto bezpieczeństwowa.
- **Scope `rdol_api`** — sprawdzić, czy poprawia/jest wymagany do dostępu do API terminów.
- **`userinfo_endpoint`** — ewentualnie do pobrania `sub` jako stabilnego `unique_id` konta (zamiast/obok danych z personal-data).
- **Auto-refresh cache filtrów** — np. raz na dobę w tle, obok ręcznego przycisku.
- **`DeviceInfo` per pacjent** — grupowanie sensorów szukajek pod „urządzeniem" reprezentującym pacjenta w UI.
- **Rezerwacja terminu z HA** — obecnie tylko odczyt; w przyszłości akcja bookowania (wymaga dodatkowych endpointów i ostrożności).

- **Dłuższa żywotność sesji — model „apki mobilnej".** Obecnie udajemy klienta `client_id: "web"`, któremu serwer celowo daje krótkie tokeny: access ~3 min, refresh w wąskim oknie (< ~10 min). Dlatego trzeba ciągle odświeżać (`TokenKeepAlive`, ~co 150s), a gdy HA jest wyłączone dłużej niż okno refresh tokena, sesja umiera i wraca prośba o reauth (MFA). Apka mobilna tego problemu nie ma — warto zbadać dlaczego i czy da się to wykorzystać.
  - **MFA = jednorazowy enrollment urządzenia, nie bramka per token.** Przy potwierdzaniu MFA `medichaser.py` wysyła `Input.IsTrustedDevice: "true"` + `Input.DeviceName` + ciasteczko `__mcc = device_id`. Serwer zapamiętuje `device_id` jako zaufane urządzenie. Refresh tokena nigdy nie dotyka MFA; SMS jest tylko przy pierwszym zaufaniu urządzeniu.
  - **Apka to inny klient OAuth.** Klient natywny (osobny `client_id`, np. „mobile"/„ios"/„android") jest zwykle skonfigurowany z dużo dłuższymi czasami życia tokenów (godziny/dni), bo trzyma je w bezpiecznym magazynie (Keychain/Keystore). Krótkie tokeny klienta „web" to świadoma decyzja bezpieczeństwa dla przeglądarki, nie ograniczenie API jako takiego.
  - **Trzy opcje do rozważenia (kompromisy):**
    - **A. Status quo** — klient „web" + keep-alive. Działa, ale nie przeżywa dłuższego wyłączenia HA (RT wygasa).
    - **B. Udawać klienta mobilnego** — gdyby ustalić jego `client_id`, dostalibyśmy długowieczne tokeny → koniec problemu z restartami. Ryzyka: inny `redirect_uri` (custom scheme `medicover://`), możliwy `client_secret`, app/device attestation, certificate pinning, podpisywanie żądań — trudniejsze do odtworzenia, kruche przy aktualizacjach apki, bliżej „nadużycia" API.
    - **C. Ciche ponowne logowanie na zaufanym urządzeniu** — zamiast prosić o MFA gdy RT umrze, spróbować automatycznie zalogować się zapisanym hasłem na tym samym (zaufanym) `device_id`, który powinien pomijać MFA; interaktywny reauth dopiero gdy to zawiedzie. Rozwiązuje realny ból (reauth po wyłączeniu HA) bez reverse-engineeringu klienta mobilnego. Koszt: trzeba **przechowywać hasło** (dziś trzymamy tylko tokeny — świadomy kompromis bezpieczeństwa). **Do zweryfikowania empirycznie:** czy ponowne `authorize` na zapisanym `device_id` faktycznie pomija MFA dla klienta „web" (medichaser zakłada, że tak — stąd persystencja `device_id`).
  - Praktycznie najciekawsza jest **C** — najwięcej korzyści przy najmniejszym ryzyku.

---

## Źródła

- [Support for config subentries — Home Assistant Developer Docs (2025-02-16)](https://developers.home-assistant.io/blog/2025/02/16/config-subentries/)
- [Config Subentries — home-assistant/architecture Discussion #1070](https://github.com/home-assistant/architecture/discussions/1070)
- [Medicover OIDC discovery — `/.well-known/openid-configuration`](https://login-online24.medicover.pl/.well-known/openid-configuration) (authorization/token/userinfo/revocation/device_authorization endpoints, scope `rdol_api`)
- `medichaser.py` (rafsaf/medichaser) — źródłowa logika API (PKCE login, MFA, refresh, `search-appointments/slots`, `/filters`)
- `/personal-data/api/personal` — dane pacjenta (imię/nazwisko) do nazwy wpisu
