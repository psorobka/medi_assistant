"""Tests for MedicoverSearchSensor."""
from __future__ import annotations

from types import MappingProxyType
from unittest.mock import MagicMock

import pytest
from homeassistant.config_entries import ConfigSubentry
from homeassistant.core import HomeAssistant

from custom_components.medi_assistant.const import SUBENTRY_TYPE_SEARCH
from custom_components.medi_assistant.sensor import (
    MedicoverSearchSensor,
    async_setup_entry,
)

from .conftest import MOCK_SUBENTRY_DATA


def _make_subentry(subentry_id: str = "sub-1") -> ConfigSubentry:
    return ConfigSubentry(
        subentry_id=subentry_id,
        subentry_type=SUBENTRY_TYPE_SEARCH,
        title="Kardiolog · Warszawa",
        data=MappingProxyType(MOCK_SUBENTRY_DATA),
        unique_id=None,
    )


def _make_sensor(slots: list, subentry_id: str = "sub-1") -> MedicoverSearchSensor:
    mock_coordinator = MagicMock()
    mock_coordinator.data = {subentry_id: slots}
    mock_coordinator.last_update_success = True

    mock_entry = MagicMock()
    mock_entry.entry_id = "entry-abc"

    subentry = _make_subentry(subentry_id)
    sensor = MedicoverSearchSensor(mock_coordinator, mock_entry, subentry)
    return sensor


def test_sensor_state_describes_earliest_slot(slots_data):
    """State is a readable summary of the earliest slot: date · doctor · clinic."""
    sensor = _make_sensor(slots_data["items"])
    value = sensor.native_value
    assert value == "2026-07-01 10:00 · dr Jan Nowak · Klinika Centrum"


def test_sensor_state_no_slots_message():
    sensor = _make_sensor([])
    assert sensor.native_value == "Brak terminów"


def test_sensor_count_attribute(slots_data):
    """Slot count moved to an attribute so automations can still use it."""
    sensor = _make_sensor(slots_data["items"])
    assert sensor.extra_state_attributes["count"] == 2


def test_sensor_count_attribute_zero():
    sensor = _make_sensor([])
    assert sensor.extra_state_attributes["count"] == 0


def test_sensor_state_none_when_no_coordinator_data():
    sensor = _make_sensor([])
    sensor.coordinator.data = None
    assert sensor.native_value is None


def test_sensor_earliest_attribute(slots_data):
    sensor = _make_sensor(slots_data["items"])
    attrs = sensor.extra_state_attributes
    assert attrs["earliest"] == "2026-07-01T10:00:00"


def test_sensor_appointments_attribute(slots_data):
    sensor = _make_sensor(slots_data["items"])
    appointments = sensor.extra_state_attributes["appointments"]
    assert len(appointments) == 2
    assert appointments[0]["clinic"] == "Klinika Centrum"
    assert appointments[0]["doctor"] == "dr Jan Nowak"


def test_sensor_unique_id():
    sensor = _make_sensor([])
    assert sensor.unique_id == "entry-abc_sub-1"


def test_sensor_name_reads_subentry_title_live():
    """Name is read live so a reconfigured (renamed) search updates w/o reload."""
    sensor = _make_sensor([])
    assert sensor.name == "Kardiolog · Warszawa"
    object.__setattr__(sensor._subentry, "title", "Dermatolog · Kraków")
    assert sensor.name == "Dermatolog · Kraków"


@pytest.mark.asyncio
async def test_setup_adds_sensor_per_search_with_subentry_id(hass: HomeAssistant):
    """Setup adds one sensor per search, tagged with its config_subentry_id."""
    entry = MagicMock()
    entry.runtime_data.coordinator = MagicMock()
    sub = _make_subentry("s1")
    entry.subentries = {"s1": sub}

    added: list[tuple] = []

    def _add(entities, config_subentry_id=None):
        added.append((list(entities), config_subentry_id))

    await async_setup_entry(hass, entry, _add)

    assert len(added) == 1
    assert added[0][1] == "s1"
    assert isinstance(added[0][0][0], MedicoverSearchSensor)


@pytest.mark.asyncio
async def test_setup_dynamically_adds_new_search(hass: HomeAssistant):
    """A search added after setup is created via the update listener (no reload)."""
    entry = MagicMock()
    entry.runtime_data.coordinator = MagicMock()
    sub1 = _make_subentry("s1")
    entry.subentries = {"s1": sub1}

    listeners: list = []
    entry.add_update_listener = lambda cb: listeners.append(cb) or (lambda: None)

    added: list[tuple] = []

    def _add(entities, config_subentry_id=None):
        added.append((list(entities), config_subentry_id))

    await async_setup_entry(hass, entry, _add)
    assert len(added) == 1  # s1

    # A second search appears; the registered listener picks it up.
    sub2 = _make_subentry("s2")
    entry.subentries = {"s1": sub1, "s2": sub2}
    await listeners[0](hass, entry)

    assert len(added) == 2
    assert added[1][1] == "s2"
    # Existing search isn't re-added.
    await listeners[0](hass, entry)
    assert len(added) == 2


def test_sensor_attributes_capped_when_many_slots(slots_data):
    """Attributes cap the appointment list (recorder guard) but count is full."""
    base = slots_data["items"][0]
    many = []
    for i in range(25):
        s = dict(base)
        s["appointmentDate"] = f"2026-07-{i + 1:02d}T10:00:00"
        many.append(s)

    sensor = _make_sensor(many)
    attrs = sensor.extra_state_attributes

    assert attrs["count"] == 25
    assert len(attrs["appointments"]) == 20
    assert attrs["appointments_truncated"] is True
    # Capped to the soonest, earliest first.
    assert attrs["appointments"][0]["date"].startswith("2026-07-01")
    assert attrs["appointments"][-1]["date"].startswith("2026-07-20")


def test_sensor_attributes_not_truncated_when_few(slots_data):
    sensor = _make_sensor(slots_data["items"])  # 2 slots
    attrs = sensor.extra_state_attributes
    assert attrs["appointments_truncated"] is False
    assert len(attrs["appointments"]) == 2
