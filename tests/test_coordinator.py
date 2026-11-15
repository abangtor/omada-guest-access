"""Concurrency, expiry, outage behavior and actual HA persistence."""

import asyncio
from datetime import timedelta
from hashlib import sha256
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


async def test_forget_all_records_only_clears_local_history(coordinator, context, hass_storage):
    pending = await coordinator.async_create_request("Alex", context)
    await coordinator.async_approve_request(pending["request_id"])
    other_context = context.__class__(
        client_mac="AA:BB:CC:DD:EE:00",
        site=context.site,
        gateway_mac=context.gateway_mac,
        ap_mac=context.ap_mac,
        ssid_name=context.ssid_name,
        radio_id=context.radio_id,
        vlan_id=context.vlan_id,
        redirect_url=context.redirect_url,
    )
    await coordinator.async_create_request("Sam", other_context)

    assert await coordinator.async_forget_all_records() == 2
    assert not coordinator.requests
    assert hass_storage[f"{STORAGE_KEY}.{coordinator.entry.entry_id}"]["data"]["requests"] == {}
    coordinator.client.async_revoke.assert_not_called()


@pytest.mark.parametrize("duration", [-1, 721, True])
async def test_invalid_duration_rejected_before_network(coordinator, context, duration):
    item = await coordinator.async_create_request("Alex", context)
    with pytest.raises(ValueError):
        await coordinator.async_approve_request(item["request_id"], duration)
    coordinator.client.async_authorize.assert_not_called()


async def test_no_expiry_approval_is_retained_until_cancelled(coordinator, context, freezer):
    item = await coordinator.async_create_request("Alex", context)
    coordinator.client.async_authorize.side_effect = None
    coordinator.client.async_authorize.return_value = None
    approved = await coordinator.async_approve_request(item["request_id"], 0)
    assert approved["access_expires_at"] is None
    coordinator.client.async_authorize.assert_awaited_once_with(context, 0)
    freezer.tick(timedelta(days=365))
    await coordinator.async_expire_requests()
    assert coordinator.requests[item["request_id"]]["status"] == "approved"


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


async def test_name_sync_precedence_updates_and_disable(coordinator, context):
    from unittest.mock import AsyncMock

    from custom_components.omada_guest_access.omada_client import OmadaApiError

    sync = AsyncMock()
    coordinator.name_client = sync
    item = await coordinator.async_create_request("Guest name", context)
    sync.async_set_client_name.assert_awaited_once_with(context, "Guest name")
    await coordinator.async_set_guest_label(item["request_id"], "Admin name")
    sync.async_set_client_name.assert_awaited_with(context, "Admin name")
    await coordinator.async_set_guest_label(item["request_id"], "")
    sync.async_set_client_name.assert_awaited_with(context, "Guest name")
    count = sync.async_set_client_name.await_count
    await coordinator._async_update_data()
    assert sync.async_set_client_name.await_count == count
    sync.async_set_client_name.side_effect = OmadaApiError("Permission denied")
    result = await coordinator.async_set_guest_label(item["request_id"], "Retry name")
    assert result["admin_label"] == "Retry name"
    sync.async_set_client_name.side_effect = None
    await coordinator._async_update_data()
    sync.async_set_client_name.assert_awaited_with(context, "Retry name")
    coordinator.name_client = None
    count = sync.async_set_client_name.await_count
    await coordinator.async_set_guest_label(item["request_id"], "Local only")
    assert sync.async_set_client_name.await_count == count


async def test_name_sync_existing_records_and_mac_transfer(coordinator, context):
    from dataclasses import replace
    from unittest.mock import AsyncMock

    item = await coordinator.async_create_request("Guest", context, browser_key_hash=sha256(("b" * 64).encode()).hexdigest())
    await coordinator.async_approve_request(item["request_id"])
    coordinator.name_client = AsyncMock()
    await coordinator._async_update_data()
    coordinator.name_client.async_set_client_name.assert_awaited_once_with(context, "Guest")
    changed = replace(context, client_mac="AA:BB:CC:DD:EE:00")
    await coordinator.async_resume_browser("b" * 64, changed)
    coordinator.name_client.async_set_client_name.assert_awaited_with(changed, "Guest")


async def test_name_sync_uses_separate_controller_credentials(hass, entry, mock_client):
    from unittest.mock import patch

    hass.config_entries.async_update_entry(entry, options={
        "sync_client_names": True,
        "name_sync_username": "controller-user",
        "name_sync_password": "controller-password",
    })
    controller = AsyncMock()
    with patch(
        "custom_components.omada_guest_access.coordinator.OmadaClientNameClient", return_value=controller
    ) as factory:
        instance = OmadaGuestAccessCoordinator(hass, entry)
    config = factory.call_args.args[1]
    assert config["username"] == "controller-user"
    assert config["password"] == "controller-password"
    assert entry.data["username"] == "operator"
    await instance.async_close()
    controller.async_close.assert_awaited_once()
    mock_client.async_close.assert_awaited_once()


async def test_sync_sanitizes_falls_back_and_refreshes_affixes(coordinator, context):
    from unittest.mock import AsyncMock

    coordinator.name_client = AsyncMock()
    coordinator.config.update(name_sync_prefix="Guest - ", name_sync_suffix=" - WiFi")
    item = await coordinator.async_create_request("<b>José</b>", context)
    coordinator.name_client.async_set_client_name.assert_awaited_with(context, "Guest - José - WiFi")
    assert item["guest_name"] == "<b>José</b>"
    result = await coordinator.async_set_guest_label(item["request_id"], "Admin\u202e")
    coordinator.name_client.async_set_client_name.assert_awaited_with(context, "Guest - Admin - WiFi")
    assert result["admin_label"] == "Admin\u202e"
    await coordinator.async_set_guest_label(item["request_id"], "<b></b>")
    coordinator.name_client.async_set_client_name.assert_awaited_with(context, "Guest - José - WiFi")
    coordinator.config.update(name_sync_prefix="Visitor - ", name_sync_suffix="")
    await coordinator._async_update_data()
    coordinator.name_client.async_set_client_name.assert_awaited_with(context, "Visitor - José")
    count = coordinator.name_client.async_set_client_name.await_count
    await coordinator._async_update_data()
    assert coordinator.name_client.async_set_client_name.await_count == count
    await coordinator.async_set_guest_label(item["request_id"], "")
    coordinator.config.update(name_sync_prefix="", name_sync_suffix="")
    await coordinator._async_update_data()
    coordinator.name_client.async_set_client_name.assert_awaited_with(context, "José")


async def test_sync_skips_empty_sanitized_name(coordinator, context):
    from unittest.mock import AsyncMock

    coordinator.name_client = AsyncMock()
    coordinator.config["name_sync_prefix"] = "Guest - "
    await coordinator.async_create_request("<b></b>", context)
    coordinator.name_client.async_set_client_name.assert_not_awaited()
