"""Booking action callable from user notification automations."""

from __future__ import annotations

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er

from .booking import result
from .cancellation import result as cancellation_result
from .const import DOMAIN
from .search_offers import async_list_search_offers

SERVICE_BOOK = "book_appointment"


@callback
def async_setup_booking_service(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, SERVICE_BOOK):
        return

    async def async_book(call: ServiceCall) -> dict:
        booking_id = call.data["booking_id"]
        for entry in hass.config_entries.async_loaded_entries(DOMAIN):
            manager = entry.runtime_data.booking
            if manager.owns(booking_id):
                outcome = await manager.async_book(booking_id)
                if outcome["success"]:
                    # A failed refresh must not turn a confirmed booking into a failure.
                    hass.async_create_task(entry.runtime_data.coordinator.async_request_refresh())
                return outcome
        return result("expired")

    async def async_list(call: ServiceCall) -> dict:
        if "entity_id" not in call.data:
            return await async_list_search_offers(hass, refresh=call.data["refresh"])
        entity_id = call.data["entity_id"]
        registered = er.async_get(hass).async_get(entity_id)
        state = hass.states.get(entity_id)
        if (
            registered is None
            or registered.platform != DOMAIN
            or state is None
            or state.state in ("unknown", "unavailable")
        ):
            raise ServiceValidationError("Wybierz dostępną szukajkę Medicover.")
        appointments = [
            slot for slot in state.attributes.get("appointments", []) if slot.get("booking_id")
        ]
        return {
            "appointments": appointments,
            "message": "Skopiuj booking_id terminu do akcji Zarezerwuj wizytę."
            if appointments
            else "Brak terminów do rezerwacji.",
        }

    async def async_list_booked(call: ServiceCall) -> dict:
        if "entity_id" not in call.data:
            accounts = []
            loaded = {entry.entry_id for entry in hass.config_entries.async_loaded_entries(DOMAIN)}
            for entry in hass.config_entries.async_entries(DOMAIN):
                if entry.disabled_by is not None:
                    continue
                if entry.entry_id in loaded:
                    listing = await entry.runtime_data.booking.cancellation.async_list()
                else:
                    listing = {
                        "success": False,
                        "status": "unavailable",
                        "appointments": [],
                        "message": "Konto jest niedostępne. Sprawdź integrację Medi Assistant.",
                    }
                accounts.append(
                    {"account_id": entry.entry_id, "account_name": entry.title, **listing}
                )
            return {
                "success": all(account["success"] for account in accounts),
                "accounts": accounts,
            }
        registered = er.async_get(hass).async_get(call.data["entity_id"])
        if registered is None or registered.platform != DOMAIN:
            raise ServiceValidationError("Wybierz szukajkę konta Medicover.")
        for entry in hass.config_entries.async_loaded_entries(DOMAIN):
            if entry.entry_id == registered.config_entry_id:
                return await entry.runtime_data.booking.cancellation.async_list()
        raise ServiceValidationError("Konto Medicover nie jest załadowane.")

    async def async_cancel(call: ServiceCall) -> dict:
        cancellation_id = call.data["cancellation_id"]
        for entry in hass.config_entries.async_loaded_entries(DOMAIN):
            manager = entry.runtime_data.booking.cancellation
            if manager.owns(cancellation_id):
                return await manager.async_cancel(
                    cancellation_id,
                    confirm_late_cancellation=call.data["confirm_late_cancellation"],
                )
        return cancellation_result("expired")

    hass.services.async_register(
        DOMAIN,
        "list_booked_appointments",
        async_list_booked,
        schema=vol.Schema({vol.Optional("entity_id"): cv.entity_id}),
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        "cancel_appointment",
        async_cancel,
        schema=vol.Schema(
            {
                vol.Required("cancellation_id"): cv.string,
                vol.Optional("confirm_late_cancellation", default=False): cv.boolean,
            }
        ),
        supports_response=SupportsResponse.ONLY,
    )

    hass.services.async_register(
        DOMAIN,
        "list_booking_offers",
        async_list,
        schema=vol.Schema(
            {
                vol.Optional("entity_id"): cv.entity_id,
                vol.Optional("refresh", default=False): cv.boolean,
            }
        ),
        supports_response=SupportsResponse.ONLY,
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_BOOK,
        async_book,
        schema=vol.Schema({vol.Required("booking_id"): cv.string}),
        supports_response=SupportsResponse.ONLY,
    )
