"""Account-scoped handles for cancelling a specific planned appointment."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from time import monotonic
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from aiohttp import ClientError
from homeassistant.core import HomeAssistant

from .api import MedicoverClient
from .exceptions import ApiError, AuthError, BookingError, InvalidGrant, MfaRequired

if TYPE_CHECKING:
    from . import MedicoverConfigEntry

MESSAGES = {
    "cancelled": "Wizyta została odwołana.",
    "expired": "Identyfikator wygasł. Ponownie pobierz zarezerwowane wizyty.",
    "unavailable": "Ta wizyta nie znajduje się już na liście zaplanowanych wizyt.",
    "not_allowed": "Medicover nie pozwala odwołać tej wizyty przez API.",
    "confirmation_required": "Medicover oznaczył wizytę jako późne odwołanie. Sprawdź warunki i zaznacz potwierdzenie późnego odwołania. Wizyta nie została odwołana.",
    "auth_required": "Sesja Medicover wygasła. Zaloguj konto ponownie w HA.",
    "read_failed": "Nie udało się pobrać aktualnej listy wizyt. Odwołanie nie zostało wysłane.",
    "rejected": "Medicover odrzucił odwołanie wizyty.",
    "rate_limit": "Medicover ograniczył liczbę żądań. Spróbuj później.",
    "unknown": "Wynik odwołania jest nieznany. Sprawdź wizyty w Medicover przed kolejną próbą.",
}


def result(status: str) -> dict[str, Any]:
    return {"success": status == "cancelled", "status": status, "message": MESSAGES[status]}


def identity(slot: dict[str, Any]) -> tuple[str, ...]:
    return (
        str(slot.get("id", "")),
        str(slot.get("date", "")),
        *(str((slot.get(key) or {}).get("id", "")) for key in ("doctor", "clinic", "specialty")),
        str(slot.get("visitType", "")),
    )


@dataclass
class Cancellation:
    identity: tuple[str, ...]
    expires: float
    outcome: dict[str, Any] | None = None


class CancellationManager:
    def __init__(
        self, hass: HomeAssistant, client: MedicoverClient, entry: MedicoverConfigEntry
    ) -> None:
        self._hass = hass
        self._client = client
        self._entry = entry
        self._handles: dict[str, Cancellation] = {}
        self._lock = asyncio.Lock()

    def owns(self, cancellation_id: str) -> bool:
        return cancellation_id in self._handles

    async def _read(self) -> list[dict[str, Any]]:
        return await self._client.async_get_planned_appointments()

    def _reauth(self) -> dict[str, Any]:
        self._entry.async_start_reauth(self._hass)
        return result("auth_required")

    async def async_list(self) -> dict[str, Any]:
        async with self._lock:
            try:
                slots = await self._read()
            except (InvalidGrant, MfaRequired, AuthError):
                return self._reauth() | {"appointments": []}
            except (ApiError, ClientError, TimeoutError, ValueError, KeyError):
                return result("read_failed") | {"appointments": []}
            now = monotonic()
            self._handles = {key: item for key, item in self._handles.items() if item.expires > now}
            public = []
            for slot in sorted(slots, key=lambda item: str(item.get("date", ""))):
                if not slot.get("id") or not slot.get("date"):
                    continue
                key = next(
                    (key for key, item in self._handles.items() if item.identity == identity(slot)),
                    None,
                )
                if key is None:
                    key = uuid4().hex
                    self._handles[key] = Cancellation(identity(slot), now + 30 * 60)
                attributes = slot.get("attributes") or {}
                public.append(
                    {
                        "cancellation_id": key,
                        "date": slot["date"],
                        **{
                            field: (slot.get(field) or {}).get("name", "")
                            for field in ("doctor", "clinic", "specialty")
                        },
                        "can_cancel": attributes.get("canCancel") is True,
                        "late_cancellation": attributes.get("isLateCancellation") is True,
                    }
                )
            return {
                "success": True,
                "status": "listed",
                "appointments": public,
                "message": "Wybierz cancellation_id wizyty do akcji Odwołaj wizytę."
                if public
                else "Brak zaplanowanych wizyt.",
            }

    async def async_cancel(
        self, cancellation_id: str, *, confirm_late_cancellation: bool = False
    ) -> dict[str, Any]:
        async with self._lock:
            handle = self._handles.get(cancellation_id)
            if handle is None or handle.expires <= monotonic():
                return result("expired")
            if handle.outcome is not None:
                return handle.outcome
            try:
                slots = await self._read()
            except (InvalidGrant, MfaRequired, AuthError):
                return self._reauth()
            except (ApiError, ClientError, TimeoutError, ValueError, KeyError):
                return result("read_failed")
            matches = [slot for slot in slots if identity(slot) == handle.identity]
            if len(matches) != 1:
                return result("unavailable")
            attributes = matches[0].get("attributes") or {}
            if attributes.get("canCancel") is not True:
                return result("not_allowed")
            if attributes.get("isLateCancellation") is not False and not confirm_late_cancellation:
                return result("confirmation_required")
            # Keep an ambiguous write terminal so a double click cannot replay it.
            handle.outcome = result("unknown")
            try:
                await self._client.async_cancel_appointment(matches[0]["id"])
            except (InvalidGrant, MfaRequired, AuthError):
                handle.outcome = self._reauth()
            except BookingError as exc:
                handle.outcome = result(exc.code if exc.code in MESSAGES else "rejected")
            except (ApiError, ClientError, TimeoutError, ValueError):
                pass
            else:
                handle.outcome = result("cancelled")
            return handle.outcome
