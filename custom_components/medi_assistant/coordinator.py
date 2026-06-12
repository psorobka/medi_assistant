from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_NOTIFY_TARGET,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    SUBENTRY_TYPE_SEARCH,
)
from .exceptions import ApiError, AuthError, InvalidGrant, MfaRequired

if TYPE_CHECKING:
    from .api import MedicoverClient

_LOGGER = logging.getLogger(__name__)

# Cap how many slots are listed in one notification (first-detection of a broad
# search can match many at once).
_MAX_NOTIFY_LINES = 15


class MedicoverCoordinator(DataUpdateCoordinator[dict[str, list[dict[str, Any]]]]):
    """One coordinator per account; fetches slots for all search subentries."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: MedicoverClient,
        filters_store: Any,
    ) -> None:
        scan_interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        super().__init__(
            hass,
            _LOGGER,
            name=f"Medicover {entry.title}",
            update_interval=timedelta(minutes=scan_interval),
        )
        self._entry = entry
        self._client = client
        self._filters_store = filters_store

    async def _async_update_data(self) -> dict[str, list[dict[str, Any]]]:
        auth = self._client._auth
        expires_at = auth._expires_at
        secs_left = (expires_at - int(time.time())) if expires_at else None
        _LOGGER.debug(
            "Poll start for '%s': token valid=%s, secs_remaining=%s",
            self._entry.title,
            auth.is_token_valid(),
            secs_left,
        )
        try:
            if not auth.is_token_valid():
                _LOGGER.debug("Token expired for '%s', refreshing before poll", self._entry.title)
                await auth.async_refresh_or_relogin()
        except (InvalidGrant, MfaRequired, AuthError) as err:
            raise ConfigEntryAuthFailed("Token expired — reauth required") from err
        except Exception as err:
            raise UpdateFailed(f"Token refresh failed: {err}") from err

        searches = [
            s for s in self._entry.subentries.values()
            if s.subentry_type == SUBENTRY_TYPE_SEARCH
        ]
        _LOGGER.info(
            "Polling %d search(es) for account '%s'",
            len(searches),
            self._entry.title,
        )

        result: dict[str, list[dict[str, Any]]] = {}
        for subentry in searches:
            data = subentry.data
            _LOGGER.debug(
                "Searching '%s': region=%s, specialty=%s, clinic=%s, doctor=%s, "
                "language=%s, date_from=%s, date_to=%s",
                subentry.title,
                data.get("region_name", data["region_id"]),
                data.get("specialty_name", data["specialty_id"]),
                data.get("clinic_name", data.get("clinic_id")),
                data.get("doctor_name", data.get("doctor_id")),
                data.get("language_id"),
                data.get("date_from"),
                data.get("date_to"),
            )
            try:
                slots = await self._client.async_find_appointments(
                    region=data["region_id"],
                    specialty=[data["specialty_id"]],
                    clinic=data.get("clinic_id"),
                    start_date=data.get("date_from"),
                    end_date=data.get("date_to"),
                    language=data.get("language_id"),
                    doctor=data.get("doctor_id"),
                    slot_search_type=data.get("slot_search_type", 0),
                )
            except InvalidGrant as err:
                raise ConfigEntryAuthFailed("Token expired") from err
            except ApiError as err:
                raise UpdateFailed(f"API error for '{subentry.title}': {err}") from err
            except Exception as err:
                raise UpdateFailed(f"Error fetching slots for '{subentry.title}': {err}") from err

            _LOGGER.info(
                "Search '%s': %d slot(s) found",
                subentry.title,
                len(slots),
            )
            if slots:
                earliest = min(
                    (s.get("appointmentDate", "") for s in slots),
                    default="",
                )
                _LOGGER.debug("Search '%s': earliest slot at %s", subentry.title, earliest)

            result[subentry.subentry_id] = slots

            # Notify on slots not seen before for this search (opt-in).
            target = data.get(CONF_NOTIFY_TARGET)
            if target:
                # Older subentries store a single string; new ones a list.
                targets = target if isinstance(target, list) else [target]
                await self._async_handle_notifications(subentry, targets, slots)

        return result

    async def _async_handle_notifications(
        self, subentry: Any, targets: list[str], slots: list[dict[str, Any]]
    ) -> None:
        """Notify each target about slots not yet seen for this search.

        The seen set is stored on disk, so a slot is announced once on first
        detection (incl. right after adding a search) and never re-announced
        across HA restarts.
        """
        seen = self._filters_store.get_seen_slots(subentry.subentry_id)
        current = {_slot_key(s) for s in slots}
        new_keys = current - seen
        if new_keys:
            new_slots = [s for s in slots if _slot_key(s) in new_keys]
            for target in targets:
                await self._async_notify(subentry, target, new_slots)
        if current != seen:
            await self._filters_store.async_set_seen_slots(subentry.subentry_id, current)

    async def _async_notify(
        self, subentry: Any, target: str, new_slots: list[dict[str, Any]]
    ) -> None:
        """Best-effort notification of new slots — never fail the poll.

        Supports both modern notify entities (notify.send_message with
        entity_id) and legacy notify services (notify.<service> with message).
        """
        lines = [_format_slot(s) for s in new_slots]
        shown = lines[:_MAX_NOTIFY_LINES]
        body = "\n".join(shown)
        if len(lines) > len(shown):
            body += f"\n…i {len(lines) - len(shown)} więcej"
        message = f"{len(new_slots)} nowy(ch) termin(ów):\n{body}"
        title = f"{self._entry.title} — {subentry.title}"
        _LOGGER.info(
            "Notifying %s: %d new slot(s) for '%s'",
            target,
            len(new_slots),
            subentry.title,
        )
        try:
            if self.hass.states.get(target) is not None:
                # Modern notify entity.
                await self.hass.services.async_call(
                    "notify",
                    "send_message",
                    {"entity_id": target, "title": title, "message": message},
                    blocking=False,
                )
            else:
                # Legacy notify service: notify.<service>.
                service = target.split(".", 1)[1] if "." in target else target
                await self.hass.services.async_call(
                    "notify",
                    service,
                    {"title": title, "message": message},
                    blocking=False,
                )
        except Exception as err:  # noqa: BLE001 — notification failure must not break polling
            _LOGGER.warning(
                "Failed to send notification to %s for '%s': %s",
                target,
                subentry.title,
                err,
            )


def _slot_key(slot: dict[str, Any]) -> str:
    """Stable, JSON-serializable identity of a slot: 'date|doctor|clinic'."""
    doctor = (slot.get("doctor") or {}).get("id")
    clinic = (slot.get("clinic") or {}).get("id")
    return f"{slot.get('appointmentDate', '')}|{doctor}|{clinic}"


def _format_slot(slot: dict[str, Any]) -> str:
    """One-line human-readable slot: 'YYYY-MM-DD HH:MM · doctor · clinic'."""
    raw = slot.get("appointmentDate")
    when = raw
    if raw:
        try:
            when = datetime.fromisoformat(raw).strftime("%Y-%m-%d %H:%M")
        except ValueError:
            when = raw
    parts = [
        when,
        (slot.get("doctor") or {}).get("name"),
        (slot.get("clinic") or {}).get("name"),
    ]
    return " · ".join(p for p in parts if p)
