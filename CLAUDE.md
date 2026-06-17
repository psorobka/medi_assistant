# CLAUDE.md — Medicover Home Assistant Integration

Kontekst i twarde reguły dla pracy nad tym repo. Czytane automatycznie w każdej sesji.

## Czym jest ten projekt

Custom component Home Assistant odpytujący nieoficjalne API Medicover (online24) o wolne
terminy wizyt. Obsługuje wiele kont (per pacjent) i wiele „szukajek" (region / klinika /
specjalista / lekarz / data). Każda szukajka = sensor, na którym użytkownik buduje
automatyzacje i notyfikacje.

## Źródło prawdy

Integracja jest już zbudowana — źródłem prawdy jest **kod w
`custom_components/medi_assistant/`**. Logika sieciowa (PKCE login, MFA, refresh tokenu,
endpointy `search-appointments/slots` i `/filters`) została sportowana na async z
referencyjnego [`medichaser`](https://github.com/rafsaf/medichaser) (rafsaf) — to tylko
upstreamowy wzorzec, nie część integracji.

## Mapa kodu (`custom_components/medi_assistant/`)

- `api.py` — `MedicoverAuth` (PKCE login, MFA, refresh tokenu, ciche re-login) +
  `MedicoverClient` (slots / filters / personal-data).
- `coordinator.py` — `DataUpdateCoordinator`: poll szukajek, diff nowych terminów, notyfikacje.
- `token_keepalive.py` — proaktywny refresh tokenu przed wygaśnięciem; retry z backoffem
  przed reauth.
- `config_flow.py` — `ConfigFlow` (konto + MFA + reauth) i `SubentryFlow` (kreator szukajek).
- `store.py` — cache filtrów / danych pacjenta; `button.py` — „Odśwież dane Medicover".
- `sensor.py` — sensor per szukajka; `diagnostics.py` — redakcja tokenów/danych.
- `const.py` — `DOMAIN`, klucze `entry.data`/`Store`; `exceptions.py` — `AuthError` /
  `MfaRequired` / `InvalidGrant`.
- Tokeny żyją w `entry.data` (nie w `Store`); auth ma własną sesję z cookie jar.

## Twarde reguły

1. **Async/aiohttp wszędzie.** Cała komunikacja sieciowa przez `async_get_clientsession(hass)`.
   Żadnego `requests`, `selenium`, ani blokujących wywołań w pętli zdarzeń.
2. **Najnowsze techniki HA.** Config subentries dla szukajek, `entry.runtime_data`
   (typowany `ConfigEntry`), `DataUpdateCoordinator`, `Store` na cache. Bez konfiguracji YAML.
3. **Prostota dla osób nietechnicznych.** UI to wyłącznie dropdowny z czytelnymi nazwami
   (nigdy ID), minimum wymaganych pól (region + specjalność), polskie tłumaczenia, czytelne
   komunikaty błędów.
4. **Endpointy z OIDC discovery** (`/.well-known/openid-configuration`), nie zaszyte na sztywno.
5. **Logowanie jak w `medichaser`** — login + hasło + krok MFA (kod SMS) w config flow.
   Zamiast czytać kod ze `stdin`, rzucamy `MfaRequired` i obsługujemy krok we flow. Device
   code flow to „Future improvements", nie teraz.
6. **Nazwa wpisu = imię i nazwisko pacjenta** z `/personal-data/api/personal`.
7. **Listy filtrów w `Store`** + przycisk „Odśwież dane Medicover" (`ButtonEntity`).

## Reguły testów

- **Nigdy nie odpytuj prawdziwego API w testach.** Mockuj warstwę HTTP (`aioresponses`) lub
  wstrzykuj fałszywy `MedicoverClient`. Odpowiedzi trzymaj jako pliki w `tests/fixtures/`
  (po redakcji danych osobowych).
- Framework: **`pytest-homeassistant-custom-component`** (fixtury `hass`,
  `enable_custom_integrations`, `MockConfigEntry`).
- **Kończ pracę zielonym `pytest`.** Preferuj TDD: najpierw test danego zachowania,
  potem implementacja aż przechodzi.
- Istniejące testy w `tests/` pokazują konwencję (fixtury, mocki API, struktura).

## Sposób pracy

- Używaj **trybu plan** przed większą zmianą; po akceptacji implementuj.
- **Commituj po każdej działającej zmianie** (zwięzłe, opisowe commity).
- Trudne zmiany w logice logowania PKCE/MFA rób w izolacji, z upstreamowym
  `medichaser` jako wzorcem.

## Stos i wersje

- Python: **3.13**
- Struktura katalogów: `custom_components/medi_assistant/...`, testy w `tests/...`.
- Dystrybucja: HACS (custom repository), plus `hacs.json` i `manifest.json`.

## Polecenia

```bash
# testy
pytest
pytest tests/test_config_flow.py -k mfa -vv   # pojedynczy plik / test

# lint + format (ruff przypięty w requirements_dev.txt)
pip install -r requirements_dev.txt
ruff check . && ruff format .
```

CI (push / PR do `main`) jest bramką: **ruff (check + format --check) + pytest + hassfest +
HACS**. Wersja ruffa przypięta w `requirements_dev.txt` (ta sama lokalnie i w CI), więc
`ruff format` lokalnie = zielony `--check` w CI. Hassfest/HACS walidują się tylko w CI.

## Notyfikacje

Integracja może **opcjonalnie** wysyłać powiadomienie per szukajka: w kreatorze szukajki jest
dropdown „Powiadomienie" (`EntitySelector(domain="notify")` → encje `notify.*`). Gdy wybrany,
koordynator po znalezieniu **nowych** terminów (diff względem poprzedniego odpytania) woła
`notify.send_message` (best-effort — błąd nie wywala pollu). Bez wyboru — zero powiadomień;
automatyzacja HA na sensorze pozostaje alternatywą. Zewnętrznych notyfikatorów z `medichaser`
(pushbullet/telegram itd.) nadal NIE portujemy — korzystamy z natywnego `notify` HA.
Uzasadnienie odejścia od pierwotnej reguły: nadrzędna wartość „prostota dla osób nietechnicznych".

## Czego NIE robić

- Nie commitować realnych danych logowania, tokenów ani niezredagowanych odpowiedzi API.
- Nie używać `hass.data[DOMAIN]` — zamiast tego `entry.runtime_data`.
- Nie dodawać zależności od przeglądarki/Selenium.
