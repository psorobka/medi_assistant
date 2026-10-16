"""Exact-slot booking, duplicate clicks, account isolation and HA services."""

import asyncio
import copy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState, ConfigSubentry
from homeassistant.core import SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.medi_assistant.booking import BookingManager
from custom_components.medi_assistant.exceptions import BookingError, InvalidGrant
from custom_components.medi_assistant.services import async_setup_booking_service


@pytest.fixture
def booking_slot():
    return json.loads((Path(__file__).parent / "fixtures/booking_slot.json").read_text())


@pytest.fixture
def booking(hass, booking_slot):
    entry = MockConfigEntry(domain="medi_assistant", title="Konto testowe")
    entry.add_to_hass(hass)
    subentry = ConfigSubentry(
        data={"region_id": 1, "specialty_id": 10},
        subentry_type="search",
        title="Szukajka testowa",
        unique_id=None,
    )
    hass.config_entries.async_add_subentry(entry, subentry)
    client = Mock()
    client.async_find_appointments = AsyncMock(return_value=[copy.deepcopy(booking_slot)])
    client.async_check_booking = AsyncMock()
    client.async_book_appointment = AsyncMock()
    manager = BookingManager(hass, client, entry)
    entry.runtime_data = SimpleNamespace(
        auth_session=Mock(closed=True),
        booking=manager,
        coordinator=Mock(async_request_refresh=AsyncMock()),
    )
    entry.mock_state(hass, ConfigEntryState.LOADED)
    offer = manager.public_slots(subentry.subentry_id, [booking_slot])[0]
    return manager, client, entry, subentry, offer


async def test_fresh_slot_handle_used_and_secret_not_published(booking, booking_slot):
    manager, client, _, _, offer = booking
    client.async_find_appointments.return_value[0]["bookingString"] = "fresh-synthetic-handle"
    assert "bookingString" not in offer
    assert (await manager.async_book(offer["booking_id"]))["success"]
    client.async_book_appointment.assert_awaited_once_with("fresh-synthetic-handle")
    assert (
        client.async_find_appointments.call_args.kwargs["start_date"]
        == booking_slot["appointmentDate"][:10]
    )


async def test_double_click_and_overlapping_search_only_write_once(hass, booking, booking_slot):
    manager, client, entry, subentry, offer = booking
    other = ConfigSubentry(
        data=dict(subentry.data), subentry_type="search", title="Druga", unique_id=None
    )
    hass.config_entries.async_add_subentry(entry, other)
    second = manager.public_slots(other.subentry_id, [booking_slot])[0]
    outcomes = await asyncio.gather(
        *(
            manager.async_book(key)
            for key in (offer["booking_id"], offer["booking_id"], second["booking_id"])
        )
    )
    assert all(outcome["success"] for outcome in outcomes)
    client.async_book_appointment.assert_awaited_once()


@pytest.mark.parametrize("change", ["missing", "doctor", "time", "ambiguous"])
async def test_never_substitute_another_slot(booking, change):
    manager, client, _, _, offer = booking
    slots = client.async_find_appointments.return_value
    if change == "missing":
        slots.clear()
    elif change == "doctor":
        slots[0]["doctor"]["id"] = "other-doctor"
    elif change == "time":
        slots[0]["appointmentDate"] = "2099-07-01T10:00:00"
    else:
        slots.append(copy.deepcopy(slots[0]))
    assert (await manager.async_book(offer["booking_id"]))["status"] == "unavailable"
    client.async_book_appointment.assert_not_awaited()


async def test_expired_and_removed_search(hass, booking):
    manager, client, entry, subentry, offer = booking
    with patch("custom_components.medi_assistant.booking.monotonic", return_value=float("inf")):
        assert (await manager.async_book(offer["booking_id"]))["status"] == "expired"
    hass.config_entries.async_remove_subentry(entry, subentry.subentry_id)
    assert (await manager.async_book(offer["booking_id"]))["status"] == "expired"
    client.async_book_appointment.assert_not_awaited()


@pytest.mark.parametrize("code", ["payment_required", "confirmation_required", "unsupported"])
async def test_preflight_rejection_does_not_write(booking, code):
    manager, client, _, _, offer = booking
    client.async_check_booking.side_effect = BookingError(code)
    assert (await manager.async_book(offer["booking_id"]))["status"] == code
    client.async_book_appointment.assert_not_awaited()


@pytest.mark.parametrize("error", [TimeoutError(), BookingError("unknown")])
async def test_ambiguous_write_is_not_repeated(booking, error):
    manager, client, _, _, offer = booking
    client.async_book_appointment.side_effect = error
    assert (await manager.async_book(offer["booking_id"]))["status"] == "unknown"
    assert (await manager.async_book(offer["booking_id"]))["status"] == "unknown"
    client.async_book_appointment.assert_awaited_once()


async def test_expired_auth_starts_reauth(booking):
    manager, client, entry, _, offer = booking
    client.async_check_booking.side_effect = InvalidGrant()
    with patch.object(type(entry), "async_start_reauth") as reauth:
        assert (await manager.async_book(offer["booking_id"]))["status"] == "auth_required"
    reauth.assert_called_once()


async def test_service_response_and_account_isolation(hass, booking, enable_custom_integrations):
    manager, client, _, _, offer = booking
    other_entry = MockConfigEntry(domain="medi_assistant", title="Inne konto")
    other_entry.add_to_hass(hass)
    other_client = Mock(async_book_appointment=AsyncMock())
    other_entry.runtime_data = SimpleNamespace(
        booking=BookingManager(hass, other_client, other_entry)
    )
    other_entry.mock_state(hass, ConfigEntryState.LOADED)
    async_setup_booking_service(hass)
    assert (
        hass.services.supports_response("medi_assistant", "book_appointment")
        == SupportsResponse.ONLY
    )
    outcome = await hass.services.async_call(
        "medi_assistant",
        "book_appointment",
        {"booking_id": offer["booking_id"]},
        blocking=True,
        return_response=True,
    )
    assert outcome["status"] == "booked"
    client.async_book_appointment.assert_awaited_once()
    other_client.async_book_appointment.assert_not_awaited()
    assert manager.owns(offer["booking_id"])


async def test_list_offers_service_is_read_only(hass, booking, enable_custom_integrations):
    _, client, entry, _, offer = booking
    entity = er.async_get(hass).async_get_or_create(
        "sensor", "medi_assistant", "test-search", config_entry=entry
    )
    hass.states.async_set(entity.entity_id, 1, {"appointments": [offer]})
    async_setup_booking_service(hass)
    response = await hass.services.async_call(
        "medi_assistant",
        "list_booking_offers",
        {"entity_id": entity.entity_id},
        blocking=True,
        return_response=True,
    )
    assert response["appointments"][0]["booking_id"] == offer["booking_id"]
    client.async_book_appointment.assert_not_awaited()
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            "medi_assistant",
            "list_booking_offers",
            {"entity_id": "sensor.other"},
            blocking=True,
            return_response=True,
        )


async def test_integration_setup_publishes_offers(hass, enable_custom_integrations, booking_slot):
    from custom_components.medi_assistant import MedicoverRuntimeData

    entry = MockConfigEntry(domain="medi_assistant", data={"access_token": "synthetic"})
    entry.add_to_hass(hass)
    subentry = ConfigSubentry(
        data={"region_id": 1, "specialty_id": 10},
        subentry_type="search",
        title="Test",
        unique_id=None,
    )
    hass.config_entries.async_add_subentry(entry, subentry)
    with (
        patch("custom_components.medi_assistant.MedicoverAuth") as auth_class,
        patch("custom_components.medi_assistant.MedicoverClient") as client_class,
        patch("custom_components.medi_assistant.FiltersStore") as store_class,
    ):
        auth_class.return_value.is_token_valid.return_value = True
        auth_class.return_value._expires_at = 9999999999
        client_class.return_value._auth = auth_class.return_value
        client_class.return_value.async_get_personal_data = AsyncMock(
            return_value={"firstName": "Konto", "lastName": "syntetyczne"}
        )
        client_class.return_value.async_find_appointments = AsyncMock(return_value=[booking_slot])
        store_class.return_value.async_ensure = AsyncMock()
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert isinstance(entry.runtime_data, MedicoverRuntimeData)
        sensors = [
            state
            for state in hass.states.async_all("sensor")
            if state.attributes.get("appointments")
        ]
        assert len(sensors) == 1
        offer = sensors[0].attributes["appointments"][0]
        assert "bookingString" not in offer
        assert offer["booking_id"]
        registry_entry = er.async_get(hass).async_get(sensors[0].entity_id)
        assert registry_entry.config_subentry_id == subentry.subentry_id
        assert await hass.config_entries.async_unload(entry.entry_id)


async def test_native_notification_reserves_exact_slot(hass, booking, booking_slot):
    hass.config.language = "pl"
    from homeassistant.core import Event
    from pytest_homeassistant_custom_component.common import async_mock_service
    from custom_components.medi_assistant.coordinator import MedicoverCoordinator

    manager, client, entry, subentry, offer = booking
    hass.config_entries.async_update_subentry(
        entry, subentry, data={**subentry.data, "notify_target": ["notify.mobile_app_test_phone"]}
    )
    subentry = entry.subentries[subentry.subentry_id]
    calls = async_mock_service(hass, "notify", "mobile_app_test_phone")
    coordinator = MedicoverCoordinator(hass, entry, client, Mock())
    coordinator.booking = manager
    coordinator.async_request_refresh = AsyncMock()

    await coordinator._async_notify(subentry, "notify.mobile_app_test_phone", [booking_slot])
    await hass.async_block_till_done()
    buttons = calls[0].data["data"]["actions"]
    assert [button["title"] for button in buttons] == [
        "Zarezerwuj",
        "Wycisz szukajkę (24h)",
        "Usuń szukajkę",
    ]
    assert "uri" not in buttons[0]
    assert buttons[0]["action"].endswith(offer["booking_id"])
    assert "Nowy termin:" in calls[0].data["message"]
    event = Event("mobile_app_notification_action", {"action": buttons[0]["action"]})
    await coordinator.async_handle_notification_action(event)
    await coordinator.async_handle_notification_action(event)
    await hass.async_block_till_done()
    client.async_book_appointment.assert_awaited_once_with("synthetic-slot-handle")
    assert calls[-1].data["message"] == "Wizyta została zarezerwowana."
    assert "actions" not in calls[-1].data["data"]


async def test_native_notification_ignores_foreign_or_forged_action(hass, booking):
    from homeassistant.core import Event
    from custom_components.medi_assistant.coordinator import MedicoverCoordinator

    manager, client, entry, subentry, offer = booking
    coordinator = MedicoverCoordinator(hass, entry, client, Mock())
    coordinator.booking = manager
    for action in (
        None,
        "MEDI_BOOK__bad-format",
        f"MEDI_BOOK__other__{subentry.subentry_id}__{offer['booking_id']}",
        f"MEDI_BOOK__{entry.entry_id}__{subentry.subentry_id}__unknown",
    ):
        await coordinator.async_handle_notification_action(
            Event("mobile_app_notification_action", {"action": action})
        )
    client.async_book_appointment.assert_not_awaited()


async def test_native_result_delivery_failure_keeps_booking(hass, booking):
    from homeassistant.core import Event
    from custom_components.medi_assistant.coordinator import MedicoverCoordinator

    manager, client, entry, subentry, offer = booking
    hass.config_entries.async_update_subentry(
        entry, subentry, data={**subentry.data, "notify_target": "notify.no_such_service"}
    )
    coordinator = MedicoverCoordinator(hass, entry, client, Mock())
    coordinator.booking = manager
    coordinator.async_request_refresh = AsyncMock()
    await coordinator.async_handle_notification_action(
        Event(
            "mobile_app_notification_action",
            {
                "action": f"MEDI_BOOK__{entry.entry_id}__{subentry.subentry_id}__{offer['booking_id']}"
            },
        )
    )
    assert (await manager.async_book(offer["booking_id"]))["success"]
    client.async_book_appointment.assert_awaited_once()


async def test_preflight_500_is_not_an_ambiguous_booking(booking):
    manager, client, _, _, offer = booking
    client.async_check_booking.side_effect = BookingError("unknown")
    assert (await manager.async_book(offer["booking_id"]))["status"] == "preflight_failed"
    client.async_book_appointment.assert_not_awaited()


async def test_expired_native_button_reports_failure_after_offer_pruned(hass, booking):
    from homeassistant.core import Event
    from pytest_homeassistant_custom_component.common import async_mock_service
    from custom_components.medi_assistant.coordinator import MedicoverCoordinator

    manager, client, entry, subentry, offer = booking
    hass.config_entries.async_update_subentry(
        entry, subentry, data={**subentry.data, "notify_target": "notify.mobile_app_test_phone"}
    )
    coordinator = MedicoverCoordinator(hass, entry, client, Mock())
    coordinator.booking = manager
    with patch("custom_components.medi_assistant.booking.monotonic", return_value=float("inf")):
        manager.public_slots(subentry.subentry_id, [])
    calls = async_mock_service(hass, "notify", "mobile_app_test_phone")
    await coordinator.async_handle_notification_action(
        Event(
            "mobile_app_notification_action",
            {
                "action": f"MEDI_BOOK__{entry.entry_id}__{subentry.subentry_id}__{offer['booking_id']}"
            },
        )
    )
    await hass.async_block_till_done()
    assert "wygasł" in calls[0].data["message"]
    client.async_book_appointment.assert_not_awaited()


async def test_card_searches_use_current_handles_and_account_scope(hass, booking, booking_slot):
    manager, client, entry, subentry, _ = booking
    entry.runtime_data.coordinator.data = {subentry.subentry_id: [booking_slot] * 22}
    entry.runtime_data.coordinator.last_update_success = True
    async_setup_booking_service(hass)
    response = await hass.services.async_call(
        "medi_assistant", "list_booking_offers", {}, blocking=True, return_response=True
    )
    account = response["accounts"][0]
    assert account["account_id"] == entry.entry_id
    search = account["searches"][0]
    assert search["name"] == subentry.title
    assert search["count"] == 22
    assert search["truncated"]
    assert len(search["appointments"]) == 20
    assert manager.owns_search(search["appointments"][0]["booking_id"], subentry.subentry_id)
    assert "bookingString" not in str(response)
    client.async_find_appointments.assert_not_awaited()
    client.async_book_appointment.assert_not_awaited()


async def test_card_search_failure_is_not_an_empty_result(hass, booking):
    _, _, entry, subentry, _ = booking
    entry.runtime_data.coordinator.data = {subentry.subentry_id: []}
    entry.runtime_data.coordinator.last_update_success = False
    async_setup_booking_service(hass)
    response = await hass.services.async_call(
        "medi_assistant", "list_booking_offers", {}, blocking=True, return_response=True
    )
    search = response["accounts"][0]["searches"][0]
    assert not search["available"]
    assert search["message"]
    assert search["appointments"] == []


async def test_card_manual_refresh_fetches_new_search_results(hass, booking):
    _, _, entry, subentry, _ = booking
    entry.runtime_data.coordinator.data = {subentry.subentry_id: []}
    entry.runtime_data.coordinator.last_update_success = True
    async_setup_booking_service(hass)
    response = await hass.services.async_call(
        "medi_assistant",
        "list_booking_offers",
        {"refresh": True},
        blocking=True,
        return_response=True,
    )
    assert response["accounts"][0]["searches"][0]["available"]
    entry.runtime_data.coordinator.async_request_refresh.assert_awaited_once()


async def test_mobile_offers_have_exact_handles_unique_tags_and_overflow(
    hass, booking, booking_slot
):
    from homeassistant.core import Event
    from pytest_homeassistant_custom_component.common import async_mock_service
    from custom_components.medi_assistant.coordinator import MedicoverCoordinator

    manager, client, entry, subentry, _ = booking
    hass.config_entries.async_update_entry(
        entry, options={"notification_path": "/dashboard-health/medicover"}
    )
    hass.config_entries.async_update_subentry(
        entry, subentry, data={**subentry.data, "notify_target": ["notify.mobile_app_test_phone"]}
    )
    subentry = entry.subentries[subentry.subentry_id]
    slots = []
    for day in range(1, 7):
        slot = copy.deepcopy(booking_slot)
        slot["appointmentDate"] = f"2026-11-{day:02d}T10:00:00"
        slot["bookingString"] = f"synthetic-slot-{day}"
        slots.append(slot)
    client.async_find_appointments.return_value = slots
    calls = async_mock_service(hass, "notify", "mobile_app_test_phone")
    coordinator = MedicoverCoordinator(hass, entry, client, Mock())
    coordinator.booking = manager
    coordinator.async_request_refresh = AsyncMock()
    await coordinator._async_notify(subentry, "notify.mobile_app_test_phone", list(reversed(slots)))
    await hass.async_block_till_done()
    assert len(calls) == 4
    assert len({call.data["data"]["tag"] for call in calls}) == 4
    for index, call in enumerate(calls[:3]):
        data = call.data["data"]
        assert data["url"] == data["clickAction"] == "/dashboard-health/medicover"
        assert f"2026-11-{index + 1:02d} 10:00" in call.data["message"]
        assert f"2026-11-{index + 2:02d} 10:00" not in call.data["message"]
        handle = data["actions"][0]["action"].split("__")[-1]
        assert manager._offers[handle].identity[0] == slots[index]["appointmentDate"]
        assert "bookingString" not in str(call.data)
    summary = calls[3]
    assert "Pozostałe nowe terminy: 3" in summary.data["message"]
    assert not any(
        action["action"].startswith("MEDI_BOOK__") for action in summary.data["data"]["actions"]
    )
    # Select the second notification: the result must replace that notification only.
    selected = calls[1].data["data"]
    await coordinator.async_handle_notification_action(
        Event("mobile_app_notification_action", {"action": selected["actions"][0]["action"]})
    )
    await hass.async_block_till_done()
    client.async_book_appointment.assert_awaited_once_with("synthetic-slot-2")
    assert calls[-1].data["data"]["tag"] == selected["tag"]
    assert calls[-1].data["data"]["clickAction"] == selected["clickAction"]
    assert "actions" not in calls[-1].data["data"]


async def test_mobile_unbookable_offer_has_no_booking_action_and_no_default_link(
    hass, booking, booking_slot
):
    from pytest_homeassistant_custom_component.common import async_mock_service
    from custom_components.medi_assistant.coordinator import MedicoverCoordinator

    _, client, entry, subentry, _ = booking
    calls = async_mock_service(hass, "notify", "mobile_app_test_phone")
    slot = {key: value for key, value in booking_slot.items() if key != "bookingString"}
    coordinator = MedicoverCoordinator(hass, entry, client, Mock())
    await coordinator._async_notify(subentry, "notify.mobile_app_test_phone", [slot])
    await coordinator._async_notify(subentry, "notify.mobile_app_test_phone", [slot])
    await hass.async_block_till_done()
    data = calls[0].data["data"]
    assert data["tag"] == calls[1].data["data"]["tag"]
    assert "url" not in data and "clickAction" not in data
    assert not any(action["action"].startswith("MEDI_BOOK__") for action in data["actions"])


async def test_mobile_notification_failure_is_contained(hass, booking, booking_slot, caplog):
    from custom_components.medi_assistant.coordinator import MedicoverCoordinator

    _, client, entry, subentry, _ = booking
    coordinator = MedicoverCoordinator(hass, entry, client, Mock())
    with patch.object(
        type(hass.services),
        "async_call",
        AsyncMock(side_effect=RuntimeError("private-synthetic-detail")),
    ):
        await coordinator._async_notify(subentry, "notify.mobile_app_test_phone", [booking_slot])
    assert "Notification delivery failed (RuntimeError)" in caplog.text
    assert "private-synthetic-detail" not in caplog.text
