from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult

from .const import CONF_NAME, CONF_TEAM, DEFAULT_NAME, DOMAIN, MLB_TEAM_MAP


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
