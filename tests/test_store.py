"""Tests for FiltersStore region-scoped specialty caching."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.core import HomeAssistant

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
