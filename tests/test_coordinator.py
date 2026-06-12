"""Tests for MedicoverCoordinator."""
from __future__ import annotations

from types import MappingProxyType
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.config_entries import ConfigSubentry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_mock_service,
)

from custom_components.medi_assistant.const import (
    CONF_NOTIFY_TARGET,
    DOMAIN,
    SUBENTRY_TYPE_SEARCH,
)
from custom_components.medi_assistant.coordinator import MedicoverCoordinator
from custom_components.medi_assistant.exceptions import ApiError, InvalidGrant

from .conftest import MOCK_ENTRY_DATA, MOCK_SUBENTRY_DATA


def _add_subentry(
    entry: MockConfigEntry,
    subentry_id: str = "sub-abc123",
    notify_target: str | None = None,
) -> ConfigSubentry:
    """Attach a ConfigSubentry to a MockConfigEntry."""
    data = dict(MOCK_SUBENTRY_DATA)
    if notify_target is not None:
        data[CONF_NOTIFY_TARGET] = notify_target
    subentry = ConfigSubentry(
        subentry_id=subentry_id,
        subentry_type=SUBENTRY_TYPE_SEARCH,
        title="Kardiolog · Warszawa",
        data=MappingProxyType(data),
        unique_id=None,
    )
    object.__setattr__(
        entry,
        "subentries",
        MappingProxyType({subentry_id: subentry}),
    )
    return subentry


def _make_filters_store(seen: set[str] | None = None) -> MagicMock:
    """Mock FiltersStore exposing the seen-slots API used by notifications."""
    fs = MagicMock()
    fs.get_seen_slots = MagicMock(return_value=set(seen or set()))
    fs.async_set_seen_slots = AsyncMock()
    return fs


@pytest.mark.asyncio
async def test_coordinator_returns_slots(hass: HomeAssistant, mock_client, slots_data):
    """Coordinator returns dict[subentry_id → slots]."""
    entry = MockConfigEntry(domain=DOMAIN, data=MOCK_ENTRY_DATA, title="Jan Kowalski")
    entry.add_to_hass(hass)
    subentry = _add_subentry(entry)

    coordinator = MedicoverCoordinator(hass, entry, mock_client, _make_filters_store())
    data = await coordinator._async_update_data()

    assert subentry.subentry_id in data
    assert len(data[subentry.subentry_id]) == 2


@pytest.mark.asyncio
async def test_coordinator_invalid_grant_raises_auth_failed(hass: HomeAssistant):
    """InvalidGrant during token refresh → ConfigEntryAuthFailed."""
    mock_auth = MagicMock()
    mock_auth.is_token_valid = MagicMock(return_value=False)
    mock_auth.async_refresh_or_relogin = AsyncMock(side_effect=InvalidGrant("expired"))

    mock_client = MagicMock()
    mock_client._auth = mock_auth

    entry = MockConfigEntry(domain=DOMAIN, data=MOCK_ENTRY_DATA, title="Jan Kowalski")
    entry.add_to_hass(hass)

    coordinator = MedicoverCoordinator(hass, entry, mock_client, _make_filters_store())

    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data()


@pytest.mark.asyncio
async def test_coordinator_api_error_raises_update_failed(hass: HomeAssistant):
    """ApiError from find_appointments → UpdateFailed."""
    mock_auth = MagicMock()
    mock_auth.is_token_valid = MagicMock(return_value=True)

    mock_client = MagicMock()
    mock_client._auth = mock_auth
    mock_client.async_find_appointments = AsyncMock(side_effect=ApiError("500 Internal"))

    entry = MockConfigEntry(domain=DOMAIN, data=MOCK_ENTRY_DATA, title="Jan Kowalski")
    entry.add_to_hass(hass)
    _add_subentry(entry, "sub-xyz")

    coordinator = MedicoverCoordinator(hass, entry, mock_client, _make_filters_store())

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()


# ---------------------------------------------------------------------------
# Notifications on new slots (opt-in per search)
# ---------------------------------------------------------------------------


# Slot keys for the fixture slots (date|doctor.id|clinic.id).
_SLOT_KEYS = {"2026-07-01T10:00:00|1|100", "2026-07-02T14:30:00|2|101"}


async def _run_poll(hass, mock_client, subentry, seen=None):
    """Build a coordinator with a seen-slots baseline, run one poll. Return the store."""
    entry = MockConfigEntry(domain=DOMAIN, data=MOCK_ENTRY_DATA, title="Jan Kowalski")
    entry.add_to_hass(hass)
    object.__setattr__(entry, "subentries", MappingProxyType({subentry.subentry_id: subentry}))
    fs = _make_filters_store(seen)
    coordinator = MedicoverCoordinator(hass, entry, mock_client, fs)
    await coordinator._async_update_data()
    await hass.async_block_till_done()
    return fs


@pytest.mark.asyncio
async def test_coordinator_notifies_unseen_via_notify_entity(hass, mock_client):
    """Unseen slots + a notify ENTITY target → notify.send_message with entity_id."""
    hass.states.async_set("notify.phone", "idle")  # marks it as an entity
    calls = async_mock_service(hass, "notify", "send_message")
    sub = _add_subentry(MockConfigEntry(domain=DOMAIN), notify_target="notify.phone")

    fs = await _run_poll(hass, mock_client, sub, seen=set())

    assert len(calls) == 1
    assert calls[0].data["entity_id"] == "notify.phone"
    assert "dr Jan Nowak" in calls[0].data["message"]
    # The seen set is persisted so we don't re-announce next time.
    fs.async_set_seen_slots.assert_awaited_once()
    assert fs.async_set_seen_slots.call_args[0][1] == _SLOT_KEYS


@pytest.mark.asyncio
async def test_coordinator_notifies_multiple_targets(hass, mock_client):
    """A search with several targets notifies each of them."""
    hass.states.async_set("notify.phone", "idle")  # entity
    entity_calls = async_mock_service(hass, "notify", "send_message")
    legacy_calls = async_mock_service(hass, "notify", "telegram")  # legacy service
    sub = _add_subentry(
        MockConfigEntry(domain=DOMAIN),
        notify_target=["notify.phone", "notify.telegram"],
    )

    await _run_poll(hass, mock_client, sub, seen=set())

    assert len(entity_calls) == 1
    assert entity_calls[0].data["entity_id"] == "notify.phone"
    assert len(legacy_calls) == 1
    assert "entity_id" not in legacy_calls[0].data


@pytest.mark.asyncio
async def test_coordinator_notifies_unseen_via_legacy_service(hass, mock_client):
    """Unseen slots + a legacy notify SERVICE → notify.<service> with message (no entity_id)."""
    calls = async_mock_service(hass, "notify", "telegram")  # legacy service, no entity state
    sub = _add_subentry(MockConfigEntry(domain=DOMAIN), notify_target="notify.telegram")

    await _run_poll(hass, mock_client, sub, seen=set())

    assert len(calls) == 1
    assert "entity_id" not in calls[0].data
    assert "dr Jan Nowak" in calls[0].data["message"]


@pytest.mark.asyncio
async def test_coordinator_no_notify_when_all_seen(hass, mock_client):
    """All current slots already seen → no notification, no re-persist."""
    hass.states.async_set("notify.phone", "idle")
    calls = async_mock_service(hass, "notify", "send_message")
    sub = _add_subentry(MockConfigEntry(domain=DOMAIN), notify_target="notify.phone")

    fs = await _run_poll(hass, mock_client, sub, seen=set(_SLOT_KEYS))

    assert len(calls) == 0
    fs.async_set_seen_slots.assert_not_called()


@pytest.mark.asyncio
async def test_coordinator_no_notify_without_target(hass, mock_client):
    """Search without a notify target → no notification, no seen tracking."""
    calls = async_mock_service(hass, "notify", "send_message")
    sub = _add_subentry(MockConfigEntry(domain=DOMAIN))  # no target

    fs = await _run_poll(hass, mock_client, sub, seen=set())

    assert len(calls) == 0
    fs.async_set_seen_slots.assert_not_called()


@pytest.mark.asyncio
async def test_coordinator_notify_failure_does_not_break_poll(hass, mock_client):
    """A failing notify (unknown service) must not fail the poll."""
    entry = MockConfigEntry(domain=DOMAIN, data=MOCK_ENTRY_DATA, title="Jan Kowalski")
    entry.add_to_hass(hass)
    sub = _add_subentry(entry, notify_target="notify.does_not_exist")
    # notify service intentionally NOT registered → ServiceNotFound (caught)
    coordinator = MedicoverCoordinator(hass, entry, mock_client, _make_filters_store())

    result = await coordinator._async_update_data()  # must not raise

    assert sub.subentry_id in result
