"""Tests for the PKCE login / MFA flow and MedicoverClient query methods.

The login flow mixes `await session.get(...)` (steps + redirects) with
`async with session.post(...)` (token exchange), so the response mock here is
both awaitable *and* an async context manager — mirroring aiohttp's real
`_RequestContextManager`. HTTP is fully mocked; the real API is never hit.
"""

from __future__ import annotations

import json
import time
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import quote_plus

import aiohttp
import pytest
from yarl import URL

from custom_components.medi_assistant.api import MedicoverAuth, MedicoverClient
from custom_components.medi_assistant.const import (
    MEDICOVER_LOGIN_URL,
    MEDICOVER_MAIN_URL,
)
from custom_components.medi_assistant.exceptions import ApiError, AuthError, MfaRequired

_DEVICE_UA = "TestBrowser/1.0"
_TOKEN_ENDPOINT = f"{MEDICOVER_LOGIN_URL}/connect/token"
_OIDC = {
    "authorization_endpoint": f"{MEDICOVER_LOGIN_URL}/connect/authorize",
    "token_endpoint": _TOKEN_ENDPOINT,
    "revocation_endpoint": f"{MEDICOVER_LOGIN_URL}/connect/revocation",
}
_CSRF_HTML = '<input name="__RequestVerificationToken" type="hidden" value="CSRF1" />'
_TOKEN_BODY = {"access_token": "at", "refresh_token": "rt", "expires_in": 300}


def _make_auth() -> tuple[MedicoverAuth, aiohttp.ClientSession]:
    session = aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True))
    auth = MedicoverAuth(session, device_id="dev-1", device_ua=_DEVICE_UA)
    auth._oidc_config = dict(_OIDC)
    return auth, session


def _resp(status=200, *, location=None, text="", url="", json_body=None) -> MagicMock:
    r = MagicMock()
    r.status = status
    r.headers = {"Location": location} if location else {}
    r.text = AsyncMock(return_value=text)
    r.json = AsyncMock(return_value=json_body if json_body is not None else {})
    r.url = url
    r.raise_for_status = MagicMock()
    return r


class _Dual:
    """Awaitable AND async-context-manager wrapper around a response mock."""

    def __init__(self, resp: MagicMock) -> None:
        self._r = resp

    def __await__(self):
        async def _a():
            return self._r

        return _a().__await__()

    async def __aenter__(self):
        return self._r

    async def __aexit__(self, *_a):
        return False


def _cm(resp: MagicMock) -> _Dual:
    return _Dual(resp)


@pytest.fixture(autouse=True)
def _no_sleep():
    """Skip the deliberate inter-step delays so login tests run instantly."""
    with patch("custom_components.medi_assistant.api.asyncio.sleep", new=AsyncMock()):
        yield


# ---------------------------------------------------------------------------
# OIDC discovery
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_oidc_config_fetches_and_caches():
    session = aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True))
    auth = MedicoverAuth(session, device_id="dev-1", device_ua=_DEVICE_UA)
    try:
        with patch.object(session, "get", return_value=_cm(_resp(json_body=_OIDC))) as mget:
            got = await auth.async_get_oidc_config()
            assert got["token_endpoint"] == _TOKEN_ENDPOINT
            # Second call is served from cache, no extra request.
            again = await auth.async_get_oidc_config()
            assert again is got
            mget.assert_called_once()
    finally:
        await session.close()


# ---------------------------------------------------------------------------
# async_login — happy path → token exchange
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_login_happy_path_exchanges_code():
    auth, session = _make_auth()
    login_url = f"{MEDICOVER_LOGIN_URL}/Account/Login?ReturnUrl=%2Fconnect%2Fauthorize%3Fx%3D1"
    code_url = f"{MEDICOVER_MAIN_URL}/signin-oidc?code=AUTHCODE&state=x"
    gets = [
        _cm(_resp(302, location=login_url)),  # GET authorize
        _cm(_resp(200, text=_CSRF_HTML, url=login_url)),  # GET login page
    ]
    posts = [
        _cm(_resp(302, location=code_url)),  # POST credentials
        _cm(_resp(200, json_body=_TOKEN_BODY)),  # POST token exchange
    ]
    try:
        with (
            patch.object(session, "get", side_effect=gets),
            patch.object(session, "post", side_effect=posts),
        ):
            await auth.async_login("user@example.com", "secret")
        assert auth._access_token == "at"
        assert auth._refresh_token_val == "rt"
        assert auth._expires_at is not None
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_login_missing_location_raises():
    auth, session = _make_auth()
    try:
        with patch.object(session, "get", return_value=_cm(_resp(302))):  # no Location
            with pytest.raises(AuthError):
                await auth.async_login("u", "p")
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_login_final_200_raises_unexpected_page():
    auth, session = _make_auth()
    login_url = f"{MEDICOVER_LOGIN_URL}/Account/Login?ReturnUrl=%2Fx"
    gets = [
        _cm(_resp(302, location=login_url)),
        _cm(_resp(200, text=_CSRF_HTML, url=login_url)),
    ]
    try:
        with (
            patch.object(session, "get", side_effect=gets),
            patch.object(session, "post", return_value=_cm(_resp(200))),  # no redirect
        ):
            with pytest.raises(AuthError):
                await auth.async_login("u", "p")
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_login_bad_status_raises():
    auth, session = _make_auth()
    login_url = f"{MEDICOVER_LOGIN_URL}/Account/Login?ReturnUrl=%2Fx"
    gets = [
        _cm(_resp(302, location=login_url)),
        _cm(_resp(200, text=_CSRF_HTML, url=login_url)),
    ]
    try:
        with (
            patch.object(session, "get", side_effect=gets),
            patch.object(session, "post", return_value=_cm(_resp(500))),
        ):
            with pytest.raises(AuthError):
                await auth.async_login("u", "p")
    finally:
        await session.close()


# ---------------------------------------------------------------------------
# MFA path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_login_mfa_redirect_raises_mfa_required():
    auth, session = _make_auth()
    login_url = f"{MEDICOVER_LOGIN_URL}/Account/Login?ReturnUrl=%2Fx"
    mfa_url = f"{MEDICOVER_LOGIN_URL}/Account/Mfa?ReturnUrl=%2Fdone"
    # Server set MfaInfo during the challenge.
    session.cookie_jar.update_cookies(
        {"MfaInfo": quote_plus(json.dumps({"MfaCodeId": "MFA-1"}))},
        URL(MEDICOVER_LOGIN_URL),
    )
    gets = [
        _cm(_resp(302, location=login_url)),  # GET authorize
        _cm(_resp(200, text=_CSRF_HTML, url=login_url)),  # GET login page
        _cm(_resp(200, text=_CSRF_HTML, url=mfa_url)),  # GET mfa page
    ]
    try:
        with (
            patch.object(session, "get", side_effect=gets),
            patch.object(session, "post", return_value=_cm(_resp(302, location=mfa_url))),
        ):
            with pytest.raises(MfaRequired) as exc:
                await auth.async_login("u", "p")
        assert exc.value.mfa_code_id == "MFA-1"
        assert auth._mfa_context is not None
        assert auth._mfa_context["return_url"] == "/done"
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_submit_mfa_exchanges_code():
    auth, session = _make_auth()
    auth._mfa_context = {
        "mfa_code_id": "MFA-1",
        "return_url": "/done",
        "csrf": "CSRF1",
        "code_verifier": "verifier",
    }
    code_url = f"{MEDICOVER_MAIN_URL}/signin-oidc?code=AUTHCODE"
    posts = [
        _cm(_resp(302, location=code_url)),  # POST Mfa
        _cm(_resp(200, json_body=_TOKEN_BODY)),  # POST token
    ]
    try:
        with patch.object(session, "post", side_effect=posts):
            await auth.async_submit_mfa("123456")
        assert auth._access_token == "at"
        assert auth._mfa_context is None
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_submit_mfa_without_context_raises():
    auth, session = _make_auth()
    try:
        with pytest.raises(AuthError):
            await auth.async_submit_mfa("123456")
    finally:
        await session.close()


# ---------------------------------------------------------------------------
# logout / revocation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_logout_noop_without_refresh_token():
    auth, session = _make_auth()
    try:
        with patch.object(session, "post") as mpost:
            await auth.async_logout()
            mpost.assert_not_called()
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_logout_posts_to_revocation_endpoint():
    auth, session = _make_auth()
    auth._refresh_token_val = "rt"
    try:
        with patch.object(session, "post", return_value=_cm(_resp(200))) as mpost:
            await auth.async_logout()
            mpost.assert_called_once()
            assert mpost.call_args[0][0] == _OIDC["revocation_endpoint"]
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_logout_swallows_errors():
    auth, session = _make_auth()
    auth._refresh_token_val = "rt"
    try:
        with patch.object(session, "post", side_effect=RuntimeError("boom")):
            await auth.async_logout()  # must not raise
    finally:
        await session.close()


# ---------------------------------------------------------------------------
# MedicoverClient query methods
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_personal_data_returns_json():
    auth, session = _make_auth()
    auth._access_token = "at"
    auth._expires_at = int(time.time()) + 300
    client = MedicoverClient(auth, session)
    try:
        with patch.object(
            session, "get", return_value=_cm(_resp(200, json_body={"firstName": "Jan"}))
        ):
            data = await client.async_get_personal_data()
        assert data["firstName"] == "Jan"
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_personal_data_retries_after_401():
    auth, session = _make_auth()
    auth.async_refresh_token = AsyncMock()
    client = MedicoverClient(auth, session)
    try:
        with patch.object(
            session,
            "get",
            side_effect=[_cm(_resp(401)), _cm(_resp(200, json_body={"firstName": "Ola"}))],
        ):
            data = await client.async_get_personal_data()
        assert data["firstName"] == "Ola"
        auth.async_refresh_token.assert_awaited_once_with(force=True)
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_personal_data_raises_on_persistent_error():
    auth, session = _make_auth()
    auth.async_refresh_token = AsyncMock()
    client = MedicoverClient(auth, session)
    try:
        with patch.object(session, "get", side_effect=[_cm(_resp(401)), _cm(_resp(500))]):
            with pytest.raises(ApiError):
                await client.async_get_personal_data()
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_find_filters_passes_region_and_specialty():
    auth, session = _make_auth()
    auth._access_token = "at"
    auth._expires_at = int(time.time()) + 300
    client = MedicoverClient(auth, session)
    try:
        with patch.object(
            session, "get", return_value=_cm(_resp(200, json_body={"regions": [{"id": 1}]}))
        ) as mget:
            data = await client.async_find_filters(region=204, specialty=[9])
        assert data["regions"] == [{"id": 1}]
        params = mget.call_args.kwargs["params"]
        assert params["RegionIds"] == 204
        assert params["SpecialtyIds"] == [9]
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_find_appointments_filters_by_end_date():
    auth, session = _make_auth()
    auth._access_token = "at"
    auth._expires_at = int(time.time()) + 300
    client = MedicoverClient(auth, session)
    items = {
        "items": [
            {"appointmentDate": "2026-07-01T10:00:00"},
            {"appointmentDate": "2026-07-20T09:00:00"},
        ]
    }
    try:
        with patch.object(session, "get", return_value=_cm(_resp(200, json_body=items))):
            result = await client.async_find_appointments(
                region=204, specialty=[9], end_date="2026-07-10"
            )
        # Only the slot on/before end_date survives the client-side filter.
        assert len(result) == 1
        assert result[0]["appointmentDate"].startswith("2026-07-01")
    finally:
        await session.close()
