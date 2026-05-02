from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.selector import ActionSelector

from .const import (
    CONF_NAME,
    CONF_TEAM,
    DEFAULT_NAME,
    DOMAIN,
    MLB_TEAM_MAP,
    OPT_ON_GAME_ENDED,
    OPT_ON_GAME_LOST,
    OPT_ON_GAME_STARTED,
    OPT_ON_GAME_WON,
    OPT_ON_OPPONENT_SCORED,
    OPT_ON_TEAM_SCORED,
)


class MlbLiveScoreboardConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict | None = None) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            team = str(user_input[CONF_TEAM]).upper().strip()
            name = str(user_input.get(CONF_NAME) or DEFAULT_NAME).strip()

            if team not in MLB_TEAM_MAP:
                errors["base"] = "invalid_team"
            else:
                await self.async_set_unique_id(team)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"{name} ({team})",
                    data={
                        CONF_TEAM: team,
                        CONF_NAME: name,
                    },
                )

        schema = vol.Schema(
            {
                vol.Required(CONF_TEAM, default="LAD"): vol.In(sorted(MLB_TEAM_MAP.keys())),
                vol.Optional(CONF_NAME, default=DEFAULT_NAME): str,
            }
        )

        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> MlbLiveScoreboardOptionsFlow:
        return MlbLiveScoreboardOptionsFlow(config_entry)


# Option keys exposed in the options flow. Each maps to an HA action sequence
# (the same shape that ``script:`` / automation ``action:`` accept).
_OPTION_KEYS: tuple[str, ...] = (
    OPT_ON_TEAM_SCORED,
    OPT_ON_OPPONENT_SCORED,
    OPT_ON_GAME_STARTED,
    OPT_ON_GAME_ENDED,
    OPT_ON_GAME_WON,
    OPT_ON_GAME_LOST,
)


class MlbLiveScoreboardOptionsFlow(config_entries.OptionsFlow):
    """Lets the user attach an arbitrary HA action sequence to each game event.

    The selector returns a list of action dicts (the same shape as a
    ``script:`` ``sequence:`` block), which the coordinator runs via
    :class:`homeassistant.helpers.script.Script` when the matching event fires.
    """

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self.config_entry = config_entry

    async def async_step_init(
        self, user_input: dict | None = None
    ) -> FlowResult:
        if user_input is not None:
            # Drop empty entries so missing/cleared fields don't persist as
            # empty action lists (which would still flag as "configured").
            cleaned = {k: v for k, v in user_input.items() if v}
            return self.async_create_entry(title="", data=cleaned)

        current = self.config_entry.options or {}
        schema_dict: dict = {}
        for key in _OPTION_KEYS:
            existing = current.get(key)
            field = (
                vol.Optional(key, default=existing)
                if existing is not None
                else vol.Optional(key)
            )
            schema_dict[field] = ActionSelector()

        return self.async_show_form(
            step_id="init", data_schema=vol.Schema(schema_dict)
        )
