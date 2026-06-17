from __future__ import annotations

import datetime
import logging
from typing import TYPE_CHECKING, Any

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentry,
    ConfigSubentryFlow,
    OptionsFlow,
)
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    DateSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import MedicoverAuth, MedicoverClient, _new_device_id, _DEFAULT_UA
from .const import (
    CONF_MFA_CODE,
    CONF_SCAN_INTERVAL,
    CONF_REGION_ID,
    CONF_REGION_NAME,
    CONF_SPECIALTY_ID,
    CONF_SPECIALTY_NAME,
    CONF_CLINIC_ID,
    CONF_CLINIC_NAME,
    CONF_DOCTOR_ID,
    CONF_DOCTOR_NAME,
    CONF_LANGUAGE_ID,
    CONF_DATE_FROM,
    CONF_DATE_TO,
    CONF_SLOT_SEARCH_TYPE,
    CONF_NOTIFY_TARGET,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_SLOT_SEARCH_TYPE,
    DOMAIN,
    LANGUAGES,
    SUBENTRY_TYPE_SEARCH,
)
from .exceptions import AuthError, MfaRequired

if TYPE_CHECKING:
    pass

_LOGGER = logging.getLogger(__name__)


def _personal_full_name(personal: dict[str, Any]) -> str:
    first = personal.get("firstName") or personal.get("name", "")
    last = personal.get("lastName") or personal.get("surname", "")
    return f"{first} {last}".strip()


def _personal_unique_id(personal: dict[str, Any], username: str) -> str:
    pid = personal.get("id") or personal.get("patientId") or personal.get("pesel")
    return str(pid) if pid else username.lower()


def _subentry_title(data: dict[str, Any]) -> str:
    parts: list[str] = []
    if name := data.get(CONF_SPECIALTY_NAME):
        parts.append(name)
    if name := data.get(CONF_REGION_NAME):
        parts.append(name)
    if date := data.get(CONF_DATE_FROM):
        try:
            d = datetime.date.fromisoformat(date)
            months = ["sty", "lut", "mar", "kwi", "maj", "cze",
                      "lip", "sie", "wrz", "paź", "lis", "gru"]
            parts.append(f"od {d.day} {months[d.month - 1]}")
        except ValueError:
            pass
    return " · ".join(parts) if parts else "Szukajka"


# ---------------------------------------------------------------------------
# Main config flow
# ---------------------------------------------------------------------------


class MedicoverConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._auth: MedicoverAuth | None = None
        self._auth_session: aiohttp.ClientSession | None = None
        self._username: str = ""
        self._password: str = ""

    @classmethod
    def async_get_supported_subentry_types(cls, config_entry: ConfigEntry) -> dict[str, type[ConfigSubentryFlow]]:
        return {SUBENTRY_TYPE_SEARCH: SearchSubentryFlowHandler}

    @callback
    def async_remove(self) -> None:
        """Close the auth session when the flow is removed (cancelled or completed)."""
        if self._auth_session and not self._auth_session.closed:
            self.hass.async_create_task(self._auth_session.close())

    # ------------------------------------------------------------------
    # Step: user (login + password)
    # ------------------------------------------------------------------

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            username: str = user_input[CONF_USERNAME]
            password: str = user_input[CONF_PASSWORD]
            self._username = username
            self._password = password

            await self._init_auth_session()
            assert self._auth is not None

            try:
                await self._auth.async_login(username, password)
            except MfaRequired:
                return await self.async_step_mfa()
            except AuthError:
                errors["base"] = "invalid_auth"
            except Exception:
                _LOGGER.exception("Unexpected error during login")
                errors["base"] = "cannot_connect"
            else:
                return await self._async_finish_login()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_USERNAME): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.EMAIL, autocomplete="username")
                    ),
                    vol.Required(CONF_PASSWORD): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD, autocomplete="current-password")
                    ),
                }
            ),
            errors=errors,
        )

    # ------------------------------------------------------------------
    # Step: mfa
    # ------------------------------------------------------------------

    async def async_step_mfa(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        assert self._auth is not None

        if user_input is not None:
            mfa_code: str = user_input[CONF_MFA_CODE]
            try:
                await self._auth.async_submit_mfa(mfa_code)
            except AuthError:
                errors["base"] = "invalid_mfa"
            except Exception:
                _LOGGER.exception("Unexpected error during MFA")
                errors["base"] = "cannot_connect"
            else:
                return await self._async_finish_login()

        return self.async_show_form(
            step_id="mfa",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_MFA_CODE): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.TEXT)
                    ),
                }
            ),
            errors=errors,
        )

    # ------------------------------------------------------------------
    # Reauth
    # ------------------------------------------------------------------

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        reauth_entry = self._get_reauth_entry()

        if user_input is not None:
            username: str = user_input[CONF_USERNAME]
            password: str = user_input[CONF_PASSWORD]
            self._username = username
            self._password = password

            await self._init_auth_session()
            assert self._auth is not None

            try:
                await self._auth.async_login(username, password)
            except MfaRequired:
                return await self.async_step_reauth_mfa()
            except AuthError:
                errors["base"] = "invalid_auth"
            except Exception:
                _LOGGER.exception("Reauth error")
                errors["base"] = "cannot_connect"
            else:
                self.hass.config_entries.async_update_entry(
                    reauth_entry,
                    data={
                        **reauth_entry.data,
                        **self._auth.token_data(),
                        CONF_USERNAME: username,
                        CONF_PASSWORD: password,
                    },
                )
                await self.hass.config_entries.async_reload(reauth_entry.entry_id)
                return self.async_abort(reason="reauth_successful")

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_USERNAME, default=reauth_entry.data.get(CONF_USERNAME, "")): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.EMAIL)
                    ),
                    vol.Required(CONF_PASSWORD): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_reauth_mfa(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        assert self._auth is not None
        reauth_entry = self._get_reauth_entry()

        if user_input is not None:
            try:
                await self._auth.async_submit_mfa(user_input[CONF_MFA_CODE])
            except AuthError:
                errors["base"] = "invalid_mfa"
            except Exception:
                _LOGGER.exception("Reauth MFA error")
                errors["base"] = "cannot_connect"
            else:
                self.hass.config_entries.async_update_entry(
                    reauth_entry,
                    data={
                        **reauth_entry.data,
                        **self._auth.token_data(),
                        CONF_USERNAME: self._username,
                        CONF_PASSWORD: self._password,
                    },
                )
                await self.hass.config_entries.async_reload(reauth_entry.entry_id)
                return self.async_abort(reason="reauth_successful")

        return self.async_show_form(
            step_id="reauth_mfa",
            data_schema=vol.Schema({vol.Required(CONF_MFA_CODE): TextSelector()}),
            errors=errors,
        )

    # ------------------------------------------------------------------
    # Options flow
    # ------------------------------------------------------------------

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return MedicoverOptionsFlowHandler(config_entry)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _init_auth_session(self) -> None:
        if self._auth_session and not self._auth_session.closed:
            await self._auth_session.close()
        self._auth_session = aiohttp.ClientSession(
            cookie_jar=aiohttp.CookieJar(unsafe=True)
        )
        device_id = _new_device_id()
        self._auth = MedicoverAuth(self._auth_session, device_id=device_id, device_ua=_DEFAULT_UA)

    async def _async_finish_login(self) -> ConfigFlowResult:
        assert self._auth is not None

        # Fetch personal data to get name + unique_id (use shared HA session)
        api_session = async_get_clientsession(self.hass)
        client = MedicoverClient(self._auth, api_session)
        try:
            personal = await client.async_get_personal_data()
        except Exception:
            personal = {}

        full_name = _personal_full_name(personal) or self._username
        unique_id = _personal_unique_id(personal, self._username)

        await self.async_set_unique_id(unique_id)
        self._abort_if_unique_id_configured()

        data = {
            **self._auth.token_data(),
            CONF_USERNAME: self._username,
            CONF_PASSWORD: self._password,
        }

        if self._auth_session and not self._auth_session.closed:
            await self._auth_session.close()

        _LOGGER.info(
            "New Medicover account configured: '%s' (unique_id=%s)",
            full_name,
            unique_id,
        )
        return self.async_create_entry(title=full_name, data=data)


# ---------------------------------------------------------------------------
# Options flow
# ---------------------------------------------------------------------------


class MedicoverOptionsFlowHandler(OptionsFlow):
    def __init__(self, config_entry: ConfigEntry) -> None:
        self._entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SCAN_INTERVAL,
                        default=self._entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                    ): NumberSelector(
                        NumberSelectorConfig(min=5, max=120, step=5, mode=NumberSelectorMode.BOX)
                    ),
                }
            ),
        )


# ---------------------------------------------------------------------------
# Search subentry flow
# ---------------------------------------------------------------------------


class SearchSubentryFlowHandler(ConfigSubentryFlow):
    """Region-first creator/editor for appointment searches.

    Specialties (and clinics/doctors) are region-dependent in Medicover — the
    global /filters call returns only a small subset (~27 vs 124 for a region) —
    so we ask for the region first, then load specialties scoped to that region.
    Create and reconfigure share the same three steps; reconfigure pre-fills
    defaults from the existing subentry and ends with update-and-abort.
    """

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._reconfigure: ConfigSubentry | None = None
        self._existing: dict[str, Any] = {}

    # ---- Reconfigure entry point reuses the create steps ----

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        self._reconfigure = self._get_reconfigure_subentry()
        self._existing = dict(self._reconfigure.data)
        return await self.async_step_user()

    # ---- Step 1: region ----

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_entry()
        runtime_data = getattr(entry, "runtime_data", None)

        regions: list[dict[str, Any]] = []
        if runtime_data is not None:
            regions = runtime_data.filters_store.regions
            if not regions:
                try:
                    await runtime_data.filters_store.async_refresh(runtime_data.client)
                    regions = runtime_data.filters_store.regions
                except Exception:
                    _LOGGER.exception("Could not load regions")

        if not regions:
            return self.async_abort(reason="cannot_load_filters")

        if user_input is not None:
            region_id = int(user_input[CONF_REGION_ID])
            self._data[CONF_REGION_ID] = region_id
            self._data[CONF_REGION_NAME] = _find_name(regions, region_id)
            # Load specialties scoped to THIS region (global list is incomplete).
            if runtime_data is not None:
                try:
                    await runtime_data.filters_store.async_refresh_specialties(
                        runtime_data.client, region_id
                    )
                except Exception:
                    _LOGGER.exception("Could not load specialties for region")
            return await self.async_step_specialty()

        # Default the region: existing one when reconfiguring, otherwise the
        # region of the most recently added search (convenient for adding several).
        default_source = self._existing
        if not self._reconfigure and CONF_REGION_ID not in default_source:
            last_region = _last_search_region(entry)
            if last_region is not None:
                default_source = {CONF_REGION_ID: last_region}

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {_req_key(CONF_REGION_ID, default_source, valid=regions): _id_value_selector(regions)}
            ),
        )

    # ---- Step 2: specialty (scoped to the chosen region) ----

    async def async_step_specialty(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_entry()
        runtime_data = getattr(entry, "runtime_data", None)
        region_id: int = self._data[CONF_REGION_ID]

        specialties: list[dict[str, Any]] = []
        if runtime_data is not None:
            specialties = runtime_data.filters_store.get_specialties_for_region(region_id)

        if not specialties:
            return self.async_abort(reason="cannot_load_filters")

        if user_input is not None:
            specialty_id = int(user_input[CONF_SPECIALTY_ID])
            self._data[CONF_SPECIALTY_ID] = specialty_id
            self._data[CONF_SPECIALTY_NAME] = _find_name(specialties, specialty_id)
            # Pre-fetch clinics/doctors for the details step (non-critical).
            if runtime_data is not None:
                try:
                    await runtime_data.filters_store.async_refresh_clinics_doctors(
                        runtime_data.client, region_id, [specialty_id]
                    )
                except Exception:
                    _LOGGER.debug("Could not pre-fetch clinics/doctors")
            return await self.async_step_details()

        # Default the existing specialty only if it's offered in this region.
        key = _req_key(CONF_SPECIALTY_ID, self._existing, valid=specialties)
        return self.async_show_form(
            step_id="specialty",
            data_schema=vol.Schema({key: _id_value_selector(specialties)}),
        )

    # ---- Step 3: optional details → create or update ----

    async def async_step_details(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_entry()
        runtime_data = getattr(entry, "runtime_data", None)
        region_id: int = self._data[CONF_REGION_ID]
        specialty_id: int = self._data[CONF_SPECIALTY_ID]

        clinics: list[dict[str, Any]] = []
        doctors: list[dict[str, Any]] = []
        if runtime_data is not None:
            clinics = runtime_data.filters_store.get_clinics(region_id, [specialty_id])
            doctors = runtime_data.filters_store.get_doctors(region_id, [specialty_id])

        if user_input is not None:
            # Start from existing (reconfigure keeps unrelated keys), apply new.
            data = dict(self._existing)
            data.update(self._data)
            data[CONF_SLOT_SEARCH_TYPE] = DEFAULT_SLOT_SEARCH_TYPE

            _apply_optional_id(data, user_input, CONF_CLINIC_ID, CONF_CLINIC_NAME, clinics)
            _apply_optional_id(data, user_input, CONF_DOCTOR_ID, CONF_DOCTOR_NAME, doctors)
            data[CONF_LANGUAGE_ID] = (
                int(user_input[CONF_LANGUAGE_ID]) if user_input.get(CONF_LANGUAGE_ID) else None
            )
            data[CONF_DATE_FROM] = user_input.get(CONF_DATE_FROM) or None
            data[CONF_DATE_TO] = user_input.get(CONF_DATE_TO) or None
            data[CONF_NOTIFY_TARGET] = user_input.get(CONF_NOTIFY_TARGET) or None
            data = {k: v for k, v in data.items() if v is not None}

            title = _subentry_title(data)
            if self._reconfigure is not None:
                return self.async_update_and_abort(
                    entry=entry, subentry=self._reconfigure, title=title, data=data
                )
            _LOGGER.info(
                "New search configured: '%s' (region=%s, specialty=%s)",
                title,
                data.get(CONF_REGION_NAME),
                data.get(CONF_SPECIALTY_NAME),
            )
            return self.async_create_entry(title=title, data=data)

        ex = self._existing
        schema_dict: dict[Any, Any] = {}
        if clinics:
            schema_dict[_opt_key(CONF_CLINIC_ID, ex, valid=clinics)] = _id_value_selector(clinics)
        if doctors:
            schema_dict[_opt_key(CONF_DOCTOR_ID, ex, valid=doctors)] = _id_value_selector(doctors)
        schema_dict[_opt_key(CONF_LANGUAGE_ID, ex, stringify=True)] = SelectSelector(
            SelectSelectorConfig(
                options=[SelectOptionDict(value=str(k), label=v) for k, v in LANGUAGES.items()],
                mode=SelectSelectorMode.DROPDOWN,
            )
        )
        schema_dict[_opt_key(CONF_DATE_FROM, ex)] = DateSelector()
        schema_dict[_opt_key(CONF_DATE_TO, ex)] = DateSelector()
        # Multi-select: a search can notify several targets at once. Existing
        # single-string values (older subentries) are normalised to a list.
        notify_default = ex.get(CONF_NOTIFY_TARGET)
        if isinstance(notify_default, str):
            notify_default = [notify_default]
        notify_key = (
            vol.Optional(CONF_NOTIFY_TARGET, default=notify_default)
            if notify_default
            else vol.Optional(CONF_NOTIFY_TARGET)
        )
        schema_dict[notify_key] = SelectSelector(
            SelectSelectorConfig(
                options=_notify_target_options(self.hass),
                mode=SelectSelectorMode.DROPDOWN,
                custom_value=True,
                sort=True,
                multiple=True,
            )
        )

        return self.async_show_form(step_id="details", data_schema=vol.Schema(schema_dict))


# ---------------------------------------------------------------------------
# Subentry flow helpers
# ---------------------------------------------------------------------------


def _last_search_region(entry: ConfigEntry) -> int | None:
    """Region of the most recently added search (subentries keep insertion order)."""
    searches = [
        s for s in entry.subentries.values() if s.subentry_type == SUBENTRY_TYPE_SEARCH
    ]
    return searches[-1].data.get(CONF_REGION_ID) if searches else None


def _notify_target_options(hass) -> list[SelectOptionDict]:
    """All notify targets: modern notify.* entities AND legacy notify.* services."""
    targets: set[str] = set(hass.states.async_entity_ids("notify"))
    for service in hass.services.async_services().get("notify", {}):
        if service != "send_message":  # the generic entity service, not a target
            targets.add(f"notify.{service}")
    return [SelectOptionDict(value=t, label=t) for t in sorted(targets)]


def _id_value_selector(items: list[dict[str, Any]]) -> SelectSelector:
    """Dropdown built from a list of {id, value} filter items."""
    return SelectSelector(
        SelectSelectorConfig(
            options=[SelectOptionDict(value=str(i["id"]), label=i["value"]) for i in items],
            mode=SelectSelectorMode.DROPDOWN,
            sort=True,
        )
    )


def _req_key(key: str, existing: dict[str, Any], valid: list[dict[str, Any]] | None = None):
    """Required vol key, defaulting to the existing value when present (and valid)."""
    val = existing.get(key)
    if val is not None and (valid is None or any(str(i.get("id")) == str(val) for i in valid)):
        return vol.Required(key, default=str(val))
    return vol.Required(key)


def _opt_key(
    key: str,
    existing: dict[str, Any],
    valid: list[dict[str, Any]] | None = None,
    stringify: bool = False,
):
    """Optional vol key, defaulting to the existing value when present (and valid)."""
    val = existing.get(key)
    if val is None:
        return vol.Optional(key)
    if valid is not None and not any(str(i.get("id")) == str(val) for i in valid):
        return vol.Optional(key)  # stale id not offered for this region/specialty
    return vol.Optional(key, default=str(val) if stringify or valid is not None else val)


def _apply_optional_id(
    data: dict[str, Any],
    user_input: dict[str, Any],
    id_key: str,
    name_key: str,
    items: list[dict[str, Any]],
) -> None:
    """Set or clear an optional id+name pair from user input."""
    raw = user_input.get(id_key)
    if raw:
        data[id_key] = int(raw)
        data[name_key] = _find_name(items, int(raw))
    else:
        data.pop(id_key, None)
        data.pop(name_key, None)


def _find_name(items: list[dict[str, Any]], item_id: int) -> str:
    str_id = str(item_id)
    for item in items:
        if str(item.get("id", "")) == str_id:
            return str(item.get("value", item_id))
    _LOGGER.debug("_find_name: id=%s not found in %d items", item_id, len(items))
    return str(item_id)
