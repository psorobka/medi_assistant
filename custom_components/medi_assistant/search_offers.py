"""Expose named searches and safe exact-slot booking handles to the card."""

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .const import DOMAIN, SUBENTRY_TYPE_SEARCH
from .sensor import _format_appointments


async def async_list_search_offers(hass: HomeAssistant, *, refresh: bool = False) -> dict:
    accounts = []
    loaded = {entry.entry_id for entry in hass.config_entries.async_loaded_entries(DOMAIN)}
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.disabled_by is not None:
            continue
        coordinator = entry.runtime_data.coordinator if entry.entry_id in loaded else None
        refresh_failed = False
        if coordinator is not None and refresh:
            try:
                await coordinator.async_request_refresh()
            except (HomeAssistantError, TimeoutError):
                refresh_failed = True
        searches = []
        for subentry in entry.subentries.values():
            if subentry.subentry_type != SUBENTRY_TYPE_SEARCH:
                continue
            available = bool(
                coordinator is not None
                and not refresh_failed
                and coordinator.last_update_success
                and coordinator.data is not None
                and subentry.subentry_id in coordinator.data
            )
            slots = coordinator.data[subentry.subentry_id] if available else []
            soonest = sorted(slots, key=lambda slot: slot.get("appointmentDate") or "")[:20]
            public = (
                entry.runtime_data.booking.public_slots(subentry.subentry_id, soonest)
                if available
                else []
            )
            searches.append(
                {
                    "search_id": subentry.subentry_id,
                    "name": subentry.title,
                    "available": available,
                    "count": len(slots) if available else None,
                    "truncated": len(slots) > 20,
                    "appointments": _format_appointments(public),
                    "message": ""
                    if available
                    else "Dane szukajki są niedostępne. Odśwież listę lub sprawdź integrację.",
                }
            )
        accounts.append(
            {"account_id": entry.entry_id, "account_name": entry.title, "searches": searches}
        )
    return {"accounts": accounts}
