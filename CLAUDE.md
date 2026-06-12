# CLAUDE.md — Medicover Home Assistant Integration

Kontekst i twarde reguły dla pracy nad tym repo. Czytane automatycznie w każdej sesji.

## Czym jest ten projekt

Custom component Home Assistant odpytujący nieoficjalne API Medicover (online24) o wolne
terminy wizyt. Obsługuje wiele kont (per pacjent) i wiele „szukajek" (region / klinika /
specjalista / lekarz / data). Każda szukajka = sensor, na którym użytkownik buduje
automatyzacje i notyfikacje.

## Źródło prawdy

- **`PLAN_integracji_medicover_ha.md`** — pełny plan architektury i etapów. Trzymaj się
  decyzji z planu. Jeśli chcesz odejść od planu, najpierw to zaproponuj i uzasadnij.
- **`medichaser.py`** — referencyjna implementacja logiki API (PKCE login, MFA, refresh
  tokenu, endpointy `search-appointments/slots` i `/filters`). Z niej portujemy logikę
  sieciową na async. NIE jest częścią integracji — to tylko wzorzec.

## Twarde reguły

1. **Async/aiohttp wszędzie.** Cała komunikacja sieciowa przez `async_get_clientsession(hass)`.
   Żadnego `requests`, `selenium`, ani blokujących wywołań w pętli zdarzeń.
2. **Najnowsze techniki HA.** Config subentries dla szukajek, `entry.runtime_data`
   (typowany `ConfigEntry`), `DataUpdateCoordinator`, `Store` na cache. Bez konfiguracji YAML.
3. **Prostota dla osób nietechnicznych.** UI to wyłącznie dropdowny z czytelnymi nazwami
   (nigdy ID), minimum wymaganych pól (region + specjalność), polskie tłumaczenia, czytelne
   komunikaty błędów. Patrz sekcja 2 planu.
4. **Endpointy z OIDC discovery** (`/.well-known/openid-configuration`), nie zaszyte na sztywno.
5. **Logowanie jak w `medichaser.py`** — login + hasło + krok MFA (kod SMS) w config flow.
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
- **Każdy etap kończ zielonym `pytest`.** Preferuj TDD: najpierw test danego zachowania,
  potem implementacja aż przechodzi.
- Zakres i struktura testów — sekcja 13 planu.

## Sposób pracy

- Pracuj **etapami z sekcji 11 planu** (1→12), pojedynczo, nie wszystko naraz.
- Używaj **trybu plan** przed większym etapem; po akceptacji implementuj.
- **Commituj po każdym działającym etapie** (zwięzłe, opisowe commity).
- Trudne etapy (port logowania PKCE z `medichaser.py`) rób w izolacji, z plikiem jako wzorcem.

## Stos i wersje

- Python: **3.13**
- Struktura katalogów: patrz sekcja 3 planu (`custom_components/medicover/...`, `tests/...`).
- Dystrybucja: HACS (custom repository), plus `hacs.json` i `manifest.json`.

## Polecenia

```bash
# testy
pytest

# pojedynczy plik / test
pytest tests/test_config_flow.py -k mfa -vv

# walidacja manifestu (lokalnie, jeśli dostępne)
python -m script.hassfest  # zwykle uruchamiane w CI, nie lokalnie
```

## Notyfikacje

Integracja może **opcjonalnie** wysyłać powiadomienie per szukajka: w kreatorze szukajki jest
dropdown „Powiadomienie" (`EntitySelector(domain="notify")` → encje `notify.*`). Gdy wybrany,
koordynator po znalezieniu **nowych** terminów (diff względem poprzedniego odpytania) woła
`notify.send_message` (best-effort — błąd nie wywala pollu). Bez wyboru — zero powiadomień;
automatyzacja HA na sensorze pozostaje alternatywą. Zewnętrznych notyfikatorów z `medichaser.py`
(pushbullet/telegram itd.) nadal NIE portujemy — korzystamy z natywnego `notify` HA.
Uzasadnienie odejścia od pierwotnej reguły: nadrzędna wartość „prostota dla osób nietechnicznych".

## Czego NIE robić

- Nie commitować realnych danych logowania, tokenów ani niezredagowanych odpowiedzi API.
- Nie używać `hass.data[DOMAIN]` — zamiast tego `entry.runtime_data`.
- Nie dodawać zależności od przeglądarki/Selenium.
