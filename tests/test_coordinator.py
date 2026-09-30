"""Concurrency, expiry, outage behavior and actual HA persistence."""

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from homeassistant.util import dt as dt_util

from custom_components.omada_guest_access.const import EVENT_REQUEST_CREATED, STORAGE_KEY
from custom_components.omada_guest_access.coordinator import OmadaGuestAccessCoordinator
from custom_components.omada_guest_access.omada_client import OmadaApiError


async def test_create_idempotent_persistent_and_no_network(hass, coordinator, context, hass_storage):
    events = []
    hass.bus.async_listen(EVENT_REQUEST_CREATED, events.append)
    first, second = await asyncio.gather(
        coordinator.async_create_request("Alex", context), coordinator.async_create_request("Alex", context)
    )
    await hass.async_block_till_done()
    assert first["request_id"] == second["request_id"]
    assert len(events) == 1
    coordinator.client.async_test_connection.assert_not_called()
    stored = hass_storage[f"{STORAGE_KEY}.{coordinator.entry.entry_id}"]["data"]["requests"]
    assert stored[first["request_id"]]["status"] == "pending"
    first["status"] = "tampered"
    assert coordinator.requests[first["request_id"]]["status"] == "pending"


async def test_concurrent_approve_and_deny_cannot_overwrite(coordinator, context):
    item = await coordinator.async_create_request("Alex", context)
    entered, release = asyncio.Event(), asyncio.Event()

    async def authorize(_context, hours):
        entered.set()
        await release.wait()
        return dt_util.utcnow() + timedelta(hours=hours)

    coordinator.client.async_authorize.side_effect = authorize
    approve = asyncio.create_task(coordinator.async_approve_request(item["request_id"], user_id="admin"))
    await entered.wait()
    deny = asyncio.create_task(coordinator.async_deny_request(item["request_id"]))
    release.set()
    approved, denied = await asyncio.gather(approve, deny, return_exceptions=True)
    assert approved["status"] == "approved"
    assert approved["decision_user_id"] == "admin"
    assert isinstance(denied, ValueError)
    coordinator.client.async_authorize.assert_awaited_once()


async def test_double_approval_calls_controller_once(coordinator, context):
    item = await coordinator.async_create_request("Alex", context)
    results = await asyncio.gather(
        *(coordinator.async_approve_request(item["request_id"]) for _ in range(2)), return_exceptions=True
    )
    assert sum(isinstance(result, ValueError) for result in results) == 1
    coordinator.client.async_authorize.assert_awaited_once()


async def test_expired_request_cannot_be_approved(coordinator, context, freezer):
    item = await coordinator.async_create_request("Alex", context)
    freezer.tick(timedelta(minutes=16))
    with pytest.raises(ValueError, match="expired"):
        await coordinator.async_approve_request(item["request_id"])
    coordinator.client.async_authorize.assert_not_called()
    assert coordinator.requests[item["request_id"]]["status"] == "expired"


async def test_cleanup_runs_offline_and_preserves_unexpired_grants(coordinator, context, freezer):
    item = await coordinator.async_create_request("Alex", context)
    await coordinator.async_approve_request(item["request_id"], 720)
    coordinator.config["retention_days"] = 1
    coordinator.client.async_test_connection.side_effect = OmadaApiError("offline")
    freezer.tick(timedelta(days=2))
    state = await coordinator._async_update_data()
    assert not state["controller_available"]
    assert coordinator.requests[item["request_id"]]["status"] == "approved"
    freezer.tick(timedelta(days=29))
    await coordinator._async_update_data()
    assert coordinator.requests[item["request_id"]]["status"] == "expired"
    freezer.tick(timedelta(days=2))
    await coordinator.async_expire_requests()
    assert item["request_id"] not in coordinator.requests


async def test_pending_expiry_when_controller_is_offline(coordinator, context, freezer):
    item = await coordinator.async_create_request("Alex", context)
    coordinator.client.async_test_connection.side_effect = OmadaApiError("offline")
    freezer.tick(timedelta(minutes=16))
    await coordinator._async_update_data()
    assert coordinator.requests[item["request_id"]]["status"] == "expired"


async def test_authorization_and_revoke_failure_preserve_state(coordinator, context):
    item = await coordinator.async_create_request("Alex", context)
    coordinator.client.async_authorize.side_effect = OmadaApiError("rejected")
    with pytest.raises(ValueError):
        await coordinator.async_approve_request(item["request_id"])
    assert coordinator.requests[item["request_id"]]["status"] == "pending"
    coordinator.client.async_authorize.side_effect = None
    expires = dt_util.utcnow() + timedelta(hours=1)
    coordinator.client.async_authorize.return_value = expires
    approved = await coordinator.async_approve_request(item["request_id"])
    assert approved["access_expires_at"] == expires
    coordinator.client.async_revoke.side_effect = OmadaApiError("unsupported")
    with pytest.raises(ValueError):
        await coordinator.async_revoke_access(item["request_id"])
    assert coordinator.requests[item["request_id"]]["status"] == "approved"


async def test_reload_restores_dates_and_decision(hass, coordinator, context):
    item = await coordinator.async_create_request("Alex", context)
    await coordinator.async_deny_request(item["request_id"], "Ask your host", user_id="admin")
    restored = OmadaGuestAccessCoordinator(hass, coordinator.entry)
    try:
        await restored.async_load()
        record = restored.requests[item["request_id"]]
        assert record["status"] == "denied"
        assert record["decision_user_id"] == "admin"
        assert record["created_at"].tzinfo is not None
    finally:
        await restored.async_close()


async def test_storage_failure_does_not_leave_phantom_request(coordinator, context):
    coordinator._store.async_save = AsyncMock(side_effect=OSError("disk full"))
    with pytest.raises(OSError):
        await coordinator.async_create_request("Alex", context)
    assert not coordinator.requests


@pytest.mark.parametrize("duration", [0, -1, 721, True])
async def test_invalid_duration_rejected_before_network(coordinator, context, duration):
    item = await coordinator.async_create_request("Alex", context)
    with pytest.raises(ValueError):
        await coordinator.async_approve_request(item["request_id"], duration)
    coordinator.client.async_authorize.assert_not_called()


async def test_consent_persists_without_retroactive_policy_changes(hass, coordinator, context):
    coordinator.config.update(require_terms=True, terms_text="Original terms")
    with pytest.raises(ValueError, match="Accept"):
        await coordinator.async_create_request("Alex", context)
    item = await coordinator.async_create_request(
        "Alex", context, terms_accepted=True, terms_version=coordinator.terms_version
    )
    coordinator.config["terms_text"] = "New terms"
    # New policy is not retroactively attributed to a pending request.
    await coordinator.async_approve_request(item["request_id"], user_id="admin")
    restored = OmadaGuestAccessCoordinator(hass, coordinator.entry)
    try:
        await restored.async_load()
        record = restored.requests[item["request_id"]]
        assert record["terms_text"] == "Original terms"
        assert record["terms_version"] == item["terms_version"]
        assert record["terms_accepted_at"] == item["terms_accepted_at"]
    finally:
        await restored.async_close()


async def test_optional_terms_do_not_record_fabricated_consent(coordinator, context):
    coordinator.config.update(require_terms=False, terms_text="Informational notice")
    item = await coordinator.async_create_request(
        "Alex", context, terms_accepted=True, terms_version=coordinator.terms_version
    )
    assert item["terms_accepted_at"] is None
    assert item["terms_text"] is None
