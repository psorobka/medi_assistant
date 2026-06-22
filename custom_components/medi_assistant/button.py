from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .exceptions import AuthError, InvalidGrant, MfaRequired


_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([MedicoverRefreshButton(hass, entry), MedicoverReauthButton(hass, entry)])


class MedicoverRefreshButton(ButtonEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "refresh_data"

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_refresh"
        self.hass = hass

    async def async_press(self) -> None:
        _LOGGER.info("Manual data refresh requested for account '%s'", self._entry.title)
        runtime_data = self._entry.runtime_data
        try:
            await runtime_data.filters_store.async_refresh(runtime_data.client)
        except Exception:
            _LOGGER.exception("Failed to refresh Medicover data for '%s'", self._entry.title)
            return

        personal = runtime_data.filters_store.personal
        first = personal.get("firstName") or personal.get("name", "")
        last = personal.get("lastName") or personal.get("surname", "")
        full_name = f"{first} {last}".strip()
        if full_name and self._entry.title != full_name:
            _LOGGER.info("Patient name updated: '%s' → '%s'", self._entry.title, full_name)
            self.hass.config_entries.async_update_entry(self._entry, title=full_name)
        _LOGGER.debug("Data refresh completed for account '%s'", self._entry.title)


class MedicoverReauthButton(ButtonEntity):
    """Force a re-login: silent first (like a restart), interactive reauth as fallback."""

    _attr_has_entity_name = True
    _attr_translation_key = "force_reauth"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:login-variant"

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_reauth"
        self.hass = hass

    async def async_press(self) -> None:
        _LOGGER.info("Manual re-login requested for account '%s'", self._entry.title)
        runtime_data = self._entry.runtime_data
        try:
            await runtime_data.auth.async_refresh_or_relogin(force=True)
        except (MfaRequired, AuthError, InvalidGrant) as err:
            # Silent recovery not possible (device not trusted / bad password / no
            # credentials) → hand off to the interactive reauth dialog.
            _LOGGER.warning(
                "Silent re-login failed for '%s' (%s) — starting interactive reauth",
                self._entry.title,
                type(err).__name__,
            )
            self._entry.async_start_reauth(self.hass)
            return
        # Session restored without a dialog — refresh sensors right away.
        await runtime_data.coordinator.async_request_refresh()
        _LOGGER.info("Manual re-login succeeded for account '%s'", self._entry.title)
