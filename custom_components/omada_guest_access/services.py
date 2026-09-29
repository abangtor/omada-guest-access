"""Admin-only guest decisions, including trusted HA automation contexts."""

from __future__ import annotations

import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.service import async_register_admin_service

from .const import DOMAIN, SERVICE_APPROVE_REQUEST, SERVICE_DENY_REQUEST, SERVICE_REVOKE_ACCESS

_REQUEST_ID_SCHEMA = {vol.Required("request_id"): cv.string}


async def async_register_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, SERVICE_APPROVE_REQUEST):
        return

    async def decide(call: ServiceCall) -> None:
        coordinator = next(
            (
                entry.runtime_data
                for entry in hass.config_entries.async_entries(DOMAIN)
                if entry.state is ConfigEntryState.LOADED
                and getattr(entry, "runtime_data", None) is not None
                and call.data["request_id"] in entry.runtime_data.requests
            ),
            None,
        )
        if coordinator is None:
            raise ServiceValidationError("Unknown request or integration is not loaded")
        try:
            if call.service == SERVICE_APPROVE_REQUEST:
                await coordinator.async_approve_request(
                    call.data["request_id"], call.data.get("duration_hours"), user_id=call.context.user_id
                )
            elif call.service == SERVICE_DENY_REQUEST:
                await coordinator.async_deny_request(
                    call.data["request_id"], call.data.get("reason"), user_id=call.context.user_id
                )
            else:
                await coordinator.async_revoke_access(call.data["request_id"], user_id=call.context.user_id)
        except ValueError as err:
            raise ServiceValidationError(str(err)) from err

    async_register_admin_service(
        hass,
        DOMAIN,
        SERVICE_APPROVE_REQUEST,
        decide,
        schema=vol.Schema(
            {
                **_REQUEST_ID_SCHEMA,
                vol.Optional("duration_hours"): vol.All(vol.Coerce(int), vol.Range(min=1, max=720)),
            }
        ),
    )
    async_register_admin_service(
        hass,
        DOMAIN,
        SERVICE_DENY_REQUEST,
        decide,
        schema=vol.Schema(
            {
                **_REQUEST_ID_SCHEMA,
                vol.Optional("reason"): vol.All(cv.string, vol.Length(max=500)),
            }
        ),
    )
    async_register_admin_service(hass, DOMAIN, SERVICE_REVOKE_ACCESS, decide, schema=vol.Schema(_REQUEST_ID_SCHEMA))
