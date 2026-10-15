"""Booking HTTP protocol; all requests intercepted, no live Medicover access."""

import base64
import copy
import json
from pathlib import Path

import aiohttp
import pytest
from aioresponses import aioresponses

from custom_components.medi_assistant.api import MedicoverAuth, MedicoverClient
from custom_components.medi_assistant.const import MEDICOVER_API_URL
from custom_components.medi_assistant.exceptions import BookingError

BOOK_URL = f"{MEDICOVER_API_URL}/appointments/api/v2/search-appointments/book-appointment"
PRICE_URL = f"{MEDICOVER_API_URL}/payment-gateway/api/v2/visit-prices"


@pytest.fixture
async def booking_http():
    claims = (
        base64.urlsafe_b64encode(json.dumps({"role": "FullAccess"}).encode()).decode().rstrip("=")
    )
    async with aiohttp.ClientSession() as session:
        auth = MedicoverAuth(session, device_id="synthetic-device", device_ua="SyntheticUA")
        auth.load_tokens(
            {
                "access_token": f"synthetic.{claims}.signature",
                "refresh_token": "synthetic-refresh",
                "expires_at": 9999999999,
            }
        )
        auth._oidc_config = {"token_endpoint": "https://example.test/token"}
        yield MedicoverClient(auth, session)


@pytest.fixture
def slot():
    return json.loads((Path(__file__).parent / "fixtures/booking_slot.json").read_text())


async def test_booking_payload_and_confirmed_response(booking_http):
    with aioresponses() as mocked:
        mocked.post(BOOK_URL, payload={"appointmentId": 123})
        await booking_http.async_book_appointment("synthetic-handle")
        request = next(iter(mocked.requests.values()))[0]
        assert request.kwargs["json"] == {
            "bookingString": "synthetic-handle",
            "metadata": {"appointmentSource": "Direct"},
        }


@pytest.mark.parametrize(
    "status,title,code",
    [
        (409, "SLOT_NO_LONGER_AVAILABLE", "unavailable"),
        (400, "error.dayLimitReached", "limit_reached"),
        (400, "error.startTimeBiggerEndTime", "conflict"),
        (429, "unused", "rate_limit"),
        (403, "private-data-must-not-leak", "rejected"),
        (500, "private-data-must-not-leak", "unknown"),
    ],
)
async def test_booking_errors_sanitized_and_not_retried(booking_http, status, title, code, caplog):
    with aioresponses() as mocked:
        mocked.post(BOOK_URL, status=status, payload={"title": title})
        with pytest.raises(BookingError) as error:
            await booking_http.async_book_appointment("synthetic-handle")
        assert error.value.code == code
        assert sum(map(len, mocked.requests.values())) == 1
    assert "private-data-must-not-leak" not in str(error.value) + caplog.text


async def test_401_refreshes_before_one_retry(booking_http):
    with aioresponses() as mocked:
        mocked.post(BOOK_URL, status=401)
        mocked.post(
            "https://example.test/token",
            payload={
                "access_token": "synthetic-new",
                "refresh_token": "synthetic-new-refresh",
                "expires_in": 180,
            },
        )
        mocked.post(BOOK_URL, payload={"appointmentId": 123})
        await booking_http.async_book_appointment("synthetic-handle")
        assert (
            sum(len(calls) for (method, _), calls in mocked.requests.items() if method == "POST")
            == 3
        )


@pytest.mark.parametrize("payload", [{}, {"status": "Error"}, []])
async def test_200_without_confirmation_is_unknown(booking_http, payload):
    with aioresponses() as mocked:
        mocked.post(BOOK_URL, payload=payload)
        with pytest.raises(BookingError, match="unknown"):
            await booking_http.async_book_appointment("synthetic-handle")


@pytest.mark.parametrize(
    "price,code",
    [
        ([{"paymentAvailable": True, "status": "Paid"}], "payment_required"),
        ([{"paymentAvailable": False, "status": "Error"}], "preflight_failed"),
        ([], "preflight_failed"),
    ],
)
async def test_price_blocks_booking(booking_http, slot, price, code):
    with aioresponses() as mocked:
        mocked.post(PRICE_URL, payload=price)
        with pytest.raises(BookingError, match=code):
            await booking_http.async_check_booking(slot)
        assert all(str(url) != BOOK_URL for _, url in mocked.requests)


@pytest.mark.parametrize("confirmation", [False, True])
async def test_preflight_with_real_http_criteria(booking_http, slot, confirmation, freezer):
    from urllib.parse import urlencode

    freezer.move_to("2026-10-15")

    params = {
        "clinicId": "11",
        "specialtyId": "10",
        "doctorId": "22",
        "date": slot["appointmentDate"],
        "isChild": "false",
        "patientAge": "26",
        "gender": "M",
        "userRole": "FullAccess",
        "visitType": "Center",
        "isOverbooking": "false",
        "hasPreparation": "false",
    }
    with aioresponses() as mocked:
        mocked.post(PRICE_URL, payload=[{"paymentAvailable": False, "status": "Free"}])
        mocked.get(
            f"{MEDICOVER_API_URL}/personal-data/api/personal",
            payload={"birthDate": "2000-01-01", "gender": "M"},
        )
        mocked.get(
            f"{MEDICOVER_API_URL}/appointments/api/v2/appointment-preparation?DoctorId=22&SpecialtyId=10",
            payload={"text": None},
        )
        for context in (
            "regulations-visit-confirm",
            "checkbox-visit-confirm",
            "overbooking-visit-confirm",
        ):
            mocked.get(
                f"{MEDICOVER_API_URL}/information-dispatcher/api/v2/message-groups/messages-for-context/{context}?{urlencode(params)}",
                payload=[{"text": "Syntetyczne potwierdzenie"}] if confirmation else [],
            )
        if confirmation:
            with pytest.raises(BookingError, match="confirmation_required"):
                await booking_http.async_check_booking(slot)
        else:
            await booking_http.async_check_booking(slot)


@pytest.mark.parametrize(
    "change",
    [{"visitType": "Phone"}, {"isOverbooking": True}, {"serviceId": "procedure"}],
)
async def test_unsupported_slot_never_calls_api(booking_http, slot, change):
    with aioresponses() as mocked:
        with pytest.raises(BookingError, match="unsupported"):
            await booking_http.async_check_booking(copy.deepcopy(slot) | change)
        assert not mocked.requests
