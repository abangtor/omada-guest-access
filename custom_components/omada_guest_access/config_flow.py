"""Config and options flows for Omada Guest Access."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import config_validation as cv

from .const import (
    CONF_CONTROLLER_ID,
    CONF_CONTROLLER_URL,
    CONF_DEFAULT_DURATION,
    CONF_PASSWORD,
    CONF_PENDING_TIMEOUT,
    CONF_PORTAL_PORT,
    CONF_PORTAL_URL,
    CONF_RETENTION_DAYS,
    CONF_SITE,
    CONF_USERNAME,
    CONF_VERIFY_SSL,
    DEFAULT_DURATION_HOURS,
    DEFAULT_PENDING_TIMEOUT_MINUTES,
    DEFAULT_PORTAL_PORT,
    DEFAULT_RETENTION_DAYS,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
)
from .omada_client import OmadaApiError, OmadaExternalPortalClient


class OmadaGuestAccessConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Configure the documented Omada External Portal API."""

    VERSION = 2

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                await OmadaExternalPortalClient(self.hass, user_input).async_test_connection()
            except OmadaApiError:
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(f"{user_input[CONF_CONTROLLER_URL]}:{user_input[CONF_SITE]}")
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=f"Omada Guest Access ({user_input[CONF_SITE]})", data=user_input)
        return self.async_show_form(step_id="user", data_schema=_data_schema(user_input), errors=errors)

    @staticmethod
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> OmadaGuestAccessOptionsFlow:
        """Return an options flow for non-secret portal settings."""
        return OmadaGuestAccessOptionsFlow(config_entry)


class OmadaGuestAccessOptionsFlow(config_entries.OptionsFlow):
    """Configure portal settings without re-entering controller credentials."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self.config_entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        values = dict(self.config_entry.data) | dict(self.config_entry.options)
        schema = vol.Schema(
            {
                vol.Optional(CONF_PORTAL_URL, default=values.get(CONF_PORTAL_URL, "")): cv.string,
                vol.Required(CONF_PORTAL_PORT, default=values.get(CONF_PORTAL_PORT, DEFAULT_PORTAL_PORT)): vol.All(
                    vol.Coerce(int), vol.Range(min=1, max=65535)
                ),
                vol.Required(
                    CONF_DEFAULT_DURATION, default=values.get(CONF_DEFAULT_DURATION, DEFAULT_DURATION_HOURS)
                ): vol.All(vol.Coerce(int), vol.Range(min=1, max=720)),
                vol.Required(
                    CONF_PENDING_TIMEOUT, default=values.get(CONF_PENDING_TIMEOUT, DEFAULT_PENDING_TIMEOUT_MINUTES)
                ): vol.All(vol.Coerce(int), vol.Range(min=1, max=1440)),
                vol.Required(
                    CONF_RETENTION_DAYS, default=values.get(CONF_RETENTION_DAYS, DEFAULT_RETENTION_DAYS)
                ): vol.All(vol.Coerce(int), vol.Range(min=1, max=365)),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)


def _data_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    defaults = defaults or {}
    return vol.Schema(
        {
            vol.Required(CONF_CONTROLLER_URL, default=defaults.get(CONF_CONTROLLER_URL, "")): cv.url,
            vol.Optional(CONF_CONTROLLER_ID, default=defaults.get(CONF_CONTROLLER_ID, "")): cv.string,
            vol.Required(CONF_USERNAME, default=defaults.get(CONF_USERNAME, "")): cv.string,
            vol.Required(CONF_PASSWORD, default=defaults.get(CONF_PASSWORD, "")): cv.string,
            vol.Required(CONF_SITE, default=defaults.get(CONF_SITE, "Default")): cv.string,
            vol.Optional(CONF_PORTAL_URL, default=defaults.get(CONF_PORTAL_URL, "")): cv.string,
            vol.Required(CONF_PORTAL_PORT, default=defaults.get(CONF_PORTAL_PORT, DEFAULT_PORTAL_PORT)): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=65535)
            ),
            vol.Required(CONF_VERIFY_SSL, default=defaults.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)): cv.boolean,
            vol.Required(
                CONF_DEFAULT_DURATION, default=defaults.get(CONF_DEFAULT_DURATION, DEFAULT_DURATION_HOURS)
            ): vol.All(vol.Coerce(int), vol.Range(min=1, max=720)),
            vol.Required(
                CONF_PENDING_TIMEOUT, default=defaults.get(CONF_PENDING_TIMEOUT, DEFAULT_PENDING_TIMEOUT_MINUTES)
            ): vol.All(vol.Coerce(int), vol.Range(min=1, max=1440)),
            vol.Required(
                CONF_RETENTION_DAYS, default=defaults.get(CONF_RETENTION_DAYS, DEFAULT_RETENTION_DAYS)
            ): vol.All(vol.Coerce(int), vol.Range(min=1, max=365)),
        }
    )
