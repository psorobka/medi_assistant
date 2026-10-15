"""Account-scoped cancellation using synthetic planned appointments only."""

import asyncio
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.medi_assistant.cancellation import CancellationManager
from custom_components.medi_assistant.services import async_setup_booking_service


@pytest.fixture
def cancellation(hass):
    slot = {
        "id": "synthetic-private-cancellation-string",
        "date": "2099-02-03T10:00:00",
        "doctor": {"id": "1", "name": "Example Doctor"},
        "clinic": {"id": "2", "name": "Example Clinic"},
        "specialty": {"id": "3", "name": "Example Specialty"},
        "visitType": "OnSite",
        "state": "Planned",
        "attributes": {"canCancel": True, "isLateCancellation": False},
    }
    entry = MockConfigEntry(domain="medi_assistant")
    entry.add_to_hass(hass)
    entry.mock_state(hass, ConfigEntryState.LOADED)
    client = Mock()
    client.async_get_planned_appointments = AsyncMock(return_value=[copy.deepcopy(slot)])
    client.async_cancel_appointment = AsyncMock()
    manager = CancellationManager(hass, client, entry)
    entry.runtime_data = SimpleNamespace(booking=SimpleNamespace(cancellation=manager))
    return manager, client, entry, slot


async def test_list_and_duplicate_cancel(cancellation):
    manager, client, _, slot = cancellation
    public = (await manager.async_list())["appointments"][0]
    assert "id" not in public
    assert slot["id"] not in str(public)
    second = (await manager.async_list())["appointments"][0]
    assert second["cancellation_id"] == public["cancellation_id"]
    outcomes = await asyncio.gather(
        *(manager.async_cancel(public["cancellation_id"]) for _ in range(2))
    )
    assert all(outcome["success"] for outcome in outcomes)
    client.async_cancel_appointment.assert_awaited_once_with(slot["id"])


@pytest.mark.parametrize(
    "change,status",
    [
        ({"date": "2099-02-04T10:00:00"}, "unavailable"),
        ({"attributes": {"canCancel": False}}, "not_allowed"),
        ({"attributes": {"canCancel": True, "isLateCancellation": True}}, "confirmation_required"),
    ],
)
async def test_revalidate_exact_appointment(cancellation, change, status):
    manager, client, _, _ = cancellation
    public = (await manager.async_list())["appointments"][0]
    client.async_get_planned_appointments.return_value[0].update(change)
    assert (await manager.async_cancel(public["cancellation_id"]))["status"] == status
    client.async_cancel_appointment.assert_not_awaited()


async def test_unknown_write_not_retried(cancellation):
    manager, client, _, _ = cancellation
    public = (await manager.async_list())["appointments"][0]
    client.async_cancel_appointment.side_effect = TimeoutError()
    for _ in range(2):
        assert (await manager.async_cancel(public["cancellation_id"]))["status"] == "unknown"
    client.async_cancel_appointment.assert_awaited_once()


async def test_missing_expired_and_failed_read(cancellation):
    from unittest.mock import patch

    manager, client, _, _ = cancellation
    assert (await manager.async_cancel("foreign"))["status"] == "expired"
    public = (await manager.async_list())["appointments"][0]
    with patch(
        "custom_components.medi_assistant.cancellation.monotonic", return_value=float("inf")
    ):
        assert (await manager.async_cancel(public["cancellation_id"]))["status"] == "expired"
    client.async_get_planned_appointments.side_effect = TimeoutError()
    assert (await manager.async_list())["status"] == "read_failed"
    client.async_cancel_appointment.assert_not_awaited()


async def test_service_account_isolation(hass, cancellation, enable_custom_integrations):
    manager, client, entry, _ = cancellation
    other = MockConfigEntry(domain="medi_assistant")
    other.add_to_hass(hass)
    other.mock_state(hass, ConfigEntryState.LOADED)
    other_client = Mock()
    other_client.async_cancel_appointment = AsyncMock()
    other.runtime_data = SimpleNamespace(
        booking=SimpleNamespace(cancellation=CancellationManager(hass, other_client, other))
    )
    entity = er.async_get(hass).async_get_or_create(
        "sensor", "medi_assistant", "synthetic-search", config_entry=entry
    )
    async_setup_booking_service(hass)
    listing = await hass.services.async_call(
        "medi_assistant",
        "list_booked_appointments",
        {"entity_id": entity.entity_id},
        blocking=True,
        return_response=True,
    )
    public = listing["appointments"][0]
    outcome = await hass.services.async_call(
        "medi_assistant",
        "cancel_appointment",
        {"cancellation_id": public["cancellation_id"]},
        blocking=True,
        return_response=True,
    )
    assert outcome["success"]
    client.async_cancel_appointment.assert_awaited_once()
    other_client.async_cancel_appointment.assert_not_awaited()


async def test_list_auth_failure_starts_reauth(cancellation, hass):
    from unittest.mock import patch
    from custom_components.medi_assistant.exceptions import InvalidGrant

    manager, client, _, _ = cancellation
    client.async_get_planned_appointments.side_effect = InvalidGrant()
    with patch("homeassistant.config_entries.ConfigEntry.async_start_reauth") as reauth:
        assert (await manager.async_list())["status"] == "auth_required"
        reauth.assert_called_once_with(hass)


async def test_cancel_read_failure_never_writes(cancellation):
    manager, client, _, _ = cancellation
    public = (await manager.async_list())["appointments"][0]
    client.async_get_planned_appointments.side_effect = TimeoutError()
    assert (await manager.async_cancel(public["cancellation_id"]))["status"] == "read_failed"
    client.async_cancel_appointment.assert_not_awaited()


async def test_cancel_rejection_returns_readable_result(cancellation):
    from custom_components.medi_assistant.exceptions import BookingError

    manager, client, _, _ = cancellation
    public = (await manager.async_list())["appointments"][0]
    client.async_cancel_appointment.side_effect = BookingError("rejected")
    outcome = await manager.async_cancel(public["cancellation_id"])
    assert not outcome["success"]
    assert outcome["status"] == "rejected"
    assert outcome["message"]


async def test_cancel_auth_failure(cancellation, hass):
    from unittest.mock import patch
    from custom_components.medi_assistant.exceptions import InvalidGrant

    manager, client, _, _ = cancellation
    public = (await manager.async_list())["appointments"][0]
    client.async_cancel_appointment.side_effect = InvalidGrant()
    with patch("homeassistant.config_entries.ConfigEntry.async_start_reauth") as reauth:
        assert (await manager.async_cancel(public["cancellation_id"]))["status"] == "auth_required"
        reauth.assert_called_once_with(hass)


async def test_cancel_revalidation_auth_failure(cancellation, hass):
    from unittest.mock import patch
    from custom_components.medi_assistant.exceptions import InvalidGrant

    manager, client, _, _ = cancellation
    public = (await manager.async_list())["appointments"][0]
    client.async_get_planned_appointments.side_effect = InvalidGrant()
    with patch("homeassistant.config_entries.ConfigEntry.async_start_reauth"):
        assert (await manager.async_cancel(public["cancellation_id"]))["status"] == "auth_required"
    client.async_cancel_appointment.assert_not_awaited()


async def test_late_cancellation_requires_explicit_confirmation(cancellation):
    manager, client, _, slot = cancellation
    slot["attributes"]["isLateCancellation"] = True
    client.async_get_planned_appointments.return_value = [slot]
    public = (await manager.async_list())["appointments"][0]
    assert public["late_cancellation"]
    assert (await manager.async_cancel(public["cancellation_id"]))[
        "status"
    ] == "confirmation_required"
    client.async_cancel_appointment.assert_not_awaited()
    outcome = await manager.async_cancel(public["cancellation_id"], confirm_late_cancellation=True)
    assert outcome["success"]
    client.async_cancel_appointment.assert_awaited_once_with(slot["id"])
