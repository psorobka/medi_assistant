"""Tests for MedicoverRefreshButton."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant

from custom_components.medi_assistant.button import MedicoverRefreshButton


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
