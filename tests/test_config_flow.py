"""Tests for the Medicover config flow (login, MFA, reauth, duplicate)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.medi_assistant.config_flow import (
    MedicoverConfigFlow,
    _subentry_title,
)
from custom_components.medi_assistant.const import (
    CONF_DATE_FROM,
    CONF_SCAN_INTERVAL,
    CONF_SPECIALTY_NAME,
    DOMAIN,
    SUBENTRY_TYPE_SEARCH,
)
from custom_components.medi_assistant.exceptions import AuthError, MfaRequired

from .conftest import MOCK_ENTRY_DATA


def _mock_auth_session():
    """Return a mock aiohttp.ClientSession with async close."""
    session = MagicMock()
    session.closed = False
    session.close = AsyncMock()
    return session


def _build_mock_auth(side_effect=None):
    mock_auth = MagicMock()
    mock_auth.async_login = AsyncMock(side_effect=side_effect)
    mock_auth.async_submit_mfa = AsyncMock()
    mock_auth.token_data = MagicMock(return_value=MOCK_ENTRY_DATA)
    mock_auth.is_token_valid = MagicMock(return_value=True)
    return mock_auth


# ---------------------------------------------------------------------------
# Happy path: login → entry titled "Jan Kowalski"
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_config_flow_happy_path(hass: HomeAssistant, personal_data):
    """Full login without MFA creates an entry titled with patient's name."""
    mock_auth = _build_mock_auth()

    with (
        patch(
            "custom_components.medi_assistant.config_flow.MedicoverAuth",
            return_value=mock_auth,
        ),
        patch("custom_components.medi_assistant.config_flow.MedicoverClient") as MockClient,
        patch(
            "custom_components.medi_assistant.config_flow.aiohttp.ClientSession",
            return_value=_mock_auth_session(),
        ),
    ):
        mock_client_instance = MagicMock()
        mock_client_instance.async_get_personal_data = AsyncMock(return_value=personal_data)
        MockClient.return_value = mock_client_instance

        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "user"

        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"username": "jan@example.com", "password": "secret"},
        )

    assert result2["type"] == FlowResultType.CREATE_ENTRY
    assert result2["title"] == "Jan Kowalski"


# ---------------------------------------------------------------------------
# MFA path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_config_flow_mfa_path(hass: HomeAssistant, personal_data):
    """Login raises MfaRequired → MFA step → entry created."""
    mock_auth = _build_mock_auth(side_effect=MfaRequired("code-id", "csrf-token", "/return"))
    mock_auth.async_submit_mfa = AsyncMock()

    with (
        patch(
            "custom_components.medi_assistant.config_flow.MedicoverAuth",
            return_value=mock_auth,
        ),
        patch("custom_components.medi_assistant.config_flow.MedicoverClient") as MockClient,
        patch(
            "custom_components.medi_assistant.config_flow.aiohttp.ClientSession",
            return_value=_mock_auth_session(),
        ),
    ):
        mock_client_instance = MagicMock()
        mock_client_instance.async_get_personal_data = AsyncMock(return_value=personal_data)
        MockClient.return_value = mock_client_instance

        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"username": "jan@example.com", "password": "secret"},
        )

        assert result2["type"] == FlowResultType.FORM
        assert result2["step_id"] == "mfa"

        result3 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"mfa_code": "123456"},
        )

    assert result3["type"] == FlowResultType.CREATE_ENTRY


# ---------------------------------------------------------------------------
# Wrong password → error
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_config_flow_invalid_auth(hass: HomeAssistant):
    mock_auth = _build_mock_auth(side_effect=AuthError("bad credentials"))

    with (
        patch(
            "custom_components.medi_assistant.config_flow.MedicoverAuth",
            return_value=mock_auth,
        ),
        patch(
            "custom_components.medi_assistant.config_flow.aiohttp.ClientSession",
            return_value=_mock_auth_session(),
        ),
    ):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"username": "bad@example.com", "password": "wrong"},
        )

    assert result2["type"] == FlowResultType.FORM
    assert result2["errors"]["base"] == "invalid_auth"


# ---------------------------------------------------------------------------
# Wrong MFA code → error
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_config_flow_invalid_mfa(hass: HomeAssistant, personal_data):
    mock_auth = _build_mock_auth(side_effect=MfaRequired("code-id", "csrf", "/return"))
    mock_auth.async_submit_mfa = AsyncMock(side_effect=AuthError("bad mfa"))

    with (
        patch(
            "custom_components.medi_assistant.config_flow.MedicoverAuth",
            return_value=mock_auth,
        ),
        patch("custom_components.medi_assistant.config_flow.MedicoverClient") as MockClient,
        patch(
            "custom_components.medi_assistant.config_flow.aiohttp.ClientSession",
            return_value=_mock_auth_session(),
        ),
    ):
        mock_client_instance = MagicMock()
        mock_client_instance.async_get_personal_data = AsyncMock(return_value=personal_data)
        MockClient.return_value = mock_client_instance

        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"username": "jan@example.com", "password": "secret"},
        )
        result_mfa = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"mfa_code": "000000"},
        )

    assert result_mfa["type"] == FlowResultType.FORM
    assert result_mfa["errors"]["base"] == "invalid_mfa"


# ---------------------------------------------------------------------------
# Duplicate account → abort
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_config_flow_duplicate_aborts(hass: HomeAssistant, personal_data):
    existing = MockConfigEntry(
        domain=DOMAIN,
        unique_id="12345",
        data=MOCK_ENTRY_DATA,
        title="Jan Kowalski",
    )
    existing.add_to_hass(hass)

    mock_auth = _build_mock_auth()

    with (
        patch(
            "custom_components.medi_assistant.config_flow.MedicoverAuth",
            return_value=mock_auth,
        ),
        patch("custom_components.medi_assistant.config_flow.MedicoverClient") as MockClient,
        patch(
            "custom_components.medi_assistant.config_flow.aiohttp.ClientSession",
            return_value=_mock_auth_session(),
        ),
    ):
        mock_client_instance = MagicMock()
        mock_client_instance.async_get_personal_data = AsyncMock(return_value=personal_data)
        MockClient.return_value = mock_client_instance

        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"username": "jan@example.com", "password": "secret"},
        )

    assert result2["type"] == FlowResultType.ABORT
    assert result2["reason"] == "already_configured"


# ---------------------------------------------------------------------------
# supported_subentry_types — regression for missing config_entry param
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Unexpected (non-auth) errors → cannot_connect
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_config_flow_unexpected_error_cannot_connect(hass: HomeAssistant):
    """A non-AuthError exception during login surfaces as cannot_connect."""
    mock_auth = _build_mock_auth(side_effect=RuntimeError("network down"))

    with (
        patch(
            "custom_components.medi_assistant.config_flow.MedicoverAuth",
            return_value=mock_auth,
        ),
        patch(
            "custom_components.medi_assistant.config_flow.aiohttp.ClientSession",
            return_value=_mock_auth_session(),
        ),
    ):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"username": "jan@example.com", "password": "secret"},
        )

    assert result2["type"] == FlowResultType.FORM
    assert result2["errors"]["base"] == "cannot_connect"


@pytest.mark.asyncio
async def test_config_flow_mfa_unexpected_error_cannot_connect(hass: HomeAssistant, personal_data):
    """A non-AuthError exception during MFA submit surfaces as cannot_connect."""
    mock_auth = _build_mock_auth(side_effect=MfaRequired("id", "csrf", "/r"))
    mock_auth.async_submit_mfa = AsyncMock(side_effect=RuntimeError("boom"))

    with (
        patch(
            "custom_components.medi_assistant.config_flow.MedicoverAuth",
            return_value=mock_auth,
        ),
        patch("custom_components.medi_assistant.config_flow.MedicoverClient") as MockClient,
        patch(
            "custom_components.medi_assistant.config_flow.aiohttp.ClientSession",
            return_value=_mock_auth_session(),
        ),
    ):
        MockClient.return_value.async_get_personal_data = AsyncMock(return_value=personal_data)
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        await hass.config_entries.flow.async_configure(
            result["flow_id"], {"username": "jan@example.com", "password": "secret"}
        )
        result_mfa = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"mfa_code": "000000"}
        )

    assert result_mfa["type"] == FlowResultType.FORM
    assert result_mfa["errors"]["base"] == "cannot_connect"


@pytest.mark.asyncio
async def test_finish_login_falls_back_to_username_when_personal_fails(hass: HomeAssistant):
    """If personal-data fetch fails, the entry is still created, titled by username."""
    mock_auth = _build_mock_auth()

    with (
        patch(
            "custom_components.medi_assistant.config_flow.MedicoverAuth",
            return_value=mock_auth,
        ),
        patch("custom_components.medi_assistant.config_flow.MedicoverClient") as MockClient,
        patch(
            "custom_components.medi_assistant.config_flow.aiohttp.ClientSession",
            return_value=_mock_auth_session(),
        ),
    ):
        MockClient.return_value.async_get_personal_data = AsyncMock(
            side_effect=RuntimeError("personal-data 500")
        )
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"username": "jan@example.com", "password": "secret"}
        )

    assert result2["type"] == FlowResultType.CREATE_ENTRY
    assert result2["title"] == "jan@example.com"


# ---------------------------------------------------------------------------
# Reauth flow
# ---------------------------------------------------------------------------


def _reauth_entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id="12345", data=MOCK_ENTRY_DATA, title="Jan Kowalski"
    )
    entry.add_to_hass(hass)
    return entry


@pytest.mark.asyncio
async def test_reauth_confirm_success(hass: HomeAssistant):
    """Reauth with correct credentials updates the entry and aborts successfully."""
    entry = _reauth_entry(hass)
    mock_auth = _build_mock_auth()

    with (
        patch(
            "custom_components.medi_assistant.config_flow.MedicoverAuth",
            return_value=mock_auth,
        ),
        patch(
            "custom_components.medi_assistant.config_flow.aiohttp.ClientSession",
            return_value=_mock_auth_session(),
        ),
        patch.object(hass.config_entries, "async_reload", AsyncMock()) as mock_reload,
    ):
        result = await entry.start_reauth_flow(hass)
        assert result["step_id"] == "reauth_confirm"
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"username": "jan@example.com", "password": "newpass"}
        )

    assert result2["type"] == FlowResultType.ABORT
    assert result2["reason"] == "reauth_successful"
    assert entry.data["password"] == "newpass"
    mock_reload.assert_awaited_once()


@pytest.mark.asyncio
async def test_reauth_confirm_invalid_auth(hass: HomeAssistant):
    """Wrong credentials during reauth re-show the form with invalid_auth."""
    entry = _reauth_entry(hass)
    mock_auth = _build_mock_auth(side_effect=AuthError("bad"))

    with (
        patch(
            "custom_components.medi_assistant.config_flow.MedicoverAuth",
            return_value=mock_auth,
        ),
        patch(
            "custom_components.medi_assistant.config_flow.aiohttp.ClientSession",
            return_value=_mock_auth_session(),
        ),
    ):
        result = await entry.start_reauth_flow(hass)
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"username": "jan@example.com", "password": "wrong"}
        )

    assert result2["type"] == FlowResultType.FORM
    assert result2["errors"]["base"] == "invalid_auth"


@pytest.mark.asyncio
async def test_reauth_mfa_success(hass: HomeAssistant):
    """Reauth that hits MFA → reauth_mfa step → success."""
    entry = _reauth_entry(hass)
    mock_auth = _build_mock_auth(side_effect=MfaRequired("id", "csrf", "/r"))
    mock_auth.async_submit_mfa = AsyncMock()

    with (
        patch(
            "custom_components.medi_assistant.config_flow.MedicoverAuth",
            return_value=mock_auth,
        ),
        patch(
            "custom_components.medi_assistant.config_flow.aiohttp.ClientSession",
            return_value=_mock_auth_session(),
        ),
        patch.object(hass.config_entries, "async_reload", AsyncMock()),
    ):
        result = await entry.start_reauth_flow(hass)
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"username": "jan@example.com", "password": "newpass"}
        )
        assert result2["step_id"] == "reauth_mfa"
        result3 = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"mfa_code": "123456"}
        )

    assert result3["type"] == FlowResultType.ABORT
    assert result3["reason"] == "reauth_successful"


@pytest.mark.asyncio
async def test_reauth_mfa_invalid_code(hass: HomeAssistant):
    """Wrong MFA code during reauth re-shows the reauth_mfa form with invalid_mfa."""
    entry = _reauth_entry(hass)
    mock_auth = _build_mock_auth(side_effect=MfaRequired("id", "csrf", "/r"))
    mock_auth.async_submit_mfa = AsyncMock(side_effect=AuthError("bad mfa"))

    with (
        patch(
            "custom_components.medi_assistant.config_flow.MedicoverAuth",
            return_value=mock_auth,
        ),
        patch(
            "custom_components.medi_assistant.config_flow.aiohttp.ClientSession",
            return_value=_mock_auth_session(),
        ),
    ):
        result = await entry.start_reauth_flow(hass)
        await hass.config_entries.flow.async_configure(
            result["flow_id"], {"username": "jan@example.com", "password": "newpass"}
        )
        result3 = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"mfa_code": "000000"}
        )

    assert result3["type"] == FlowResultType.FORM
    assert result3["errors"]["base"] == "invalid_mfa"


# ---------------------------------------------------------------------------
# Options flow (scan interval)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_options_flow_sets_scan_interval(hass: HomeAssistant):
    entry = MockConfigEntry(domain=DOMAIN, unique_id="12345", data=MOCK_ENTRY_DATA)
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "init"

    result2 = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SCAN_INTERVAL: 15}
    )
    assert result2["type"] == FlowResultType.CREATE_ENTRY
    assert result2["data"][CONF_SCAN_INTERVAL] == 15


# ---------------------------------------------------------------------------
# _subentry_title — bad date is ignored, not fatal
# ---------------------------------------------------------------------------


def test_subentry_title_ignores_invalid_date():
    title = _subentry_title({CONF_SPECIALTY_NAME: "Kardiolog", CONF_DATE_FROM: "not-a-date"})
    assert title == "Kardiolog"


def test_subentry_title_empty_is_default():
    assert _subentry_title({}) == "Szukajka"


def test_supported_subentry_types_classmethod_accepts_config_entry():
    """async_get_supported_subentry_types must accept a config_entry argument.

    HA calls handler.async_get_supported_subentry_types(config_entry) when
    serialising a newly-created entry.  Missing the parameter causes a TypeError
    that crashes the HTTP response after a successful MFA login.
    """
    mock_entry = MagicMock()
    result = MedicoverConfigFlow.async_get_supported_subentry_types(mock_entry)
    assert SUBENTRY_TYPE_SEARCH in result


@pytest.mark.asyncio
async def test_config_flow_entry_has_search_subentry_type(hass: HomeAssistant, personal_data):
    """After login the config entry must expose the 'search' subentry type.

    entry.supported_subentry_types calls async_get_supported_subentry_types(entry)
    internally — this exercises the exact HA code path that crashed on MFA completion.
    """
    mock_auth = _build_mock_auth(side_effect=MfaRequired("code-id", "csrf-token", "/return"))
    mock_auth.async_submit_mfa = AsyncMock()

    with (
        patch(
            "custom_components.medi_assistant.config_flow.MedicoverAuth",
            return_value=mock_auth,
        ),
        patch("custom_components.medi_assistant.config_flow.MedicoverClient") as MockClient,
        patch(
            "custom_components.medi_assistant.config_flow.aiohttp.ClientSession",
            return_value=_mock_auth_session(),
        ),
    ):
        mock_client_instance = MagicMock()
        mock_client_instance.async_get_personal_data = AsyncMock(return_value=personal_data)
        MockClient.return_value = mock_client_instance

        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"username": "jan@example.com", "password": "secret"},
        )
        result3 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"mfa_code": "123456"},
        )

    assert result3["type"] == FlowResultType.CREATE_ENTRY

    entries = hass.config_entries.async_entries(DOMAIN)
    assert len(entries) == 1
    subentry_types = entries[0].supported_subentry_types
    assert SUBENTRY_TYPE_SEARCH in subentry_types
