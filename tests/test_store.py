"""Tests for FiltersStore region-scoped specialty caching."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.core import HomeAssistant

from custom_components.medi_assistant.const import (
    STORE_KEY_LAST_REFRESHED,
    STORE_KEY_REGIONS,
)
from custom_components.medi_assistant.store import FiltersStore


@pytest.mark.asyncio
async def test_region_specialties_cached_per_region(hass: HomeAssistant):
    """async_refresh_specialties fetches region-scoped specialties and caches them.

    The global /filters call returns a small subset (~27); region-scoped returns
    the full set (e.g. 124), so specialties are fetched and cached per region.
    """
    store = FiltersStore(hass, "entry-1")
    await store.async_load()

    client = MagicMock()
    client.async_find_filters = AsyncMock(
        return_value={"specialties": [{"id": 9, "value": "Kardiolog"}]}
    )

    await store.async_refresh_specialties(client, 200)

    # Region passed without a specialty filter.
    client.async_find_filters.assert_awaited_once_with(region=200)
    assert store.get_specialties_for_region(200) == [{"id": 9, "value": "Kardiolog"}]
    # Different / unknown region → empty until fetched.
    assert store.get_specialties_for_region(999) == []


@pytest.mark.asyncio
async def test_seen_slots_roundtrip(hass: HomeAssistant):
    """Seen slot keys persist per subentry (first-detection notifications)."""
    store = FiltersStore(hass, "entry-seen")
    await store.async_load()

    assert store.get_seen_slots("sub1") == set()

    await store.async_set_seen_slots("sub1", {"2026-07-01T10:00:00|1|100", "k2"})
    assert store.get_seen_slots("sub1") == {"2026-07-01T10:00:00|1|100", "k2"}
    assert store.get_seen_slots("sub2") == set()


@pytest.mark.asyncio
async def test_snooze_roundtrip(hass: HomeAssistant):
    """Snooze deadline persists per subentry; default is 0 (not snoozed)."""
    store = FiltersStore(hass, "entry-snooze")
    await store.async_load()

    assert store.get_snoozed_until("sub1") == 0

    await store.async_set_snooze("sub1", 1_700_000_000)
    assert store.get_snoozed_until("sub1") == 1_700_000_000
    assert store.get_snoozed_until("sub2") == 0


def _refresh_client() -> MagicMock:
    client = MagicMock()
    client.async_get_personal_data = AsyncMock(
        return_value={"firstName": "Jan", "lastName": "Kowalski"}
    )
    client.async_find_filters = AsyncMock(
        return_value={
            "regions": [{"id": 204, "value": "Warszawa"}],
            "specialties": [{"id": 9, "value": "Kardiolog"}],
        }
    )
    return client


@pytest.mark.asyncio
async def test_refresh_populates_personal_and_filters(hass: HomeAssistant):
    """async_refresh stores patient data + base filters and exposes them via properties."""
    store = FiltersStore(hass, "entry-refresh")
    await store.async_load()

    client = _refresh_client()
    await store.async_refresh(client)

    assert store.personal == {"firstName": "Jan", "lastName": "Kowalski"}
    assert store.regions == [{"id": 204, "value": "Warszawa"}]
    assert store.specialties == [{"id": 9, "value": "Kardiolog"}]


@pytest.mark.asyncio
async def test_ensure_refreshes_when_empty(hass: HomeAssistant):
    """async_ensure refreshes from the API when the cache is empty."""
    store = FiltersStore(hass, "entry-ensure-empty")
    client = _refresh_client()

    await store.async_ensure(client)

    client.async_find_filters.assert_awaited_once()
    assert store.regions == [{"id": 204, "value": "Warszawa"}]


@pytest.mark.asyncio
async def test_ensure_skips_refresh_when_cache_fresh(hass: HomeAssistant):
    """A populated, recent cache must not trigger an API refresh."""
    store = FiltersStore(hass, "entry-ensure-fresh")
    await store.async_load()
    store._data[STORE_KEY_REGIONS] = [{"id": 1, "value": "X"}]
    store._data[STORE_KEY_LAST_REFRESHED] = int(time.time())
    await store.async_save()

    client = _refresh_client()
    await store.async_ensure(client)

    client.async_find_filters.assert_not_called()


@pytest.mark.asyncio
async def test_refresh_clinics_doctors_caches_per_region_specialty(hass: HomeAssistant):
    """Clinics/doctors are cached and read back per (region, specialty) key."""
    store = FiltersStore(hass, "entry-cd")
    await store.async_load()

    client = MagicMock()
    client.async_find_filters = AsyncMock(
        return_value={
            "clinics": [{"id": 11, "value": "Klinika Centrum"}],
            "doctors": [{"id": 22, "value": "dr Jan Nowak"}],
        }
    )

    await store.async_refresh_clinics_doctors(client, 204, [9])

    client.async_find_filters.assert_awaited_once_with(region=204, specialty=[9])
    assert store.get_clinics(204, [9]) == [{"id": 11, "value": "Klinika Centrum"}]
    assert store.get_doctors(204, [9]) == [{"id": 22, "value": "dr Jan Nowak"}]
    # Unknown combination → empty.
    assert store.get_clinics(999, [9]) == []


@pytest.mark.asyncio
async def test_clear_subentry_state_drops_seen_and_snooze(hass: HomeAssistant):
    """Deleting a search clears its seen slots and snooze deadline."""
    store = FiltersStore(hass, "entry-clear")
    await store.async_load()

    await store.async_set_seen_slots("sub1", {"k1"})
    await store.async_set_snooze("sub1", 1_700_000_000)

    await store.async_clear_subentry_state("sub1")

    assert store.get_seen_slots("sub1") == set()
    assert store.get_snoozed_until("sub1") == 0
