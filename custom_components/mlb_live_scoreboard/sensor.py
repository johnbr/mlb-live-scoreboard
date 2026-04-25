from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import RuntimeData
from .const import DOMAIN


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: RuntimeData = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([MlbLiveScoreboardSensor(coordinator, entry)])


class MlbLiveScoreboardSensor(CoordinatorEntity[RuntimeData], SensorEntity):
    _attr_icon = "mdi:baseball"
    _attr_has_entity_name = True

    def __init__(self, coordinator: RuntimeData, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        team = str(entry.data.get("team", "mlb")).lower()
        self._attr_unique_id = f"{entry.entry_id}_scoreboard"
        self._attr_name = "Scoreboard"
        # Entity ID will be: sensor.mlb_live_scoreboard_lad (for LAD team)
        self._attr_suggested_object_id = f"mlb_live_scoreboard_{team}"

    @property
    def native_value(self) -> str:
        return self.coordinator.data.display_event_id or "idle"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data
        return {
            "team_abbr": data.team_abbr,
            "team_id": data.team_id,
            "team_name": data.team_name,
            "mode": data.mode,
            "is_live": data.is_live,
            "is_delayed": data.is_delayed,
            "status_text": data.status_text,
            "display_event_id": data.display_event_id,
            "live_event_id": data.live_event_id,
            "previous_event_id": data.previous_event_id,
            "next_event_id": data.next_event_id,
            "competition": data.selected_competition or {},
            "inning_context": data.inning_context,
            "recent_plays": data.recent_plays,
            "current_pitches": data.current_pitches,
            "away_team": data.away_team,
            "home_team": data.home_team,
            "current_batter": data.current_batter,
            "current_pitcher": data.current_pitcher,
            "batter_stats": data.batter_stats,
            "pitcher_stats": data.pitcher_stats,
            "situation": data.situation,
            "probable_pitchers": data.probable_pitchers,
            "due_up": data.due_up,
            "third_out_play": data.third_out_play,
            "on_deck": data.on_deck,
            "leaders": data.leaders,
        }

    @property
    def device_info(self) -> dict[str, Any]:
        data = self.coordinator.data
        return {
            "identifiers": {(DOMAIN, self.coordinator.entry.entry_id)},
            "name": f"MLB Live Scoreboard {data.team_abbr}",
            "manufacturer": "ESPN / Custom",
            "model": "MLB Live Scoreboard",
            "entry_type": DeviceEntryType.SERVICE,
        }
