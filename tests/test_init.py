"""Tests for async_setup_entry / async_unload_entry."""

from __future__ import annotations

import contextlib
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.medi_assistant import _async_update_listener
from custom_components.medi_assistant.exceptions import MfaRequired
from custom_components.medi_assistant.const import (
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)

from .conftest import MOCK_ENTRY_DATA


def _make_entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="12345",
        data=MOCK_ENTRY_DATA,
        title="Jan Kowalski",
    )
    entry.add_to_hass(hass)
    return entry


def _setup_patches(
    first_refresh_side_effect=None,
    token_valid=True,
    relogin_side_effect=None,
):
    """Return (mock_auth_session, patches_list).

    The auth session is identified by the presence of a cookie_jar kwarg —
    that's how our code creates it.  HA's shared session is created without
    one, so the two mocks stay separate and HA can't overwrite our AsyncMock.
    """
    mock_auth_session = MagicMock()
    mock_auth_session.closed = False
    mock_auth_session.close = AsyncMock()

    def _session_factory(*args, **kwargs):
        # Our auth_session is the only one created with cookie_jar=
        if "cookie_jar" in kwargs:
            return mock_auth_session
        s = MagicMock()
        s.close = AsyncMock()
        return s

    mock_auth = MagicMock()
    mock_auth.is_token_valid = MagicMock(return_value=token_valid)
    mock_auth.async_refresh_token = AsyncMock()
    mock_auth.async_refresh_or_relogin = AsyncMock(side_effect=relogin_side_effect)
    mock_auth._expires_at = 9999999999

    mock_client = MagicMock()
    mock_client.async_get_personal_data = AsyncMock(
        return_value={"firstName": "Jan", "lastName": "Kowalski"}
    )

    mock_store = MagicMock()
    mock_store.async_ensure = AsyncMock()
    mock_store.regions = []

    mock_coord = MagicMock()
    mock_coord.async_config_entry_first_refresh = AsyncMock(side_effect=first_refresh_side_effect)

    patches = [
        patch("custom_components.medi_assistant.MedicoverAuth", return_value=mock_auth),
        patch("custom_components.medi_assistant.MedicoverClient", return_value=mock_client),
        patch("custom_components.medi_assistant.FiltersStore", return_value=mock_store),
        patch("custom_components.medi_assistant.MedicoverCoordinator", return_value=mock_coord),
        patch("aiohttp.ClientSession", side_effect=_session_factory),
        patch("aiohttp.CookieJar"),
        patch("custom_components.medi_assistant.TokenKeepAlive"),
    ]
    return mock_auth_session, patches


@pytest.mark.asyncio
async def test_setup_entry_sets_patient_name(hass: HomeAssistant, mock_client, personal_data):
    """async_setup_entry should update title to patient full name."""
    entry = _make_entry(hass)

    with (
        patch("custom_components.medi_assistant.MedicoverAuth") as MockAuth,
        patch("custom_components.medi_assistant.MedicoverClient", return_value=mock_client),
        patch("custom_components.medi_assistant.FiltersStore") as MockStore,
        patch("custom_components.medi_assistant.TokenKeepAlive"),
        patch("aiohttp.ClientSession"),
        patch("aiohttp.CookieJar"),
    ):
        mock_auth = MagicMock()
        mock_auth.is_token_valid = MagicMock(return_value=True)
        mock_auth.async_refresh_token = AsyncMock()
        mock_auth.token_data = MagicMock(return_value=MOCK_ENTRY_DATA)
        MockAuth.return_value = mock_auth

        mock_store = MagicMock()
        mock_store.async_ensure = AsyncMock()
        mock_store.regions = [{"id": 204, "value": "Warszawa"}]
        MockStore.return_value = mock_store

        with patch("custom_components.medi_assistant.MedicoverCoordinator") as MockCoord:
            mock_coord = MagicMock()
            mock_coord.async_config_entry_first_refresh = AsyncMock()
            MockCoord.return_value = mock_coord

            assert await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done()

    assert entry.title == "Jan Kowalski"


@pytest.mark.asyncio
async def test_unload_entry(hass: HomeAssistant, mock_client):
    """async_unload_entry should close auth session and return True."""
    entry = _make_entry(hass)

    with (
        patch("custom_components.medi_assistant.MedicoverAuth") as MockAuth,
        patch("custom_components.medi_assistant.MedicoverClient", return_value=mock_client),
        patch("custom_components.medi_assistant.FiltersStore") as MockStore,
        patch("custom_components.medi_assistant.TokenKeepAlive"),
        patch("aiohttp.ClientSession"),
        patch("aiohttp.CookieJar"),
    ):
        mock_auth = MagicMock()
        mock_auth.is_token_valid = MagicMock(return_value=True)
        mock_auth.async_refresh_token = AsyncMock()
        MockAuth.return_value = mock_auth

        mock_store = MagicMock()
        mock_store.async_ensure = AsyncMock()
        mock_store.regions = []
        MockStore.return_value = mock_store

        with patch("custom_components.medi_assistant.MedicoverCoordinator") as MockCoord:
            mock_coord = MagicMock()
            mock_coord.async_config_entry_first_refresh = AsyncMock()
            MockCoord.return_value = mock_coord

            await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done()

            result = await hass.config_entries.async_unload(entry.entry_id)

    assert result is True


# ---------------------------------------------------------------------------
# Session leak regression: auth_session must be closed on setup failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_setup_entry_closes_session_on_config_entry_not_ready(hass: HomeAssistant):
    """auth_session must be closed when first refresh raises ConfigEntryNotReady.

    Before the fix, only ConfigEntryAuthFailed was caught — a network error
    during the initial poll (ConfigEntryNotReady) silently leaked the session.
    """
    entry = _make_entry(hass)
    mock_session, patches = _setup_patches(
        first_refresh_side_effect=ConfigEntryNotReady("network error")
    )

    with contextlib.ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    mock_session.close.assert_called_once()


# ---------------------------------------------------------------------------
# Update listener: token-only changes must NOT reload (would kill keep-alive)
# ---------------------------------------------------------------------------


def _listener_mocks(options: dict | None, running_interval_min: int, runtime_data=...):
    """Build (hass, entry) mocks for exercising _async_update_listener."""
    hass_mock = MagicMock()
    hass_mock.config_entries.async_reload = AsyncMock()

    entry = MagicMock()
    entry.entry_id = "entry-123"
    entry.options = options or {}
    if runtime_data is ...:
        runtime_data = MagicMock()
        runtime_data.coordinator.update_interval = timedelta(minutes=running_interval_min)
    entry.runtime_data = runtime_data
    return hass_mock, entry


@pytest.mark.asyncio
async def test_update_listener_no_reload_on_non_interval_change():
    """Token saves and subentry add/remove/edit must NOT reload.

    Token refresh would tear down the keep-alive every ~2.5 min; subentry
    changes are handled without a reload (sensors added dynamically, removed by
    HA, edits applied live). Only a scan-interval change reloads.
    """
    hass_mock, entry = _listener_mocks(options={}, running_interval_min=DEFAULT_SCAN_INTERVAL)

    await _async_update_listener(hass_mock, entry)

    hass_mock.config_entries.async_reload.assert_not_called()


@pytest.mark.asyncio
async def test_update_listener_reloads_on_interval_change():
    """Changing the scan interval (options) must reload the entry."""
    hass_mock, entry = _listener_mocks(
        options={CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL + 5},
        running_interval_min=DEFAULT_SCAN_INTERVAL,
    )

    await _async_update_listener(hass_mock, entry)

    hass_mock.config_entries.async_reload.assert_called_once_with(entry.entry_id)


@pytest.mark.asyncio
async def test_update_listener_noop_without_runtime_data():
    """Listener firing before runtime_data is set must not raise or reload."""
    hass_mock, entry = _listener_mocks(
        options={}, running_interval_min=DEFAULT_SCAN_INTERVAL, runtime_data=None
    )

    await _async_update_listener(hass_mock, entry)

    hass_mock.config_entries.async_reload.assert_not_called()


@pytest.mark.asyncio
async def test_setup_entry_closes_session_on_auth_failed(hass: HomeAssistant):
    """auth_session must be closed when first refresh raises ConfigEntryAuthFailed."""
    entry = _make_entry(hass)
    mock_session, patches = _setup_patches(
        first_refresh_side_effect=ConfigEntryAuthFailed("token expired")
    )

    with contextlib.ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    mock_session.close.assert_called_once()


# ---------------------------------------------------------------------------
# H2: setup self-heals via silent re-login instead of forcing reauth
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_setup_silent_relogin_on_expired_token(hass: HomeAssistant):
    """Stale token at startup → async_refresh_or_relogin recovers → entry loads."""
    entry = _make_entry(hass)
    _, patches = _setup_patches(token_valid=False)  # relogin succeeds (no side effect)

    with contextlib.ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        ok = await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert ok is True


@pytest.mark.asyncio
async def test_setup_mfa_required_triggers_auth_failed(hass: HomeAssistant):
    """Untrusted device (silent re-login raises MfaRequired) → reauth, not retry loop."""
    entry = _make_entry(hass)
    _, patches = _setup_patches(
        token_valid=False, relogin_side_effect=MfaRequired("id", "csrf", "/r")
    )

    with contextlib.ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        ok = await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert ok is False
    assert entry.state is ConfigEntryState.SETUP_ERROR


@pytest.mark.asyncio
async def test_setup_does_not_start_keepalive_if_platform_setup_fails(hass: HomeAssistant):
    """If forward_entry_setups raises, the keep-alive timer must not be started
    (otherwise a leaked async_call_later keeps refreshing a half-dead entry)."""
    import custom_components.medi_assistant as integration

    entry = _make_entry(hass)
    _, patches = _setup_patches()

    with contextlib.ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        stack.enter_context(
            patch.object(
                hass.config_entries,
                "async_forward_entry_setups",
                AsyncMock(side_effect=RuntimeError("platform boom")),
            )
        )
        ka_start = integration.TokenKeepAlive.return_value.start
        ok = await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert ok is False
    assert ka_start.call_count == 0
