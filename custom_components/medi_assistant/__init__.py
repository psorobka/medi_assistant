from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import MedicoverAuth, MedicoverClient, _DEFAULT_UA
from .const import (
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_USERNAME,
    DEFAULT_SCAN_INTERVAL,
    ENTRY_DEVICE_ID,
    ENTRY_DEVICE_UA,
    NOTIFY_ACTION_EVENT,
    SUBENTRY_TYPE_SEARCH,
)
from .coordinator import MedicoverCoordinator
from .exceptions import AuthError, InvalidGrant, MfaRequired
from .store import FiltersStore
from .token_keepalive import TokenKeepAlive

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR, Platform.BUTTON]


@dataclass
class MedicoverRuntimeData:
    auth: MedicoverAuth
    client: MedicoverClient
    coordinator: MedicoverCoordinator
    filters_store: FiltersStore
    auth_session: aiohttp.ClientSession
    keepalive: TokenKeepAlive


type MedicoverConfigEntry = ConfigEntry[MedicoverRuntimeData]


async def async_setup_entry(hass: HomeAssistant, entry: MedicoverConfigEntry) -> bool:
    _LOGGER.debug("Setting up Medicover entry '%s' (entry_id=%s)", entry.title, entry.entry_id)
    device_id = entry.data.get(ENTRY_DEVICE_ID, "")
    device_ua = entry.data.get(ENTRY_DEVICE_UA, _DEFAULT_UA)

    # Auth needs its own session with a cookie jar for PKCE/MFA flows
    auth_session = aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True))

    async def _on_tokens_updated(token_data: dict) -> None:
        hass.config_entries.async_update_entry(entry, data={**entry.data, **token_data})

    auth = MedicoverAuth(
        auth_session,
        device_id=device_id,
        device_ua=device_ua,
        token_update_callback=_on_tokens_updated,
    )
    auth.load_tokens(entry.data)
    # Stored credentials enable silent re-login when the refresh token dies.
    auth.set_credentials(entry.data.get(CONF_USERNAME), entry.data.get(CONF_PASSWORD))

    # Shared HA session for API calls (no cookies needed)
    api_session = async_get_clientsession(hass)
    client = MedicoverClient(auth, api_session)
    filters_store = FiltersStore(hass, entry.entry_id)

    try:
        if not auth.is_token_valid():
            _LOGGER.debug("Access token expired for '%s', refreshing", entry.title)
            # Self-heal across restarts: if the refresh token died during downtime,
            # silently re-login on the trusted device instead of forcing reauth.
            await auth.async_refresh_or_relogin()

        personal = await client.async_get_personal_data()
        full_name = _build_full_name(personal)
        if full_name and entry.title != full_name:
            hass.config_entries.async_update_entry(entry, title=full_name)

        await filters_store.async_ensure(client)

    except (InvalidGrant, MfaRequired, AuthError) as err:
        # invalid_grant w/o credentials, untrusted device (MFA), or bad password
        # → genuine reauth needed.
        await auth_session.close()
        raise ConfigEntryAuthFailed from err
    except Exception as err:
        await auth_session.close()
        raise ConfigEntryNotReady from err

    coordinator = MedicoverCoordinator(hass, entry, client, filters_store)
    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception:
        await auth_session.close()
        raise

    # Keep the short-lived refresh token alive by refreshing well before the
    # access token expires (independent of the slot poll interval).
    keepalive = TokenKeepAlive(hass, entry, auth)

    entry.runtime_data = MedicoverRuntimeData(
        auth=auth,
        client=client,
        coordinator=coordinator,
        filters_store=filters_store,
        auth_session=auth_session,
        keepalive=keepalive,
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Start the keep-alive only after platforms set up — if the forward raised,
    # there's no scheduled timer to leak.
    keepalive.start()
    entry.async_on_unload(keepalive.stop)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    # Listen for taps on notification action buttons (Drzemka / Usuń). The
    # event is global; the handler ignores actions for other accounts.
    entry.async_on_unload(
        hass.bus.async_listen(NOTIFY_ACTION_EVENT, coordinator.async_handle_notification_action)
    )

    n_searches = sum(
        1 for s in entry.subentries.values() if s.subentry_type == SUBENTRY_TYPE_SEARCH
    )
    _LOGGER.info(
        "Medicover account '%s' ready — %d search(es) configured",
        entry.title,
        n_searches,
    )
    return True


async def _async_update_listener(hass: HomeAssistant, entry: MedicoverConfigEntry) -> None:
    """Reload only when the scan interval changed.

    This listener fires on every config-entry update — token refreshes (~every
    2.5 min), and subentry add/remove/edit. We must NOT reload on token saves
    (would tear down the keep-alive). Subentry changes don't need a reload
    either: new search sensors are added dynamically (sensor platform), removed
    ones are cleared by HA, and edits apply live. So only an options
    (scan_interval) change warrants a reload.
    """
    runtime_data = getattr(entry, "runtime_data", None)
    if runtime_data is None:
        return
    new_interval = timedelta(minutes=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL))
    if runtime_data.coordinator.update_interval != new_interval:
        await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: MedicoverConfigEntry) -> bool:
    _LOGGER.debug("Unloading Medicover entry '%s'", entry.title)
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        runtime_data: MedicoverRuntimeData = entry.runtime_data
        if not runtime_data.auth_session.closed:
            await runtime_data.auth_session.close()
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: MedicoverConfigEntry) -> None:
    """Revoke token when integration is deleted."""
    device_id = entry.data.get(ENTRY_DEVICE_ID, "")
    device_ua = entry.data.get(ENTRY_DEVICE_UA, _DEFAULT_UA)
    async with aiohttp.ClientSession() as session:
        auth = MedicoverAuth(session, device_id=device_id, device_ua=device_ua)
        auth.load_tokens(entry.data)
        await auth.async_logout()


def _build_full_name(personal: dict) -> str:
    first = personal.get("firstName") or personal.get("name", "")
    last = personal.get("lastName") or personal.get("surname", "")
    return f"{first} {last}".strip()
