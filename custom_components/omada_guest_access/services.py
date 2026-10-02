"""Guest decisions gated by configured Home Assistant users."""

from __future__ import annotations

import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError, Unauthorized
from homeassistant.helpers import config_validation as cv

from .const import (
    DOMAIN,
    SERVICE_APPROVE_REQUEST,
    SERVICE_DENY_REQUEST,
    SERVICE_REVOKE_ACCESS,
    SERVICE_SET_GUEST_LABEL,
)

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
        if not await _can_decide(hass, coordinator.config.get("decision_user_ids", []), call):
            raise Unauthorized(context=call.context, user_id=call.context.user_id)
        try:
            if call.service == SERVICE_APPROVE_REQUEST:
                await coordinator.async_approve_request(
                    call.data["request_id"], call.data.get("duration_hours"), user_id=call.context.user_id
                )
            elif call.service == SERVICE_DENY_REQUEST:
                await coordinator.async_deny_request(
                    call.data["request_id"], call.data.get("reason"), user_id=call.context.user_id
                )
            elif call.service == SERVICE_SET_GUEST_LABEL:
                await coordinator.async_set_guest_label(
                    call.data["request_id"], call.data.get("label"), user_id=call.context.user_id
                )
            else:
                await coordinator.async_revoke_access(call.data["request_id"], user_id=call.context.user_id)
        except ValueError as err:
            raise ServiceValidationError(str(err)) from err

    hass.services.async_register(
        DOMAIN,
        SERVICE_APPROVE_REQUEST,
        decide,
        schema=vol.Schema(
            {
                **_REQUEST_ID_SCHEMA,
                vol.Optional("duration_hours"): vol.All(vol.Coerce(int), vol.Range(min=0, max=720)),
            }
        ),
    )
    hass.services.async_register(
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
    hass.services.async_register(DOMAIN, SERVICE_REVOKE_ACCESS, decide, schema=vol.Schema(_REQUEST_ID_SCHEMA))
    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_GUEST_LABEL,
        decide,
        schema=vol.Schema(
            {
                **_REQUEST_ID_SCHEMA,
                vol.Optional("label", default=""): vol.All(cv.string, vol.Length(max=120)),
            }
        ),
    )


async def _can_decide(hass: HomeAssistant, allowed_user_ids: object, call: ServiceCall) -> bool:
    """Allow administrators, selected active users, and trusted automations."""
    if call.context.user_id is None:
        return True
    user = await hass.auth.async_get_user(call.context.user_id)
    if user is not None and user.is_active and user.is_admin:
        return True
    return (
        user is not None
        and user.is_active
        and isinstance(allowed_user_ids, list)
        and call.context.user_id in allowed_user_ids
    )
