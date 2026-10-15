"""The card is served as a bundled module, without external dependencies."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from custom_components.medi_assistant.frontend import async_setup_card, CARD_URL


async def test_register_card_only_with_frontend(hass):
    hass.http = SimpleNamespace(async_register_static_paths=AsyncMock())
    assert await async_setup_card(hass)
    hass.http.async_register_static_paths.assert_not_awaited()
    hass.config.components.add("frontend")
    with patch("custom_components.medi_assistant.frontend.frontend.add_extra_js_url") as register:
        assert await async_setup_card(hass)
        config = hass.http.async_register_static_paths.call_args.args[0][0]
        assert config.url_path == CARD_URL.split("?")[0]
        assert config.path.endswith("medi-assistant-card.js")
        register.assert_called_once_with(hass, CARD_URL)
