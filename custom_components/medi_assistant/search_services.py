"""Account-scoped search discovery and creation actions."""

import asyncio

import voluptuous as vol
from homeassistant.config_entries import ConfigSubentry
from homeassistant.core import SupportsResponse, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.service import async_register_admin_service

from .const import DOMAIN, LANGUAGES, SUBENTRY_TYPE_SEARCH
from .search_config import _subentry_title, build_search_data
from .store import FiltersStore


@callback
def async_setup_search_services(hass):
    """Register once; resolve loaded accounts afresh for each call."""
    if hass.services.has_service(DOMAIN, "create_search"):
        return
    locks = {}

    def account(account_id, *, require_loaded=True):
        entry = hass.config_entries.async_get_entry(account_id)
        if (
            entry is None
            or entry.domain != DOMAIN
            or (require_loaded and entry not in hass.config_entries.async_loaded_entries(DOMAIN))
        ):
            raise ServiceValidationError("Wybierz załadowane konto Medi Assistant.")
        return entry

    async def filters(entry, values):
        runtime = entry.runtime_data
        store = runtime.filters_store
        try:
            if not store.regions:
                await store.async_refresh(runtime.client)
            region = values.get("region_id")
            specialty = values.get("specialty_id")
            if specialty is not None and region is None:
                raise ServiceValidationError("Najpierw wybierz region.")
            if region is not None:
                if not any(str(item["id"]) == str(region) for item in store.regions):
                    raise ServiceValidationError("Nieprawidłowy region.")
                await store.async_refresh_specialties(runtime.client, region)
            if specialty is not None:
                if not any(
                    str(item["id"]) == str(specialty)
                    for item in store.get_specialties_for_region(region)
                ):
                    raise ServiceValidationError("Specjalizacja niedostępna w regionie.")
                await store.async_refresh_clinics_doctors(runtime.client, region, [specialty])
        except HomeAssistantError:
            raise
        except Exception as err:
            raise HomeAssistantError("Nie udało się pobrać filtrów szukajki.") from err
        return store

    async def get_filters(call):
        if "account_id" not in call.data:
            if "region_id" in call.data or "specialty_id" in call.data:
                raise ServiceValidationError("Najpierw wybierz konto.")
            return {
                "accounts": [
                    {"account_id": entry.entry_id, "account_name": entry.title}
                    for entry in hass.config_entries.async_loaded_entries(DOMAIN)
                ]
            }
        entry = account(call.data["account_id"])
        async with locks.setdefault(entry.entry_id, asyncio.Lock()):
            store = await filters(entry, call.data)
            region = call.data.get("region_id")
            specialty = call.data.get("specialty_id")
            return {
                "account_id": entry.entry_id,
                "regions": store.regions,
                "specialties": store.get_specialties_for_region(region) if region else [],
                "clinics": store.get_clinics(region, [specialty]) if specialty else [],
                "doctors": store.get_doctors(region, [specialty]) if specialty else [],
                "languages": [{"id": key, "value": value} for key, value in LANGUAGES.items()],
            }

    async def create_search(call):
        entry = account(call.data["account_id"])
        async with locks.setdefault(entry.entry_id, asyncio.Lock()):
            store = await filters(entry, call.data)
            try:
                data = build_search_data(call.data, store)
            except ValueError as err:
                raise ServiceValidationError(str(err)) from err
            # No await between duplicate detection and persistence.
            existing = None
            account(entry.entry_id)
            for search in entry.subentries.values():
                if search.subentry_type != SUBENTRY_TYPE_SEARCH:
                    continue
                try:
                    matches = build_search_data(search.data, store) == data
                except ValueError:
                    continue  # Existing search may refer to a retired filter.
                if matches:
                    existing = search
                    break
            search = existing or ConfigSubentry(
                data=data,
                subentry_type=SUBENTRY_TYPE_SEARCH,
                title=_subentry_title(data),
                unique_id=None,
            )
            if existing is None:
                hass.config_entries.async_add_subentry(entry, search)
            return {
                "success": True,
                "created": existing is None,
                "account_id": entry.entry_id,
                "search_id": search.subentry_id,
                "title": search.title,
                "data": dict(search.data),
            }

    id_field = vol.All(vol.Coerce(int), vol.Range(min=1))

    async def delete_search(call):
        entry = account(call.data["account_id"], require_loaded=False)
        async with locks.setdefault(entry.entry_id, asyncio.Lock()):
            account(entry.entry_id, require_loaded=False)
            search = entry.subentries.get(call.data["search_id"])
            if search is None:
                return {"success": True, "deleted": False}
            if search.subentry_type != SUBENTRY_TYPE_SEARCH:
                raise ServiceValidationError("Wybierz szukajkę Medi Assistant.")
            # Clear persisted notification state before removing the subentry.
            if entry in hass.config_entries.async_loaded_entries(DOMAIN):
                store = entry.runtime_data.filters_store
            else:
                store = FiltersStore(hass, entry.entry_id)
                await store.async_load()
            await store.async_clear_subentry_state(search.subentry_id)
            hass.config_entries.async_remove_subentry(entry, search.subentry_id)
            return {"success": True, "deleted": True}

    async_register_admin_service(
        hass,
        DOMAIN,
        "delete_search",
        delete_search,
        schema=vol.Schema(
            {
                vol.Required("account_id"): cv.string,
                vol.Required("search_id"): cv.string,
            }
        ),
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        "get_search_filters",
        get_filters,
        schema=vol.Schema(
            {
                vol.Optional("account_id"): cv.string,
                vol.Optional("region_id"): id_field,
                vol.Optional("specialty_id"): id_field,
            }
        ),
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        "create_search",
        create_search,
        schema=vol.Schema(
            {
                vol.Required("account_id"): cv.string,
                vol.Required("region_id"): id_field,
                vol.Required("specialty_id"): id_field,
                vol.Optional("clinic_id"): id_field,
                vol.Optional("doctor_id"): id_field,
                vol.Optional("language_id"): vol.In(LANGUAGES),
                vol.Optional("date_from"): cv.string,
                vol.Optional("date_to"): cv.string,
                vol.Optional("notify_target"): vol.All(cv.ensure_list, [cv.string]),
            }
        ),
        supports_response=SupportsResponse.ONLY,
    )
