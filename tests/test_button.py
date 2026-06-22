"""Tests for MedicoverRefreshButton."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant

from custom_components.medi_assistant.button import (
    MedicoverReauthButton,
    MedicoverRefreshButton,
)
from custom_components.medi_assistant.exceptions import AuthError, InvalidGrant, MfaRequired


@pytest.mark.asyncio
async def test_button_press_refreshes_filters(hass: HomeAssistant, personal_data, filters_data):
    mock_store = MagicMock()
    mock_store.async_refresh = AsyncMock()
    mock_store.personal = personal_data  # firstName=Jan, lastName=Kowalski

    mock_client = MagicMock()
    mock_runtime = MagicMock()
    mock_runtime.filters_store = mock_store
    mock_runtime.client = mock_client

    mock_entry = MagicMock()
    mock_entry.runtime_data = mock_runtime
    # Title already matches personal_data → no update_entry call
    mock_entry.title = "Jan Kowalski"
    mock_entry.entry_id = "entry-abc"

    button = MedicoverRefreshButton(hass, mock_entry)
    await button.async_press()

    mock_store.async_refresh.assert_called_once_with(mock_client)


@pytest.mark.asyncio
async def test_button_press_updates_entry_title(hass: HomeAssistant, personal_data):
    """After refresh, entry title should be updated to the full patient name."""
    mock_store = MagicMock()
    mock_store.async_refresh = AsyncMock()
    mock_store.personal = personal_data  # firstName=Jan, lastName=Kowalski

    mock_entry = MagicMock()
    mock_entry.runtime_data = MagicMock(filters_store=mock_store, client=MagicMock())
    mock_entry.title = "Stara Nazwa"
    mock_entry.entry_id = "entry-abc"

    updated_titles: list[str] = []

    with patch.object(
        hass.config_entries,
        "async_update_entry",
        side_effect=lambda entry, **kwargs: updated_titles.append(kwargs.get("title", "")),
    ):
        button = MedicoverRefreshButton(hass, mock_entry)
        await button.async_press()

    assert updated_titles == ["Jan Kowalski"]


# ---------------------------------------------------------------------------
# MedicoverReauthButton — force re-login, fall back to interactive reauth
# ---------------------------------------------------------------------------


def _make_reauth_entry() -> MagicMock:
    mock_entry = MagicMock()
    mock_entry.runtime_data = MagicMock(
        auth=MagicMock(async_refresh_or_relogin=AsyncMock()),
        coordinator=MagicMock(async_request_refresh=AsyncMock()),
    )
    mock_entry.title = "Jan Kowalski"
    mock_entry.entry_id = "entry-abc"
    return mock_entry


@pytest.mark.asyncio
async def test_reauth_button_silent_relogin_then_refresh(hass: HomeAssistant):
    """Success path: force a silent re-login, then refresh sensors; no reauth dialog."""
    mock_entry = _make_reauth_entry()

    button = MedicoverReauthButton(hass, mock_entry)
    await button.async_press()

    mock_entry.runtime_data.auth.async_refresh_or_relogin.assert_awaited_once_with(force=True)
    mock_entry.runtime_data.coordinator.async_request_refresh.assert_awaited_once()
    mock_entry.async_start_reauth.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "err",
    [MfaRequired("id", "csrf", "/r"), AuthError("bad password"), InvalidGrant("no creds")],
)
async def test_reauth_button_falls_back_to_interactive_reauth(hass: HomeAssistant, err):
    """If the silent re-login can't recover, start the interactive reauth flow."""
    mock_entry = _make_reauth_entry()
    mock_entry.runtime_data.auth.async_refresh_or_relogin = AsyncMock(side_effect=err)

    button = MedicoverReauthButton(hass, mock_entry)
    await button.async_press()

    mock_entry.async_start_reauth.assert_called_once_with(hass)
    mock_entry.runtime_data.coordinator.async_request_refresh.assert_not_called()
