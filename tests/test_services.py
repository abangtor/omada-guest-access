"""Exercise HA authorization, unloaded-entry routing and service errors."""

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import Context
from homeassistant.exceptions import ServiceValidationError, Unauthorized
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.omada_guest_access.const import DOMAIN
from custom_components.omada_guest_access.omada_client import OmadaApiError
from custom_components.omada_guest_access.services import async_register_services


async def test_admin_can_decide_and_is_recorded(hass, coordinator, context, hass_admin_user):
    await async_register_services(hass)
    coordinator.entry._async_set_state(hass, ConfigEntryState.LOADED, None)
    item = await coordinator.async_create_request("Alex", context)
    await hass.services.async_call(
        DOMAIN,
        "approve_request",
        {"request_id": item["request_id"]},
        blocking=True,
        context=Context(user_id=hass_admin_user.id),
    )
    assert coordinator.requests[item["request_id"]]["decision_user_id"] == hass_admin_user.id


async def test_non_admin_cannot_approve(hass, coordinator, context, hass_read_only_user):
    await async_register_services(hass)
    coordinator.entry._async_set_state(hass, ConfigEntryState.LOADED, None)
    item = await coordinator.async_create_request("Alex", context)
    with pytest.raises(Unauthorized):
        await hass.services.async_call(
            DOMAIN,
            "approve_request",
            {"request_id": item["request_id"]},
            blocking=True,
            context=Context(user_id=hass_read_only_user.id),
        )
    coordinator.client.async_authorize.assert_not_called()


async def test_selected_non_admin_can_decide(hass, coordinator, context, hass_read_only_user):
    """Configured users may make decisions without being HA administrators."""
    await async_register_services(hass)
    coordinator.entry._async_set_state(hass, ConfigEntryState.LOADED, None)
    coordinator.config["decision_user_ids"] = [hass_read_only_user.id]
    item = await coordinator.async_create_request("Alex", context)
    await hass.services.async_call(
        DOMAIN,
        "approve_request",
        {"request_id": item["request_id"]},
        blocking=True,
        context=Context(user_id=hass_read_only_user.id),
    )
    assert coordinator.requests[item["request_id"]]["decision_user_id"] == hass_read_only_user.id


async def test_selected_non_admin_can_set_guest_label(hass, coordinator, context, hass_read_only_user):
    """A configured decision user may set or clear an admin display label."""
    await async_register_services(hass)
    coordinator.entry._async_set_state(hass, ConfigEntryState.LOADED, None)
    coordinator.config["decision_user_ids"] = [hass_read_only_user.id]
    item = await coordinator.async_create_request("A. Visitor", context)
    await hass.services.async_call(
        DOMAIN,
        "set_guest_label",
        {"request_id": item["request_id"], "label": "Anna from Finance"},
        blocking=True,
        context=Context(user_id=hass_read_only_user.id),
    )
    assert coordinator.requests[item["request_id"]]["guest_name"] == "A. Visitor"
    assert coordinator.requests[item["request_id"]]["admin_label"] == "Anna from Finance"
    await hass.services.async_call(
        DOMAIN,
        "set_guest_label",
        {"request_id": item["request_id"], "label": ""},
        blocking=True,
        context=Context(user_id=hass_read_only_user.id),
    )
    assert coordinator.requests[item["request_id"]]["admin_label"] is None


async def test_automation_skips_unloaded_entries(hass, coordinator, context):
    await async_register_services(hass)
    unloaded = MockConfigEntry(domain=DOMAIN, data={}, title="Unloaded")
    unloaded.add_to_hass(hass)
    coordinator.entry._async_set_state(hass, ConfigEntryState.LOADED, None)
    item = await coordinator.async_create_request("Alex", context)
    await hass.services.async_call(DOMAIN, "deny_request", {"request_id": item["request_id"]}, blocking=True)
    assert coordinator.requests[item["request_id"]]["status"] == "denied"
    coordinator.entry._async_set_state(hass, ConfigEntryState.NOT_LOADED, None)
    with pytest.raises(ServiceValidationError, match="not loaded"):
        await hass.services.async_call(DOMAIN, "approve_request", {"request_id": item["request_id"]}, blocking=True)


async def test_controller_error_is_user_facing(hass, coordinator, context):
    await async_register_services(hass)
    coordinator.entry._async_set_state(hass, ConfigEntryState.LOADED, None)
    item = await coordinator.async_create_request("Alex", context)
    coordinator.client.async_authorize.side_effect = OmadaApiError("offline")
    with pytest.raises(ServiceValidationError, match="offline"):
        await hass.services.async_call(DOMAIN, "approve_request", {"request_id": item["request_id"]}, blocking=True)
