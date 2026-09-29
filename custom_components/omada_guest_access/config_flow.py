"""Config flow for Omada Guest Access."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult

from .const import (
    CONF_CONTROLLER_URL,
    CONF_DEFAULT_DURATION,
    CONF_PASSWORD,
    CONF_PORTAL_PORT,
    CONF_PORTAL_URL,
    CONF_SITE,
    CONF_USERNAME,
    DEFAULT_DURATION_HOURS,
    DEFAULT_PORTAL_PORT,
    DOMAIN,
)


class OmadaGuestAccessConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle configuration of Omada Guest Access."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial setup step."""
        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_CONTROLLER_URL])
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=f"Omada Guest Access ({user_input[CONF_SITE]})",
                data=user_input,
            )

        schema = vol.Schema(
            {
                vol.Required(CONF_CONTROLLER_URL): str,
                vol.Required(CONF_USERNAME): str,
                vol.Required(CONF_PASSWORD): str,
                vol.Required(CONF_SITE, default="Default"): str,
                vol.Optional(CONF_PORTAL_URL): str,
                vol.Required(CONF_PORTAL_PORT, default=DEFAULT_PORTAL_PORT): vol.All(
                    vol.Coerce(int), vol.Range(min=1, max=65535)
                ),
                vol.Required(
                    CONF_DEFAULT_DURATION, default=DEFAULT_DURATION_HOURS
                ): vol.All(vol.Coerce(int), vol.Range(min=1, max=720)),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema)

