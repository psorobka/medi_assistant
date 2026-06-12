"""Tests for the SearchSubentryFlowHandler (region-first add/reconfigure)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.medi_assistant.config_flow import _find_name
from custom_components.medi_assistant.const import (
    CONF_CLINIC_ID,
    CONF_DATE_FROM,
    CONF_DOCTOR_ID,
    CONF_NOTIFY_TARGET,
    CONF_REGION_ID,
    CONF_SPECIALTY_ID,
    DOMAIN,
    SUBENTRY_TYPE_SEARCH,
)

from .conftest import MOCK_ENTRY_DATA, MOCK_SUBENTRY_DATA


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_DEFAULT_REGIONS = [{"id": 204, "value": "Warszawa"}, {"id": 205, "value": "Kraków"}]
_DEFAULT_SPECIALTIES = [{"id": 9, "value": "Kardiolog"}, {"id": 14, "value": "Dermatolog"}]


def _make_runtime_data(
    regions=_DEFAULT_REGIONS,
    specialties=_DEFAULT_SPECIALTIES,
    clinics=None,
    doctors=None,
    refresh_raises=False,
):
    """Build a minimal runtime_data mock for subentry flow tests."""
    filters_store = MagicMock()
    filters_store.regions = list(regions)
    filters_store.specialties = list(specialties)
    # Specialties are region-scoped now; the mock returns the same list for any region.
    filters_store.get_specialties_for_region = MagicMock(return_value=list(specialties))
    filters_store.get_clinics = MagicMock(return_value=clinics or [])
    filters_store.get_doctors = MagicMock(return_value=doctors or [])

    if refresh_raises:
        filters_store.async_refresh = AsyncMock(side_effect=Exception("network error"))
    else:
        filters_store.async_refresh = AsyncMock()

    filters_store.async_refresh_specialties = AsyncMock()
    filters_store.async_refresh_clinics_doctors = AsyncMock()

    rd = MagicMock()
    rd.filters_store = filters_store
    rd.client = MagicMock()
    return rd


async def _init_subentry_flow(hass: HomeAssistant, entry: MockConfigEntry):
    """Start a new subentry flow and return the first (region) result."""
    return await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_SEARCH),
        context={"source": "user"},
    )


async def _to_details(hass, flow_id, region="204", specialty="9"):
    """Submit the region then the specialty step; return the details-step result."""
    await hass.config_entries.subentries.async_configure(flow_id, {CONF_REGION_ID: region})
    return await hass.config_entries.subentries.async_configure(
        flow_id, {CONF_SPECIALTY_ID: specialty}
    )


# ---------------------------------------------------------------------------
# Step 1: region
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_subentry_flow_first_step_is_region_only(hass: HomeAssistant):
    """First step shows the region selector only (specialty comes next, scoped)."""
    entry = MockConfigEntry(domain=DOMAIN, data=MOCK_ENTRY_DATA, title="Jan Kowalski")
    entry.add_to_hass(hass)
    entry.runtime_data = _make_runtime_data()

    result = await _init_subentry_flow(hass, entry)

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"
    assert CONF_REGION_ID in result["data_schema"].schema
    assert CONF_SPECIALTY_ID not in result["data_schema"].schema


@pytest.mark.asyncio
async def test_subentry_flow_no_filters_aborts(hass: HomeAssistant):
    """Abort when regions are empty and refresh also fails."""
    entry = MockConfigEntry(domain=DOMAIN, data=MOCK_ENTRY_DATA, title="Jan Kowalski")
    entry.add_to_hass(hass)
    entry.runtime_data = _make_runtime_data(regions=[], specialties=[], refresh_raises=True)

    result = await _init_subentry_flow(hass, entry)

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "cannot_load_filters"


@pytest.mark.asyncio
async def test_subentry_flow_empty_filters_refreshes_and_continues(hass: HomeAssistant):
    """When stored regions are empty, the flow refreshes them and shows the form."""
    entry = MockConfigEntry(domain=DOMAIN, data=MOCK_ENTRY_DATA, title="Jan Kowalski")
    entry.add_to_hass(hass)

    rd = _make_runtime_data(regions=[], specialties=[])

    async def _fake_refresh(_client):
        rd.filters_store.regions = [{"id": 204, "value": "Warszawa"}]

    rd.filters_store.async_refresh = AsyncMock(side_effect=_fake_refresh)
    entry.runtime_data = rd

    result = await _init_subentry_flow(hass, entry)

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"
    rd.filters_store.async_refresh.assert_called_once()


@pytest.mark.asyncio
async def test_subentry_flow_defaults_region_to_last_search(hass: HomeAssistant):
    """Adding another search defaults the region to the last-added search's region."""
    from homeassistant.config_entries import ConfigSubentry
    from homeassistant.util.read_only_dict import ReadOnlyDict

    entry = MockConfigEntry(domain=DOMAIN, data=MOCK_ENTRY_DATA, title="Jan Kowalski")
    entry.add_to_hass(hass)
    rd = _make_runtime_data()
    entry.runtime_data = rd

    existing = ConfigSubentry(
        data=ReadOnlyDict({**MOCK_SUBENTRY_DATA, CONF_REGION_ID: 205}),
        subentry_type=SUBENTRY_TYPE_SEARCH,
        title="Dermatolog · Kraków",
        unique_id=None,
    )
    hass.config_entries.async_add_subentry(entry, existing)

    result = await _init_subentry_flow(hass, entry)
    # Submit the region step with no value → the default (last region 205) applies.
    result2 = await hass.config_entries.subentries.async_configure(result["flow_id"], {})

    assert result2["step_id"] == "specialty"
    rd.filters_store.async_refresh_specialties.assert_called_once_with(rd.client, 205)


# ---------------------------------------------------------------------------
# Step 2: specialty (region-scoped)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_subentry_flow_loads_region_scoped_specialties(hass: HomeAssistant):
    """Picking a region fetches specialties for THAT region and shows them."""
    entry = MockConfigEntry(domain=DOMAIN, data=MOCK_ENTRY_DATA, title="Jan Kowalski")
    entry.add_to_hass(hass)
    rd = _make_runtime_data()
    entry.runtime_data = rd

    result = await _init_subentry_flow(hass, entry)
    result2 = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_REGION_ID: "204"}
    )

    assert result2["step_id"] == "specialty"
    assert CONF_SPECIALTY_ID in result2["data_schema"].schema
    rd.filters_store.async_refresh_specialties.assert_called_once_with(rd.client, 204)


# ---------------------------------------------------------------------------
# Step 3: details → CREATE_ENTRY
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_subentry_flow_happy_path_creates_entry(hass: HomeAssistant):
    """Full flow: region → specialty → skip optional details → subentry created."""
    entry = MockConfigEntry(domain=DOMAIN, data=MOCK_ENTRY_DATA, title="Jan Kowalski")
    entry.add_to_hass(hass)
    entry.runtime_data = _make_runtime_data()

    result = await _init_subentry_flow(hass, entry)
    details = await _to_details(hass, result["flow_id"])

    assert details["type"] == FlowResultType.FORM
    assert details["step_id"] == "details"

    result3 = await hass.config_entries.subentries.async_configure(result["flow_id"], {})

    assert result3["type"] == FlowResultType.CREATE_ENTRY
    assert result3["data"][CONF_REGION_ID] == 204
    assert result3["data"][CONF_SPECIALTY_ID] == 9


@pytest.mark.asyncio
async def test_subentry_flow_title_reflects_specialty_and_region(hass: HomeAssistant):
    """Created subentry title should include specialty and region names."""
    entry = MockConfigEntry(domain=DOMAIN, data=MOCK_ENTRY_DATA, title="Jan Kowalski")
    entry.add_to_hass(hass)
    entry.runtime_data = _make_runtime_data()

    result = await _init_subentry_flow(hass, entry)
    await _to_details(hass, result["flow_id"])
    result3 = await hass.config_entries.subentries.async_configure(result["flow_id"], {})

    assert "Kardiolog" in result3["title"]
    assert "Warszawa" in result3["title"]


@pytest.mark.asyncio
async def test_subentry_flow_with_clinic_and_doctor(hass: HomeAssistant, filters_data):
    """Optional clinic and doctor selections are stored in subentry data."""
    entry = MockConfigEntry(domain=DOMAIN, data=MOCK_ENTRY_DATA, title="Jan Kowalski")
    entry.add_to_hass(hass)
    entry.runtime_data = _make_runtime_data(
        clinics=filters_data["clinics"], doctors=filters_data["doctors"]
    )

    result = await _init_subentry_flow(hass, entry)
    await _to_details(hass, result["flow_id"])
    result3 = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_CLINIC_ID: "100", CONF_DOCTOR_ID: "1"}
    )

    assert result3["type"] == FlowResultType.CREATE_ENTRY
    assert result3["data"][CONF_CLINIC_ID] == 100
    assert result3["data"][CONF_DOCTOR_ID] == 1


@pytest.mark.asyncio
async def test_subentry_flow_details_has_notify_field(hass: HomeAssistant):
    """The details step exposes the optional notify-target selector."""
    entry = MockConfigEntry(domain=DOMAIN, data=MOCK_ENTRY_DATA, title="Jan Kowalski")
    entry.add_to_hass(hass)
    entry.runtime_data = _make_runtime_data()

    result = await _init_subentry_flow(hass, entry)
    details = await _to_details(hass, result["flow_id"])

    assert details["step_id"] == "details"
    assert CONF_NOTIFY_TARGET in details["data_schema"].schema


@pytest.mark.asyncio
async def test_subentry_flow_stores_notify_target(hass: HomeAssistant):
    """A chosen notify entity is persisted in the subentry data."""
    entry = MockConfigEntry(domain=DOMAIN, data=MOCK_ENTRY_DATA, title="Jan Kowalski")
    entry.add_to_hass(hass)
    entry.runtime_data = _make_runtime_data()

    result = await _init_subentry_flow(hass, entry)
    await _to_details(hass, result["flow_id"])
    result3 = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {CONF_NOTIFY_TARGET: ["notify.mobile_app_phone", "notify.telegram"]},
    )

    assert result3["type"] == FlowResultType.CREATE_ENTRY
    assert result3["data"][CONF_NOTIFY_TARGET] == [
        "notify.mobile_app_phone",
        "notify.telegram",
    ]


@pytest.mark.asyncio
async def test_subentry_flow_with_date_from(hass: HomeAssistant):
    """date_from is stored in subentry data when provided."""
    entry = MockConfigEntry(domain=DOMAIN, data=MOCK_ENTRY_DATA, title="Jan Kowalski")
    entry.add_to_hass(hass)
    entry.runtime_data = _make_runtime_data()

    result = await _init_subentry_flow(hass, entry)
    await _to_details(hass, result["flow_id"])
    result3 = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_DATE_FROM: "2026-07-01"}
    )

    assert result3["type"] == FlowResultType.CREATE_ENTRY
    assert result3["data"][CONF_DATE_FROM] == "2026-07-01"
    assert "lip" in result3["title"]


@pytest.mark.asyncio
async def test_subentry_flow_prefetches_clinics_doctors(hass: HomeAssistant):
    """After the specialty step the flow pre-fetches clinics/doctors for region+specialty."""
    entry = MockConfigEntry(domain=DOMAIN, data=MOCK_ENTRY_DATA, title="Jan Kowalski")
    entry.add_to_hass(hass)
    rd = _make_runtime_data()
    entry.runtime_data = rd

    result = await _init_subentry_flow(hass, entry)
    await _to_details(hass, result["flow_id"])

    rd.filters_store.async_refresh_clinics_doctors.assert_called_once_with(
        rd.client, 204, [9]
    )


# ---------------------------------------------------------------------------
# Reconfigure existing subentry (also region-first)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_subentry_reconfigure_updates_and_aborts(hass: HomeAssistant):
    """Reconfigure goes region → specialty → details, then updates and aborts."""
    from homeassistant.config_entries import ConfigSubentry
    from homeassistant.util.read_only_dict import ReadOnlyDict

    entry = MockConfigEntry(domain=DOMAIN, data=MOCK_ENTRY_DATA, title="Jan Kowalski")
    entry.add_to_hass(hass)
    entry.runtime_data = _make_runtime_data()

    subentry = ConfigSubentry(
        data=ReadOnlyDict(MOCK_SUBENTRY_DATA),
        subentry_type=SUBENTRY_TYPE_SEARCH,
        title="Kardiolog · Warszawa",
        unique_id=None,
    )
    hass.config_entries.async_add_subentry(entry, subentry)

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_SEARCH),
        context={"source": "reconfigure", "subentry_id": subentry.subentry_id},
    )

    # Reconfigure reuses the region step.
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"

    details = await _to_details(hass, result["flow_id"], region="205", specialty="14")
    assert details["step_id"] == "details"

    result4 = await hass.config_entries.subentries.async_configure(result["flow_id"], {})

    assert result4["type"] == FlowResultType.ABORT
    assert result4["reason"] == "reconfigure_successful"

    updated = entry.subentries[subentry.subentry_id]
    assert updated.data[CONF_REGION_ID] == 205
    assert updated.data[CONF_SPECIALTY_ID] == 14


# ---------------------------------------------------------------------------
# _find_name — unit tests for type-safe ID matching
# ---------------------------------------------------------------------------


def test_find_name_integer_ids():
    """Matches when API returns integer IDs (most common case)."""
    items = [{"id": 204, "value": "Warszawa"}, {"id": 205, "value": "Kraków"}]
    assert _find_name(items, 204) == "Warszawa"


def test_find_name_string_ids():
    """Matches when API returns string IDs — regression for '16234 · 200' bug."""
    items = [{"id": "204", "value": "Warszawa"}, {"id": "205", "value": "Kraków"}]
    assert _find_name(items, 204) == "Warszawa"


def test_find_name_missing_returns_str_id():
    """Falls back to str(id) when item is not in the list."""
    items = [{"id": 999, "value": "Inne"}]
    assert _find_name(items, 204) == "204"


def test_find_name_empty_list_returns_str_id():
    assert _find_name([], 204) == "204"


# ---------------------------------------------------------------------------
# Subentry title with string IDs from API — end-to-end regression
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_subentry_title_with_string_ids_from_api(hass: HomeAssistant):
    """Title must show human-readable names even when API returns string IDs."""
    entry = MockConfigEntry(domain=DOMAIN, data=MOCK_ENTRY_DATA, title="Jan Kowalski")
    entry.add_to_hass(hass)
    entry.runtime_data = _make_runtime_data(
        regions=[{"id": "204", "value": "Warszawa"}],
        specialties=[{"id": "9", "value": "Kardiolog"}],
    )

    result = await _init_subentry_flow(hass, entry)
    await _to_details(hass, result["flow_id"])
    result3 = await hass.config_entries.subentries.async_configure(result["flow_id"], {})

    assert result3["type"] == FlowResultType.CREATE_ENTRY
    assert "Kardiolog" in result3["title"], f"Got: {result3['title']}"
    assert "Warszawa" in result3["title"], f"Got: {result3['title']}"
