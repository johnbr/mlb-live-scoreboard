"""Pytest fixtures for offline unit tests.

The integration imports ``homeassistant.*`` at module load time, but the
public helpers we test are pure (stateless transformations of ESPN payload
shapes). To avoid a heavy Home Assistant test dependency, this conftest
installs minimal stub modules in ``sys.modules`` *before* the integration
package is imported, satisfying the few imports that occur at module level.

This stub strategy is only sufficient for testing pure helpers — anything
that touches ``DataUpdateCoordinator`` lifecycle, the entity machinery, or
HA config-entry flow needs real ``homeassistant`` and ``pytest-homeassistant-
custom-component`` and is out of scope for these unit tests.
"""

from __future__ import annotations

import sys
import types as _types
from pathlib import Path


def _install_homeassistant_stubs() -> None:
    """Install minimal `homeassistant.*` stubs needed by import-time code paths.

    Only the symbols actually referenced from module top-level by the
    integration are stubbed; anything deeper raises AttributeError, which
    surfaces clearly if a test accidentally exercises a code path needing
    real HA.
    """
    if "homeassistant" in sys.modules:
        return

    def _make(name: str) -> _types.ModuleType:
        mod = _types.ModuleType(name)
        sys.modules[name] = mod
        return mod

    ha = _make("homeassistant")
    ha_components = _make("homeassistant.components")
    ha_components_http = _make("homeassistant.components.http")
    ha_config_entries = _make("homeassistant.config_entries")
    ha_const = _make("homeassistant.const")
    ha_core = _make("homeassistant.core")
    ha_helpers = _make("homeassistant.helpers")
    ha_helpers_aiohttp = _make("homeassistant.helpers.aiohttp_client")
    ha_helpers_cv = _make("homeassistant.helpers.config_validation")
    ha_helpers_device = _make("homeassistant.helpers.device_registry")
    ha_helpers_platform = _make("homeassistant.helpers.entity_platform")
    ha_helpers_update = _make("homeassistant.helpers.update_coordinator")
    ha_components_sensor = _make("homeassistant.components.sensor")

    # Wire submodule attrs so `from homeassistant.x import Y` also resolves
    # via attribute access on the parent module.
    ha.components = ha_components
    ha.config_entries = ha_config_entries
    ha.const = ha_const
    ha.core = ha_core
    ha.helpers = ha_helpers
    ha_components.http = ha_components_http
    ha_components.sensor = ha_components_sensor
    ha_helpers.aiohttp_client = ha_helpers_aiohttp
    ha_helpers.config_validation = ha_helpers_cv
    ha_helpers.device_registry = ha_helpers_device
    ha_helpers.entity_platform = ha_helpers_platform
    ha_helpers.update_coordinator = ha_helpers_update

    # Symbols the integration imports by name. Real implementations are not
    # needed because tests never exercise these paths.
    class _Stub:
        def __init__(self, *args, **kwargs):
            pass

        def __init_subclass__(cls, **kwargs):
            return None

        def __class_getitem__(cls, item):
            return cls

    ha_components_http.StaticPathConfig = _Stub
    ha_config_entries.ConfigEntry = _Stub
    ha_const.EVENT_HOMEASSISTANT_STARTED = "homeassistant_started"
    ha_core.HomeAssistant = _Stub
    ha_helpers_aiohttp.async_get_clientsession = lambda hass: None
    ha_helpers_cv.config_entry_only_config_schema = lambda domain: None
    ha_helpers_device.DeviceEntryType = _types.SimpleNamespace(SERVICE="service")
    ha_helpers_platform.AddEntitiesCallback = _Stub
    ha_helpers_update.DataUpdateCoordinator = _Stub
    ha_helpers_update.UpdateFailed = type("UpdateFailed", (Exception,), {})
    ha_components_sensor.SensorEntity = _Stub


_install_homeassistant_stubs()


# Make `custom_components` importable from the repo root.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
