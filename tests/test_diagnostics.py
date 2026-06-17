"""Tests for config-entry diagnostics redaction."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.medi_assistant.const import DOMAIN
from custom_components.medi_assistant.diagnostics import (
    async_get_config_entry_diagnostics,
)

from .conftest import MOCK_ENTRY_DATA

_REDACTED = "**REDACTED**"


@pytest.mark.asyncio
async def test_diagnostics_redacts_secrets(hass: HomeAssistant):
    """access_token, refresh_token, password and auth_cookies must be redacted.

    Regression: auth_cookies (idsrv session etc.) are bearer-equivalent and were
    leaking unredacted in diagnostics downloads.
    """
    data = {
        **MOCK_ENTRY_DATA,
        "password": "hunter2",
        "auth_cookies": {"idsrv": "secret", "idsrv.session": "sess"},
    }
    entry = MockConfigEntry(domain=DOMAIN, data=data, title="Jan Kowalski")
    entry.add_to_hass(hass)

    diag = await async_get_config_entry_diagnostics(hass, entry)
    entry_data = diag["entry_data"]

    assert entry_data["access_token"] == _REDACTED
    assert entry_data["refresh_token"] == _REDACTED
    assert entry_data["password"] == _REDACTED
    assert entry_data["auth_cookies"] == _REDACTED
    # Non-secret field stays visible.
    assert entry_data["username"] == MOCK_ENTRY_DATA["username"]
