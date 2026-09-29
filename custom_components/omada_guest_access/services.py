"""Services for Omada Guest Access."""

from __future__ import annotations

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv

from .const import (
    DOMAIN,
    EVENT_ACCESS_REVOKED,
    EVENT_REQUEST_APPROVED,
    EVENT_REQUEST_DENIED,
    SERVICE_APPROVE_REQUEST,
    SERVICE_DENY_REQUEST,
    SERVICE_REVOKE_ACCESS,
)
from .coordinator import event_data

_REQUEST_ID_SCHEMA = {vol.Required("request_id"): cv.string}


async def async_register_services(hass: HomeAssistant) -> None:
    """Register services once; route calls to the owning config entry."""
    if hass.services.has_service(DOMAIN, SERVICE_APPROVE_REQUEST):
        return

    def coordinator_for(call: ServiceCall):
        entries = hass.config_entries.async_entries(DOMAIN)
        if not entries:
            raise ValueError("Omada Guest Access is not configured")
        for entry in entries:
            coordinator = entry.runtime_data
            if call.data["request_id"] in coordinator.requests:
                return coordinator
        raise ValueError("Unknown guest access request")

    async def approve(call: ServiceCall) -> None:
        request = await coordinator_for(call).async_approve_request(
            call.data["request_id"], call.data.get("duration_hours")
        )
        hass.bus.async_fire(EVENT_REQUEST_APPROVED, event_data(request))

    async def deny(call: ServiceCall) -> None:
        request = await coordinator_for(call).async_deny_request(call.data["request_id"], call.data.get("reason"))
        hass.bus.async_fire(EVENT_REQUEST_DENIED, event_data(request))

    async def revoke(call: ServiceCall) -> None:
        request = await coordinator_for(call).async_revoke_access(call.data["request_id"])
        hass.bus.async_fire(EVENT_ACCESS_REVOKED, event_data(request))

    hass.services.async_register(
        DOMAIN,
        SERVICE_APPROVE_REQUEST,
        approve,
        schema=vol.Schema(
            {**_REQUEST_ID_SCHEMA, vol.Optional("duration_hours"): vol.All(vol.Coerce(int), vol.Range(min=1, max=720))}
        ),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_DENY_REQUEST,
        deny,
        schema=vol.Schema({**_REQUEST_ID_SCHEMA, vol.Optional("reason"): cv.string}),
    )
    hass.services.async_register(DOMAIN, SERVICE_REVOKE_ACCESS, revoke, schema=vol.Schema(_REQUEST_ID_SCHEMA))
