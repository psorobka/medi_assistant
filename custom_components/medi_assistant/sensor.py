from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import SUBENTRY_TYPE_SEARCH
from .coordinator import MedicoverCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: MedicoverCoordinator = entry.runtime_data.coordinator
    known: set[str] = set()

    @callback
    def _add_new_searches() -> None:
        """Create sensors for searches not yet seen — no entry reload needed.

        Adding a search dynamically creates its sensor here; removing one is
        handled by HA (it clears the subentry's entities from the registry).
        """
        for subentry in entry.subentries.values():
            if subentry.subentry_type != SUBENTRY_TYPE_SEARCH:
                continue
            if subentry.subentry_id in known:
                continue
            known.add(subentry.subentry_id)
            async_add_entities(
                [MedicoverSearchSensor(coordinator, entry, subentry)],
                config_subentry_id=subentry.subentry_id,
            )

    _add_new_searches()

    async def _entry_updated(hass: HomeAssistant, updated_entry: ConfigEntry) -> None:
        _add_new_searches()

    entry.async_on_unload(entry.add_update_listener(_entry_updated))


NO_SLOTS_STATE = "Brak terminów"

# HA caps sensor state at 255 chars.
_MAX_STATE_LEN = 255

# Max appointments kept in state attributes (recorder/DB size guard).
_MAX_ATTR_APPOINTMENTS = 20


class MedicoverSearchSensor(CoordinatorEntity[MedicoverCoordinator], SensorEntity):
    _attr_has_entity_name = True
    _attr_icon = "mdi:calendar-clock"

    def __init__(
        self,
        coordinator: MedicoverCoordinator,
        entry: ConfigEntry,
        subentry: Any,
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._subentry = subentry
        self._attr_unique_id = f"{entry.entry_id}_{subentry.subentry_id}"

    @property
    def name(self) -> str:
        # Read live so a reconfigured (renamed) search updates without a reload.
        return self._subentry.title

    @property
    def native_value(self) -> str | None:
        """Human-readable summary of the earliest slot (date · doctor · clinic)."""
        if self.coordinator.data is None:
            return None
        slots = self.coordinator.data.get(self._subentry.subentry_id, [])
        if not slots:
            return NO_SLOTS_STATE
        earliest = min(slots, key=lambda s: s.get("appointmentDate") or "")
        parts = [
            _format_dt(earliest.get("appointmentDate")),
            (earliest.get("doctor") or {}).get("name"),
            (earliest.get("clinic") or {}).get("name"),
        ]
        summary = " · ".join(p for p in parts if p)
        return (summary or NO_SLOTS_STATE)[:_MAX_STATE_LEN]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if self.coordinator.data is None:
            return {}
        slots = self.coordinator.data.get(self._subentry.subentry_id, [])
        earliest: str | None = None
        if slots:
            dates = [s.get("appointmentDate") for s in slots if s.get("appointmentDate")]
            if dates:
                earliest = min(dates)
        # Cap the attribute list — a popular specialty can return thousands of
        # slots (PageSize=5000) and the recorder serializes attributes on every
        # state change. `count` keeps the true total for automations.
        soonest = sorted(slots, key=lambda s: s.get("appointmentDate") or "")[
            :_MAX_ATTR_APPOINTMENTS
        ]
        return {
            "count": len(slots),
            "appointments": _format_appointments(soonest),
            "appointments_truncated": len(slots) > _MAX_ATTR_APPOINTMENTS,
            "earliest": earliest,
            "search": dict(self._subentry.data),
        }

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success


def _format_dt(value: str | None) -> str | None:
    """Format an ISO appointment date as 'YYYY-MM-DD HH:MM'."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return value


def _format_appointments(slots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for slot in slots:
        clinic = (slot.get("clinic") or {}).get("name", "N/A")
        doctor = (slot.get("doctor") or {}).get("name", "N/A")
        specialty = (slot.get("specialty") or {}).get("name", "N/A")
        langs = ", ".join(
            lang.get("name", "") for lang in (slot.get("doctorLanguages") or [])
        ) or "N/A"
        result.append(
            {
                "date": slot.get("appointmentDate", "N/A"),
                "clinic": clinic,
                "doctor": doctor,
                "specialty": specialty,
                "languages": langs,
            }
        )
    return result
