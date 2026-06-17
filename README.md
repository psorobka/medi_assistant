# Medi Assistant — Medicover appointments for Home Assistant

[![CI](https://github.com/psorobka/medi_assistant/actions/workflows/ci.yml/badge.svg)](https://github.com/psorobka/medi_assistant/actions/workflows/ci.yml)
[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)

A Home Assistant custom integration that polls the unofficial Medicover **online24**
API for free appointment slots and exposes each saved search as a sensor you can
build automations and notifications on.

> ⚠️ **Unofficial integration.** It talks to Medicover's private `online24` API
> (the same one the website/app uses). It is not affiliated with or endorsed by
> Medicover and may break if they change their API. Use at your own risk.

---

## Features

- 🔐 **Login like the app** — e‑mail + password + MFA (SMS *or* e‑mail code) handled in the config flow.
- 👨‍👩‍👧 **Multiple accounts** — one config entry per patient (named after the patient).
- 🔎 **Multiple searches per account** — each search ("szukajka") = one sensor.
- 🧭 **Region‑first wizard** — region → specialty (scoped to the region) → optional clinic / doctor / visit language / date range / notifications.
- 🔔 **Built‑in notifications (optional)** — pick one or more notify targets per search; you get a push when a **new** slot appears (deduplicated across restarts).
- 📅 **Readable sensor** — state shows the soonest slot (`date · doctor · clinic`); attributes carry the full list, count and earliest date.
- ♻️ **Self‑healing auth** — keeps the short‑lived token alive and silently re‑logs in after long downtime (no constant MFA prompts).
- 🌍 **Polish & English** UI translations, dropdowns with human‑readable names (never raw IDs).
- 🛠️ No YAML — everything via the UI. Config subentries, `DataUpdateCoordinator`, typed `runtime_data`, cached filter lists.

## Requirements

- Home Assistant **2025.2.0+** (uses config subentries).
- A Medicover account with online24 access.

## Installation

### HACS (recommended)

1. HACS → **Integrations** → ⋮ → **Custom repositories**.
2. Add `https://github.com/psorobka/medi_assistant` with category **Integration**.
3. Install **Medi Assistant**, then **restart Home Assistant**.

### Manual

Copy `custom_components/medi_assistant` into your HA `config/custom_components/`
directory and restart Home Assistant.

## Setup

### Add an account

**Settings → Devices & Services → Add Integration → Medi Assistant**

1. Enter your Medicover **e‑mail** and **password**.
2. Enter the **verification code** Medicover sends (SMS or e‑mail, depending on
   your account's MFA method).

The entry is named after the patient (fetched from `/personal-data`).

> The password is stored in the config entry so the integration can silently
> re‑authenticate after long downtime (see [Authentication & sessions](#authentication--sessions)).
> If you'd rather not store it, you can still use the integration — you'll just be
> asked to re‑authenticate occasionally.

### Add a search ("szukajka")

On the integration card: **Add szukajkę**, then a 3‑step wizard:

1. **Region** (defaults to the region of your last search).
2. **Specialty** — the list is loaded **for the chosen region** (Medicover's
   global list is incomplete, so it's fetched per region).
3. **Details (all optional):** clinic, doctor, visit language, "search from"/"until"
   dates, and **notification target(s)**.

Each search becomes a sensor and can be edited or deleted at any time —
sensors are created/removed instantly, without reloading the integration.

## The sensor

- **State** — soonest available slot, e.g. `2026-07-01 10:00 · dr Jan Nowak · Klinika Centrum`, or `Brak terminów` ("no slots") when empty.
- **Attributes:**
  - `count` — total number of free slots matching the search.
  - `earliest` — ISO datetime of the soonest slot.
  - `appointments` — list of up to 20 soonest slots (`date`, `clinic`, `doctor`, `specialty`, `languages`).
  - `appointments_truncated` — `true` if more than 20 slots matched.
  - `search` — the saved search parameters.

### Example automation

```yaml
automation:
  - alias: "Medicover — cardiologist slot found"
    trigger:
      - platform: numeric_state
        entity_id: sensor.your_search   # find the exact id in Developer Tools → States
        attribute: count
        above: 0
    action:
      - service: notify.mobile_app_your_phone
        data:
          title: "Free Medicover slot"
          message: "{{ states('sensor.your_search') }}"
```

> You don't need this if you set a notification target on the search — the
> integration sends it for you.

## Notifications

In a search's **Details** step, set one or more **notification targets**. The
dropdown lists both modern `notify.*` **entities** (e.g. `notify.mobile_app_*`)
and legacy `notify.*` **services** (telegram, pushbullet, `notify.persistent_notification`, …);
you can also type a target manually.

Behavior:

- A slot is announced **once, on first detection** (including right after you add
  a search when something is already free).
- Already‑announced slots are remembered **on disk**, so HA restarts don't
  re‑spam you.
- The message lists the new slots (`date · doctor · clinic`, capped at 15 with an
  "…and N more" line). Title is `Patient — search`.

> `notify.persistent_notification` is the easiest target to test with — it shows
> up in HA's notifications bell, no mobile app needed.

## Options

Integration **Configure** → **Scan interval** (5–120 min, default **10**). Changing
it reloads the entry.

## Refresh button

Each account has a **Refresh Medicover data** button that re‑fetches the filter
lists (regions/specialties) and the patient name on demand.

## Authentication & sessions

Medicover's web tokens are short‑lived (access token ~3 min, refresh token with a
small sliding window). The integration:

- runs a **keep‑alive** that refreshes the token shortly before it expires,
- on a rejected refresh, performs a **silent re‑login** with the stored
  credentials (the device is trusted after the first MFA, so no new code is
  needed),
- persists the login session cookies so refresh works after a restart,
- **retries the silent re‑login a few times** (with backoff) on a transient
  failure before falling back to an interactive re‑authentication prompt — a
  single hiccup no longer triggers a reauth notification.

You'll only be asked to re‑authenticate (with a fresh MFA code) if the password
changes or Medicover revokes the device's trust.

## Troubleshooting

- **"Re‑authentication required" notification** — open it and sign in again
  (e‑mail + password + verification code).
- **A specialty/clinic is missing for a region** — the lists are region‑scoped;
  if it's not offered in that region it won't appear. Use the **Refresh Medicover
  data** button if lists look stale.
- **No notification right after adding a search** — you only get notified about
  **new** slots; the very first detection counts, so if there were already free
  slots you should get one on the next poll.
- **Enable debug logs:**
  ```yaml
  logger:
    logs:
      custom_components.medi_assistant: debug
  ```

## Privacy & security

Credentials, OAuth tokens and login session cookies are stored in the Home
Assistant config entry (standard for integrations that log in on your behalf).
Diagnostics downloads redact tokens, password and cookies. Nothing is sent
anywhere except Medicover's own API.

## Development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements_test.txt -r requirements_dev.txt
pytest                 # test suite (loads the integration into a real hass)
ruff check .           # lint
ruff format .          # format
```

Tests never hit the real API — the HTTP layer is mocked and fixtures live in
`tests/fixtures/` (redacted).

**CI** (GitHub Actions, on every push / PR to `main`) runs four jobs: ruff lint +
`ruff format --check`, the pytest suite, **hassfest** manifest validation and
**HACS** validation. `ruff` is pinned in `requirements_dev.txt` so its version is
identical locally and in CI. **Dependabot** opens weekly, grouped PRs to bump the
GitHub Actions and the Python dev/test dependencies.

## Credits

Network/login logic (PKCE, MFA, token refresh, the `search-appointments`
endpoints) was ported to async from
[`medichaser`](https://github.com/rafsaf/medichaser) by rafsaf — thanks!

Built with the help of [Claude Code](https://claude.com/claude-code) (Anthropic). 🤖

## License

Licensed under the **GNU General Public License v3.0** — see [LICENSE](LICENSE).
Because the network/login logic is derived from
[`medichaser`](https://github.com/rafsaf/medichaser) (GPL‑3.0), this project
inherits the same license.

## Disclaimer

This project is not affiliated with Medicover. It uses an undocumented API that
may change or break at any time. No warranty of any kind.
