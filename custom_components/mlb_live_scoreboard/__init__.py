from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path

from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN, PLATFORMS
from .coordinator import MlbLiveScoreboardCoordinator

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

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
CARD_FILENAME = "mlb-live-game-card.js"
# Preferred URL — matches the convention used by other HACS-installed
# integrations that bundle a Lovelace card. ``/hacsfiles/<name>/`` is mapped by
# HACS itself to ``<config>/www/community/<name>/``.
HACS_URL_BASE = f"/hacsfiles/{DOMAIN}/{CARD_FILENAME}"
# Legacy URL — kept so we can detect and migrate previously-registered
# resources that point at the integration's own static path.
LEGACY_URL_BASE = f"/{DOMAIN}/{CARD_FILENAME}"
# Fallback URL used when copying into ``www/community/`` fails (e.g. the
# directory is read-only). Mirrors the historical behavior.
FALLBACK_URL_BASE = LEGACY_URL_BASE
CARD_NAME = "mlb-live-game-card"


def _sync_card_to_www_community(hass_config_path: str, source: Path) -> Path | None:
    """Copy the bundled card JS into ``<config>/www/community/<DOMAIN>/``.

    Returns the target path on success, or ``None`` if the copy could not be
    completed (in which case callers should fall back to the legacy
    static-path serving model).
    """
    try:
        target_dir = Path(hass_config_path) / "www" / "community" / DOMAIN
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / CARD_FILENAME
        # Skip the copy when the destination already matches the source. We
        # compare size and mtime; the version query string handles browser
        # cache busting separately so byte-perfect equality isn't required.
        if target.exists():
            src_stat = source.stat()
            dst_stat = target.stat()
            if (
                src_stat.st_size == dst_stat.st_size
                and int(src_stat.st_mtime) == int(dst_stat.st_mtime)
            ):
                return target
        shutil.copy2(source, target)
        return target
    except OSError as err:
        _LOGGER.warning(
            "Could not copy %s into www/community/%s (%s); falling back to /%s/ static path",
            CARD_FILENAME,
            DOMAIN,
            err,
            DOMAIN,
        )
        return None


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    source = Path(__file__).parent / CARD_FILENAME

    # Try to install the card under www/community/<DOMAIN>/ so it is served
    # via the standard ``/hacsfiles/<DOMAIN>/`` URL. Fall back to serving it
    # directly from the integration package if that fails.
    target = await hass.async_add_executor_job(
        _sync_card_to_www_community, hass.config.path(), source
    )

    if target is not None:
        card_url_base = HACS_URL_BASE
    else:
        card_url_base = FALLBACK_URL_BASE
        # Only register the legacy static path when we actually need it —
        # avoids leaking the integration's package directory over HTTP when
        # the preferred install location is in use.
        await hass.http.async_register_static_paths([
            StaticPathConfig(
                url_path=f"/{DOMAIN}",
                path=str(Path(__file__).parent),
                cache_headers=False,
            )
        ])

    card_url = f"{card_url_base}?v={_VERSION_NUM}"
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN]["_card_url"] = card_url
    hass.data[DOMAIN]["_card_url_base"] = card_url_base

    # Try to register card now, and also schedule for after HA fully starts
    await _async_register_card(hass, card_url, card_url_base)

    async def _register_on_start(event):
        await _async_register_card(hass, card_url, card_url_base)

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _register_on_start)

    return True


async def _async_register_card(
    hass: HomeAssistant, card_url: str, card_url_base: str
) -> None:
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

        if hasattr(resources, "loaded") and not resources.loaded:
            await resources.async_load()
            resources.loaded = True

        # Match resources pointing at either the preferred URL or the legacy
        # static path so old installs migrate to the new location seamlessly.
        def _is_ours(url: str) -> bool:
            return HACS_URL_BASE in url or LEGACY_URL_BASE in url

        existing = [r for r in resources.async_items() if _is_ours(r.get("url", ""))]
        if existing:
            for res in existing:
                if res.get("url") != card_url:
                    _LOGGER.info(
                        "Updating MLB Live Game Card resource: %s -> %s",
                        res.get("url"),
                        card_url,
                    )
                    await resources.async_update_item(res["id"], {"url": card_url})
                else:
                    _LOGGER.debug("MLB Live Game Card already registered with current URL")
            return

        # Register the resource
        await resources.async_create_item({
            "url": card_url,
            "res_type": "module",
        })
        _LOGGER.info("Registered MLB Live Game Card as Lovelace resource: %s", card_url)
    except ImportError:
        _LOGGER.debug("Lovelace resources module not available")
    except Exception as err:
        _LOGGER.warning("Could not auto-register card resource: %s", err)
        _LOGGER.info("Manually add this resource: %s (type: module)", card_url)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = MlbLiveScoreboardCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator

    # Reload the entry whenever the user updates the options flow so newly
    # configured action sequences take effect immediately.
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok
