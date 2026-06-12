from __future__ import annotations

DOMAIN = "medi_assistant"

MEDICOVER_LOGIN_URL = "https://login-online24.medicover.pl"
MEDICOVER_MAIN_URL = "https://online24.medicover.pl"
MEDICOVER_API_URL = "https://api-gateway-online24.medicover.pl"
OIDC_DISCOVERY_URL = f"{MEDICOVER_LOGIN_URL}/.well-known/openid-configuration"

# Config entry data keys
ENTRY_ACCESS_TOKEN = "access_token"
ENTRY_REFRESH_TOKEN = "refresh_token"
ENTRY_EXPIRES_AT = "expires_at"
ENTRY_DEVICE_ID = "device_id"
ENTRY_DEVICE_UA = "device_ua"
ENTRY_AUTH_COOKIES = "auth_cookies"

# Config / options keys
CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_MFA_CODE = "mfa_code"
CONF_SCAN_INTERVAL = "scan_interval"

# Subentry data keys
CONF_REGION_ID = "region_id"
CONF_REGION_NAME = "region_name"
CONF_SPECIALTY_ID = "specialty_id"
CONF_SPECIALTY_NAME = "specialty_name"
CONF_CLINIC_ID = "clinic_id"
CONF_CLINIC_NAME = "clinic_name"
CONF_DOCTOR_ID = "doctor_id"
CONF_DOCTOR_NAME = "doctor_name"
CONF_LANGUAGE_ID = "language_id"
CONF_DATE_FROM = "date_from"
CONF_DATE_TO = "date_to"
CONF_SLOT_SEARCH_TYPE = "slot_search_type"
CONF_NOTIFY_TARGET = "notify_target"

# Store keys (file: {DOMAIN}.{entry_id}.json)
STORE_KEY_PERSONAL = "personal"
STORE_KEY_REGIONS = "regions"
STORE_KEY_SPECIALTIES = "specialties"
STORE_KEY_REGION_SPECIALTIES = "region_specialties"
STORE_KEY_CLINICS = "clinics"
STORE_KEY_DOCTORS = "doctors"
STORE_KEY_LAST_REFRESHED = "last_refreshed"
STORE_KEY_SEEN_SLOTS = "seen_slots"

LANGUAGES: dict[int, str] = {4: "Polski", 6: "Angielski", 60: "Ukraiński"}

DEFAULT_SCAN_INTERVAL = 10  # minutes
DEFAULT_SLOT_SEARCH_TYPE = 0
CACHE_TTL = 86400  # 24 h in seconds

SUBENTRY_TYPE_SEARCH = "search"
