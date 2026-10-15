"""One-use offers for booking the exact appointment shown in a notification."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from time import monotonic
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from aiohttp import ClientError
from homeassistant.core import HomeAssistant

from .api import MedicoverClient
from .cancellation import CancellationManager
from .exceptions import ApiError, AuthError, BookingError, InvalidGrant, MfaRequired

if TYPE_CHECKING:
    from . import MedicoverConfigEntry

OFFER_LIFETIME = 30 * 60

MESSAGES = {
    "booked": "Wizyta została zarezerwowana.",
    "expired": "Przycisk wygasł. Poczekaj na nowe powiadomienie.",
    "unavailable": "Ten termin nie jest już dostępny.",
    "unsupported": "Ten rodzaj wizyty nie obsługuje rezerwacji z powiadomienia.",
    "payment_required": "Wizyta wymaga płatności i nie została zarezerwowana.",
    "confirmation_required": ("Wizyta wymaga dodatkowych potwierdzeń i nie została zarezerwowana."),
    "auth_required": "Sesja Medicover wygasła. Zaloguj konto ponownie w HA.",
    "limit_reached": "Medicover odrzucił rezerwację z powodu limitu wizyt.",
    "conflict": "Termin koliduje z inną wizytą.",
    "rate_limit": "Medicover ograniczył liczbę żądań. Spróbuj później.",
    "rejected": "Medicover odrzucił rezerwację.",
    "preflight_failed": (
        "Nie udało się sprawdzić warunków wizyty. Rezerwacja nie została wysłana."
    ),
    "unknown": ("Wynik rezerwacji jest nieznany. Sprawdź wizyty w Medicover przed kolejną próbą."),
}


def result(status: str) -> dict[str, Any]:
    """Return a response suitable for a user-owned notification automation."""
    return {
        "success": status == "booked",
        "status": status,
        "message": MESSAGES[status],
    }


def slot_identity(slot: dict[str, Any]) -> tuple[str, ...]:
    """Match clinical identity, never the position of a slot in a refreshed list."""
    return (
        str(slot.get("appointmentDate", "")),
        *(str((slot.get(key) or {}).get("id", "")) for key in ("clinic", "doctor", "specialty")),
        str(slot.get("visitType", "")),
        str(slot.get("serviceId", "")),
        str(slot.get("visitVariant", "Standard")),
        str(slot.get("isOverbooking", False)),
    )


@dataclass
class Offer:
    subentry_id: str
    identity: tuple[str, ...]
    expires: float
    outcome: dict[str, Any] | None = None


class BookingManager:
    """Keep account-scoped offers and serialize writes, including double taps."""

    def __init__(
        self, hass: HomeAssistant, client: MedicoverClient, entry: MedicoverConfigEntry
    ) -> None:
        self._hass = hass
        self._client = client
        self._entry = entry
        self.cancellation = CancellationManager(hass, client, entry)
        self._offers: dict[str, Offer] = {}
        self._lock = asyncio.Lock()

    def owns(self, booking_id: str) -> bool:
        return booking_id in self._offers

    def owns_search(self, booking_id: str, subentry_id: str) -> bool:
        offer = self._offers.get(booking_id)
        return offer is not None and offer.subentry_id == subentry_id

    def public_slots(self, subentry_id: str, slots: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Expose random handles, never the private API's booking strings."""
        now = monotonic()
        self._offers = {key: value for key, value in self._offers.items() if value.expires > now}
        published = []
        for slot in slots[:20]:
            public = {key: value for key, value in slot.items() if key != "bookingString"}
            if slot.get("bookingString"):
                identity = slot_identity(slot)
                booking_id = next(
                    (
                        key
                        for key, offer in self._offers.items()
                        if offer.subentry_id == subentry_id and offer.identity == identity
                    ),
                    None,
                )
                if booking_id is None:
                    booking_id = uuid4().hex
                    self._offers[booking_id] = Offer(subentry_id, identity, now + OFFER_LIFETIME)
                public["booking_id"] = booking_id
            published.append(public)
        return published

    async def async_book(self, booking_id: str) -> dict[str, Any]:
        async with self._lock:
            offer = self._offers.get(booking_id)
            if offer is None or offer.expires <= monotonic():
                return result("expired")
            if offer.outcome is not None:
                return offer.outcome
            for previous in self._offers.values():
                if (
                    previous.identity == offer.identity
                    and previous.outcome
                    and previous.outcome["status"] in ("booked", "unknown")
                ):
                    return previous.outcome
            subentry = self._entry.subentries.get(offer.subentry_id)
            if subentry is None:
                return result("expired")
            try:
                data = subentry.data
                slots = await self._client.async_find_appointments(
                    region=int(data["region_id"]),
                    specialty=[int(data["specialty_id"])],
                    clinic=int(data["clinic_id"]) if data.get("clinic_id") else None,
                    doctor=int(data["doctor_id"]) if data.get("doctor_id") else None,
                    language=int(data["language_id"]) if data.get("language_id") else None,
                    start_date=offer.identity[0][:10],
                    end_date=offer.identity[0][:10],
                    slot_search_type=int(data.get("slot_search_type", 0)),
                )
                matches = [slot for slot in slots if slot_identity(slot) == offer.identity]
                if len(matches) != 1:
                    offer.outcome = result("unavailable")
                    return offer.outcome
                slot = matches[0]
                await self._client.async_check_booking(slot)
            except (InvalidGrant, MfaRequired, AuthError):
                self._entry.async_start_reauth(self._hass)
                return result("auth_required")
            except BookingError as exc:
                return result("preflight_failed" if exc.code == "unknown" else exc.code)
            except (
                ApiError,
                ClientError,
                TimeoutError,
                ValueError,
                KeyError,
            ):
                return result("preflight_failed")

            # An interrupted/ambiguous POST must never be replayed by a double tap.
            offer.outcome = result("unknown")
            try:
                await self._client.async_book_appointment(slot["bookingString"])
            except (InvalidGrant, MfaRequired, AuthError):
                offer.outcome = result("auth_required")
                self._entry.async_start_reauth(self._hass)
            except BookingError as exc:
                offer.outcome = result(exc.code)
            except (ApiError, ClientError, TimeoutError, ValueError):
                pass
            else:
                offer.outcome = result("booked")
            return offer.outcome
