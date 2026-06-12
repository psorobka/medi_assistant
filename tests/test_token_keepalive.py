"""Tests for TokenKeepAlive — proactive refresh of the short-lived token.

Regression 2026-06-13: polling slots only every 10 min let the Medicover
refresh token sit unused past its sliding window, so the next refresh failed
with invalid_grant → forced reauth. The keep-alive refreshes the token shortly
before the access token expires, mimicking an active web session.
"""
from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.medi_assistant.exceptions import InvalidGrant
from custom_components.medi_assistant.token_keepalive import (
    FALLBACK_DELAY,
    MIN_DELAY,
    REFRESH_BUFFER,
    RETRY_DELAY,
    TokenKeepAlive,
)

_MODULE = "custom_components.medi_assistant.token_keepalive"


def _make_keepalive(expires_at=None):
    hass = MagicMock()
    entry = MagicMock()
    entry.title = "Jan Kowalski"
    auth = MagicMock()
    auth._expires_at = expires_at
    auth.async_refresh_token = AsyncMock()
    auth.async_refresh_or_relogin = AsyncMock()
    return TokenKeepAlive(hass, entry, auth), auth, entry


# ---------------------------------------------------------------------------
# _next_delay
# ---------------------------------------------------------------------------


def test_next_delay_uses_expiry_minus_buffer():
    """Delay should be (expires_at - now - REFRESH_BUFFER) for a fresh token."""
    expires_at = int(time.time()) + 180
    ka, _, _ = _make_keepalive(expires_at)
    delay = ka._next_delay()
    assert abs(delay - (180 - REFRESH_BUFFER)) < 2


def test_next_delay_floors_at_min_delay():
    """A nearly-expired token must not schedule a near-zero / negative delay."""
    ka, _, _ = _make_keepalive(int(time.time()) + 5)
    assert ka._next_delay() == MIN_DELAY


def test_next_delay_fallback_when_no_expiry():
    """No expires_at → use the fallback delay."""
    ka, _, _ = _make_keepalive(expires_at=None)
    assert ka._next_delay() == FALLBACK_DELAY


# ---------------------------------------------------------------------------
# start / stop scheduling
# ---------------------------------------------------------------------------


def test_start_schedules_call_later():
    """start() registers a timer via async_call_later."""
    ka, _, _ = _make_keepalive(int(time.time()) + 180)
    with patch(f"{_MODULE}.async_call_later", return_value=MagicMock()) as mock_cl:
        ka.start()
    mock_cl.assert_called_once()
    # delay arg is positional index 1
    assert abs(mock_cl.call_args[0][1] - (180 - REFRESH_BUFFER)) < 2


def test_stop_cancels_pending_timer():
    """stop() calls the unsub returned by async_call_later."""
    ka, _, _ = _make_keepalive(int(time.time()) + 180)
    unsub = MagicMock()
    with patch(f"{_MODULE}.async_call_later", return_value=unsub):
        ka.start()
    ka.stop()
    unsub.assert_called_once()


def test_stop_is_noop_when_nothing_scheduled():
    """stop() before start() must not raise."""
    ka, _, _ = _make_keepalive()
    ka.stop()  # no exception


# ---------------------------------------------------------------------------
# refresh outcomes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_success_reschedules():
    """A successful refresh schedules the next one."""
    ka, auth, _ = _make_keepalive(int(time.time()) + 180)
    with patch(f"{_MODULE}.async_call_later", return_value=MagicMock()) as mock_cl:
        await ka._async_refresh(None)

    auth.async_refresh_or_relogin.assert_awaited_once()
    mock_cl.assert_called_once()  # rescheduled


@pytest.mark.asyncio
async def test_refresh_invalid_grant_starts_reauth_and_stops():
    """invalid_grant → start reauth, do NOT reschedule."""
    ka, auth, entry = _make_keepalive(int(time.time()) + 180)
    auth.async_refresh_or_relogin = AsyncMock(side_effect=InvalidGrant("expired"))

    with patch(f"{_MODULE}.async_call_later") as mock_cl:
        await ka._async_refresh(None)

    entry.async_start_reauth.assert_called_once()
    mock_cl.assert_not_called()  # no reschedule after fatal auth error


@pytest.mark.asyncio
async def test_refresh_transient_error_retries():
    """A non-auth error reschedules a retry with RETRY_DELAY (and no reauth)."""
    ka, auth, entry = _make_keepalive(int(time.time()) + 180)
    auth.async_refresh_or_relogin = AsyncMock(side_effect=ConnectionError("boom"))

    with patch(f"{_MODULE}.async_call_later", return_value=MagicMock()) as mock_cl:
        await ka._async_refresh(None)

    entry.async_start_reauth.assert_not_called()
    mock_cl.assert_called_once()
    assert mock_cl.call_args[0][1] == RETRY_DELAY
