"""Keeps the (short-lived) Medicover refresh token alive.

Medicover issues very short access tokens (~3 min) and refresh tokens with a
small sliding window (< ~10 min). The reference implementation (medichaser)
runs a tight loop that refreshes the access token within seconds of expiry,
which keeps rotating the refresh token so it never sits unused long enough to
be rejected.

Polling appointment slots only every N (default 10) minutes is far too slow:
the refresh token expires between polls and the next refresh fails with
``invalid_grant`` → forced reauth.

This helper schedules a proactive refresh shortly before each access token
expires (independent of the slot poll interval), mimicking an active web
session and keeping the refresh token chain alive.
"""
from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.event import async_call_later

from .exceptions import AuthError, InvalidGrant, MfaRequired

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

    from .api import MedicoverAuth

_LOGGER = logging.getLogger(__name__)

# Refresh this many seconds before the access token expires.
REFRESH_BUFFER = 30
# Never schedule sooner than this (avoids a hot loop on odd expiry values).
MIN_DELAY = 30.0
# Fallback delay when expiry is unknown.
FALLBACK_DELAY = 60.0
# Delay before retrying after a transient (non-auth) refresh error.
RETRY_DELAY = 30.0
# How many consecutive auth failures to tolerate before forcing reauth. The
# silent re-login (trusted device) occasionally fails transiently — a single
# hiccup should not pop a reauth prompt and stop the keep-alive (a restart
# would just recover it). Retry a few times first.
MAX_AUTH_RETRIES = 3
# Base delay between auth-failure retries (grows linearly per attempt).
AUTH_RETRY_DELAY = 30.0


class TokenKeepAlive:
    """Periodically refreshes the access token to keep the refresh token alive."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, auth: MedicoverAuth
    ) -> None:
        self._hass = hass
        self._entry = entry
        self._auth = auth
        self._unsub: CALLBACK_TYPE | None = None
        # Consecutive auth failures; reset on any successful refresh.
        self._auth_failures = 0

    @callback
    def start(self) -> None:
        """Schedule the first refresh."""
        self._schedule(self._next_delay())

    @callback
    def stop(self) -> None:
        """Cancel any pending refresh (called on entry unload)."""
        if self._unsub is not None:
            self._unsub()
            self._unsub = None

    @callback
    def _schedule(self, delay: float) -> None:
        self._unsub = async_call_later(self._hass, delay, self._async_refresh)
        _LOGGER.debug(
            "Token keep-alive for '%s': next refresh in %.0fs",
            self._entry.title,
            delay,
        )

    def _next_delay(self) -> float:
        expires_at = self._auth._expires_at
        if not expires_at:
            return FALLBACK_DELAY
        return max(MIN_DELAY, expires_at - time.time() - REFRESH_BUFFER)

    async def _async_refresh(self, _now) -> None:
        self._unsub = None
        try:
            await self._auth.async_refresh_or_relogin()
        except (InvalidGrant, MfaRequired, AuthError) as err:
            self._auth_failures += 1
            if self._auth_failures < MAX_AUTH_RETRIES:
                retry_delay = AUTH_RETRY_DELAY * self._auth_failures
                _LOGGER.warning(
                    "Refresh + silent re-login failed for '%s' (%s, attempt %d/%d) "
                    "— retrying in %.0fs",
                    self._entry.title,
                    type(err).__name__,
                    self._auth_failures,
                    MAX_AUTH_RETRIES,
                    retry_delay,
                )
                self._schedule(retry_delay)
                return
            _LOGGER.warning(
                "Refresh + silent re-login failed for '%s' (%s) after %d attempts "
                "— starting reauth",
                self._entry.title,
                type(err).__name__,
                self._auth_failures,
            )
            self._auth_failures = 0
            self._entry.async_start_reauth(self._hass)
            return
        except Exception as err:  # noqa: BLE001 — transient errors get a retry
            _LOGGER.warning(
                "Token keep-alive refresh failed for '%s': %s — retrying in %.0fs",
                self._entry.title,
                err,
                RETRY_DELAY,
            )
            self._schedule(RETRY_DELAY)
            return
        self._auth_failures = 0
        self._schedule(self._next_delay())
