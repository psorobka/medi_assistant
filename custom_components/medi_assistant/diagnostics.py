from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import ENTRY_ACCESS_TOKEN, ENTRY_AUTH_COOKIES, ENTRY_REFRESH_TOKEN

# auth_cookies (idsrv, idsrv.session, ...) are bearer-equivalent — redact them too.
_TO_REDACT = {
    ENTRY_ACCESS_TOKEN,
    ENTRY_REFRESH_TOKEN,
    ENTRY_AUTH_COOKIES,
    "password",
    "pesel",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    return {
        "entry_data": async_redact_data(dict(entry.data), _TO_REDACT),
        "subentries": [{"title": sub.title, "data": sub.data} for sub in entry.subentries.values()],
        "filters_cached": bool(
            getattr(getattr(entry, "runtime_data", None), "filters_store", None)
            and entry.runtime_data.filters_store.regions
        ),
    }
