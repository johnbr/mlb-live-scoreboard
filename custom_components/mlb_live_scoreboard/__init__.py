from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN, PLATFORMS
from .coordinator import MlbLiveScoreboardCoordinator

_LOGGER = logging.getLogger(__name__)

type RuntimeData = MlbLiveScoreboardCoordinator

CARD_URL = f"/mlb_live_scoreboard/mlb-live-game-card.js"
CARD_NAME = "mlb-live-game-card"


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    # Register the static path to serve the JS file
    await hass.http.async_register_static_paths([
        StaticPathConfig(
            url_path="/mlb_live_scoreboard",
            path=str(Path(__file__).parent),
            cache_headers=False,
        )
    ])

    # Register the Lovelace resource
    await _async_register_card(hass)
    
    return True


async def _async_register_card(hass: HomeAssistant) -> None:
    """Register the custom card as a Lovelace resource."""
    # Check if lovelace resources are available
    if "lovelace" not in hass.data:
        return

    try:
        from homeassistant.components.lovelace import ResourceStorageCollection
        from homeassistant.components.lovelace.const import DOMAIN as LOVELACE_DOMAIN
        
        # Get the resources collection
        resources = hass.data.get(LOVELACE_DOMAIN, {}).get("resources")
        if resources is None:
            return
            
        # Check if already registered
        existing = [r for r in resources.async_items() if CARD_URL in r.get("url", "")]
        if existing:
            _LOGGER.debug("MLB Live Game Card already registered")
            return
            
        # Register the resource
        await resources.async_create_item({
            "url": CARD_URL,
            "res_type": "module",
        })
        _LOGGER.info("Registered MLB Live Game Card as Lovelace resource: %s", CARD_URL)
    except Exception as err:
        _LOGGER.warning("Could not auto-register card resource: %s", err)
        _LOGGER.info("Manually add this resource: %s (type: module)", CARD_URL)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = MlbLiveScoreboardCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok
