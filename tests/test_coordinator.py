"""Tests for MedicoverCoordinator."""

from __future__ import annotations

from types import MappingProxyType
from unittest.mock import AsyncMock, MagicMock

import time

import pytest
from homeassistant.config_entries import ConfigSubentry
from homeassistant.core import Event, HomeAssistant
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_mock_service,
)

from custom_components.medi_assistant.const import (
    ACTION_DELETE_PREFIX,
    ACTION_SNOOZE_PREFIX,
    CONF_NOTIFY_TARGET,
    DOMAIN,
    NOTIFY_ACTION_EVENT,
    SNOOZE_SECONDS,
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


def _make_filters_store(seen: set[str] | None = None, snoozed_until: int = 0) -> MagicMock:
    """Mock FiltersStore exposing the seen-slots / snooze API used by notifications."""
    fs = MagicMock()
    fs.get_seen_slots = MagicMock(return_value=set(seen or set()))
    fs.async_set_seen_slots = AsyncMock()
    fs.get_snoozed_until = MagicMock(return_value=snoozed_until)
    fs.async_set_snooze = AsyncMock()
    fs.async_clear_subentry_state = AsyncMock()
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
async def test_coordinator_invalid_grant_raises_update_failed(hass: HomeAssistant):
    """InvalidGrant during token refresh → UpdateFailed (reauth deferred to keep-alive).

    The coordinator must NOT pop reauth on a single poll-time auth hiccup; the
    keep-alive is the designated escalator (it retries + re-seeds cookies first).
    """
    mock_auth = MagicMock()
    mock_auth.is_token_valid = MagicMock(return_value=False)
    mock_auth.async_refresh_or_relogin = AsyncMock(side_effect=InvalidGrant("expired"))

    mock_client = MagicMock()
    mock_client._auth = mock_auth

    entry = MockConfigEntry(domain=DOMAIN, data=MOCK_ENTRY_DATA, title="Jan Kowalski")
    entry.add_to_hass(hass)

    coordinator = MedicoverCoordinator(hass, entry, mock_client, _make_filters_store())

    with pytest.raises(UpdateFailed):
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


async def _run_poll(hass, mock_client, subentry, seen=None, snoozed_until=0):
    """Build a coordinator with a seen-slots baseline, run one poll. Return the store."""
    entry = MockConfigEntry(domain=DOMAIN, data=MOCK_ENTRY_DATA, title="Jan Kowalski")
    entry.add_to_hass(hass)
    object.__setattr__(entry, "subentries", MappingProxyType({subentry.subentry_id: subentry}))
    fs = _make_filters_store(seen, snoozed_until)
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


# ---------------------------------------------------------------------------
# Snooze: a snoozed search stays silent but marks slots as seen
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_snooze_suppresses_notify_but_marks_seen(hass, mock_client):
    """While snoozed: no notification, but current slots are recorded as seen."""
    hass.states.async_set("notify.phone", "idle")
    calls = async_mock_service(hass, "notify", "send_message")
    sub = _add_subentry(MockConfigEntry(domain=DOMAIN), notify_target="notify.phone")

    future = int(time.time()) + 3600
    fs = await _run_poll(hass, mock_client, sub, seen=set(), snoozed_until=future)

    assert len(calls) == 0
    # Slots seen during the snooze are marked seen, so they aren't replayed later.
    fs.async_set_seen_slots.assert_awaited_once()
    assert fs.async_set_seen_slots.call_args[0][1] == _SLOT_KEYS


@pytest.mark.asyncio
async def test_expired_snooze_notifies_again(hass, mock_client):
    """A snooze in the past behaves as if absent → unseen slots are announced."""
    hass.states.async_set("notify.phone", "idle")
    calls = async_mock_service(hass, "notify", "send_message")
    sub = _add_subentry(MockConfigEntry(domain=DOMAIN), notify_target="notify.phone")

    past = int(time.time()) - 3600
    await _run_poll(hass, mock_client, sub, seen=set(), snoozed_until=past)

    assert len(calls) == 1


@pytest.mark.asyncio
async def test_notify_payload_includes_actions(hass, mock_client):
    """A mobile_app legacy service target gets Drzemka/Usuń buttons; others don't.

    Action buttons live in `data`, which only the legacy `notify.mobile_app_*`
    service accepts — so the user targets that service directly.
    """
    hass.config.language = "pl"
    mobile_calls = async_mock_service(hass, "notify", "mobile_app_phone")
    legacy_calls = async_mock_service(hass, "notify", "telegram")
    entry = MockConfigEntry(domain=DOMAIN, data=MOCK_ENTRY_DATA, title="Jan Kowalski")
    entry.add_to_hass(hass)
    sub = _add_subentry(entry, notify_target=["notify.mobile_app_phone", "notify.telegram"])

    fs = _make_filters_store(set())
    coordinator = MedicoverCoordinator(hass, entry, mock_client, fs)
    await coordinator._async_update_data()
    await hass.async_block_till_done()

    actions = mobile_calls[0].data["data"]["actions"]
    assert "entity_id" not in mobile_calls[0].data
    assert [a["title"] for a in actions] == ["Drzemka (24h)", "Usuń"]
    base = f"{entry.entry_id}__{sub.subentry_id}"
    assert actions[0]["action"] == f"{ACTION_SNOOZE_PREFIX}__{base}"
    assert actions[1]["action"] == f"{ACTION_DELETE_PREFIX}__{base}"
    assert actions[1]["destructive"] is True
    # Non-mobile_app legacy service path carries no action payload.
    assert "data" not in legacy_calls[0].data


@pytest.mark.asyncio
async def test_notify_entity_has_no_data(hass, mock_client):
    """A modern notify entity target is sent without `data` (actions impossible).

    Regression: the entity `notify.send_message` schema rejects `data`, which
    used to make notifications fail with "extra keys not allowed @ data['data']".
    """
    hass.states.async_set("notify.phone", "idle")
    calls = async_mock_service(hass, "notify", "send_message")
    sub = _add_subentry(MockConfigEntry(domain=DOMAIN), notify_target="notify.phone")

    await _run_poll(hass, mock_client, sub, seen=set())

    assert len(calls) == 1
    assert calls[0].data["entity_id"] == "notify.phone"
    assert "data" not in calls[0].data


@pytest.mark.asyncio
async def test_notify_persistent_notification_has_no_data(hass, mock_client):
    """A persistent_notification target → plain HA panel entry, no action buttons."""
    calls = async_mock_service(hass, "notify", "persistent_notification")
    sub = _add_subentry(
        MockConfigEntry(domain=DOMAIN), notify_target="notify.persistent_notification"
    )

    await _run_poll(hass, mock_client, sub, seen=set())

    assert len(calls) == 1
    assert "data" not in calls[0].data
    assert "dr Jan Nowak" in calls[0].data["message"]


@pytest.mark.asyncio
async def test_notify_action_titles_localized(hass, mock_client):
    """Action titles come from translations: English locale → English titles."""
    hass.config.language = "en"
    calls = async_mock_service(hass, "notify", "mobile_app_phone")
    entry = MockConfigEntry(domain=DOMAIN, data=MOCK_ENTRY_DATA, title="Jan Kowalski")
    entry.add_to_hass(hass)
    _add_subentry(entry, notify_target="notify.mobile_app_phone")

    coordinator = MedicoverCoordinator(hass, entry, mock_client, _make_filters_store(set()))
    await coordinator._async_update_data()
    await hass.async_block_till_done()

    titles = [a["title"] for a in calls[0].data["data"]["actions"]]
    assert titles == ["Snooze (24h)", "Delete"]


# ---------------------------------------------------------------------------
# Notification action handler (Drzemka / Usuń)
# ---------------------------------------------------------------------------


def _build_coordinator(hass, mock_client, fs=None):
    """Coordinator + entry with one subentry, ready for action-handler tests."""
    entry = MockConfigEntry(domain=DOMAIN, data=MOCK_ENTRY_DATA, title="Jan Kowalski")
    entry.add_to_hass(hass)
    sub = _add_subentry(entry)
    coordinator = MedicoverCoordinator(hass, entry, mock_client, fs or _make_filters_store())
    return coordinator, entry, sub


def _action_event(action: str) -> Event:
    return Event(NOTIFY_ACTION_EVENT, {"action": action})


@pytest.mark.asyncio
async def test_snooze_action_sets_snooze(hass, mock_client):
    """Tapping Drzemka snoozes the search ~24h ahead."""
    fs = _make_filters_store()
    coordinator, entry, sub = _build_coordinator(hass, mock_client, fs)

    before = int(time.time())
    await coordinator.async_handle_notification_action(
        _action_event(f"{ACTION_SNOOZE_PREFIX}__{entry.entry_id}__{sub.subentry_id}")
    )

    fs.async_set_snooze.assert_awaited_once()
    sub_id, until = fs.async_set_snooze.call_args[0]
    assert sub_id == sub.subentry_id
    assert before + SNOOZE_SECONDS <= until <= int(time.time()) + SNOOZE_SECONDS


@pytest.mark.asyncio
async def test_delete_action_removes_subentry(hass, mock_client):
    """Tapping Usuń clears per-search state and removes the subentry."""
    fs = _make_filters_store()
    coordinator, entry, sub = _build_coordinator(hass, mock_client, fs)
    hass.config_entries.async_remove_subentry = MagicMock()

    await coordinator.async_handle_notification_action(
        _action_event(f"{ACTION_DELETE_PREFIX}__{entry.entry_id}__{sub.subentry_id}")
    )

    fs.async_clear_subentry_state.assert_awaited_once_with(sub.subentry_id)
    hass.config_entries.async_remove_subentry.assert_called_once_with(entry, sub.subentry_id)


@pytest.mark.asyncio
async def test_action_for_other_entry_is_ignored(hass, mock_client):
    """An action targeting a different account is a no-op."""
    fs = _make_filters_store()
    coordinator, entry, sub = _build_coordinator(hass, mock_client, fs)
    hass.config_entries.async_remove_subentry = MagicMock()

    await coordinator.async_handle_notification_action(
        _action_event(f"{ACTION_SNOOZE_PREFIX}__other-entry__{sub.subentry_id}")
    )

    fs.async_set_snooze.assert_not_called()
    hass.config_entries.async_remove_subentry.assert_not_called()


@pytest.mark.asyncio
async def test_action_for_unknown_subentry_is_ignored(hass, mock_client):
    """An action for a subentry that no longer exists is a no-op."""
    fs = _make_filters_store()
    coordinator, entry, sub = _build_coordinator(hass, mock_client, fs)
    hass.config_entries.async_remove_subentry = MagicMock()

    await coordinator.async_handle_notification_action(
        _action_event(f"{ACTION_DELETE_PREFIX}__{entry.entry_id}__missing-sub")
    )

    fs.async_clear_subentry_state.assert_not_called()
    hass.config_entries.async_remove_subentry.assert_not_called()


@pytest.mark.asyncio
async def test_malformed_action_is_ignored(hass, mock_client):
    """An action id that doesn't match the expected shape is a no-op."""
    fs = _make_filters_store()
    coordinator, entry, sub = _build_coordinator(hass, mock_client, fs)

    await coordinator.async_handle_notification_action(_action_event("SOMETHING_ELSE"))

    fs.async_set_snooze.assert_not_called()
    fs.async_clear_subentry_state.assert_not_called()
