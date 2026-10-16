"""Synthetic search action tests, including parity with native subentry flows."""

import asyncio
from unittest.mock import AsyncMock

import pytest
from homeassistant.config_entries import ConfigEntryState, ConfigSubentry
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.medi_assistant.search_services import async_setup_search_services
from custom_components.medi_assistant.sensor import async_setup_entry

from .test_subentry_flow import _init_subentry_flow, _make_runtime_data, _to_details


@pytest.fixture
def account(hass):
    entry = MockConfigEntry(domain="medi_assistant", title="Konto testowe")
    entry.add_to_hass(hass)
    entry.runtime_data = _make_runtime_data(
        clinics=[{"id": 1, "value": "Placówka testowa"}],
        doctors=[{"id": 2, "value": "Lekarz testowy"}],
    )
    entry.mock_state(hass, ConfigEntryState.LOADED)
    async_setup_search_services(hass)
    async_setup_search_services(hass)
    return entry


async def call(hass, action, data):
    return await hass.services.async_call(
        "medi_assistant", action, data, blocking=True, return_response=True
    )


def params(account, **extra):
    return {"account_id": account.entry_id, "region_id": 204, "specialty_id": 9, **extra}


async def test_filters_are_account_and_region_scoped(hass, account):
    assert (await call(hass, "get_search_filters", {}))["accounts"] == [
        {"account_id": account.entry_id, "account_name": account.title}
    ]
    result = await call(hass, "get_search_filters", {"account_id": account.entry_id})
    assert result["regions"] and result["specialties"] == []
    result = await call(hass, "get_search_filters", params(account))
    assert result["specialties"] and result["clinics"] and result["doctors"]
    assert result["languages"][0]["id"] == 4
    account.runtime_data.filters_store.async_refresh_specialties.assert_awaited_once_with(
        account.runtime_data.client, 204
    )


async def test_create_matches_flow_and_can_be_reconfigured(hass, account):
    details = {
        "clinic_id": "1",
        "doctor_id": "2",
        "language_id": "4",
        "date_from": "2026-11-01",
        "date_to": "2026-11-30",
        "notify_target": ["notify.test"],
    }
    flow = await _init_subentry_flow(hass, account)
    await _to_details(hass, flow["flow_id"])
    await hass.config_entries.subentries.async_configure(flow["flow_id"], details)
    native = next(iter(account.subentries.values()))
    expected = dict(native.data)
    hass.config_entries.async_remove_subentry(account, native.subentry_id)
    # Exercise the actual sensor platform update listener.
    added = []
    await async_setup_entry(hass, account, lambda sensors, **kwargs: added.extend(sensors))
    result = await call(hass, "create_search", params(account, **{**details, "language_id": 4}))
    await hass.async_block_till_done()
    search = account.subentries[result["search_id"]]
    assert search.data == expected
    assert search.title == native.title
    assert result["created"] and len(added) == 1
    assert added[0].unique_id.endswith(search.subentry_id)
    flow = await hass.config_entries.subentries.async_init(
        (account.entry_id, "search"),
        context={"source": "reconfigure", "subentry_id": search.subentry_id},
    )
    await _to_details(hass, flow["flow_id"])
    result = await hass.config_entries.subentries.async_configure(
        flow["flow_id"], {"date_to": "2026-12-10"}
    )
    assert result["reason"] == "reconfigure_successful"
    assert account.subentries[search.subentry_id].data["date_to"] == "2026-12-10"


async def test_concurrent_retries_do_not_duplicate_and_accounts_are_isolated(hass, account):
    other = MockConfigEntry(domain="medi_assistant", title="Drugie konto")
    other.add_to_hass(hass)
    other.runtime_data = _make_runtime_data()
    other.mock_state(hass, ConfigEntryState.LOADED)
    results = await asyncio.gather(
        *[call(hass, "create_search", params(account)) for _ in range(3)]
    )
    assert sum(result["created"] for result in results) == 1
    assert len({result["search_id"] for result in results}) == 1
    assert not other.subentries
    assert (await call(hass, "create_search", params(other)))["created"]


@pytest.mark.parametrize(
    "extra",
    [
        {"region_id": 999},
        {"specialty_id": 999},
        {"clinic_id": 999},
        {"doctor_id": 999},
        {"date_from": "bad"},
        {"date_from": "2026-11-30", "date_to": "2026-11-01"},
        {"account_id": "missing"},
    ],
)
async def test_invalid_creation_never_persists(hass, account, extra):
    with pytest.raises(ServiceValidationError):
        await call(hass, "create_search", params(account, **extra))
    assert not account.subentries


@pytest.mark.parametrize("data", [{"region_id": 204}, {"specialty_id": 9}])
async def test_filters_need_account(hass, account, data):
    with pytest.raises(ServiceValidationError):
        await call(hass, "get_search_filters", data)


async def test_unloaded_account_and_bad_filter_dependencies(hass, account):
    with pytest.raises(ServiceValidationError):
        await call(hass, "get_search_filters", {"account_id": account.entry_id, "specialty_id": 9})
    account.mock_state(hass, ConfigEntryState.NOT_LOADED)
    with pytest.raises(ServiceValidationError):
        await call(hass, "create_search", params(account))


async def test_filter_failure_does_not_create(hass, account):
    store = account.runtime_data.filters_store
    store.regions = []
    store.async_refresh = AsyncMock(side_effect=RuntimeError("synthetic failure"))
    with pytest.raises(HomeAssistantError, match="pobrać filtrów"):
        await call(hass, "create_search", params(account))
    assert not account.subentries


async def test_retired_search_does_not_block_creation(hass, account):
    hass.config_entries.async_add_subentry(
        account,
        ConfigSubentry(
            data={"region_id": 999, "specialty_id": 9},
            subentry_type="search",
            title="Stara",
            unique_id=None,
        ),
    )
    assert (await call(hass, "create_search", params(account)))["created"]


async def test_invalid_dates_return_form_error(hass, account):
    flow = await _init_subentry_flow(hass, account)
    await _to_details(hass, flow["flow_id"])
    result = await hass.config_entries.subentries.async_configure(
        flow["flow_id"], {"date_from": "2026-11-30", "date_to": "2026-11-01"}
    )
    assert result["errors"] == {"base": "invalid_search"}
    assert not account.subentries
