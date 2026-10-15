"""Booking action callable from user notification automations."""

from __future__ import annotations

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er

from .booking import result
from .const import DOMAIN

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

    hass.services.async_register(
        DOMAIN,
        "list_booking_offers",
        async_list,
        schema=vol.Schema({vol.Required("entity_id"): cv.entity_id}),
        supports_response=SupportsResponse.ONLY,
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_BOOK,
        async_book,
        schema=vol.Schema({vol.Required("booking_id"): cv.string}),
        supports_response=SupportsResponse.ONLY,
    )
