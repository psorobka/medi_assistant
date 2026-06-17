"""Shared fixtures for Medicover HA integration tests."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable custom integrations in all tests."""
    return

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text())


# ---------------------------------------------------------------------------
# Re-usable data fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def personal_data() -> dict[str, Any]:
    return load_fixture("personal_data.json")


@pytest.fixture
def filters_data() -> dict[str, Any]:
    return load_fixture("filters.json")


@pytest.fixture
def slots_data() -> dict[str, Any]:
    return load_fixture("slots.json")


@pytest.fixture
def oidc_config() -> dict[str, Any]:
    return load_fixture("oidc_config.json")


# ---------------------------------------------------------------------------
# Mock MedicoverClient (used to bypass real HTTP)
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_client(personal_data, filters_data, slots_data):
    client = MagicMock()
    client.async_get_personal_data = AsyncMock(return_value=personal_data)
    client.async_find_filters = AsyncMock(return_value=filters_data)
    client.async_find_appointments = AsyncMock(return_value=slots_data["items"])
    client._auth = MagicMock()
    client._auth.is_token_valid = MagicMock(return_value=True)
    client._auth.async_refresh_token = AsyncMock()
    client._auth.token_data = MagicMock(return_value={
        "access_token": "fake-access-token",
        "refresh_token": "fake-refresh-token",
        "expires_at": 9999999999,
        "device_id": "fake-device-id",
        "device_ua": "FakeUA/1.0",
    })
    return client


# ---------------------------------------------------------------------------
# Config entry helpers
# ---------------------------------------------------------------------------

MOCK_ENTRY_DATA = {
    "access_token": "fake-access-token",
    "refresh_token": "fake-refresh-token",
    "expires_at": 9999999999,
    "device_id": "fake-device-id",
    "device_ua": "FakeUA/1.0",
    "username": "jan.kowalski@example.com",
}

MOCK_SUBENTRY_DATA = {
    "region_id": 204,
    "region_name": "Warszawa",
    "specialty_id": 9,
    "specialty_name": "Kardiolog",
    "slot_search_type": 0,
}
