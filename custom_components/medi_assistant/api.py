from __future__ import annotations

import asyncio
import base64
import datetime as dt
import hashlib
import json
import logging
import random
import re
import string
import time
import uuid
from collections.abc import Callable, Coroutine
from typing import Any
from urllib.parse import parse_qs, quote_plus, unquote_plus, urlparse

import aiohttp
from yarl import URL as _YarlURL

from .const import (
    ENTRY_ACCESS_TOKEN,
    ENTRY_AUTH_COOKIES,
    ENTRY_DEVICE_ID,
    ENTRY_DEVICE_UA,
    ENTRY_EXPIRES_AT,
    ENTRY_REFRESH_TOKEN,
    MEDICOVER_API_URL,
    MEDICOVER_LOGIN_URL,
    MEDICOVER_MAIN_URL,
    OIDC_DISCOVERY_URL,
)
from .exceptions import ApiError, AuthError, InvalidGrant, MfaRequired

_LOGGER = logging.getLogger(__name__)

_DEFAULT_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def _new_device_id() -> str:
    return str(uuid.uuid4())


def _build_base_headers(device_ua: str, access_token: str | None = None) -> dict[str, str]:
    h: dict[str, str] = {
        "Accept": "application/json",
        "Accept-Encoding": "gzip, deflate, br, zstd",
        "Accept-Language": "pl",
        "Origin": MEDICOVER_MAIN_URL,
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-site",
        "Sec-GPC": "1",
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": "Linux",
        "User-Agent": device_ua,
    }
    if access_token:
        h["Authorization"] = f"Bearer {access_token}"
    return h


class MedicoverAuth:
    """PKCE + MFA login, token refresh and revocation."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        device_id: str,
        device_ua: str,
        token_update_callback: Callable[[dict[str, Any]], Coroutine[Any, Any, None]] | None = None,
    ) -> None:
        self._session = session
        self._device_id = device_id
        self._device_ua = device_ua
        self._token_update_callback = token_update_callback

        self._access_token: str | None = None
        self._refresh_token_val: str | None = None
        self._expires_at: int | None = None
        self._oidc_config: dict[str, Any] | None = None

        # Serializes token refresh/re-login so the keep-alive timer and the
        # coordinator poll (which can fire in the same ~30s window) never POST
        # the same refresh token twice (Medicover rotates it → invalid_grant).
        self._refresh_lock = asyncio.Lock()

        # Optional stored credentials for silent re-login (medichaser model).
        self._username: str | None = None
        self._password: str | None = None

        # Preserved between async_login → async_submit_mfa
        self._mfa_context: dict[str, Any] | None = None

        # Medicover identifies the device via __mcc cookie (same as device_id param
        # in the authorize request). Must be present on every request including
        # token refresh, otherwise the server returns invalid_grant.
        session.cookie_jar.update_cookies({"__mcc": device_id}, _YarlURL(MEDICOVER_LOGIN_URL))

    # ------------------------------------------------------------------
    # Token helpers
    # ------------------------------------------------------------------

    def set_credentials(self, username: str | None, password: str | None) -> None:
        """Store credentials so refresh can fall back to a silent re-login."""
        self._username = username
        self._password = password

    def load_tokens(self, data: dict[str, Any]) -> None:
        self._access_token = data.get(ENTRY_ACCESS_TOKEN)
        self._refresh_token_val = data.get(ENTRY_REFRESH_TOKEN)
        self._expires_at = data.get(ENTRY_EXPIRES_AT)
        self.import_cookies(data.get(ENTRY_AUTH_COOKIES) or {})
        _LOGGER.debug(
            "Tokens loaded: has_access=%s, has_refresh=%s, expires_at=%s, "
            "secs_remaining=%s, cookies=%d",
            bool(self._access_token),
            bool(self._refresh_token_val),
            self._expires_at,
            (self._expires_at - int(time.time())) if self._expires_at else None,
            len(data.get(ENTRY_AUTH_COOKIES) or {}),
        )

    def token_data(self) -> dict[str, Any]:
        return {
            ENTRY_ACCESS_TOKEN: self._access_token,
            ENTRY_REFRESH_TOKEN: self._refresh_token_val,
            ENTRY_EXPIRES_AT: self._expires_at,
            ENTRY_DEVICE_ID: self._device_id,
            ENTRY_DEVICE_UA: self._device_ua,
            ENTRY_AUTH_COOKIES: self.export_cookies(),
        }

    def export_cookies(self) -> dict[str, str]:
        """Snapshot all cookies on the auth session (login/idsrv session etc.).

        Medicover's token endpoint rejects a refresh that carries only `__mcc`;
        it needs the session cookies the server set during login. We persist
        them so the runtime session (and post-restart session) can refresh.
        """
        out: dict[str, str] = {}
        try:
            for morsel in self._session.cookie_jar:
                out[morsel.key] = morsel.value
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Could not export cookies: %s", err)
        return out

    def import_cookies(self, cookies: dict[str, str]) -> None:
        """Restore persisted cookies onto the auth session's jar."""
        if cookies:
            self._session.cookie_jar.update_cookies(cookies, _YarlURL(MEDICOVER_LOGIN_URL))

    def is_token_valid(self) -> bool:
        return bool(
            self._access_token and self._expires_at and self._expires_at > int(time.time()) + 30
        )

    def get_headers(self) -> dict[str, str]:
        return _build_base_headers(self._device_ua, self._access_token)

    # ------------------------------------------------------------------
    # OIDC discovery
    # ------------------------------------------------------------------

    async def async_get_oidc_config(self) -> dict[str, Any]:
        if self._oidc_config:
            return self._oidc_config
        async with self._session.get(OIDC_DISCOVERY_URL) as resp:
            resp.raise_for_status()
            self._oidc_config = await resp.json(content_type=None)
        return self._oidc_config

    # ------------------------------------------------------------------
    # Login (PKCE)
    # ------------------------------------------------------------------

    async def async_login(self, username: str, password: str) -> None:
        """Perform PKCE login. Raises MfaRequired if SMS code needed."""
        _LOGGER.info("Starting PKCE login for user '%s'", username)
        oidc = await self.async_get_oidc_config()
        authorization_endpoint: str = oidc["authorization_endpoint"]

        # PKCE
        code_verifier = "".join(
            random.choice(string.ascii_uppercase + string.digits) for _ in range(50)
        )
        code_challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode()).digest())
            .decode()
            .replace("=", "")
        )
        state = uuid.uuid4().hex + uuid.uuid4().hex

        params: dict[str, Any] = {
            "client_id": "web",
            "redirect_uri": f"{MEDICOVER_MAIN_URL}/signin-oidc",
            "response_type": "code",
            "scope": "openid offline_access profile",
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "response_mode": "query",
            "ui_locales": "en",
            "app_version": "3.9.3-beta.1.8",
            "device_id": self._device_id,
            "device_name": "Chrome",
            "ts": int(time.time() * 1000),
        }

        html_headers = {**self.get_headers(), "Accept": "text/html,application/xhtml+xml"}

        # Step 1: GET authorize → follow redirect to login page
        resp = await self._session.get(
            authorization_endpoint, params=params, headers=html_headers, allow_redirects=False
        )
        resp.raise_for_status()
        next_url = resp.headers.get("Location")
        if not next_url:
            raise AuthError("Missing Location in authorize response")

        await asyncio.sleep(1)

        # Step 2: GET login page, extract CSRF
        resp = await self._session.get(next_url, headers=html_headers, allow_redirects=False)
        login_page = await resp.text()
        csrf_token = _extract_csrf(login_page)
        return_url = _extract_return_url(str(resp.url))

        # Step 3: POST credentials
        login_data = {
            "Input.ReturnUrl": return_url,
            "Input.LoginType": "FullLogin",
            "Input.Username": username,
            "Input.Password": password,
            "Input.Button": "login",
            "__RequestVerificationToken": csrf_token,
        }
        await asyncio.sleep(1)
        resp = await self._session.post(
            f"{MEDICOVER_LOGIN_URL}/Account/Login?ReturnUrl={return_url}",
            data=login_data,
            headers={
                **html_headers,
                "Content-Type": "application/x-www-form-urlencoded",
            },
            allow_redirects=False,
        )
        await self._follow_redirects(resp, code_verifier)

    async def _follow_redirects(
        self, resp: aiohttp.ClientResponse, code_verifier: str, max_hops: int = 20
    ) -> None:
        html_headers = {**self.get_headers(), "Accept": "text/html,application/xhtml+xml"}
        count = 0
        while resp.status == 302 and count < max_hops:
            count += 1
            location = resp.headers.get("Location", "")
            if not location.startswith("https://"):
                location = MEDICOVER_LOGIN_URL + location

            _LOGGER.debug("Login redirect %d → %s", count, location)

            if "mfa" in location.lower():
                _LOGGER.info("MFA challenge detected, waiting for verification code (SMS or email)")
                await self._handle_mfa_redirect(location, code_verifier)
                return

            if "code=" in location:
                parsed = urlparse(location)
                code = parse_qs(parsed.query).get("code", [None])[0]
                if code:
                    await self._exchange_code(code, code_verifier)
                    return

            await asyncio.sleep(1)
            resp = await self._session.get(location, headers=html_headers, allow_redirects=False)

        if resp.status not in (200, 302):
            raise AuthError(f"Login failed after redirects, status={resp.status}")
        if resp.status == 200:
            raise AuthError("Login failed — unexpected final page")
        raise AuthError("Too many redirects during login")

    async def _handle_mfa_redirect(self, mfa_url: str, code_verifier: str) -> None:
        html_headers = {**self.get_headers(), "Accept": "text/html,application/xhtml+xml"}
        await asyncio.sleep(1)
        resp = await self._session.get(mfa_url, headers=html_headers, allow_redirects=False)
        page = await resp.text()
        csrf_token = _extract_csrf(page)

        mfa_code_id = _extract_mfa_code_id(self._session.cookie_jar, mfa_url)
        return_url = _extract_return_url_from_url(mfa_url)

        self._mfa_context = {
            "mfa_code_id": mfa_code_id,
            "csrf": csrf_token,
            "return_url": return_url,
            "code_verifier": code_verifier,
        }
        raise MfaRequired(mfa_code_id=mfa_code_id, csrf=csrf_token, return_url=return_url)

    async def async_submit_mfa(self, mfa_code: str) -> None:
        """Submit the MFA code (SMS or email) after MfaRequired was raised."""
        if not self._mfa_context:
            raise AuthError("No MFA context — call async_login first")
        ctx = self._mfa_context
        self._mfa_context = None

        _LOGGER.debug("Submitting MFA code (length=%d)", len(mfa_code))

        mfa_data = {
            "Input.MfaCodeId": ctx["mfa_code_id"],
            "Input.ReturnUrl": ctx["return_url"],
            "Input.DeviceName": "Chrome",
            "Input.MfaCode": mfa_code,
            "Input.IsTrustedDevice": "true",
            "Input.Channel": "SMS",
            "Input.Button": "confirm",
            "__RequestVerificationToken": ctx["csrf"],
        }
        html_headers = {
            **self.get_headers(),
            "Accept": "text/html,application/xhtml+xml",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        await asyncio.sleep(1)
        resp = await self._session.post(
            f"{MEDICOVER_LOGIN_URL}/Account/Mfa?ReturnUrl={quote_plus(ctx['return_url'])}",
            data=mfa_data,
            headers=html_headers,
            allow_redirects=False,
        )
        await self._follow_redirects(resp, ctx["code_verifier"])

    async def _exchange_code(self, code: str, code_verifier: str) -> None:
        oidc = await self.async_get_oidc_config()
        token_data = {
            "client_id": "web",
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": code_verifier,
            "redirect_uri": f"{MEDICOVER_MAIN_URL}/signin-oidc",
        }
        headers = {**self.get_headers(), "Content-Type": "application/x-www-form-urlencoded"}
        async with self._session.post(
            oidc["token_endpoint"], data=token_data, headers=headers
        ) as resp:
            resp.raise_for_status()
            data = await resp.json(content_type=None)
        if "error" in data:
            raise AuthError(
                f"Token exchange failed: {data.get('error_description', data['error'])}"
            )
        _LOGGER.info("Login successful — access token obtained")
        await self._save_tokens(data)

    # ------------------------------------------------------------------
    # Refresh / logout
    # ------------------------------------------------------------------

    async def async_refresh_token(self, force: bool = False) -> None:
        """Refresh the access token (lock-guarded, double-checked).

        `force=True` refreshes even if the token still looks clock-valid — used
        by the 401 retry path where the server rejected an unexpired token.
        """
        async with self._refresh_lock:
            if not force and self.is_token_valid():
                return  # another task already refreshed while we waited
            await self._async_perform_refresh()

    async def _async_perform_refresh(self) -> None:
        _LOGGER.debug("Refreshing access token")
        if not self._refresh_token_val:
            raise AuthError("No refresh token available")
        oidc = await self.async_get_oidc_config()
        headers = _build_base_headers(self._device_ua)  # no auth header
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        refresh_data = {
            "grant_type": "refresh_token",
            "refresh_token": self._refresh_token_val,
            "scope": "openid offline_access profile",
            "client_id": "web",
        }
        async with self._session.post(
            oidc["token_endpoint"], data=refresh_data, headers=headers, allow_redirects=False
        ) as resp:
            status = resp.status
            raw = await resp.text()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as err:
            raise AuthError(
                f"Token endpoint returned non-JSON (status={status}): {raw[:200]}"
            ) from err
        if "error" in data:
            err_desc = data.get("error_description", data["error"])
            if data["error"] == "invalid_grant":
                _LOGGER.info("Refresh token rejected (invalid_grant) — will try silent re-login")
                raise InvalidGrant("Refresh token expired or revoked")
            raise AuthError(f"Token refresh failed: {err_desc}")
        _LOGGER.info("Access token refreshed successfully")
        await self._save_tokens(data)

    async def async_refresh_or_relogin(self) -> None:
        """Refresh the access token; on invalid_grant, attempt a silent re-login.

        Mirrors medichaser: a rejected refresh token is recovered by a full
        login. On a trusted device (same device_id), the login completes without
        MFA. Propagates MfaRequired (device not trusted → needs interactive
        reauth) or AuthError (bad stored password); re-raises InvalidGrant if no
        credentials are stored.
        """
        async with self._refresh_lock:
            if self.is_token_valid():
                return  # another task already refreshed while we waited
            try:
                await self._async_perform_refresh()
                return
            except InvalidGrant:
                if not (self._username and self._password):
                    raise
            _LOGGER.warning("Refresh token rejected — attempting silent re-login (trusted device)")
            await self.async_login(self._username, self._password)
            _LOGGER.info("Silent re-login succeeded — session restored without reauth")

    async def async_logout(self) -> None:
        if not self._refresh_token_val:
            return
        try:
            oidc = await self.async_get_oidc_config()
            revocation = oidc.get("revocation_endpoint")
            if not revocation:
                return
            headers = {**self.get_headers(), "Content-Type": "application/x-www-form-urlencoded"}
            await self._session.post(
                revocation,
                data={"token": self._refresh_token_val, "client_id": "web"},
                headers=headers,
            )
        except Exception:
            _LOGGER.debug("Token revocation failed (non-critical)")

    async def _save_tokens(self, data: dict[str, Any]) -> None:
        expires_in = data.get("expires_in")
        self._access_token = data.get("access_token")
        self._refresh_token_val = data.get("refresh_token")
        self._expires_at = int(time.time()) + expires_in if expires_in else None
        _LOGGER.debug("Token saved, expires_in=%ss", expires_in)
        if self._token_update_callback:
            await self._token_update_callback(self.token_data())


# ------------------------------------------------------------------
# Medicover API client
# ------------------------------------------------------------------


class MedicoverClient:
    """Queries Medicover appointments and filter APIs."""

    def __init__(self, auth: MedicoverAuth, session: aiohttp.ClientSession) -> None:
        self._auth = auth
        self._session = session

    async def _get(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        for attempt in range(2):
            headers = self._auth.get_headers()
            async with self._session.get(url, params=params, headers=headers) as resp:
                if resp.status in (401, 403) and attempt == 0:
                    await self._auth.async_refresh_token(force=True)
                    continue
                if resp.status != 200:
                    raise ApiError(f"API {resp.status} for {url}")
                return await resp.json(content_type=None)
        raise ApiError(f"Request failed after retry: {url}")

    async def async_get_personal_data(self) -> dict[str, Any]:
        url = f"{MEDICOVER_API_URL}/personal-data/api/personal"
        for attempt in range(2):
            headers = self._auth.get_headers()
            async with self._session.get(url, headers=headers) as resp:
                if resp.status in (401, 403) and attempt == 0:
                    await self._auth.async_refresh_token(force=True)
                    continue
                if resp.status != 200:
                    raise ApiError(f"personal-data returned {resp.status}")
                return await resp.json(content_type=None)
        raise ApiError("personal-data request failed")

    async def async_find_filters(
        self,
        region: int | None = None,
        specialty: list[int] | None = None,
        slot_search_type: int = 0,
    ) -> dict[str, Any]:
        url = f"{MEDICOVER_API_URL}/appointments/api/search-appointments/filters"
        params: dict[str, Any] = {"SlotSearchType": slot_search_type}
        if region is not None:
            params["RegionIds"] = region
        if specialty:
            params["SpecialtyIds"] = specialty
        return await self._get(url, params)

    async def async_find_appointments(
        self,
        region: int,
        specialty: list[int],
        clinic: int | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        language: int | None = None,
        doctor: int | None = None,
        slot_search_type: int = 0,
    ) -> list[dict[str, Any]]:
        url = f"{MEDICOVER_API_URL}/appointments/api/search-appointments/slots"
        if start_date is None:
            start_date = dt.date.today().isoformat()

        params: dict[str, Any] = {
            "RegionIds": region,
            "SpecialtyIds": specialty,
            "Page": 1,
            "PageSize": 5000,
            "StartTime": start_date,
            "SlotSearchType": slot_search_type,
            "VisitType": "Center",
        }
        if clinic:
            params["ClinicIds"] = clinic
        if language:
            params["DoctorLanguageIds"] = language
        if doctor:
            params["DoctorIds"] = doctor

        _LOGGER.debug(
            "find_appointments: region=%s, specialty=%s, clinic=%s, doctor=%s, "
            "language=%s, start=%s, end=%s",
            region,
            specialty,
            clinic,
            doctor,
            language,
            start_date,
            end_date,
        )
        data = await self._get(url, params)
        items: list[dict[str, Any]] = data.get("items", [])

        if end_date:
            items = [
                x
                for x in items
                if x.get("appointmentDate") and x["appointmentDate"][:10] <= end_date
            ]
        _LOGGER.debug("find_appointments returned %d item(s)", len(items))
        return items


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _extract_csrf(html: str) -> str:
    match = re.search(
        r'<input name="__RequestVerificationToken" type="hidden" value="([^"]+)"',
        html,
    )
    if not match:
        raise AuthError("CSRF token not found in page")
    return match.group(1)


def _extract_return_url(page_url: str) -> str:
    parsed = urlparse(page_url)
    values = parse_qs(parsed.query).get("ReturnUrl")
    if not values:
        raise AuthError("ReturnUrl not found in page URL")
    return values[0]


def _extract_return_url_from_url(url: str) -> str:
    parsed = urlparse(url)
    values = parse_qs(parsed.query).get("ReturnUrl")
    if not values:
        raise AuthError("ReturnUrl not found in MFA URL")
    return values[0]


def _extract_mfa_code_id(cookie_jar: aiohttp.CookieJar, url: str) -> str:
    cookies = cookie_jar.filter_cookies(_YarlURL(url))
    mfa_info_morsel = cookies.get("MfaInfo")
    if mfa_info_morsel is None:
        # Fallback: search all cookies
        for morsel in cookie_jar:
            if morsel.key == "MfaInfo":
                mfa_info_morsel = morsel
                break
    if mfa_info_morsel is None:
        raise AuthError("MfaInfo cookie not found")

    raw = unquote_plus(mfa_info_morsel.value)
    try:
        info = json.loads(raw)
    except json.JSONDecodeError as err:
        raise AuthError("Cannot parse MfaInfo cookie") from err
    code_id = info.get("MfaCodeId")
    if not code_id:
        raise AuthError("MfaCodeId missing from MfaInfo cookie")
    return code_id
