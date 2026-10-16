"""Shared search data validation for the UI and service actions."""

from __future__ import annotations

import datetime
from typing import Any

from .const import (
    CONF_SPECIALTY_NAME,
    CONF_REGION_NAME,
    CONF_DATE_FROM,
    LANGUAGES,
)


def _subentry_title(data: dict[str, Any]) -> str:
    parts: list[str] = []
    if name := data.get(CONF_SPECIALTY_NAME):
        parts.append(name)
    if name := data.get(CONF_REGION_NAME):
        parts.append(name)
    if date := data.get(CONF_DATE_FROM):
        try:
            d = datetime.date.fromisoformat(date)
            months = [
                "sty",
                "lut",
                "mar",
                "kwi",
                "maj",
                "cze",
                "lip",
                "sie",
                "wrz",
                "paź",
                "lis",
                "gru",
            ]
            parts.append(f"od {d.day} {months[d.month - 1]}")
        except ValueError:
            pass
    return " · ".join(parts) if parts else "Szukajka"


def build_search_data(values, store, existing=None):
    """Build the same canonical subentry data for every creation path."""
    data = dict(existing or {})
    for key, name_key, items in (
        ("region_id", "region_name", store.regions),
        (
            "specialty_id",
            "specialty_name",
            store.get_specialties_for_region(int(values["region_id"])),
        ),
        (
            "clinic_id",
            "clinic_name",
            store.get_clinics(int(values["region_id"]), [int(values["specialty_id"])]),
        ),
        (
            "doctor_id",
            "doctor_name",
            store.get_doctors(int(values["region_id"]), [int(values["specialty_id"])]),
        ),
    ):
        raw = values.get(key)
        data.pop(key, None)
        data.pop(name_key, None)
        if raw is None or raw == "":
            if key in ("region_id", "specialty_id"):
                raise ValueError("Wybierz region i specjalizację.")
            continue
        identifier = int(raw)
        match = next((item for item in items if str(item["id"]) == str(identifier)), None)
        if match is None:
            raise ValueError("Wybrany filtr nie jest dostępny dla tego konta i regionu.")
        data[key] = identifier
        data[name_key] = str(match["value"])
    for key in ("date_from", "date_to", "language_id", "notify_target"):
        data.pop(key, None)
        if values.get(key):
            data[key] = values[key]
    if "language_id" in data:
        data["language_id"] = int(data["language_id"])
        if data["language_id"] not in LANGUAGES:
            raise ValueError("Nieobsługiwany język wizyty.")
    for key in ("date_from", "date_to"):
        if key in data:
            data[key] = datetime.date.fromisoformat(data[key]).isoformat()
    if data.get("date_from", "") > data.get("date_to", "9999-12-31"):
        raise ValueError("Data końcowa nie może poprzedzać początkowej.")
    if isinstance(data.get("notify_target"), str):
        data["notify_target"] = [data["notify_target"]]
    if "notify_target" in data:
        data["notify_target"] = sorted(set(data["notify_target"]))
    data["slot_search_type"] = 0
    return data
