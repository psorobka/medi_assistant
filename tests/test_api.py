"""Tests for MedicoverAuth — device cookie and token refresh behaviour."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
from yarl import URL

from custom_components.medi_assistant.api import (
    MedicoverAuth,
    MedicoverClient,
    _extract_csrf,
    _extract_mfa_code_id,
    _extract_return_url,
    _extract_return_url_from_url,
)
from custom_components.medi_assistant.const import MEDICOVER_LOGIN_URL
from custom_components.medi_assistant.exceptions import AuthError, InvalidGrant, MfaRequired

_DEVICE_ID = "test-device-id-1234"
_DEVICE_UA = "TestBrowser/1.0"
_TOKEN_ENDPOINT = "https://login-online24.medicover.pl/connect/token"
_OIDC = {"token_endpoint": _TOKEN_ENDPOINT}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_auth(device_id: str = _DEVICE_ID) -> tuple[MedicoverAuth, aiohttp.ClientSession]:
    jar = aiohttp.CookieJar(unsafe=True)
    session = aiohttp.ClientSession(cookie_jar=jar)
    auth = MedicoverAuth(session, device_id=device_id, device_ua=_DEVICE_UA)
    return auth, session


def _mock_post_cm(body: str | dict, status: int = 200) -> MagicMock:
    """Return an async context-manager mock for session.post(...)."""
    if isinstance(body, dict):
        body = json.dumps(body)
    resp = MagicMock()
    resp.status = status
    resp.text = AsyncMock(return_value=body)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=resp)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


# ---------------------------------------------------------------------------
# __mcc cookie — device identification required by Medicover token endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mcc_cookie_set_on_auth_init():
    """MedicoverAuth.__init__ must write __mcc = device_id into the cookie jar.

    Regression 2026-06-13: the first scheduled poll after login triggered a
    token refresh, but __mcc was absent from the session, causing Medicover to
    return invalid_grant even though the refresh token itself was still valid.
    """
    auth, session = _make_auth()
    try:
        cookies = session.cookie_jar.filter_cookies(URL(MEDICOVER_LOGIN_URL))
        assert "__mcc" in cookies, f"__mcc missing; found: {list(cookies.keys())}"
        assert cookies["__mcc"].value == _DEVICE_ID
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_mcc_cookie_value_matches_device_id():
    """__mcc value must equal the device_id argument, not some default."""
    custom_id = "custom-uuid-abcd-5678"
    auth, session = _make_auth(device_id=custom_id)
    try:
        cookies = session.cookie_jar.filter_cookies(URL(MEDICOVER_LOGIN_URL))
        assert cookies["__mcc"].value == custom_id
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_mcc_cookie_set_on_fresh_session():
    """A brand-new session (as created by async_setup_entry on every HA start)
    must also receive __mcc — the fix must not rely on cookies carried over
    from the config-flow login session.
    """
    jar = aiohttp.CookieJar(unsafe=True)
    fresh_session = aiohttp.ClientSession(cookie_jar=jar)
    try:
        MedicoverAuth(fresh_session, device_id=_DEVICE_ID, device_ua=_DEVICE_UA)
        cookies = fresh_session.cookie_jar.filter_cookies(URL(MEDICOVER_LOGIN_URL))
        assert "__mcc" in cookies, "Fresh session cookie jar must have __mcc after MedicoverAuth init"
    finally:
        await fresh_session.close()


# ---------------------------------------------------------------------------
# Login session cookies — must survive the login→runtime handoff
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_token_data_includes_login_cookies():
    """token_data() must export session cookies so they get persisted.

    Regression 2026-06-14: refresh in a fresh session carrying only __mcc was
    rejected with invalid_grant. Medicover's token endpoint needs the session
    cookies set during login (e.g. idsrv.session), so they must round-trip.
    """
    auth, session = _make_auth()
    try:
        # Simulate cookies the server set during login.
        session.cookie_jar.update_cookies(
            {"idsrv.session": "abc123", "idsrv": "deadbeef"},
            URL(MEDICOVER_LOGIN_URL),
        )
        data = auth.token_data()
        cookies = data["auth_cookies"]
        assert cookies["idsrv.session"] == "abc123"
        assert cookies["idsrv"] == "deadbeef"
        assert cookies["__mcc"] == _DEVICE_ID
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_load_tokens_restores_cookies_into_fresh_session():
    """load_tokens() must import persisted cookies into the runtime jar.

    This is the fix: a fresh runtime session (post-restart / post-setup) gets
    back the login session cookies, so refresh carries more than just __mcc.
    """
    auth, session = _make_auth()
    try:
        auth.load_tokens(
            {
                "access_token": "at",
                "refresh_token": "rt",
                "expires_at": 9999999999,
                "auth_cookies": {"idsrv.session": "xyz", "__mcc": _DEVICE_ID},
            }
        )
        cookies = session.cookie_jar.filter_cookies(URL(MEDICOVER_LOGIN_URL))
        assert "idsrv.session" in cookies
        assert cookies["idsrv.session"].value == "xyz"
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_cookie_roundtrip_export_import():
    """Cookies exported from one auth survive import into another (fresh) auth."""
    auth1, session1 = _make_auth()
    auth2, session2 = _make_auth(device_id="other-device")
    try:
        session1.cookie_jar.update_cookies(
            {"idsrv.session": "roundtrip"}, URL(MEDICOVER_LOGIN_URL)
        )
        exported = auth1.export_cookies()

        auth2.import_cookies(exported)
        cookies = session2.cookie_jar.filter_cookies(URL(MEDICOVER_LOGIN_URL))
        assert cookies["idsrv.session"].value == "roundtrip"
    finally:
        await session1.close()
        await session2.close()


# ---------------------------------------------------------------------------
# async_refresh_or_relogin — medichaser model: silent re-login on invalid_grant
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_or_relogin_no_relogin_on_success():
    """When refresh works, no re-login is attempted."""
    auth, session = _make_auth()
    auth.set_credentials("user@example.com", "secret")
    auth._async_perform_refresh = AsyncMock()
    auth.async_login = AsyncMock()
    try:
        await auth.async_refresh_or_relogin()
        auth._async_perform_refresh.assert_awaited_once()
        auth.async_login.assert_not_called()
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_refresh_or_relogin_relogins_on_invalid_grant():
    """invalid_grant + stored credentials → silent re-login with those creds."""
    auth, session = _make_auth()
    auth.set_credentials("user@example.com", "secret")
    auth._async_perform_refresh = AsyncMock(side_effect=InvalidGrant("dead"))
    auth.async_login = AsyncMock()
    try:
        await auth.async_refresh_or_relogin()
        auth.async_login.assert_awaited_once_with("user@example.com", "secret")
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_refresh_or_relogin_reraises_without_credentials():
    """invalid_grant + no stored credentials → propagate InvalidGrant (→ reauth)."""
    auth, session = _make_auth()  # no set_credentials
    auth._async_perform_refresh = AsyncMock(side_effect=InvalidGrant("dead"))
    auth.async_login = AsyncMock()
    try:
        with pytest.raises(InvalidGrant):
            await auth.async_refresh_or_relogin()
        auth.async_login.assert_not_called()
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_refresh_or_relogin_propagates_mfa_required():
    """If silent re-login hits MFA (device not trusted), propagate MfaRequired."""
    auth, session = _make_auth()
    auth.set_credentials("user@example.com", "secret")
    auth._async_perform_refresh = AsyncMock(side_effect=InvalidGrant("dead"))
    auth.async_login = AsyncMock(side_effect=MfaRequired("id", "csrf", "/r"))
    try:
        with pytest.raises(MfaRequired):
            await auth.async_refresh_or_relogin()
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_concurrent_refresh_runs_only_once():
    """Two tasks refreshing at once → lock + double-check = one actual refresh.

    Regression for the keep-alive vs coordinator race: both could POST the same
    refresh token and the second would get invalid_grant.
    """
    import asyncio
    import time

    auth, session = _make_auth()
    auth.set_credentials("user@example.com", "secret")
    calls = 0

    async def _fake_refresh():
        nonlocal calls
        calls += 1
        await asyncio.sleep(0)  # yield so the second task can interleave
        auth._access_token = "fresh"
        auth._expires_at = int(time.time()) + 300

    auth._async_perform_refresh = _fake_refresh
    auth.async_login = AsyncMock()
    try:
        await asyncio.gather(
            auth.async_refresh_or_relogin(),
            auth.async_refresh_or_relogin(),
        )
        assert calls == 1  # second task saw a valid token and skipped
        auth.async_login.assert_not_called()
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_refresh_token_double_checked_skips_when_valid():
    """async_refresh_token (force=False) returns early if token already valid."""
    import time

    auth, session = _make_auth()
    auth._access_token = "at"
    auth._expires_at = int(time.time()) + 300
    auth._async_perform_refresh = AsyncMock()
    try:
        await auth.async_refresh_token()  # force=False
        auth._async_perform_refresh.assert_not_called()
        await auth.async_refresh_token(force=True)  # 401 path bypasses the check
        auth._async_perform_refresh.assert_awaited_once()
    finally:
        await session.close()


# ---------------------------------------------------------------------------
# async_refresh_token
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_refresh_token_updates_tokens():
    """Successful refresh stores new access/refresh tokens and expires_at."""
    auth, session = _make_auth()
    auth._refresh_token_val = "old-rt"
    auth._oidc_config = _OIDC

    body = {"access_token": "new-at", "refresh_token": "new-rt", "expires_in": 300}
    try:
        with patch.object(session, "post", return_value=_mock_post_cm(body)):
            await auth.async_refresh_token()

        assert auth._access_token == "new-at"
        assert auth._refresh_token_val == "new-rt"
        assert auth._expires_at is not None
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_async_refresh_token_raises_invalid_grant():
    """Token endpoint returning {"error": "invalid_grant"} must raise InvalidGrant."""
    auth, session = _make_auth()
    auth._refresh_token_val = "expired-rt"
    auth._oidc_config = _OIDC

    body = {"error": "invalid_grant", "error_description": "Refresh token expired"}
    try:
        with patch.object(session, "post", return_value=_mock_post_cm(body, status=400)):
            with pytest.raises(InvalidGrant):
                await auth.async_refresh_token()
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_async_refresh_token_raises_auth_error_on_other_error():
    """Other OAuth errors (not invalid_grant) must raise AuthError, not InvalidGrant."""
    auth, session = _make_auth()
    auth._refresh_token_val = "some-rt"
    auth._oidc_config = _OIDC

    body = {"error": "server_error", "error_description": "Internal error"}
    try:
        with patch.object(session, "post", return_value=_mock_post_cm(body, status=500)):
            with pytest.raises(AuthError):
                await auth.async_refresh_token()
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_async_refresh_token_no_refresh_token_raises_auth_error():
    """Calling async_refresh_token with no stored refresh token raises AuthError."""
    auth, session = _make_auth()
    auth._oidc_config = _OIDC
    try:
        with pytest.raises(AuthError):
            await auth.async_refresh_token()
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_async_refresh_token_calls_update_callback():
    """token_update_callback must be called with the new token data."""
    callback = AsyncMock()
    jar = aiohttp.CookieJar(unsafe=True)
    session = aiohttp.ClientSession(cookie_jar=jar)
    auth = MedicoverAuth(
        session, device_id=_DEVICE_ID, device_ua=_DEVICE_UA, token_update_callback=callback
    )
    auth._refresh_token_val = "old-rt"
    auth._oidc_config = _OIDC

    body = {"access_token": "new-at", "refresh_token": "new-rt", "expires_in": 300}
    try:
        with patch.object(session, "post", return_value=_mock_post_cm(body)):
            await auth.async_refresh_token()

        callback.assert_called_once()
        assert callback.call_args[0][0]["access_token"] == "new-at"
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_async_refresh_token_raises_auth_error_on_non_json_response():
    """Non-JSON response from token endpoint raises AuthError (not a crash)."""
    auth, session = _make_auth()
    auth._refresh_token_val = "some-rt"
    auth._oidc_config = _OIDC

    try:
        with patch.object(session, "post", return_value=_mock_post_cm("not json at all", status=502)):
            with pytest.raises(AuthError):
                await auth.async_refresh_token()
    finally:
        await session.close()


# ---------------------------------------------------------------------------
# MedicoverClient._get — 401 → refresh → retry
# ---------------------------------------------------------------------------


def _mock_get_cm(status: int, json_body: dict | None = None) -> MagicMock:
    resp = MagicMock()
    resp.status = status
    resp.json = AsyncMock(return_value=json_body or {})
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=resp)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


@pytest.mark.asyncio
async def test_get_retries_after_401_with_forced_refresh():
    """A 401 triggers a forced token refresh and one retry, then returns data."""
    auth, session = _make_auth()
    auth.async_refresh_token = AsyncMock()
    client = MedicoverClient(auth, session)
    try:
        with patch.object(
            session, "get",
            side_effect=[_mock_get_cm(401), _mock_get_cm(200, {"ok": True})],
        ):
            data = await client._get("https://api/x", {})
        assert data == {"ok": True}
        auth.async_refresh_token.assert_awaited_once_with(force=True)
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_get_raises_apierror_on_persistent_failure():
    auth, session = _make_auth()
    auth.async_refresh_token = AsyncMock()
    client = MedicoverClient(auth, session)
    try:
        with patch.object(
            session, "get",
            side_effect=[_mock_get_cm(401), _mock_get_cm(500)],
        ):
            with pytest.raises(Exception):
                await client._get("https://api/x", {})
    finally:
        await session.close()


# ---------------------------------------------------------------------------
# HTML / cookie parsers (brittle — worth pinning)
# ---------------------------------------------------------------------------


def test_extract_csrf_found():
    html = '<input name="__RequestVerificationToken" type="hidden" value="TOK123" />'
    assert _extract_csrf(html) == "TOK123"


def test_extract_csrf_missing_raises():
    with pytest.raises(AuthError):
        _extract_csrf("<html>no token here</html>")


def test_extract_return_url_from_page_url():
    url = "https://login-online24.medicover.pl/Account/Login?ReturnUrl=%2Fconnect%2Fauthorize%3Fx%3D1"
    assert _extract_return_url(url) == "/connect/authorize?x=1"


def test_extract_return_url_missing_raises():
    with pytest.raises(AuthError):
        _extract_return_url("https://login-online24.medicover.pl/Account/Login")


def test_extract_return_url_from_mfa_url():
    url = "https://login-online24.medicover.pl/Account/Mfa?ReturnUrl=%2Fdone"
    assert _extract_return_url_from_url(url) == "/done"


@pytest.mark.asyncio
async def test_extract_mfa_code_id_from_cookie():
    import json as _json
    from urllib.parse import quote_plus

    jar = aiohttp.CookieJar(unsafe=True)
    jar.update_cookies(
        {"MfaInfo": quote_plus(_json.dumps({"MfaCodeId": "MFA-99"}))},
        URL(MEDICOVER_LOGIN_URL),
    )
    assert _extract_mfa_code_id(jar, MEDICOVER_LOGIN_URL) == "MFA-99"


@pytest.mark.asyncio
async def test_extract_mfa_code_id_missing_cookie_raises():
    jar = aiohttp.CookieJar(unsafe=True)
    with pytest.raises(AuthError):
        _extract_mfa_code_id(jar, MEDICOVER_LOGIN_URL)
