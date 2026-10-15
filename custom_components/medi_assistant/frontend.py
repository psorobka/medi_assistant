"""Serve the bundled appointments card using Home Assistant's frontend API."""

from pathlib import Path

from homeassistant.components import frontend
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant

CARD_PATH = "/medi_assistant/medi-assistant-card.js"
CARD_URL = CARD_PATH + "?v=2"


async def async_setup_card(hass: HomeAssistant) -> bool:
    """Register once from integration setup; headless HA needs no frontend."""
    if "frontend" not in hass.config.components:
        return True
    path = str(Path(__file__).parent / "www" / "medi-assistant-card.js")
    await hass.http.async_register_static_paths([StaticPathConfig(CARD_PATH, path, False)])
    frontend.add_extra_js_url(hass, CARD_URL)
    return True
