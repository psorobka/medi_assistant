from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import (
    CACHE_TTL,
    DOMAIN,
    STORE_KEY_CLINICS,
    STORE_KEY_DOCTORS,
    STORE_KEY_LAST_REFRESHED,
    STORE_KEY_PERSONAL,
    STORE_KEY_REGION_SPECIALTIES,
    STORE_KEY_REGIONS,
    STORE_KEY_SEEN_SLOTS,
    STORE_KEY_SNOOZE,
    STORE_KEY_SPECIALTIES,
)

if TYPE_CHECKING:
    from .api import MedicoverClient

_LOGGER = logging.getLogger(__name__)
_STORE_VERSION = 1


class FiltersStore:
    """Persists patient data and filter lists (regions / specialties / clinics / doctors)."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store: Store[dict[str, Any]] = Store(hass, _STORE_VERSION, f"{DOMAIN}.{entry_id}")
        self._data: dict[str, Any] = {}

    async def async_load(self) -> None:
        loaded = await self._store.async_load()
        self._data = loaded or {}

    async def async_save(self) -> None:
        await self._store.async_save(self._data)

    async def async_ensure(self, client: MedicoverClient) -> None:
        """Load from disk; refresh from API if stale or empty."""
        await self.async_load()
        last = self._data.get(STORE_KEY_LAST_REFRESHED, 0)
        if not self._data.get(STORE_KEY_REGIONS) or (time.time() - last) > CACHE_TTL:
            await self.async_refresh(client)

    async def async_refresh(self, client: MedicoverClient) -> None:
        """Fetch fresh patient data + base filters from API."""
        _LOGGER.debug("Fetching fresh filter data from Medicover API")
        personal = await client.async_get_personal_data()
        filters = await client.async_find_filters()
        self._data[STORE_KEY_PERSONAL] = personal
        self._data[STORE_KEY_REGIONS] = filters.get("regions", [])
        self._data[STORE_KEY_SPECIALTIES] = filters.get("specialties", [])
        self._data[STORE_KEY_LAST_REFRESHED] = int(time.time())
        await self.async_save()
        _LOGGER.info(
            "Filter cache refreshed: %d region(s), %d specialty(ies)",
            len(self._data[STORE_KEY_REGIONS]),
            len(self._data[STORE_KEY_SPECIALTIES]),
        )

    async def async_refresh_clinics_doctors(
        self, client: MedicoverClient, region: int, specialty: list[int]
    ) -> None:
        """Fetch clinics and doctors for given region + specialty combination."""
        _LOGGER.debug("Fetching clinics/doctors for region=%d, specialties=%s", region, specialty)
        filters = await client.async_find_filters(region=region, specialty=specialty)
        ckey = _cd_key("clinics", region, specialty)
        dkey = _cd_key("doctors", region, specialty)
        clinics = filters.get("clinics", [])
        doctors = filters.get("doctors", [])
        self._data.setdefault(STORE_KEY_CLINICS, {})[ckey] = clinics
        self._data.setdefault(STORE_KEY_DOCTORS, {})[dkey] = doctors
        await self.async_save()
        _LOGGER.debug(
            "Clinics/doctors cached: %d clinic(s), %d doctor(s) for region=%d, specialties=%s",
            len(clinics),
            len(doctors),
            region,
            specialty,
        )

    async def async_refresh_specialties(self, client: MedicoverClient, region: int) -> None:
        """Fetch specialties available in a region.

        The global /filters call (no region) returns only a small subset, so the
        search wizard loads specialties per region after the region is chosen.
        """
        filters = await client.async_find_filters(region=region)
        specs = filters.get("specialties", [])
        self._data.setdefault(STORE_KEY_REGION_SPECIALTIES, {})[str(region)] = specs
        await self.async_save()

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    @property
    def personal(self) -> dict[str, Any]:
        return self._data.get(STORE_KEY_PERSONAL, {})

    @property
    def regions(self) -> list[dict[str, Any]]:
        return self._data.get(STORE_KEY_REGIONS, [])

    @property
    def specialties(self) -> list[dict[str, Any]]:
        return self._data.get(STORE_KEY_SPECIALTIES, [])

    def get_specialties_for_region(self, region: int) -> list[dict[str, Any]]:
        return self._data.get(STORE_KEY_REGION_SPECIALTIES, {}).get(str(region), [])

    # ------------------------------------------------------------------
    # Seen slots (per search) — for first-detection notifications that
    # survive restarts (no re-notify of already-seen slots).
    # ------------------------------------------------------------------

    def get_seen_slots(self, subentry_id: str) -> set[str]:
        return set(self._data.get(STORE_KEY_SEEN_SLOTS, {}).get(subentry_id, []))

    async def async_set_seen_slots(self, subentry_id: str, keys: set[str]) -> None:
        self._data.setdefault(STORE_KEY_SEEN_SLOTS, {})[subentry_id] = sorted(keys)
        await self.async_save()

    # ------------------------------------------------------------------
    # Snooze (per search) — mutes notifications until an epoch timestamp.
    # ------------------------------------------------------------------

    def get_snoozed_until(self, subentry_id: str) -> int:
        return self._data.get(STORE_KEY_SNOOZE, {}).get(subentry_id, 0)

    async def async_set_snooze(self, subentry_id: str, until: int) -> None:
        self._data.setdefault(STORE_KEY_SNOOZE, {})[subentry_id] = until
        await self.async_save()

    async def async_clear_subentry_state(self, subentry_id: str) -> None:
        """Drop per-search state (seen slots + snooze) when a search is deleted."""
        for key in (STORE_KEY_SEEN_SLOTS, STORE_KEY_SNOOZE):
            self._data.get(key, {}).pop(subentry_id, None)
        await self.async_save()

    def get_clinics(self, region: int, specialty: list[int]) -> list[dict[str, Any]]:
        return self._data.get(STORE_KEY_CLINICS, {}).get(_cd_key("clinics", region, specialty), [])

    def get_doctors(self, region: int, specialty: list[int]) -> list[dict[str, Any]]:
        return self._data.get(STORE_KEY_DOCTORS, {}).get(_cd_key("doctors", region, specialty), [])


def _cd_key(prefix: str, region: int, specialty: list[int]) -> str:
    return f"{prefix}_{region}_{'_'.join(str(s) for s in sorted(specialty))}"
