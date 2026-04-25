from __future__ import annotations

import json
import logging
from pathlib import Path

from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import HomeAssistant

from .const import DOMAIN, PLATFORMS
from .coordinator import MlbLiveScoreboardCoordinator

_LOGGER = logging.getLogger(__name__)

type RuntimeData = MlbLiveScoreboardCoordinator

# Read version from manifest.json for cache busting
_MANIFEST_PATH = Path(__file__).parent / "manifest.json"
try:
    with open(_MANIFEST_PATH) as f:
        _VERSION = json.load(f).get("version", "0.0.0")
except Exception:
    _VERSION = "0.0.0"

# Cache buster: convert 1.5.0 -> 150
_VERSION_NUM = _VERSION.replace(".", "")
CARD_URL_BASE = "/mlb_live_scoreboard/mlb-live-game-card.js"
CARD_URL = f"{CARD_URL_BASE}?v={_VERSION_NUM}"
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

    # Try to register card now, and also schedule for after HA fully starts
    await _async_register_card(hass)
    
    async def _register_on_start(event):
        await _async_register_card(hass)
    
    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _register_on_start)
    
    return True


async def _async_register_card(hass: HomeAssistant) -> None:
    """Register the custom card as a Lovelace resource."""
    try:
        from homeassistant.components.lovelace.const import DOMAIN as LOVELACE_DOMAIN
        
        # Get the lovelace data object
        lovelace_data = hass.data.get(LOVELACE_DOMAIN)
        if lovelace_data is None:
            _LOGGER.debug("Lovelace not ready yet")
            return
        
        # Access resources attribute (it's an object, not a dict)
        resources = getattr(lovelace_data, "resources", None)
        if resources is None:
            _LOGGER.debug("Lovelace resources not available")
            return
            
        # Check if already registered (match base URL without version)
        existing = [r for r in resources.async_items() if CARD_URL_BASE in r.get("url", "")]
        if existing:
            # Update existing resource with new version if different
            for res in existing:
                if res.get("url") != CARD_URL:
                    _LOGGER.info("Updating MLB Live Game Card resource with new version: %s", CARD_URL)
                    await resources.async_update_item(res["id"], {"url": CARD_URL})
                else:
                    _LOGGER.debug("MLB Live Game Card already registered with current version")
            return
            
        # Register the resource
        await resources.async_create_item({
            "url": CARD_URL,
            "res_type": "module",
        })
        _LOGGER.info("Registered MLB Live Game Card as Lovelace resource: %s", CARD_URL)
    except ImportError:
        _LOGGER.debug("Lovelace resources module not available")
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
